#!/usr/bin/env python3
"""Watch for a new OOS config and promote it to the active config.

Usage:
  python scripts/meme_config_switcher.py --watch config/meme_best_oos.json --active config/meme_active.json
"""
from __future__ import annotations

import argparse
import hashlib
import os
import time
from datetime import datetime


def file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(8192)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", required=True, help="OOS config file to watch")
    parser.add_argument("--active", required=True, help="Active config file to overwrite")
    parser.add_argument("--poll", type=int, default=60, help="Seconds between checks")
    args = parser.parse_args()

    last_hash = None
    while True:
        if os.path.exists(args.watch):
            try:
                h = file_hash(args.watch)
                if h != last_hash:
                    with open(args.watch, "rb") as src:
                        data = src.read()
                    with open(args.active, "wb") as dst:
                        dst.write(data)
                    last_hash = h
                    print(f"[{datetime.now()}] Updated active config: {args.active}")
            except Exception:
                pass
        time.sleep(max(10, args.poll))


if __name__ == "__main__":
    main()
