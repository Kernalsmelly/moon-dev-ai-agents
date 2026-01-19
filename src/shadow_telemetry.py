#!/usr/bin/env python3
"""Shadow Telemetry: Lightweight logging for shadow trade tracking.

This module provides a simple API to log shadow trades with complete P&L data.
It's designed to be called from brain.py or orchestrator.py with minimal coupling.

Usage:
    from src.shadow_telemetry import log_shadow_entry, log_shadow_exit, close_shadow_trade

    # When entering a shadow position
    trade_id = log_shadow_entry(
        mint='So11111111...',
        symbol='SOL',
        entry_price_usd=150.25,
        amount_tokens=1.5,
        amount_usd=225.38,
    )

    # When exiting (updates the entry record with exit data)
    close_shadow_trade(
        trade_id=trade_id,
        exit_price_usd=165.50,
        exit_reason='TP1',
        fees_usd=0.50,
    )
"""
import csv
import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

# Thread lock for CSV writes
_write_lock = threading.Lock()

# Data directory
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, 'data')
SHADOW_TRADES_CSV = os.path.join(DATA_DIR, 'shadow_trades.csv')

# Ensure data directory exists
os.makedirs(DATA_DIR, exist_ok=True)

# CSV columns
CSV_COLUMNS = [
    'trade_id', 'ts_entry', 'ts_exit', 'mint', 'symbol', 'side',
    'entry_price_usd', 'exit_price_usd', 'amount_tokens', 'amount_usd',
    'exit_amount_usd', 'pnl_usd', 'pnl_pct', 'exit_reason', 'status',
    'fees_usd', 'slippage_pct', 'metadata'
]


@dataclass
class ShadowTradeRecord:
    """Internal representation of a shadow trade."""
    trade_id: str
    ts_entry: str
    ts_exit: Optional[str]
    mint: str
    symbol: str
    side: str
    entry_price_usd: float
    exit_price_usd: Optional[float]
    amount_tokens: float
    amount_usd: float
    exit_amount_usd: Optional[float]
    pnl_usd: Optional[float]
    pnl_pct: Optional[float]
    exit_reason: Optional[str]
    status: str
    fees_usd: float
    slippage_pct: float
    metadata: str  # JSON string


def _ensure_csv_header():
    """Create CSV file with header if it doesn't exist."""
    if not os.path.exists(SHADOW_TRADES_CSV):
        with open(SHADOW_TRADES_CSV, 'w', newline='') as fh:
            writer = csv.writer(fh)
            writer.writerow(CSV_COLUMNS)


def _get_timestamp() -> str:
    """Get current timestamp in ISO format with timezone."""
    try:
        return datetime.now().astimezone().isoformat()
    except Exception:
        return datetime.now().isoformat()


def log_shadow_entry(
    mint: str,
    symbol: str,
    entry_price_usd: float,
    amount_tokens: float,
    amount_usd: float,
    side: str = 'BUY',
    metadata: Optional[dict] = None,
) -> str:
    """Log a new shadow trade entry.

    Args:
        mint: Token mint address
        symbol: Human-readable token symbol
        entry_price_usd: Entry price in USD
        amount_tokens: Number of tokens purchased
        amount_usd: Total USD value of the trade
        side: 'BUY' or 'SELL' (default: 'BUY')
        metadata: Optional dict with additional context

    Returns:
        trade_id: Unique identifier for this trade (use to close it later)
    """
    trade_id = str(uuid.uuid4())[:8]

    record = ShadowTradeRecord(
        trade_id=trade_id,
        ts_entry=_get_timestamp(),
        ts_exit=None,
        mint=mint,
        symbol=symbol,
        side=side,
        entry_price_usd=entry_price_usd,
        exit_price_usd=None,
        amount_tokens=amount_tokens,
        amount_usd=amount_usd,
        exit_amount_usd=None,
        pnl_usd=None,
        pnl_pct=None,
        exit_reason=None,
        status='open',
        fees_usd=0.0,
        slippage_pct=0.0,
        metadata=json.dumps(metadata or {}),
    )

    with _write_lock:
        _ensure_csv_header()
        with open(SHADOW_TRADES_CSV, 'a', newline='') as fh:
            writer = csv.writer(fh)
            writer.writerow([
                record.trade_id, record.ts_entry, record.ts_exit, record.mint,
                record.symbol, record.side, record.entry_price_usd, record.exit_price_usd,
                record.amount_tokens, record.amount_usd, record.exit_amount_usd,
                record.pnl_usd, record.pnl_pct, record.exit_reason, record.status,
                record.fees_usd, record.slippage_pct, record.metadata
            ])

    return trade_id


