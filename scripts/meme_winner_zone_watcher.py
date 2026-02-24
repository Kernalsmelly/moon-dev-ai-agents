#!/usr/bin/env python3
"""Periodically rebuild winner-zone allowlist from signal outcomes."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from dotenv import load_dotenv

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
PYTHON = "/opt/homebrew/bin/python3"


def _zone_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        zones = obj.get("zones") if isinstance(obj, dict) else None
        if not isinstance(zones, list):
            return 0
        return len(zones)
    except Exception:
        return 0


def _write_alert(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def main() -> int:
    load_dotenv(dotenv_path=str(BASE / ".env"), override=True)
    env = os.environ.copy()

    interval_min = float(env.get("MEME_WINNER_ZONE_INTERVAL_MIN", "30") or 30)
    outcomes_file = env.get("MEME_WINNER_ZONE_SOURCE_FILE", "data/signal_outcomes.jsonl")
    out_path = env.get("MEME_WINNER_ZONE_PATH", "data/meme_winner_zones.json")
    out_md = env.get("MEME_WINNER_ZONE_OUT_MD", "data/meme_winner_zones.md")
    horizon = int(env.get("MEME_WINNER_ZONE_HORIZON_S", "120") or 120)
    lookback_h = float(env.get("MEME_WINNER_ZONE_LOOKBACK_HOURS", "72") or 72)
    roundtrip = float(env.get("MEME_WINNER_ZONE_ROUNDTRIP_COST_PCT", "0.03") or 0.03)
    min_samples = int(env.get("MEME_WINNER_ZONE_MIN_SAMPLES", "20") or 20)
    min_wr = float(env.get("MEME_WINNER_ZONE_MIN_WIN_RATE", "0.50") or 0.50)
    min_mean = float(env.get("MEME_WINNER_ZONE_MIN_MEAN_ADJ", "0.00") or 0.00)
    max_zones = int(env.get("MEME_WINNER_ZONE_MAX_ZONES", "16") or 16)
    coarse_fallback = int(env.get("MEME_WINNER_ZONE_COARSE_FALLBACK", "1") or 1)
    coarse_min_samples = int(env.get("MEME_WINNER_ZONE_COARSE_MIN_SAMPLES", "8") or 8)
    coarse_min_wr = float(env.get("MEME_WINNER_ZONE_COARSE_MIN_WIN_RATE", "0.48") or 0.48)
    coarse_min_mean = float(env.get("MEME_WINNER_ZONE_COARSE_MIN_MEAN_ADJ", "-0.002") or -0.002)
    sanity_min_zones = int(env.get("MEME_WINNER_ZONE_SANITY_MIN_ZONES", "1") or 1)
    sanity_alert_path = BASE / env.get(
        "MEME_WINNER_ZONE_SANITY_ALERT_PATH", "data/meme_reports/winner_zone_sanity_alert.json"
    )
    sanity_coarse_min_samples = int(env.get("MEME_WINNER_ZONE_SANITY_COARSE_MIN_SAMPLES", "3") or 3)
    sanity_coarse_min_wr = float(env.get("MEME_WINNER_ZONE_SANITY_COARSE_MIN_WIN_RATE", "0.35") or 0.35)
    sanity_coarse_min_mean = float(env.get("MEME_WINNER_ZONE_SANITY_COARSE_MIN_MEAN_ADJ", "-0.02") or -0.02)

    while True:
        cmd_base = [
            PYTHON,
            "-u",
            str(BASE / "scripts" / "meme_winner_zone_builder.py"),
            "--file",
            str(outcomes_file),
            "--out",
            str(out_path),
            "--out-md",
            str(out_md),
            "--horizon",
            str(horizon),
            "--lookback-hours",
            str(lookback_h),
            "--roundtrip-cost-pct",
            str(roundtrip),
            "--min-samples",
            str(min_samples),
            "--min-win-rate",
            str(min_wr),
            "--min-mean-adj",
            str(min_mean),
            "--max-zones",
            str(max_zones),
            "--coarse-fallback",
            str(coarse_fallback),
            "--coarse-min-samples",
            str(coarse_min_samples),
            "--coarse-min-win-rate",
            str(coarse_min_wr),
            "--coarse-min-mean-adj",
            str(coarse_min_mean),
        ]
        try:
            subprocess.run(cmd_base, cwd=str(BASE), check=False)
        except Exception as e:
            print(f"winner_zone_watcher error: {e}")

        out_abs = BASE / out_path if not Path(out_path).is_absolute() else Path(out_path)
        zone_n = _zone_count(out_abs)
        if zone_n < sanity_min_zones:
            print(
                f"winner_zone_watcher sanity trigger: zone_count={zone_n} < min={sanity_min_zones}; attempting fallback rebuild"
            )
            cmd_fallback = [
                PYTHON,
                "-u",
                str(BASE / "scripts" / "meme_winner_zone_builder.py"),
                "--file",
                str(outcomes_file),
                "--out",
                str(out_path),
                "--out-md",
                str(out_md),
                "--horizon",
                str(horizon),
                "--lookback-hours",
                str(lookback_h),
                "--roundtrip-cost-pct",
                str(roundtrip),
                "--min-samples",
                str(min_samples),
                "--min-win-rate",
                str(min_wr),
                "--min-mean-adj",
                str(min_mean),
                "--max-zones",
                str(max_zones),
                "--coarse-fallback",
                "1",
                "--coarse-min-samples",
                str(sanity_coarse_min_samples),
                "--coarse-min-win-rate",
                str(sanity_coarse_min_wr),
                "--coarse-min-mean-adj",
                str(sanity_coarse_min_mean),
            ]
            try:
                subprocess.run(cmd_fallback, cwd=str(BASE), check=False)
            except Exception as e:
                print(f"winner_zone_watcher fallback error: {e}")
            zone_after = _zone_count(out_abs)
            if zone_after < sanity_min_zones:
                _write_alert(
                    sanity_alert_path,
                    {
                        "ts": time.time(),
                        "status": "degraded",
                        "zone_count": zone_after,
                        "required_min_zones": sanity_min_zones,
                        "action": "fallback_rebuild_attempted",
                        "winner_zone_path": str(out_abs),
                    },
                )
            else:
                _write_alert(
                    sanity_alert_path,
                    {
                        "ts": time.time(),
                        "status": "recovered",
                        "zone_count": zone_after,
                        "required_min_zones": sanity_min_zones,
                        "action": "fallback_rebuild_succeeded",
                        "winner_zone_path": str(out_abs),
                    },
                )
        time.sleep(max(300.0, interval_min * 60.0))


if __name__ == "__main__":
    raise SystemExit(main())
