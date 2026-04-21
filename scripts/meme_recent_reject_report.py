#!/usr/bin/env python3
"""Summarize recent reject reasons and missed winners from signal outcomes."""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
DEBUG_GLOB = "data/meme_signal_debug_cont_*.jsonl"
SIGNALS = BASE / "data" / "meme_launch_signals.jsonl"
OUTCOMES = BASE / "data" / "signal_outcomes.jsonl"
OUT_MD = BASE / "data" / "meme_reports" / "recent_reject_report.md"
OUT_JSON = BASE / "data" / "meme_reports" / "recent_reject_report.json"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def _lane_from_path(path: Path) -> str:
    stem = path.stem
    prefix = "meme_signal_debug_cont_"
    if stem.startswith(prefix):
        return stem[len(prefix):]
    return stem


def build_report(since_min: int, horizon_s: int, min_ret: float) -> dict[str, Any]:
    now = time.time()
    since_ts = now - max(1, since_min) * 60

    signal_rows = _load_jsonl(SIGNALS)
    signal_by_mint: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in signal_rows:
        mint = str(row.get("mint") or "").strip()
        if not mint:
            continue
        try:
            ts = float(row.get("ts") or row.get("timestamp") or 0.0)
        except Exception:
            ts = 0.0
        if ts <= 0:
            continue
        signal_by_mint[mint].append(row)
    for mint in signal_by_mint:
        signal_by_mint[mint].sort(key=lambda row: float(row.get("ts") or row.get("timestamp") or 0.0))

    outcome_rows = _load_jsonl(OUTCOMES)
    outcome_map: dict[tuple[str, float], dict[str, Any]] = {}
    for row in outcome_rows:
        mint = str(row.get("mint") or "").strip()
        if not mint:
            continue
        try:
            if int(row.get("horizon_s") or 0) != int(horizon_s):
                continue
        except Exception:
            continue
        try:
            sig_ts = float(row.get("signal_ts") or 0.0)
        except Exception:
            sig_ts = 0.0
        if sig_ts <= 0:
            continue
        outcome_map[(mint, sig_ts)] = row

    rejects: list[dict[str, Any]] = []
    for path in sorted(BASE.glob(DEBUG_GLOB)):
        lane = _lane_from_path(path)
        for row in _load_jsonl(path):
            kind = str(row.get("kind") or "")
            if not kind.startswith("reject_"):
                continue
            try:
                ts = float(row.get("ts") or 0.0)
            except Exception:
                ts = 0.0
            if ts < since_ts:
                continue
            mint = str(row.get("mint") or "").strip()
            if not mint:
                continue
            rejects.append(
                {
                    "ts": ts,
                    "lane": lane,
                    "kind": kind,
                    "mint": mint,
                    "symbol": row.get("symbol"),
                    "extra": row.get("extra") if isinstance(row.get("extra"), dict) else {},
                    "signal_profile": row.get("signal_profile"),
                }
            )

    reason_counts = Counter()
    lane_counts: dict[str, Counter[str]] = defaultdict(Counter)
    missed_winners: list[dict[str, Any]] = []
    missed_seen: set[tuple[str, str, str, int]] = set()

    for rej in rejects:
        kind = str(rej["kind"])
        lane = str(rej["lane"])
        reason_counts[kind] += 1
        lane_counts[lane][kind] += 1

        candidates = signal_by_mint.get(rej["mint"], [])
        if not candidates:
            continue
        chosen: dict[str, Any] | None = None
        best_gap = 999999999.0
        for sig in candidates:
            try:
                sig_ts = float(sig.get("ts") or sig.get("timestamp") or 0.0)
            except Exception:
                continue
            gap = abs(float(rej["ts"]) - sig_ts)
            if gap <= 900 and gap < best_gap:
                best_gap = gap
                chosen = sig
        if not chosen:
            continue
        sig_ts = float(chosen.get("ts") or chosen.get("timestamp") or 0.0)
        outcome = outcome_map.get((rej["mint"], sig_ts))
        if not outcome:
            continue
        try:
            ret = float(outcome.get("ret") or 0.0)
        except Exception:
            continue
        if ret < float(min_ret):
            continue
        metrics = chosen.get("metrics") if isinstance(chosen.get("metrics"), dict) else {}
        dedupe_key = (lane, rej["mint"], kind, int(sig_ts))
        if dedupe_key in missed_seen:
            continue
        missed_seen.add(dedupe_key)
        missed_winners.append(
            {
                "lane": lane,
                "kind": kind,
                "symbol": rej.get("symbol") or metrics.get("symbol") or rej["mint"][:6],
                "mint": rej["mint"],
                "ret": ret,
                "market_cap": metrics.get("market_cap") or metrics.get("market_cap_usd"),
                "price_change_5m": metrics.get("price_change_5m"),
                "hits": metrics.get("hits"),
                "pair_age_min": metrics.get("pair_age_min"),
                "mover_pattern": metrics.get("mover_pattern"),
                "buy_sell_ratio": metrics.get("buy_sell_ratio"),
                "signal_ts": sig_ts,
                "reject_ts": rej["ts"],
            }
        )

    missed_winners.sort(key=lambda row: float(row.get("ret") or 0.0), reverse=True)
    top_reasons = [{"reason": reason, "count": count} for reason, count in reason_counts.most_common(12)]
    lane_summary = {
        lane: [{"reason": reason, "count": count} for reason, count in counter.most_common(8)]
        for lane, counter in sorted(lane_counts.items())
    }
    return {
        "generated_at": now,
        "since_min": since_min,
        "horizon_s": horizon_s,
        "min_ret": min_ret,
        "reject_count": len(rejects),
        "top_reasons": top_reasons,
        "lane_summary": lane_summary,
        "missed_winners": missed_winners[:25],
    }