def log_shadow_exit(
    mint: str,
    symbol: str,
    exit_price_usd: float,
    amount_tokens: float,
    exit_amount_usd: float,
    entry_price_usd: Optional[float] = None,
    exit_reason: Optional[str] = None,
    fees_usd: float = 0.0,
    slippage_pct: float = 0.0,
    metadata: Optional[dict] = None,
) -> str:
    """Log a shadow exit as a complete trade (entry + exit in one record).

    Use this when you don't have a trade_id from a prior entry, or when
    reconstructing trades from signals.

    Args:
        mint: Token mint address
        symbol: Human-readable token symbol
        exit_price_usd: Exit price in USD
        amount_tokens: Number of tokens sold
        exit_amount_usd: Total USD received
        entry_price_usd: Entry price (for P&L calculation), or None if unknown
        exit_reason: Why the exit happened ('TP1', 'TP2', 'SL', 'manual', etc.)
        fees_usd: Estimated transaction fees
        slippage_pct: Estimated slippage percentage
        metadata: Optional dict with additional context

    Returns:
        trade_id: Unique identifier for this trade record
    """
    trade_id = str(uuid.uuid4())[:8]
    ts = _get_timestamp()

    # Calculate P&L if we have entry price
    pnl_usd = None
    pnl_pct = None
    amount_usd = 0.0

    if entry_price_usd is not None and entry_price_usd > 0:
        amount_usd = entry_price_usd * amount_tokens
        pnl_usd = exit_amount_usd - amount_usd - fees_usd
        pnl_pct = (pnl_usd / amount_usd * 100) if amount_usd > 0 else 0.0

    record = ShadowTradeRecord(
        trade_id=trade_id,
        ts_entry=ts,  # Use same timestamp since we don't have actual entry time
        ts_exit=ts,
        mint=mint,
        symbol=symbol,
        side='SELL',
        entry_price_usd=entry_price_usd or 0.0,
        exit_price_usd=exit_price_usd,
        amount_tokens=amount_tokens,
        amount_usd=amount_usd,
        exit_amount_usd=exit_amount_usd,
        pnl_usd=pnl_usd,
        pnl_pct=pnl_pct,
        exit_reason=exit_reason,
        status='closed',
        fees_usd=fees_usd,
        slippage_pct=slippage_pct,
        metadata=json.dumps(metadata or {}),
    )

    with _write_lock:
        _ensure_csv_header()
        with open(SHADOW_TRADES_CSV, 'a', newline='') as fh:
            writer = csv.writer(fh)
            writer.writerow([
                record.trade_id, record.ts_entry, record.ts_exit, record.mint,
                record.symbol, record.side, record.entry_price_usd, record.exit_price_usd,
                record.amount_tokens, record.amount_usd, record.exit_amount_usd,
                record.pnl_usd, record.pnl_pct, record.exit_reason, record.status,
                record.fees_usd, record.slippage_pct, record.metadata
            ])

    return trade_id


