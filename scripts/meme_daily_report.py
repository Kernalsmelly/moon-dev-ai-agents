#!/usr/bin/env python3
"""Generate a daily performance report from replay trades.

Usage:
  python scripts/meme_daily_report.py --input data/meme_replay_trades.csv --out data/meme_daily_report.txt
"""
from __future__ import annotations

import argparse
import csv
import os
from statistics import mean, median, pstdev
from datetime import datetime


def to_float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Replay trades CSV")
    parser.add_argument("--out", default="data/meme_daily_report.txt", help="Output report path")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print("File not found:", args.input)
        return

    trades = []
    with open(args.input, "r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            trades.append(row)

    if not trades:
        print("No trades found.")
        return

    pnls = [to_float(t.get("pnl_usd")) for t in trades]
    win_trades = [p for p in pnls if p > 0]
    loss_trades = [p for p in pnls if p <= 0]

    win_rate = (len(win_trades) / len(trades)) * 100.0
    avg_win = mean(win_trades) if win_trades else 0.0
    avg_loss = mean(loss_trades) if loss_trades else 0.0
    expectancy = (win_rate / 100.0) * avg_win + (1 - win_rate / 100.0) * avg_loss

    # Max drawdown (equity curve)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    pnl_std = pstdev(pnls) if len(pnls) > 1 else 0.0
    sharpe_like = (mean(pnls) / pnl_std) * (len(pnls) ** 0.5) if pnl_std > 0 else 0.0
    profit_factor = (sum(win_trades) / abs(sum(loss_trades))) if loss_trades else 999.0

    # Exit reasons
    reason_counts = {}
    for t in trades:
        reason = str(t.get("exit_reason") or "").strip()
        if reason:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    top_reasons = sorted(reason_counts.items(), key=lambda r: r[1], reverse=True)[:8]

    lines = []
    lines.append(f"Meme Daily Report — {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"Trades: {len(trades)}")
    lines.append(f"Win Rate: {win_rate:.2f}%")
    lines.append(f"Avg Win: {avg_win:.4f}")
    lines.append(f"Avg Loss: {avg_loss:.4f}")
    lines.append(f"Net PnL: {sum(pnls):.4f}")
    lines.append(f"Expectancy: {expectancy:.4f}")
    lines.append(f"Max Drawdown: {max_dd:.4f}")
    lines.append(f"Profit Factor: {profit_factor:.3f}")
    lines.append(f"Sharpe-like: {sharpe_like:.3f}")
    if top_reasons:
        lines.append("Top Exit Reasons:")
        for k, v in top_reasons:
            lines.append(f"- {k}: {v}")

    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    print(f"Report saved: {args.out}")


if __name__ == "__main__":
    main()
