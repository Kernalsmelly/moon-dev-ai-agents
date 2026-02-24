#!/usr/bin/env python3
"""Targeted sweep for short-term momentum filters.

Usage:
  python scripts/meme_momentum_sweep.py --input data/meme_snapshots.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Snapshot JSONL")
    parser.add_argument("--out", default="data/meme_replay_trades.momentum.csv", help="Baseline output CSV")
    parser.add_argument("--regime-file", default="", help="Optional regime tags JSON")
    parser.add_argument("--hot-only", action="store_true", help="Only trade during hot regimes")
    parser.add_argument("--config-file", default="", help="Optional base config JSON")
    args = parser.parse_args()

    min_5m = [0.0, 2.0, 5.0]
    min_bs_5m = [1.1, 1.3, 1.5]
    min_txns_5m = [10, 20]
    confirm_n = [2, 3]
    entry_cooldown = [120, 300]
    min_1h = [0.0, 5.0]

    grids = []
    for m5 in min_5m:
        for bs5 in min_bs_5m:
            for tx5 in min_txns_5m:
                for cn in confirm_n:
                    for cd in entry_cooldown:
                        for m1h in min_1h:
                            name = f"m5{int(m5)}_bs{int(bs5*10)}_tx{tx5}_cn{cn}_cd{cd}_m1h{int(m1h)}"
                            grids.append({
                                "name": name,
                                "MIN_PRICE_CHANGE_5M": m5,
                                "MIN_BUY_SELL_RATIO_5M": bs5,
                                "MIN_TXNS_5M": tx5,
                                "CONFIRM_N": cn,
                                "ENTRY_COOLDOWN_SECONDS": cd,
                                "MIN_PRICE_CHANGE_1H": m1h,
                            })

    variants_path = "data/meme_variants_momentum.json"
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
    if args.regime_file:
        cmd.extend(["--regime-file", args.regime_file])
    if args.hot_only:
        cmd.append("--hot-only")

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ)
    env["PYTHONPATH"] = project_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

    print(f"Running momentum sweep with {len(grids)} variants...")
    subprocess.run(cmd, check=False, cwd=project_root, env=env)
    print("Momentum sweep complete.")


if __name__ == "__main__":
    main()