def close_shadow_trade(
    trade_id: str,
    exit_price_usd: float,
    exit_reason: Optional[str] = None,
    fees_usd: float = 0.0,
    slippage_pct: float = 0.0,
) -> bool:
    """Close an existing shadow trade by updating its record.

    This reads the CSV, finds the trade by ID, updates it with exit data,
    and rewrites the file. For high-frequency trading, consider using
    log_shadow_exit() instead to avoid file rewrites.

    Args:
        trade_id: The trade_id returned from log_shadow_entry()
        exit_price_usd: Exit price in USD
        exit_reason: Why the exit happened
        fees_usd: Transaction fees
        slippage_pct: Slippage percentage

    Returns:
        True if trade was found and updated, False otherwise
    """
    if not os.path.exists(SHADOW_TRADES_CSV):
        return False

    with _write_lock:
        # Read all rows
        rows = []
        found = False

        with open(SHADOW_TRADES_CSV, 'r', newline='') as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                if row.get('trade_id') == trade_id and row.get('status') == 'open':
                    # Update this row
                    ts_exit = _get_timestamp()
                    entry_price = float(row.get('entry_price_usd', 0) or 0)
                    amount_tokens = float(row.get('amount_tokens', 0) or 0)
                    amount_usd = float(row.get('amount_usd', 0) or 0)

                    exit_amount_usd = exit_price_usd * amount_tokens
                    pnl_usd = exit_amount_usd - amount_usd - fees_usd
                    pnl_pct = (pnl_usd / amount_usd * 100) if amount_usd > 0 else 0.0

                    row['ts_exit'] = ts_exit
                    row['exit_price_usd'] = str(exit_price_usd)
                    row['exit_amount_usd'] = str(exit_amount_usd)
                    row['pnl_usd'] = str(pnl_usd)
                    row['pnl_pct'] = str(pnl_pct)
                    row['exit_reason'] = exit_reason or ''
                    row['status'] = 'closed'
                    row['fees_usd'] = str(fees_usd)
                    row['slippage_pct'] = str(slippage_pct)
                    found = True

                rows.append(row)

        if not found:
            return False

        # Rewrite file
        with open(SHADOW_TRADES_CSV, 'w', newline='') as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

        return True


def get_open_shadow_trades(mint: Optional[str] = None) -> list[dict]:
    """Get all open shadow trades, optionally filtered by mint.

    Args:
        mint: Filter by mint address (optional)

    Returns:
        List of trade dicts with open status
    """
    if not os.path.exists(SHADOW_TRADES_CSV):
        return []

    trades = []
    with open(SHADOW_TRADES_CSV, 'r', newline='') as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if row.get('status') != 'open':
                continue
            if mint and row.get('mint') != mint:
                continue
            trades.append(dict(row))

    return trades


