#!/usr/bin/env python3
"""Continuous live-sim runner for meme strategy.

Pipeline:
1. Record snapshots for a window
2. Run replay on snapshots
3. Rank variants and print top results

Usage:
  python scripts/meme_live_sim_runner.py --window 3600 --interval 10
"""
from __future__ import annotations

import argparse
import os
import subprocess
import time


def run_cmd(cmd: list[str]) -> int:
    try:
        return subprocess.call(cmd)
    except Exception:
        return 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--window", type=int, default=3600, help="Seconds to record per cycle")
    parser.add_argument("--interval", type=int, default=10, help="Snapshot interval")
    parser.add_argument("--out", default="data/meme_snapshots.jsonl", help="Snapshot output path")
    args = parser.parse_args()

    while True:
        # 1) Record snapshots
        run_cmd([
            "python",
            "scripts/meme_snapshot_recorder.py",
            "--interval",
            str(args.interval),
            "--duration",
            str(args.window),
            "--out",
            args.out,
        ])

        # 2) Replay + sweep
        run_cmd([
            "python",
            "scripts/meme_variant_sweep.py",
            "--input",
            args.out,
        ])

        # 3) Rank
        run_cmd([
            "python",
            "scripts/meme_variant_rank.py",
            "--dir",
            "data",
        ])

        # Sleep until next cycle
        time.sleep(5)


if __name__ == "__main__":
    main()
