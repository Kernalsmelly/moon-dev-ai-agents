#!/usr/bin/env python3
"""Compare baseline vs winner-zone A/B paper lanes."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
META = BASE / "data" / "meme_ab_zone_runner.json"


@dataclass
class LaneSummary:
    name: str
    run_id: str
    db_path: Path
    trades: int
    wins: int
    pnl_usd: float
    avg_pnl_usd: float
    median_pnl_usd: float
    cluster_count: int
    loss_cluster_share: float
    dominant_cluster_leg_share: float
    zone_tagged_trades: int
    top_reasons: list[tuple[str, int]]
    dbg_events: int
    dbg_pass_prequote: int
    dbg_zone_match_passes: int
    dbg_zone_bypass_passes: int
    dbg_reject_total: int
    dbg_top_rejects: list[tuple[str, int]]
    entry_attribution_counts: dict[str, int]
    entry_attribution_pnl_usd: dict[str, float]


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _safe_dt(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _cluster_stats(rows: list[dict[str, Any]], entry_tol_s: int, gap_fallback_s: int) -> tuple[int, float, float]:
    if not rows:
        return 0, 0.0, 0.0

    rows_sorted = sorted(rows, key=lambda r: float(r.get("ts") or 0.0))
    by_mint: dict[str, list[dict[str, Any]]] = {}
    for tr in rows_sorted:
        mint = str(tr.get("mint") or "_unknown_")
        ts = float(tr.get("ts") or 0.0)
        entry_anchor = float(tr.get("entry_anchor") or ts)
        pnl = float(tr.get("pnl") or 0.0)

        bucket = by_mint.setdefault(mint, [])
        chosen: dict[str, Any] | None = None
        for c in reversed(bucket):
            same_entry = abs(entry_anchor - float(c.get("entry_anchor") or 0.0)) <= float(entry_tol_s)
            close_exit = (ts - float(c.get("last_ts") or 0.0)) <= float(gap_fallback_s)
            if same_entry or close_exit:
                chosen = c
                break
        if chosen is None:
            chosen = {"entry_anchor": entry_anchor, "last_ts": ts, "trade_count": 0, "pnl_usd": 0.0}
            bucket.append(chosen)

        chosen["trade_count"] = int(chosen.get("trade_count") or 0) + 1
        chosen["last_ts"] = max(float(chosen.get("last_ts") or 0.0), ts)
        chosen["pnl_usd"] = float(chosen.get("pnl_usd") or 0.0) + pnl

    clusters: list[dict[str, Any]] = []
    for xs in by_mint.values():
        clusters.extend(xs)
    if not clusters:
        return 0, 0.0, 0.0

    n = max(1, len(rows_sorted))
    dominant_leg_share = max(float(c.get("trade_count") or 0.0) for c in clusters) / float(n)
    losses = [abs(float(c.get("pnl_usd") or 0.0)) for c in clusters if float(c.get("pnl_usd") or 0.0) < 0.0]
    loss_cluster_share = (max(losses) / sum(losses)) if losses and sum(losses) > 0 else 0.0
    return len(clusters), float(loss_cluster_share), float(dominant_leg_share)


def _load_debug_summary(
    run_id: str, debug_path: Path, minutes: float
) -> tuple[int, int, int, int, int, list[tuple[str, int]]]:
    if not run_id or not debug_path.exists():
        return 0, 0, 0, 0, 0, []
    cutoff = time.time() - max(1.0, float(minutes)) * 60.0
    events = 0
    pass_prequote = 0
    zone_match_passes = 0
    zone_bypass_passes = 0
    reject_total = 0
    rejects = Counter()
    with debug_path.open("r", encoding="utf-8") as fh:
        for ln in fh:
            try:
                obj = json.loads(ln)
            except Exception:
                continue
            if str(obj.get("run_id") or "").strip() != run_id:
                continue
            ts = _safe_float(obj.get("ts"), 0.0)
            if ts > 0 and ts < cutoff:
                continue
            events += 1
            kind = str(obj.get("kind") or "")
            if kind == "pass_prequote":
                pass_prequote += 1
            if kind == "pass_winner_zone":
                zone_match_passes += 1
            if kind == "pass_winner_zone_bypass":
                zone_bypass_passes += 1
            if kind.startswith("reject_"):
                reject_total += 1
                rejects[kind] += 1
    return events, pass_prequote, zone_match_passes, zone_bypass_passes, reject_total, rejects.most_common(5)


def _load_lane_summary(
    name: str,
    run_id: str,
    db_path: Path,
    entry_tol_s: int,
    gap_fallback_s: int,
    debug_path: Path,
    debug_minutes: float,
) -> LaneSummary:
    if not db_path.exists():
        d = _load_debug_summary(run_id, debug_path, debug_minutes)
        return LaneSummary(
            name,
            run_id,
            db_path,
            0,
            0,
            0.0,
            0.0,
            0.0,
            0,
            0.0,
            0.0,
            0,
            [],
            d[0],
            d[1],
            d[2],
            d[3],
            d[4],
            d[5],
            {"zone_match": 0, "zone_bypass": 0, "non_zone": 0},
            {"zone_match": 0.0, "zone_bypass": 0.0, "non_zone": 0.0},
        )

    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    rows_db = cur.execute(
        "SELECT mint, pnl_usd, exit_reason, metadata, exit_timestamp FROM trades ORDER BY exit_timestamp ASC"
    ).fetchall()
    con.close()

    rows: list[dict[str, Any]] = []
    pnls: list[float] = []
    wins = 0
    zone_tagged = 0
    reasons: dict[str, int] = {}
    attrib_counts = {"zone_match": 0, "zone_bypass": 0, "non_zone": 0}
    attrib_pnl = {"zone_match": 0.0, "zone_bypass": 0.0, "non_zone": 0.0}

    for r in rows_db:
        md_raw = r["metadata"] or "{}"
        try:
            md = json.loads(md_raw) if isinstance(md_raw, str) else (md_raw if isinstance(md_raw, dict) else {})
        except Exception:
            md = {}
        rid = str((md or {}).get("run_id") or "").strip()
        if rid != run_id:
            continue

        pnl = _safe_float(r["pnl_usd"], 0.0)
        pnls.append(pnl)
        if pnl > 0:
            wins += 1

        reason = str(r["exit_reason"] or "UNKNOWN")
        reasons[reason] = int(reasons.get(reason) or 0) + 1

        zone_id = str((md or {}).get("winner_zone_id") or "").strip()
        zone_bypassed = bool((md or {}).get("winner_zone_bypassed", False))
        if zone_id:
            zone_tagged += 1
        tag = "zone_bypass" if zone_bypassed else ("zone_match" if zone_id else "non_zone")
        attrib_counts[tag] = int(attrib_counts.get(tag) or 0) + 1
        attrib_pnl[tag] = float(attrib_pnl.get(tag) or 0.0) + float(pnl)

        dt = _safe_dt(str(r["exit_timestamp"] or ""))
        ts = float(dt.timestamp()) if dt else 0.0
        hold_s = _safe_float((md or {}).get("hold_time_sec"), 0.0)
        entry_anchor = float(ts - hold_s) if hold_s > 0 else ts

        rows.append(
            {
                "mint": str(r["mint"] or ""),
                "pnl": pnl,
                "ts": ts,
                "entry_anchor": entry_anchor,
            }
        )

    n = len(pnls)
    pnl_total = float(sum(pnls))
    avg = (pnl_total / float(n)) if n > 0 else 0.0
    med = sorted(pnls)[n // 2] if n > 0 else 0.0
    cn, tail, dom_legs = _cluster_stats(rows, entry_tol_s=entry_tol_s, gap_fallback_s=gap_fallback_s)
    top_reasons = sorted(reasons.items(), key=lambda kv: kv[1], reverse=True)[:5]
    d = _load_debug_summary(run_id, debug_path, debug_minutes)
    return LaneSummary(
        name=name,
        run_id=run_id,
        db_path=db_path,
        trades=n,
        wins=wins,
        pnl_usd=pnl_total,
        avg_pnl_usd=float(avg),
        median_pnl_usd=float(med),
        cluster_count=cn,
        loss_cluster_share=float(tail),
        dominant_cluster_leg_share=float(dom_legs),
        zone_tagged_trades=zone_tagged,
        top_reasons=top_reasons,
        dbg_events=int(d[0]),
        dbg_pass_prequote=int(d[1]),
        dbg_zone_match_passes=int(d[2]),
        dbg_zone_bypass_passes=int(d[3]),
        dbg_reject_total=int(d[4]),
        dbg_top_rejects=d[5],
        entry_attribution_counts=attrib_counts,
        entry_attribution_pnl_usd=attrib_pnl,
    )


def _wr(x: LaneSummary) -> float:
    return (float(x.wins) / float(x.trades)) if x.trades > 0 else 0.0


def _fmt_pct(x: float) -> str:
    return f"{x * 100.0:.1f}%"


def _fmt_usd(x: float) -> str:
    return f"${x:+.2f}"


def _lane_to_dict(x: LaneSummary) -> dict[str, Any]:
    wr = _wr(x)
    return {
        "name": x.name,
        "run_id": x.run_id,
        "db_path": str(x.db_path),
        "trades": int(x.trades),
        "wins": int(x.wins),
        "winrate": float(wr),
        "pnl_usd": float(x.pnl_usd),
        "avg_pnl_usd": float(x.avg_pnl_usd),
        "median_pnl_usd": float(x.median_pnl_usd),
        "cluster_count": int(x.cluster_count),
        "loss_cluster_share": float(x.loss_cluster_share),
        "dominant_cluster_leg_share": float(x.dominant_cluster_leg_share),
        "zone_tagged_trades": int(x.zone_tagged_trades),
        "top_reasons": list(x.top_reasons),
        "debug": {
            "events": int(x.dbg_events),
            "pass_prequote": int(x.dbg_pass_prequote),
            "zone_match_passes": int(x.dbg_zone_match_passes),
            "zone_bypass_passes": int(x.dbg_zone_bypass_passes),
            "reject_total": int(x.dbg_reject_total),
            "top_rejects": list(x.dbg_top_rejects),
            "prequote_pass_rate": float(
                float(x.dbg_pass_prequote) / float(max(1, int(x.dbg_pass_prequote) + int(x.dbg_reject_total)))
            ),
        },
        "entry_attribution": {
            "counts": dict(x.entry_attribution_counts),
            "pnl_usd": {k: float(v) for k, v in x.entry_attribution_pnl_usd.items()},
        },
    }


def _summary_dict(base: LaneSummary, zone: LaneSummary, debug_minutes: float) -> dict[str, Any]:
    b = _lane_to_dict(base)
    z = _lane_to_dict(zone)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "debug_window_min": float(debug_minutes),
        "base": b,
        "zone": z,
        "delta": {
            "trades": int(z["trades"] - b["trades"]),
            "winrate": float(z["winrate"] - b["winrate"]),
            "pnl_usd": float(z["pnl_usd"] - b["pnl_usd"]),
            "avg_pnl_usd": float(z["avg_pnl_usd"] - b["avg_pnl_usd"]),
            "loss_cluster_share": float(z["loss_cluster_share"] - b["loss_cluster_share"]),
            "dominant_cluster_leg_share": float(
                z["dominant_cluster_leg_share"] - b["dominant_cluster_leg_share"]
            ),
            "debug_prequote_pass_rate": float(z["debug"]["prequote_pass_rate"] - b["debug"]["prequote_pass_rate"]),
            "debug_zone_match_passes": int(z["debug"]["zone_match_passes"] - b["debug"]["zone_match_passes"]),
            "debug_zone_bypass_passes": int(z["debug"]["zone_bypass_passes"] - b["debug"]["zone_bypass_passes"]),
        },
    }


def _report_markdown(base: LaneSummary, zone: LaneSummary, debug_minutes: float) -> str:
    lines: list[str] = []
    lines.append("# A/B Zone Report")
    lines.append("")
    lines.append(f"- generated_at: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"- base_run_id: `{base.run_id}`")
    lines.append(f"- zone_run_id: `{zone.run_id}`")
    lines.append(f"- debug_window_min: {debug_minutes:g}")
    lines.append("")
    lines.append("## Lane Metrics")
    lines.append("")
    lines.append("| lane | trades | winrate | pnl_usd | avg_pnl | clusters | cluster_tail | dominant_leg_share | zone_tagged |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for x in (base, zone):
        lines.append(
            f"| {x.name} | {x.trades} | {_fmt_pct(_wr(x))} | {_fmt_usd(x.pnl_usd)} | {_fmt_usd(x.avg_pnl_usd)} "
            f"| {x.cluster_count} | {_fmt_pct(x.loss_cluster_share)} | {_fmt_pct(x.dominant_cluster_leg_share)} "
            f"| {x.zone_tagged_trades} |"
        )
    lines.append("")
    lines.append("## Delta (zone - base)")
    lines.append("")
    lines.append(f"- trades: {zone.trades - base.trades:+d}")
    lines.append(f"- winrate: {(100.0 * (_wr(zone) - _wr(base))):+.1f}pp")
    lines.append(f"- pnl_usd: {_fmt_usd(zone.pnl_usd - base.pnl_usd)}")
    lines.append(f"- avg_pnl_usd: {_fmt_usd(zone.avg_pnl_usd - base.avg_pnl_usd)}")
    lines.append(f"- cluster_tail: {(100.0 * (zone.loss_cluster_share - base.loss_cluster_share)):+.1f}pp")
    lines.append(
        f"- dominant_leg_share: {(100.0 * (zone.dominant_cluster_leg_share - base.dominant_cluster_leg_share)):+.1f}pp"
    )
    lines.append("")
    lines.append("## Top Exit Reasons")
    lines.append("")
    lines.append(f"- base: {', '.join([f'{k}={v}' for k, v in base.top_reasons]) or 'n/a'}")
    lines.append(f"- zone: {', '.join([f'{k}={v}' for k, v in zone.top_reasons]) or 'n/a'}")
    lines.append("")
    lines.append("## Signal Debug (Run-Scoped)")
    lines.append("")
    lines.append(
        "| lane | debug_events | pass_prequote | zone_match_passes | zone_bypass_passes | reject_total | prequote_pass_rate |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for x in (base, zone):
        denom = max(1, int(x.dbg_pass_prequote) + int(x.dbg_reject_total))
        pr = float(x.dbg_pass_prequote) / float(denom)
        lines.append(
            f"| {x.name} | {x.dbg_events} | {x.dbg_pass_prequote} | {x.dbg_zone_match_passes} | {x.dbg_zone_bypass_passes} | "
            f"{x.dbg_reject_total} | {_fmt_pct(pr)} |"
        )
    lines.append("")
    lines.append(f"- base top rejects: {', '.join([f'{k}={v}' for k, v in base.dbg_top_rejects]) or 'n/a'}")
    lines.append(f"- zone top rejects: {', '.join([f'{k}={v}' for k, v in zone.dbg_top_rejects]) or 'n/a'}")
    lines.append("")
    lines.append("## Entry Attribution")
    lines.append("")
    lines.append("| lane | zone_match_n | zone_match_pnl | zone_bypass_n | zone_bypass_pnl | non_zone_n | non_zone_pnl |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for x in (base, zone):
        c = x.entry_attribution_counts
        p = x.entry_attribution_pnl_usd
        lines.append(
            f"| {x.name} | {int(c.get('zone_match') or 0)} | {_fmt_usd(float(p.get('zone_match') or 0.0))} | "
            f"{int(c.get('zone_bypass') or 0)} | {_fmt_usd(float(p.get('zone_bypass') or 0.0))} | "
            f"{int(c.get('non_zone') or 0)} | {_fmt_usd(float(p.get('non_zone') or 0.0))} |"
        )
    lines.append("")
    if base.trades == 0 and zone.trades == 0:
        lines.append("No closed trades yet; keep lanes running and rerun this report.")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--meta", default=str(META))
    ap.add_argument("--out", default="")
    ap.add_argument("--entry-tol-sec", type=int, default=180)
    ap.add_argument("--gap-fallback-sec", type=int, default=900)
    ap.add_argument("--signal-debug", default=str(BASE / "data" / "meme_signal_debug.jsonl"))
    ap.add_argument("--signal-debug-minutes", type=float, default=180.0)
    ap.add_argument("--json-out", default="", help="Optional JSON summary output path")
    args = ap.parse_args()

    meta_path = Path(args.meta)
    if not meta_path.exists():
        raise SystemExit(f"missing meta: {meta_path}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    lanes = meta.get("lanes") if isinstance(meta, dict) else None
    if not isinstance(lanes, dict):
        raise SystemExit("invalid meta lanes")

    b = lanes.get("base") if isinstance(lanes.get("base"), dict) else {}
    z = lanes.get("zone") if isinstance(lanes.get("zone"), dict) else {}
    base_run = str(b.get("run_id") or "").strip()
    zone_run = str(z.get("run_id") or "").strip()
    base_db = Path(str(b.get("db") or ""))
    zone_db = Path(str(z.get("db") or ""))
    if not base_db.is_absolute():
        base_db = BASE / base_db
    if not zone_db.is_absolute():
        zone_db = BASE / zone_db

    debug_path = Path(args.signal_debug)
    base = _load_lane_summary(
        "base",
        base_run,
        base_db,
        args.entry_tol_sec,
        args.gap_fallback_sec,
        debug_path,
        float(args.signal_debug_minutes),
    )
    zone = _load_lane_summary(
        "zone",
        zone_run,
        zone_db,
        args.entry_tol_sec,
        args.gap_fallback_sec,
        debug_path,
        float(args.signal_debug_minutes),
    )
    report = _report_markdown(base, zone, float(args.signal_debug_minutes))
    summary = _summary_dict(base, zone, float(args.signal_debug_minutes))

    out = Path(args.out) if args.out else (BASE / "data" / "meme_reports" / f"ab_zone_{int(time.time())}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report + "\n", encoding="utf-8")
    json_out = Path(args.json_out) if args.json_out else out.with_suffix(".json")
    json_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {out}")
    print(f"wrote {json_out}")
    print(
        f"base trades={base.trades} wr={_fmt_pct(_wr(base))} pnl={_fmt_usd(base.pnl_usd)} | "
        f"zone trades={zone.trades} wr={_fmt_pct(_wr(zone))} pnl={_fmt_usd(zone.pnl_usd)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
