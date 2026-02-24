#!/usr/bin/env python3
"""Walk-forward evaluation for meme replay strategies.

Splits snapshot data into train/test windows, runs replay on both,
ranks variants separately, and produces a combined OOS summary.

Usage:
  python scripts/meme_walkforward.py --input data/meme_snapshots.jsonl --variants-file data/meme_variants_v2.json
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
from pathlib import Path


def load_snapshots(path: str) -> list[dict]:
    rows = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    rows.sort(key=lambda r: float(r.get("ts", 0)))
    return rows


def write_jsonl(path: str, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def run_cmd(cmd: list[str]) -> int:
    try:
        return subprocess.call(cmd)
    except Exception:
        return 1


def load_rank(path: str) -> dict[str, dict]:
    rows = {}
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rows[row["file"]] = row
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Snapshot JSONL")
    parser.add_argument("--variants-file", required=True, help="Variants JSON file")
    parser.add_argument("--split", type=float, default=0.7, help="Train split ratio (0-1)")
    parser.add_argument("--min-trades", type=int, default=20, help="Minimum trades for ranking")
    parser.add_argument("--out-dir", default="data/walkforward", help="Output directory")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    train_dir = out_dir / "train"
    test_dir = out_dir / "test"
    out_dir.mkdir(parents=True, exist_ok=True)
    train_dir.mkdir(parents=True, exist_ok=True)
    test_dir.mkdir(parents=True, exist_ok=True)

    rows = load_snapshots(args.input)
    if not rows:
        print("No snapshots loaded.")
        return

    split_idx = max(1, min(len(rows) - 1, int(len(rows) * args.split)))
    train_rows = rows[:split_idx]
    test_rows = rows[split_idx:]

    train_path = str(train_dir / "snapshots_train.jsonl")
    test_path = str(test_dir / "snapshots_test.jsonl")
    write_jsonl(train_path, train_rows)
    write_jsonl(test_path, test_rows)

    # Run replay for train/test
    train_out = str(train_dir / "meme_replay_trades.csv")
    test_out = str(test_dir / "meme_replay_trades.csv")

    print("Running replay on train...")
    run_cmd([
        "python3",
        "scripts/meme_replay.py",
        "--input",
        train_path,
        "--out",
        train_out,
        "--variants-file",
        args.variants_file,
    ])

    print("Running replay on test...")
    run_cmd([
        "python3",
        "scripts/meme_replay.py",
        "--input",
        test_path,
        "--out",
        test_out,
        "--variants-file",
        args.variants_file,
    ])

    # Rank train/test
    train_rank = str(train_dir / "rank.csv")
    test_rank = str(test_dir / "rank.csv")
    run_cmd([
        "python3",
        "scripts/meme_variant_rank.py",
        "--dir",
        str(train_dir),
        "--min-trades",
        str(args.min_trades),
        "--out",
        train_rank,
    ])
    run_cmd([
        "python3",
        "scripts/meme_variant_rank.py",
        "--dir",
        str(test_dir),
        "--min-trades",
        str(args.min_trades),
        "--out",
        test_rank,
    ])

    # Combine rank results for OOS summary
    train_rows_map = load_rank(train_rank)
    test_rows_map = load_rank(test_rank)

    summary_path = out_dir / "oos_summary.csv"
    with open(summary_path, "w", encoding="utf-8") as fh:
        fh.write("file,train_expectancy,train_net_pnl,train_trades,test_expectancy,test_net_pnl,test_trades\n")
        for fn, t in train_rows_map.items():
            if fn not in test_rows_map:
                continue
            te = t.get("expectancy", "")
            tn = t.get("net_pnl", "")
            tt = t.get("trades", "")
            o = test_rows_map[fn]
            oe = o.get("expectancy", "")
            on = o.get("net_pnl", "")
            ot = o.get("trades", "")
            fh.write(f"{fn},{te},{tn},{tt},{oe},{on},{ot}\n")

    print(f"OOS summary saved: {summary_path}")


if __name__ == "__main__":
    main()
