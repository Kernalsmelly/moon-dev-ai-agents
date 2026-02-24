#!/usr/bin/env python3
"""Position Store: SQLite-backed persistence for positions and trade history.

This module replaces the JSON-based positions_state.json with a proper database
that survives crashes, supports concurrent access, and maintains trade history.

Features:
- ACID-compliant position storage
- Full trade history with P&L tracking
- Automatic migration from existing JSON state
- Drop-in replacement API for nice_funcs.py

Usage:
    from src.position_store import PositionStore

    store = PositionStore()  # Uses default data/positions.db

    # Get/set position state (same API as before)
    pos = store.get_position('mint_address')
    store.update_position('mint_address', entry_price=1.50, amount_usd=25.0)

    # Record completed trades
    store.record_trade(
        mint='...', symbol='SOL', side='BUY',
        entry_price=150.0, exit_price=165.0,
        amount_usd=25.0, exit_reason='TP1'
    )

    # Get trade history
    trades = store.get_trades(days=7)
    summary = store.get_performance_summary()
"""
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

# Project paths
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, 'data')
LEGACY_JSON_PATH = os.path.join(DATA_DIR, 'positions_state.json')


def _resolve_default_db_path() -> str:
    raw = str(os.getenv('MEME_POSITIONS_DB', '') or '').strip()
    if not raw:
        return os.path.join(DATA_DIR, 'positions.db')
    if os.path.isabs(raw):
        return raw
    return os.path.join(PROJECT_ROOT, raw)


DEFAULT_DB_PATH = _resolve_default_db_path()


@dataclass
class Position:
    """Current position state."""
    mint: str
    symbol: str
    entry_price: float
    entry_timestamp: str
    amount_tokens: float
    amount_usd: float
    highest_price_seen: float
    de_risked: bool
    status: str  # 'open', 'closed', 'partial'
    metadata: dict


@dataclass
class Trade:
    """Completed trade record."""
    trade_id: int
    mint: str
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    entry_timestamp: str
    exit_timestamp: str
    amount_tokens: float
    amount_usd: float
    pnl_usd: float
    pnl_pct: float
    exit_reason: str
    fees_usd: float
    metadata: dict


