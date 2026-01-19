"""
Sports Data Source

Uses The Odds API to get betting odds from major sportsbooks.
Compare sportsbook odds to Polymarket prices to find edge.

The Odds API: https://the-odds-api.com/
- Free tier: 500 requests/month
- Covers: NFL, NBA, MLB, NHL, UFC, Soccer, Golf, Tennis, etc.
"""

import os
import httpx
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, List

logger = logging.getLogger(__name__)

# API endpoint
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")

# Sport keys for The Odds API
SPORT_KEYS = {
    'nfl': 'americanfootball_nfl',
    'nba': 'basketball_nba',
    'mlb': 'baseball_mlb',
    'nhl': 'icehockey_nhl',
    'ufc': 'mma_mixed_martial_arts',
    'mma': 'mma_mixed_martial_arts',
    'golf': 'golf_pga_championship',
    'tennis': 'tennis_atp_aus_open',
    'soccer': 'soccer_epl',
    'premier league': 'soccer_epl',
    'champions league': 'soccer_uefa_champs_league',
    'college football': 'americanfootball_ncaaf',
    'college basketball': 'basketball_ncaab',
    'march madness': 'basketball_ncaab',
}


class SportsSource:
    """
    Sports betting odds data source.

    Aggregates odds from major sportsbooks:
    - DraftKings, FanDuel, BetMGM, Caesars, etc.

    Compares average implied probability to Polymarket price.
    """

    def __init__(self):
        self.client = httpx.Client(timeout=15.0)
        self.cache = {}
        self.cache_ttl = 300  # 5 minute cache

    def get_probability(self, market: Dict) -> Optional[Dict]:
        """
        Get probability estimate for a sports market.
        """
        if not ODDS_API_KEY:
            logger.warning("ODDS_API_KEY not set - get free key at the-odds-api.com")
            return None

        question = (market.get('question') or '').lower()

        # Detect sport
        sport_key = self._detect_sport(question)
        if not sport_key:
            return None

        # Get team/player name
        team = self._extract_team(question)
        if not team:
            return None

        # Get odds from API
        odds = self._get_odds(sport_key, team)
        if not odds:
            return None

        return odds

    def _detect_sport(self, question: str) -> Optional[str]:
        """Detect sport from question text."""
        question_lower = question.lower()

        for keyword, sport_key in SPORT_KEYS.items():
            if keyword in question_lower:
                return sport_key

        return None

    def _extract_team(self, question: str) -> Optional[str]:
        """Extract team or player name from question."""
        # Common patterns
        # "Will the Lakers win..."
        # "Will Patrick Mahomes..."
        # "Chiefs to win..."

        # This is a simplified extraction - in production you'd use NER
        # For now, return the question for matching
        return question

    def _get_odds(self, sport_key: str, team_query: str) -> Optional[Dict]:
        """Get odds from The Odds API."""
        cache_key = f"{sport_key}:{team_query[:50]}"

        # Check cache
        if cache_key in self.cache:
            cached = self.cache[cache_key]
            if (datetime.now(timezone.utc) - cached['timestamp']).seconds < self.cache_ttl:
                return cached['data']

        try:
            url = f"{ODDS_API_BASE}/sports/{sport_key}/odds"
            params = {
                'apiKey': ODDS_API_KEY,
                'regions': 'us',
                'markets': 'h2h,spreads,totals',
                'oddsFormat': 'american',
            }

            response = self.client.get(url, params=params)
            if response.status_code != 200:
                logger.error(f"Odds API error: {response.status_code}")
                return None

            events = response.json()

            # Find matching event
            for event in events:
                home_team = event.get('home_team', '').lower()
                away_team = event.get('away_team', '').lower()

                # Check if query matches either team
                query_lower = team_query.lower()
                if home_team in query_lower or away_team in query_lower:
                    # Calculate implied probability from odds
                    prob = self._calculate_implied_probability(event, query_lower)
                    if prob:
                        result = {
                            'source': 'The Odds API (Sportsbook Average)',
                            'probability': prob['probability'],
                            'confidence': prob['confidence'],
                            'reasoning': prob['reasoning'],
                            'last_updated': datetime.now(timezone.utc),
                        }

                        # Cache result
                        self.cache[cache_key] = {
                            'data': result,
                            'timestamp': datetime.now(timezone.utc),
                        }

                        return result

            return None

        except Exception as e:
            logger.error(f"Odds API error: {e}")
            return None

    def _calculate_implied_probability(self, event: Dict, query: str) -> Optional[Dict]:
        """Calculate implied probability from American odds."""
        bookmakers = event.get('bookmakers', [])
        if not bookmakers:
            return None

        probabilities = []
        home_team = event.get('home_team', '').lower()

        for bookmaker in bookmakers:
            markets = bookmaker.get('markets', [])
            for market in markets:
                if market.get('key') == 'h2h':
                    outcomes = market.get('outcomes', [])
                    for outcome in outcomes:
                        team_name = outcome.get('name', '').lower()
                        if team_name in query:
                            american_odds = outcome.get('price', 0)
                            prob = self._american_to_probability(american_odds)
                            if prob:
                                probabilities.append(prob)

        if not probabilities:
            return None

        avg_prob = sum(probabilities) / len(probabilities)

        return {
            'probability': round(avg_prob, 3),
            'confidence': min(0.9, 0.5 + len(probabilities) * 0.1),  # More books = more confidence
            'reasoning': f"Average of {len(probabilities)} sportsbooks: {avg_prob:.1%} implied probability",
        }

    def _american_to_probability(self, american_odds: int) -> Optional[float]:
        """Convert American odds to implied probability."""
        try:
            if american_odds > 0:
                # Underdog: +150 means $100 bet wins $150
                prob = 100 / (american_odds + 100)
            else:
                # Favorite: -150 means $150 bet wins $100
                prob = abs(american_odds) / (abs(american_odds) + 100)

            return prob
        except:
            return None

    def get_upcoming_events(self, sport_key: str) -> List[Dict]:
        """Get list of upcoming events for a sport."""
        if not ODDS_API_KEY:
            return []

        try:
            url = f"{ODDS_API_BASE}/sports/{sport_key}/events"
            params = {'apiKey': ODDS_API_KEY}

            response = self.client.get(url, params=params)
            if response.status_code == 200:
                return response.json()

        except Exception as e:
            logger.error(f"Error fetching events: {e}")

        return []

    def close(self):
        """Close HTTP client."""
        self.client.close()
