#!/usr/bin/env python3
"""Periodic A/B zone report writer."""

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
    interval_s = int(env.get("MEME_AB_ZONE_REPORT_INTERVAL_S", "600") or 600)
    out = env.get("MEME_AB_ZONE_REPORT_OUT", "data/meme_reports/ab_zone_latest.md")
    out_json = env.get("MEME_AB_ZONE_REPORT_JSON_OUT", str(Path(out).with_suffix(".json")))
    decision_json = env.get("MEME_AB_ZONE_DECISION_OUT_JSON", "data/meme_reports/ab_zone_decision.json")
    decision_md = env.get("MEME_AB_ZONE_DECISION_OUT_MD", "data/meme_reports/ab_zone_decision.md")
    ready_json = env.get("MEME_AB_ZONE_READY_OUT_JSON", "data/meme_reports/ab_zone_ready.json")
    ready_md = env.get("MEME_AB_ZONE_READY_OUT_MD", "data/meme_reports/ab_zone_ready.md")

    report_cmd = [
        PYTHON,
        "-u",
        str(BASE / "scripts" / "meme_ab_zone_report.py"),
        "--out",
        str(out),
        "--json-out",
        str(out_json),
    ]
    decider_cmd = [
        PYTHON,
        "-u",
        str(BASE / "scripts" / "meme_ab_zone_decider.py"),
        "--summary",
        str(out_json),
        "--out-json",
        str(decision_json),
        "--out-md",
        str(decision_md),
    ]
    ready_cmd = [
        PYTHON,
        "-u",
        str(BASE / "scripts" / "meme_ab_zone_readiness.py"),
        "--summary",
        str(out_json),
        "--out-json",
        str(ready_json),
        "--out-md",
        str(ready_md),
    ]
    apply_cmd = [
        PYTHON,
        "-u",
        str(BASE / "scripts" / "meme_ab_zone_apply.py"),
    ]

    while True:
        try:
            subprocess.run(report_cmd, cwd=str(BASE), check=False)
            subprocess.run(decider_cmd, cwd=str(BASE), check=False)
            subprocess.run(ready_cmd, cwd=str(BASE), check=False)
            subprocess.run(apply_cmd, cwd=str(BASE), check=False)
        except Exception as e:
            print(f"ab_zone_watcher error: {e}")
        time.sleep(max(60, interval_s))


if __name__ == "__main__":
    raise SystemExit(main())
