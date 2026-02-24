#!/usr/bin/env python3
"""Derive launch threshold config from launch feature CSV.

Usage:
  python scripts/meme_threshold_builder.py --input data/meme_launch_features.csv --out config/meme_early_edge_auto.json
"""
from __future__ import annotations

import argparse
import csv
import json
import math


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Launch features CSV")
    parser.add_argument("--out", required=True, help="Output config JSON")
    parser.add_argument("--min-samples", type=int, default=8, help="Min rows per combo")
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
                    "volume_1h": float(r.get("volume_1h", 0) or 0),
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
    share_grid = [0.0, 0.10, 0.20]

    best = None
    for b in buys_grid:
        for t in txns_grid:
            for v in vol_grid:
                for a in accel_grid:
                    for s in share_grid:
                        subset = []
                        for r in rows:
                            share = (r["volume_5m"] / r["volume_1h"]) if r["volume_1h"] > 0 else 0.0
                            if (
                                r["buys_5m"] >= b
                                and r["txns_5m"] >= t
                                and r["volume_5m"] >= v
                                and r["price_accel"] >= a
                                and share >= s
                            ):
                                subset.append(r)
                        n = len(subset)
                        if n < args.min_samples:
                            continue
                        avg = sum(r["max_return_future"] for r in subset) / n
                        score = avg * math.log(n + 1)
                        candidate = {
                            "buys_5m": b,
                            "txns_5m": t,
                            "volume_5m": v,
                            "price_accel": a,
                            "vol5m_share": s,
                            "n": n,
                            "avg": avg,
                            "score": score,
                        }
                        if best is None or candidate["score"] > best["score"]:
                            best = candidate

    if not best:
        print("No viable threshold combo found with min samples.")
        return

    config = {
        "parameters": {
            "MIN_PRICE_CHANGE_5M": 0.0,
            "MIN_BUYS_5M": best["buys_5m"],
            "MIN_TXNS_5M": best["txns_5m"],
            "MIN_VOLUME_5M": float(best["volume_5m"]),
            "MIN_VOL5M_SHARE": float(best["vol5m_share"]),
            "MIN_VHI_SCORE": 55,
            "MAX_5M_PUMP": 25,
        },
        "metadata": {
            "source": "meme_threshold_builder",
            "n": best["n"],
            "avg_max_return_future": round(best["avg"], 4),
            "score": round(best["score"], 4),
            "price_accel_min": best["price_accel"],
        },
    }

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2)

    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
