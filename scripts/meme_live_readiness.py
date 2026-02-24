#!/usr/bin/env python3
"""Run-scoped live-readiness checklist for meme bot.

This script is intentionally simple and conservative. It evaluates the
currently active run (or an explicit run_id) against basic paper-trading gates.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
DB_DEFAULT = BASE / "data" / "positions.db"
DEBUG_DEFAULT = BASE / "data" / "meme_signal_debug.jsonl"
LOG_DEFAULT = BASE / "logs" / "meme_bot_early_edge_auto.log"


def _auto_run_id(log_path: Path) -> str:
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


def _parse_created_at(v: str) -> datetime | None:
    if not v:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(v, fmt)
        except Exception:
            continue
    return None


def _load_run_trades(db_path: Path, run_id: str, hours: float) -> list[dict]:
    cutoff = datetime.now() - timedelta(hours=hours)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT trade_id, mint, symbol, side, pnl_usd, exit_reason, created_at, exit_timestamp, metadata
            FROM trades
            WHERE side='SELL'
            ORDER BY trade_id ASC
            """
        ).fetchall()
    finally:
        con.close()

    out: list[dict] = []
    for r in rows:
        created_at = _parse_created_at(str(r["created_at"] or ""))
        if created_at is None or created_at < cutoff:
            continue
        try:
            md = json.loads(r["metadata"] or "{}")
        except Exception:
            md = {}
        if str((md or {}).get("run_id") or "").strip() != run_id:
            continue
        out.append(
            {
                "trade_id": int(r["trade_id"] or 0),
                "mint": str(r["mint"] or ""),
                "symbol": str(r["symbol"] or ""),
                "pnl_usd": float(r["pnl_usd"] or 0.0),
                "exit_reason": str(r["exit_reason"] or "UNKNOWN"),
                "created_at": created_at,
                "exit_timestamp": str(r["exit_timestamp"] or ""),
                "metadata": md if isinstance(md, dict) else {},
            }
        )
    return out


def _load_run_debug(debug_path: Path, run_id: str, hours: float) -> Counter:
    cutoff_ts = time.time() - (hours * 3600.0)
    ctr: Counter = Counter()
    if not debug_path.exists():
        return ctr
    with debug_path.open("r", encoding="utf-8") as fh:
        for ln in fh:
            try:
                obj = json.loads(ln)
            except Exception:
                continue
            if str(obj.get("run_id") or "").strip() != run_id:
                continue
            try:
                ts = float(obj.get("ts") or 0.0)
            except Exception:
                ts = 0.0
            if ts < cutoff_ts:
                continue
            ctr[str(obj.get("kind") or "unknown")] += 1
    return ctr


def _max_drawdown(trades: list[dict]) -> float:
    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        eq += float(t["pnl_usd"])
        peak = max(peak, eq)
        dd = peak - eq
        max_dd = max(max_dd, dd)
    return max_dd


def _fmt_gate(ok: bool, name: str, detail: str) -> str:
    status = "PASS" if ok else "FAIL"
    return f"- [{status}] {name}: {detail}"


@dataclass
class _Cluster:
    mint: str
    trade_count: int = 0
    pnl_usd: float = 0.0
    approx_entry_epoch: float | None = None
    end_ts: datetime | None = None


def _parse_iso(v: str) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v)
    except Exception:
        return None


def _entry_anchor_epoch(tr: dict) -> float | None:
    dt = _parse_iso(str(tr.get("exit_timestamp") or ""))
    try:
        hold_s = float((tr.get("metadata") or {}).get("hold_time_sec"))
    except Exception:
        hold_s = None
    if dt is None or hold_s is None or hold_s < 0:
        return None
    return dt.timestamp() - hold_s


