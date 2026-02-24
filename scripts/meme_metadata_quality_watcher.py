#!/usr/bin/env python3
"""Periodically regenerate metadata quality reports."""

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

    interval_min = float(env.get("MEME_METADATA_QUALITY_INTERVAL_MIN", "30") or 30)
    since_hours = int(env.get("MEME_METADATA_QUALITY_SINCE_HOURS", "72") or 72)
    db = env.get("MEME_METADATA_QUALITY_DB", "data/positions.db")
    out_json = env.get("MEME_METADATA_QUALITY_OUT_JSON", "data/meme_metadata_quality_report.json")
    out_md = env.get("MEME_METADATA_QUALITY_OUT_MD", "data/meme_metadata_quality_report.md")

    while True:
        cmd = [
            PYTHON,
            "-u",
            str(BASE / "scripts" / "meme_metadata_quality_report.py"),
            "--db",
            str(db),
            "--since-hours",
            str(since_hours),
            "--out-json",
            str(out_json),
            "--out-md",
            str(out_md),
        ]
        try:
            subprocess.run(cmd, cwd=str(BASE), check=False)
        except Exception as e:
            print(f"metadata_quality_watcher error: {e}")
        time.sleep(max(300.0, interval_min * 60.0))


if __name__ == "__main__":
    raise SystemExit(main())

