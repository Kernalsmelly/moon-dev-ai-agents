"""
Polymarket Resolution Timer

INVERTS the logic from polymarket_agent.py (line 254-256):
- Original agent EXCLUDES near-resolution prices (within 0.02 of $0 or $1)
- We TARGET markets 24-72 hours from resolution where price hasn't converged

Resolution Timing Edge:
When a market is close to resolution and the outcome is predictable,
but the price hasn't fully converged (e.g., 0.75 when it should be 0.95),
there's free alpha for patient capital.

Big bots avoid these because:
1. Capital efficiency: locked for days earning small %
2. Liquidity: often thin near resolution
3. Event risk: outcome could flip

But for micro capital ($10-25 positions), these are ideal:
- Predictable outcome with known resolution date
- Less competition from sophisticated traders
- Clear expected value calculation
"""

from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple
import logging

from src.config.polymarket_micro_config import (
    RESOLUTION_HOURS_MIN,
    RESOLUTION_HOURS_MAX,
    PRICE_CONVERGENCE_MIN,
    PRICE_CONVERGENCE_MAX,
    MIN_EDGE_PCT,
)

logger = logging.getLogger(__name__)


class ResolutionTimer:
    """
    Finds markets near resolution with unexploited edge.

    The key insight: If a market resolves in 48 hours and the outcome
    is 90% certain (based on external analysis), but the price is only
    at 0.75, there's a 15% edge minus capital lock-up cost.

    For micro positions, the capital lock-up cost is negligible.
    """

    def __init__(
        self,
        min_hours: float = None,
        max_hours: float = None,
        price_min: float = None,
        price_max: float = None,
        min_edge: float = None,
    ):
        self.min_hours = min_hours or RESOLUTION_HOURS_MIN
        self.max_hours = max_hours or RESOLUTION_HOURS_MAX
        self.price_min = price_min or PRICE_CONVERGENCE_MIN
        self.price_max = price_max or PRICE_CONVERGENCE_MAX
        self.min_edge = min_edge or MIN_EDGE_PCT

    def get_hours_to_resolution(self, end_date: Optional[datetime | str]) -> Optional[float]:
        """
        Calculate hours until market resolution.

        Returns None if end_date is invalid or in the past.
        """
        if end_date is None:
            return None

        try:
            if isinstance(end_date, str):
                # Handle various ISO formats
                end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            else:
                end_dt = end_date

            # Ensure timezone aware
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)

            now = datetime.now(timezone.utc)
            delta = end_dt - now

            if delta.total_seconds() <= 0:
                return None  # Already resolved

            return delta.total_seconds() / 3600

        except (ValueError, TypeError) as e:
            logger.debug(f"Failed to parse end_date: {end_date}, error: {e}")
            return None

    def is_near_resolution(self, market: Dict) -> bool:
        """
        Check if market is in our target resolution window.

        Criteria:
        - Resolution is min_hours to max_hours away (default 24-72h)
        - Price is in convergence range (default 0.60-0.95)
        """
        end_date = market.get('end_date')
        hours = self.get_hours_to_resolution(end_date)

        if hours is None:
            return False

        if not (self.min_hours <= hours <= self.max_hours):
            return False

        # Check price convergence
        yes_price = market.get('yes_price')
        no_price = market.get('no_price')

        if yes_price is not None:
            if self.price_min <= yes_price <= self.price_max:
                return True

        if no_price is not None:
            if self.price_min <= no_price <= self.price_max:
                return True

        return False

    def calculate_expected_value(
        self,
        current_price: float,
        expected_outcome: str,
        confidence: float,
    ) -> Dict:
        """
        Calculate expected value of a resolution timing trade.

        Args:
            current_price: Current YES price (0-1)
            expected_outcome: 'YES' or 'NO'
            confidence: Probability of expected outcome (0-1)

        Returns:
            {
                'side': str ('YES' or 'NO'),
                'entry_price': float,
                'expected_exit': float (1.0 if right, 0.0 if wrong),
                'ev_per_dollar': float,
                'edge_pct': float,
                'is_profitable': bool
            }
        """
        if expected_outcome.upper() == 'YES':
            entry_price = current_price
            # If YES wins: receive $1. If NO wins: receive $0.
            expected_payout = confidence * 1.0 + (1 - confidence) * 0.0
        else:  # NO
            entry_price = 1.0 - current_price  # NO price
            # If NO wins: receive $1. If YES wins: receive $0.
            expected_payout = confidence * 1.0 + (1 - confidence) * 0.0

        ev_per_dollar = expected_payout - entry_price
        edge_pct = (ev_per_dollar / entry_price) * 100 if entry_price > 0 else 0

        return {
            'side': expected_outcome.upper(),
            'entry_price': round(entry_price, 4),
            'expected_exit': round(expected_payout, 4),
            'ev_per_dollar': round(ev_per_dollar, 4),
            'edge_pct': round(edge_pct, 2),
            'is_profitable': edge_pct >= self.min_edge,
        }

    def analyze_resolution_opportunity(
        self,
        market: Dict,
        expected_outcome: Optional[str] = None,
        confidence: Optional[float] = None,
    ) -> Dict:
        """
        Full analysis of a resolution timing opportunity.

        If expected_outcome/confidence not provided, we'll analyze
        both sides to see which offers better edge.

        Returns:
            {
                'condition_id': str,
                'question': str,
                'hours_to_resolution': float,
                'yes_price': float,
                'no_price': float,
                'best_side': str,
                'best_edge_pct': float,
                'is_opportunity': bool,
                'analysis': dict
            }
        """
        end_date = market.get('end_date')
        hours = self.get_hours_to_resolution(end_date)

        yes_price = market.get('yes_price') or 0.5
        no_price = market.get('no_price') or (1.0 - yes_price)

        result = {
            'condition_id': market.get('condition_id'),
            'question': market.get('question'),
            'hours_to_resolution': round(hours, 1) if hours else None,
            'yes_price': yes_price,
            'no_price': no_price,
            'best_side': None,
            'best_edge_pct': 0,
            'is_opportunity': False,
            'analysis': {},
        }

        if hours is None:
            result['analysis']['error'] = 'No valid resolution date'
            return result

        if not (self.min_hours <= hours <= self.max_hours):
            result['analysis']['error'] = f'Outside resolution window ({hours:.1f}h)'
            return result

        # If caller provided expected outcome
        if expected_outcome and confidence:
            ev = self.calculate_expected_value(yes_price, expected_outcome, confidence)
            result['best_side'] = ev['side']
            result['best_edge_pct'] = ev['edge_pct']
            result['is_opportunity'] = ev['is_profitable']
            result['analysis'] = ev
            return result

        # Otherwise, analyze both sides
        # For YES: assume market thinks YES is likely (based on price)
        yes_confidence = yes_price + 0.05  # Slight bump from current price
        yes_ev = self.calculate_expected_value(yes_price, 'YES', min(yes_confidence, 0.95))

        # For NO: assume opposite
        no_confidence = no_price + 0.05
        no_ev = self.calculate_expected_value(yes_price, 'NO', min(no_confidence, 0.95))

        result['analysis'] = {
            'yes_analysis': yes_ev,
            'no_analysis': no_ev,
        }

        # Pick the better side
        if yes_ev['edge_pct'] >= no_ev['edge_pct']:
            result['best_side'] = 'YES'
            result['best_edge_pct'] = yes_ev['edge_pct']
            result['is_opportunity'] = yes_ev['is_profitable']
        else:
            result['best_side'] = 'NO'
            result['best_edge_pct'] = no_ev['edge_pct']
            result['is_opportunity'] = no_ev['is_profitable']

        return result

    def find_resolution_opportunities(
        self,
        markets: List[Dict],
        min_edge: float = None,
        max_edge: float = 50.0,  # Cap unrealistic edges
    ) -> List[Dict]:
        """
        Find all markets with resolution timing opportunities.

        Returns markets sorted by edge (best first).
        """
        min_edge = min_edge or self.min_edge
        opportunities = []

        for market in markets:
            if not self.is_near_resolution(market):
                continue

            # Skip lottery tickets (prices < 15% or > 85%)
            yes_price = market.get('yes_price', 0)
            if yes_price < 0.15 or yes_price > 0.85:
                continue

            analysis = self.analyze_resolution_opportunity(market)

            # Skip unrealistic edges (usually calculation artifacts)
            if analysis['best_edge_pct'] > max_edge:
                continue

            if analysis['is_opportunity'] and analysis['best_edge_pct'] >= min_edge:
                market['resolution_analysis'] = analysis
                market['hours_to_resolution'] = analysis['hours_to_resolution']
                market['is_near_resolution'] = True
                opportunities.append(market)

        # Sort by edge descending
        opportunities.sort(
            key=lambda m: m.get('resolution_analysis', {}).get('best_edge_pct', 0),
            reverse=True,
        )

        return opportunities

    def calculate_annualized_return(
        self,
        edge_pct: float,
        hours_to_resolution: float,
    ) -> float:
        """
        Calculate annualized return for a resolution timing trade.

        This helps compare opportunities with different time horizons.
        """
        if hours_to_resolution <= 0:
            return 0.0

        # Hours in a year
        hours_per_year = 365.25 * 24

        # Annualize the edge
        periods_per_year = hours_per_year / hours_to_resolution
        annualized = edge_pct * periods_per_year

        return round(annualized, 2)

    def get_countdown_message(self, hours: float) -> str:
        """Human-readable countdown to resolution."""
        if hours is None:
            return "Unknown"

        if hours < 1:
            return f"{int(hours * 60)} minutes"
        elif hours < 24:
            return f"{hours:.1f} hours"
        elif hours < 48:
            return f"~{hours / 24:.1f} days ({int(hours)} hours)"
        else:
            return f"{hours / 24:.0f} days"


# Convenience function
def find_near_resolution_markets(markets: List[Dict]) -> List[Dict]:
    """Quick filter for near-resolution markets."""
    timer = ResolutionTimer()
    return timer.find_resolution_opportunities(markets)
