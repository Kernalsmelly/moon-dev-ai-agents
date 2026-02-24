#!/usr/bin/env python3
"""Continuously emit compact performance + feature-attribution snapshots.

This is intentionally dumb and resilient:
- It shells out to existing report scripts.
- It never prints secrets (we do not dump env).
- It keeps running even if reports fail temporarily.
"""

from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime


BASE = "/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents"
PYTHON = "/opt/homebrew/bin/python3"


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, str(default)) or default).strip())
    except Exception:
        return default


def _run(cmd: list[str], timeout_s: int = 30) -> str:
    try:
        p = subprocess.run(
            cmd,
            cwd=BASE,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        out = (p.stdout or "").strip()
        err = (p.stderr or "").strip()
        if out and err:
            return out + "\n" + err
        return out or err
    except Exception as e:
        return f"(report failed: {type(e).__name__}: {e})"


def main() -> int:
    interval_s = _env_int("MEME_AUTO_ATTRIBUTION_INTERVAL_S", 300)
    window_min = _env_int("MEME_AUTO_ATTRIBUTION_WINDOW_MIN", 240)
    hours = max(1, int(round(window_min / 60)))
    top = _env_int("MEME_AUTO_ATTRIBUTION_TOP", 10)

    print(
        f"meme_auto_attribution start interval_s={interval_s} window_min={window_min} top={top}",
        flush=True,
    )
    while True:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n===== attribution @ {ts} =====", flush=True)

        # High-level run report (auto-detect run_id from bot log).
        print("\n--- run_report ---", flush=True)
        print(
            _run([PYTHON, "-u", "scripts/meme_run_report.py", "--hours", str(hours), "--auto"], timeout_s=30),
            flush=True,
        )

        # Readiness snapshot (strictly paper gates, run-scoped).
        print("\n--- live_readiness ---", flush=True)
        print(
            _run(
                [
                    PYTHON,
                    "-u",
                    "scripts/meme_live_readiness.py",
                    "--hours",
                    str(hours),
                    "--auto-run-id",
                ],
                timeout_s=30,
            ),
            flush=True,
        )

        # Run-scoped signal funnel (why candidates are rejected/passing).
        print("\n--- signal_debug_rollup ---", flush=True)
        print(
            _run(
                [
                    PYTHON,
                    "-u",
                    "scripts/meme_signal_debug_rollup.py",
                    "--minutes",
                    str(window_min),
                    "--auto-run-id",
                    "--top",
                    "15",
                ],
                timeout_s=30,
            ),
            flush=True,
        )

        # Feature + exit reason attribution over the same window.
        print("\n--- trade_feature_report ---", flush=True)
        print(
            _run(
                [
                    PYTHON,
                    "-u",
                    "scripts/meme_trade_feature_report.py",
                    "--minutes",
                    str(window_min),
                    "--top",
                    str(top),
                    "--auto-run-id",
                ],
                timeout_s=30,
            ),
            flush=True,
        )

        # Run-scoped realized outcomes (tiers/scores) over the same window.
        print("\n--- signal_outcome_report ---", flush=True)
        print(
            _run(
                [
                    PYTHON,
                    "-u",
                    "scripts/meme_signal_outcome_report.py",
                    "--since-hours",
                    str(hours),
                    "--auto-run-id",
                    "--min-trades",
                    "1",
                ],
                timeout_s=30,
            ),
            flush=True,
        )

        # Offline what-if on gate sensitivity from historical outcomes.
        print("\n--- gate_whatif (offline) ---", flush=True)
        print(
            _run(
                [
                    PYTHON,
                    "-u",
                    "scripts/meme_gate_whatif.py",
                    "--horizon",
                    "300",
                    "--roundtrip-cost-pct",
                    "0.03",
                    "--min-samples",
                    "120",
                ],
                timeout_s=30,
            ),
            flush=True,
        )

        # Sleep until next snapshot.
        time.sleep(max(30, interval_s))


if __name__ == "__main__":
    raise SystemExit(main())
