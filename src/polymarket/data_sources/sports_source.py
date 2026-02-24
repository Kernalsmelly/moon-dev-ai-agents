"""
Sports Data Source

Uses The Odds API to get betting odds from major sportsbooks.
Falls back to ESPN (free, no key) when Odds API is exhausted.

The Odds API: https://the-odds-api.com/
- Free tier: 500 requests/month
- Covers: NFL, NBA, MLB, NHL, UFC, Soccer, Golf, Tennis, etc.

ESPN API (fallback):
- Free, no key required
- Provides team records, rankings, and ESPN's win predictions
"""

import os
import httpx
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, List

logger = logging.getLogger(__name__)

# Import ESPN fallback
try:
    from .espn_source import ESPNSource, get_espn_source
    HAS_ESPN = True
except ImportError:
    HAS_ESPN = False
    ESPNSource = None

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
    # Championship markets
    'nba championship': 'basketball_nba_championship',
    'super bowl': 'americanfootball_nfl_super_bowl_winner',
    'stanley cup': 'icehockey_nhl_championship',
    'world series': 'baseball_mlb_world_series_winner',
}

# NBA team names for matching
NBA_TEAMS = [
    'lakers', 'celtics', 'warriors', 'nets', 'clippers', 'heat', 'bucks', 'suns',
    'nuggets', 'sixers', '76ers', 'mavericks', 'grizzlies', 'cavaliers', 'hawks',
    'bulls', 'knicks', 'raptors', 'pacers', 'magic', 'hornets', 'wizards', 'pistons',
    'thunder', 'rockets', 'pelicans', 'spurs', 'kings', 'timberwolves', 'blazers', 'jazz',
    'boston', 'golden state', 'los angeles', 'brooklyn', 'miami', 'milwaukee', 'phoenix',
    'denver', 'philadelphia', 'dallas', 'memphis', 'cleveland', 'atlanta', 'chicago',
    'new york', 'toronto', 'indiana', 'orlando', 'charlotte', 'washington', 'detroit',
    'oklahoma city', 'houston', 'new orleans', 'san antonio', 'sacramento', 'minnesota',
    'portland', 'utah',
]

# NHL team names for matching
NHL_TEAMS = [
    'golden knights', 'vegas', 'bruins', 'avalanche', 'panthers', 'rangers', 'oilers',
    'hurricanes', 'stars', 'maple leafs', 'lightning', 'jets', 'wild', 'kraken',
    'flames', 'canucks', 'blues', 'islanders', 'capitals', 'penguins', 'red wings',
    'senators', 'blackhawks', 'predators', 'devils', 'ducks', 'kings', 'flyers',
    'coyotes', 'sharks', 'sabres', 'canadiens', 'blue jackets',
]


class SportsSource:
    """
    Sports betting odds data source.

    Aggregates odds from major sportsbooks:
    - DraftKings, FanDuel, BetMGM, Caesars, etc.

    Falls back to ESPN (free) when Odds API is exhausted.
    """

    def __init__(self):
        self.client = httpx.Client(timeout=15.0)
        self.cache = {}
        self.cache_ttl = 3600  # 1 hour cache (conserve API calls - only 500/month free)
        self.api_calls_made = 0
        self.max_api_calls_per_session = 10  # Limit calls per session
        self.odds_api_exhausted = False  # Track if we've hit rate limits
        self.espn_source = get_espn_source() if HAS_ESPN else None

    def get_probability(self, market: Dict) -> Optional[Dict]:
        """
        Get probability estimate for a sports market.

        Uses The Odds API if available, falls back to ESPN.
        """
        question = (market.get('question') or '').lower()

        # Detect sport
        sport_key = self._detect_sport(question)
        if not sport_key:
            # Try ESPN even without sport detection - it has its own detection
            if self.espn_source:
                return self.espn_source.get_probability(market)
            return None

        # Get team/player name
        team = self._extract_team(question)
        if not team:
            return None

        # Try The Odds API first (if we have a key and haven't exhausted it)
        if ODDS_API_KEY and not self.odds_api_exhausted:
            odds = self._get_odds(sport_key, team)
            if odds:
                return odds

        # Fallback to ESPN (free, no key required)
        if self.espn_source:
            logger.debug(f"Using ESPN fallback for sports market")
            result = self.espn_source.get_probability(market)
            if result:
                # Mark as ESPN fallback
                result['source'] = f"ESPN (fallback) - {result.get('source', '')}"
                return result

        return None

    def _detect_sport(self, question: str) -> Optional[str]:
        """Detect sport from question text."""
        question_lower = question.lower()

        # Check explicit sport keywords first
        for keyword, sport_key in SPORT_KEYS.items():
            if keyword in question_lower:
                return sport_key

        # Detect sport from team names
        for team in NBA_TEAMS:
            if team in question_lower:
                # Check if it's a championship/playoff market
                if 'championship' in question_lower or 'win the' in question_lower:
                    return 'basketball_nba_championship'
                return 'basketball_nba'

        for team in NHL_TEAMS:
            if team in question_lower:
                if 'stanley cup' in question_lower or 'playoff' in question_lower:
                    return 'icehockey_nhl_championship'
                return 'icehockey_nhl'

        return None

    def _extract_team(self, question: str) -> Optional[str]:
        """Extract team or player name from question."""
        question_lower = question.lower()

        # Check for NBA teams
        for team in NBA_TEAMS:
            if team in question_lower:
                return team

        # Check for NHL teams
        for team in NHL_TEAMS:
            if team in question_lower:
                return team

        # Return the question for fallback matching
        return question

    def _get_odds(self, sport_key: str, team_query: str) -> Optional[Dict]:
        """Get odds from The Odds API."""
        cache_key = f"{sport_key}:{team_query[:50]}"

        # Check cache first (1 hour TTL to conserve API calls)
        if cache_key in self.cache:
            cached = self.cache[cache_key]
            if (datetime.now(timezone.utc) - cached['timestamp']).seconds < self.cache_ttl:
                return cached['data']

        # Check API call limit (free tier = 500/month, be conservative)
        if self.api_calls_made >= self.max_api_calls_per_session:
            logger.warning(f"API call limit reached ({self.max_api_calls_per_session}/session) - using cache only")
            return None

        try:
            self.api_calls_made += 1
            url = f"{ODDS_API_BASE}/sports/{sport_key}/odds"
            params = {
                'apiKey': ODDS_API_KEY,
                'regions': 'us',
                'markets': 'h2h,spreads,totals',
                'oddsFormat': 'american',
            }

            response = self.client.get(url, params=params)
            if response.status_code == 401:
                logger.warning("Odds API key invalid or exhausted - switching to ESPN fallback")
                self.odds_api_exhausted = True
                return None
            if response.status_code == 429:
                logger.warning("Odds API rate limited - switching to ESPN fallback")
                self.odds_api_exhausted = True
                return None
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
