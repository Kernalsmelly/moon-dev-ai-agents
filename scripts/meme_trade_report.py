#!/usr/bin/env python3
"""Trade report from positions DB (last N hours)."""
from __future__ import annotations

import argparse
import os
import sqlite3
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/positions.db")
    parser.add_argument("--hours", type=int, default=6)
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print("No positions.db found.")
        return

    cutoff = time.time() - (args.hours * 3600)
    cutoff_iso = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(cutoff))

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "SELECT * FROM trades WHERE created_at >= ? ORDER BY created_at DESC",
        (cutoff_iso,),
    )
    rows = cur.fetchall()
    conn.close()

    trades = len(rows)
    pnl = sum(r["pnl_usd"] or 0 for r in rows)
    wins = sum(1 for r in rows if (r["pnl_usd"] or 0) > 0)
    wr = (wins / trades * 100) if trades else 0.0

    print(f"Trades (last {args.hours}h): {trades}")
    print(f"Win rate: {wr:.2f}%")
    print(f"PnL: {pnl:.2f}")


if __name__ == "__main__":
    main()
