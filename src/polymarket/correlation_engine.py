"""
Polymarket Correlation Engine

Detects pricing discrepancies across related markets for statistical arbitrage.

Types of correlations:
1. Complementary: "Trump wins" + "Trump loses" should sum to ~$1.00
2. Mutually exclusive: Multiple candidates in same race
3. Causally linked: "Event X happens" affects "Event Y outcome"

Arbitrage opportunity exists when:
- Sum of complementary YES prices != 1.0
- Sum of mutually exclusive outcomes != 1.0
- Causally linked markets are mispriced relative to each other

Example:
- "Trump wins GOP primary" YES = $0.85
- "Trump loses GOP primary" YES = $0.20
- Sum = $1.05 -> 5% arbitrage opportunity
- Buy NO on "loses" at $0.80, sell YES on "wins" at $0.85
- Lock in $0.05 profit regardless of outcome
"""

import re
from typing import List, Dict, Tuple, Optional
from datetime import datetime, timezone
import logging
from difflib import SequenceMatcher

from src.config.polymarket_micro_config import ARBITRAGE_THRESHOLD

logger = logging.getLogger(__name__)


class CorrelationEngine:
    """
    Finds pricing discrepancies across related Polymarket markets.

    This is statistical arbitrage for prediction markets:
    - Find markets that should be correlated
    - Detect when prices deviate from expected relationship
    - Generate signals to capture the spread
    """

    def __init__(self, arb_threshold: float = None):
        self.arb_threshold = arb_threshold or ARBITRAGE_THRESHOLD

    def find_complementary_markets(
        self,
        markets: List[Dict],
        similarity_threshold: float = 0.7,
    ) -> List[Tuple[Dict, Dict]]:
        """
        Find market pairs that are complementary (YES/NO of same question).

        Many markets on Polymarket have explicit YES/NO pairs, but some
        are created separately (e.g., by different users). This finds
        markets that SHOULD be complementary based on question similarity.

        Returns list of (market_a, market_b) tuples.
        """
        pairs = []
        seen_pairs = set()

        # Common patterns that indicate complementary questions
        positive_patterns = [
            r'will (.+) win',
            r'will (.+) happen',
            r'(.+) to win',
            r'(.+) above',
            r'(.+) over',
            r'(.+) yes',
            r'(.+) before',
        ]

        negative_patterns = [
            r'will (.+) lose',
            r"won't (.+) happen",
            r'(.+) to lose',
            r'(.+) below',
            r'(.+) under',
            r'(.+) no',
            r'(.+) after',
        ]

        for i, market_a in enumerate(markets):
            q_a = (market_a.get('question') or '').lower()

            for j, market_b in enumerate(markets):
                if i >= j:  # Avoid duplicates
                    continue

                q_b = (market_b.get('question') or '').lower()

                # Skip if already paired
                pair_key = tuple(sorted([
                    market_a.get('condition_id'),
                    market_b.get('condition_id'),
                ]))
                if pair_key in seen_pairs:
                    continue

                # Check if questions are complementary
                if self._are_complementary(q_a, q_b, similarity_threshold):
                    seen_pairs.add(pair_key)
                    pairs.append((market_a, market_b))

        return pairs

    def _are_complementary(
        self,
        question_a: str,
        question_b: str,
        threshold: float,
    ) -> bool:
        """
        Check if two questions are complementary (opposite outcomes).

        Examples of complementary pairs:
        - "Will X win?" and "Will X lose?"
        - "BTC above $50k?" and "BTC below $50k?"
        """
        # Check for explicit opposite keywords
        opposite_pairs = [
            ('win', 'lose'),
            ('above', 'below'),
            ('over', 'under'),
            ('yes', 'no'),
            ('before', 'after'),
            ('will', "won't"),
            ('higher', 'lower'),
            ('more', 'less'),
        ]

        for pos, neg in opposite_pairs:
            if pos in question_a and neg in question_b:
                # Check if rest of question is similar
                q_a_clean = question_a.replace(pos, '').strip()
                q_b_clean = question_b.replace(neg, '').strip()
                similarity = SequenceMatcher(None, q_a_clean, q_b_clean).ratio()
                if similarity >= threshold:
                    return True

            if neg in question_a and pos in question_b:
                q_a_clean = question_a.replace(neg, '').strip()
                q_b_clean = question_b.replace(pos, '').strip()
                similarity = SequenceMatcher(None, q_a_clean, q_b_clean).ratio()
                if similarity >= threshold:
                    return True

        return False

    def find_mutually_exclusive_markets(
        self,
        markets: List[Dict],
        event_slug: Optional[str] = None,
    ) -> List[List[Dict]]:
        """
        Find groups of markets that are mutually exclusive.

        For example, in a race with 5 candidates:
        - Sum of all "X wins" markets should = 1.0
        - If sum > 1.0, there's arbitrage

        Returns list of market groups (each group is mutually exclusive).
        """
        groups = []

        if event_slug:
            # Filter by event slug
            event_markets = [
                m for m in markets
                if m.get('slug') == event_slug or m.get('event_slug') == event_slug
            ]
            if len(event_markets) > 1:
                groups.append(event_markets)
        else:
            # Group by similar event slugs
            slug_groups = {}
            for market in markets:
                slug = market.get('slug') or market.get('event_slug') or ''
                if not slug:
                    continue

                # Extract base event (before specific outcome)
                base_slug = self._extract_base_slug(slug)
                if base_slug not in slug_groups:
                    slug_groups[base_slug] = []
                slug_groups[base_slug].append(market)

            # Only include groups with 2+ markets
            for base_slug, group_markets in slug_groups.items():
                if len(group_markets) >= 2:
                    groups.append(group_markets)

        return groups

    def _extract_base_slug(self, slug: str) -> str:
        """Extract base event slug from a market slug."""
        # Remove common suffixes like "-winner", "-yes", "-no", etc.
        patterns = [
            r'-winner$', r'-yes$', r'-no$', r'-outcome$',
            r'-result$', r'-[a-z]+-wins$',
        ]

        base = slug.lower()
        for pattern in patterns:
            base = re.sub(pattern, '', base)

        return base

    def detect_arbitrage(
        self,
        market_a: Dict,
        market_b: Dict,
        correlation_type: str = 'complementary',
    ) -> Optional[Dict]:
        """
        Check if a pair of correlated markets has arbitrage opportunity.

        Args:
            market_a: First market
            market_b: Second market
            correlation_type: 'complementary' (should sum to 1) or 'inverse'

        Returns:
            Arbitrage opportunity dict or None if no opportunity.
        """
        yes_a = market_a.get('yes_price') or 0.5
        yes_b = market_b.get('yes_price') or 0.5

        if correlation_type == 'complementary':
            # YES_A + NO_B should = 1.0 (where NO_B = 1 - YES_B)
            # So YES_A + YES_B should = 1.0 if they're perfect complements
            # But often we want YES_A + (1-YES_B) = 1, meaning YES_A = YES_B
            # Actually for true complements: YES_A should = NO_B = 1 - YES_B

            # Check if "A wins" + "A loses" sums to 1
            expected_sum = 1.0
            actual_sum = yes_a + (1 - yes_b)  # YES_A + NO_B

            # Alternative: maybe they're the same question framed differently
            # In that case YES_A + YES_B = 1
            alt_sum = yes_a + yes_b

        elif correlation_type == 'inverse':
            # If A happens, B doesn't (and vice versa)
            expected_sum = 1.0
            actual_sum = yes_a + yes_b
            alt_sum = actual_sum
        else:
            return None

        # Check for deviation
        deviation = abs(actual_sum - expected_sum)
        alt_deviation = abs(alt_sum - expected_sum)

        # Use whichever interpretation shows more deviation
        if alt_deviation > deviation:
            actual_sum = alt_sum
            deviation = alt_deviation

        if deviation < self.arb_threshold:
            return None

        # Calculate profit opportunity
        arb_pct = deviation * 100

        return {
            'market_a': {
                'condition_id': market_a.get('condition_id'),
                'question': market_a.get('question'),
                'yes_price': yes_a,
            },
            'market_b': {
                'condition_id': market_b.get('condition_id'),
                'question': market_b.get('question'),
                'yes_price': yes_b,
            },
            'correlation_type': correlation_type,
            'expected_sum': expected_sum,
            'actual_sum': round(actual_sum, 4),
            'deviation': round(deviation, 4),
            'arbitrage_pct': round(arb_pct, 2),
            'is_opportunity': True,
            'strategy': self._suggest_arb_strategy(
                market_a, market_b, yes_a, yes_b, actual_sum,
            ),
        }

    def _suggest_arb_strategy(
        self,
        market_a: Dict,
        market_b: Dict,
        yes_a: float,
        yes_b: float,
        actual_sum: float,
    ) -> str:
        """Generate a strategy description for the arbitrage."""
        if actual_sum > 1.0:
            # Prices too high - sell both
            return (
                f"SELL: Both YES positions are overpriced (sum={actual_sum:.2f}). "
                f"Sell YES on '{market_a.get('question', '')[:50]}...' at ${yes_a:.2f} "
                f"and sell YES on '{market_b.get('question', '')[:50]}...' at ${yes_b:.2f}. "
                f"Expected profit: ~{(actual_sum - 1) * 100:.1f}% locked in."
            )
        else:
            # Prices too low - buy both
            return (
                f"BUY: Both YES positions are underpriced (sum={actual_sum:.2f}). "
                f"Buy YES on both markets to lock in guaranteed return."
            )

    def detect_group_arbitrage(
        self,
        markets: List[Dict],
    ) -> Optional[Dict]:
        """
        Check if a group of mutually exclusive markets has arbitrage.

        For N mutually exclusive outcomes, sum of YES prices should = 1.0.
        If sum > 1.0: sell all outcomes
        If sum < 1.0: buy all outcomes
        """
        if len(markets) < 2:
            return None

        total_yes = sum(m.get('yes_price', 0) or 0 for m in markets)
        expected = 1.0
        deviation = abs(total_yes - expected)

        if deviation < self.arb_threshold:
            return None

        return {
            'markets': [
                {
                    'condition_id': m.get('condition_id'),
                    'question': m.get('question'),
                    'yes_price': m.get('yes_price'),
                }
                for m in markets
            ],
            'num_markets': len(markets),
            'expected_sum': expected,
            'actual_sum': round(total_yes, 4),
            'deviation': round(deviation, 4),
            'arbitrage_pct': round(deviation * 100, 2),
            'is_opportunity': True,
            'strategy': (
                'SELL all outcomes' if total_yes > 1.0
                else 'BUY all outcomes'
            ),
        }

    def find_all_arbitrage_opportunities(
        self,
        markets: List[Dict],
        min_arb_pct: float = None,
        max_arb_pct: float = 25.0,  # Cap to filter suspicious results
        min_sum: float = 0.50,  # Filter out groups of long-shots
    ) -> List[Dict]:
        """
        Scan all markets for arbitrage opportunities.

        Checks both pair-wise complementary markets and mutually exclusive groups.
        """
        min_arb_pct = min_arb_pct or (self.arb_threshold * 100)
        opportunities = []

        # 1. Find complementary pairs
        pairs = self.find_complementary_markets(markets)
        for market_a, market_b in pairs:
            # Skip if either market is a long-shot (< 15% or > 85%)
            yes_a = market_a.get('yes_price', 0.5)
            yes_b = market_b.get('yes_price', 0.5)
            if yes_a < 0.15 or yes_a > 0.85 or yes_b < 0.15 or yes_b > 0.85:
                continue

            arb = self.detect_arbitrage(market_a, market_b, 'complementary')
            if arb and min_arb_pct <= arb['arbitrage_pct'] <= max_arb_pct:
                opportunities.append(arb)

        # 2. Find mutually exclusive groups
        groups = self.find_mutually_exclusive_markets(markets)
        for group in groups:
            # Skip groups where total sum is too low (just long-shot markets)
            total_yes = sum(m.get('yes_price', 0) or 0 for m in group)
            if total_yes < min_sum:
                continue

            arb = self.detect_group_arbitrage(group)
            if arb and min_arb_pct <= arb['arbitrage_pct'] <= max_arb_pct:
                opportunities.append(arb)

        # Sort by arbitrage percentage
        opportunities.sort(key=lambda x: x.get('arbitrage_pct', 0), reverse=True)

        return opportunities

    def estimate_arbitrage_profit(
        self,
        opportunity: Dict,
        position_size: float,
    ) -> Dict:
        """
        Calculate expected profit from an arbitrage opportunity.
        """
        arb_pct = opportunity.get('arbitrage_pct', 0)
        profit_usd = position_size * (arb_pct / 100)

        return {
            'position_size': position_size,
            'arbitrage_pct': arb_pct,
            'expected_profit': round(profit_usd, 2),
            'risk': 'Low (locked in if executed properly)',
            'execution_notes': (
                'Execute both legs simultaneously to lock in profit. '
                'Price may move between executions on thin markets.'
            ),
        }


# Convenience function
def find_arbitrage(markets: List[Dict]) -> List[Dict]:
    """Quick scan for arbitrage opportunities."""
    engine = CorrelationEngine()
    return engine.find_all_arbitrage_opportunities(markets)
