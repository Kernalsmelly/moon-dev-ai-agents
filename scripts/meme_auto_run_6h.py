#!/usr/bin/env python3
"""Wait for a snapshot capture window to finish, then run sweep + walk-forward.

Usage:
  python scripts/meme_auto_run_6h.py --input data/meme_snapshots_6h.jsonl --duration 21600
"""
from __future__ import annotations

import argparse
import subprocess
import time


def run_cmd(cmd: list[str]) -> int:
    try:
        return subprocess.call(cmd)
    except Exception:
        return 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Snapshot JSONL")
    parser.add_argument("--duration", type=int, required=True, help="Seconds to wait before running")
    parser.add_argument("--variants-file", default="data/meme_variants_v2.json", help="Variants JSON file")
    args = parser.parse_args()

    print(f"Waiting {args.duration}s for snapshot window to finish...")
    time.sleep(max(1, args.duration))

    print("Running v2 sweep...")
    run_cmd([
        "python3",
        "scripts/meme_variant_sweep_v2.py",
        "--input",
        args.input,
        "--out",
        "data/meme_replay_trades.v2_6h.csv",
    ])

    print("Running walk-forward...")
    run_cmd([
        "python3",
        "scripts/meme_walkforward.py",
        "--input",
        args.input,
        "--variants-file",
        args.variants_file,
        "--split",
        "0.7",
        "--min-trades",
        "30",
        "--out-dir",
        "data/walkforward_6h",
    ])


if __name__ == "__main__":
    main()
