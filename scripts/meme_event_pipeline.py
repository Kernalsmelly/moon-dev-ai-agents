#!/usr/bin/env python3
"""Continuously process WS events into launch mints."""
from __future__ import annotations

import argparse
import subprocess
import time


def run(cmd: list[str]) -> int:
    try:
        return subprocess.call(cmd)
    except Exception:
        return 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=15, help="Seconds between processing loops")
    args = parser.parse_args()

    while True:
        run([
            "python3",
            "scripts/meme_launch_detector.py",
            "--input",
            "data/helius_events.jsonl",
            "--out",
            "data/meme_launch_candidates.jsonl",
        ])
        run([
            "python3",
            "scripts/meme_launch_filter.py",
            "--input",
            "data/meme_launch_candidates.jsonl",
            "--out",
            "data/meme_launch_mints.jsonl",
        ])
        time.sleep(max(5, args.interval))


if __name__ == "__main__":
    main()
