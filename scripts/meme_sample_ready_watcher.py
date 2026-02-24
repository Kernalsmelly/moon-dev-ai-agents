#!/usr/bin/env python3
"""Watch run sample size/quality and auto-generate decision reports once ready.

This avoids idle/manual polling while paper data accumulates.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv


BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
DB = BASE / "data" / "positions.db"
REPORT_DIR = BASE / "data" / "meme_reports"
STATE_FILE = BASE / "data" / "meme_sample_ready_state.json"
PYTHON = "/opt/homebrew/bin/python3"


def _f(v, d=0.0) -> float:
    try:
        if v is None:
            return d
        return float(v)
    except Exception:
        return d


def _auto_run_id() -> str:
    log_path = BASE / "logs" / "meme_bot_early_edge_auto.log"
    if not log_path.exists():
        return ""
    try:
        data = log_path.read_bytes()
        if len(data) > 250_000:
            data = data[-250_000:]
        text = data.decode("utf-8", errors="ignore")
        lines = [ln for ln in text.splitlines() if "run_id=" in ln]
        if not lines:
            return ""
        rid = lines[-1].split("run_id=", 1)[1].strip()
        rid = rid.replace("[/dim]", "").split()[0].strip()
        return rid
    except Exception:
        return ""


def _dt_iso(v: str) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v)
    except Exception:
        return None


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {"reported_runs": {}}
    try:
        obj = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    return {"reported_runs": {}}


def _save_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        pass


def _not_ready_reasons(
    s: dict,
    *,
    min_trades: int,
    min_clusters: int,
    max_cluster_tail: float,
    max_dom_legs: float,
) -> list[str]:
    out: list[str] = []
    if int(s.get("trades") or 0) < int(min_trades):
        out.append(f"trades {int(s.get('trades') or 0)}<{int(min_trades)}")
    if int(s.get("clusters") or 0) < int(min_clusters):
        out.append(f"clusters {int(s.get('clusters') or 0)}<{int(min_clusters)}")
    if float(s.get("cluster_tail_loss_share") or 0.0) > float(max_cluster_tail):
        out.append(
            f"cluster_tail {float(s.get('cluster_tail_loss_share') or 0.0):.1%}>{float(max_cluster_tail):.1%}"
        )
    if float(s.get("dominant_leg_share") or 0.0) > float(max_dom_legs):
        out.append(f"dom_legs {float(s.get('dominant_leg_share') or 0.0):.1%}>{float(max_dom_legs):.1%}")
    return out


def _load_run_rows(run_id: str, hours: float) -> list[dict]:
    if not DB.exists():
        return []
    cutoff = datetime.now() - timedelta(hours=float(hours))
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    try:
        cur.execute(
            """
            SELECT mint, symbol, pnl_usd, exit_reason, exit_timestamp, metadata
            FROM trades
            WHERE side='SELL'
            ORDER BY trade_id DESC
            LIMIT 12000
            """
        )
        out: list[dict] = []
        for r in cur.fetchall():
            md = {}
            try:
                md = json.loads(r["metadata"] or "{}")
                if not isinstance(md, dict):
                    md = {}
            except Exception:
                md = {}
            if str(md.get("run_id") or "").strip() != run_id:
                continue
            dt = _dt_iso(str(r["exit_timestamp"] or ""))
            if dt is None or dt < cutoff:
                continue
            out.append(
                {
                    "mint": str(r["mint"] or ""),
                    "symbol": str(r["symbol"] or ""),
                    "pnl_usd": _f(r["pnl_usd"], 0.0),
                    "exit_reason": str(r["exit_reason"] or "UNKNOWN"),
                    "exit_timestamp": str(r["exit_timestamp"] or ""),
                    "metadata": md,
                }
            )
        return out
    finally:
        con.close()


def _cluster_rows(rows: list[dict], entry_tol_s: float = 180.0) -> list[dict]:
    by_mint: dict[str, list[dict]] = {}
    xs = sorted(rows, key=lambda r: _dt_iso(str(r.get("exit_timestamp") or "")) or datetime.min)
    for r in xs:
        mint = str(r.get("mint") or "UNKNOWN_MINT")
        md = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}
        dt = _dt_iso(str(r.get("exit_timestamp") or ""))
        exit_ts = dt.timestamp() if dt else 0.0
        hold_s = None
        try:
            hv = md.get("hold_time_sec")
            hold_s = float(hv) if hv is not None else None
        except Exception:
            hold_s = None
        anchor = (exit_ts - hold_s) if (hold_s is not None and hold_s >= 0) else None

        bucket = by_mint.setdefault(mint, [])
        chosen = None
        if anchor is not None:
            best = None
            for c in bucket:
                ca = c.get("anchor")
                if ca is None:
                    continue
                dist = abs(float(ca) - float(anchor))
                if dist <= float(entry_tol_s) and (best is None or dist < best):
                    best = dist
                    chosen = c
        if chosen is None:
            chosen = {
                "mint": mint,
                "symbol": str(r.get("symbol") or mint[:8]),
                "anchor": anchor,
                "legs": 0,
                "pnl": 0.0,
                "reasons": set(),
            }
            bucket.append(chosen)
        chosen["legs"] = int(chosen.get("legs") or 0) + 1
        chosen["pnl"] = float(chosen.get("pnl") or 0.0) + _f(r.get("pnl_usd"), 0.0)
        rs = chosen.get("reasons")
        if not isinstance(rs, set):
            rs = set()
        rs.add(str(r.get("exit_reason") or "UNKNOWN"))
        chosen["reasons"] = rs

    out: list[dict] = []
    for vals in by_mint.values():
        out.extend(vals)
    return out


def _summarize(rows: list[dict], clusters: list[dict]) -> dict:
    n = len(rows)
    wins = sum(1 for r in rows if _f(r.get("pnl_usd")) > 0)
    pnl = sum(_f(r.get("pnl_usd")) for r in rows)
    cn = len(clusters)
    c_wins = sum(1 for c in clusters if _f(c.get("pnl")) > 0)
    c_losses = [abs(_f(c.get("pnl"))) for c in clusters if _f(c.get("pnl")) < 0]
    c_total_loss = sum(c_losses)
    c_tail = (max(c_losses) / c_total_loss) if c_total_loss > 0 else 0.0
    total_legs = sum(int(c.get("legs") or 0) for c in clusters)
    dom_legs = max((int(c.get("legs") or 0) for c in clusters), default=0)
    dom_leg_share = (dom_legs / total_legs) if total_legs > 0 else 0.0
    return {
        "trades": n,
        "wins": wins,
        "pnl": pnl,
        "clusters": cn,
        "cluster_wins": c_wins,
        "cluster_tail_loss_share": c_tail,
        "dominant_leg_share": dom_leg_share,
    }


def _run_capture(cmd: list[str], out_file: Path) -> None:
    try:
        out = subprocess.check_output(cmd, cwd=str(BASE), text=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        out = f"[exit={e.returncode}]\n{e.output}"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(out, encoding="utf-8")


def _discord_notify(webhook: str, content: str) -> None:
    try:
        payload = json.dumps({"content": content}).encode("utf-8")
        req = urllib.request.Request(webhook, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        urllib.request.urlopen(req, timeout=8).read()
    except Exception:
        pass


def _write_status_snapshot(
    *,
    run_id: str,
    lookback_h: float,
    summary: dict,
    ready: bool,
    reasons: list[str],
) -> Path:
    run_dir = REPORT_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": int(time.time()),
        "run_id": run_id,
        "hours": float(lookback_h),
        "ready": bool(ready),
        "reasons": reasons,
        "summary": summary,
    }
    (run_dir / "sample_status.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    lines = [
        f"# Sample Status: {run_id}",
        "",
        f"- Window: last {lookback_h:g}h",
        f"- Ready: {'YES' if ready else 'NO'}",
        f"- Trades: {int(summary.get('trades') or 0)}",
        f"- Clusters: {int(summary.get('clusters') or 0)}",
        f"- PnL: ${float(summary.get('pnl') or 0.0):+.2f}",
        f"- Cluster Tail Loss Share: {float(summary.get('cluster_tail_loss_share') or 0.0):.1%}",
        f"- Dominant Leg Share: {float(summary.get('dominant_leg_share') or 0.0):.1%}",
        "",
    ]
    if reasons:
        lines.append("## Not Ready Reasons")
        for r in reasons:
            lines.append(f"- {r}")
        lines.append("")
    (run_dir / "sample_status.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return run_dir


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="Run one check iteration and exit")
    ap.add_argument("--interval-s", type=int, default=0, help="Override check interval seconds")
    ap.add_argument("--hours", type=float, default=0.0, help="Override lookback hours")
    args = ap.parse_args()

    load_dotenv(BASE / ".env", override=True)
    interval_s = int(os.getenv("MEME_SAMPLE_READY_INTERVAL_S", "300") or 300)
    lookback_h = float(os.getenv("MEME_SAMPLE_READY_HOURS", "24") or 24)
    if int(args.interval_s or 0) > 0:
        interval_s = int(args.interval_s)
    if float(args.hours or 0.0) > 0:
        lookback_h = float(args.hours)
    min_trades = int(os.getenv("MEME_SAMPLE_READY_MIN_TRADES", "40") or 40)
    min_clusters = int(os.getenv("MEME_SAMPLE_READY_MIN_CLUSTERS", "10") or 10)
    max_cluster_tail = float(os.getenv("MEME_SAMPLE_READY_MAX_CLUSTER_TAIL", "0.35") or 0.35)
    max_dom_legs = float(os.getenv("MEME_SAMPLE_READY_MAX_DOMINANT_LEG_SHARE", "0.75") or 0.75)
    heartbeat_s = int(os.getenv("MEME_SAMPLE_READY_HEARTBEAT_S", "1800") or 1800)
    reject_minutes = int(os.getenv("MEME_SAMPLE_READY_REJECT_MINUTES", "240") or 240)
    notify_discord = str(os.getenv("MEME_SAMPLE_READY_NOTIFY_DISCORD", "0") or "0").lower() in ("1", "true", "yes")
    webhook = str(os.getenv("DISCORD_WEBHOOK", "") or "").strip()

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    state = _load_state()
    reported = state.get("reported_runs") if isinstance(state.get("reported_runs"), dict) else {}
    if not isinstance(reported, dict):
        reported = {}
        state["reported_runs"] = reported

    while True:
        rid = str(_auto_run_id() or "").strip()
        if not rid:
            print("sample_ready: run_id unavailable")
            if args.once:
                return 0
            time.sleep(max(30, interval_s))
            continue

        rows = _load_run_rows(rid, lookback_h)
        clusters = _cluster_rows(rows)
        s = _summarize(rows, clusters)

        ready = (
            int(s["trades"]) >= int(min_trades)
            and int(s["clusters"]) >= int(min_clusters)
            and float(s["cluster_tail_loss_share"]) <= float(max_cluster_tail)
            and float(s["dominant_leg_share"]) <= float(max_dom_legs)
        )
        reasons = _not_ready_reasons(
            s,
            min_trades=min_trades,
            min_clusters=min_clusters,
            max_cluster_tail=max_cluster_tail,
            max_dom_legs=max_dom_legs,
        )

        print(
            "sample_ready:"
            f" run={rid} trades={s['trades']} clusters={s['clusters']}"
            f" c_tail={float(s['cluster_tail_loss_share']):.1%}"
            f" dom_legs={float(s['dominant_leg_share']):.1%}"
            f" ready={ready} reasons={';'.join(reasons) if reasons else '-'}"
        )

        # Periodic heartbeat snapshot even when not ready.
        hb_key = f"{rid}:heartbeat"
        last_hb = int(reported.get(hb_key) or 0)
        now_ts = int(time.time())
        if last_hb <= 0 or (now_ts - last_hb) >= int(max(60, heartbeat_s)):
            run_dir = _write_status_snapshot(
                run_id=rid,
                lookback_h=lookback_h,
                summary=s,
                ready=ready,
                reasons=reasons,
            )
            _run_capture(
                [
                    PYTHON,
                    str(BASE / "scripts" / "meme_reject_tuning_report.py"),
                    "--run-id",
                    rid,
                    "--minutes",
                    str(reject_minutes),
                ],
                run_dir / "reject_tuning_heartbeat.md",
            )
            reported[hb_key] = now_ts

        if ready and rid not in reported:
            ts = int(time.time())
            run_dir = _write_status_snapshot(
                run_id=rid,
                lookback_h=lookback_h,
                summary=s,
                ready=ready,
                reasons=reasons,
            )

            _run_capture(
                [PYTHON, str(BASE / "scripts" / "meme_run_report.py"), "--run-id", rid, "--hours", str(lookback_h)],
                run_dir / "run_report.md",
            )
            _run_capture(
                [
                    PYTHON,
                    str(BASE / "scripts" / "meme_live_readiness.py"),
                    "--run-id",
                    rid,
                    "--hours",
                    str(lookback_h),
                    "--min-trades",
                    str(min_trades),
                    "--min-clusters",
                    str(min_clusters),
                    "--max-cluster-tail-loss-share",
                    str(max_cluster_tail),
                ],
                run_dir / "readiness.md",
            )
            _run_capture(
                [
                    PYTHON,
                    str(BASE / "scripts" / "meme_reject_tuning_report.py"),
                    "--run-id",
                    rid,
                    "--minutes",
                    str(reject_minutes),
                ],
                run_dir / "reject_tuning.md",
            )

            summary = (
                f"sample_ready trigger: {rid} trades={s['trades']} clusters={s['clusters']} "
                f"pnl={float(s['pnl']):+.2f} c_tail={float(s['cluster_tail_loss_share']):.1%} "
                f"dom_legs={float(s['dominant_leg_share']):.1%} report_dir={run_dir}"
            )
            print(summary)
            if notify_discord and webhook:
                _discord_notify(webhook, summary)

            reported[rid] = ts
            # keep state small
            if len(reported) > 100:
                keep = sorted(reported.items(), key=lambda kv: kv[1], reverse=True)[:200]
                state["reported_runs"] = {k: v for k, v in keep}
                reported = state["reported_runs"]
            _save_state(state)
        else:
            _save_state(state)

        if args.once:
            return 0
        time.sleep(max(30, interval_s))


if __name__ == "__main__":
    raise SystemExit(main())
