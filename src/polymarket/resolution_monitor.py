"""
Resolution Monitor for Polymarket Micro-Edge Agent

Monitors markets for resolution and auto-calculates PnL for simulated trades.
Fetches final outcomes from Polymarket API and updates our tracking.

Usage:
    from src.polymarket.resolution_monitor import ResolutionMonitor
    monitor = ResolutionMonitor()
    resolved = monitor.check_and_resolve()
"""

import os
import httpx
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from pathlib import Path

from src.polymarket.pnl_tracker import get_tracker

logger = logging.getLogger(__name__)

# Polymarket API endpoints
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

# Database paths
DATA_DIR = Path(__file__).parent.parent / "data" / "polymarket_micro"
MARKETS_DB = DATA_DIR / "niche_markets.db"
PNL_DB = DATA_DIR / "simulated_pnl.db"


class ResolutionMonitor:
    """
    Monitor markets for resolution and calculate PnL.

    When a market resolves:
    1. Fetch final outcome from Polymarket
    2. Calculate PnL for our simulated trades
    3. Update database with results
    """

    def __init__(self):
        self.client = httpx.Client(timeout=30.0)
        self._ensure_db()

    def _ensure_db(self):
        """Ensure database tables exist."""
        conn = sqlite3.connect(PNL_DB)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS resolved_markets (
                condition_id TEXT PRIMARY KEY,
                question TEXT,
                outcome TEXT,  -- 'YES', 'NO', or 'INVALID'
                resolved_at TEXT,
                final_yes_price REAL,
                final_no_price REAL
            )
        ''')
        conn.commit()
        conn.close()

    def check_and_resolve(self) -> List[Dict]:
        """
        Check for resolved markets and update simulated PnL.

        Returns list of newly resolved trades with PnL.
        """
        resolved_trades = []
        tracker = get_tracker()

        # Get pending simulated trades from pnl_tracker
        pending = tracker.get_pending_trades()
        if not pending:
            logger.info("No pending trades to check")
            return []

        # Group by condition_id to minimize API calls
        condition_ids = set(t['condition_id'] for t in pending)
        logger.info(f"Checking {len(condition_ids)} markets for resolution...")

        for condition_id in condition_ids:
            outcome = self._check_market_resolution(condition_id)
            if outcome:
                # Market resolved - use pnl_tracker to resolve all trades
                results = tracker.resolve_by_condition(condition_id, outcome)

                # Get trade details for logging
                trades_for_market = [t for t in pending if t['condition_id'] == condition_id]
                for i, trade in enumerate(trades_for_market):
                    if i < len(results):
                        result = results[i]
                        resolved_trades.append({
                            'trade_id': result.get('trade_id'),
                            'condition_id': condition_id,
                            'question': trade.get('question', ''),
                            'side': trade['side'],
                            'entry_price': trade['entry_price'],
                            'outcome': outcome,
                            'pnl_usd': result.get('pnl_usd', 0),
                            'pnl_pct': result.get('pnl_pct', 0),
                            'was_correct': result.get('was_correct', False)
                        })

        if resolved_trades:
            logger.info(f"Resolved {len(resolved_trades)} trades")
            self._log_resolution_summary(resolved_trades)

        return resolved_trades

    def _check_market_resolution(self, condition_id: str) -> Optional[str]:
        """
        Check if a market has resolved.

        Returns:
            'YES', 'NO', 'INVALID', or None if not resolved
        """
        # Check if we already know the outcome
        conn = sqlite3.connect(PNL_DB)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            'SELECT outcome FROM resolved_markets WHERE condition_id = ?',
            (condition_id,)
        )
        row = cur.fetchone()
        conn.close()

        if row:
            return row['outcome']

        # Fetch from Polymarket API
        try:
            # Try CLOB API for market status
            url = f"{CLOB_API}/markets/{condition_id}"
            resp = self.client.get(url)

            if resp.status_code == 200:
                data = resp.json()

                # Check if resolved
                if data.get('closed') or data.get('resolved'):
                    # Determine outcome from final prices
                    yes_price = float(data.get('outcomePrices', ['0', '0'])[0] or 0)
                    no_price = float(data.get('outcomePrices', ['0', '0'])[1] or 0)

                    if yes_price >= 0.99:
                        outcome = 'YES'
                    elif no_price >= 0.99 or yes_price <= 0.01:
                        outcome = 'NO'
                    else:
                        # Market closed but unclear outcome - check gamma
                        outcome = self._check_gamma_resolution(condition_id)
                        if not outcome:
                            return None

                    # Record resolution
                    self._record_market_resolution(
                        condition_id,
                        data.get('question', ''),
                        outcome,
                        yes_price,
                        no_price
                    )
                    return outcome

            # Try Gamma API as fallback
            return self._check_gamma_resolution(condition_id)

        except Exception as e:
            logger.error(f"Error checking resolution for {condition_id}: {e}")
            return None

    def _check_gamma_resolution(self, condition_id: str) -> Optional[str]:
        """Check Gamma API for market resolution."""
        try:
            url = f"{GAMMA_API}/markets"
            params = {'condition_id': condition_id}
            resp = self.client.get(url, params=params)

            if resp.status_code == 200:
                markets = resp.json()
                if markets:
                    market = markets[0]
                    # Check for resolution indicators
                    if market.get('closed') or market.get('resolved'):
                        # Use outcome prices to determine
                        outcome_prices = market.get('outcomePrices', '')
                        if outcome_prices:
                            try:
                                prices = [float(p) for p in outcome_prices.split(',')]
                                if len(prices) >= 2:
                                    if prices[0] >= 0.99:
                                        return 'YES'
                                    elif prices[1] >= 0.99 or prices[0] <= 0.01:
                                        return 'NO'
                            except:
                                pass
        except Exception as e:
            logger.debug(f"Gamma API check failed: {e}")

        return None

    def _record_market_resolution(
        self,
        condition_id: str,
        question: str,
        outcome: str,
        yes_price: float,
        no_price: float
    ):
        """Record market resolution in database."""
        conn = sqlite3.connect(PNL_DB)
        conn.execute('''
            INSERT OR REPLACE INTO resolved_markets
            (condition_id, question, outcome, resolved_at, final_yes_price, final_no_price)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            condition_id,
            question,
            outcome,
            datetime.now(timezone.utc).isoformat(),
            yes_price,
            no_price
        ))
        conn.commit()
        conn.close()

    def _log_resolution_summary(self, resolved_trades: List[Dict]):
        """Log summary of resolved trades."""
        total_pnl = sum(t['pnl_usd'] for t in resolved_trades)
        wins = sum(1 for t in resolved_trades if t['pnl_usd'] > 0)
        losses = sum(1 for t in resolved_trades if t['pnl_usd'] < 0)

        logger.info(f"Resolution Summary:")
        logger.info(f"  Trades resolved: {len(resolved_trades)}")
        logger.info(f"  Wins: {wins}, Losses: {losses}")
        logger.info(f"  Total PnL: ${total_pnl:.2f}")

        for t in resolved_trades:
            emoji = "✅" if t['pnl_usd'] > 0 else "❌"
            logger.info(f"  {emoji} {t['side']} @ ${t['entry_price']:.2f} -> {t['outcome']}: ${t['pnl_usd']:.2f}")

    def get_resolution_stats(self) -> Dict:
        """Get statistics on resolved markets."""
        tracker = get_tracker()
        summary = tracker.get_performance_summary()

        pending = tracker.get_pending_trades()

        return {
            'resolved': summary.get('total_resolved', 0),
            'pending': len(pending),
            'wins': summary.get('correct', 0),
            'losses': summary.get('wrong', 0),
            'win_rate': summary.get('win_rate', 0),
            'total_pnl': summary.get('total_pnl', 0),
            'avg_pnl': summary.get('avg_pnl', 0)
        }

    def close(self):
        """Close HTTP client."""
        self.client.close()


# Singleton instance
_monitor: Optional[ResolutionMonitor] = None


def get_monitor() -> ResolutionMonitor:
    """Get singleton ResolutionMonitor instance."""
    global _monitor
    if _monitor is None:
        _monitor = ResolutionMonitor()
    return _monitor


def check_resolutions() -> List[Dict]:
    """Check for resolved markets and return PnL updates."""
    return get_monitor().check_and_resolve()


def get_stats() -> Dict:
    """Get resolution statistics."""
    return get_monitor().get_resolution_stats()
