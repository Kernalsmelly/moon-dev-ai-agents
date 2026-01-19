"""
Polymarket Micro-Edge SQLite Store

Persistence layer for niche markets, signals, positions, and correlations.
Follows pattern from src/position_store.py but tailored for micro-edge strategy.
"""

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from src.config.polymarket_micro_config import DB_PATH, DATA_DIR


@dataclass
class Market:
    """Parsed market from database."""
    condition_id: str
    question: str
    slug: str
    category: str
    niche_score: float
    niche_category: str
    liquidity: float
    volume_24h: float
    hours_to_resolution: float
    yes_price: float
    no_price: float
    yes_token_id: str
    no_token_id: str
    first_seen: str
    last_analyzed: Optional[str]


@dataclass
class Signal:
    """Trading signal."""
    id: int
    condition_id: str
    signal_type: str  # 'resolution', 'correlation', 'news', 'niche'
    side: str  # 'YES', 'NO'
    entry_price: float
    target_price: float
    confidence: float
    edge_pct: float
    status: str  # 'pending', 'active', 'closed', 'expired'
    created_at: str


@dataclass
class Position:
    """Open or closed position."""
    id: int
    condition_id: str
    side: str
    entry_price: float
    amount_usd: float
    shares: float
    status: str
    exit_price: Optional[float]
    pnl_usd: Optional[float]
    pnl_pct: Optional[float]
    exit_reason: Optional[str]


