#!/usr/bin/env python3
"""Targeted sweep for liquidity/volume spike filters.

Usage:
  python scripts/meme_spike_sweep.py --input data/meme_snapshots.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Snapshot JSONL")
    parser.add_argument("--out", default="data/meme_replay_trades.spike.csv", help="Baseline output CSV")
    parser.add_argument("--regime-file", default="", help="Optional regime tags JSON")
    parser.add_argument("--hot-only", action="store_true", help="Only trade during hot regimes")
    parser.add_argument("--config-file", default="", help="Optional base config JSON")
    args = parser.parse_args()

    liq_spikes = [0, 5000, 10000]
    vol_spikes = [0, 1000, 3000]
    grids = []

    for ls in liq_spikes:
        for vs in vol_spikes:
            name = f"spike_l{int(ls)}_v{int(vs)}"
            grids.append({
                "name": name,
                "SPIKE_FILTER_ENABLED": True,
                "MIN_LIQ_SPIKE_USD": ls,
                "MIN_VOL_SPIKE_5M": vs,
            })

    variants_path = "data/meme_variants_spike.json"
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

    print(f"Running spike sweep with {len(grids)} variants...")
    subprocess.run(cmd, check=False, cwd=project_root, env=env)
    print("Spike sweep complete.")


if __name__ == "__main__":
    main()
