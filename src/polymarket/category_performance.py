"""
Category Performance Tracker for Polymarket Micro-Edge Agent

Tracks YOUR win rate by market category to identify where you have real edge.
Top Polymarket performers (like HyperLiquid0xb with $1.4M in MLB) specialize
in categories where they have information advantages.

Key insight from research:
- The Quant Fund: 2,000+ trades, 58% overall, but 75%+ in specialty
- Axios: 96% win rate specifically in "mention markets"
- Domain specialists outperform generalists

This module:
1. Tracks performance by category (sports, politics, crypto, entertainment, etc.)
2. Calculates rolling win rates per category
3. Provides edge multipliers for signal scoring
4. Recommends categories to focus on vs. avoid
"""

import os
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from pathlib import Path

from src.config.polymarket_micro_config import DATA_DIR


# Standard Polymarket categories (from niche_classifier)
CATEGORIES = [
    'politics',
    'crypto',
    'sports',
    'entertainment',
    'science',
    'business',
    'weather',
    'esports',
    'mma',
    'golf',
    'tennis',
    'soccer',
    'nba',
    'nfl',
    'mlb',
    'nhl',
    'culture',
    'awards',
    'legal',
    'other',
]

# Minimum trades needed to calculate reliable win rate
MIN_TRADES_FOR_CONFIDENCE = 10


