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

try:
    from .predictit_source import PredictItSource
except ImportError:
    PredictItSource = None

try:
    from .election_source import ElectionSource
except ImportError:
    ElectionSource = None

try:
    from .news_source import NewsSource
except ImportError:
    NewsSource = None

try:
    from .crypto_source import CryptoSource
except ImportError:
    CryptoSource = None

try:
    from .espn_source import ESPNSource
except ImportError:
    ESPNSource = None

try:
    from .soccer_source import SoccerSource
except ImportError:
    SoccerSource = None

try:
    from .onchain_source import OnChainSource
except ImportError:
    OnChainSource = None

try:
    from .social_sentiment import SocialSentiment
except ImportError:
    SocialSentiment = None

try:
    from .fed_rates_source import FedRatesSource
except ImportError:
    FedRatesSource = None

try:
    from .ai_tech_source import AITechSource
except ImportError:
    AITechSource = None


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
        if PredictItSource:
            self.sources['predictit'] = PredictItSource()
        if ElectionSource:
            self.sources['election'] = ElectionSource()
        if NewsSource:
            self.sources['news'] = NewsSource()
        if CryptoSource:
            self.sources['crypto'] = CryptoSource()
        if SoccerSource:
            self.sources['soccer'] = SoccerSource()
        if OnChainSource:
            self.sources['onchain'] = OnChainSource()
        if SocialSentiment:
            self.sources['social'] = SocialSentiment()
        if FedRatesSource:
            self.sources['fed'] = FedRatesSource()
        if AITechSource:
            self.sources['ai_tech'] = AITechSource()

    def get_external_probability(self, market: Dict) -> Optional[Dict]:
        """
        Get external probability estimate for a market.

        Tries multiple data sources in priority order:
        1. Category-specific source (entertainment, sports, etc.)
        2. Cross-platform comparison (PredictIt, Odds aggregator)
        3. News sentiment as fallback

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
        results = []

        # 1. Try category-specific source
        category = self._detect_category(market)
        if category and category in self.sources:
            source = self.sources[category]
            result = source.get_probability(market)
            if result:
                results.append(result)

        # 2. For Fed/economics, use fed source
        if category == 'fed' and 'fed' in self.sources:
            result = self.sources['fed'].get_probability(market)
            if result:
                results.append(result)

        # 2b. For AI/Tech, use ai_tech source
        if category == 'ai_tech' and 'ai_tech' in self.sources:
            result = self.sources['ai_tech'].get_probability(market)
            if result:
                results.append(result)

        # 3. For politics/elections, also try election source
        if category == 'politics' and 'election' in self.sources:
            result = self.sources['election'].get_probability(market)
            if result:
                results.append(result)

        # 3. Try PredictIt for cross-platform comparison (ONLY for politics)
        # Skip PredictIt for non-political markets to avoid false matches
        if 'predictit' in self.sources and category == 'politics':
            result = self.sources['predictit'].get_probability(market)
            if result:
                results.append(result)

        # 4. For crypto, also try on-chain metrics
        if category == 'crypto' and 'onchain' in self.sources:
            result = self.sources['onchain'].get_probability(market)
            if result:
                results.append(result)

        # 5. Try news sentiment as supplementary signal
        if 'news' in self.sources and not results:
            result = self.sources['news'].get_probability(market)
            if result:
                results.append(result)

        # 6. Try social sentiment (Reddit) as additional signal
        if 'social' in self.sources and len(results) < 2:
            result = self.sources['social'].get_probability(market)
            if result:
                results.append(result)

        if not results:
            return None

        # If we have multiple results, average them with confidence weighting
        if len(results) == 1:
            return results[0]

        total_confidence = sum(r.get('confidence', 0.5) for r in results)
        if total_confidence == 0:
            total_confidence = len(results) * 0.5

        weighted_prob = sum(
            r.get('probability', 0.5) * r.get('confidence', 0.5)
            for r in results
        ) / total_confidence

        sources = ', '.join(r.get('source', 'unknown')[:20] for r in results)

        return {
            'source': f'Multi-Source ({sources})',
            'probability': round(weighted_prob, 3),
            'confidence': min(0.95, sum(r.get('confidence', 0.5) for r in results) / len(results) + 0.1),
            'reasoning': f"Average of {len(results)} sources",
            'individual_results': results,
        }

    def _detect_category(self, market: Dict) -> Optional[str]:
        """Detect market category from question text."""
        question = (market.get('question') or '').lower()
        category = (market.get('category') or '').lower()

        # Weather keywords (avoid matching "Hurricanes" sports team)
        if any(w in question for w in ['weather', 'temperature', 'rainfall', 'snow']):
            return 'weather'
        # Hurricane only if NOT about Carolina Hurricanes
        if 'hurricane' in question and 'carolina' not in question and 'hurricanes' not in question:
            return 'weather'
        if 'storm' in question and 'lightning' not in question:  # Avoid Tampa Bay Lightning
            return 'weather'

        # Esports keywords (check before general sports)
        if any(w in question for w in ['esports', 'e-sports', 'league of legends', 'dota', 'valorant', 'csgo', 'cs2', 'overwatch']):
            return 'esports'

        # Soccer/World Cup keywords (check before general sports)
        if any(w in question for w in ['world cup', 'fifa', 'premier league', 'champions league', 'la liga', 'bundesliga']):
            return 'soccer'

        # Sports keywords
        if any(w in question for w in ['nfl', 'nba', 'mlb', 'nhl', 'ufc', 'mma', 'golf', 'tennis',
                                       'super bowl', 'stanley cup', 'world series', 'playoffs',
                                       'finals', 'championship']):
            return 'sports'

        # Entertainment keywords
        if any(w in question for w in ['oscar', 'academy award', 'emmy', 'grammy', 'golden globe', 'box office', 'movie', 'film']):
            return 'entertainment'

        # Politics keywords
        if any(w in question for w in ['election', 'president', 'senate', 'congress', 'vote', 'poll', 'governor']):
            return 'politics'

        # Crypto keywords
        if any(w in question for w in ['bitcoin', 'btc', 'ethereum', 'eth', 'crypto', 'solana', 'sol', 'defi', 'altcoin']):
            return 'crypto'

        # Fed/Economics keywords
        if any(w in question for w in ['fed ', 'fomc', 'interest rate', 'federal reserve', 'rate cut', 'rate hike', 'bps', 'basis points']):
            return 'fed'

        # AI/Tech keywords
        if any(w in question for w in ['ai model', 'best ai', 'chatgpt', 'gpt-4', 'claude', 'gemini', 'llama', 'mistral', 'grok', 'openai', 'acquire', 'acquisition']):
            return 'ai_tech'

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

        # Skip lottery tickets (entry < $0.05) - not actionable
        # But allow small entries if confidence is high (we're betting against near-certainties)
        confidence = external.get('confidence', 0.5)
        min_entry = 0.03 if confidence >= 0.80 else 0.05
        if entry_price < min_entry:
            return None

        edge_pct = (edge / entry_price) * 100 if entry_price > 0 else 0
        raw_edge_pct = edge_pct  # Keep uncapped for debugging

        # Dynamic edge cap based on source confidence
        # High confidence (>0.7) -> allow up to 40% edge
        # Medium confidence (0.5-0.7) -> allow up to 30% edge
        # Low confidence (<0.5) -> allow up to 20% edge
        confidence = external.get('confidence', 0.5)
        if confidence >= 0.75:
            max_edge = 45.0
        elif confidence >= 0.60:
            max_edge = 35.0
        elif confidence >= 0.50:
            max_edge = 25.0
        else:
            max_edge = 15.0

        edge_pct = min(edge_pct, max_edge)

        # Calculate confidence-adjusted edge for ranking
        # This gives better weight to high-confidence, moderate edges
        # vs low-confidence, high edges
        adjusted_edge = edge_pct * confidence

        return {
            'has_edge': edge_pct >= 5.0,  # Minimum 5% edge for data-backed
            'edge_pct': round(edge_pct, 2),
            'raw_edge_pct': round(raw_edge_pct, 2),
            'adjusted_edge': round(adjusted_edge, 2),  # For better ranking
            'recommended_side': side,
            'market_price': market_yes_price,
            'external_probability': external_prob,
            'source': external.get('source', 'unknown'),
            'confidence': confidence,
            'reasoning': external.get('reasoning', ''),
        }


__all__ = [
    'DataSourceManager',
    'WeatherSource',
    'SportsSource',
    'ESPNSource',
    'EsportsSource',
    'EntertainmentSource',
    'PoliticsSource',
    'OddsAggregator',
    'PredictItSource',
    'ElectionSource',
    'NewsSource',
    'CryptoSource',
    'SoccerSource',
    'OnChainSource',
    'SocialSentiment',
    'FedRatesSource',
    'AITechSource',
]
