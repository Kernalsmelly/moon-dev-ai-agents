#!/usr/bin/env python3
"""Parameter Backtesting Script: Replay historical trades through MemeExitManager with varied params.

For each trade: creates PositionState, steps through simulated price path, calls check_exit().

Parameter grid:
- TP_TIERS: 3 variants (current, wider-first, tighter-first)
- INITIAL_STOP_LOSS: [-0.12, -0.15, -0.18, -0.20]
- TRAILING_ACTIVATION: [0.08, 0.10, 0.12, 0.15]
- QUICK_SCALP_GAIN: [0.08, 0.10, 0.12, 0.15]

Output: src/data/strategy_tuning/backtest_results.csv

Usage:
    python scripts/backtest_params.py
    python scripts/backtest_params.py --days 30
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import os
import sys
import time
from dataclasses import dataclass
from collections import defaultdict
from datetime import datetime

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ============================================================================
# Parameter grid definitions
# ============================================================================

# TP tier variants: (name, tiers_list)
TP_VARIANTS = {
    'current': [
        (0.25, 0.25),  # TP0: +25%
        (0.50, 0.25),  # TP1: +50%
        (0.80, 0.20),  # TP2: +80%
        (1.20, 0.10),  # TP3: +120%
        (2.00, 0.10),  # TP4: +200%
    ],
    'wider_first': [
        (0.35, 0.25),  # TP0: +35% (wider first take)
        (0.60, 0.25),  # TP1: +60%
        (1.00, 0.20),  # TP2: +100%
        (1.50, 0.10),  # TP3: +150%
        (2.50, 0.10),  # TP4: +250%
    ],
    'tighter_first': [
        (0.15, 0.25),  # TP0: +15% (quick first take)
        (0.35, 0.25),  # TP1: +35%
        (0.60, 0.20),  # TP2: +60%
        (1.00, 0.10),  # TP3: +100%
        (1.80, 0.10),  # TP4: +180%
    ],
}

STOP_LOSS_VALUES = [-0.12, -0.15, -0.18, -0.20]
TRAILING_ACTIVATION_VALUES = [0.08, 0.10, 0.12, 0.15]
QUICK_SCALP_GAIN_VALUES = [0.08, 0.10, 0.12, 0.15]


@dataclass
class BacktestTrade:
    """A single trade from history with its price path."""
    mint: str
    symbol: str
    entry_price: float
    entry_time: float
    exit_price: float
    amount_usd: float
    pnl_usd: float
    pnl_pct: float
    exit_reason: str
    hold_time_sec: float
    score: int
    # Simulated price path (list of prices from entry to exit)
    price_path: list[float]


def load_historical_trades(days: int | None = None) -> list[BacktestTrade]:
    """Load trades from position store for replay."""
    trades = []
    try:
        from src.position_store import PositionStore
        store = PositionStore()
        raw = store.get_trades(days=days, limit=10000)
    except Exception as e:
        print(f"[WARN] Could not load trades: {e}")
        return trades

    for t in raw:
        meta = t.get('metadata', {})
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}

        # Skip test/synthetic
        mint = str(t.get('mint', '')).lower()
        if 'fake' in mint or 'test' in mint:
            continue

        entry_price = float(t.get('entry_price', 0))
        exit_price = float(t.get('exit_price', 0))
        if entry_price <= 0:
            continue

        hold_sec = float(meta.get('hold_time_sec', 0))
        if hold_sec <= 0:
            # Try to compute from timestamps
            try:
                entry_dt = datetime.fromisoformat(t['entry_timestamp'])
                exit_dt = datetime.fromisoformat(t['exit_timestamp'])
                hold_sec = (exit_dt - entry_dt).total_seconds()
            except Exception:
                hold_sec = 300  # default 5 min

        score = int(meta.get('entry_score', meta.get('score', 50)))

        # Generate a synthetic price path from entry to exit
        # Uses highest_price_seen if available for realistic peak
        highest = float(meta.get('highest_price_seen', max(entry_price, exit_price)))
        price_path = _generate_price_path(entry_price, exit_price, highest, hold_sec)

        trades.append(BacktestTrade(
            mint=t.get('mint', ''),
            symbol=t.get('symbol', ''),
            entry_price=entry_price,
            entry_time=0,  # Relative time, start at 0
            exit_price=exit_price,
            amount_usd=float(t.get('amount_usd', 25)),
            pnl_usd=float(t.get('pnl_usd', 0)),
            pnl_pct=float(t.get('pnl_pct', 0)),
            exit_reason=t.get('exit_reason', ''),
            hold_time_sec=hold_sec,
            score=score,
            price_path=price_path,
        ))

    return trades


def _generate_price_path(
    entry_price: float,
    exit_price: float,
    highest_price: float,
    hold_sec: float,
    steps: int = 60,
) -> list[float]:
    """Generate a synthetic price path for backtesting.

    Creates a path that goes from entry -> peak -> exit with some noise.
    """
    if steps < 3:
        return [entry_price, highest_price, exit_price]

    import random
    path = []

    # Phase 1: entry to peak (first 60% of steps)
    peak_step = int(steps * 0.6)
    for i in range(peak_step):
        frac = i / peak_step
        price = entry_price + (highest_price - entry_price) * frac
        # Add small noise (1% max)
        noise = random.uniform(-0.01, 0.01) * price
        path.append(max(price + noise, entry_price * 0.5))

    # Phase 2: peak to exit (last 40%)
    remaining = steps - peak_step
    for i in range(remaining):
        frac = i / remaining
        price = highest_price + (exit_price - highest_price) * frac
        noise = random.uniform(-0.01, 0.01) * price
        path.append(max(price + noise, entry_price * 0.1))

    path.append(exit_price)
    return path


def run_backtest(
    trades: list[BacktestTrade],
    tp_tiers: list[tuple],
    initial_stop_loss: float,
    trailing_activation: float,
    quick_scalp_gain: float,
) -> dict:
    """Run exit manager simulation with given parameters on all trades.

    Returns aggregate stats dict.
    """
    import src.meme_config as meme_config
    from src.meme_exit_manager import MemeExitManager, PositionState

    # Override config temporarily
    orig_tp = meme_config.TP_TIERS
    orig_sl = meme_config.INITIAL_STOP_LOSS
    orig_trail = meme_config.TRAILING_ACTIVATION
    orig_scalp = meme_config.QUICK_SCALP_GAIN

    meme_config.TP_TIERS = tp_tiers
    meme_config.INITIAL_STOP_LOSS = initial_stop_loss
    meme_config.TRAILING_ACTIVATION = trailing_activation
    meme_config.QUICK_SCALP_GAIN = quick_scalp_gain
    # Also sync breakeven with trailing to avoid dead zone
    orig_be = meme_config.BREAKEVEN_TRIGGER
    meme_config.BREAKEVEN_TRIGGER = trailing_activation

    try:
        manager = MemeExitManager()
        results = _simulate_trades(manager, trades)
    finally:
        # Restore originals
        meme_config.TP_TIERS = orig_tp
        meme_config.INITIAL_STOP_LOSS = orig_sl
        meme_config.TRAILING_ACTIVATION = orig_trail
        meme_config.QUICK_SCALP_GAIN = orig_scalp
        meme_config.BREAKEVEN_TRIGGER = orig_be

    return results


def _simulate_trades(manager, trades: list[BacktestTrade]) -> dict:
    """Replay trades through exit manager and collect stats."""
    from src.meme_exit_manager import PositionState

    total_pnl = 0.0
    wins = 0
    losses = 0
    exit_reasons = defaultdict(int)
    tp_reached = defaultdict(int)

    for trade in trades:
        pos = PositionState(
            mint=trade.mint,
            symbol=trade.symbol,
            entry_price=trade.entry_price,
            entry_time=time.time() - trade.hold_time_sec,
            amount_tokens=trade.amount_usd / trade.entry_price if trade.entry_price > 0 else 0,
            amount_usd=trade.amount_usd,
            score=trade.score,
        )

        # Step through price path
        trade_pnl = 0.0
        remaining_fraction = 1.0

        for price in trade.price_path:
            if remaining_fraction <= 0:
                break

            result = manager.check_exit(pos, price)
            if result.should_exit:
                # Calculate P&L for this exit chunk
                pnl_pct = (price - trade.entry_price) / trade.entry_price
                chunk_pnl = pnl_pct * trade.amount_usd * result.sell_fraction * remaining_fraction
                trade_pnl += chunk_pnl
                remaining_fraction -= result.sell_fraction * remaining_fraction

                exit_reasons[result.reason] += 1

                # Track TP progression
                if result.reason.startswith('TP'):
                    tp_name = result.reason.split('_')[0]
                    tp_reached[tp_name] += 1

                if remaining_fraction <= 0.01:
                    break

        # If position still open at end of path, use final price
        if remaining_fraction > 0.01:
            final_pnl_pct = (trade.price_path[-1] - trade.entry_price) / trade.entry_price
            trade_pnl += final_pnl_pct * trade.amount_usd * remaining_fraction

        total_pnl += trade_pnl
        if trade_pnl > 0:
            wins += 1
        else:
            losses += 1

    total = wins + losses
    return {
        'total_trades': total,
        'wins': wins,
        'losses': losses,
        'win_rate': (wins / total * 100) if total > 0 else 0,
        'total_pnl': round(total_pnl, 2),
        'avg_pnl': round(total_pnl / total, 2) if total > 0 else 0,
        'exit_reasons': dict(exit_reasons),
        'tp_reached': dict(tp_reached),
    }


def main():
    parser = argparse.ArgumentParser(description='Backtest exit parameters')
    parser.add_argument('--days', type=int, default=None, help='Limit to last N days of trades')
    args = parser.parse_args()

    print("Loading historical trades...")
    trades = load_historical_trades(days=args.days)
    if not trades:
        print("No historical trades found. Run the bot first to generate trade data.")
        return

    print(f"Loaded {len(trades)} trades for backtesting")

    # Generate parameter combinations
    combos = list(itertools.product(
        TP_VARIANTS.items(),
        STOP_LOSS_VALUES,
        TRAILING_ACTIVATION_VALUES,
        QUICK_SCALP_GAIN_VALUES,
    ))
    total_combos = len(combos)
    print(f"Running {total_combos} parameter combinations...")

    # Prepare output
    output_dir = os.path.join(PROJECT_ROOT, 'src', 'data', 'strategy_tuning')
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, 'backtest_results.csv')

    results = []
    for i, ((tp_name, tp_tiers), sl, trail, scalp) in enumerate(combos):
        if (i + 1) % 20 == 0:
            print(f"  Progress: {i + 1}/{total_combos}")

        stats = run_backtest(trades, tp_tiers, sl, trail, scalp)
        row = {
            'tp_variant': tp_name,
            'initial_stop_loss': sl,
            'trailing_activation': trail,
            'quick_scalp_gain': scalp,
            'total_trades': stats['total_trades'],
            'wins': stats['wins'],
            'losses': stats['losses'],
            'win_rate': stats['win_rate'],
            'total_pnl': stats['total_pnl'],
            'avg_pnl': stats['avg_pnl'],
        }
        results.append(row)

    # Sort by total P&L descending
    results.sort(key=lambda x: x['total_pnl'], reverse=True)

    # Write CSV
    if results:
        with open(output_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=results[0].keys())
            writer.writeheader()
            writer.writerows(results)
        print(f"\nResults written to {output_path}")

    # Print top 10
    print("\n" + "=" * 80)
    print("TOP 10 PARAMETER COMBINATIONS (by total P&L)")
    print("=" * 80)
    print(f"{'#':>3} {'TP Variant':<15} {'SL':>6} {'Trail':>6} {'Scalp':>6} "
          f"{'Win%':>7} {'Total P&L':>11} {'Avg P&L':>9}")
    print("-" * 80)

    for i, r in enumerate(results[:10]):
        print(f"{i+1:>3} {r['tp_variant']:<15} {r['initial_stop_loss']:>6.2f} "
              f"{r['trailing_activation']:>6.2f} {r['quick_scalp_gain']:>6.2f} "
              f"{r['win_rate']:>6.1f}% ${r['total_pnl']:>10.2f} ${r['avg_pnl']:>8.2f}")

    # Print bottom 3 for comparison
    print("\nBOTTOM 3:")
    for r in results[-3:]:
        print(f"    {r['tp_variant']:<15} {r['initial_stop_loss']:>6.2f} "
              f"{r['trailing_activation']:>6.2f} {r['quick_scalp_gain']:>6.2f} "
              f"{r['win_rate']:>6.1f}% ${r['total_pnl']:>10.2f}")


if __name__ == '__main__':
    main()
