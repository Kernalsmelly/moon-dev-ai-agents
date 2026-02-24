#!/usr/bin/env python3
"""Sweep fail-fast parameters for meme replay.

Usage:
  python scripts/meme_failfast_sweep.py --input data/meme_snapshots.jsonl --config-file config/meme_active.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
from itertools import product


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Snapshot JSONL")
    parser.add_argument("--out", default="data/meme_replay_trades.failfast.csv", help="Output CSV")
    parser.add_argument("--config-file", default="", help="Optional base config JSON to apply")
    args = parser.parse_args()

    windows = [90, 120, 180]
    min_gain = [0.3, 0.5, 1.0]
    sell_frac = [0.25, 0.5, 0.75]

    grids = []
    for w, g, s in product(windows, min_gain, sell_frac):
        name = f"ffw{w}_g{g}_s{int(s*100)}"
        grids.append({
            "name": name,
            "FAIL_FAST_WINDOW_SECONDS": w,
            "FAIL_FAST_MIN_GAIN_PCT": g,
            "FAIL_FAST_SELL_FRACTION": s,
        })

    variants_path = "data/meme_failfast_variants.json"
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

    print(f"Running fail-fast sweep with {len(grids)} variants...")
    subprocess.run(cmd, check=False)


if __name__ == "__main__":
    main()
