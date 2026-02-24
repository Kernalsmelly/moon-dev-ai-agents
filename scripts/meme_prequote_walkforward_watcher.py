#!/usr/bin/env python3
"""Periodically refresh prequote walk-forward tuning report."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from dotenv import load_dotenv

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
PYTHON = "/opt/homebrew/bin/python3"


def main() -> int:
    load_dotenv(dotenv_path=str(BASE / ".env"), override=True)
    env = os.environ.copy()

    interval_min = float(env.get("MEME_PREQUOTE_WF_INTERVAL_MIN", "30") or 30)
    lookback = int(env.get("MEME_PREQUOTE_WF_LOOKBACK", "12000") or 12000)
    horizon = int(env.get("MEME_PREQUOTE_WF_HORIZON_S", "300") or 300)
    min_train = int(env.get("MEME_PREQUOTE_WF_MIN_TRAIN", "120") or 120)
    min_val = int(env.get("MEME_PREQUOTE_WF_MIN_VAL", "60") or 60)
    rt_cost = float(env.get("MEME_PREQUOTE_WF_ROUNDTRIP_COST_PCT", "0.03") or 0.03)
    out_json = env.get("MEME_PREQUOTE_WF_OUT_JSON", "data/meme_prequote_walkforward.json")
    out_md = env.get("MEME_PREQUOTE_WF_OUT_MD", "data/meme_prequote_walkforward.md")

    while True:
        cmd = [
            PYTHON,
            "-u",
            str(BASE / "scripts" / "meme_prequote_walkforward.py"),
            "--file",
            "data/signal_outcomes.jsonl",
            "--lookback",
            str(lookback),
            "--horizon",
            str(horizon),
            "--min-train",
            str(min_train),
            "--min-val",
            str(min_val),
            "--roundtrip-cost-pct",
            str(rt_cost),
            "--out-json",
            str(out_json),
            "--out-md",
            str(out_md),
        ]
        try:
            subprocess.run(cmd, cwd=str(BASE), check=False)
        except Exception as e:
            print(f"meme_prequote_walkforward_watcher error: {e}")
        time.sleep(max(300.0, interval_min * 60.0))


if __name__ == "__main__":
    raise SystemExit(main())

