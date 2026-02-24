#!/usr/bin/env python3
"""Build early-window launch features from snapshot data.

This script summarizes the first N seconds after a token appears and
computes a simple future max-return metric.

Usage:
  python scripts/meme_launch_features.py --input data/meme_snapshots.jsonl --out data/meme_launch_features.csv
"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from typing import Optional


@dataclass
class MintState:
    mint: str
    symbol: str
    first_ts: float
    first_price: float
    window_end: float
    future_end: float
    last_ts: float
    last_price: float
    price_at_30s: Optional[float] = None
    price_at_120s: Optional[float] = None
    max_liquidity: float = 0.0
    max_volume_5m: float = 0.0
    last_buys_5m: int = 0
    last_sells_5m: int = 0
    last_txns_5m: int = 0
    last_volume_1h: float = 0.0
    last_volume_5m: float = 0.0
    last_price_change_5m: float = 0.0
    last_price_change_1h: float = 0.0
    snapshots_in_window: int = 0
    max_future_price: float = 0.0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Snapshot JSONL")
    parser.add_argument("--out", default="data/meme_launch_features.csv", help="Output CSV")
    parser.add_argument("--window-sec", type=int, default=120, help="Early window duration")
    parser.add_argument("--future-sec", type=int, default=1800, help="Future window for max return")
    parser.add_argument("--min-window-snaps", type=int, default=3, help="Min snapshots in early window")
    args = parser.parse_args()

    states: dict[str, MintState] = {}

    with open(args.input, "r", encoding="utf-8") as fh:
        for line in fh:
            try:
                s = json.loads(line)
            except Exception:
                continue
            mint = s.get("mint")
            if not mint:
                continue
            ts = float(s.get("ts", 0) or 0)
            price = float(s.get("price", 0) or 0)
            if price <= 0 or ts <= 0:
                continue

            state = states.get(mint)
            if state is None:
                # First observation becomes the launch time
                symbol = s.get("symbol") or ""
                state = MintState(
                    mint=mint,
                    symbol=symbol,
                    first_ts=ts,
                    first_price=price,
                    window_end=ts + args.window_sec,
                    future_end=ts + args.window_sec + args.future_sec,
                    last_ts=ts,
                    last_price=price,
                    max_future_price=price,
                )
                states[mint] = state

            # Update window stats
            if ts <= state.window_end:
                state.snapshots_in_window += 1
                state.last_ts = ts
                state.last_price = price
                state.max_liquidity = max(state.max_liquidity, float(s.get("liquidity", 0) or 0))
                v5 = float(s.get("volume_5m", 0) or 0)
                state.max_volume_5m = max(state.max_volume_5m, v5)
                buys_5m = int(s.get("buys_5m", 0) or 0)
                sells_5m = int(s.get("sells_5m", 0) or 0)
                state.last_buys_5m = buys_5m
                state.last_sells_5m = sells_5m
                state.last_txns_5m = buys_5m + sells_5m
                state.last_volume_1h = float(s.get("volume_1h", 0) or 0)
                state.last_volume_5m = v5
                state.last_price_change_5m = float(s.get("price_change_5m", 0) or 0)
                state.last_price_change_1h = float(s.get("price_change_1h", 0) or 0)

                # Capture price around 30s and window end (closest)
                if state.price_at_30s is None and ts >= state.first_ts + 30:
                    state.price_at_30s = price
                if state.price_at_120s is None and ts >= state.window_end:
                    state.price_at_120s = price

            # Update future max price
            if state.window_end < ts <= state.future_end:
                if price > state.max_future_price:
                    state.max_future_price = price

    # Emit CSV
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        fieldnames = [
            "mint", "symbol", "first_ts", "first_price",
            "window_sec", "snapshots_in_window",
            "max_liquidity", "max_volume_5m",
            "buys_5m", "sells_5m", "txns_5m",
            "volume_1h", "volume_5m",
            "price_change_5m", "price_change_1h",
            "price_at_30s", "price_at_window",
            "price_accel",
            "max_future_price", "max_return_future",
        ]
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()

        for state in states.values():
            if state.snapshots_in_window < args.min_window_snaps:
                continue
            price_at_window = state.price_at_120s or state.last_price
            price_at_30s = state.price_at_30s or state.first_price
            accel = 0.0
            if state.first_price > 0:
                r30 = (price_at_30s / state.first_price) - 1.0
                r120 = (price_at_window / state.first_price) - 1.0
                accel = r120 - r30
            max_ret = 0.0
            if price_at_window > 0:
                max_ret = (state.max_future_price / price_at_window) - 1.0

            writer.writerow({
                "mint": state.mint,
                "symbol": state.symbol,
                "first_ts": round(state.first_ts, 3),
                "first_price": state.first_price,
                "window_sec": args.window_sec,
                "snapshots_in_window": state.snapshots_in_window,
                "max_liquidity": round(state.max_liquidity, 2),
                "max_volume_5m": round(state.max_volume_5m, 2),
                "buys_5m": state.last_buys_5m,
                "sells_5m": state.last_sells_5m,
                "txns_5m": state.last_txns_5m,
                "volume_1h": round(state.last_volume_1h, 2),
                "volume_5m": round(state.last_volume_5m, 2),
                "price_change_5m": round(state.last_price_change_5m, 3),
                "price_change_1h": round(state.last_price_change_1h, 3),
                "price_at_30s": price_at_30s,
                "price_at_window": price_at_window,
                "price_accel": round(accel, 4),
                "max_future_price": state.max_future_price,
                "max_return_future": round(max_ret, 4),
            })

    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