class PositionStore:
    """SQLite-backed position and trade storage."""

    # Schema version for migrations
    SCHEMA_VERSION = 1

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        """Initialize the position store.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._local = threading.local()

        # Ensure data directory exists
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        # Initialize schema
        self._init_schema()

        # Migrate from JSON if needed
        self._migrate_from_json()

    def _get_conn(self) -> sqlite3.Connection:
        """Get thread-local database connection."""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path, timeout=30.0)
            self._local.conn.row_factory = sqlite3.Row
            # Enable WAL mode for better concurrent access
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

    def _execute(self, sql: str, params: tuple = ()) -> int:
        """Execute a single SQL statement and return lastrowid."""
        with self._transaction() as conn:
            cur = conn.execute(sql, params)
            return cur.lastrowid

    def _init_schema(self):
        """Create database tables if they don't exist."""
        with self._transaction() as conn:
            # Positions table - current state of each position
            conn.execute('''
                CREATE TABLE IF NOT EXISTS positions (
                    mint TEXT PRIMARY KEY,
                    symbol TEXT,
                    entry_price REAL,
                    entry_timestamp TEXT,
                    amount_tokens REAL DEFAULT 0,
                    amount_usd REAL DEFAULT 0,
                    highest_price_seen REAL DEFAULT 0,
                    de_risked INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'open',
                    metadata TEXT DEFAULT '{}',
                    updated_at TEXT
                )
            ''')

            # Trades table - historical record of all trades
            conn.execute('''
                CREATE TABLE IF NOT EXISTS trades (
                    trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mint TEXT NOT NULL,
                    symbol TEXT,
                    side TEXT NOT NULL,
                    entry_price REAL,
                    exit_price REAL,
                    entry_timestamp TEXT,
                    exit_timestamp TEXT,
                    amount_tokens REAL,
                    amount_usd REAL,
                    pnl_usd REAL,
                    pnl_pct REAL,
                    exit_reason TEXT,
                    fees_usd REAL DEFAULT 0,
                    metadata TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Schema version tracking
            conn.execute('''
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY
                )
            ''')

            # Insert initial version if needed
            cur = conn.execute('SELECT version FROM schema_version')
            if cur.fetchone() is None:
                conn.execute('INSERT INTO schema_version (version) VALUES (?)', (self.SCHEMA_VERSION,))

            # Exhaustion events table - logs CVD divergence, order book collapse, velocity blowoff
            conn.execute('''
                CREATE TABLE IF NOT EXISTS exhaustion_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    mint TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    active INTEGER DEFAULT 1,
                    details_json TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Indexes for common queries
            conn.execute('CREATE INDEX IF NOT EXISTS idx_trades_mint ON trades(mint)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_trades_exit_ts ON trades(exit_timestamp)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_exhaustion_mint ON exhaustion_events(mint)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_exhaustion_ts ON exhaustion_events(ts)')

    def _migrate_from_json(self):
        """Migrate existing positions_state.json to SQLite."""
        if not os.path.exists(LEGACY_JSON_PATH):
            return

        try:
            with open(LEGACY_JSON_PATH, 'r') as f:
                legacy_state = json.load(f)

            if not legacy_state:
                return

            migrated = 0
            with self._transaction() as conn:
                for mint, state in legacy_state.items():
                    # Check if already migrated
                    cur = conn.execute('SELECT mint FROM positions WHERE mint = ?', (mint,))
                    if cur.fetchone() is not None:
                        continue

                    # Insert position
                    conn.execute('''
                        INSERT INTO positions (
                            mint, symbol, entry_price, entry_timestamp,
                            amount_tokens, amount_usd, highest_price_seen,
                            de_risked, status, metadata, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        mint,
                        state.get('symbol', mint[:8]),
                        state.get('entry_price', 0),
                        state.get('entry_timestamp', datetime.now().isoformat()),
                        state.get('amount_tokens', 0),
                        state.get('amount_usd', 0),
                        state.get('highest_price_seen', 0),
                        1 if state.get('de_risked') else 0,
                        'open' if state.get('entry_price') else 'closed',
                        json.dumps(state.get('metadata', {})),
                        datetime.now().isoformat(),
                    ))
                    migrated += 1

            if migrated > 0:
                # Rename old file to backup
                backup_path = LEGACY_JSON_PATH + '.migrated'
                os.rename(LEGACY_JSON_PATH, backup_path)
                print(f"[PositionStore] Migrated {migrated} positions from JSON to SQLite")
                print(f"[PositionStore] Backup saved to {backup_path}")

        except Exception as e:
            print(f"[PositionStore] Warning: Failed to migrate from JSON: {e}")

    # =========================================================================
    # Position API (drop-in replacement for nice_funcs.py)
    # =========================================================================

    def get_position(self, mint: str) -> Optional[dict]:
        """Get position state for a mint address.

        Returns dict compatible with legacy positions_state.json format.
        """
        conn = self._get_conn()
        cur = conn.execute('SELECT * FROM positions WHERE mint = ?', (mint,))
        row = cur.fetchone()

        if row is None:
            return None

        return {
            'mint': row['mint'],
            'symbol': row['symbol'],
            'entry_price': row['entry_price'],
            'entry_timestamp': row['entry_timestamp'],
            'amount_tokens': row['amount_tokens'],
            'amount_usd': row['amount_usd'],
            'highest_price_seen': row['highest_price_seen'],
            'de_risked': bool(row['de_risked']),
            'status': row['status'],
            'metadata': json.loads(row['metadata'] or '{}'),
        }

    def get_all_positions(self, status: Optional[str] = None) -> dict:
        """Get all positions, optionally filtered by status.

        Returns dict keyed by mint, compatible with legacy format.
        """
        conn = self._get_conn()

        if status:
            cur = conn.execute('SELECT * FROM positions WHERE status = ?', (status,))
        else:
            cur = conn.execute('SELECT * FROM positions')

        positions = {}
        for row in cur.fetchall():
            positions[row['mint']] = {
                'symbol': row['symbol'],
                'entry_price': row['entry_price'],
                'entry_timestamp': row['entry_timestamp'],
                'amount_tokens': row['amount_tokens'],
                'amount_usd': row['amount_usd'],
                'highest_price_seen': row['highest_price_seen'],
                'de_risked': bool(row['de_risked']),
                'status': row['status'],
                'metadata': json.loads(row['metadata'] or '{}'),
            }

        return positions

    def update_position(
        self,
        mint: str,
        symbol: Optional[str] = None,
        entry_price: Optional[float] = None,
        amount_tokens: Optional[float] = None,
        amount_usd: Optional[float] = None,
        highest_price_seen: Optional[float] = None,
        de_risked: Optional[bool] = None,
        status: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> bool:
        """Update or create a position.

        Only provided fields are updated; None values are ignored.
        """
        try:
            with self._transaction() as conn:
                # Check if position exists
                cur = conn.execute('SELECT mint FROM positions WHERE mint = ?', (mint,))
                exists = cur.fetchone() is not None

                if exists:
                    # Build dynamic UPDATE
                    updates = []
                    params = []

                    if symbol is not None:
                        updates.append('symbol = ?')
                        params.append(symbol)
                    if entry_price is not None:
                        updates.append('entry_price = ?')
                        params.append(entry_price)
                    if amount_tokens is not None:
                        updates.append('amount_tokens = ?')
                        params.append(amount_tokens)
                    if amount_usd is not None:
                        updates.append('amount_usd = ?')
                        params.append(amount_usd)
                    if highest_price_seen is not None:
                        updates.append('highest_price_seen = ?')
                        params.append(highest_price_seen)
                    if de_risked is not None:
                        updates.append('de_risked = ?')
                        params.append(1 if de_risked else 0)
                    if status is not None:
                        updates.append('status = ?')
                        params.append(status)
                    if metadata is not None:
                        # Merge metadata for existing rows so callers can safely update a subset
                        # without wiping previously captured attributes (run_id, entry features, etc).
                        try:
                            cur = conn.execute('SELECT metadata FROM positions WHERE mint = ?', (mint,))
                            row = cur.fetchone()
                            existing_md = json.loads((row['metadata'] if row else None) or '{}')
                            if not isinstance(existing_md, dict):
                                existing_md = {}
                        except Exception:
                            existing_md = {}
                        try:
                            incoming_md = metadata if isinstance(metadata, dict) else {}
                        except Exception:
                            incoming_md = {}
                        existing_md.update(incoming_md)
                        updates.append('metadata = ?')
                        params.append(json.dumps(existing_md))

                    updates.append('updated_at = ?')
                    params.append(datetime.now().isoformat())
                    params.append(mint)

                    if updates:
                        sql = f"UPDATE positions SET {', '.join(updates)} WHERE mint = ?"
                        conn.execute(sql, params)
                else:
                    # INSERT new position
                    conn.execute('''
                        INSERT INTO positions (
                            mint, symbol, entry_price, entry_timestamp,
                            amount_tokens, amount_usd, highest_price_seen,
                            de_risked, status, metadata, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        mint,
                        symbol or mint[:8],
                        entry_price or 0,
                        datetime.now().isoformat(),
                        amount_tokens or 0,
                        amount_usd or 0,
                        highest_price_seen or 0,
                        1 if de_risked else 0,
                        status or 'open',
                        json.dumps(metadata or {}),
                        datetime.now().isoformat(),
                    ))

            return True
        except Exception as e:
            print(f"[PositionStore] Error updating position: {e}")
            return False

    def close_position(self, mint: str) -> bool:
        """Mark a position as closed."""
        return self.update_position(mint, status='closed')

    def delete_position(self, mint: str) -> bool:
        """Delete a position entirely (use sparingly)."""
        try:
            with self._transaction() as conn:
                conn.execute('DELETE FROM positions WHERE mint = ?', (mint,))
            return True
        except Exception as e:
            print(f"[PositionStore] Error deleting position: {e}")
            return False

    def reap_stale_open_positions(
        self,
        *,
        max_age_hours: float,
        status_from: str = "open",
        status_to: str = "stale",
        limit: int = 10_000,
    ) -> int:
        """Mark very old 'open' positions as stale so they don't wedge dashboards/limits.

        This repo runs many long-lived paper sessions. If the bot crashes or is killed,
        we can end up with positions stuck in `status='open'` indefinitely. The trading
        bot itself tracks active positions in memory, but dashboards and analysis tools
        often query `status='open'`.

        We deliberately do NOT record a synthetic trade here because we do not know the
        real exit price. Callers can still inspect metadata later if needed.

        Returns:
            Number of positions updated.
        """
        try:
            max_age_hours = float(max_age_hours)
        except Exception:
            return 0
        if max_age_hours <= 0:
            return 0

        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        cutoff_iso = cutoff.isoformat()
        now_iso = datetime.now().isoformat()

        conn = self._get_conn()
        cur = conn.execute(
            """
            SELECT mint, metadata
            FROM positions
            WHERE status = ?
              AND COALESCE(updated_at, entry_timestamp) < ?
            ORDER BY COALESCE(updated_at, entry_timestamp) ASC
            LIMIT ?
            """,
            (status_from, cutoff_iso, int(limit)),
        )
        rows = cur.fetchall() or []
        if not rows:
            return 0

        updated = 0
        with self._transaction() as tx:
            for row in rows:
                mint = row["mint"]
                try:
                    md = json.loads(row["metadata"] or "{}")
                except Exception:
                    md = {}
                if not isinstance(md, dict):
                    md = {}
                md.setdefault("stale_prev_status", status_from)
                md["stale_reaped_at"] = now_iso
                md.setdefault("stale_reason", f"reaped_after_{max_age_hours:g}h")
                tx.execute(
                    "UPDATE positions SET status = ?, metadata = ?, updated_at = ? WHERE mint = ?",
                    (status_to, json.dumps(md), now_iso, mint),
                )
                updated += 1

        return updated

    # =========================================================================
    # Trade History API
    # =========================================================================

    def record_trade(
        self,
        mint: str,
        symbol: str,
        side: str,
        entry_price: float,
        exit_price: float,
        amount_usd: float,
        exit_reason: str,
        amount_tokens: Optional[float] = None,
        entry_timestamp: Optional[str] = None,
        fees_usd: float = 0.0,
        metadata: Optional[dict] = None,
    ) -> int:
        """Record a completed trade.

        Returns the trade_id of the new record.
        """
        # Calculate P&L
        if amount_tokens is None and entry_price > 0:
            amount_tokens = amount_usd / entry_price

        exit_amount_usd = (amount_tokens or 0) * exit_price
        pnl_usd = exit_amount_usd - amount_usd - fees_usd
        pnl_pct = (pnl_usd / amount_usd * 100) if amount_usd > 0 else 0

        now = datetime.now().isoformat()

        with self._transaction() as conn:
            cur = conn.execute('''
                INSERT INTO trades (
                    mint, symbol, side, entry_price, exit_price,
                    entry_timestamp, exit_timestamp, amount_tokens, amount_usd,
                    pnl_usd, pnl_pct, exit_reason, fees_usd, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                mint, symbol, side, entry_price, exit_price,
                entry_timestamp or now, now, amount_tokens, amount_usd,
                pnl_usd, pnl_pct, exit_reason, fees_usd,
                json.dumps(metadata or {}),
            ))
            return cur.lastrowid

    def get_trades(
        self,
        days: Optional[int] = None,
        mint: Optional[str] = None,
        limit: int = 1000,
    ) -> list[dict]:
        """Get trade history with optional filters."""
        conn = self._get_conn()

        conditions = []
        params = []

        if days is not None:
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            conditions.append('exit_timestamp >= ?')
            params.append(cutoff)

        if mint is not None:
            conditions.append('mint = ?')
            params.append(mint)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)

        cur = conn.execute(f'''
            SELECT * FROM trades
            {where}
            ORDER BY exit_timestamp DESC
            LIMIT ?
        ''', params)

        trades = []
        for row in cur.fetchall():
            trades.append({
                'trade_id': row['trade_id'],
                'mint': row['mint'],
                'symbol': row['symbol'],
                'side': row['side'],
                'entry_price': row['entry_price'],
                'exit_price': row['exit_price'],
                'entry_timestamp': row['entry_timestamp'],
                'exit_timestamp': row['exit_timestamp'],
                'amount_tokens': row['amount_tokens'],
                'amount_usd': row['amount_usd'],
                'pnl_usd': row['pnl_usd'],
                'pnl_pct': row['pnl_pct'],
                'exit_reason': row['exit_reason'],
                'fees_usd': row['fees_usd'],
                'metadata': json.loads(row['metadata'] or '{}'),
            })

        return trades

    def get_performance_summary(self, days: Optional[int] = None) -> dict:
        """Get aggregate performance metrics."""
        conn = self._get_conn()

        conditions = []
        params = []

        if days is not None:
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            conditions.append('exit_timestamp >= ?')
            params.append(cutoff)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        cur = conn.execute(f'''
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as winners,
                SUM(CASE WHEN pnl_usd <= 0 THEN 1 ELSE 0 END) as losers,
                SUM(pnl_usd) as total_pnl,
                SUM(fees_usd) as total_fees,
                AVG(pnl_usd) as avg_pnl,
                MAX(pnl_usd) as best_trade,
                MIN(pnl_usd) as worst_trade,
                SUM(CASE WHEN pnl_usd > 0 THEN pnl_usd ELSE 0 END) as gross_profit,
                SUM(CASE WHEN pnl_usd < 0 THEN ABS(pnl_usd) ELSE 0 END) as gross_loss
            FROM trades
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
                'net_pnl': 0.0,
                'avg_pnl': 0.0,
                'best_trade': 0.0,
                'worst_trade': 0.0,
                'profit_factor': 0.0,
            }

        total = row['total_trades']
        winners = row['winners'] or 0
        gross_profit = row['gross_profit'] or 0
        gross_loss = row['gross_loss'] or 0

        return {
            'total_trades': total,
            'winners': winners,
            'losers': row['losers'] or 0,
            'win_rate': (winners / total * 100) if total > 0 else 0,
            'total_pnl': row['total_pnl'] or 0,
            'total_fees': row['total_fees'] or 0,
            'net_pnl': (row['total_pnl'] or 0) - (row['total_fees'] or 0),
            'avg_pnl': row['avg_pnl'] or 0,
            'best_trade': row['best_trade'] or 0,
            'worst_trade': row['worst_trade'] or 0,
            'profit_factor': (gross_profit / gross_loss) if gross_loss > 0 else float('inf') if gross_profit > 0 else 0,
        }

    # =========================================================================
    # Compatibility API (drop-in for nice_funcs.py)
    # =========================================================================

    def load_positions_state(self) -> dict:
        """Legacy-compatible: Load all positions as dict."""
        return self.get_all_positions()

    def save_positions_state(self, state: dict) -> bool:
        """Legacy-compatible: Save positions dict.

        Note: This does a full sync - positions not in state will be marked closed.
        """
        try:
            for mint, pos_data in state.items():
                self.update_position(
                    mint=mint,
                    symbol=pos_data.get('symbol'),
                    entry_price=pos_data.get('entry_price'),
                    amount_tokens=pos_data.get('amount_tokens'),
                    amount_usd=pos_data.get('amount_usd'),
                    highest_price_seen=pos_data.get('highest_price_seen'),
                    de_risked=pos_data.get('de_risked'),
                    status=pos_data.get('status', 'open'),
                    metadata=pos_data.get('metadata'),
                )
            return True
        except Exception as e:
            print(f"[PositionStore] Error saving positions state: {e}")
            return False


