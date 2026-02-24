#!/usr/bin/env python3
"""Lightweight status aggregator for swarm jobs.

Usage:
  python scripts/meme_swarm_status.py --out data/meme_swarm_status.json --watch
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime


WATCH_FILES = [
    "logs/meme_snapshot_6h_run2.log",
    "logs/meme_auto_6h_run2.log",
    "logs/meme_sweep_v2_expanded_3h.log",
    "logs/meme_sweep_v2_hot_3h.log",
    "logs/meme_sweep_ranker.log",
    "logs/meme_sweep_ranker_hot.log",
    "logs/meme_paper_run.log",
]

DATA_FILES = [
    "data/meme_snapshots_6h_run2.jsonl",
    "data/meme_variant_rank_v2_expanded_3h.csv",
    "data/meme_variant_rank_v2_hot_3h.csv",
    "data/meme_best_oos.json",
]


def file_info(path: str) -> dict:
    if not os.path.exists(path):
        return {"path": path, "exists": False}
    st = os.stat(path)
    return {
        "path": path,
        "exists": True,
        "size": st.st_size,
        "mtime": st.st_mtime,
    }


def snapshot_status(out_path: str):
    status = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "logs": [file_info(p) for p in WATCH_FILES],
        "data": [file_info(p) for p in DATA_FILES],
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(status, fh, indent=2)
    print(f"Wrote status to {out_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="data/meme_swarm_status.json", help="Output JSON")
    parser.add_argument("--watch", action="store_true", help="Loop forever")
    parser.add_argument("--interval", type=int, default=300, help="Seconds between updates")
    args = parser.parse_args()

    if args.watch:
        while True:
            snapshot_status(args.out)
            time.sleep(max(30, args.interval))
    else:
        snapshot_status(args.out)


if __name__ == "__main__":
    main()
