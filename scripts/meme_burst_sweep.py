#!/usr/bin/env python3
"""Targeted sweep for 5m burst activity filters.

Usage:
  python scripts/meme_burst_sweep.py --input data/meme_snapshots.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Snapshot JSONL")
    parser.add_argument("--out", default="data/meme_replay_trades.burst.csv", help="Baseline output CSV")
    parser.add_argument("--regime-file", default="", help="Optional regime tags JSON")
    parser.add_argument("--hot-only", action="store_true", help="Only trade during hot regimes")
    parser.add_argument("--config-file", default="", help="Optional base config JSON")
    args = parser.parse_args()

    min_buys_5m = [0, 5, 10]
    min_txns_5m = [0, 10]
    min_vol5m_share = [0.0, 0.15, 0.25]

    grids = []
    for b5 in min_buys_5m:
        for tx5 in min_txns_5m:
            for vshare in min_vol5m_share:
                name = f"b5{b5}_tx5{tx5}_vs{int(vshare*100)}"
                grids.append({
                    "name": name,
                    "MIN_BUYS_5M": b5,
                    "MIN_TXNS_5M": tx5,
                    "MIN_VOL5M_SHARE": vshare,
                })

    variants_path = "data/meme_variants_burst.json"
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

    print(f"Running burst sweep with {len(grids)} variants...")
    subprocess.run(cmd, check=False, cwd=project_root, env=env)
    print("Burst sweep complete.")


if __name__ == "__main__":
    main()
