#!/usr/bin/env python3
"""Summarize Helius event logs by program and time bucket.

Usage:
  python scripts/helius_event_stats.py --input data/helius_events.jsonl
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Helius events JSONL")
    parser.add_argument("--out", default="data/helius_event_stats.json", help="Output JSON")
    parser.add_argument("--bucket", type=int, default=60, help="Seconds per bucket")
    args = parser.parse_args()

    by_program = defaultdict(int)
    by_bucket = defaultdict(int)

    with open(args.input, "r", encoding="utf-8") as fh:
        for line in fh:
            try:
                evt = json.loads(line)
            except Exception:
                continue
            pid = evt.get("program_id") or "unknown"
            ts = float(evt.get("ts", 0) or 0)
            by_program[pid] += 1
            if ts:
                b = int(ts - (ts % args.bucket))
                by_bucket[b] += 1

    stats = {
        "program_counts": dict(sorted(by_program.items(), key=lambda x: x[1], reverse=True)),
        "bucket_counts": {datetime.utcfromtimestamp(k).isoformat(): v for k, v in sorted(by_bucket.items())},
    }

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(stats, fh, indent=2)

    print(f"Wrote stats to {args.out}")


if __name__ == "__main__":
    main()
