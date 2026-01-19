#!/usr/bin/env python3
"""Generate sample shadow trade data for testing the analyzer.

This creates realistic-looking shadow trades to demonstrate the analyzer output.
Run this once to populate data/shadow_trades.csv, then run the analyzer.

Usage:
    python scripts/generate_sample_shadow_data.py
    python src/scripts/shadow_analyzer.py
"""
import os
import random
import sys
from datetime import datetime, timedelta

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.shadow_telemetry import log_complete_shadow_trade

# Sample tokens (realistic Solana tokens)
SAMPLE_TOKENS = [
    ('So11111111111111111111111111111111111111112', 'SOL'),
    ('EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', 'USDC'),
    ('DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263', 'BONK'),
    ('JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN', 'JUP'),
    ('7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr', 'POPCAT'),
    ('EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm', 'WIF'),
    ('MEW1gQWJ3nEXg2qgERiKu7FAFj79PHvQVREQUzScPP5', 'MEW'),
]


def generate_sample_trades(num_trades: int = 25):
    """Generate a mix of winning and losing trades over the past week."""
    print(f"Generating {num_trades} sample shadow trades...")

    now = datetime.now()
    trades_generated = 0

    for i in range(num_trades):
        # Random token
        mint, symbol = random.choice(SAMPLE_TOKENS)

        # Random entry time in past 7 days
        hours_ago = random.randint(1, 168)  # Up to 7 days
        ts_entry = (now - timedelta(hours=hours_ago)).isoformat()

        # Hold time: 1-48 hours
        hold_hours = random.randint(1, 48)
        ts_exit = (now - timedelta(hours=hours_ago - hold_hours)).isoformat()

        # Entry price (varies by token type)
        if symbol == 'SOL':
            entry_price = random.uniform(140, 200)
        elif symbol == 'USDC':
            entry_price = 1.0
        elif symbol in ('BONK', 'MEW'):
            entry_price = random.uniform(0.00001, 0.0001)
        else:
            entry_price = random.uniform(0.5, 10)

        # Position size: $25-100 USD
        amount_usd = random.uniform(25, 100)
        amount_tokens = amount_usd / entry_price

        # Outcome: 55% win rate with realistic P&L distribution
        is_winner = random.random() < 0.55

        if is_winner:
            # Winners: +5% to +50%
            pnl_pct = random.uniform(5, 50)
            exit_reason = random.choice(['TP1', 'TP2', 'manual'])
        else:
            # Losers: -5% to -20% (capped by stop loss)
            pnl_pct = random.uniform(-20, -5)
            exit_reason = random.choice(['SL', 'manual'])

        exit_price = entry_price * (1 + pnl_pct / 100)

        # Fees: roughly 0.3% of trade
        fees_usd = amount_usd * 0.003

        # Slippage: 0.1-2%
        slippage = random.uniform(0.1, 2.0)

        trade_id = log_complete_shadow_trade(
            mint=mint,
            symbol=symbol,
            side='BUY',
            entry_price_usd=entry_price,
            exit_price_usd=exit_price,
            amount_tokens=amount_tokens,
            amount_usd=amount_usd,
            exit_reason=exit_reason,
            fees_usd=fees_usd,
            slippage_pct=slippage,
            ts_entry=ts_entry,
            ts_exit=ts_exit,
            metadata={'source': 'sample_generator', 'batch': i},
        )

        trades_generated += 1
        status = 'WIN' if is_winner else 'LOSS'
        pnl_usd = amount_usd * pnl_pct / 100 - fees_usd
        print(f"  [{trades_generated}] {symbol}: {status} ${pnl_usd:+.2f} ({pnl_pct:+.1f}%) - {exit_reason}")

    print(f"\nGenerated {trades_generated} trades to data/shadow_trades.csv")
    print("\nRun the analyzer to see results:")
    print("  python src/scripts/shadow_analyzer.py")


if __name__ == '__main__':
    generate_sample_trades()
