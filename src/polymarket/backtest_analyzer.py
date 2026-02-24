"""
Backtest Analyzer for Polymarket Micro-Edge Strategy

Analyzes historical signals against actual market resolutions to:
1. Calculate real PnL for past signals
2. Identify which signal types actually work
3. Find your best-performing categories
4. Validate the strategy before going live

Run: python -m src.polymarket.backtest_analyzer
"""

import os
import sys
import sqlite3
import httpx
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from collections import defaultdict

# Add project root to path
project_root = str(Path(__file__).parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from termcolor import cprint

from src.config.polymarket_micro_config import DATA_DIR

# Polymarket API
GAMMA_API = "https://gamma-api.polymarket.com"


class BacktestAnalyzer:
    """
    Analyze historical signals against real market outcomes.

    Fetches resolved market data from Polymarket and calculates
    what your PnL would have been.
    """

    def __init__(self):
        self.pnl_db = str(DATA_DIR / "simulated_pnl.db")
        self.markets_db = str(DATA_DIR / "niche_markets.db")
        self.client = httpx.Client(timeout=30.0)

        # Results
        self.resolved_trades = []
        self.unresolved_trades = []
        self.stats = {}

    def _get_pnl_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.pnl_db, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _get_markets_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.markets_db, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def fetch_market_resolution(self, condition_id: str) -> Optional[Dict]:
        """
        Fetch market resolution status from Polymarket API.

        Returns:
            {
                'resolved': bool,
                'outcome': 'YES' | 'NO' | None,
                'resolution_date': datetime | None,
                'final_price': float,  # Final YES price (1.0 for YES win, 0.0 for NO win)
            }
        """
        try:
            # Try the markets endpoint
            url = f"{GAMMA_API}/markets/{condition_id}"
            resp = self.client.get(url)

            if resp.status_code == 200:
                data = resp.json()

                # Check if resolved
                resolved = data.get('closed', False) or data.get('resolved', False)
                outcome_prices = data.get('outcomePrices', '[]')

                if isinstance(outcome_prices, str):
                    try:
                        outcome_prices = json.loads(outcome_prices)
                    except:
                        outcome_prices = []

                # Determine outcome from final prices
                # If YES price is 1.0 (or very close), YES won
                # If YES price is 0.0 (or very close), NO won
                yes_price = float(outcome_prices[0]) if outcome_prices else 0.5

                outcome = None
                if resolved:
                    if yes_price >= 0.95:
                        outcome = 'YES'
                    elif yes_price <= 0.05:
                        outcome = 'NO'

                end_date = data.get('endDate')
                resolution_date = None
                if end_date:
                    try:
                        resolution_date = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                    except:
                        pass

                return {
                    'resolved': resolved,
                    'outcome': outcome,
                    'resolution_date': resolution_date,
                    'final_yes_price': yes_price,
                    'question': data.get('question', ''),
                }

            elif resp.status_code == 404:
                # Market not found - might be old/removed
                return {'resolved': False, 'outcome': None, 'error': 'not_found'}

        except Exception as e:
            cprint(f"   Error fetching {condition_id[:20]}...: {e}", "yellow")

        return None

    def analyze_signals(self, limit: int = None, update_db: bool = True) -> Dict:
        """
        Analyze all historical signals against actual outcomes.

        Args:
            limit: Max signals to analyze (None = all)
            update_db: Whether to update pnl_tracker with results

        Returns:
            Summary statistics
        """
        cprint("\n" + "=" * 70, "cyan")
        cprint("BACKTEST ANALYZER - Validating Historical Signals", "cyan", attrs=['bold'])
        cprint("=" * 70, "cyan")

        # Get all unresolved signals
        conn = self._get_pnl_conn()
        cur = conn.execute('''
            SELECT * FROM simulated_trades
            WHERE resolved = 0
            ORDER BY created_at DESC
        ''')
        signals = [dict(row) for row in cur.fetchall()]

        if limit:
            signals = signals[:limit]

        cprint(f"\nAnalyzing {len(signals)} unresolved signals...\n", "white")

        # Track results
        results = {
            'total_checked': 0,
            'resolved': 0,
            'unresolved': 0,
            'wins': 0,
            'losses': 0,
            'total_pnl': 0.0,
            'by_signal_type': defaultdict(lambda: {'wins': 0, 'losses': 0, 'pnl': 0.0}),
            'by_category': defaultdict(lambda: {'wins': 0, 'losses': 0, 'pnl': 0.0}),
        }

        # Check each signal
        for i, signal in enumerate(signals):
            condition_id = signal['condition_id']
            results['total_checked'] += 1

            # Progress indicator
            if (i + 1) % 50 == 0:
                cprint(f"   Checked {i + 1}/{len(signals)}...", "white")

            # Fetch resolution
            resolution = self.fetch_market_resolution(condition_id)

            if not resolution or not resolution.get('resolved'):
                results['unresolved'] += 1
                self.unresolved_trades.append(signal)
                continue

            if not resolution.get('outcome'):
                # Resolved but outcome unclear
                results['unresolved'] += 1
                continue

            # Calculate PnL
            results['resolved'] += 1
            outcome = resolution['outcome']
            side = signal['side']
            entry_price = signal['entry_price']
            amount = signal['simulated_amount_usd']
            shares = signal['simulated_shares']

            was_correct = outcome == side

            if was_correct:
                # Won - collect full payout minus entry
                if side == 'YES':
                    pnl = shares * (1.0 - entry_price)
                else:
                    pnl = shares * (1.0 - (1 - entry_price))
                results['wins'] += 1
            else:
                # Lost - lose entry
                pnl = -amount
                results['losses'] += 1

            results['total_pnl'] += pnl

            # Track by signal type
            sig_type = signal.get('signal_type', 'unknown')
            results['by_signal_type'][sig_type]['pnl'] += pnl
            if was_correct:
                results['by_signal_type'][sig_type]['wins'] += 1
            else:
                results['by_signal_type'][sig_type]['losses'] += 1

            # Track by category (if available)
            category = signal.get('category') or self._get_category_from_question(signal.get('question', ''))
            if category:
                results['by_category'][category]['pnl'] += pnl
                if was_correct:
                    results['by_category'][category]['wins'] += 1
                else:
                    results['by_category'][category]['losses'] += 1

            # Store result
            trade_result = {
                **signal,
                'outcome': outcome,
                'was_correct': was_correct,
                'pnl_usd': round(pnl, 4),
                'resolution_date': resolution.get('resolution_date'),
            }
            self.resolved_trades.append(trade_result)

            # Update database if requested
            if update_db:
                self._update_trade_resolution(signal['id'], outcome, pnl, was_correct)

        # Calculate summary stats
        results['win_rate'] = (results['wins'] / results['resolved'] * 100) if results['resolved'] > 0 else 0
        results['avg_pnl'] = results['total_pnl'] / results['resolved'] if results['resolved'] > 0 else 0

        self.stats = results
        return results

    def _get_category_from_question(self, question: str) -> Optional[str]:
        """Infer category from question text."""
        q = question.lower()

        if any(w in q for w in ['bitcoin', 'btc', 'ethereum', 'eth', 'crypto', 'solana']):
            return 'crypto'
        if any(w in q for w in ['nba', 'nfl', 'mlb', 'nhl', 'ufc', 'sports']):
            return 'sports'
        if any(w in q for w in ['election', 'president', 'senate', 'congress', 'vote']):
            return 'politics'
        if any(w in q for w in ['oscar', 'emmy', 'grammy', 'movie', 'film']):
            return 'entertainment'
        if any(w in q for w in ['spacex', 'launch', 'rocket', 'nasa']):
            return 'space'
        if any(w in q for w in ['weather', 'hurricane', 'temperature']):
            return 'weather'

        return None

    def _update_trade_resolution(self, trade_id: int, outcome: str, pnl: float, was_correct: bool):
        """Update the trade in the database with resolution."""
        try:
            conn = self._get_pnl_conn()
            conn.execute('''
                UPDATE simulated_trades SET
                    resolved = 1,
                    resolution_date = ?,
                    actual_outcome = ?,
                    exit_price = ?,
                    simulated_pnl_usd = ?,
                    simulated_pnl_pct = ?,
                    was_correct = ?,
                    updated_at = ?
                WHERE id = ?
            ''', (
                datetime.now(timezone.utc).isoformat(),
                outcome,
                1.0 if was_correct else 0.0,
                round(pnl, 4),
                round((pnl / 10.0) * 100, 2),  # Assuming $10 default position
                1 if was_correct else 0,
                datetime.now(timezone.utc).isoformat(),
                trade_id,
            ))
            conn.commit()
        except Exception as e:
            cprint(f"   DB update error: {e}", "yellow")

    def print_results(self):
        """Print backtest results dashboard."""
        if not self.stats:
            cprint("No results to display. Run analyze_signals() first.", "yellow")
            return

        s = self.stats

        cprint("\n" + "=" * 70, "green")
        cprint("BACKTEST RESULTS", "green", attrs=['bold'])
        cprint("=" * 70, "green")

        # Overall stats
        cprint(f"\nOVERALL PERFORMANCE:", "yellow", attrs=['bold'])
        cprint(f"   Signals Checked: {s['total_checked']}", "white")
        cprint(f"   Resolved: {s['resolved']} | Unresolved: {s['unresolved']}", "white")

        if s['resolved'] > 0:
            win_color = "green" if s['win_rate'] >= 50 else "red"
            pnl_color = "green" if s['total_pnl'] >= 0 else "red"

            cprint(f"   Wins: {s['wins']} | Losses: {s['losses']}", "white")
            cprint(f"   Win Rate: {s['win_rate']:.1f}%", win_color, attrs=['bold'])
            cprint(f"   Total PnL: ${s['total_pnl']:.2f}", pnl_color, attrs=['bold'])
            cprint(f"   Avg PnL/Trade: ${s['avg_pnl']:.2f}", "white")

        # By signal type
        if s['by_signal_type']:
            cprint(f"\nBY SIGNAL TYPE:", "yellow", attrs=['bold'])
            cprint(f"   {'Type':<15} {'Wins':>6} {'Losses':>7} {'Win%':>7} {'PnL':>10}", "white")
            cprint("   " + "-" * 48, "white")

            for sig_type, data in sorted(s['by_signal_type'].items(), key=lambda x: x[1]['pnl'], reverse=True):
                total = data['wins'] + data['losses']
                if total == 0:
                    continue
                win_rate = data['wins'] / total * 100
                color = "green" if win_rate >= 50 else "red"
                cprint(f"   {sig_type:<15} {data['wins']:>6} {data['losses']:>7} {win_rate:>6.1f}% ${data['pnl']:>9.2f}", color)

        # By category
        if s['by_category']:
            cprint(f"\nBY CATEGORY:", "yellow", attrs=['bold'])
            cprint(f"   {'Category':<15} {'Wins':>6} {'Losses':>7} {'Win%':>7} {'PnL':>10}", "white")
            cprint("   " + "-" * 48, "white")

            for category, data in sorted(s['by_category'].items(), key=lambda x: x[1]['pnl'], reverse=True):
                total = data['wins'] + data['losses']
                if total == 0:
                    continue
                win_rate = data['wins'] / total * 100
                color = "green" if win_rate >= 50 else "red"
                cprint(f"   {category:<15} {data['wins']:>6} {data['losses']:>7} {win_rate:>6.1f}% ${data['pnl']:>9.2f}", color)

        # Key insights
        cprint(f"\nKEY INSIGHTS:", "yellow", attrs=['bold'])

        # Best signal type
        if s['by_signal_type']:
            best_type = max(s['by_signal_type'].items(), key=lambda x: x[1]['pnl'])
            if best_type[1]['wins'] + best_type[1]['losses'] >= 5:
                win_rate = best_type[1]['wins'] / (best_type[1]['wins'] + best_type[1]['losses']) * 100
                cprint(f"   Best Signal Type: {best_type[0]} ({win_rate:.0f}% win rate, ${best_type[1]['pnl']:.2f} PnL)", "green")

        # Best category
        if s['by_category']:
            valid_cats = {k: v for k, v in s['by_category'].items() if v['wins'] + v['losses'] >= 5}
            if valid_cats:
                best_cat = max(valid_cats.items(), key=lambda x: x[1]['pnl'])
                win_rate = best_cat[1]['wins'] / (best_cat[1]['wins'] + best_cat[1]['losses']) * 100
                cprint(f"   Best Category: {best_cat[0]} ({win_rate:.0f}% win rate, ${best_cat[1]['pnl']:.2f} PnL)", "green")

                worst_cat = min(valid_cats.items(), key=lambda x: x[1]['pnl'])
                if worst_cat[1]['pnl'] < 0:
                    win_rate = worst_cat[1]['wins'] / (worst_cat[1]['wins'] + worst_cat[1]['losses']) * 100
                    cprint(f"   Worst Category: {worst_cat[0]} ({win_rate:.0f}% win rate, ${worst_cat[1]['pnl']:.2f} PnL) - AVOID", "red")

        cprint("\n" + "=" * 70 + "\n", "green")

    def export_results(self, filepath: str = None) -> str:
        """Export detailed results to CSV."""
        import csv

        filepath = filepath or str(DATA_DIR / "backtest_results.csv")

        with open(filepath, 'w', newline='') as f:
            if not self.resolved_trades:
                f.write("No resolved trades\n")
                return filepath

            writer = csv.DictWriter(f, fieldnames=self.resolved_trades[0].keys())
            writer.writeheader()
            writer.writerows(self.resolved_trades)

        cprint(f"Results exported to: {filepath}", "cyan")
        return filepath

    def close(self):
        """Close HTTP client."""
        self.client.close()


def main():
    """Run backtest analysis."""
    import argparse

    parser = argparse.ArgumentParser(description='Backtest Polymarket Signal History')
    parser.add_argument('--limit', type=int, default=None, help='Max signals to check')
    parser.add_argument('--no-update', action='store_true', help='Do not update database')
    parser.add_argument('--export', action='store_true', help='Export results to CSV')
    args = parser.parse_args()

    analyzer = BacktestAnalyzer()

    try:
        results = analyzer.analyze_signals(
            limit=args.limit,
            update_db=not args.no_update,
        )
        analyzer.print_results()

        if args.export:
            analyzer.export_results()

    finally:
        analyzer.close()


if __name__ == "__main__":
    main()
