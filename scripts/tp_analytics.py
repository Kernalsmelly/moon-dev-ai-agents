#!/usr/bin/env python3
"""TP Analytics Script: Analyze take profit funnel and tier performance.

Reads trades from position store, aggregates TP tier stats, and prints:
- TP funnel: entries -> TP0 -> TP1 -> TP2 -> TP3 -> TP4 -> moon bag
- Average gain at each tier
- "Stopped out before TP0" count
- TP stage progression analysis

Usage:
    python scripts/tp_analytics.py
    python scripts/tp_analytics.py --days 7
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def load_trades(days: int | None = None) -> list[dict]:
    """Load trades from the position store."""
    try:
        from src.position_store import PositionStore
        store = PositionStore()
        return store.get_trades(days=days, limit=10000)
    except Exception as e:
        print(f"[WARN] Could not load trades: {e}")
        return []


def _safe_json(val) -> dict:
    if isinstance(val, dict):
        return val
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return {}


def analyze_tp_funnel(trades: list[dict]):
    """Print the TP funnel: what fraction of entries reached each tier."""
    total = len(trades)
    if total == 0:
        print("No trades to analyze.")
        return

    print("\n" + "=" * 70)
    print("TAKE PROFIT FUNNEL ANALYSIS")
    print("=" * 70)
    print(f"Total entries: {total}\n")

    tiers = ['TP0', 'TP1', 'TP2', 'TP3', 'TP4']

    # Track which TPs each trade reached
    tier_counts = defaultdict(int)
    tier_pnl_pcts = defaultdict(list)
    stopped_before_tp0 = 0
    moon_bags = 0

    for t in trades:
        reason = t.get('exit_reason', '')
        meta = _safe_json(t.get('metadata', {}))
        pnl_pct = float(t.get('pnl_pct', 0))

        # Check tp_stages from metadata (new format)
        stages = meta.get('tp_stages', [])
        if isinstance(stages, list) and stages:
            for stage in stages:
                tier = stage.get('tier', '')
                if tier in tiers:
                    tier_counts[tier] += 1
                    tier_pnl_pcts[tier].append(stage.get('pnl_pct', pnl_pct))
        else:
            # Fall back to tp_hit flags
            for tp_key in ['tp0_hit', 'tp1_hit', 'tp2_hit', 'tp3_hit', 'tp4_hit']:
                if meta.get(tp_key):
                    tier = tp_key.replace('_hit', '').upper()
                    tier_counts[tier] += 1
                    tier_pnl_pcts[tier].append(pnl_pct)

            # Count from exit_reason if no metadata
            if not any(meta.get(f'tp{i}_hit') for i in range(5)):
                if reason.startswith('TP'):
                    tp_name = reason.split('_')[0]
                    if tp_name in tiers:
                        tier_counts[tp_name] += 1
                        tier_pnl_pcts[tp_name].append(pnl_pct)

        # Stopped before TP0
        is_stop = any(kw in reason for kw in ['STOP', 'GAP', 'STAGNATION', 'MAX_HOLD', 'MAX_LOSS', 'DUMP'])
        if is_stop and not meta.get('tp0_hit') and not any(s.get('tier') == 'TP0' for s in (stages if isinstance(stages, list) else [])):
            stopped_before_tp0 += 1

        # Moon bags
        if 'MOON_BAG' in reason or meta.get('tp_summary', {}).get('is_moon_bag'):
            moon_bags += 1

    # Print funnel
    print("TP FUNNEL:")
    print(f"  {'Entries':<12} {total:>6} (100.0%)")
    print(f"  {'Before TP0':<12} {stopped_before_tp0:>6} ({stopped_before_tp0/total*100:>5.1f}%) - stopped out early")
    print()

    for tier in tiers:
        count = tier_counts.get(tier, 0)
        pct = (count / total * 100) if total > 0 else 0
        avg_gain = sum(tier_pnl_pcts.get(tier, [0])) / max(1, len(tier_pnl_pcts.get(tier, [0])))
        bar = '█' * int(pct / 2)
        print(f"  {tier:<12} {count:>6} ({pct:>5.1f}%)  avg gain: {avg_gain:>+6.1f}%  {bar}")

    print(f"\n  {'Moon bags':<12} {moon_bags:>6}")


def analyze_tp_stage_progression(trades: list[dict]):
    """Analyze how positions progress through TP stages."""
    print("\n" + "=" * 70)
    print("TP STAGE PROGRESSION")
    print("=" * 70)

    # Count max stages hit per trade
    stages_distribution = defaultdict(int)
    stage_timings = defaultdict(list)  # tier -> list of seconds from entry to hit

    for t in trades:
        meta = _safe_json(t.get('metadata', {}))
        stages = meta.get('tp_stages', [])
        summary = meta.get('tp_summary', {})

        if isinstance(summary, dict) and 'stages_hit' in summary:
            stages_hit = summary['stages_hit']
            stages_distribution[stages_hit] += 1
        elif isinstance(stages, list):
            stages_distribution[len(stages)] += 1
        else:
            # Count from flags
            count = sum(1 for i in range(5) if meta.get(f'tp{i}_hit'))
            stages_distribution[count] += 1

        # Track timing between stages
        if isinstance(stages, list) and len(stages) >= 2:
            for i in range(1, len(stages)):
                prev_ts = stages[i-1].get('timestamp', 0)
                curr_ts = stages[i].get('timestamp', 0)
                if prev_ts and curr_ts:
                    delta = curr_ts - prev_ts
                    tier = stages[i].get('tier', f'TP{i}')
                    stage_timings[tier].append(delta)

    total = sum(stages_distribution.values())
    if total == 0:
        print("No stage data available yet.")
        return

    print(f"\nStages hit per trade:")
    for n in range(6):
        count = stages_distribution.get(n, 0)
        pct = (count / total * 100)
        bar = '#' * int(pct / 2)
        print(f"  {n} stages: {count:>4} ({pct:>5.1f}%) {bar}")

    if stage_timings:
        print(f"\nAvg time between TP tiers:")
        for tier in ['TP1', 'TP2', 'TP3', 'TP4']:
            times = stage_timings.get(tier, [])
            if times:
                avg_sec = sum(times) / len(times)
                print(f"  To {tier}: {avg_sec/60:>6.1f} min avg ({len(times)} samples)")


def analyze_exit_after_tp(trades: list[dict]):
    """Analyze how trades ultimately exit after hitting TP tiers."""
    print("\n" + "=" * 70)
    print("FINAL EXIT AFTER TP TIERS")
    print("=" * 70)

    # For trades that hit at least one TP, what was the final exit reason?
    tp_exits = defaultdict(lambda: defaultdict(int))

    for t in trades:
        meta = _safe_json(t.get('metadata', {}))
        reason = t.get('exit_reason', '')

        max_tp = None
        for i in range(4, -1, -1):
            if meta.get(f'tp{i}_hit'):
                max_tp = f'TP{i}'
                break

        if max_tp is None:
            stages = meta.get('tp_stages', [])
            if isinstance(stages, list) and stages:
                max_tp = stages[-1].get('tier')

        if max_tp:
            tp_exits[max_tp][reason] += 1

    if not tp_exits:
        print("No TP stage data available yet.")
        return

    for tp in ['TP0', 'TP1', 'TP2', 'TP3', 'TP4']:
        exits = tp_exits.get(tp)
        if not exits:
            continue
        total = sum(exits.values())
        print(f"\n  Trades that reached {tp} ({total} total):")
        for reason, count in sorted(exits.items(), key=lambda x: -x[1]):
            pct = (count / total * 100)
            print(f"    {reason:<30} {count:>4} ({pct:>5.1f}%)")


def main():
    parser = argparse.ArgumentParser(description='TP funnel analytics')
    parser.add_argument('--days', type=int, default=None, help='Limit to last N days')
    args = parser.parse_args()

    trades = load_trades(days=args.days)
    if not trades:
        print("No trades found. Run the bot first to generate trade data.")
        print("(TP analytics will populate as new trades flow through with TP stage tracking)")
        return

    # Filter test rows
    trades = [t for t in trades
              if 'fake' not in str(t.get('mint', '')).lower()
              and 'test' not in str(t.get('mint', '')).lower()]

    print(f"Analyzing {len(trades)} trades")

    analyze_tp_funnel(trades)
    analyze_tp_stage_progression(trades)
    analyze_exit_after_tp(trades)

    print("\n" + "=" * 70)
    print("TP ANALYTICS COMPLETE")
    print("=" * 70)


if __name__ == '__main__':
    main()
