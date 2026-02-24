#!/usr/bin/env python3
"""Sweep exit parameters (TP tiers and trailing distances) for meme replay.

Usage:
  python scripts/meme_exit_sweep.py --input data/meme_snapshots.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from itertools import product


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Snapshot JSONL")
    parser.add_argument("--out", default="data/meme_replay_trades.exit.csv", help="Baseline output CSV")
    parser.add_argument("--config-file", default="", help="Optional base config JSON to apply")
    args = parser.parse_args()

    # Exit tier variants: (tp0, tp1, tp2, tp3, tp4) gains, and sell fractions
    tp_gain_sets = [
        [0.30, 0.55, 0.90, 1.40, 2.20],
        [0.35, 0.60, 1.00, 1.50, 2.50],
        [0.25, 0.45, 0.80, 1.20, 2.00],
    ]
    tp_sell_sets = [
        [0.25, 0.25, 0.20, 0.10, 0.10],
        [0.30, 0.25, 0.20, 0.10, 0.05],
    ]
    trail_sets = [
        (-0.08, -0.12, -0.18),
        (-0.07, -0.10, -0.16),
    ]

    grids = []
    for gains, sells, trails in product(tp_gain_sets, tp_sell_sets, trail_sets):
        name = f"tp{int(gains[0]*100)}_{int(gains[1]*100)}_{int(gains[2]*100)}_{int(gains[3]*100)}_{int(gains[4]*100)}"
        name += f"_sf{int(sells[0]*100)}{int(sells[1]*100)}{int(sells[2]*100)}{int(sells[3]*100)}{int(sells[4]*100)}"
        name += f"_tr{int(trails[0]*100)}{int(trails[1]*100)}{int(trails[2]*100)}"

        grids.append({
            "name": name,
            "TP_TIERS": list(zip(gains, sells)),
            "TRAILING_DISTANCE_TIGHT": trails[0],
            "TRAILING_DISTANCE_MODERATE": trails[1],
            "TRAILING_DISTANCE_WIDE": trails[2],
        })

    variants_path = "data/meme_exit_variants.json"
    with open(variants_path, "w", encoding="utf-8") as fh:
        json.dump(grids, fh)

    cmd = [
        "python3",
        "scripts/meme_replay.py",
        "--input",
        args.input,
        "--out",
        args.out,
        "--variants-file",
        variants_path,
    ]
    if args.config_file:
        cmd.extend(["--config-file", args.config_file])

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ)
    env["PYTHONPATH"] = project_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

    print(f"Running exit sweep with {len(grids)} variants...")
    subprocess.run(cmd, check=False, cwd=project_root, env=env)
    print("Exit sweep complete. Review outputs in data/")


if __name__ == "__main__":
    main()
