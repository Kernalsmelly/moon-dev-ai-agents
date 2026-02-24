#!/usr/bin/env python3
"""Periodic edge report for the meme pipeline.

Writes a compact line to logs/meme_edge_report.log every N seconds so we can track:
- trade count / pnl / winrate
- which exit reasons are dragging (MAX_LOSS_CAP, FAIL_FAST, etc.)

This keeps iteration tight without needing interactive terminal checks.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections import defaultdict
from pathlib import Path


BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
DB_PATH = BASE / "data" / "positions.db"
LOG_PATH = BASE / "logs" / "meme_edge_report.log"
EDGE_RUN_STATE = BASE / "data" / "edge_run_state.json"


def _iso(ts: float) -> str:
    # keep it simple and stable: localtime ISO seconds
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))


def _query_cutoff_iso(cutoff_iso: str) -> dict:
    out = {
        "n": 0,
        "pnl": 0.0,
        "wins": 0,
        "losses": 0,
        "avg_win": None,
        "avg_loss": None,
        "reasons": {},
    }
    if not DB_PATH.exists():
        return out

    con = sqlite3.connect(str(DB_PATH))
    try:
        cur = con.cursor()
        rows = cur.execute(
            """
            SELECT pnl_usd, exit_reason
            FROM trades
            WHERE side='SELL' AND exit_timestamp >= ?
            """,
            (cutoff_iso,),
        ).fetchall()
    finally:
        con.close()

    if not rows:
        return out

    pnl_sum = 0.0
    wins = []
    losses = []
    reasons = defaultdict(lambda: {"n": 0, "pnl": 0.0})
    for pnl, reason in rows:
        try:
            p = float(pnl or 0.0)
        except Exception:
            p = 0.0
        pnl_sum += p
        if p > 0:
            wins.append(p)
        elif p < 0:
            losses.append(p)
        r = str(reason or "UNKNOWN")
        reasons[r]["n"] += 1
        reasons[r]["pnl"] += p

    out["n"] = len(rows)
    out["pnl"] = pnl_sum
    out["wins"] = len(wins)
    out["losses"] = len(losses)
    out["avg_win"] = (sum(wins) / len(wins)) if wins else None
    out["avg_loss"] = (sum(losses) / len(losses)) if losses else None
    # keep as plain dict for json-ish logging
    out["reasons"] = {k: {"n": v["n"], "pnl": v["pnl"]} for k, v in reasons.items()}
    return out


def main() -> int:
    interval_s = float(os.getenv("MEME_EDGE_REPORT_INTERVAL_S", "300") or 300)
    hours = float(os.getenv("MEME_EDGE_REPORT_WINDOW_H", "2") or 2)
    minutes = float(os.getenv("MEME_EDGE_REPORT_WINDOW_M", "30") or 30)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    started_ts = time.time()
    started_iso = _iso(started_ts)
    last_run_state_ts = 0.0
    run_id = ""

    def _maybe_reset_run_window() -> None:
        nonlocal started_ts, started_iso, last_run_state_ts, run_id
        if not EDGE_RUN_STATE.exists():
            return
        try:
            obj = json.loads(EDGE_RUN_STATE.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(obj, dict):
            return
        try:
            rst = float(obj.get("run_started_ts") or 0.0)
        except Exception:
            rst = 0.0
        try:
            rid = str(obj.get("run_id") or "").strip()
        except Exception:
            rid = ""
        if not rst or rst <= 0:
            return
        # Avoid thrash: only reset if the value actually changed.
        if abs(rst - last_run_state_ts) < 0.5:
            return
        last_run_state_ts = rst
        started_ts = rst
        started_iso = _iso(started_ts)
        run_id = rid

    while True:
        now = time.time()
        _maybe_reset_run_window()
        rep_2h = _query_cutoff_iso(_iso(now - (hours * 3600.0)))
        rep_30m = _query_cutoff_iso(_iso(now - (minutes * 60.0)))
        rep_run = _query_cutoff_iso(started_iso)

        # Extract the reasons we most care about.
        def r(rep: dict, name: str) -> tuple[int, float]:
            d = rep["reasons"].get(name) or {}
            return int(d.get("n") or 0), float(d.get("pnl") or 0.0)

        def fmt(rep: dict, label: str) -> str:
            wr = (100.0 * rep["wins"] / rep["n"]) if rep["n"] else 0.0
            max_n, max_p = r(rep, "MAX_LOSS_CAP")
            ff_n, ff_p = r(rep, "FAIL_FAST")
            dump_n, dump_p = r(rep, "MOMENTUM_DUMP")
            plateau_n, plateau_p = r(rep, "MCAP_PLATEAU")
            return (
                f"{label} n={rep['n']} pnl={rep['pnl']:+.2f} wr={wr:.0f}% "
                f"MAX_LOSS({max_n},{max_p:+.2f}) FF({ff_n},{ff_p:+.2f}) "
                f"DUMP({dump_n},{dump_p:+.2f}) PLAT({plateau_n},{plateau_p:+.2f})"
            )

        prefix = f"run_id={run_id} " if run_id else ""
        line = " ".join(
            [
                f"edge_report ts={_iso(now)}",
                prefix.strip(),
                fmt(rep_run, "run"),
                fmt(rep_30m, "30m"),
                fmt(rep_2h, f"{int(hours)}h"),
            ]
        )
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

        time.sleep(max(5.0, interval_s))


if __name__ == "__main__":
    raise SystemExit(main())
