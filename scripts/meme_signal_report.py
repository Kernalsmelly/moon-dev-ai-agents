#!/usr/bin/env python3
"""Simple report: signals per hour and recent pass rate."""
from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--signals", default="data/meme_launch_signals.jsonl", help="Signals JSONL")
    parser.add_argument("--window-hours", type=int, default=6, help="Hours to include")
    args = parser.parse_args()

    cutoff = time.time() - (args.window_hours * 3600)
    buckets = defaultdict(int)
    total = 0

    try:
        with open(args.signals, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                ts = float(obj.get("ts", 0) or 0)
                if ts < cutoff:
                    continue
                hour = int(ts // 3600)
                buckets[hour] += 1
                total += 1
    except FileNotFoundError:
        print("No signals file found.")
        return

    print(f"Signals in last {args.window_hours}h: {total}")
    for hour, count in sorted(buckets.items()):
        print(f"hour={hour}: {count}")


if __name__ == "__main__":
    main()
