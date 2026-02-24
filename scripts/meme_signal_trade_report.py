#!/usr/bin/env python3
"""Report PnL by signal tier from trades metadata."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import defaultdict


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/positions.db")
    parser.add_argument("--hours", type=int, default=24)
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print("No positions.db found.")
        return

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "SELECT * FROM trades WHERE created_at >= datetime('now', ?)",
        (f"-{args.hours} hours",),
    )
    rows = cur.fetchall()
    conn.close()

    buckets = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})
    for r in rows:
        meta = {}
        try:
            meta = json.loads(r["metadata"] or "{}")
        except Exception:
            meta = {}
        tier = meta.get("signal_tier") or "unknown"
        pnl = r["pnl_usd"] or 0.0
        buckets[tier]["trades"] += 1
        buckets[tier]["pnl"] += pnl
        if pnl > 0:
            buckets[tier]["wins"] += 1

    print(f"Signal-tier report (last {args.hours}h)")
    for tier, stats in sorted(buckets.items()):
        trades = stats["trades"]
        pnl = stats["pnl"]
        wr = (stats["wins"] / trades * 100) if trades else 0.0
        print(f"{tier}: trades={trades} pnl={pnl:.2f} wr={wr:.1f}%")


if __name__ == "__main__":
    main()
