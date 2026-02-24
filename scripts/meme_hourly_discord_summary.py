#!/usr/bin/env python3
"""Post an hourly meme-bot performance summary to Discord.

Reads local SQLite (data/positions.db) and sends a compact summary via src.alerts.
Designed to be run as a long-lived process (sleep loop).

Env:
- MEME_HOURLY_SUMMARY_ENABLED: 1/true to run (default: true when launched directly)
- MEME_HOURLY_SUMMARY_INTERVAL_MINUTES: default 60
- MEME_HOURLY_SUMMARY_ALIGN_TO_HOUR: default true (post near HH:00)
- MEME_HOURLY_SUMMARY_MIN_TRADES: default 1 (skip empty windows)
- MEME_HOURLY_SUMMARY_LEVEL: info/warning/error (default info)
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
DB_PATH = BASE / "data" / "positions.db"

# Ensure repo root is on sys.path so `import src...` works when launched from elsewhere.
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

load_dotenv(dotenv_path=str(BASE / ".env"), override=True)

from src.alerts import send_system_alert


def _env_bool(name: str, default: bool) -> bool:
    v = str(os.getenv(name, "" if default is None else str(default))).strip().lower()
    if not v:
        return bool(default)
    return v in ("1", "true", "yes", "y", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)) or default)
    except Exception:
        return default


def _env_str(name: str, default: str) -> str:
    return str(os.getenv(name, default) or default)


def _fmt_money(v: float) -> str:
    return f"${v:+.2f}"


@dataclass
class WindowStats:
    n: int
    pnl: float
    wins: int
    losses: int
    reasons: dict[str, dict[str, float]]


def _query_window(cur: sqlite3.Cursor, minutes: int) -> WindowStats:
    rows = cur.execute(
        """
        SELECT pnl_usd, exit_reason
        FROM trades
        WHERE side='SELL'
          AND replace(exit_timestamp,'T',' ') >= datetime('now','localtime', ?)
        """,
        (f"-{int(minutes)} minutes",),
    ).fetchall()

    pnl_sum = 0.0
    wins = 0
    losses = 0
    reasons = defaultdict(lambda: {"n": 0, "pnl": 0.0})
    for pnl, reason in rows:
        try:
            p = float(pnl or 0.0)
        except Exception:
            p = 0.0
        pnl_sum += p
        if p > 0:
            wins += 1
        elif p < 0:
            losses += 1
        r = str(reason or "UNKNOWN")
        reasons[r]["n"] += 1
        reasons[r]["pnl"] += p

    return WindowStats(n=len(rows), pnl=pnl_sum, wins=wins, losses=losses, reasons=dict(reasons))


def _open_positions(cur: sqlite3.Cursor) -> int:
    try:
        row = cur.execute("SELECT COUNT(*) FROM positions WHERE status='open'").fetchone()
        return int(row[0] or 0) if row else 0
    except Exception:
        return 0


def _top_reasons(reasons: dict[str, dict[str, float]], *, worst: bool, limit: int = 4) -> list[tuple[str, int, float]]:
    items = []
    for k, v in reasons.items():
        try:
            n = int(v.get("n") or 0)
        except Exception:
            n = 0
        try:
            pnl = float(v.get("pnl") or 0.0)
        except Exception:
            pnl = 0.0
        items.append((str(k), n, pnl))
    items.sort(key=lambda x: x[2], reverse=(not worst))
    return items[:limit]


def _render_reason_list(items: list[tuple[str, int, float]]) -> str:
    if not items:
        return "-"
    return "\n".join([f"{name}: {_fmt_money(pnl)} (n={n})" for name, n, pnl in items])


def _sleep_until_next_hour() -> None:
    # Sleep until ~HH:00:10 local time.
    now = time.time()
    lt = time.localtime(now)
    # seconds since hour
    since_hour = lt.tm_min * 60 + lt.tm_sec
    to_next = (3600 - since_hour) + 10
    time.sleep(max(5.0, float(to_next)))


def post_once(minutes: int) -> bool:
    if not DB_PATH.exists():
        return False

    con = sqlite3.connect(str(DB_PATH))
    try:
        cur = con.cursor()
        w = _query_window(cur, minutes)
        opens = _open_positions(cur)
    finally:
        con.close()

    min_trades = _env_int("MEME_HOURLY_SUMMARY_MIN_TRADES", 1)
    if w.n < min_trades:
        try:
            print(f"hourly_summary skip window={minutes}m n={w.n} (min_trades={min_trades})", flush=True)
        except Exception:
            pass
        return False

    wr = (100.0 * w.wins / w.n) if w.n else 0.0
    worst = _top_reasons(w.reasons, worst=True, limit=4)
    best = _top_reasons(w.reasons, worst=False, limit=3)

    level = _env_str("MEME_HOURLY_SUMMARY_LEVEL", "info")

    fields = [
        {"name": f"Last {minutes}m", "value": f"P&L {_fmt_money(w.pnl)} | {w.wins}W/{w.losses}L | WR {wr:.0f}%", "inline": False},
        {"name": "Top Drags", "value": _render_reason_list(worst)[:900], "inline": False},
        {"name": "Top Winners", "value": _render_reason_list(best)[:900], "inline": False},
        {"name": "Open Positions", "value": str(opens), "inline": True},
    ]

    ok = bool(
        send_system_alert(
            title="Hourly Meme Summary",
            description="Automated summary from positions.db (paper/live independent).",
            level=level,
            fields=fields,
        )
    )
    try:
        print(f"hourly_summary post window={minutes}m n={w.n} pnl={_fmt_money(w.pnl)} wr={wr:.0f}% ok={ok}", flush=True)
    except Exception:
        pass
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="Post once and exit")
    ap.add_argument("--minutes", type=int, default=60)
    args = ap.parse_args()

    # Detach from the launching shell so this can be started from short-lived
    # command sessions without getting reaped when the parent exits.
    if not args.once:
        try:
            os.setsid()
        except Exception:
            pass

    enabled = _env_bool("MEME_HOURLY_SUMMARY_ENABLED", True)
    if not enabled:
        return 0

    if args.once:
        post_once(args.minutes)
        return 0

    interval_min = max(5, _env_int("MEME_HOURLY_SUMMARY_INTERVAL_MINUTES", 60))
    align = _env_bool("MEME_HOURLY_SUMMARY_ALIGN_TO_HOUR", True)

    while True:
        try:
            post_once(interval_min)
        except Exception:
            pass

        if align and interval_min >= 60:
            _sleep_until_next_hour()
        else:
            time.sleep(float(interval_min) * 60.0)


if __name__ == "__main__":
    raise SystemExit(main())
