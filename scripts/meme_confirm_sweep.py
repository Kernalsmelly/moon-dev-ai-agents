#!/usr/bin/env python3
"""Targeted sweep for confirmation and cooldown filters.

Usage:
  python scripts/meme_confirm_sweep.py --input data/meme_snapshots.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Snapshot JSONL")
    parser.add_argument("--out", default="data/meme_replay_trades.confirm.csv", help="Baseline output CSV")
    parser.add_argument("--regime-file", default="", help="Optional regime tags JSON")
    parser.add_argument("--hot-only", action="store_true", help="Only trade during hot regimes")
    parser.add_argument("--config-file", default="", help="Optional base config JSON")
    args = parser.parse_args()

    confirm_n = [0, 2, 3]
    entry_cooldown = [0, 120, 300]
    max_5m_pump = [20, 30]
    min_txns_5m = [0, 15]

    grids = []
    for cn in confirm_n:
        for cd in entry_cooldown:
            for mp in max_5m_pump:
                for tx5 in min_txns_5m:
                    name = f"cn{cn}_cd{cd}_p{mp}_tx5{tx5}"
                    grids.append({
                        "name": name,
                        "CONFIRM_N": cn,
                        "ENTRY_COOLDOWN_SECONDS": cd,
                        "MAX_5M_PUMP": mp,
                        "MIN_TXNS_5M": tx5,
                    })

    variants_path = "data/meme_variants_confirm.json"
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

    print(f"Running confirm/cooldown sweep with {len(grids)} variants...")
    subprocess.run(cmd, check=False, cwd=project_root, env=env)
    print("Confirm sweep complete.")


if __name__ == "__main__":
    main()
