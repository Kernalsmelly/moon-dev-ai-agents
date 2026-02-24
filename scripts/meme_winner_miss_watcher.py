#!/usr/bin/env python3
"""Periodically regenerate winner-miss report for the active run."""

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

    interval_min = float(env.get("MEME_WINNER_MISS_INTERVAL_MIN", "20") or 20)
    since_min = int(env.get("MEME_WINNER_MISS_SINCE_MIN", "240") or 240)
    run_id = str(env.get("MEME_WINNER_MISS_RUN_ID", "") or "").strip()
    auto_run_id = str(env.get("MEME_WINNER_MISS_AUTO_RUN_ID", "1") or "1").strip().lower() in ("1", "true", "yes")
    out_json = env.get("MEME_WINNER_MISS_OUT_JSON", "data/meme_winner_miss_report.json")
    out_md = env.get("MEME_WINNER_MISS_OUT_MD", "data/meme_winner_miss_report.md")

    while True:
        cmd = [
            PYTHON,
            "-u",
            str(BASE / "scripts" / "meme_winner_miss_report.py"),
            "--since-minutes",
            str(since_min),
            "--out-json",
            str(out_json),
            "--out-md",
            str(out_md),
        ]
        if run_id:
            cmd.extend(["--run-id", run_id])
        elif auto_run_id:
            cmd.append("--auto-run-id")
        try:
            subprocess.run(cmd, cwd=str(BASE), check=False)
        except Exception as e:
            print(f"meme_winner_miss_watcher error: {e}")
        time.sleep(max(300.0, interval_min * 60.0))


if __name__ == "__main__":
    raise SystemExit(main())