# =========================================================================
# Module-level singleton for easy import
# =========================================================================

_store: Optional[PositionStore] = None


def get_store() -> PositionStore:
    """Get the global PositionStore instance."""
    global _store
    db_path = _resolve_default_db_path()
    if _store is None:
        _store = PositionStore(db_path=db_path)
    else:
        try:
            current = str(getattr(_store, 'db_path', '') or '')
        except Exception:
            current = ''
        if current != str(db_path):
            _store = PositionStore(db_path=db_path)
    return _store


# Legacy-compatible functions (can be imported directly)
def load_positions_state() -> dict:
    """Load all positions from database."""
    return get_store().load_positions_state()


def save_positions_state(state: dict) -> bool:
    """Save positions to database."""
    return get_store().save_positions_state(state)


def get_position(mint: str) -> Optional[dict]:
    """Get a single position."""
    return get_store().get_position(mint)


def update_position(mint: str, **kwargs) -> bool:
    """Update a position."""
    return get_store().update_position(mint, **kwargs)


def record_trade(**kwargs) -> int:
    """Record a completed trade."""
    return get_store().record_trade(**kwargs)


def get_trades(**kwargs) -> list[dict]:
    """Get trade history."""
    return get_store().get_trades(**kwargs)


def get_performance_summary(**kwargs) -> dict:
    """Get performance metrics."""
    return get_store().get_performance_summary(**kwargs)
