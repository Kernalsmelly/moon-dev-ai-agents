#!/usr/bin/env python3
"""Trade Analysis Script: Analyze real trade performance from position store.

Computes:
- Win rate by exit_reason (TP0-TP4, SL, manual, STAGNATION) with count, avg P&L%, total P&L USD
- Hold time buckets (0-5min, 5-15min, 15-60min, 1-6h, 6h+) with win rate per bucket
- TP funnel: of all entries, what % reached each TP tier
- Fee/slippage drag on total P&L
- Score-to-outcome correlation (if entry_score in metadata)

Usage:
    python scripts/analyze_trades.py
    python scripts/analyze_trades.py --days 7
    python scripts/analyze_trades.py --csv data/shadow_trades.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def load_trades_from_db(days: int | None = None) -> list[dict]:
    """Load trades from the SQLite position store."""
    try:
        from src.position_store import PositionStore
        store = PositionStore()
        return store.get_trades(days=days, limit=10000)
    except Exception as e:
        print(f"[WARN] Could not load from positions.db: {e}")
        return []


def load_trades_from_csv(csv_path: str) -> list[dict]:
    """Load trades from a CSV file (shadow_trades.csv format)."""
    trades = []
    if not os.path.exists(csv_path):
        print(f"[WARN] CSV not found: {csv_path}")
        return trades

    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Skip synthetic/test rows
            if _is_test_row(row):
                continue
            trade = {
                'mint': row.get('mint', ''),
                'symbol': row.get('symbol', ''),
                'side': row.get('side', 'SELL'),
                'entry_price': _safe_float(row.get('entry_price', 0)),
                'exit_price': _safe_float(row.get('exit_price', 0)),
                'entry_timestamp': row.get('entry_timestamp', ''),
                'exit_timestamp': row.get('exit_timestamp', ''),
                'amount_usd': _safe_float(row.get('amount_usd', 0)),
                'pnl_usd': _safe_float(row.get('pnl_usd', 0)),
                'pnl_pct': _safe_float(row.get('pnl_pct', 0)),
                'exit_reason': row.get('exit_reason', 'unknown'),
                'fees_usd': _safe_float(row.get('fees_usd', 0)),
                'metadata': _safe_json(row.get('metadata', '{}')),
            }
            trades.append(trade)
    return trades


def _is_test_row(row: dict) -> bool:
    """Filter out test/synthetic rows."""
    mint = str(row.get('mint', '')).lower()
    symbol = str(row.get('symbol', '')).lower()
    metadata_str = str(row.get('metadata', ''))

    if 'fake' in mint or 'test' in mint:
        return True
    if 'fake' in symbol or 'test' in symbol:
        return True
    if 'shadow_mode' in metadata_str and '"shadow_mode": true' in metadata_str.lower():
        return True
    return False


def _safe_float(val) -> float:
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _safe_json(val) -> dict:
    if isinstance(val, dict):
        return val
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return {}


def analyze_by_exit_reason(trades: list[dict]):
    """Win rate by exit_reason with count, avg P&L%, total P&L USD."""
    print("\n" + "=" * 70)
    print("WIN RATE BY EXIT REASON")
    print("=" * 70)

    by_reason = defaultdict(list)
    for t in trades:
        reason = t.get('exit_reason', 'unknown')
        by_reason[reason].append(t)

    # Header
    print(f"{'Reason':<25} {'Count':>6} {'Wins':>5} {'Win%':>7} {'Avg P&L%':>9} {'Total P&L':>11}")
    print("-" * 70)

    total_pnl = 0
    total_count = 0
    for reason in sorted(by_reason.keys()):
        group = by_reason[reason]
        count = len(group)
        wins = sum(1 for t in group if t['pnl_usd'] > 0)
        win_rate = (wins / count * 100) if count > 0 else 0
        avg_pnl_pct = sum(t['pnl_pct'] for t in group) / count if count > 0 else 0
        total = sum(t['pnl_usd'] for t in group)
        total_pnl += total
        total_count += count
        print(f"{reason:<25} {count:>6} {wins:>5} {win_rate:>6.1f}% {avg_pnl_pct:>8.1f}% ${total:>10.2f}")

    print("-" * 70)
    overall_wins = sum(1 for t in trades if t['pnl_usd'] > 0)
    overall_wr = (overall_wins / total_count * 100) if total_count > 0 else 0
    print(f"{'TOTAL':<25} {total_count:>6} {overall_wins:>5} {overall_wr:>6.1f}%           ${total_pnl:>10.2f}")


def analyze_hold_time_buckets(trades: list[dict]):
    """Hold time distribution with win rate per bucket."""
    print("\n" + "=" * 70)
    print("HOLD TIME ANALYSIS")
    print("=" * 70)

    buckets = [
        ('0-5 min', 0, 300),
        ('5-15 min', 300, 900),
        ('15-60 min', 900, 3600),
        ('1-6 hours', 3600, 21600),
        ('6+ hours', 21600, float('inf')),
    ]

    bucket_trades = {name: [] for name, _, _ in buckets}

    for t in trades:
        hold_sec = _get_hold_time_sec(t)
        if hold_sec is None:
            continue
        for name, lo, hi in buckets:
            if lo <= hold_sec < hi:
                bucket_trades[name].append(t)
                break

    print(f"{'Bucket':<15} {'Count':>6} {'Wins':>5} {'Win%':>7} {'Avg P&L%':>9} {'Total P&L':>11}")
    print("-" * 60)

    for name, _, _ in buckets:
        group = bucket_trades[name]
        count = len(group)
        if count == 0:
            print(f"{name:<15} {0:>6}     -       -           -            -")
            continue
        wins = sum(1 for t in group if t['pnl_usd'] > 0)
        win_rate = (wins / count * 100) if count > 0 else 0
        avg_pnl = sum(t['pnl_pct'] for t in group) / count
        total = sum(t['pnl_usd'] for t in group)
        print(f"{name:<15} {count:>6} {wins:>5} {win_rate:>6.1f}% {avg_pnl:>8.1f}% ${total:>10.2f}")


def _get_hold_time_sec(trade: dict) -> float | None:
    """Extract hold time in seconds from trade metadata or timestamps."""
    meta = trade.get('metadata', {})
    if isinstance(meta, dict) and 'hold_time_sec' in meta:
        return float(meta['hold_time_sec'])

    entry_ts = trade.get('entry_timestamp', '')
    exit_ts = trade.get('exit_timestamp', '')
    if entry_ts and exit_ts:
        try:
            entry_dt = datetime.fromisoformat(entry_ts)
            exit_dt = datetime.fromisoformat(exit_ts)
            return (exit_dt - entry_dt).total_seconds()
        except (ValueError, TypeError):
            pass
    return None


def analyze_tp_funnel(trades: list[dict]):
    """TP funnel: of all entries, what % reached each TP tier."""
    print("\n" + "=" * 70)
    print("TAKE PROFIT FUNNEL")
    print("=" * 70)

    total_entries = len(trades)
    if total_entries == 0:
        print("No trades to analyze.")
        return

    # Count TP hits from exit_reason and metadata
    tp_counts = defaultdict(int)
    stopped_before_tp0 = 0

    for t in trades:
        reason = t.get('exit_reason', '')
        meta = t.get('metadata', {})

        # Check which TPs were hit from metadata
        if isinstance(meta, dict):
            for tp_key in ['tp0_hit', 'tp1_hit', 'tp2_hit', 'tp3_hit', 'tp4_hit']:
                if meta.get(tp_key):
                    tier = tp_key.replace('_hit', '').upper()
                    tp_counts[tier] += 1

            # Check tp_stages if available
            stages = meta.get('tp_stages', [])
            if isinstance(stages, list):
                for stage in stages:
                    tier = stage.get('tier', '')
                    if tier:
                        tp_counts[tier] += 1

        # Also count from exit_reason
        if reason.startswith('TP'):
            tp_name = reason.split('_')[0]  # TP0, TP1, etc.
            # Only count if not already counted from metadata
            if tp_name not in tp_counts or tp_counts[tp_name] == 0:
                tp_counts[tp_name] += 1

        # Stopped before TP0
        if 'STOP' in reason or 'GAP' in reason or 'STAGNATION' in reason:
            if not (isinstance(meta, dict) and meta.get('tp0_hit')):
                stopped_before_tp0 += 1

    print(f"Total entries: {total_entries}")
    print(f"Stopped before TP0: {stopped_before_tp0} ({stopped_before_tp0/total_entries*100:.1f}%)")
    print()

    tiers = ['TP0', 'TP1', 'TP2', 'TP3', 'TP4']
    remaining = total_entries
    for tier in tiers:
        count = tp_counts.get(tier, 0)
        pct = (count / total_entries * 100) if total_entries > 0 else 0
        bar = '#' * int(pct / 2)
        print(f"  {tier}: {count:>4} ({pct:>5.1f}%) {bar}")

    moon_bags = sum(1 for t in trades if t.get('exit_reason', '').startswith('TP4_MOON'))
    if moon_bags:
        print(f"  Moon bags: {moon_bags}")


def analyze_fee_drag(trades: list[dict]):
    """Fee/slippage impact on total P&L."""
    print("\n" + "=" * 70)
    print("FEE & SLIPPAGE ANALYSIS")
    print("=" * 70)

    total_pnl = sum(t['pnl_usd'] for t in trades)
    total_fees = sum(t.get('fees_usd', 0) for t in trades)
    total_volume = sum(t['amount_usd'] for t in trades)

    print(f"Gross P&L:     ${total_pnl + total_fees:>10.2f}")
    print(f"Total fees:    ${total_fees:>10.2f}")
    print(f"Net P&L:       ${total_pnl:>10.2f}")
    print(f"Total volume:  ${total_volume:>10.2f}")
    if total_volume > 0:
        fee_pct = (total_fees / total_volume) * 100
        print(f"Fee drag:      {fee_pct:.2f}% of volume")
    if total_pnl + total_fees != 0:
        fee_impact = (total_fees / abs(total_pnl + total_fees)) * 100
        print(f"Fee impact:    {fee_impact:.1f}% of gross P&L")


def analyze_score_correlation(trades: list[dict]):
    """Score-to-outcome correlation if entry_score in metadata."""
    print("\n" + "=" * 70)
    print("SCORE-TO-OUTCOME CORRELATION")
    print("=" * 70)

    scored_trades = []
    for t in trades:
        meta = t.get('metadata', {})
        if isinstance(meta, dict):
            score = meta.get('entry_score') or meta.get('score')
            if score is not None:
                scored_trades.append((int(score), t))

    if not scored_trades:
        print("No score data available in trade metadata.")
        return

    # Bucket by score ranges
    score_buckets = [
        ('40-50', 40, 50),
        ('50-60', 50, 60),
        ('60-70', 60, 70),
        ('70-80', 70, 80),
        ('80+', 80, 101),
    ]

    print(f"{'Score':<10} {'Count':>6} {'Win%':>7} {'Avg P&L%':>9} {'Total P&L':>11}")
    print("-" * 50)

    for name, lo, hi in score_buckets:
        group = [(s, t) for s, t in scored_trades if lo <= s < hi]
        count = len(group)
        if count == 0:
            continue
        wins = sum(1 for _, t in group if t['pnl_usd'] > 0)
        win_rate = (wins / count * 100)
        avg_pnl = sum(t['pnl_pct'] for _, t in group) / count
        total = sum(t['pnl_usd'] for _, t in group)
        print(f"{name:<10} {count:>6} {win_rate:>6.1f}% {avg_pnl:>8.1f}% ${total:>10.2f}")


def main():
    parser = argparse.ArgumentParser(description='Analyze trade performance')
    parser.add_argument('--days', type=int, default=None, help='Limit to last N days')
    parser.add_argument('--csv', type=str, default=None, help='Load from CSV file instead of DB')
    args = parser.parse_args()

    # Load trades
    trades = []
    csv_path = args.csv or os.path.join(PROJECT_ROOT, 'data', 'shadow_trades.csv')

    # Try DB first, then CSV
    db_trades = load_trades_from_db(days=args.days)
    if db_trades:
        trades.extend(db_trades)
        print(f"Loaded {len(db_trades)} trades from positions.db")

    if os.path.exists(csv_path):
        csv_trades = load_trades_from_csv(csv_path)
        if csv_trades:
            trades.extend(csv_trades)
            print(f"Loaded {len(csv_trades)} trades from {csv_path}")

    if not trades:
        print("No trades found. Run the bot first to generate trade data.")
        return

    # Filter test/synthetic if loading from DB (DB trades may also have test data)
    trades = [t for t in trades if not _is_test_row(t)]
    print(f"\nAnalyzing {len(trades)} real trades")

    # Run all analyses
    analyze_by_exit_reason(trades)
    analyze_hold_time_buckets(trades)
    analyze_tp_funnel(trades)
    analyze_fee_drag(trades)
    analyze_score_correlation(trades)

    print("\n" + "=" * 70)
    print("ANALYSIS COMPLETE")
    print("=" * 70)


if __name__ == '__main__':
    main()
