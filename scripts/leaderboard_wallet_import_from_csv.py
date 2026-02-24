#!/usr/bin/env python3
"""Append external leaderboard rows into the wallet-import feed.

Usage:
  python3 scripts/leaderboard_wallet_import_from_csv.py --csv data/my_leaderboard.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
load_dotenv(dotenv_path=os.path.join(PROJECT_ROOT, ".env"), override=False)

DATA_DIR = os.path.join(PROJECT_ROOT, "data")
DEFAULT_OUT = (os.getenv("MEME_LEADERBOARD_IMPORT_JSONL") or "").strip() or os.path.join(
    DATA_DIR, "leaderboard_wallet_import.jsonl"
)
WALLET_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

WALLET_KEYS = ("wallet", "wallet_address", "walletAddress", "address", "trader", "publicKey")
WR_KEYS = ("win_rate", "winRate", "win_pct", "winPercentage", "wr")
PNL_KEYS = ("pnl_usd", "pnlUsd", "pnl", "totalPnl", "profit")
TRADES_KEYS = ("trades", "tradeCount", "txns", "transactions")


def _pick(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        if k in row and row.get(k) not in ("", None):
            return row.get(k)
    return None


def _to_float(v: Any, default: float | None = None) -> float | None:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _to_int(v: Any, default: int | None = None) -> int | None:
    try:
        if v is None:
            return default
        return int(float(v))
    except Exception:
        return default


def main() -> int:
    ap = argparse.ArgumentParser(description="Import wallet leaderboard CSV rows into JSONL feed.")
    ap.add_argument("--csv", required=True, help="Path to CSV file with wallet/perf columns.")
    ap.add_argument("--out", default=DEFAULT_OUT, help=f"Output JSONL file (default: {DEFAULT_OUT})")
    ap.add_argument("--source", default="leaderboard_csv", help="Source label to store in each imported row.")
    args = ap.parse_args()

    csv_path = args.csv
    out_path = args.out
    source = str(args.source or "leaderboard_csv").strip()

    if not os.path.exists(csv_path):
        print(f"CSV not found: {csv_path}")
        return 1

    accepted = 0
    skipped = 0
    now = time.time()

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(csv_path, "r", encoding="utf-8", newline="") as fh, open(out_path, "a", encoding="utf-8") as out:
        reader = csv.DictReader(fh)
        for row in reader:
            if not isinstance(row, dict):
                skipped += 1
                continue
            wallet = str(_pick(row, WALLET_KEYS) or "").strip()
            if not wallet or not WALLET_RE.match(wallet):
                skipped += 1
                continue
            wr = _to_float(_pick(row, WR_KEYS))
            pnl = _to_float(_pick(row, PNL_KEYS))
            trades = _to_int(_pick(row, TRADES_KEYS))
            payload = {
                "wallet": wallet,
                "win_rate": wr,
                "pnl_usd": pnl,
                "trades": trades,
                "source": source,
                "ts": now,
            }
            out.write(json.dumps(payload) + "\n")
            accepted += 1

    print(f"Imported wallets: {accepted} | skipped: {skipped} | out: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
