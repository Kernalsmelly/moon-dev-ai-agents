"""
Soccer/Football Data Source

Provides probability estimates for soccer markets using:
1. FIFA World Rankings (official)
2. Elo ratings (more predictive)
3. Historical World Cup performance

FIFA Rankings: https://www.fifa.com/fifa-world-ranking
World Football Elo: https://www.eloratings.net/

Key insight: FIFA rankings are less predictive than Elo ratings.
Use Elo for probability estimates.
"""

import httpx
import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# World Football Elo ratings (as of Jan 2026 - approximate)
# Higher Elo = stronger team
# Source: eloratings.net concepts
ELO_RATINGS = {
    # Top tier (2000+)
    'argentina': 2073,
    'france': 2049,
    'spain': 2030,
    'england': 2018,
    'brazil': 2006,
    'belgium': 1985,
    'netherlands': 1980,
    'portugal': 1975,
    'germany': 1970,
    'italy': 1960,

    # Strong (1900-2000)
    'croatia': 1945,
    'uruguay': 1920,
    'colombia': 1910,
    'usa': 1890,
    'mexico': 1880,
    'switzerland': 1875,
    'denmark': 1870,
    'japan': 1860,
    'senegal': 1855,
    'morocco': 1850,

    # Good (1800-1900)
    'poland': 1840,
    'austria': 1835,
    'ukraine': 1830,
    'turkey': 1825,
    'korea republic': 1820,
    'south korea': 1820,
    'australia': 1810,
    'serbia': 1805,
    'ecuador': 1800,
    'iran': 1795,

    # Average (1700-1800)
    'wales': 1780,
    'czech republic': 1775,
    'russia': 1770,
    'nigeria': 1765,
    'cameroon': 1760,
    'egypt': 1755,
    'algeria': 1750,
    'ghana': 1745,
    'tunisia': 1740,
    'scotland': 1735,
    'canada': 1730,
    'chile': 1725,
    'peru': 1720,
    'saudi arabia': 1715,
    'costa rica': 1710,
    'paraguay': 1705,
    'venezuela': 1700,

    # Below average (1600-1700)
    'jamaica': 1680,
    'qatar': 1670,
    'panama': 1660,
    'honduras': 1650,
    'el salvador': 1640,
    'new zealand': 1630,
    'iceland': 1625,
    'norway': 1620,
    'ireland': 1615,
    'bosnia': 1610,
    'slovenia': 1605,
    'montenegro': 1600,

    # Weak (1500-1600)
    'bolivia': 1580,
    'united arab emirates': 1560,
    'jordan': 1550,
    'uzbekistan': 1540,
    'china': 1530,
    'iraq': 1520,
    'oman': 1510,
    'bahrain': 1500,

    # Very weak (<1500)
    'haiti': 1450,
    'curacao': 1420,
    'cape verde': 1400,
    'india': 1350,
}

# World Cup winners (historical strength indicator)
WORLD_CUP_WINNERS = {
    'brazil': 5,
    'germany': 4,
    'italy': 4,
    'argentina': 3,
    'france': 2,
    'uruguay': 2,
    'england': 1,
    'spain': 1,
}