class CategoryPerformance:
    """
    Track and analyze performance by market category.

    Usage:
        tracker = CategoryPerformance()

        # Record a trade
        tracker.record_trade(
            condition_id='0x123',
            category='sports',
            subcategory='mlb',
            side='YES',
            entry_price=0.45,
            amount_usd=10.0,
        )

        # When market resolves
        tracker.resolve_trade(condition_id='0x123', outcome='YES')

        # Get your edge by category
        edges = tracker.get_category_edges()
        # {'mlb': 1.4, 'politics': 0.8, 'crypto': 1.0, ...}

        # Apply to signal scoring
        adjusted_score = base_score * edges.get(category, 1.0)
    """

    def __init__(self, db_path: str = None):
        self.db_path = db_path or str(DATA_DIR / "category_performance.db")
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self):
        with self._get_conn() as conn:
            # Individual trades with category
            conn.execute('''
                CREATE TABLE IF NOT EXISTS category_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    condition_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    subcategory TEXT,
                    side TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    amount_usd REAL DEFAULT 10.0,
                    shares REAL,

                    -- Resolution
                    resolved INTEGER DEFAULT 0,
                    outcome TEXT,
                    exit_price REAL,
                    pnl_usd REAL,
                    was_correct INTEGER,

                    -- Metadata
                    signal_type TEXT,
                    edge_at_entry REAL,
                    confidence_at_entry REAL,

                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    resolved_at TEXT,

                    UNIQUE(condition_id, side)
                )
            ''')

            # Aggregated category stats (updated periodically)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS category_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT UNIQUE NOT NULL,
                    total_trades INTEGER DEFAULT 0,
                    resolved_trades INTEGER DEFAULT 0,
                    wins INTEGER DEFAULT 0,
                    losses INTEGER DEFAULT 0,
                    win_rate REAL DEFAULT 0,
                    total_pnl REAL DEFAULT 0,
                    avg_pnl REAL DEFAULT 0,
                    avg_edge_at_entry REAL DEFAULT 0,
                    edge_multiplier REAL DEFAULT 1.0,
                    recommendation TEXT DEFAULT 'neutral',
                    last_updated TEXT
                )
            ''')

            # Historical snapshots for trend analysis
            conn.execute('''
                CREATE TABLE IF NOT EXISTS category_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_date TEXT NOT NULL,
                    category TEXT NOT NULL,
                    win_rate REAL,
                    total_pnl REAL,
                    trade_count INTEGER,
                    UNIQUE(snapshot_date, category)
                )
            ''')

            conn.execute('CREATE INDEX IF NOT EXISTS idx_cat_trades_category ON category_trades(category)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_cat_trades_resolved ON category_trades(resolved)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_cat_trades_condition ON category_trades(condition_id)')

    def record_trade(
        self,
        condition_id: str,
        category: str,
        side: str,
        entry_price: float,
        amount_usd: float = 10.0,
        subcategory: str = None,
        signal_type: str = None,
        edge_at_entry: float = None,
        confidence_at_entry: float = None,
    ) -> int:
        """
        Record a new trade with its category.

        Args:
            condition_id: Polymarket market condition ID
            category: Market category (politics, sports, crypto, etc.)
            side: 'YES' or 'NO'
            entry_price: Price paid per share
            amount_usd: Position size in USD
            subcategory: More specific category (e.g., 'mlb' within 'sports')
            signal_type: Type of signal that generated this trade
            edge_at_entry: Calculated edge % when entering
            confidence_at_entry: Confidence score when entering

        Returns:
            Trade ID
        """
        shares = amount_usd / entry_price if entry_price > 0 else 0
        category = category.lower().strip()

        with self._get_conn() as conn:
            cur = conn.execute('''
                INSERT OR REPLACE INTO category_trades (
                    condition_id, category, subcategory, side, entry_price,
                    amount_usd, shares, signal_type, edge_at_entry, confidence_at_entry
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                condition_id, category, subcategory, side.upper(), entry_price,
                amount_usd, shares, signal_type, edge_at_entry, confidence_at_entry
            ))
            return cur.lastrowid

    def resolve_trade(
        self,
        condition_id: str,
        outcome: str,
        exit_price: float = None,
    ) -> List[Dict]:
        """
        Resolve all trades for a condition and calculate PnL.

        Args:
            condition_id: Market condition ID
            outcome: 'YES' or 'NO' - what actually happened
            exit_price: Optional exit price (defaults to 1.0 for correct, 0.0 for wrong)

        Returns:
            List of resolved trade results
        """
        outcome = outcome.upper()
        results = []

        conn = self._get_conn()
        cur = conn.execute(
            'SELECT * FROM category_trades WHERE condition_id = ? AND resolved = 0',
            (condition_id,)
        )
        trades = cur.fetchall()

        for trade in trades:
            side = trade['side']
            entry = trade['entry_price']
            shares = trade['shares']
            amount = trade['amount_usd']

            # Determine if correct
            was_correct = 1 if outcome == side else 0

            # Calculate exit price
            if exit_price is None:
                actual_exit = 1.0 if was_correct else 0.0
            else:
                actual_exit = exit_price

            # Calculate PnL
            if side == 'YES':
                pnl = shares * (actual_exit - entry)
            else:
                pnl = shares * ((1 - actual_exit) - (1 - entry))

            with self._get_conn() as conn:
                conn.execute('''
                    UPDATE category_trades SET
                        resolved = 1,
                        outcome = ?,
                        exit_price = ?,
                        pnl_usd = ?,
                        was_correct = ?,
                        resolved_at = ?
                    WHERE id = ?
                ''', (
                    outcome, actual_exit, round(pnl, 4), was_correct,
                    datetime.now(timezone.utc).isoformat(), trade['id']
                ))

            results.append({
                'trade_id': trade['id'],
                'category': trade['category'],
                'subcategory': trade['subcategory'],
                'side': side,
                'outcome': outcome,
                'was_correct': bool(was_correct),
                'pnl_usd': round(pnl, 4),
                'entry_price': entry,
                'exit_price': actual_exit,
            })

        # Update category stats after resolution
        if results:
            self._update_category_stats(results[0]['category'])

        return results

    def _update_category_stats(self, category: str = None):
        """Recalculate stats for a category (or all if None)."""
        conn = self._get_conn()

        if category:
            categories = [category]
        else:
            cur = conn.execute('SELECT DISTINCT category FROM category_trades')
            categories = [row['category'] for row in cur.fetchall()]

        for cat in categories:
            cur = conn.execute('''
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN resolved = 1 THEN 1 ELSE 0 END) as resolved,
                    SUM(CASE WHEN was_correct = 1 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN resolved = 1 AND was_correct = 0 THEN 1 ELSE 0 END) as losses,
                    SUM(pnl_usd) as total_pnl,
                    AVG(pnl_usd) as avg_pnl,
                    AVG(edge_at_entry) as avg_edge
                FROM category_trades
                WHERE category = ?
            ''', (cat,))

            row = cur.fetchone()

            resolved = row['resolved'] or 0
            wins = row['wins'] or 0
            win_rate = (wins / resolved * 100) if resolved > 0 else 0

            # Calculate edge multiplier
            # > 60% win rate with 10+ trades = boost signals from this category
            # < 40% win rate with 10+ trades = reduce signals from this category
            edge_mult = 1.0
            recommendation = 'neutral'

            if resolved >= MIN_TRADES_FOR_CONFIDENCE:
                if win_rate >= 65:
                    edge_mult = 1.3  # Strong edge - boost 30%
                    recommendation = 'focus'
                elif win_rate >= 55:
                    edge_mult = 1.15  # Slight edge - boost 15%
                    recommendation = 'favorable'
                elif win_rate <= 35:
                    edge_mult = 0.5  # Losing badly - cut by 50%
                    recommendation = 'avoid'
                elif win_rate <= 45:
                    edge_mult = 0.75  # Slight losing - reduce 25%
                    recommendation = 'caution'

            with self._get_conn() as conn:
                conn.execute('''
                    INSERT INTO category_stats (
                        category, total_trades, resolved_trades, wins, losses,
                        win_rate, total_pnl, avg_pnl, avg_edge_at_entry,
                        edge_multiplier, recommendation, last_updated
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(category) DO UPDATE SET
                        total_trades = excluded.total_trades,
                        resolved_trades = excluded.resolved_trades,
                        wins = excluded.wins,
                        losses = excluded.losses,
                        win_rate = excluded.win_rate,
                        total_pnl = excluded.total_pnl,
                        avg_pnl = excluded.avg_pnl,
                        avg_edge_at_entry = excluded.avg_edge_at_entry,
                        edge_multiplier = excluded.edge_multiplier,
                        recommendation = excluded.recommendation,
                        last_updated = excluded.last_updated
                ''', (
                    cat, row['total'], resolved, wins, row['losses'] or 0,
                    round(win_rate, 1), round(row['total_pnl'] or 0, 2),
                    round(row['avg_pnl'] or 0, 2), round(row['avg_edge'] or 0, 1),
                    edge_mult, recommendation, datetime.now(timezone.utc).isoformat()
                ))

    def get_category_stats(self, category: str = None) -> List[Dict]:
        """
        Get performance stats by category.

        Args:
            category: Specific category or None for all

        Returns:
            List of category stats dicts
        """
        conn = self._get_conn()

        if category:
            cur = conn.execute(
                'SELECT * FROM category_stats WHERE category = ?',
                (category.lower(),)
            )
        else:
            cur = conn.execute(
                'SELECT * FROM category_stats ORDER BY win_rate DESC'
            )

        return [dict(row) for row in cur.fetchall()]

    def get_category_edges(self) -> Dict[str, float]:
        """
        Get edge multipliers for all categories.

        Returns:
            Dict mapping category -> edge multiplier (1.0 = neutral)

        Example:
            {'mlb': 1.3, 'politics': 0.75, 'crypto': 1.0}
        """
        stats = self.get_category_stats()
        return {s['category']: s['edge_multiplier'] for s in stats}

    def get_recommendations(self) -> Dict[str, List[str]]:
        """
        Get category recommendations based on your performance.

        Returns:
            {
                'focus': ['mlb', 'esports'],      # 65%+ win rate
                'favorable': ['politics'],        # 55-65% win rate
                'neutral': ['crypto'],            # 45-55% win rate
                'caution': ['entertainment'],     # 35-45% win rate
                'avoid': ['weather'],             # <35% win rate
            }
        """
        stats = self.get_category_stats()

        recs = {
            'focus': [],
            'favorable': [],
            'neutral': [],
            'caution': [],
            'avoid': [],
        }

        for s in stats:
            rec = s.get('recommendation', 'neutral')
            if rec in recs:
                recs[rec].append(s['category'])

        return recs

    def get_performance_summary(self) -> Dict:
        """Get overall performance summary with category breakdown."""
        conn = self._get_conn()

        # Overall stats
        cur = conn.execute('''
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN resolved = 1 THEN 1 ELSE 0 END) as resolved,
                SUM(CASE WHEN was_correct = 1 THEN 1 ELSE 0 END) as wins,
                SUM(pnl_usd) as total_pnl
            FROM category_trades
        ''')
        overall = cur.fetchone()

        resolved = overall['resolved'] or 0
        wins = overall['wins'] or 0

        # Best and worst categories
        best_cats = self.get_category_stats()
        best = best_cats[0] if best_cats else None
        worst = best_cats[-1] if len(best_cats) > 1 else None

        return {
            'total_trades': overall['total_trades'],
            'resolved_trades': resolved,
            'wins': wins,
            'losses': resolved - wins,
            'win_rate': round((wins / resolved * 100) if resolved > 0 else 0, 1),
            'total_pnl': round(overall['total_pnl'] or 0, 2),
            'best_category': {
                'name': best['category'],
                'win_rate': best['win_rate'],
                'trades': best['resolved_trades'],
            } if best else None,
            'worst_category': {
                'name': worst['category'],
                'win_rate': worst['win_rate'],
                'trades': worst['resolved_trades'],
            } if worst and worst['resolved_trades'] >= MIN_TRADES_FOR_CONFIDENCE else None,
            'recommendations': self.get_recommendations(),
        }

    def apply_category_weight(
        self,
        base_score: float,
        category: str,
    ) -> Tuple[float, str]:
        """
        Apply category-based weighting to a signal score.

        Args:
            base_score: Original signal quality score (0-100)
            category: Market category

        Returns:
            (adjusted_score, reason)
        """
        edges = self.get_category_edges()
        multiplier = edges.get(category.lower(), 1.0)

        adjusted = base_score * multiplier

        if multiplier > 1.0:
            reason = f"+{int((multiplier-1)*100)}% category edge"
        elif multiplier < 1.0:
            reason = f"-{int((1-multiplier)*100)}% category penalty"
        else:
            reason = "neutral category"

        return (round(adjusted, 1), reason)

    def save_daily_snapshot(self):
        """Save today's stats for historical trend analysis."""
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        stats = self.get_category_stats()

        with self._get_conn() as conn:
            for s in stats:
                conn.execute('''
                    INSERT OR REPLACE INTO category_history (
                        snapshot_date, category, win_rate, total_pnl, trade_count
                    ) VALUES (?, ?, ?, ?, ?)
                ''', (
                    today, s['category'], s['win_rate'],
                    s['total_pnl'], s['resolved_trades']
                ))

    def print_dashboard(self):
        """Print a performance dashboard to console."""
        from termcolor import cprint

        summary = self.get_performance_summary()
        stats = self.get_category_stats()

        cprint("\n" + "=" * 70, "cyan")
        cprint("CATEGORY PERFORMANCE DASHBOARD", "cyan", attrs=['bold'])
        cprint("=" * 70, "cyan")

        # Overall
        cprint(f"\nOVERALL: {summary['wins']}/{summary['resolved_trades']} wins "
               f"({summary['win_rate']:.1f}%) | PnL: ${summary['total_pnl']:.2f}",
               "green" if summary['win_rate'] >= 50 else "red")

        # By category
        cprint("\nBY CATEGORY:", "yellow", attrs=['bold'])
        cprint(f"{'Category':<15} {'Trades':>8} {'Win Rate':>10} {'PnL':>10} {'Action':>12}", "white")
        cprint("-" * 55, "white")

        for s in stats:
            if s['resolved_trades'] == 0:
                continue

            color = "green" if s['win_rate'] >= 55 else ("red" if s['win_rate'] < 45 else "white")
            rec_color = {
                'focus': 'green',
                'favorable': 'cyan',
                'neutral': 'white',
                'caution': 'yellow',
                'avoid': 'red',
            }.get(s['recommendation'], 'white')

            cprint(
                f"{s['category']:<15} {s['resolved_trades']:>8} "
                f"{s['win_rate']:>9.1f}% {s['total_pnl']:>9.2f}$ ",
                color,
                end=""
            )
            cprint(f"{s['recommendation'].upper():>12}", rec_color)

        # Recommendations
        recs = summary['recommendations']
        if recs['focus']:
            cprint(f"\nFOCUS ON: {', '.join(recs['focus'])}", "green", attrs=['bold'])
        if recs['avoid']:
            cprint(f"AVOID: {', '.join(recs['avoid'])}", "red", attrs=['bold'])

        cprint("=" * 70 + "\n", "cyan")


# Singleton instance
_tracker: Optional[CategoryPerformance] = None


def get_category_tracker() -> CategoryPerformance:
    """Get global category performance tracker."""
    global _tracker
    if _tracker is None:
        _tracker = CategoryPerformance()
    return _tracker


def get_category_edge(category: str) -> float:
    """Quick helper to get edge multiplier for a category."""
    return get_category_tracker().get_category_edges().get(category.lower(), 1.0)


if __name__ == "__main__":
    """Run standalone to see your category performance dashboard."""
    import argparse

    parser = argparse.ArgumentParser(description='Polymarket Category Performance Tracker')
    parser.add_argument('--export', action='store_true', help='Export to CSV')
    parser.add_argument('--snapshot', action='store_true', help='Save daily snapshot')
    args = parser.parse_args()

    tracker = get_category_tracker()

    if args.snapshot:
        tracker.save_daily_snapshot()
        print("Daily snapshot saved.")
    elif args.export:
        # Export would go here
        print("Export not yet implemented")
    else:
        tracker.print_dashboard()
