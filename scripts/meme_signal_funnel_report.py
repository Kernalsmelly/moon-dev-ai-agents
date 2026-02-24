#!/usr/bin/env python3
"""Summarize signal funnel health for a run (pass/reject by stage, unique mints)."""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from pathlib import Path


def _stage_for_kind(kind: str) -> str:
    k = str(kind or "").strip()
    if not k:
        return "unknown"
    if k == "pass_prequote":
        return "prequote_pass"
    if k.startswith("reject_prequote_"):
        return "prequote_reject"
    if k == "pass_winner_zone":
        return "winner_zone_pass"
    if k in ("winner_zone_match_suppressed", "reject_winner_zone"):
        return "winner_zone_reject"
    if k in ("reject_age", "reject_age_fresh"):
        return "age_reject"
    if k in ("reject_mcap_low", "reject_mcap_confirm", "reject_mcap_high"):
        return "mcap_reject"
    if k == "reject_quote":
        return "quote_reject"
    if k == "reject_entry_pattern":
        return "entry_pattern_reject"
    if k.startswith("reject_entry_"):
        return "entry_risk_reject"
    if k.startswith("skip_entry_size_floor"):
        return "size_floor_skip"
    if k == "entry_sellability_pass":
        return "entry_risk_pass"
    if k.startswith("pass_"):
        return "other_pass"
    if k.startswith("reject_") or k.startswith("skip_"):
        return "other_reject"
    return "other"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="data/meme_signal_debug.jsonl")
    ap.add_argument("--minutes", type=int, default=120)
    ap.add_argument("--run-id", default="", help="optional run_id filter")
    ap.add_argument("--top-kinds", type=int, default=15)
    ap.add_argument("--tail-lines", type=int, default=120000)
    args = ap.parse_args()

    p = Path(args.file)
    if not p.exists():
        print(f"missing: {p}")
        return 1

    cutoff = time.time() - (args.minutes * 60)
    run_id = str(args.run_id or "").strip()

    kind_events = Counter()
    kind_mints: dict[str, set[str]] = defaultdict(set)
    stage_events = Counter()
    stage_mints: dict[str, set[str]] = defaultdict(set)
    all_mints: set[str] = set()

    lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()[-max(1000, int(args.tail_lines)) :]
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        ts = float(row.get("ts") or 0.0)
        if ts < cutoff:
            continue
        if run_id and str(row.get("run_id") or "").strip() != run_id:
            continue
        kind = str(row.get("kind") or "").strip()
        mint = str(row.get("mint") or "").strip()
        if not kind or not mint:
            continue

        stage = _stage_for_kind(kind)
        kind_events[kind] += 1
        kind_mints[kind].add(mint)
        stage_events[stage] += 1
        stage_mints[stage].add(mint)
        all_mints.add(mint)

    print(
        f"window={args.minutes}m run_id={run_id or 'ALL'} "
        f"events={sum(kind_events.values())} unique_mints={len(all_mints)}"
    )

    stage_order = [
        "prequote_pass",
        "prequote_reject",
        "winner_zone_pass",
        "winner_zone_reject",
        "age_reject",
        "mcap_reject",
        "quote_reject",
        "entry_pattern_reject",
        "entry_risk_pass",
        "entry_risk_reject",
        "size_floor_skip",
        "other_pass",
        "other_reject",
        "other",
    ]
    print("stage_summary (events | unique_mints):")
    for stage in stage_order:
        ev = int(stage_events.get(stage, 0))
        um = len(stage_mints.get(stage, set()))
        if ev <= 0 and um <= 0:
            continue
        print(f"  {stage:22s} {ev:6d} | {um:5d}")

    total_events = sum(kind_events.values()) or 1
    print("kind_breakdown (events | unique_mints | pct):")
    for kind, n in kind_events.most_common(max(1, int(args.top_kinds))):
        um = len(kind_mints.get(kind, set()))
        pct = 100.0 * float(n) / float(total_events)
        print(f"  {kind:28s} {n:6d} | {um:5d} | {pct:5.1f}%")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

