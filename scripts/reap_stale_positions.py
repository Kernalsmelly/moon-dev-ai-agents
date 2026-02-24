#!/usr/bin/env python3
"""Reap stale open positions from the SQLite store.

Why:
- The meme bot tracks active positions in memory.
- If the bot crashes or is restarted mid-trade, the DB can accumulate positions
  stuck in status='open' for days.
- That can confuse dashboards and any tooling that treats DB "open" as active.

This script marks old open positions as status='stale' (no synthetic trades).
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

load_dotenv(dotenv_path=os.path.join(PROJECT_ROOT, ".env"), override=True)

from src.position_store import PositionStore


def main() -> int:
    hours_raw = (os.getenv("MEME_REAP_STALE_OPEN_HOURS") or os.getenv("REAP_STALE_OPEN_HOURS") or "").strip()
    if not hours_raw:
        # default to a conservative window
        max_age_hours = 24.0
    else:
        try:
            max_age_hours = float(hours_raw)
        except Exception:
            print("reap_stale_positions: invalid MEME_REAP_STALE_OPEN_HOURS", flush=True)
            return 2

    store = PositionStore()
    n = store.reap_stale_open_positions(max_age_hours=max_age_hours)
    print(f"reap_stale_positions: updated={n} max_age_hours={max_age_hours:g}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

