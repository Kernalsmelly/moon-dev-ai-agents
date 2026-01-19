#!/usr/bin/env python3
"""Shadow Trade Analyzer: Calculate hypothetical P&L from shadow mode trading.

This standalone module reads shadow trade telemetry and produces actionable reports:
- Total P&L (hypothetical)
- Win rate and average gain/loss
- Best and worst trades
- Daily/weekly summaries

Usage:
    python src/scripts/shadow_analyzer.py                    # Analyze all shadow trades
    python src/scripts/shadow_analyzer.py --days 7           # Last 7 days only
    python src/scripts/shadow_analyzer.py --export report.csv # Export to CSV

The analyzer reads from:
    - data/shadow_trades.csv (primary - complete trade records)
    - data/execution_events.csv (fallback - exit events)
    - data/alpha_journal.csv (fallback - entry signals)
"""
import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Data paths
DATA_DIR = os.path.join(PROJECT_ROOT, 'data')
SHADOW_TRADES_CSV = os.path.join(DATA_DIR, 'shadow_trades.csv')
EXECUTION_EVENTS_CSV = os.path.join(DATA_DIR, 'execution_events.csv')
ALPHA_JOURNAL_CSV = os.path.join(DATA_DIR, 'alpha_journal.csv')


@dataclass
class ShadowTrade:
    """Represents a complete shadow trade (entry + exit)."""
    ts_entry: datetime
    ts_exit: Optional[datetime]
    mint: str
    symbol: str
    side: str  # 'BUY' or 'SELL'
    entry_price_usd: float
    exit_price_usd: Optional[float]
    amount_tokens: float
    amount_usd: float
    exit_amount_usd: Optional[float]
    pnl_usd: Optional[float]
    pnl_pct: Optional[float]
    exit_reason: Optional[str]  # 'TP1', 'TP2', 'SL', 'manual', None
    status: str  # 'open', 'closed', 'simulated'
    fees_usd: float = 0.0
    slippage_pct: float = 0.0
    metadata: dict = field(default_factory=dict)

    @property
    def is_winner(self) -> bool:
        return self.pnl_usd is not None and self.pnl_usd > 0

    @property
    def is_closed(self) -> bool:
        return self.status == 'closed' and self.exit_price_usd is not None


@dataclass
class AnalysisReport:
    """Summary report of shadow trading performance."""
    period_start: datetime
    period_end: datetime
    total_trades: int
    closed_trades: int
    open_trades: int
    winners: int
    losers: int
    win_rate: float
    total_pnl_usd: float
    avg_pnl_usd: float
    avg_winner_usd: float
    avg_loser_usd: float
    best_trade_pnl: float
    worst_trade_pnl: float
    best_trade_symbol: str
    worst_trade_symbol: str
    total_fees_usd: float
    net_pnl_usd: float
    profit_factor: float  # gross_profit / gross_loss
    avg_hold_time_hours: float
    trades_by_day: dict


def parse_timestamp(ts_str: str) -> Optional[datetime]:
    """Parse various timestamp formats."""
    if not ts_str:
        return None

    formats = [
        '%Y-%m-%dT%H:%M:%S.%f%z',
        '%Y-%m-%dT%H:%M:%S%z',
        '%Y-%m-%dT%H:%M:%S.%fZ',
        '%Y-%m-%dT%H:%M:%S.%f',
        '%Y-%m-%dT%H:%M:%SZ',
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%d %H:%M:%S',
    ]

    for fmt in formats:
        try:
            return datetime.strptime(ts_str.replace('+00:00', 'Z').replace('Z', '+0000'), fmt.replace('Z', '%z'))
        except ValueError:
            continue

    # Last resort: try fromisoformat
    try:
        return datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
    except Exception:
        return None


