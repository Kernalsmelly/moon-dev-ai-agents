#!/usr/bin/env python3
"""Run signal + trade reports in a loop and append stats."""
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
    parser.add_argument("--interval", type=int, default=600)
    args = parser.parse_args()

    while True:
        run(["python3", "scripts/meme_signal_monitor.py", "--window-hours", "1"])
        run(["python3", "scripts/meme_trade_report.py", "--hours", "6"])
        run(["python3", "scripts/meme_signal_outcome_report.py", "--since-hours", "24", "--min-trades", "2"])
        time.sleep(max(30, args.interval))


if __name__ == "__main__":
    main()
