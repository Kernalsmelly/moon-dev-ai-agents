#!/usr/bin/env python3
"""Low-overhead status watcher for overnight meme pipeline runs."""

from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
BOT_LOG = BASE / "logs" / "meme_bot_early_edge_auto.log"
DB = BASE / "data" / "positions.db"
SIGNALS = BASE / "data" / "meme_launch_signals.jsonl"
OUTCOMES = BASE / "data" / "signal_outcomes.jsonl"
RAW_SIGNALS = BASE / "data" / "meme_launch_signals_raw.jsonl"
SLEEP_S = 300


def _line_count(path: Path) -> int:
    try:
        with path.open("rb") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def _latest_run_id() -> str:
    try:
        txt = BOT_LOG.read_bytes().decode("utf-8", "ignore")
        ids = re.findall(r"run_id=([\w_]+)", txt)
        return ids[-1] if ids else ""
    except Exception:
        return ""


def _run_stats(run_id: str) -> tuple[int, float, float]:
    if not run_id:
        return 0, 0.0, 0.0
    try:
        con = sqlite3.connect(str(DB))
        row = con.execute(
            "select count(*), coalesce(sum(pnl_usd),0), "
            "coalesce(avg(case when pnl_usd>0 then 1.0 else 0.0 end),0) "
            "from trades where json_extract(metadata,'$.run_id')=?",
            (run_id,),
        ).fetchone()
        return int(row[0] or 0), float(row[1] or 0.0), float(row[2] or 0.0)
    except Exception:
        return 0, 0.0, 0.0


def main() -> int:
    while True:
        run_id = _latest_run_id()
        trades, pnl, wr = _run_stats(run_id)
        print(
            f"{int(time.time())} run={run_id} "
            f"trades={trades} pnl={pnl:+.3f} wr={wr*100:.1f}% "
            f"signals={_line_count(SIGNALS)} outcomes={_line_count(OUTCOMES)} "
            f"raw={_line_count(RAW_SIGNALS)}",
            flush=True,
        )
        time.sleep(SLEEP_S)


if __name__ == "__main__":
    raise SystemExit(main())