def load_shadow_trades(filepath: str = SHADOW_TRADES_CSV) -> list[ShadowTrade]:
    """Load shadow trades from the primary CSV."""
    trades = []

    if not os.path.exists(filepath):
        return trades

    with open(filepath, 'r', newline='') as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            try:
                ts_entry = parse_timestamp(row.get('ts_entry', ''))
                ts_exit = parse_timestamp(row.get('ts_exit', ''))

                trade = ShadowTrade(
                    ts_entry=ts_entry or datetime.now(),
                    ts_exit=ts_exit,
                    mint=row.get('mint', ''),
                    symbol=row.get('symbol', row.get('mint', '')[:8]),
                    side=row.get('side', 'BUY'),
                    entry_price_usd=float(row.get('entry_price_usd', 0) or 0),
                    exit_price_usd=float(row.get('exit_price_usd', 0) or 0) if row.get('exit_price_usd') else None,
                    amount_tokens=float(row.get('amount_tokens', 0) or 0),
                    amount_usd=float(row.get('amount_usd', 0) or 0),
                    exit_amount_usd=float(row.get('exit_amount_usd', 0) or 0) if row.get('exit_amount_usd') else None,
                    pnl_usd=float(row.get('pnl_usd', 0) or 0) if row.get('pnl_usd') else None,
                    pnl_pct=float(row.get('pnl_pct', 0) or 0) if row.get('pnl_pct') else None,
                    exit_reason=row.get('exit_reason') or None,
                    status=row.get('status', 'closed'),
                    fees_usd=float(row.get('fees_usd', 0) or 0),
                    slippage_pct=float(row.get('slippage_pct', 0) or 0),
                    metadata=json.loads(row.get('metadata', '{}') or '{}'),
                )
                trades.append(trade)
            except Exception as e:
                print(f"Warning: Failed to parse trade row: {e}")
                continue

    return trades


def load_trades_from_execution_events(filepath: str = EXECUTION_EVENTS_CSV) -> list[ShadowTrade]:
    """Attempt to reconstruct trades from execution_events.csv (limited data)."""
    trades = []

    if not os.path.exists(filepath):
        return trades

    # Group events by mint to reconstruct trades
    events_by_mint: dict[str, list] = {}

    with open(filepath, 'r', newline='') as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            event_type = row.get('event_type', '')
            # Only process trade-related events
            if event_type not in ('exit_simulated', 'jito_bundle_simulated', 'jito_bundle_submitted',
                                   'chunk_executed', 'chunking_completed'):
                continue

            mint = row.get('mint', '')
            if not mint:
                continue

            events_by_mint.setdefault(mint, []).append({
                'ts': parse_timestamp(row.get('ts', '')),
                'event_type': event_type,
                'data': json.loads(row.get('data_json', '{}') or '{}'),
            })

    # Convert grouped events to trade records
    for mint, events in events_by_mint.items():
        if not events:
            continue

        # Sort by timestamp
        events.sort(key=lambda x: x['ts'] or datetime.min)

        # Use first event as entry proxy, last as exit
        first = events[0]
        last = events[-1]

        data = last.get('data', {})
        symbol = data.get('symbol', mint[:8])

        # Estimate P&L from available data
        impact_pct = data.get('estimated_impact_pct') or data.get('impact_pct') or 0

        trade = ShadowTrade(
            ts_entry=first['ts'] or datetime.now(),
            ts_exit=last['ts'],
            mint=mint,
            symbol=symbol,
            side='SELL',  # execution_events are mostly exits
            entry_price_usd=0,  # Not available in current format
            exit_price_usd=0,   # Not available
            amount_tokens=0,
            amount_usd=0,
            exit_amount_usd=0,
            pnl_usd=None,  # Can't calculate without prices
            pnl_pct=None,
            exit_reason=None,
            status='simulated',
            slippage_pct=float(impact_pct) if impact_pct else 0,
            metadata={'source': 'execution_events', 'events': len(events)},
        )
        trades.append(trade)

    return trades


