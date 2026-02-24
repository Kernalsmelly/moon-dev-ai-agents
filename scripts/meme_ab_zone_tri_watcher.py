#!/usr/bin/env python3
"""Periodic reporter for tri-lane zone experiment."""

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
    interval_min = float(env.get("MEME_AB_TRI_REPORT_INTERVAL_MIN", "10") or 10)
    out_md = env.get("MEME_AB_TRI_REPORT_OUT_MD", "data/meme_reports/ab_zone_tri_latest.md")
    out_json = env.get("MEME_AB_TRI_REPORT_OUT_JSON", "data/meme_reports/ab_zone_tri_latest.json")
    while True:
        cmd = [
            PYTHON,
            "-u",
            str(BASE / "scripts" / "meme_ab_zone_tri_report.py"),
            "--out-md",
            str(out_md),
            "--out-json",
            str(out_json),
        ]
        try:
            subprocess.run(cmd, cwd=str(BASE), check=False)
        except Exception as e:
            print(f"tri_watcher error: {e}")
        time.sleep(max(60.0, interval_min * 60.0))


if __name__ == "__main__":
    raise SystemExit(main())

