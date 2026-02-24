#!/usr/bin/env python3
"""Watch for OOS summary and emit best config JSON.

Usage:
  python scripts/meme_oos_watcher.py --oos data/walkforward_6h/oos_summary.csv --out-config config/meme_best_oos.json
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import time
from datetime import datetime


def _to_float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def parse_variant_name(file_name: str) -> dict:
    # strip prefix/suffix
    name = file_name
    if name.endswith(".csv"):
        name = name[:-4]
    if ".v2." in name:
        name = name.split(".v2.", 1)[1]
    elif "." in name:
        name = name.split(".", 1)[-1]

    params = {}
    parts = name.split("_")
    for p in parts:
        if p.startswith("s") and p[1:].isdigit():
            params["MIN_VHI_SCORE"] = int(p[1:])
        elif p.startswith("p") and p[1:].isdigit():
            params["MAX_5M_PUMP"] = int(p[1:])
        elif p.startswith("h") and p[1:].lstrip("-").isdigit():
            params["MIN_PRICE_CHANGE_1H"] = int(p[1:])
        elif p.startswith("la") and p[2:].isdigit():
            params["MIN_LIQ_ACCEL_PCT"] = int(p[2:])
        elif p.startswith("va") and p[2:].isdigit():
            params["MIN_VOL_ACCEL_PCT"] = int(p[2:])
        elif p.startswith("d"):
            try:
                params["SCORE_DECAY_PER_HOUR"] = float(p[1:])
            except Exception:
                pass
        elif p.startswith("mba") and p[3:].isdigit():
            params["MAX_BOOST_AGE_SECONDS"] = int(p[3:])
        elif p.startswith("t10") and p[3:].isdigit():
            params["MAX_TOP10_HOLDER_PCT"] = int(p[3:]) / 100.0
            params["USE_TOP10_CHECK"] = True
        elif p.startswith("tx5") and p[3:].isdigit():
            params["MIN_TXNS_5M"] = int(p[3:])
        elif p.startswith("bs5"):
            try:
                params["MIN_BUY_SELL_RATIO_5M"] = float(p[3:])
            except Exception:
                pass
        elif p.startswith("v1h") and p[3:].isdigit():
            params["MIN_VOLUME_1H"] = int(p[3:])
        elif p.startswith("v5m") and p[3:].isdigit():
            params["MIN_VOLUME_5M"] = int(p[3:])
        elif p.startswith("mlr") and p[3:].isdigit():
            params["MAX_MCAP_LIQ_RATIO"] = int(p[3:])
        elif p.startswith("cd") and p[2:].isdigit():
            params["ENTRY_COOLDOWN_SECONDS"] = int(p[2:])

    return params


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--oos", required=True, help="OOS summary CSV path")
    parser.add_argument("--out-config", required=True, help="Output config JSON path")
    parser.add_argument("--min-test-trades", type=int, default=20, help="Min test trades")
    parser.add_argument("--poll", type=int, default=60, help="Seconds between checks")
    args = parser.parse_args()

    while True:
        if os.path.exists(args.oos):
            rows = []
            with open(args.oos, "r", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    if int(float(row.get("test_trades") or 0)) < args.min_test_trades:
                        continue
                    rows.append(row)

            if not rows:
                print(f"[{datetime.now()}] OOS summary found but no rows meet min trades.")
                return

            rows.sort(key=lambda r: (_to_float(r.get("test_expectancy")), _to_float(r.get("test_net_pnl"))), reverse=True)
            best = rows[0]
            params = parse_variant_name(best.get("file", ""))
            out = {
                "name": "meme_best_oos",
                "source_oos_file": args.oos,
                "selected_variant": best.get("file"),
                "train_expectancy": best.get("train_expectancy"),
                "test_expectancy": best.get("test_expectancy"),
                "train_net_pnl": best.get("train_net_pnl"),
                "test_net_pnl": best.get("test_net_pnl"),
                "train_trades": best.get("train_trades"),
                "test_trades": best.get("test_trades"),
                "parameters": params,
            }
            with open(args.out_config, "w", encoding="utf-8") as fh:
                json.dump(out, fh, indent=2)
            print(f"[{datetime.now()}] Wrote best OOS config to {args.out_config}")
            return

        print(f"[{datetime.now()}] Waiting for OOS summary: {args.oos}")
        time.sleep(max(10, args.poll))


if __name__ == "__main__":
    main()