def analyze_trades(trades: list[ShadowTrade], days_back: Optional[int] = None) -> AnalysisReport:
    """Generate analysis report from trade list."""
    now = datetime.now()

    # Filter by date if specified
    if days_back is not None:
        cutoff = now - timedelta(days=days_back)
        # Handle timezone-aware vs naive datetime comparison
        def ts_after_cutoff(ts):
            if ts is None:
                return False
            # Remove timezone info for comparison if present
            ts_naive = ts.replace(tzinfo=None) if ts.tzinfo else ts
            return ts_naive >= cutoff
        trades = [t for t in trades if ts_after_cutoff(t.ts_entry)]

    if not trades:
        return AnalysisReport(
            period_start=now,
            period_end=now,
            total_trades=0,
            closed_trades=0,
            open_trades=0,
            winners=0,
            losers=0,
            win_rate=0.0,
            total_pnl_usd=0.0,
            avg_pnl_usd=0.0,
            avg_winner_usd=0.0,
            avg_loser_usd=0.0,
            best_trade_pnl=0.0,
            worst_trade_pnl=0.0,
            best_trade_symbol='N/A',
            worst_trade_symbol='N/A',
            total_fees_usd=0.0,
            net_pnl_usd=0.0,
            profit_factor=0.0,
            avg_hold_time_hours=0.0,
            trades_by_day={},
        )

    # Calculate metrics
    closed = [t for t in trades if t.is_closed]
    open_trades = [t for t in trades if not t.is_closed]
    winners = [t for t in closed if t.is_winner]
    losers = [t for t in closed if t.pnl_usd is not None and t.pnl_usd <= 0]

    total_pnl = sum(t.pnl_usd or 0 for t in closed)
    total_fees = sum(t.fees_usd for t in trades)

    gross_profit = sum(t.pnl_usd for t in winners if t.pnl_usd)
    gross_loss = abs(sum(t.pnl_usd for t in losers if t.pnl_usd))

    # Find best/worst
    best_trade = max(closed, key=lambda t: t.pnl_usd or float('-inf')) if closed else None
    worst_trade = min(closed, key=lambda t: t.pnl_usd or float('inf')) if closed else None

    # Calculate hold times
    hold_times = []
    for t in closed:
        if t.ts_entry and t.ts_exit:
            delta = t.ts_exit - t.ts_entry
            hold_times.append(delta.total_seconds() / 3600)

    # Group by day
    trades_by_day: dict[str, dict] = {}
    for t in trades:
        if t.ts_entry:
            day = t.ts_entry.strftime('%Y-%m-%d')
            if day not in trades_by_day:
                trades_by_day[day] = {'count': 0, 'pnl': 0.0, 'winners': 0, 'losers': 0}
            trades_by_day[day]['count'] += 1
            if t.pnl_usd is not None:
                trades_by_day[day]['pnl'] += t.pnl_usd
                if t.pnl_usd > 0:
                    trades_by_day[day]['winners'] += 1
                else:
                    trades_by_day[day]['losers'] += 1

    # Determine period (normalize to naive datetimes for comparison)
    def to_naive(ts):
        return ts.replace(tzinfo=None) if ts and ts.tzinfo else ts
    timestamps = [to_naive(t.ts_entry) for t in trades if t.ts_entry]
    period_start = min(timestamps) if timestamps else now
    period_end = max(timestamps) if timestamps else now

    return AnalysisReport(
        period_start=period_start,
        period_end=period_end,
        total_trades=len(trades),
        closed_trades=len(closed),
        open_trades=len(open_trades),
        winners=len(winners),
        losers=len(losers),
        win_rate=(len(winners) / len(closed) * 100) if closed else 0.0,
        total_pnl_usd=total_pnl,
        avg_pnl_usd=(total_pnl / len(closed)) if closed else 0.0,
        avg_winner_usd=(gross_profit / len(winners)) if winners else 0.0,
        avg_loser_usd=(-gross_loss / len(losers)) if losers else 0.0,
        best_trade_pnl=best_trade.pnl_usd if best_trade and best_trade.pnl_usd else 0.0,
        worst_trade_pnl=worst_trade.pnl_usd if worst_trade and worst_trade.pnl_usd else 0.0,
        best_trade_symbol=best_trade.symbol if best_trade else 'N/A',
        worst_trade_symbol=worst_trade.symbol if worst_trade else 'N/A',
        total_fees_usd=total_fees,
        net_pnl_usd=total_pnl - total_fees,
        profit_factor=(gross_profit / gross_loss) if gross_loss > 0 else float('inf') if gross_profit > 0 else 0.0,
        avg_hold_time_hours=(sum(hold_times) / len(hold_times)) if hold_times else 0.0,
        trades_by_day=trades_by_day,
    )