class MicroEdgeStore:
    """SQLite-backed storage for micro-edge strategy."""

    SCHEMA_VERSION = 1

    def __init__(self, db_path: str = str(DB_PATH)):
        self.db_path = db_path
        self._local = threading.local()

        # Ensure data directory exists
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        # Initialize schema
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        """Get thread-local database connection."""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path, timeout=30.0)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute('PRAGMA journal_mode=WAL')
            self._local.conn.execute('PRAGMA busy_timeout=30000')
        return self._local.conn

    @contextmanager
    def _transaction(self):
        """Context manager for database transactions."""
        conn = self._get_conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _init_schema(self):
        """Create database tables."""
        with self._transaction() as conn:
            # Markets table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS markets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    condition_id TEXT UNIQUE NOT NULL,
                    question TEXT NOT NULL,
                    description TEXT,
                    slug TEXT,
                    category TEXT,

                    -- Pricing
                    yes_price REAL,
                    no_price REAL,
                    last_price_update TEXT,

                    -- Liquidity metrics
                    volume_24h REAL DEFAULT 0,
                    volume_total REAL DEFAULT 0,
                    liquidity REAL DEFAULT 0,

                    -- Resolution timing
                    end_date TEXT,
                    hours_to_resolution REAL,
                    is_near_resolution INTEGER DEFAULT 0,

                    -- Niche scoring
                    niche_score REAL DEFAULT 0,
                    niche_category TEXT,
                    is_major_sport INTEGER DEFAULT 0,
                    is_major_crypto INTEGER DEFAULT 0,

                    -- Token IDs for trading
                    yes_token_id TEXT,
                    no_token_id TEXT,

                    -- Tracking
                    first_seen TEXT DEFAULT CURRENT_TIMESTAMP,
                    last_analyzed TEXT,
                    analysis_count INTEGER DEFAULT 0
                )
            ''')

            # Correlations table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS correlations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_a_id TEXT NOT NULL,
                    market_b_id TEXT NOT NULL,
                    correlation_type TEXT NOT NULL,
                    correlation_score REAL,
                    expected_sum REAL,
                    actual_sum REAL,
                    arbitrage_opportunity REAL,
                    last_calculated TEXT,
                    UNIQUE(market_a_id, market_b_id)
                )
            ''')

            # Signals table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    condition_id TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    side TEXT NOT NULL,
                    entry_price REAL,
                    target_price REAL,
                    confidence REAL,
                    edge_pct REAL,
                    status TEXT DEFAULT 'pending',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    executed_at TEXT,
                    closed_at TEXT,
                    notes TEXT
                )
            ''')

            # Positions table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    condition_id TEXT NOT NULL,
                    side TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    entry_timestamp TEXT NOT NULL,
                    amount_usd REAL NOT NULL,
                    shares REAL NOT NULL,
                    status TEXT DEFAULT 'open',
                    exit_price REAL,
                    exit_timestamp TEXT,
                    pnl_usd REAL,
                    pnl_pct REAL,
                    exit_reason TEXT,
                    signal_id INTEGER,
                    order_id TEXT,
                    metadata TEXT DEFAULT '{}'
                )
            ''')

            # Schema version
            conn.execute('''
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY
                )
            ''')

            cur = conn.execute('SELECT version FROM schema_version')
            if cur.fetchone() is None:
                conn.execute('INSERT INTO schema_version (version) VALUES (?)', (self.SCHEMA_VERSION,))

            # Indexes
            conn.execute('CREATE INDEX IF NOT EXISTS idx_markets_niche_score ON markets(niche_score DESC)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_markets_resolution ON markets(hours_to_resolution)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_markets_liquidity ON markets(liquidity)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status, created_at)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_correlations_arb ON correlations(arbitrage_opportunity DESC)')

    # =========================================================================
    # Market Operations
    # =========================================================================

    def upsert_market(self, market: Dict[str, Any]) -> bool:
        """Insert or update a market."""
        try:
            with self._transaction() as conn:
                conn.execute('''
                    INSERT INTO markets (
                        condition_id, question, description, slug, category,
                        yes_price, no_price, last_price_update,
                        volume_24h, volume_total, liquidity,
                        end_date, hours_to_resolution, is_near_resolution,
                        niche_score, niche_category, is_major_sport, is_major_crypto,
                        yes_token_id, no_token_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(condition_id) DO UPDATE SET
                        question = excluded.question,
                        yes_price = excluded.yes_price,
                        no_price = excluded.no_price,
                        last_price_update = excluded.last_price_update,
                        volume_24h = excluded.volume_24h,
                        liquidity = excluded.liquidity,
                        hours_to_resolution = excluded.hours_to_resolution,
                        is_near_resolution = excluded.is_near_resolution,
                        niche_score = excluded.niche_score,
                        niche_category = excluded.niche_category
                ''', (
                    market.get('condition_id'),
                    market.get('question'),
                    market.get('description'),
                    market.get('slug'),
                    market.get('category'),
                    market.get('yes_price'),
                    market.get('no_price'),
                    datetime.now(timezone.utc).isoformat(),
                    market.get('volume_24h', 0),
                    market.get('volume', 0),
                    market.get('liquidity', 0),
                    market.get('end_date').isoformat() if market.get('end_date') else None,
                    market.get('hours_to_resolution'),
                    1 if market.get('is_near_resolution') else 0,
                    market.get('niche_score', 0),
                    market.get('niche_category'),
                    1 if market.get('is_major_sport') else 0,
                    1 if market.get('is_major_crypto') else 0,
                    market.get('yes_token_id'),
                    market.get('no_token_id'),
                ))
            return True
        except Exception as e:
            print(f"[MicroStore] Error upserting market: {e}")
            return False

    def get_market(self, condition_id: str) -> Optional[Dict]:
        """Get a market by condition ID."""
        conn = self._get_conn()
        cur = conn.execute('SELECT * FROM markets WHERE condition_id = ?', (condition_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def get_niche_markets(
        self,
        min_score: float = 6.0,
        max_liquidity: float = 5000,
        limit: int = 50,
    ) -> List[Dict]:
        """Get high-niche-score, low-liquidity markets."""
        conn = self._get_conn()
        cur = conn.execute('''
            SELECT * FROM markets
            WHERE niche_score >= ?
              AND liquidity <= ?
              AND liquidity > 0
            ORDER BY niche_score DESC, liquidity ASC
            LIMIT ?
        ''', (min_score, max_liquidity, limit))
        return [dict(row) for row in cur.fetchall()]

    def get_resolution_opportunities(
        self,
        min_hours: float = 24,
        max_hours: float = 72,
        limit: int = 20,
    ) -> List[Dict]:
        """Get markets near resolution."""
        conn = self._get_conn()
        cur = conn.execute('''
            SELECT * FROM markets
            WHERE hours_to_resolution >= ?
              AND hours_to_resolution <= ?
              AND is_near_resolution = 1
            ORDER BY hours_to_resolution ASC
            LIMIT ?
        ''', (min_hours, max_hours, limit))
        return [dict(row) for row in cur.fetchall()]

    # =========================================================================
    # Signal Operations
    # =========================================================================

    def create_signal(
        self,
        condition_id: str,
        signal_type: str,
        side: str,
        entry_price: float,
        target_price: float,
        confidence: float,
        edge_pct: float,
        notes: str = '',
    ) -> int:
        """Create a new trading signal."""
        with self._transaction() as conn:
            cur = conn.execute('''
                INSERT INTO signals (
                    condition_id, signal_type, side,
                    entry_price, target_price, confidence, edge_pct, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                condition_id, signal_type, side,
                entry_price, target_price, confidence, edge_pct, notes,
            ))
            return cur.lastrowid

    def get_pending_signals(self, limit: int = 20, min_entry_price: float = 0.10) -> List[Dict]:
        """Get signals awaiting execution.

        Args:
            limit: Max signals to return
            min_entry_price: Minimum entry price to filter lottery tickets (default $0.10)
        """
        conn = self._get_conn()
        cur = conn.execute('''
            SELECT s.*, m.question, m.slug
            FROM signals s
            JOIN markets m ON s.condition_id = m.condition_id
            WHERE s.status = 'pending'
              AND s.entry_price >= ?
            ORDER BY s.edge_pct DESC
            LIMIT ?
        ''', (min_entry_price, limit))
        return [dict(row) for row in cur.fetchall()]

    def update_signal_status(self, signal_id: int, status: str) -> bool:
        """Update signal status."""
        try:
            with self._transaction() as conn:
                conn.execute('''
                    UPDATE signals
                    SET status = ?,
                        executed_at = CASE WHEN ? = 'active' THEN datetime('now') ELSE executed_at END,
                        closed_at = CASE WHEN ? IN ('closed', 'expired') THEN datetime('now') ELSE closed_at END
                    WHERE id = ?
                ''', (status, status, status, signal_id))
            return True
        except Exception:
            return False

    # =========================================================================
    # Position Operations
    # =========================================================================

    def open_position(
        self,
        condition_id: str,
        side: str,
        entry_price: float,
        amount_usd: float,
        shares: float,
        signal_id: Optional[int] = None,
        order_id: Optional[str] = None,
    ) -> int:
        """Record a new open position."""
        with self._transaction() as conn:
            cur = conn.execute('''
                INSERT INTO positions (
                    condition_id, side, entry_price, entry_timestamp,
                    amount_usd, shares, signal_id, order_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                condition_id, side, entry_price,
                datetime.now(timezone.utc).isoformat(),
                amount_usd, shares, signal_id, order_id,
            ))
            return cur.lastrowid

    def close_position(
        self,
        position_id: int,
        exit_price: float,
        exit_reason: str,
    ) -> bool:
        """Close a position and calculate P&L."""
        try:
            conn = self._get_conn()
            cur = conn.execute('SELECT * FROM positions WHERE id = ?', (position_id,))
            pos = cur.fetchone()

            if pos is None:
                return False

            entry_price = pos['entry_price']
            amount_usd = pos['amount_usd']
            shares = pos['shares']
            side = pos['side']

            # Calculate P&L
            if side == 'YES':
                pnl_usd = shares * (exit_price - entry_price)
            else:  # NO
                pnl_usd = shares * (entry_price - exit_price)

            pnl_pct = (pnl_usd / amount_usd) * 100 if amount_usd > 0 else 0

            with self._transaction() as conn:
                conn.execute('''
                    UPDATE positions
                    SET status = 'closed',
                        exit_price = ?,
                        exit_timestamp = ?,
                        pnl_usd = ?,
                        pnl_pct = ?,
                        exit_reason = ?
                    WHERE id = ?
                ''', (
                    exit_price,
                    datetime.now(timezone.utc).isoformat(),
                    pnl_usd, pnl_pct, exit_reason, position_id,
                ))
            return True
        except Exception as e:
            print(f"[MicroStore] Error closing position: {e}")
            return False

    def get_open_positions(self) -> List[Dict]:
        """Get all open positions."""
        conn = self._get_conn()
        cur = conn.execute('''
            SELECT p.*, m.question, m.slug, m.yes_price, m.no_price
            FROM positions p
            JOIN markets m ON p.condition_id = m.condition_id
            WHERE p.status = 'open'
        ''')
        return [dict(row) for row in cur.fetchall()]

    def get_performance_summary(self, days: Optional[int] = None) -> Dict:
        """Get aggregate performance metrics."""
        conn = self._get_conn()

        where = ""
        params = []
        if days:
            where = "WHERE exit_timestamp >= datetime('now', ?)"
            params.append(f'-{days} days')

        cur = conn.execute(f'''
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as winners,
                SUM(CASE WHEN pnl_usd <= 0 THEN 1 ELSE 0 END) as losers,
                SUM(pnl_usd) as total_pnl,
                AVG(pnl_usd) as avg_pnl,
                MAX(pnl_usd) as best_trade,
                MIN(pnl_usd) as worst_trade
            FROM positions
            WHERE status = 'closed'
            {where}
        ''', params)

        row = cur.fetchone()

        if row is None or row['total_trades'] == 0:
            return {
                'total_trades': 0,
                'winners': 0,
                'losers': 0,
                'win_rate': 0.0,
                'total_pnl': 0.0,
                'avg_pnl': 0.0,
            }

        return {
            'total_trades': row['total_trades'],
            'winners': row['winners'] or 0,
            'losers': row['losers'] or 0,
            'win_rate': (row['winners'] / row['total_trades'] * 100) if row['total_trades'] > 0 else 0,
            'total_pnl': row['total_pnl'] or 0,
            'avg_pnl': row['avg_pnl'] or 0,
            'best_trade': row['best_trade'] or 0,
            'worst_trade': row['worst_trade'] or 0,
        }

    # =========================================================================
    # Correlation Operations
    # =========================================================================

    def upsert_correlation(
        self,
        market_a_id: str,
        market_b_id: str,
        correlation_type: str,
        expected_sum: float,
        actual_sum: float,
    ) -> bool:
        """Insert or update a market correlation."""
        arb_opportunity = abs(actual_sum - expected_sum)

        try:
            with self._transaction() as conn:
                conn.execute('''
                    INSERT INTO correlations (
                        market_a_id, market_b_id, correlation_type,
                        expected_sum, actual_sum, arbitrage_opportunity, last_calculated
                    ) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(market_a_id, market_b_id) DO UPDATE SET
                        expected_sum = excluded.expected_sum,
                        actual_sum = excluded.actual_sum,
                        arbitrage_opportunity = excluded.arbitrage_opportunity,
                        last_calculated = datetime('now')
                ''', (
                    market_a_id, market_b_id, correlation_type,
                    expected_sum, actual_sum, arb_opportunity,
                ))
            return True
        except Exception:
            return False

    def get_arbitrage_opportunities(
        self,
        min_opportunity: float = 0.03,
        limit: int = 10,
    ) -> List[Dict]:
        """Get correlation arbitrage opportunities."""
        conn = self._get_conn()
        cur = conn.execute('''
            SELECT c.*,
                   m1.question as market_a_question,
                   m2.question as market_b_question
            FROM correlations c
            JOIN markets m1 ON c.market_a_id = m1.condition_id
            JOIN markets m2 ON c.market_b_id = m2.condition_id
            WHERE c.arbitrage_opportunity >= ?
            ORDER BY c.arbitrage_opportunity DESC
            LIMIT ?
        ''', (min_opportunity, limit))
        return [dict(row) for row in cur.fetchall()]


# Module-level singleton
_store: Optional[MicroEdgeStore] = None


def get_store() -> MicroEdgeStore:
    """Get the global MicroEdgeStore instance."""
    global _store
    if _store is None:
        _store = MicroEdgeStore()
    return _store
