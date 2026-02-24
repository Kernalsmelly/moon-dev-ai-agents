#!/usr/bin/env python3
"""Wait for a snapshot window, then run targeted small sweeps."""
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
    parser.add_argument("--input", required=True, help="Snapshot JSONL")
    parser.add_argument("--duration", type=int, required=True, help="Seconds to wait before running")
    parser.add_argument("--regime-out", default="data/meme_regime_tags_loose_new.json", help="Regime tags output")
    args = parser.parse_args()

    print(f"Waiting {args.duration}s for snapshot window to finish...")
    time.sleep(max(1, args.duration))

    base = os.path.splitext(os.path.basename(args.input))[0]
    features_out = f"data/{base}_launch_features.csv"
    analyzer_out = f"data/{base}_launch_analyzer.txt"

    print("Tagging regimes (loose)...")
    run_cmd([
        "python3",
        "scripts/meme_regime_tagger.py",
        "--input",
        args.input,
        "--out",
        args.regime_out,
        "--min-avg-5m",
        "1.0",
        "--min-avg-1h",
        "2.0",
        "--min-avg-txns",
        "25",
    ])

    print("Running confirm/cooldown sweep...")
    run_cmd([
        "python3",
        "scripts/meme_confirm_sweep.py",
        "--input",
        args.input,
        "--out",
        "data/meme_replay_trades.confirm_new.csv",
        "--regime-file",
        args.regime_out,
        "--hot-only",
    ])

    print("Running momentum sweep...")
    run_cmd([
        "python3",
        "scripts/meme_momentum_sweep.py",
        "--input",
        args.input,
        "--out",
        "data/meme_replay_trades.momentum_new.csv",
        "--regime-file",
        args.regime_out,
        "--hot-only",
    ])

    print("Building launch features...")
    run_cmd([
        "python3",
        "scripts/meme_launch_features.py",
        "--input",
        args.input,
        "--out",
        features_out,
        "--window-sec",
        "120",
        "--future-sec",
        "1800",
        "--min-window-snaps",
        "3",
    ])

    print("Analyzing launch thresholds...")
    run_cmd([
        "python3",
        "scripts/meme_launch_analyzer.py",
        "--input",
        features_out,
        "--min-samples",
        "8",
        "--out",
        analyzer_out,
    ])

    print("Building auto threshold config...")
    run_cmd([
        "python3",
        "scripts/meme_threshold_builder.py",
        "--input",
        features_out,
        "--out",
        "config/meme_early_edge_auto.json",
        "--min-samples",
        "8",
    ])


if __name__ == "__main__":
    main()
