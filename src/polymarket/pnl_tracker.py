"""
Simulated PnL Tracker for Polymarket Micro-Edge Agent

Tracks dry-run signals and calculates what PnL WOULD have been.
When markets resolve, compares our predictions to actual outcomes.

This validates the strategy before risking real money.
"""

import os
import json
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional
from pathlib import Path

from src.config.polymarket_micro_config import DATA_DIR


class PnLTracker:
    """Track simulated PnL for dry-run validation."""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or str(DATA_DIR / "simulated_pnl.db")
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self):
        with self._get_conn() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS simulated_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    condition_id TEXT NOT NULL,
                    question TEXT,
                    side TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    simulated_amount_usd REAL DEFAULT 10.0,
                    simulated_shares REAL,
                    signal_type TEXT,
                    edge_pct REAL,
                    confidence REAL,
                    source TEXT,

                    -- Category tracking (for performance analysis)
                    category TEXT,
                    subcategory TEXT,

                    -- Resolution tracking
                    resolved INTEGER DEFAULT 0,
                    resolution_date TEXT,
                    actual_outcome TEXT,  -- 'YES' or 'NO'
                    exit_price REAL,

                    -- PnL
                    simulated_pnl_usd REAL,
                    simulated_pnl_pct REAL,
                    was_correct INTEGER,

                    -- Timestamps
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT
                )
            ''')

            conn.execute('''
                CREATE TABLE IF NOT EXISTS daily_summary (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT UNIQUE NOT NULL,
                    total_signals INTEGER DEFAULT 0,
                    resolved_signals INTEGER DEFAULT 0,
                    correct_predictions INTEGER DEFAULT 0,
                    wrong_predictions INTEGER DEFAULT 0,
                    win_rate REAL,
                    simulated_pnl_usd REAL DEFAULT 0,
                    avg_edge_pct REAL,
                    best_trade_pnl REAL,
                    worst_trade_pnl REAL
                )
            ''')

            conn.execute('CREATE INDEX IF NOT EXISTS idx_trades_resolved ON simulated_trades(resolved)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_trades_condition ON simulated_trades(condition_id)')

    def record_signal(
        self,
        condition_id: str,
        question: str,
        side: str,
        entry_price: float,
        edge_pct: float,
        signal_type: str = 'unknown',
        confidence: float = 0.5,
        source: str = '',
        amount_usd: float = 10.0,
        category: str = None,
        subcategory: str = None,
    ) -> int:
        """Record a simulated trade from a dry-run signal."""
        shares = amount_usd / entry_price if entry_price > 0 else 0

        with self._get_conn() as conn:
            cur = conn.execute('''
                INSERT INTO simulated_trades (
                    condition_id, question, side, entry_price,
                    simulated_amount_usd, simulated_shares,
                    signal_type, edge_pct, confidence, source,
                    category, subcategory
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                condition_id, question, side, entry_price,
                amount_usd, shares, signal_type, edge_pct, confidence, source,
                category, subcategory
            ))

            # Also record in category performance tracker
            if category:
                try:
                    from src.polymarket.category_performance import get_category_tracker
                    cat_tracker = get_category_tracker()
                    cat_tracker.record_trade(
                        condition_id=condition_id,
                        category=category,
                        subcategory=subcategory,
                        side=side,
                        entry_price=entry_price,
                        amount_usd=amount_usd,
                        signal_type=signal_type,
                        edge_at_entry=edge_pct,
                        confidence_at_entry=confidence,
                    )
                except ImportError:
                    pass  # Category tracking not available

            return cur.lastrowid

    def resolve_trade(
        self,
        trade_id: int,
        actual_outcome: str,
        exit_price: float = None,
    ) -> Dict:
        """Mark a trade as resolved and calculate PnL."""
        conn = self._get_conn()
        cur = conn.execute('SELECT * FROM simulated_trades WHERE id = ?', (trade_id,))
        trade = cur.fetchone()

        if not trade:
            return {'error': 'Trade not found'}

        # Determine exit price
        if exit_price is None:
            exit_price = 1.0 if actual_outcome.upper() == trade['side'] else 0.0

        # Calculate PnL
        entry = trade['entry_price']
        shares = trade['simulated_shares']
        amount = trade['simulated_amount_usd']
        side = trade['side']

        if side == 'YES':
            pnl_usd = shares * (exit_price - entry)
        else:  # NO
            pnl_usd = shares * ((1 - exit_price) - (1 - entry))

        pnl_pct = (pnl_usd / amount) * 100 if amount > 0 else 0
        was_correct = 1 if actual_outcome.upper() == side else 0

        with self._get_conn() as conn:
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
                actual_outcome.upper(),
                exit_price,
                round(pnl_usd, 4),
                round(pnl_pct, 2),
                was_correct,
                datetime.now(timezone.utc).isoformat(),
                trade_id,
            ))

        # Also resolve in category performance tracker
        try:
            from src.polymarket.category_performance import get_category_tracker
            cat_tracker = get_category_tracker()
            cat_tracker.resolve_trade(
                condition_id=trade['condition_id'],
                outcome=actual_outcome,
                exit_price=exit_price,
            )
        except (ImportError, Exception):
            pass  # Category tracking not available or error

        return {
            'trade_id': trade_id,
            'was_correct': bool(was_correct),
            'pnl_usd': round(pnl_usd, 4),
            'pnl_pct': round(pnl_pct, 2),
        }

    def resolve_by_condition(self, condition_id: str, actual_outcome: str) -> List[Dict]:
        """Resolve all trades for a condition_id."""
        conn = self._get_conn()
        cur = conn.execute(
            'SELECT id FROM simulated_trades WHERE condition_id = ? AND resolved = 0',
            (condition_id,)
        )
        results = []
        for row in cur.fetchall():
            result = self.resolve_trade(row['id'], actual_outcome)
            results.append(result)
        return results

    def get_pending_trades(self) -> List[Dict]:
        """Get all unresolved simulated trades."""
        conn = self._get_conn()
        cur = conn.execute('''
            SELECT * FROM simulated_trades
            WHERE resolved = 0
            ORDER BY created_at DESC
        ''')
        return [dict(row) for row in cur.fetchall()]

    def get_performance_summary(self, days: int = None) -> Dict:
        """Get aggregate performance metrics."""
        conn = self._get_conn()

        where = "WHERE resolved = 1"
        params = []
        if days:
            where += " AND resolution_date >= datetime('now', ?)"
            params.append(f'-{days} days')

        cur = conn.execute(f'''
            SELECT
                COUNT(*) as total_resolved,
                SUM(was_correct) as correct,
                SUM(CASE WHEN was_correct = 0 THEN 1 ELSE 0 END) as wrong,
                SUM(simulated_pnl_usd) as total_pnl,
                AVG(simulated_pnl_usd) as avg_pnl,
                AVG(edge_pct) as avg_edge,
                MAX(simulated_pnl_usd) as best_trade,
                MIN(simulated_pnl_usd) as worst_trade
            FROM simulated_trades
            {where}
        ''', params)

        row = cur.fetchone()

        if not row or row['total_resolved'] == 0:
            return {
                'total_resolved': 0,
                'win_rate': 0,
                'total_pnl': 0,
                'avg_pnl': 0,
                'message': 'No resolved trades yet'
            }

        return {
            'total_resolved': row['total_resolved'],
            'correct': row['correct'] or 0,
            'wrong': row['wrong'] or 0,
            'win_rate': (row['correct'] / row['total_resolved'] * 100) if row['total_resolved'] > 0 else 0,
            'total_pnl': round(row['total_pnl'] or 0, 2),
            'avg_pnl': round(row['avg_pnl'] or 0, 2),
            'avg_edge': round(row['avg_edge'] or 0, 1),
            'best_trade': round(row['best_trade'] or 0, 2),
            'worst_trade': round(row['worst_trade'] or 0, 2),
        }

    def get_pending_by_category(self) -> Dict[str, int]:
        """Get count of pending trades by signal type."""
        conn = self._get_conn()
        cur = conn.execute('''
            SELECT signal_type, COUNT(*) as count
            FROM simulated_trades
            WHERE resolved = 0
            GROUP BY signal_type
        ''')
        return {row['signal_type']: row['count'] for row in cur.fetchall()}

    def get_performance_by_category(self) -> List[Dict]:
        """Get performance breakdown by market category."""
        conn = self._get_conn()
        cur = conn.execute('''
            SELECT
                category,
                COUNT(*) as total_trades,
                SUM(CASE WHEN resolved = 1 THEN 1 ELSE 0 END) as resolved,
                SUM(was_correct) as wins,
                SUM(simulated_pnl_usd) as total_pnl,
                AVG(CASE WHEN resolved = 1 THEN simulated_pnl_usd END) as avg_pnl
            FROM simulated_trades
            WHERE category IS NOT NULL
            GROUP BY category
            ORDER BY total_pnl DESC
        ''')

        results = []
        for row in cur.fetchall():
            resolved = row['resolved'] or 0
            wins = row['wins'] or 0
            results.append({
                'category': row['category'],
                'total_trades': row['total_trades'],
                'resolved': resolved,
                'wins': wins,
                'win_rate': round((wins / resolved * 100) if resolved > 0 else 0, 1),
                'total_pnl': round(row['total_pnl'] or 0, 2),
                'avg_pnl': round(row['avg_pnl'] or 0, 2),
            })

        return results

    def export_to_csv(self, filepath: str = None) -> str:
        """Export all trades to CSV for analysis."""
        import csv

        filepath = filepath or str(DATA_DIR / "simulated_trades.csv")

        conn = self._get_conn()
        cur = conn.execute('SELECT * FROM simulated_trades ORDER BY created_at DESC')
        rows = cur.fetchall()

        if not rows:
            return "No trades to export"

        with open(filepath, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(rows[0].keys())
            for row in rows:
                writer.writerow(tuple(row))

        return filepath


# Singleton instance
_tracker: Optional[PnLTracker] = None


def get_tracker() -> PnLTracker:
    """Get global PnL tracker instance."""
    global _tracker
    if _tracker is None:
        _tracker = PnLTracker()
    return _tracker