class SoccerSource:
    """
    Soccer/football data source for prediction markets.

    Uses Elo ratings to estimate World Cup probabilities.
    """

    def __init__(self):
        self.client = httpx.Client(timeout=15.0)
        self.cache = {}
        self.cache_ttl = 3600  # 1 hour cache

    def get_probability(self, market: Dict) -> Optional[Dict]:
        """Get probability estimate for a soccer market."""
        question = (market.get('question') or '').lower()

        # Detect if World Cup market
        if 'world cup' in question or 'fifa' in question:
            return self._estimate_world_cup_probability(question)

        # Could add Champions League, Premier League, etc. later
        return None

    def _estimate_world_cup_probability(self, question: str) -> Optional[Dict]:
        """
        Estimate World Cup win probability using Elo ratings.

        Formula: P(win) = 1 / (1 + 10^((avg_opponent_elo - team_elo) / 400))

        For tournament win, we simulate multiple rounds.
        """
        # Extract country name
        country = self._extract_country(question)
        if not country:
            return None

        # Get Elo rating
        elo = ELO_RATINGS.get(country.lower())
        if not elo:
            # Unknown team - assume weak
            elo = 1400
            confidence = 0.40
        else:
            confidence = 0.70

        # Calculate World Cup win probability
        # Using simplified model: compare to average of top 8 teams
        top_8_avg = 2000  # Approximate average Elo of typical World Cup QF teams

        # Expected score against average QF opponent
        expected_vs_top8 = 1 / (1 + 10 ** ((top_8_avg - elo) / 400))

        # Need to win ~7 games to win World Cup
        # Simplified: probability of winning tournament
        if elo >= 2050:
            # Elite team (Argentina, France level)
            base_prob = 0.12
        elif elo >= 2000:
            # Top tier (Spain, England, Brazil)
            base_prob = 0.08
        elif elo >= 1950:
            # Strong (Netherlands, Portugal, Germany)
            base_prob = 0.05
        elif elo >= 1900:
            # Good (Uruguay, Colombia, USA)
            base_prob = 0.02
        elif elo >= 1800:
            # Average
            base_prob = 0.005
        elif elo >= 1700:
            # Below average
            base_prob = 0.001
        else:
            # Weak teams
            base_prob = 0.0001

        # Adjust for historical World Cup success
        wc_wins = WORLD_CUP_WINNERS.get(country.lower(), 0)
        if wc_wins > 0:
            base_prob *= (1 + wc_wins * 0.1)  # 10% boost per past win

        # Cap probability
        probability = min(0.20, base_prob)

        # For longshots (Haiti, Qatar, etc.), we're very confident they WON'T win
        if elo < 1700:
            confidence = 0.85  # High confidence in low probability
            reasoning = f"{country} (Elo: {elo}) is a significant underdog - extremely unlikely to win"
        elif elo < 1900:
            confidence = 0.75
            reasoning = f"{country} (Elo: {elo}) is below top tier - very unlikely to win World Cup"
        else:
            reasoning = f"{country} (Elo: {elo}) has {probability*100:.1f}% chance based on Elo rating"

        return {
            'source': 'Soccer Elo Ratings',
            'probability': round(probability, 4),
            'confidence': confidence,
            'reasoning': reasoning,
            'elo_rating': elo,
            'historical_wins': wc_wins,
        }

    def _extract_country(self, question: str) -> Optional[str]:
        """Extract country name from question."""
        # Pattern: "Will [Country] win the ... World Cup"
        patterns = [
            r'will\s+([a-z\s]+?)\s+win\s+(?:the\s+)?(?:\d+\s+)?(?:fifa\s+)?world\s+cup',
            r'([a-z\s]+?)\s+(?:to\s+)?win\s+(?:the\s+)?world\s+cup',
        ]

        question_lower = question.lower()

        for pattern in patterns:
            match = re.search(pattern, question_lower)
            if match:
                country = match.group(1).strip()
                # Clean up common variations
                country = country.replace('the ', '').strip()
                return country

        # Try matching known countries directly
        for country in ELO_RATINGS.keys():
            if country in question_lower:
                return country

        return None

    def get_world_cup_odds(self) -> List[Dict]:
        """Get World Cup win probabilities for all teams."""
        results = []
        for country, elo in sorted(ELO_RATINGS.items(), key=lambda x: x[1], reverse=True):
            prob = self._estimate_world_cup_probability(f"Will {country} win the World Cup?")
            if prob:
                results.append({
                    'country': country.title(),
                    'elo': elo,
                    'probability': prob['probability'],
                })
        return results[:20]  # Top 20

    def close(self):
        """Close HTTP client."""
        self.client.close()


# Singleton
_source: Optional[SoccerSource] = None


def get_soccer_source() -> SoccerSource:
    """Get singleton soccer source."""
    global _source
    if _source is None:
        _source = SoccerSource()
    return _source
