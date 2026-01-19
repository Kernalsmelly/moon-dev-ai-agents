#!/usr/bin/env python3
"""Position Status: Quick view of current positions and trade history.

Usage:
    python src/scripts/position_status.py                # Show all positions
    python src/scripts/position_status.py --trades       # Show recent trades
    python src/scripts/position_status.py --performance  # Show performance summary
    python src/scripts/position_status.py --all          # Show everything
"""
import argparse
import os
import sys

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.position_store import PositionStore


def format_usd(amount: float) -> str:
    """Format USD amount with color hint."""
    if amount > 0:
        return f"+${amount:,.2f}"
    elif amount < 0:
        return f"${amount:,.2f}"
    else:
        return "$0.00"


def show_positions(store: PositionStore):
    """Display current positions."""
    positions = store.get_all_positions()

    print('')
    print('=' * 60)
    print('                  CURRENT POSITIONS')
    print('=' * 60)

    if not positions:
        print('')
        print('  No positions found.')
        print('')
        return

    open_positions = {k: v for k, v in positions.items() if v.get('status') == 'open'}
    closed_positions = {k: v for k, v in positions.items() if v.get('status') != 'open'}

    if open_positions:
        print('')
        print('--- OPEN POSITIONS ---')
        for mint, pos in open_positions.items():
            symbol = pos.get('symbol', mint[:8])
            entry = pos.get('entry_price', 0)
            amount = pos.get('amount_usd', 0)
            de_risked = 'Yes' if pos.get('de_risked') else 'No'
            print(f"  {symbol:<10} Entry: ${entry:.4f}  Size: ${amount:.2f}  De-risked: {de_risked}")
            print(f"             Mint: {mint[:20]}...")

    if closed_positions:
        print('')
        print(f'--- CLOSED POSITIONS ({len(closed_positions)}) ---')
        print('  (Use --trades to see trade history)')

    print('')


def show_trades(store: PositionStore, limit: int = 20):
    """Display recent trades."""
    trades = store.get_trades(limit=limit)

    print('')
    print('=' * 60)
    print('                   RECENT TRADES')
    print('=' * 60)

    if not trades:
        print('')
        print('  No trades recorded yet.')
        print('  Run in shadow mode to generate trade history.')
        print('')
        return

    print('')
    for trade in trades[:limit]:
        symbol = trade.get('symbol', '???')
        pnl = trade.get('pnl_usd', 0)
        pnl_pct = trade.get('pnl_pct', 0)
        reason = trade.get('exit_reason', 'unknown')
        ts = trade.get('exit_timestamp', '')[:10]

        result = 'WIN ' if pnl > 0 else 'LOSS'
        pnl_str = format_usd(pnl)

        print(f"  {ts}  {symbol:<8}  {result}  {pnl_str:<12}  ({pnl_pct:+.1f}%)  {reason}")

    print('')


def show_performance(store: PositionStore, days: int = None):
    """Display performance summary."""
    perf = store.get_performance_summary(days=days)

    period = f"Last {days} days" if days else "All time"

    print('')
    print('=' * 60)
    print(f'              PERFORMANCE SUMMARY ({period})')
    print('=' * 60)
    print('')

    if perf['total_trades'] == 0:
        print('  No completed trades to analyze.')
        print('  Run in shadow mode to generate data.')
        print('')
        return

    print(f"  Total Trades:    {perf['total_trades']}")
    print(f"  Winners:         {perf['winners']}")
    print(f"  Losers:          {perf['losers']}")
    print(f"  Win Rate:        {perf['win_rate']:.1f}%")
    print('')
    print(f"  Total P&L:       {format_usd(perf['total_pnl'])}")
    print(f"  Total Fees:      -${perf['total_fees']:.2f}")
    print(f"  Net P&L:         {format_usd(perf['net_pnl'])}")
    print('')
    print(f"  Avg P&L:         {format_usd(perf['avg_pnl'])}")
    print(f"  Best Trade:      {format_usd(perf['best_trade'])}")
    print(f"  Worst Trade:     {format_usd(perf['worst_trade'])}")
    print('')

    pf = perf['profit_factor']
    if pf == float('inf'):
        print(f"  Profit Factor:   Infinite (no losses)")
    else:
        print(f"  Profit Factor:   {pf:.2f}")

    print('')

    # Verdict
    print('-' * 60)
    if perf['net_pnl'] > 0 and perf['win_rate'] >= 50:
        print('  VERDICT: Strategy is profitable')
    elif perf['net_pnl'] > 0:
        print('  VERDICT: Profitable but low win rate - high variance')
    elif perf['win_rate'] >= 50:
        print('  VERDICT: Good win rate but losing money - check sizing')
    else:
        print('  VERDICT: Needs work - both P&L and win rate are weak')
    print('-' * 60)
    print('')


def main():
    parser = argparse.ArgumentParser(description='View position status and trade history')
    parser.add_argument('--trades', action='store_true', help='Show recent trades')
    parser.add_argument('--performance', action='store_true', help='Show performance summary')
    parser.add_argument('--all', action='store_true', help='Show everything')
    parser.add_argument('--days', type=int, default=None, help='Filter to last N days')
    parser.add_argument('--limit', type=int, default=20, help='Limit number of trades shown')
    args = parser.parse_args()

    store = PositionStore()

    if args.all:
        show_positions(store)
        show_trades(store, limit=args.limit)
        show_performance(store, days=args.days)
    elif args.trades:
        show_trades(store, limit=args.limit)
    elif args.performance:
        show_performance(store, days=args.days)
    else:
        show_positions(store)
        show_performance(store, days=args.days)


if __name__ == '__main__':
    main()
