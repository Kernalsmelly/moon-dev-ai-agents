"""
Polymarket Data Sources Module

Provides real external data to calculate actual edge in prediction markets.
Each data source compares market prices to external predictions/odds.

Supported niches:
- Weather: NOAA, OpenWeatherMap
- Sports: ESPN, The Odds API
- Esports: HLTV, Liquipedia, LoL Esports
- Entertainment: Gold Derby (Oscars), IMDB
- Politics: FiveThirtyEight, RealClearPolitics
- Crypto: Already integrated via existing APIs
- Space: NASA, SpaceX launch data
"""

from typing import Dict, Optional

# Import each data source with graceful fallback
try:
    from .weather_source import WeatherSource
except ImportError:
    WeatherSource = None

try:
    from .sports_source import SportsSource
except ImportError:
    SportsSource = None

try:
    from .esports_source import EsportsSource
except ImportError:
    EsportsSource = None

try:
    from .entertainment_source import EntertainmentSource
except ImportError:
    EntertainmentSource = None

try:
    from .politics_source import PoliticsSource
except ImportError:
    PoliticsSource = None

try:
    from .odds_aggregator import OddsAggregator
except ImportError:
    OddsAggregator = None


class DataSourceManager:
    """
    Unified interface for all data sources.

    Automatically routes markets to appropriate data source based on category.
    """

    def __init__(self):
        self.sources = {}

        # Initialize available sources
        if WeatherSource:
            self.sources['weather'] = WeatherSource()
        if SportsSource:
            self.sources['sports'] = SportsSource()
        if EsportsSource:
            self.sources['esports'] = EsportsSource()
        if EntertainmentSource:
            self.sources['entertainment'] = EntertainmentSource()
        if PoliticsSource:
            self.sources['politics'] = PoliticsSource()
        if OddsAggregator:
            self.sources['odds'] = OddsAggregator()

    def get_external_probability(self, market: Dict) -> Optional[Dict]:
        """
        Get external probability estimate for a market.

        Returns:
            {
                'source': str,  # Data source name
                'probability': float,  # External probability (0-1)
                'confidence': float,  # Confidence in estimate (0-1)
                'edge_vs_market': float,  # Difference from market price
                'reasoning': str,  # Why this probability
                'last_updated': datetime,
            }
        """
        category = self._detect_category(market)

        if category and category in self.sources:
            source = self.sources[category]
            return source.get_probability(market)

        return None

    def _detect_category(self, market: Dict) -> Optional[str]:
        """Detect market category from question text."""
        question = (market.get('question') or '').lower()
        category = (market.get('category') or '').lower()

        # Weather keywords
        if any(w in question for w in ['weather', 'temperature', 'hurricane', 'storm', 'rainfall', 'snow']):
            return 'weather'

        # Esports keywords (check before general sports)
        if any(w in question for w in ['esports', 'e-sports', 'league of legends', 'dota', 'valorant', 'csgo', 'cs2', 'overwatch']):
            return 'esports'

        # Sports keywords
        if any(w in question for w in ['nfl', 'nba', 'mlb', 'nhl', 'ufc', 'mma', 'golf', 'tennis', 'soccer', 'football']):
            return 'sports'

        # Entertainment keywords
        if any(w in question for w in ['oscar', 'academy award', 'emmy', 'grammy', 'golden globe', 'box office', 'movie', 'film']):
            return 'entertainment'

        # Politics keywords
        if any(w in question for w in ['election', 'president', 'senate', 'congress', 'vote', 'poll', 'governor']):
            return 'politics'

        return None

    def calculate_real_edge(self, market: Dict) -> Optional[Dict]:
        """
        Calculate real edge based on external data.

        Returns:
            {
                'has_edge': bool,
                'edge_pct': float,
                'recommended_side': str,  # 'YES' or 'NO'
                'market_price': float,
                'external_probability': float,
                'source': str,
                'confidence': float,
            }
        """
        external = self.get_external_probability(market)

        if not external:
            return None

        market_yes_price = market.get('yes_price', 0.5)
        external_prob = external.get('probability', 0.5)

        # Calculate edge
        if external_prob > market_yes_price:
            # External says YES is more likely than market thinks
            edge = external_prob - market_yes_price
            side = 'YES'
            entry_price = market_yes_price
        else:
            # External says NO is more likely
            edge = market_yes_price - external_prob
            side = 'NO'
            entry_price = 1 - market_yes_price

        # Skip lottery tickets (entry < $0.10) - not actionable
        if entry_price < 0.10:
            return None

        edge_pct = (edge / entry_price) * 100 if entry_price > 0 else 0

        # Cap unrealistic edges at 50%
        edge_pct = min(edge_pct, 50.0)

        return {
            'has_edge': edge_pct >= 5.0,  # Minimum 5% edge for data-backed
            'edge_pct': round(edge_pct, 2),
            'recommended_side': side,
            'market_price': market_yes_price,
            'external_probability': external_prob,
            'source': external.get('source', 'unknown'),
            'confidence': external.get('confidence', 0.5),
            'reasoning': external.get('reasoning', ''),
        }


__all__ = [
    'DataSourceManager',
    'WeatherSource',
    'SportsSource',
    'EsportsSource',
    'EntertainmentSource',
    'PoliticsSource',
    'OddsAggregator',
]