def format_report(report: AnalysisReport, trades: list[ShadowTrade]) -> str:
    """Format the analysis report for console output."""
    lines = []
    lines.append('')
    lines.append('=' * 60)
    lines.append('       SHADOW TRADING PERFORMANCE REPORT')
    lines.append('=' * 60)
    lines.append('')

    # Period
    lines.append(f"Period: {report.period_start.strftime('%Y-%m-%d')} to {report.period_end.strftime('%Y-%m-%d')}")
    lines.append('')

    # Summary
    lines.append('--- SUMMARY ---')
    lines.append(f"Total Trades:    {report.total_trades}")
    lines.append(f"Closed Trades:   {report.closed_trades}")
    lines.append(f"Open Positions:  {report.open_trades}")
    lines.append('')

    # P&L
    lines.append('--- PROFIT & LOSS ---')
    pnl_color = '+' if report.total_pnl_usd >= 0 else ''
    lines.append(f"Total P&L:       {pnl_color}${report.total_pnl_usd:,.2f}")
    lines.append(f"Total Fees:      -${report.total_fees_usd:,.2f}")
    net_color = '+' if report.net_pnl_usd >= 0 else ''
    lines.append(f"Net P&L:         {net_color}${report.net_pnl_usd:,.2f}")
    lines.append('')

    # Win/Loss
    lines.append('--- WIN/LOSS ---')
    lines.append(f"Winners:         {report.winners}")
    lines.append(f"Losers:          {report.losers}")
    lines.append(f"Win Rate:        {report.win_rate:.1f}%")
    lines.append(f"Profit Factor:   {report.profit_factor:.2f}" if report.profit_factor != float('inf') else "Profit Factor:   ∞ (no losses)")
    lines.append('')

    # Averages
    lines.append('--- AVERAGES ---')
    lines.append(f"Avg P&L:         ${report.avg_pnl_usd:,.2f}")
    lines.append(f"Avg Winner:      +${report.avg_winner_usd:,.2f}")
    lines.append(f"Avg Loser:       ${report.avg_loser_usd:,.2f}")
    lines.append(f"Avg Hold Time:   {report.avg_hold_time_hours:.1f} hours")
    lines.append('')

    # Best/Worst
    lines.append('--- NOTABLE TRADES ---')
    lines.append(f"Best Trade:      +${report.best_trade_pnl:,.2f} ({report.best_trade_symbol})")
    lines.append(f"Worst Trade:     ${report.worst_trade_pnl:,.2f} ({report.worst_trade_symbol})")
    lines.append('')

    # Daily breakdown
    if report.trades_by_day:
        lines.append('--- DAILY BREAKDOWN ---')
        for day in sorted(report.trades_by_day.keys(), reverse=True)[:7]:
            stats = report.trades_by_day[day]
            day_pnl = stats['pnl']
            pnl_str = f"+${day_pnl:,.2f}" if day_pnl >= 0 else f"${day_pnl:,.2f}"
            lines.append(f"{day}: {stats['count']} trades, {pnl_str} ({stats['winners']}W/{stats['losers']}L)")
        lines.append('')

    # Verdict
    lines.append('=' * 60)
    if report.closed_trades == 0:
        lines.append('  VERDICT: No closed trades to analyze')
        lines.append('  Run shadow mode to generate trade data')
    elif report.total_pnl_usd > 0 and report.win_rate >= 50:
        lines.append('  VERDICT: PROFITABLE - Consider going live')
        lines.append(f'  Strategy shows +${report.total_pnl_usd:,.2f} with {report.win_rate:.0f}% win rate')
    elif report.total_pnl_usd > 0:
        lines.append('  VERDICT: PROFITABLE but LOW WIN RATE')
        lines.append('  Winners are large but infrequent - high variance')
    elif report.win_rate >= 50:
        lines.append('  VERDICT: UNPROFITABLE despite good win rate')
        lines.append('  Losers are too large - review risk management')
    else:
        lines.append('  VERDICT: NEEDS WORK - Keep tuning')
        lines.append(f'  Strategy shows ${report.total_pnl_usd:,.2f} with {report.win_rate:.0f}% win rate')
    lines.append('=' * 60)
    lines.append('')

    return '\n'.join(lines)


