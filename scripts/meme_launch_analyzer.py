#!/usr/bin/env python3
"""Analyze launch features and find threshold combos with best future returns.

Usage:
  python scripts/meme_launch_analyzer.py --input data/meme_launch_features.csv
"""
from __future__ import annotations

import argparse
import csv
import math


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Launch features CSV")
    parser.add_argument("--min-samples", type=int, default=8, help="Min rows per combo")
    parser.add_argument("--out", default="", help="Optional output file for results")
    args = parser.parse_args()

    rows = []
    with open(args.input, "r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for r in reader:
            try:
                rows.append({
                    "max_return_future": float(r.get("max_return_future", 0) or 0),
                    "buys_5m": int(r.get("buys_5m", 0) or 0),
                    "txns_5m": int(r.get("txns_5m", 0) or 0),
                    "volume_5m": float(r.get("volume_5m", 0) or 0),
                    "price_accel": float(r.get("price_accel", 0) or 0),
                })
            except Exception:
                continue

    if not rows:
        print("No rows to analyze.")
        return

    buys_grid = [5, 10, 15]
    txns_grid = [10, 20, 30]
    vol_grid = [1000, 2000, 5000]
    accel_grid = [0.0, 0.05]

    results = []
    for b in buys_grid:
        for t in txns_grid:
            for v in vol_grid:
                for a in accel_grid:
                    subset = [
                        r for r in rows
                        if r["buys_5m"] >= b
                        and r["txns_5m"] >= t
                        and r["volume_5m"] >= v
                        and r["price_accel"] >= a
                    ]
                    n = len(subset)
                    if n < args.min_samples:
                        continue
                    avg = sum(r["max_return_future"] for r in subset) / n
                    gt50 = sum(1 for r in subset if r["max_return_future"] >= 0.5)
                    gt100 = sum(1 for r in subset if r["max_return_future"] >= 1.0)
                    score = avg * math.log(n + 1)
                    results.append({
                        "buys_5m": b,
                        "txns_5m": t,
                        "volume_5m": v,
                        "price_accel": a,
                        "n": n,
                        "avg": avg,
                        "gt50": gt50,
                        "gt100": gt100,
                        "score": score,
                    })

    results.sort(key=lambda x: x["score"], reverse=True)

    lines = []
    lines.append("Top launch threshold combos (by avg*log(n)):")
    for r in results[:10]:
        lines.append(
            f"b{r['buys_5m']} t{r['txns_5m']} v{int(r['volume_5m'])} a{r['price_accel']:.2f} | "
            f"n={r['n']} avg={r['avg']:.3f} gt50={r['gt50']} gt100={r['gt100']} score={r['score']:.3f}"
        )

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    else:
        print("\n".join(lines))


if __name__ == "__main__":
    main()
