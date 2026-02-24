#!/usr/bin/env python3
"""Periodically run scout lane decision report."""

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

    interval_min = float(env.get("MEME_SCOUT_DECIDER_INTERVAL_MIN", "30") or 30)
    since_hours = float(env.get("MEME_SCOUT_DECIDER_SINCE_HOURS", "24") or 24)
    run_id = str(env.get("MEME_SCOUT_DECIDER_RUN_ID", "") or "").strip()
    auto_run_id = str(env.get("MEME_SCOUT_DECIDER_AUTO_RUN_ID", "1") or "1").strip().lower() in ("1", "true", "yes")
    min_scout = int(env.get("MEME_SCOUT_DECIDER_MIN_SCOUT_TRADES", "12") or 12)
    min_strict = int(env.get("MEME_SCOUT_DECIDER_MIN_STRICT_TRADES", "12") or 12)
    pnl_tol = float(env.get("MEME_SCOUT_DECIDER_PNL_TOL_USD", "0.08") or 0.08)
    win_tol = float(env.get("MEME_SCOUT_DECIDER_WIN_TOL", "0.08") or 0.08)

    out_json = env.get("MEME_SCOUT_DECIDER_OUT_JSON", "data/meme_scout_decider.json")
    out_md = env.get("MEME_SCOUT_DECIDER_OUT_MD", "data/meme_scout_decider.md")

    while True:
        cmd = [
            PYTHON,
            "-u",
            str(BASE / "scripts" / "meme_scout_decider.py"),
            "--since-hours",
            str(since_hours),
            "--min-scout-trades",
            str(min_scout),
            "--min-strict-trades",
            str(min_strict),
            "--pnl-tol-usd",
            str(pnl_tol),
            "--win-tol",
            str(win_tol),
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
            print(f"meme_scout_decider_watcher error: {e}")
        time.sleep(max(300.0, interval_min * 60.0))


if __name__ == "__main__":
    raise SystemExit(main())
