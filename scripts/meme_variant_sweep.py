#!/usr/bin/env python3
"""Run a grid sweep of meme replay variants.

Usage:
  python scripts/meme_variant_sweep.py --input data/meme_snapshots.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Snapshot JSONL")
    parser.add_argument("--out", default="data/meme_replay_trades.csv", help="Baseline output CSV")
    args = parser.parse_args()

    grids = []
    # Sweep ranges (edit as needed)
    min_scores = [45, 50, 55, 60]
    max_pumps = [20, 30]
    min_1h = [0, 5, 10]
    liq_accel = [0, 5, 10]
    vol_accel = [0, 10, 20]
    decay_per_hour = [0.0, 2.0, 4.0]
    max_boost_age = [0, 600, 1800]  # 0=disabled, 10m, 30m
    max_top10 = [0.50, 0.65, 0.75]

    for ms in min_scores:
        for mp in max_pumps:
            for m1h in min_1h:
                for la in liq_accel:
                    for va in vol_accel:
                        for dc in decay_per_hour:
                            for mba in max_boost_age:
                                for mt in max_top10:
                                    name = f"s{ms}_p{mp}_h{m1h}_la{la}_va{va}_d{dc}_mba{mba}_t10{int(mt*100)}"
                                    grids.append({
                                        "name": name,
                                        "MIN_VHI_SCORE": ms,
                                        "MAX_5M_PUMP": mp,
                                        "MIN_PRICE_CHANGE_1H": m1h,
                                        "MIN_LIQ_ACCEL_PCT": la,
                                        "MIN_VOL_ACCEL_PCT": va,
                                        "SCORE_DECAY_PER_HOUR": dc,
                                        "MAX_BOOST_AGE_SECONDS": mba,
                                        "MAX_TOP10_HOLDER_PCT": mt,
                                        "USE_TOP10_CHECK": True,
                                    })

    variants_json = json.dumps(grids)
    cmd = [
        "python3",
        "scripts/meme_replay.py",
        "--input",
        args.input,
        "--out",
        args.out,
        "--variants",
        variants_json,
    ]

    print("Running sweep...")
    subprocess.run(cmd, check=False)
    print("Sweep complete. Review outputs in data/")


if __name__ == "__main__":
    main()