def _cluster_trades(
    trades: list[dict],
    *,
    entry_tolerance_sec: int = 180,
    gap_fallback_sec: int = 900,
) -> list[_Cluster]:
    by_mint: dict[str, list[_Cluster]] = {}
    rows = sorted(trades, key=lambda t: _parse_iso(str(t.get("exit_timestamp") or "")) or datetime.min)
    for tr in rows:
        mint = str(tr.get("mint") or "UNKNOWN_MINT")
        dt = _parse_iso(str(tr.get("exit_timestamp") or ""))
        anchor = _entry_anchor_epoch(tr)
        bucket = by_mint.setdefault(mint, [])

        chosen: _Cluster | None = None
        if anchor is not None:
            best_dist: float | None = None
            for c in bucket:
                if c.approx_entry_epoch is None:
                    continue
                dist = abs(c.approx_entry_epoch - anchor)
                if dist <= float(entry_tolerance_sec) and (best_dist is None or dist < best_dist):
                    best_dist = dist
                    chosen = c
        if chosen is None and anchor is None and dt is not None and bucket:
            last = bucket[-1]
            if last.end_ts is not None:
                gap = (dt - last.end_ts).total_seconds()
                if 0 <= gap <= float(gap_fallback_sec):
                    chosen = last

        if chosen is None:
            chosen = _Cluster(mint=mint, approx_entry_epoch=anchor, end_ts=dt)
            bucket.append(chosen)

        chosen.trade_count += 1
        chosen.pnl_usd += float(tr.get("pnl_usd") or 0.0)
        if dt is not None and (chosen.end_ts is None or dt > chosen.end_ts):
            chosen.end_ts = dt

    out: list[_Cluster] = []
    for xs in by_mint.values():
        out.extend(xs)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB_DEFAULT))
    ap.add_argument("--debug", default=str(DEBUG_DEFAULT))
    ap.add_argument("--log", default=str(LOG_DEFAULT))
    ap.add_argument("--run-id", default="")
    ap.add_argument("--auto-run-id", action="store_true")
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--min-trades", type=int, default=40)
    ap.add_argument("--min-winrate", type=float, default=0.50)
    ap.add_argument("--min-pnl", type=float, default=0.0)
    ap.add_argument("--max-drawdown", type=float, default=10.0)
    ap.add_argument("--max-tail-loss-share", type=float, default=0.35)
    ap.add_argument(
        "--max-cluster-tail-loss-share",
        type=float,
        default=1.0,
        help="Optional normalized tail-loss gate; set <1.0 to enforce",
    )
    ap.add_argument("--min-clusters", type=int, default=0, help="Optional normalized sample-size gate")
    ap.add_argument("--cluster-entry-tolerance-sec", type=int, default=180)
    ap.add_argument("--cluster-gap-fallback-sec", type=int, default=900)
    args = ap.parse_args()

    run_id = str(args.run_id or "").strip()
    if args.auto_run_id and not run_id:
        run_id = _auto_run_id(Path(args.log))
    if not run_id:
        print("Could not resolve run_id.")
        return 1

    trades = _load_run_trades(Path(args.db), run_id, float(args.hours))
    debug = _load_run_debug(Path(args.debug), run_id, float(args.hours))
    clusters = _cluster_trades(
        trades,
        entry_tolerance_sec=int(args.cluster_entry_tolerance_sec),
        gap_fallback_sec=int(args.cluster_gap_fallback_sec),
    )

    n = len(trades)
    pnl = sum(float(t["pnl_usd"]) for t in trades)
    wins = sum(1 for t in trades if float(t["pnl_usd"]) > 0)
    wr = (wins / n) if n else 0.0
    max_dd = _max_drawdown(trades)

    losses = [abs(float(t["pnl_usd"])) for t in trades if float(t["pnl_usd"]) < 0]
    total_loss = sum(losses)
    largest_loss = max(losses) if losses else 0.0
    tail_share = (largest_loss / total_loss) if total_loss > 0 else 0.0
    c_n = len(clusters)
    c_wins = sum(1 for c in clusters if c.pnl_usd > 0)
    c_wr = (c_wins / c_n) if c_n else 0.0
    c_losses = [abs(float(c.pnl_usd)) for c in clusters if float(c.pnl_usd) < 0]
    c_total_loss = sum(c_losses)
    c_largest_loss = max(c_losses) if c_losses else 0.0
    c_tail_share = (c_largest_loss / c_total_loss) if c_total_loss > 0 else 0.0

    reasons = Counter(str(t["exit_reason"] or "UNKNOWN") for t in trades)

    pass_prequote = int(debug.get("pass_prequote", 0))
    reject_hits = int(debug.get("reject_prequote_hits", 0))
    reject_top = int(debug.get("reject_prequote_top_share", 0))
    reject_mcap = int(debug.get("reject_mcap_low", 0))
    prequote_total = pass_prequote + reject_hits + reject_top + reject_mcap
    pass_rate = (pass_prequote / prequote_total) if prequote_total > 0 else 0.0

    gates: list[tuple[bool, str, str]] = [
        (n >= int(args.min_trades), "Sample Size", f"{n} trades >= {int(args.min_trades)}"),
        (wr >= float(args.min_winrate), "Win Rate", f"{wr:.1%} >= {float(args.min_winrate):.1%}"),
        (pnl >= float(args.min_pnl), "Net PnL", f"${pnl:+.2f} >= ${float(args.min_pnl):+.2f}"),
        (max_dd <= float(args.max_drawdown), "Max Drawdown", f"${max_dd:.2f} <= ${float(args.max_drawdown):.2f}"),
        (
            tail_share <= float(args.max_tail_loss_share),
            "Tail-Loss Concentration",
            f"{tail_share:.1%} <= {float(args.max_tail_loss_share):.1%}",
        ),
    ]
    if int(args.min_clusters) > 0:
        gates.append(
            (
                c_n >= int(args.min_clusters),
                "Cluster Sample Size",
                f"{c_n} clusters >= {int(args.min_clusters)}",
            )
        )
    if float(args.max_cluster_tail_loss_share) < 1.0:
        gates.append(
            (
                c_tail_share <= float(args.max_cluster_tail_loss_share),
                "Cluster Tail-Loss Concentration",
                f"{c_tail_share:.1%} <= {float(args.max_cluster_tail_loss_share):.1%}",
            )
        )

    score = sum(1 for ok, _, _ in gates if ok)
    ready = score == len(gates)

    print(f"# Live Readiness: {run_id}")
    print(f"- Window: last {args.hours:g}h")
    print(f"- Ready: {'YES' if ready else 'NO'} ({score}/{len(gates)} gates)")
    print(f"- Trades: {n} | Win rate: {wr:.1%} | PnL: ${pnl:+.2f} | Max DD: ${max_dd:.2f}")
    print(f"- Clusters (normalized): {c_n} | Cluster win rate: {c_wr:.1%}")
    print(f"- Cluster largest-loss share: {c_tail_share:.1%}")
    print(f"- Largest-loss share: {tail_share:.1%}")
    print("")
    print("## Gates")
    for ok, name, detail in gates:
        print(_fmt_gate(ok, name, detail))
    print("")
    print("## Funnel")
    print(f"- pass_prequote: {pass_prequote}")
    print(f"- reject_prequote_hits: {reject_hits}")
    print(f"- reject_prequote_top_share: {reject_top}")
    print(f"- reject_mcap_low: {reject_mcap}")
    print(f"- prequote_pass_rate: {pass_rate:.1%} (over selected reject/pass events)")
    print("")
    print("## Top Exit Reasons")
    for k, v in reasons.most_common(8):
        print(f"- {k}: {v}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
