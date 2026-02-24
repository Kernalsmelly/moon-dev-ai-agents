#!/usr/bin/env python3
"""Periodically rebuild winner profile from recent trades."""

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

    interval_min = float(env.get("MEME_WINNER_PROFILE_INTERVAL_MIN", "30") or 30)
    lookback_h = int(env.get("MEME_WINNER_PROFILE_LOOKBACK_HOURS", "72") or 72)
    db_path = env.get("MEME_WINNER_PROFILE_DB", "data/positions.db")
    out_path = env.get("MEME_WINNER_PROFILE_PATH", "data/meme_winner_profile.json")
    winner_pnl_pct = float(env.get("MEME_WINNER_PROFILE_WINNER_PNL_PCT", "12") or 12)
    loser_pnl_pct = float(env.get("MEME_WINNER_PROFILE_LOSER_PNL_PCT", "-6") or -6)
    min_group = int(env.get("MEME_WINNER_PROFILE_MIN_GROUP", "20") or 20)
    min_feature_samples = int(env.get("MEME_WINNER_PROFILE_MIN_FEATURE_SAMPLES", "10") or 10)

    while True:
        cmd = [
            PYTHON,
            "-u",
            str(BASE / "scripts" / "meme_winner_profile.py"),
            "--db",
            str(db_path),
            "--out",
            str(out_path),
            "--lookback-hours",
            str(lookback_h),
            "--winner-pnl-pct",
            str(winner_pnl_pct),
            "--loser-pnl-pct",
            str(loser_pnl_pct),
            "--min-group",
            str(min_group),
            "--min-feature-samples",
            str(min_feature_samples),
        ]
        try:
            subprocess.run(cmd, cwd=str(BASE), check=False)
        except Exception as e:
            print(f"winner_profile_watcher error: {e}")
        time.sleep(max(300.0, interval_min * 60.0))


if __name__ == "__main__":
    raise SystemExit(main())
