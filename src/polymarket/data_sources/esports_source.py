"""
Esports Data Source

Gets competitive gaming odds and statistics from:
- PandaScore API (esports odds and stats)
- HLTV (CS:GO/CS2 rankings)
- Liquipedia (Dota 2, LoL data)

Esports markets are often less efficient than traditional sports,
making them good targets for edge.
"""

import os
import httpx
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, List

logger = logging.getLogger(__name__)

# API endpoints
PANDASCORE_API_BASE = "https://api.pandascore.co"
PANDASCORE_API_KEY = os.getenv("PANDASCORE_API_KEY", "")

# Also supports The Odds API for esports
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")

# Esports game mappings
ESPORTS_GAMES = {
    'league of legends': 'lol',
    'lol': 'lol',
    'dota': 'dota2',
    'dota 2': 'dota2',
    'csgo': 'csgo',
    'cs:go': 'csgo',
    'cs2': 'csgo',
    'counter-strike': 'csgo',
    'valorant': 'valorant',
    'overwatch': 'ow',
    'overwatch 2': 'ow',
    'rocket league': 'rl',
    'call of duty': 'codmw',
    'cod': 'codmw',
}


class EsportsSource:
    """
    Esports betting and statistics data source.

    Key insight: Esports markets are often mispriced because:
    1. Less liquidity than traditional sports
    2. Faster-changing team rosters
    3. Game patches can shift power balance
    4. Regional strength differences often ignored
    """

    def __init__(self):
        self.client = httpx.Client(timeout=15.0)
        self.cache = {}
        self.cache_ttl = 300  # 5 minute cache

    def get_probability(self, market: Dict) -> Optional[Dict]:
        """
        Get probability estimate for an esports market.
        """
        question = (market.get('question') or '').lower()

        # Detect game
        game_key = self._detect_game(question)
        if not game_key:
            return None

        # Try PandaScore first
        if PANDASCORE_API_KEY:
            result = self._get_pandascore_odds(game_key, question)
            if result:
                return result

        # Fallback to The Odds API esports
        if ODDS_API_KEY:
            result = self._get_odds_api_esports(game_key, question)
            if result:
                return result

        return None

    def _detect_game(self, question: str) -> Optional[str]:
        """Detect esports game from question text."""
        question_lower = question.lower()

        for keyword, game_key in ESPORTS_GAMES.items():
            if keyword in question_lower:
                return game_key

        return None

    def _get_pandascore_odds(self, game: str, query: str) -> Optional[Dict]:
        """Get odds from PandaScore API."""
        try:
            # Get upcoming matches
            url = f"{PANDASCORE_API_BASE}/{game}/matches/upcoming"
            headers = {'Authorization': f'Bearer {PANDASCORE_API_KEY}'}

            response = self.client.get(url, headers=headers)
            if response.status_code != 200:
                return None

            matches = response.json()

            # Find matching match
            for match in matches:
                opponents = match.get('opponents', [])
                if len(opponents) >= 2:
                    team1 = opponents[0].get('opponent', {}).get('name', '').lower()
                    team2 = opponents[1].get('opponent', {}).get('name', '').lower()

                    query_lower = query.lower()
                    if team1 in query_lower or team2 in query_lower:
                        # Get odds if available
                        odds = match.get('odds', {})
                        if odds:
                            return self._parse_pandascore_odds(match, query_lower)

            return None

        except Exception as e:
            logger.error(f"PandaScore API error: {e}")
            return None

    def _parse_pandascore_odds(self, match: Dict, query: str) -> Optional[Dict]:
        """Parse PandaScore match data into probability."""
        opponents = match.get('opponents', [])
        if len(opponents) < 2:
            return None

        team1 = opponents[0].get('opponent', {})
        team2 = opponents[1].get('opponent', {})

        # Determine which team we're looking for
        if team1.get('name', '').lower() in query:
            target_team = team1
        elif team2.get('name', '').lower() in query:
            target_team = team2
        else:
            return None

        # Use ELO-based probability if available
        # PandaScore provides team rankings
        odds_data = match.get('odds', {})

        # Calculate implied probability from odds if available
        if odds_data:
            # Odds are typically in decimal format
            team1_odds = odds_data.get('team1', 2.0)
            team2_odds = odds_data.get('team2', 2.0)

            team1_prob = 1 / team1_odds if team1_odds > 0 else 0.5
            team2_prob = 1 / team2_odds if team2_odds > 0 else 0.5

            # Normalize
            total = team1_prob + team2_prob
            team1_prob /= total
            team2_prob /= total

            if target_team == team1:
                prob = team1_prob
            else:
                prob = team2_prob

            return {
                'source': 'PandaScore',
                'probability': round(prob, 3),
                'confidence': 0.75,
                'reasoning': f"Based on PandaScore esports odds: {prob:.1%}",
                'last_updated': datetime.now(timezone.utc),
            }

        return None

    def _get_odds_api_esports(self, game: str, query: str) -> Optional[Dict]:
        """Get esports odds from The Odds API."""
        # Map game to Odds API sport key
        game_to_odds_key = {
            'lol': 'esports_league_of_legends',
            'dota2': 'esports_dota_2',
            'csgo': 'esports_counter_strike',
        }

        sport_key = game_to_odds_key.get(game)
        if not sport_key:
            return None

        try:
            url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds"
            params = {
                'apiKey': ODDS_API_KEY,
                'regions': 'us,eu',
                'markets': 'h2h',
                'oddsFormat': 'decimal',
            }

            response = self.client.get(url, params=params)
            if response.status_code != 200:
                return None

            events = response.json()

            for event in events:
                home_team = event.get('home_team', '').lower()
                away_team = event.get('away_team', '').lower()

                query_lower = query.lower()
                if home_team in query_lower or away_team in query_lower:
                    prob = self._calculate_implied_prob(event, query_lower)
                    if prob:
                        return {
                            'source': 'The Odds API (Esports)',
                            'probability': prob,
                            'confidence': 0.7,
                            'reasoning': f"Aggregated esports betting odds: {prob:.1%}",
                            'last_updated': datetime.now(timezone.utc),
                        }

            return None

        except Exception as e:
            logger.error(f"Odds API esports error: {e}")
            return None

    def _calculate_implied_prob(self, event: Dict, query: str) -> Optional[float]:
        """Calculate implied probability from decimal odds."""
        bookmakers = event.get('bookmakers', [])
        if not bookmakers:
            return None

        probs = []
        for book in bookmakers:
            markets = book.get('markets', [])
            for market in markets:
                if market.get('key') == 'h2h':
                    for outcome in market.get('outcomes', []):
                        if outcome.get('name', '').lower() in query:
                            decimal_odds = outcome.get('price', 2.0)
                            prob = 1 / decimal_odds if decimal_odds > 0 else 0.5
                            probs.append(prob)

        if probs:
            return sum(probs) / len(probs)

        return None

    def get_team_rankings(self, game: str) -> List[Dict]:
        """Get current team rankings for a game."""
        if not PANDASCORE_API_KEY:
            return []

        try:
            url = f"{PANDASCORE_API_BASE}/{game}/rankings"
            headers = {'Authorization': f'Bearer {PANDASCORE_API_KEY}'}

            response = self.client.get(url, headers=headers)
            if response.status_code == 200:
                return response.json()

        except Exception as e:
            logger.error(f"Rankings error: {e}")

        return []

    def close(self):
        """Close HTTP client."""
        self.client.close()
