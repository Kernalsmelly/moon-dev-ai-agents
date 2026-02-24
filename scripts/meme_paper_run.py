#!/usr/bin/env python3
"""Run the meme bot in paper mode for a fixed duration.

Usage:
  python scripts/meme_paper_run.py --duration 7200 --config-file config/meme_active.json
"""
from __future__ import annotations

import argparse
import os
import subprocess
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=3600, help="Seconds to run")
    parser.add_argument("--config-file", default="", help="Optional MEME_CONFIG_FILE path")
    args = parser.parse_args()

    env = os.environ.copy()
    env["MEME_PAPER_MODE"] = "true"
    env["MEME_LIVE_ENABLED"] = "false"
    if args.config_file:
        env["MEME_CONFIG_FILE"] = args.config_file

    print(f"Starting meme bot in PAPER mode for {args.duration}s...")
    proc = subprocess.Popen(["python3", "src/meme_bot.py"], env=env)
    start = time.time()
    try:
        while True:
            if proc.poll() is not None:
                print("Meme bot exited early.")
                return
            if time.time() - start >= args.duration:
                print("Duration reached; stopping meme bot.")
                break
            time.sleep(5)
    finally:
        try:
            proc.terminate()
            time.sleep(5)
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass


if __name__ == "__main__":
    main()
