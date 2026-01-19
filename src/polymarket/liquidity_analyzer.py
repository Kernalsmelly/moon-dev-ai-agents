"""
Polymarket Liquidity Analyzer

INVERTS the logic from polymarket_agent.py:
- Original agent filters for trades >$500 (big money)
- We target markets with LOW liquidity (<$5k) that big bots ignore

This module:
1. Identifies low-liquidity markets
2. Calculates price impact for micro positions
3. Finds optimal entry points where we can get filled without slippage
"""

from typing import List, Dict, Optional, Tuple
from datetime import datetime, timezone

from src.config.polymarket_micro_config import (
    MAX_LIQUIDITY_TARGET,
    MIN_LIQUIDITY_FLOOR,
    MICRO_POSITION_SIZE_USD,
)


class LiquidityAnalyzer:
    """
    Finds and analyzes low-liquidity markets for micro-edge trading.

    Key insight: Markets with <$5k liquidity are ignored by big bots
    because they can't size in without moving the price. But for $10-25
    positions, these markets offer pristine alpha.
    """

    def __init__(
        self,
        max_liquidity: float = None,
        min_liquidity: float = None,
        default_position_size: float = None,
    ):
        self.max_liquidity = max_liquidity or MAX_LIQUIDITY_TARGET
        self.min_liquidity = min_liquidity or MIN_LIQUIDITY_FLOOR
        self.default_position_size = default_position_size or MICRO_POSITION_SIZE_USD

    def is_low_liquidity(self, market: Dict) -> bool:
        """
        Check if market qualifies as low-liquidity target.

        Criteria:
        - Liquidity under max threshold (default $5k)
        - Liquidity above min floor (default $100, to ensure tradeable)
        """
        liquidity = market.get('liquidity', 0) or 0
        return self.min_liquidity <= liquidity <= self.max_liquidity

    def calculate_liquidity_score(self, market: Dict) -> float:
        """
        Calculate liquidity attractiveness score (0-1).

        Lower liquidity = higher score (more opportunity).
        Score of 0 if outside target range.
        """
        liquidity = market.get('liquidity', 0) or 0

        if liquidity < self.min_liquidity:
            return 0.0  # Too illiquid to trade

        if liquidity > self.max_liquidity:
            return 0.0  # Too liquid (big bots will be there)

        # Linear score: lower liquidity = higher score
        # At min_liquidity: score = 1.0
        # At max_liquidity: score = 0.1
        range_size = self.max_liquidity - self.min_liquidity
        position_in_range = liquidity - self.min_liquidity
        score = 1.0 - (position_in_range / range_size) * 0.9

        return round(score, 3)

    def estimate_price_impact(
        self,
        market: Dict,
        position_size: float = None,
        orderbook: Optional[Dict] = None,
    ) -> Dict:
        """
        Estimate price impact for a given position size.

        For micro positions ($10-25), impact should be negligible on
        markets with >$100 liquidity. But we calculate it anyway
        to ensure we're not taking too large a bite.

        Returns:
            {
                'position_size': float,
                'estimated_impact_pct': float,
                'is_viable': bool,
                'recommendation': str
            }
        """
        position_size = position_size or self.default_position_size
        liquidity = market.get('liquidity', 0) or 0

        if liquidity <= 0:
            return {
                'position_size': position_size,
                'estimated_impact_pct': 100.0,
                'is_viable': False,
                'recommendation': 'No liquidity - avoid',
            }

        # Simple impact model: impact % = (position_size / liquidity) * impact_factor
        # For prediction markets, impact factor ~5-10x the ratio
        impact_factor = 5.0  # Conservative estimate
        impact_pct = (position_size / liquidity) * impact_factor * 100

        # If we have orderbook data, use it for more accurate estimate
        if orderbook:
            impact_pct = self._calculate_orderbook_impact(orderbook, position_size)

        is_viable = impact_pct < 5.0  # Less than 5% impact is acceptable

        if impact_pct < 1.0:
            recommendation = f"Excellent: ${position_size} has <1% impact"
        elif impact_pct < 5.0:
            recommendation = f"Good: ${position_size} has {impact_pct:.1f}% impact"
        elif impact_pct < 10.0:
            recommendation = f"Marginal: Consider smaller size (impact {impact_pct:.1f}%)"
        else:
            recommendation = f"Poor: Impact {impact_pct:.1f}% - reduce size or skip"

        return {
            'position_size': position_size,
            'estimated_impact_pct': round(impact_pct, 2),
            'is_viable': is_viable,
            'recommendation': recommendation,
        }

    def _calculate_orderbook_impact(
        self,
        orderbook: Dict,
        position_size: float,
    ) -> float:
        """
        Calculate actual price impact from orderbook depth.

        Simulates walking the book for given size.
        """
        # Assume we're buying YES
        asks = orderbook.get('asks', [])

        if not asks:
            return 100.0  # No asks = can't estimate

        # Sort asks by price ascending
        sorted_asks = sorted(asks, key=lambda x: float(x.get('price', 0)))

        remaining_size = position_size
        weighted_price = 0.0
        total_filled = 0.0
        best_ask = float(sorted_asks[0].get('price', 0)) if sorted_asks else 0

        for ask in sorted_asks:
            price = float(ask.get('price', 0))
            size = float(ask.get('size', 0))
            ask_value = price * size

            if ask_value >= remaining_size:
                # Partial fill at this level
                fill_size = remaining_size / price
                weighted_price += price * fill_size
                total_filled += fill_size
                remaining_size = 0
                break
            else:
                # Take entire level
                weighted_price += price * size
                total_filled += size
                remaining_size -= ask_value

        if total_filled <= 0 or best_ask <= 0:
            return 100.0

        avg_fill_price = weighted_price / total_filled
        impact_pct = ((avg_fill_price - best_ask) / best_ask) * 100

        return max(0, impact_pct)

    def find_low_liquidity_markets(
        self,
        markets: List[Dict],
        min_score: float = 0.3,
    ) -> List[Dict]:
        """
        Filter and rank markets by liquidity attractiveness.

        Returns markets sorted by liquidity score (most attractive first).
        """
        targets = []

        for market in markets:
            if not self.is_low_liquidity(market):
                continue

            score = self.calculate_liquidity_score(market)
            if score < min_score:
                continue

            impact = self.estimate_price_impact(market)
            if not impact['is_viable']:
                continue

            market['liquidity_score'] = score
            market['price_impact'] = impact
            targets.append(market)

        # Sort by liquidity score descending
        targets.sort(key=lambda m: m.get('liquidity_score', 0), reverse=True)

        return targets

    def analyze_market_depth(
        self,
        market: Dict,
        orderbook: Dict,
    ) -> Dict:
        """
        Full depth analysis of a market's orderbook.

        Returns:
            {
                'bid_depth': float (total bid liquidity in $),
                'ask_depth': float (total ask liquidity in $),
                'spread': float (bid-ask spread as %),
                'mid_price': float,
                'depth_ratio': float (bid/ask ratio),
                'optimal_entry_price': float,
                'max_viable_size': float
            }
        """
        bids = orderbook.get('bids', [])
        asks = orderbook.get('asks', [])

        # Calculate depths
        bid_depth = sum(float(b.get('price', 0)) * float(b.get('size', 0)) for b in bids)
        ask_depth = sum(float(a.get('price', 0)) * float(a.get('size', 0)) for a in asks)

        # Best bid/ask
        best_bid = max((float(b.get('price', 0)) for b in bids), default=0)
        best_ask = min((float(a.get('price', 0)) for a in asks), default=1)

        mid_price = (best_bid + best_ask) / 2 if best_bid > 0 and best_ask < 1 else None
        spread = ((best_ask - best_bid) / mid_price * 100) if mid_price else None

        # Depth ratio (>1 means more bids than asks = bullish pressure)
        depth_ratio = bid_depth / ask_depth if ask_depth > 0 else float('inf')

        # Optimal entry: slightly better than best bid/ask
        # For buying: place just above best bid
        # For selling: place just below best ask
        optimal_buy_price = best_bid + 0.01 if best_bid > 0 else None
        optimal_sell_price = best_ask - 0.01 if best_ask < 1 else None

        # Max viable size: amount we can trade with <5% impact
        max_viable = self._estimate_max_viable_size(orderbook)

        return {
            'bid_depth': round(bid_depth, 2),
            'ask_depth': round(ask_depth, 2),
            'spread': round(spread, 2) if spread else None,
            'mid_price': round(mid_price, 4) if mid_price else None,
            'best_bid': best_bid,
            'best_ask': best_ask,
            'depth_ratio': round(depth_ratio, 2) if depth_ratio != float('inf') else None,
            'optimal_buy_price': optimal_buy_price,
            'optimal_sell_price': optimal_sell_price,
            'max_viable_size': max_viable,
        }

    def _estimate_max_viable_size(
        self,
        orderbook: Dict,
        max_impact_pct: float = 5.0,
    ) -> float:
        """
        Estimate maximum position size with acceptable price impact.

        Binary search for size that results in exactly max_impact_pct impact.
        """
        asks = orderbook.get('asks', [])

        if not asks:
            return 0.0

        # Start with total ask liquidity as upper bound
        total_ask_value = sum(
            float(a.get('price', 0)) * float(a.get('size', 0))
            for a in asks
        )

        if total_ask_value <= 0:
            return 0.0

        # Binary search
        low, high = 0.0, total_ask_value
        viable_size = 0.0

        for _ in range(20):  # Max iterations
            mid = (low + high) / 2
            impact = self._calculate_orderbook_impact(orderbook, mid)

            if impact <= max_impact_pct:
                viable_size = mid
                low = mid
            else:
                high = mid

            if high - low < 1.0:  # $1 precision
                break

        return round(viable_size, 2)


# Convenience function
def is_micro_viable(market: Dict, position_size: float = 10.0) -> bool:
    """Quick check if market is viable for micro position."""
    analyzer = LiquidityAnalyzer()
    impact = analyzer.estimate_price_impact(market, position_size)
    return impact['is_viable']