def log_complete_shadow_trade(
    mint: str,
    symbol: str,
    side: str,
    entry_price_usd: float,
    exit_price_usd: float,
    amount_tokens: float,
    amount_usd: float,
    exit_reason: Optional[str] = None,
    fees_usd: float = 0.0,
    slippage_pct: float = 0.0,
    ts_entry: Optional[str] = None,
    ts_exit: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> str:
    """Log a complete shadow trade with both entry and exit data.

    This is the recommended method for logging simulated trades where
    you have all the data available at once.

    Args:
        mint: Token mint address
        symbol: Human-readable token symbol
        side: 'BUY' or 'SELL'
        entry_price_usd: Entry price in USD
        exit_price_usd: Exit price in USD
        amount_tokens: Number of tokens traded
        amount_usd: Entry value in USD
        exit_reason: Why the exit happened
        fees_usd: Total fees paid
        slippage_pct: Actual slippage
        ts_entry: Entry timestamp (ISO format) or None for now
        ts_exit: Exit timestamp (ISO format) or None for now
        metadata: Optional dict with additional context

    Returns:
        trade_id: Unique identifier for this trade record
    """
    trade_id = str(uuid.uuid4())[:8]
    now = _get_timestamp()

    exit_amount_usd = exit_price_usd * amount_tokens
    pnl_usd = exit_amount_usd - amount_usd - fees_usd
    pnl_pct = (pnl_usd / amount_usd * 100) if amount_usd > 0 else 0.0

    record = ShadowTradeRecord(
        trade_id=trade_id,
        ts_entry=ts_entry or now,
        ts_exit=ts_exit or now,
        mint=mint,
        symbol=symbol,
        side=side,
        entry_price_usd=entry_price_usd,
        exit_price_usd=exit_price_usd,
        amount_tokens=amount_tokens,
        amount_usd=amount_usd,
        exit_amount_usd=exit_amount_usd,
        pnl_usd=pnl_usd,
        pnl_pct=pnl_pct,
        exit_reason=exit_reason,
        status='closed',
        fees_usd=fees_usd,
        slippage_pct=slippage_pct,
        metadata=json.dumps(metadata or {}),
    )

    with _write_lock:
        _ensure_csv_header()
        with open(SHADOW_TRADES_CSV, 'a', newline='') as fh:
            writer = csv.writer(fh)
            writer.writerow([
                record.trade_id, record.ts_entry, record.ts_exit, record.mint,
                record.symbol, record.side, record.entry_price_usd, record.exit_price_usd,
                record.amount_tokens, record.amount_usd, record.exit_amount_usd,
                record.pnl_usd, record.pnl_pct, record.exit_reason, record.status,
                record.fees_usd, record.slippage_pct, record.metadata
            ])

    # Also record to SQLite for long-term persistence
    try:
        from src.position_store import record_trade as db_record_trade
        db_record_trade(
            mint=mint,
            symbol=symbol,
            side=side,
            entry_price=entry_price_usd,
            exit_price=exit_price_usd,
            amount_usd=amount_usd,
            amount_tokens=amount_tokens,
            exit_reason=exit_reason or 'shadow',
            fees_usd=fees_usd,
            entry_timestamp=ts_entry,
            metadata={'shadow': True, 'csv_trade_id': trade_id, **(metadata or {})},
        )
    except Exception:
        pass  # SQLite recording is optional enhancement

    return trade_id


# Convenience function for quick P&L logging from brain.py exit events
def log_exit_with_pnl(
    mint: str,
    symbol: str,
    entry_price_usd: float,
    exit_price_usd: float,
    position_usd: float,
    exit_reason: str = 'shadow',
    fees_usd: float = 0.0,
    impact_pct: float = 0.0,
    metadata: Optional[dict] = None,
) -> str:
    """Convenience function to log a shadow exit with P&L calculation.

    Designed to be called from brain.py's exit logic with minimal changes.

    Args:
        mint: Token mint address
        symbol: Token symbol
        entry_price_usd: Price at entry
        exit_price_usd: Price at exit
        position_usd: Total position value in USD
        exit_reason: Exit trigger (TP1, TP2, SL, etc.)
        fees_usd: Estimated fees
        impact_pct: Price impact percentage
        metadata: Additional context

    Returns:
        trade_id: Unique trade identifier
    """
    if entry_price_usd <= 0:
        entry_price_usd = exit_price_usd  # Fallback

    amount_tokens = position_usd / entry_price_usd if entry_price_usd > 0 else 0

    # Calculate P&L for alert
    exit_amount = exit_price_usd * amount_tokens
    pnl_usd = exit_amount - position_usd - fees_usd
    pnl_pct = (pnl_usd / position_usd * 100) if position_usd > 0 else 0

    trade_id = log_complete_shadow_trade(
        mint=mint,
        symbol=symbol,
        side='SELL',
        entry_price_usd=entry_price_usd,
        exit_price_usd=exit_price_usd,
        amount_tokens=amount_tokens,
        amount_usd=position_usd,
        exit_reason=exit_reason,
        fees_usd=fees_usd,
        slippage_pct=impact_pct,
        metadata=metadata,
    )

    # Send Discord alert
    try:
        from src.alerts import send_trade_alert
        send_trade_alert(
            symbol=symbol,
            side='SELL',
            entry_price=entry_price_usd,
            exit_price=exit_price_usd,
            pnl_usd=pnl_usd,
            pnl_pct=pnl_pct,
            exit_reason=exit_reason,
            amount_usd=position_usd,
            mint=mint,
        )
    except Exception:
        pass  # Alerts are optional - don't break trading on alert failure

    return trade_id