def write_report(report: dict[str, Any]) -> None:
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    lines: list[str] = []
    lines.append("# Recent Reject Report")
    lines.append("")
    lines.append(f"- Since: last {report['since_min']} min")
    lines.append(f"- Horizon: {report['horizon_s']}s")
    lines.append(f"- Reject events: {report['reject_count']}")
    lines.append("")
    lines.append("## Top Reasons")
    for row in report.get("top_reasons", []):
        lines.append(f"- {row['reason']}: {row['count']}")
    lines.append("")
    lines.append("## Lane Summary")
    for lane, rows in (report.get("lane_summary") or {}).items():
        lines.append(f"### {lane}")
        for row in rows:
            lines.append(f"- {row['reason']}: {row['count']}")
    lines.append("")
    lines.append("## Missed Winners")
    if report.get("missed_winners"):
        for row in report["missed_winners"]:
            lines.append(
                "- "
                f"{row['lane']} {row['symbol']} {row['mint']} "
                f"ret={float(row['ret']):+.1%} "
                f"reason={row['kind']} "
                f"mcap={row.get('market_cap')} "
                f"mom5m={row.get('price_change_5m')} "
                f"hits={row.get('hits')} "
                f"age_min={row.get('pair_age_min')} "
                f"pattern={row.get('mover_pattern')} "
                f"bs={row.get('buy_sell_ratio')}"
            )
    else:
        lines.append("- none")
    lines.append("")
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since-min", type=int, default=360)
    parser.add_argument("--horizon-s", type=int, default=900)
    parser.add_argument("--min-ret", type=float, default=0.20)
    args = parser.parse_args()

    report = build_report(args.since_min, args.horizon_s, args.min_ret)
    write_report(report)
    print(f"wrote {OUT_MD}")
    print(f"wrote {OUT_JSON}")
    print(json.dumps({
        "reject_count": report["reject_count"],
        "top_reasons": report["top_reasons"][:5],
        "missed_winners": len(report.get("missed_winners") or []),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