def export_report_csv(report: AnalysisReport, trades: list[ShadowTrade], filepath: str):
    """Export detailed trade list and summary to CSV."""
    # Write trades
    trades_path = filepath.replace('.csv', '_trades.csv')
    with open(trades_path, 'w', newline='') as fh:
        writer = csv.writer(fh)
        writer.writerow([
            'ts_entry', 'ts_exit', 'mint', 'symbol', 'side',
            'entry_price_usd', 'exit_price_usd', 'amount_usd',
            'pnl_usd', 'pnl_pct', 'exit_reason', 'status', 'fees_usd'
        ])
        for t in trades:
            writer.writerow([
                t.ts_entry.isoformat() if t.ts_entry else '',
                t.ts_exit.isoformat() if t.ts_exit else '',
                t.mint, t.symbol, t.side,
                t.entry_price_usd, t.exit_price_usd, t.amount_usd,
                t.pnl_usd, t.pnl_pct, t.exit_reason, t.status, t.fees_usd
            ])

    # Write summary
    summary_path = filepath.replace('.csv', '_summary.csv')
    with open(summary_path, 'w', newline='') as fh:
        writer = csv.writer(fh)
        writer.writerow(['metric', 'value'])
        writer.writerow(['period_start', report.period_start.isoformat()])
        writer.writerow(['period_end', report.period_end.isoformat()])
        writer.writerow(['total_trades', report.total_trades])
        writer.writerow(['closed_trades', report.closed_trades])
        writer.writerow(['winners', report.winners])
        writer.writerow(['losers', report.losers])
        writer.writerow(['win_rate_pct', f"{report.win_rate:.2f}"])
        writer.writerow(['total_pnl_usd', f"{report.total_pnl_usd:.2f}"])
        writer.writerow(['net_pnl_usd', f"{report.net_pnl_usd:.2f}"])
        writer.writerow(['profit_factor', f"{report.profit_factor:.2f}"])
        writer.writerow(['avg_hold_time_hours', f"{report.avg_hold_time_hours:.2f}"])

    print(f"Exported: {trades_path}")
    print(f"Exported: {summary_path}")


def main():
    parser = argparse.ArgumentParser(description='Analyze shadow trading performance')
    parser.add_argument('--days', type=int, help='Analyze only last N days')
    parser.add_argument('--export', type=str, help='Export report to CSV file')
    parser.add_argument('--source', type=str, choices=['shadow', 'events', 'auto'], default='auto',
                        help='Data source: shadow_trades.csv, execution_events.csv, or auto-detect')
    args = parser.parse_args()

    # Load trades
    trades = []

    if args.source in ('shadow', 'auto'):
        trades = load_shadow_trades()
        if trades:
            print(f"Loaded {len(trades)} trades from shadow_trades.csv")

    if not trades and args.source in ('events', 'auto'):
        trades = load_trades_from_execution_events()
        if trades:
            print(f"Loaded {len(trades)} trade events from execution_events.csv")
            print("Note: Limited data available - P&L may be incomplete")

    if not trades:
        print('')
        print('No shadow trades found.')
        print('')
        print('To generate shadow trade data:')
        print('  1. Ensure SHADOW_MODE=true in config or environment')
        print('  2. Run the orchestrator: python src/orchestrator.py')
        print('  3. The system will log trades to data/shadow_trades.csv')
        print('')
        print('Or create sample data with: python src/scripts/shadow_analyzer.py --generate-sample')
        return

    # Analyze
    report = analyze_trades(trades, days_back=args.days)

    # Output
    print(format_report(report, trades))

    if args.export:
        export_report_csv(report, trades, args.export)


if __name__ == '__main__':
    main()
