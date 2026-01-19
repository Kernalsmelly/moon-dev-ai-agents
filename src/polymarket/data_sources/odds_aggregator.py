"""
Odds Aggregator

Compares prices across prediction markets:
- Polymarket
- PredictIt
- Kalshi
- Metaculus (probability estimates)

Cross-market arbitrage is the purest form of edge:
if the same event is priced differently on two platforms,
there's guaranteed profit by buying low and selling high.
"""

import os
import httpx
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, List
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

# API endpoints
PREDICTIT_API = "https://www.predictit.org/api/marketdata/all"
KALSHI_API = "https://trading-api.kalshi.com/trade-api/v2/markets"
METACULUS_API = "https://www.metaculus.com/api2/questions"


class OddsAggregator:
    """
    Cross-platform prediction market aggregator.

    Finds price discrepancies between:
    1. Polymarket vs PredictIt (for political markets)
    2. Polymarket vs Kalshi (for event markets)
    3. Market price vs Metaculus consensus (for probability check)

    Edge types:
    - Pure arbitrage: Same event, different price
    - Soft arbitrage: Similar events, price divergence
    - Model edge: Market price vs expert consensus
    """

    def __init__(self):
        self.client = httpx.Client(timeout=20.0)
        self.predictit_cache = None
        self.predictit_cache_time = None
        self.cache_ttl = 300  # 5 minute cache

    def get_probability(self, market: Dict) -> Optional[Dict]:
        """
        Get cross-platform probability comparison.
        """
        question = (market.get('question') or '').lower()
        market_price = market.get('yes_price', 0.5)

        # Try to find this market on other platforms
        results = []

        # Check PredictIt
        predictit_prob = self._get_predictit_probability(question)
        if predictit_prob:
            results.append(('PredictIt', predictit_prob))

        # Check Metaculus
        metaculus_prob = self._get_metaculus_probability(question)
        if metaculus_prob:
            results.append(('Metaculus', metaculus_prob))

        if not results:
            return None

        # Calculate average external probability
        avg_prob = sum(p for _, p in results) / len(results)
        sources = ', '.join(s for s, _ in results)

        # Calculate edge vs Polymarket
        edge = abs(avg_prob - market_price)

        return {
            'source': f'Cross-Platform ({sources})',
            'probability': avg_prob,
            'confidence': min(0.9, 0.5 + len(results) * 0.2),
            'edge_vs_market': edge,
            'reasoning': f"Average of {len(results)} platforms: {avg_prob:.1%} vs Polymarket {market_price:.1%}",
            'last_updated': datetime.now(timezone.utc),
        }

    def _get_predictit_probability(self, question: str) -> Optional[float]:
        """Get probability from PredictIt."""
        try:
            # Refresh cache if needed
            if self._should_refresh_predictit_cache():
                response = self.client.get(PREDICTIT_API)
                if response.status_code == 200:
                    self.predictit_cache = response.json().get('markets', [])
                    self.predictit_cache_time = datetime.now(timezone.utc)
                else:
                    return None

            if not self.predictit_cache:
                return None

            # Find matching market
            best_match = None
            best_score = 0

            for pi_market in self.predictit_cache:
                pi_question = pi_market.get('name', '').lower()

                # Calculate similarity
                score = SequenceMatcher(None, question, pi_question).ratio()

                if score > best_score and score > 0.5:
                    best_score = score
                    best_match = pi_market

            if best_match:
                # Get Yes price from first contract
                contracts = best_match.get('contracts', [])
                if contracts:
                    # Find YES contract
                    for contract in contracts:
                        if contract.get('name', '').lower() in ['yes', 'democrat', 'democratic']:
                            yes_price = contract.get('lastTradePrice') or contract.get('bestBuyYesCost')
                            if yes_price:
                                return yes_price
                        elif contract.get('name', '').lower() in ['no', 'republican']:
                            no_price = contract.get('lastTradePrice') or contract.get('bestBuyYesCost')
                            if no_price:
                                return 1 - no_price

            return None

        except Exception as e:
            logger.error(f"PredictIt API error: {e}")
            return None

    def _should_refresh_predictit_cache(self) -> bool:
        """Check if PredictIt cache needs refresh."""
        if self.predictit_cache is None:
            return True
        if self.predictit_cache_time is None:
            return True
        elapsed = (datetime.now(timezone.utc) - self.predictit_cache_time).seconds
        return elapsed > self.cache_ttl

    def _get_metaculus_probability(self, question: str) -> Optional[float]:
        """Get probability from Metaculus."""
        try:
            # Search Metaculus questions
            params = {
                'search': question[:100],
                'status': 'open',
                'type': 'forecast',
                'limit': 5,
            }

            response = self.client.get(METACULUS_API, params=params)
            if response.status_code != 200:
                return None

            data = response.json()
            questions = data.get('results', [])

            # Find best matching question
            best_match = None
            best_score = 0

            for q in questions:
                mc_title = q.get('title', '').lower()
                score = SequenceMatcher(None, question, mc_title).ratio()

                if score > best_score and score > 0.5:
                    best_score = score
                    best_match = q

            if best_match:
                # Metaculus community prediction
                prediction = best_match.get('community_prediction', {})
                if isinstance(prediction, dict):
                    prob = prediction.get('full', {}).get('q2')  # Median
                    if prob:
                        return prob
                elif isinstance(prediction, (int, float)):
                    return prediction

            return None

        except Exception as e:
            logger.error(f"Metaculus API error: {e}")
            return None

    def find_cross_platform_arbitrage(
        self,
        polymarket_markets: List[Dict],
        min_edge: float = 0.05,
    ) -> List[Dict]:
        """
        Find arbitrage opportunities across platforms.

        Returns markets where Polymarket price differs significantly
        from other platforms.
        """
        opportunities = []

        for market in polymarket_markets:
            external = self.get_probability(market)

            if not external:
                continue

            edge = external.get('edge_vs_market', 0)
            if edge >= min_edge:
                opportunities.append({
                    'market': market,
                    'polymarket_price': market.get('yes_price'),
                    'external_price': external.get('probability'),
                    'edge_pct': edge * 100,
                    'source': external.get('source'),
                    'recommended_side': 'YES' if external['probability'] > market.get('yes_price', 0.5) else 'NO',
                    'reasoning': external.get('reasoning'),
                })

        # Sort by edge
        opportunities.sort(key=lambda x: x['edge_pct'], reverse=True)

        return opportunities

    def close(self):
        """Close HTTP client."""
        self.client.close()


# Convenience function
def compare_market_prices(market: Dict) -> Optional[Dict]:
    """Quick comparison of a market across platforms."""
    aggregator = OddsAggregator()
    try:
        return aggregator.get_probability(market)
    finally:
        aggregator.close()
