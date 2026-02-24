#!/usr/bin/env python3
"""Watch a sweep log for completion, then rank variants and emit a report.

Usage:
  python scripts/meme_sweep_ranker.py --log logs/meme_sweep_v2_expanded_3h.log
"""
from __future__ import annotations

import argparse
import csv
import os
import subprocess
import time


def run_cmd(cmd: list[str]) -> int:
    try:
        return subprocess.call(cmd)
    except Exception:
        return 1


def read_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except Exception:
        return ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True, help="Sweep log file to watch")
    parser.add_argument("--dir", default="data", help="Directory with replay CSVs")
    parser.add_argument("--min-trades", type=int, default=20, help="Min trades for ranking")
    parser.add_argument("--rank-out", default="data/meme_variant_rank_v2_expanded_3h.csv", help="Ranking output CSV")
    parser.add_argument("--report-out", default="data/meme_daily_report_top_expanded.txt", help="Daily report path")
    parser.add_argument("--poll", type=int, default=60, help="Seconds between log checks")
    args = parser.parse_args()

    # Wait for sweep completion
    while True:
        if os.path.exists(args.log):
            content = read_file(args.log)
            if "Sweep complete" in content or "Sweep complete." in content:
                break
        time.sleep(max(10, args.poll))

    # Rank variants
    run_cmd([
        "python3",
        "scripts/meme_variant_rank.py",
        "--dir",
        args.dir,
        "--min-trades",
        str(args.min_trades),
        "--out",
        args.rank_out,
    ])

    # Find top variant
    top_file = None
    try:
        with open(args.rank_out, "r", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                top_file = row.get("file")
                break
    except Exception:
        top_file = None

    if not top_file:
        return

    # Generate daily report on top file
    run_cmd([
        "python3",
        "scripts/meme_daily_report.py",
        "--input",
        os.path.join(args.dir, top_file),
        "--out",
        args.report_out,
    ])


if __name__ == "__main__":
    main()
