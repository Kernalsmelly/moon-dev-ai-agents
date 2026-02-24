"""
Signal Quality Scorer for Polymarket Micro-Edge Agent

Scores signals based on multiple factors, not just edge percentage.
Higher score = higher quality signal = prioritize execution.

Factors:
1. Edge percentage (higher = better)
2. Confidence from data source (higher = better)
3. Entry price (mid-range better than extremes)
4. Time to resolution (closer = more predictable)
5. Signal type (data-backed > resolution > correlation)
6. Liquidity (enough to enter/exit)
7. Category edge (YOUR historical performance in this category)

Key insight from top performers:
- HyperLiquid0xb: $1.4M profit specializing in MLB
- Axios: 96% win rate in "mention markets"
- The Quant Fund: 58% overall but 75%+ in specialty

Your historical category performance directly affects signal priority.
"""

from typing import Dict, Optional
from datetime import datetime, timezone


class SignalScorer:
    """Score signals by multiple quality factors."""

    # Weights for each factor (sum to 1.0)
    WEIGHTS = {
        'edge': 0.25,           # Edge percentage
        'confidence': 0.15,     # Source confidence
        'entry_price': 0.15,    # Price quality (mid-range preferred)
        'time_factor': 0.10,    # Time to resolution
        'signal_type': 0.10,    # Type of signal
        'liquidity': 0.10,      # Market liquidity
        'category_edge': 0.15,  # YOUR historical edge in this category
    }

    # Signal type scores (data-backed is best)
    SIGNAL_TYPE_SCORES = {
        'data_edge': 1.0,       # Backed by external data
        'resolution': 0.7,      # Near resolution timing
        'correlation': 0.5,     # Statistical correlation
        'news': 0.8,            # News-driven
        'unknown': 0.3,
    }

    def __init__(self):
        self._category_tracker = None

    def _get_category_tracker(self):
        """Lazy load category tracker."""
        if self._category_tracker is None:
            try:
                from src.polymarket.category_performance import get_category_tracker
                self._category_tracker = get_category_tracker()
            except ImportError:
                pass
        return self._category_tracker

    def score_signal(self, signal: Dict) -> Dict:
        """
        Calculate quality score for a signal.

        Returns dict with:
        - quality_score: float (0-100)
        - factor_scores: dict of individual factor scores
        - recommendation: str
        """
        scores = {}

        # 1. Edge score (0-100, capped at 50% edge = 100 score)
        edge_pct = signal.get('edge_pct', 0)
        scores['edge'] = min(edge_pct * 2, 100)  # 50% edge = 100 score

        # 2. Confidence score (0-100)
        confidence = signal.get('confidence', 0.5)
        scores['confidence'] = confidence * 100

        # 3. Entry price score (mid-range is best)
        # Optimal: $0.30-$0.70, penalize extremes
        entry_price = signal.get('entry_price', 0.5)
        if 0.30 <= entry_price <= 0.70:
            scores['entry_price'] = 100
        elif 0.20 <= entry_price < 0.30 or 0.70 < entry_price <= 0.80:
            scores['entry_price'] = 70
        elif 0.10 <= entry_price < 0.20 or 0.80 < entry_price <= 0.90:
            scores['entry_price'] = 40
        else:
            scores['entry_price'] = 20  # Extreme prices

        # 4. Time to resolution score
        # Closer is better (more predictable), but not too close (event risk)
        hours = signal.get('hours_to_resolution', 168)
        if 24 <= hours <= 72:
            scores['time_factor'] = 100  # Sweet spot
        elif 12 <= hours < 24 or 72 < hours <= 168:
            scores['time_factor'] = 70
        elif hours < 12:
            scores['time_factor'] = 40  # Too close, event risk
        else:
            scores['time_factor'] = 50  # Too far out

        # 5. Signal type score
        signal_type = signal.get('signal_type', 'unknown')
        scores['signal_type'] = self.SIGNAL_TYPE_SCORES.get(signal_type, 0.3) * 100

        # 6. Liquidity score (simplified - assume good if we got this far)
        liquidity = signal.get('liquidity', 1000)
        if liquidity >= 5000:
            scores['liquidity'] = 100
        elif liquidity >= 1000:
            scores['liquidity'] = 80
        elif liquidity >= 500:
            scores['liquidity'] = 60
        else:
            scores['liquidity'] = 40

        # 7. Category edge score (YOUR historical performance)
        # This is the key insight from top performers - they specialize
        category = signal.get('category') or signal.get('niche_category', '')
        category_multiplier = 1.0
        category_recommendation = 'neutral'

        tracker = self._get_category_tracker()
        if tracker and category:
            edges = tracker.get_category_edges()
            category_multiplier = edges.get(category.lower(), 1.0)

            # Get recommendation
            recs = tracker.get_recommendations()
            for rec_type, cats in recs.items():
                if category.lower() in [c.lower() for c in cats]:
                    category_recommendation = rec_type
                    break

        # Convert multiplier to score (1.3 = 100, 1.0 = 70, 0.5 = 30)
        if category_multiplier >= 1.3:
            scores['category_edge'] = 100  # Strong historical edge
        elif category_multiplier >= 1.15:
            scores['category_edge'] = 85   # Good historical edge
        elif category_multiplier >= 1.0:
            scores['category_edge'] = 70   # Neutral
        elif category_multiplier >= 0.75:
            scores['category_edge'] = 50   # Below average
        else:
            scores['category_edge'] = 30   # Historically losing

        # Calculate weighted total
        quality_score = sum(
            scores[factor] * weight
            for factor, weight in self.WEIGHTS.items()
        )

        # Store category info for transparency
        signal['_category_multiplier'] = category_multiplier
        signal['_category_recommendation'] = category_recommendation

        # Generate recommendation
        if quality_score >= 80:
            recommendation = "STRONG - High confidence signal"
        elif quality_score >= 60:
            recommendation = "GOOD - Worth executing"
        elif quality_score >= 40:
            recommendation = "MODERATE - Consider carefully"
        else:
            recommendation = "WEAK - Skip or reduce size"

        return {
            'quality_score': round(quality_score, 1),
            'factor_scores': scores,
            'recommendation': recommendation,
            'edge_pct': edge_pct,
            'entry_price': entry_price,
            'signal_type': signal_type,
            'category': category,
            'category_multiplier': category_multiplier,
            'category_recommendation': category_recommendation,
        }

    def rank_signals(self, signals: list) -> list:
        """Rank signals by quality score."""
        scored = []
        for signal in signals:
            score_data = self.score_signal(signal)
            signal['quality_score'] = score_data['quality_score']
            signal['quality_recommendation'] = score_data['recommendation']
            signal['category_multiplier'] = score_data.get('category_multiplier', 1.0)
            signal['category_recommendation'] = score_data.get('category_recommendation', 'neutral')
            scored.append(signal)

        # Sort by quality score descending
        scored.sort(key=lambda x: x.get('quality_score', 0), reverse=True)
        return scored

    def filter_quality_signals(
        self,
        signals: list,
        min_score: float = 50.0,
    ) -> list:
        """Filter to only high-quality signals."""
        ranked = self.rank_signals(signals)
        return [s for s in ranked if s.get('quality_score', 0) >= min_score]


# Convenience functions
_scorer: Optional[SignalScorer] = None


def get_scorer() -> SignalScorer:
    global _scorer
    if _scorer is None:
        _scorer = SignalScorer()
    return _scorer


def score_signal(signal: Dict) -> Dict:
    """Score a single signal."""
    return get_scorer().score_signal(signal)


def rank_signals(signals: list) -> list:
    """Rank signals by quality."""
    return get_scorer().rank_signals(signals)
