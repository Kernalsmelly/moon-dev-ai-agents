#!/usr/bin/env python3
"""Analyze loss clusters by token age, liquidity, and price change buckets.

Usage:
  python scripts/meme_loss_cluster.py --input data/meme_replay_trades.csv
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict


def bucket(value: float, step: float) -> float:
    if step <= 0:
        return value
    return round((value // step) * step, 2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Replay trades CSV")
    args = parser.parse_args()

    losses = []
    with open(args.input, "r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            try:
                pnl = float(row.get("pnl_usd") or 0)
            except Exception:
                pnl = 0
            if pnl >= 0:
                continue
            losses.append(row)

    clusters = defaultdict(int)
    for row in losses:
        try:
            pnl = float(row.get("pnl_usd") or 0)
        except Exception:
            pnl = 0
        # simple buckets using entry/exit prices if available
        try:
            entry = float(row.get("entry_price") or 0)
            exitp = float(row.get("exit_price") or 0)
            pct = (exitp - entry) / entry * 100 if entry > 0 else 0
        except Exception:
            pct = 0
        b = bucket(pct, 5)
        clusters[b] += 1

    print("Loss clusters by pct bucket:")
    for k in sorted(clusters.keys()):
        print(f"{k:+.0f}%: {clusters[k]}")


if __name__ == "__main__":
    main()
