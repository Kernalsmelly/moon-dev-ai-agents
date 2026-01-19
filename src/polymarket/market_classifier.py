"""
Polymarket Niche Market Classifier

INVERTS the logic from polymarket_agent.py (lines 38-54):
- Original agent EXCLUDES sports -> We TARGET niche sports
- Original agent EXCLUDES crypto -> We still exclude saturated crypto price markets
- We ADD scoring for obscure categories that big bots ignore

Niche Score (0-10):
- +2 points: Low liquidity (<$5k)
- +3 points: Target niche category
- +2 points: Low volume (<$2k/24h)
- +1 point: Newly created (<48h old)
- +2 points: Not major sport/crypto
"""

import re
from datetime import datetime, timezone, timedelta
from typing import Dict, Tuple, Optional, List

from src.config.polymarket_micro_config import (
    NICHE_SPORTS_INCLUDE,
    NICHE_CATEGORIES_TARGET,
    CATEGORIES_EXCLUDE,
    MAX_LIQUIDITY_TARGET,
    MAX_VOLUME_24H,
    MIN_NICHE_SCORE,
)


class NicheClassifier:
    """
    Classifies markets by "niche score" - higher score = less competition.

    This is the INVERSE of polymarket_agent.py which filters OUT niche markets.
    We WANT the markets that big bots ignore.
    """

    def __init__(
        self,
        niche_sports: List[str] = None,
        niche_categories: List[str] = None,
        exclude_categories: List[str] = None,
        max_liquidity: float = None,
        max_volume_24h: float = None,
    ):
        self.niche_sports = [s.lower() for s in (niche_sports or NICHE_SPORTS_INCLUDE)]
        self.niche_categories = [c.lower() for c in (niche_categories or NICHE_CATEGORIES_TARGET)]
        self.exclude_categories = [c.lower() for c in (exclude_categories or CATEGORIES_EXCLUDE)]
        self.max_liquidity = max_liquidity or MAX_LIQUIDITY_TARGET
        self.max_volume_24h = max_volume_24h or MAX_VOLUME_24H

    def calculate_niche_score(self, market: Dict) -> float:
        """
        Calculate niche score (0-10). Higher = more niche = more edge potential.

        Scoring breakdown:
        - +2 points: Low liquidity (under max_liquidity threshold)
        - +3 points: Matches target niche category
        - +2 points: Low 24h volume (under threshold)
        - +1 point: Newly created (under 48h old)
        - +2 points: Not in saturated/excluded categories
        """
        score = 0.0

        question = (market.get('question') or '').lower()
        category = (market.get('category') or '').lower()
        combined_text = f"{question} {category}"

        liquidity = market.get('liquidity', 0) or 0
        volume_24h = market.get('volume_24h', 0) or 0

        # --- EXCLUSION CHECK (score = -1 means reject) ---
        for excluded in self.exclude_categories:
            if excluded in combined_text:
                return -1  # Reject: saturated market

        # --- SCORING ---

        # +2 points: Low liquidity (our primary target)
        if 0 < liquidity <= self.max_liquidity:
            score += 2.0
            # Bonus for very low liquidity
            if liquidity < self.max_liquidity / 2:
                score += 0.5

        # +3 points: Matches niche sports (INVERTED from original filter)
        for sport in self.niche_sports:
            if sport in combined_text:
                score += 3.0
                break  # Only count once

        # +3 points: Matches other niche categories
        for category_kw in self.niche_categories:
            if category_kw in combined_text:
                score += 3.0
                break  # Only count once

        # +2 points: Low 24h volume
        if 0 < volume_24h <= self.max_volume_24h:
            score += 2.0

        # +1 point: Newly created (if we have first_seen timestamp)
        first_seen = market.get('first_seen')
        if first_seen:
            try:
                if isinstance(first_seen, str):
                    seen_dt = datetime.fromisoformat(first_seen.replace('Z', '+00:00'))
                else:
                    seen_dt = first_seen

                age_hours = (datetime.now(timezone.utc) - seen_dt).total_seconds() / 3600
                if age_hours < 48:
                    score += 1.0
            except (ValueError, TypeError):
                pass

        # +2 points: Not a major sport or crypto (default assumption)
        is_major = market.get('is_major_sport') or market.get('is_major_crypto')
        if not is_major:
            score += 2.0

        return min(score, 10.0)  # Cap at 10

    def classify_market(self, market: Dict) -> Dict:
        """
        Full classification with score and metadata.

        Returns:
            {
                'niche_score': float (0-10, or -1 if rejected),
                'niche_category': str (matched category or 'general'),
                'is_target': bool (whether to include in pipeline),
                'rejection_reason': str or None,
                'scoring_breakdown': dict
            }
        """
        question = (market.get('question') or '').lower()
        category = (market.get('category') or '').lower()
        combined_text = f"{question} {category}"

        # Check for exclusion first
        for excluded in self.exclude_categories:
            if excluded in combined_text:
                return {
                    'niche_score': -1,
                    'niche_category': None,
                    'is_target': False,
                    'rejection_reason': f"Saturated category: {excluded}",
                    'scoring_breakdown': {},
                }

        # Calculate score with breakdown
        breakdown = {}
        score = 0.0

        # Liquidity scoring
        liquidity = market.get('liquidity', 0) or 0
        if 0 < liquidity <= self.max_liquidity:
            breakdown['low_liquidity'] = 2.0
            score += 2.0
            if liquidity < self.max_liquidity / 2:
                breakdown['very_low_liquidity_bonus'] = 0.5
                score += 0.5

        # Niche sports check (INVERTED)
        matched_sport = None
        for sport in self.niche_sports:
            if sport in combined_text:
                matched_sport = sport
                breakdown['niche_sport'] = 3.0
                score += 3.0
                break

        # Niche category check
        matched_category = None
        if not matched_sport:  # Don't double-count
            for cat in self.niche_categories:
                if cat in combined_text:
                    matched_category = cat
                    breakdown['niche_category'] = 3.0
                    score += 3.0
                    break

        # Volume scoring
        volume_24h = market.get('volume_24h', 0) or 0
        if 0 < volume_24h <= self.max_volume_24h:
            breakdown['low_volume'] = 2.0
            score += 2.0

        # Age scoring
        first_seen = market.get('first_seen')
        if first_seen:
            try:
                if isinstance(first_seen, str):
                    seen_dt = datetime.fromisoformat(first_seen.replace('Z', '+00:00'))
                else:
                    seen_dt = first_seen
                age_hours = (datetime.now(timezone.utc) - seen_dt).total_seconds() / 3600
                if age_hours < 48:
                    breakdown['newly_created'] = 1.0
                    score += 1.0
            except (ValueError, TypeError):
                pass

        # Non-major bonus
        is_major = market.get('is_major_sport') or market.get('is_major_crypto')
        if not is_major:
            breakdown['not_major'] = 2.0
            score += 2.0

        score = min(score, 10.0)

        # Determine category label
        niche_cat = matched_sport or matched_category or 'general'

        return {
            'niche_score': score,
            'niche_category': niche_cat,
            'is_target': score >= MIN_NICHE_SCORE,
            'rejection_reason': None,
            'scoring_breakdown': breakdown,
        }

    def filter_target_markets(self, markets: List[Dict]) -> List[Dict]:
        """
        Filter markets to only include high-niche-score targets.

        Returns markets sorted by niche score (highest first).
        """
        targets = []

        for market in markets:
            classification = self.classify_market(market)

            if classification['is_target']:
                # Attach classification to market
                market['niche_score'] = classification['niche_score']
                market['niche_category'] = classification['niche_category']
                market['scoring_breakdown'] = classification['scoring_breakdown']
                targets.append(market)

        # Sort by niche score descending
        targets.sort(key=lambda m: m.get('niche_score', 0), reverse=True)

        return targets

    def is_niche_sport(self, text: str) -> Tuple[bool, Optional[str]]:
        """Check if text contains a niche sport keyword."""
        text_lower = text.lower()
        for sport in self.niche_sports:
            if sport in text_lower:
                return True, sport
        return False, None

    def is_niche_category(self, text: str) -> Tuple[bool, Optional[str]]:
        """Check if text matches a niche category."""
        text_lower = text.lower()
        for cat in self.niche_categories:
            if cat in text_lower:
                return True, cat
        return False, None

    def is_excluded(self, text: str) -> Tuple[bool, Optional[str]]:
        """Check if text matches an excluded category."""
        text_lower = text.lower()
        for excluded in self.exclude_categories:
            if excluded in text_lower:
                return True, excluded
        return False, None


# Convenience function
def score_market(market: Dict) -> float:
    """Quick niche score calculation."""
    classifier = NicheClassifier()
    return classifier.calculate_niche_score(market)
