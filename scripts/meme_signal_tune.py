#!/usr/bin/env python3
"""Suggest signal threshold adjustments based on recent signal rate."""
from __future__ import annotations

import argparse
import json
import os


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stats", default="data/meme_signal_stats.jsonl")
    parser.add_argument("--target-low", type=float, default=20.0)
    parser.add_argument("--target-high", type=float, default=60.0)
    args = parser.parse_args()

    if not os.path.exists(args.stats):
        print("No stats file.")
        return

    last = None
    with open(args.stats, "r", encoding="utf-8") as fh:
        for line in fh:
            try:
                last = json.loads(line)
            except Exception:
                continue

    if not last:
        print("No stats entries.")
        return

    rate = float(last.get("signal_rate_per_hour", 0) or 0)
    if rate < args.target_low:
        print(f"Signal rate {rate}/h is low. Suggest: lower min-score or volume/liquidity thresholds.")
    elif rate > args.target_high:
        print(f"Signal rate {rate}/h is high. Suggest: raise min-score or volume/liquidity thresholds.")
    else:
        print(f"Signal rate {rate}/h is within target range.")


if __name__ == "__main__":
    main()
