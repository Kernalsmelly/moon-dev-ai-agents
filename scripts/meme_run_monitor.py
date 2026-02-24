#!/usr/bin/env python3
"""Monitor snapshot file growth and alert on stalls.

Usage:
  python scripts/meme_run_monitor.py --file data/meme_snapshots_6h_run2.jsonl --interval 300 --stale-minutes 20
"""
from __future__ import annotations

import argparse
import os
import time
from datetime import datetime


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True, help="Snapshot JSONL file to monitor")
    parser.add_argument("--interval", type=int, default=300, help="Seconds between checks")
    parser.add_argument("--stale-minutes", type=int, default=20, help="Warn if no growth for N minutes")
    args = parser.parse_args()

    last_size = None
    last_change_ts = time.time()

    while True:
        now = time.time()
        if os.path.exists(args.file):
            size = os.path.getsize(args.file)
            if last_size is None:
                last_size = size
                last_change_ts = now
                print(f"[{datetime.now()}] size={size} bytes (init)")
            else:
                if size != last_size:
                    delta = size - last_size
                    last_size = size
                    last_change_ts = now
                    print(f"[{datetime.now()}] size={size} bytes (+{delta})")
                else:
                    minutes_stale = (now - last_change_ts) / 60.0
                    if minutes_stale >= args.stale_minutes:
                        print(f"[{datetime.now()}] WARNING: no growth for {minutes_stale:.1f} minutes")
        else:
            print(f"[{datetime.now()}] waiting for file: {args.file}")

        time.sleep(max(5, args.interval))


if __name__ == "__main__":
    main()
