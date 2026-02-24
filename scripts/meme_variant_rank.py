#!/usr/bin/env python3
"""Rank variant CSV outputs by expectancy and drawdown.

Usage:
  python scripts/meme_variant_rank.py --dir data
"""
from __future__ import annotations

import argparse
import csv
import os
from statistics import mean, pstdev


def load_trades(path: str) -> list[dict]:
    trades = []
    with open(path, "r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            trades.append(row)
    return trades


def to_float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def summarize(path: str) -> dict:
    trades = load_trades(path)
    pnls = [to_float(t.get("pnl_usd")) for t in trades]
    win_trades = [p for p in pnls if p > 0]
    loss_trades = [p for p in pnls if p <= 0]

    win_rate = (len(win_trades) / len(trades)) * 100.0 if trades else 0.0
    avg_win = mean(win_trades) if win_trades else 0.0
    avg_loss = mean(loss_trades) if loss_trades else 0.0
    expectancy = (win_rate / 100.0) * avg_win + (1 - win_rate / 100.0) * avg_loss
    pnl_std = pstdev(pnls) if len(pnls) > 1 else 0.0
    sharpe_like = (mean(pnls) / pnl_std) * (len(pnls) ** 0.5) if pnl_std > 0 else 0.0
    loss_sum = abs(sum(loss_trades)) if loss_trades else 0.0
    profit_factor = (sum(win_trades) / loss_sum) if loss_sum > 0 else 999.0

    # drawdown
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd

    return {
        "trades": len(trades),
        "win_rate": win_rate,
        "expectancy": expectancy,
        "net_pnl": sum(pnls),
        "max_dd": max_dd,
        "profit_factor": profit_factor,
        "sharpe_like": sharpe_like,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default="data", help="Directory with replay CSVs")
    parser.add_argument("--min-trades", type=int, default=20, help="Minimum trades to include")
    parser.add_argument("--out", default="data/meme_variant_rank.csv", help="Output CSV")
    args = parser.parse_args()

    rows = []
    for fn in os.listdir(args.dir):
        if not fn.startswith("meme_replay_trades"):
            continue
        if not fn.endswith(".csv"):
            continue
        path = os.path.join(args.dir, fn)
        summary = summarize(path)
        if summary["trades"] < args.min_trades:
            continue
        rows.append((fn, summary))

    # Rank by expectancy, then by max drawdown (lower is better)
    rows.sort(key=lambda r: (r[1]["expectancy"], -r[1]["max_dd"]), reverse=True)

    print("Variant Rankings")
    print("file,trades,win_rate,expectancy,net_pnl,max_dd,profit_factor,sharpe_like")
    for fn, s in rows[:25]:
        print(
            f"{fn},{s['trades']},{s['win_rate']:.2f},{s['expectancy']:.4f},"
            f"{s['net_pnl']:.2f},{s['max_dd']:.2f},{s['profit_factor']:.2f},{s['sharpe_like']:.2f}"
        )

    # Write full ranking to CSV
    try:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write("file,trades,win_rate,expectancy,net_pnl,max_dd,profit_factor,sharpe_like\n")
            for fn, s in rows:
                fh.write(
                    f"{fn},{s['trades']},{s['win_rate']:.2f},{s['expectancy']:.4f},"
                    f"{s['net_pnl']:.2f},{s['max_dd']:.2f},{s['profit_factor']:.2f},{s['sharpe_like']:.2f}\n"
                )
        print(f"Ranking CSV saved: {args.out}")
    except Exception:
        pass


if __name__ == "__main__":
    main()
