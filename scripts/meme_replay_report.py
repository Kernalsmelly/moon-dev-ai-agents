#!/usr/bin/env python3
"""Generate a summary report for meme replay CSV outputs.

Usage:
  python scripts/meme_replay_report.py --input data/meme_replay_trades.csv
"""
from __future__ import annotations

import argparse
import csv
import os
from statistics import mean, median, pstdev


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Replay trades CSV")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print("File not found:", args.input)
        return

    trades = load_trades(args.input)
    if not trades:
        print("No trades found.")
        return

    # Sort by exit time for equity curve
    try:
        trades.sort(key=lambda t: float(t.get("exit_ts") or 0))
    except Exception:
        pass

    pnls = [to_float(t.get("pnl_usd")) for t in trades]
    win_trades = [p for p in pnls if p > 0]
    loss_trades = [p for p in pnls if p <= 0]

    win_rate = (len(win_trades) / len(trades)) * 100.0
    avg_win = mean(win_trades) if win_trades else 0.0
    avg_loss = mean(loss_trades) if loss_trades else 0.0
    expectancy = (win_rate / 100.0) * avg_win + (1 - win_rate / 100.0) * avg_loss

    # Hold time stats (minutes)
    holds = []
    reason_counts = {}
    for t in trades:
        try:
            entry_ts = float(t.get("entry_ts") or 0)
            exit_ts = float(t.get("exit_ts") or 0)
            if entry_ts and exit_ts and exit_ts >= entry_ts:
                holds.append((exit_ts - entry_ts) / 60.0)
        except Exception:
            continue
        try:
            reason = str(t.get("exit_reason") or "").strip()
            if reason:
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
        except Exception:
            pass
    avg_hold = mean(holds) if holds else 0.0
    med_hold = median(holds) if holds else 0.0

    # Equity curve + max drawdown
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    equity_curve = []
    for p in pnls:
        equity += p
        equity_curve.append(equity)
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd

    print("Replay Report")
    print(f"Trades: {len(trades)}")
    print(f"Win Rate: {win_rate:.2f}%")
    print(f"Avg Win: {avg_win:.4f}")
    print(f"Avg Loss: {avg_loss:.4f}")
    print(f"Net PnL: {sum(pnls):.4f}")
    print(f"Expectancy: {expectancy:.4f}")
    # Risk metrics
    pnl_std = pstdev(pnls) if len(pnls) > 1 else 0.0
    sharpe_like = (mean(pnls) / pnl_std) * (len(pnls) ** 0.5) if pnl_std > 0 else 0.0
    profit_factor = (sum(win_trades) / abs(sum(loss_trades))) if loss_trades else 999.0

    print(f"Max Drawdown: {max_dd:.4f}")
    print(f"Avg Hold (min): {avg_hold:.2f}")
    print(f"Median Hold (min): {med_hold:.2f}")
    print(f"Profit Factor: {profit_factor:.3f}")
    print(f"Sharpe-like: {sharpe_like:.3f}")
    if reason_counts:
        top_reasons = sorted(reason_counts.items(), key=lambda r: r[1], reverse=True)[:6]
        reason_str = ", ".join([f"{k}:{v}" for k, v in top_reasons])
        print(f"Top Exit Reasons: {reason_str}")

    # Save equity curve CSV next to input
    try:
        curve_path = args.input.replace(".csv", ".equity.csv")
        with open(curve_path, "w", newline="", encoding="utf-8") as fh:
            fh.write("step,equity\n")
            for i, v in enumerate(equity_curve, 1):
                fh.write(f"{i},{v}\n")
        print(f"Equity curve saved: {curve_path}")
    except Exception:
        pass


if __name__ == "__main__":
    main()
