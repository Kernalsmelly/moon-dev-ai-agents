#!/usr/bin/env python3
"""Summarize recent signal-debug reject reasons for fast tuning decisions."""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="data/meme_signal_debug.jsonl")
    ap.add_argument("--minutes", type=int, default=120)
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--run-id", default="", help="optional run_id filter")
    ap.add_argument(
        "--include-non-reject",
        action="store_true",
        help="include all debug kinds (default: only reject/skip/suppressed kinds)",
    )
    args = ap.parse_args()

    p = Path(args.file)
    if not p.exists():
        print(f"missing: {p}")
        return 1

    cutoff = time.time() - (args.minutes * 60)
    run_id = str(args.run_id or "").strip()
    reasons = Counter()
    examples: dict[str, list[dict]] = {}

    # Keep memory bounded: recent tail is enough for tuning.
    lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()[-50000:]
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        ts = float(row.get("ts") or 0)
        if ts < cutoff:
            continue
        if run_id:
            if str(row.get("run_id") or "").strip() != run_id:
                continue
        kind = str(row.get("kind") or "").strip()
        if not kind:
            continue
        if not args.include_non_reject:
            if not (
                kind.startswith("reject_")
                or kind.startswith("skip_")
                or kind in ("winner_zone_match_suppressed",)
            ):
                continue
        reasons[kind] += 1
        if kind not in examples:
            examples[kind] = []
        if len(examples[kind]) < 3:
            examples[kind].append(
                {
                    "mint": row.get("mint"),
                    "symbol": row.get("symbol"),
                    "mcap": (row.get("extra") or {}).get("mcap") if isinstance(row.get("extra"), dict) else None,
                    "min_mcap": (row.get("extra") or {}).get("min_mcap") if isinstance(row.get("extra"), dict) else None,
                    "liq": (row.get("m") or {}).get("liquidity") if isinstance(row.get("m"), dict) else None,
                    "net_sol_in": (row.get("m") or {}).get("net_sol_in") if isinstance(row.get("m"), dict) else None,
                    "unique_buyers": (row.get("m") or {}).get("unique_buyers") if isinstance(row.get("m"), dict) else None,
                    "top_buyer_share": (row.get("m") or {}).get("top_buyer_share") if isinstance(row.get("m"), dict) else None,
                }
            )

    total = sum(reasons.values())
    if run_id:
        print(f"window={args.minutes}m run_id={run_id} rejects={total}")
    else:
        print(f"window={args.minutes}m rejects={total}")
    for kind, n in reasons.most_common(max(1, args.top)):
        pct = (n / total * 100.0) if total else 0.0
        print(f"{kind:24s} n={n:5d} ({pct:5.1f}%)")
        ex = examples.get(kind) or []
        for e in ex:
            print(
                "  "
                + f"symbol={str(e.get('symbol') or ''):10s} "
                + f"mcap={e.get('mcap')} min_mcap={e.get('min_mcap')} "
                + f"net_sol={e.get('net_sol_in')} uniq={e.get('unique_buyers')} top_share={e.get('top_buyer_share')}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
