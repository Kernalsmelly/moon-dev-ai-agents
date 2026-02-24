"""
News Sentiment Data Source

Monitors news for events that could move prediction markets.
Uses free news APIs to detect breaking news relevant to open positions.

APIs used:
- NewsData.io (free tier: 200 requests/day)
- GNews.io (free tier: 100 requests/day)
"""

import os
import httpx
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# API keys from environment
NEWSDATA_KEY = os.getenv("NEWSDATA_API_KEY", "")
GNEWS_KEY = os.getenv("GNEWS_API_KEY", "")

# API endpoints
NEWSDATA_API = "https://newsdata.io/api/1/news"
GNEWS_API = "https://gnews.io/api/v4/search"

# Keywords for different market categories
CATEGORY_KEYWORDS = {
    'oscars': ['oscar', 'academy award', 'academy awards', 'oscars 2025', 'oscars 2026', 'best picture', 'best actor', 'best actress', 'best director'],
    'sports': ['nba', 'nfl', 'super bowl', 'championship', 'playoffs', 'final'],
    'crypto': ['bitcoin', 'ethereum', 'crypto', 'btc', 'eth', 'sec crypto'],
    'politics': ['biden', 'trump', 'election', 'congress', 'senate', 'supreme court'],
    'tech': ['ai', 'openai', 'google', 'apple', 'microsoft', 'nvidia'],
    'world': ['ukraine', 'russia', 'china', 'war', 'nato', 'sanctions'],
}


class NewsSource:
    """
    News monitoring for prediction market edge.

    Detects:
    - Breaking news that could resolve markets
    - Sentiment shifts on open markets
    - Information asymmetry opportunities
    """

    def __init__(self):
        self.client = httpx.Client(timeout=30.0)
        self.cache = {}
        self.cache_ttl = 300  # 5 minute cache
        self.api_calls_today = 0
        self.max_api_calls = 150  # Combined limit to stay safe

    def get_probability(self, market: Dict) -> Optional[Dict]:
        """
        Check if recent news affects a market's probability.
        """
        question = market.get('question', '')
        if not question:
            return None

        # Extract relevant keywords
        keywords = self._extract_keywords(question)
        if not keywords:
            return None

        # Search for recent news
        news = self._search_news(keywords)
        if not news:
            return None

        # Analyze sentiment and relevance
        sentiment = self._analyze_sentiment(news, question)
        if not sentiment:
            return None

        return sentiment

    def _extract_keywords(self, question: str) -> List[str]:
        """Extract search keywords from market question."""
        question_lower = question.lower()

        # Check for category keywords
        for category, keywords in CATEGORY_KEYWORDS.items():
            for kw in keywords:
                if kw in question_lower:
                    return [kw, category]

        # Extract proper nouns (capitalized words)
        words = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', question)
        if words:
            # Take top 2-3 proper nouns
            return words[:3]

        return []

    def _search_news(self, keywords: List[str]) -> List[Dict]:
        """Search for recent news using available APIs."""
        cache_key = '+'.join(sorted(keywords))

        # Check cache
        if cache_key in self.cache:
            cached = self.cache[cache_key]
            age = (datetime.now(timezone.utc) - cached['timestamp']).seconds
            if age < self.cache_ttl:
                return cached['data']

        # Check API limits
        if self.api_calls_today >= self.max_api_calls:
            logger.warning("News API daily limit reached")
            return []

        results = []

        # Try NewsData.io first
        if NEWSDATA_KEY:
            results.extend(self._search_newsdata(keywords))

        # Try GNews as backup
        if not results and GNEWS_KEY:
            results.extend(self._search_gnews(keywords))

        # Cache results
        self.cache[cache_key] = {
            'data': results,
            'timestamp': datetime.now(timezone.utc)
        }

        return results

    def _search_newsdata(self, keywords: List[str]) -> List[Dict]:
        """Search using NewsData.io API."""
        try:
            self.api_calls_today += 1

            query = ' OR '.join(keywords)
            params = {
                'apikey': NEWSDATA_KEY,
                'q': query,
                'language': 'en',
                'timeframe': 24,  # Last 24 hours
            }

            resp = self.client.get(NEWSDATA_API, params=params)
            if resp.status_code != 200:
                logger.debug(f"NewsData API error: {resp.status_code}")
                return []

            data = resp.json()
            articles = data.get('results', [])

            return [
                {
                    'title': a.get('title', ''),
                    'description': a.get('description', ''),
                    'source': a.get('source_id', 'unknown'),
                    'published': a.get('pubDate', ''),
                    'url': a.get('link', ''),
                    'sentiment': a.get('sentiment', ''),
                }
                for a in articles[:10]
            ]

        except Exception as e:
            logger.debug(f"NewsData error: {e}")
            return []

    def _search_gnews(self, keywords: List[str]) -> List[Dict]:
        """Search using GNews API."""
        try:
            self.api_calls_today += 1

            query = ' OR '.join(keywords)
            params = {
                'token': GNEWS_KEY,
                'q': query,
                'lang': 'en',
                'max': 10,
            }

            resp = self.client.get(GNEWS_API, params=params)
            if resp.status_code != 200:
                logger.debug(f"GNews API error: {resp.status_code}")
                return []

            data = resp.json()
            articles = data.get('articles', [])

            return [
                {
                    'title': a.get('title', ''),
                    'description': a.get('description', ''),
                    'source': a.get('source', {}).get('name', 'unknown'),
                    'published': a.get('publishedAt', ''),
                    'url': a.get('url', ''),
                }
                for a in articles
            ]

        except Exception as e:
            logger.debug(f"GNews error: {e}")
            return []

    def _analyze_sentiment(self, news: List[Dict], question: str) -> Optional[Dict]:
        """
        Analyze news sentiment relative to market question.

        Returns probability adjustment based on news.
        """
        if not news:
            return None

        question_lower = question.lower()

        # Keywords that suggest YES/NO outcomes
        YES_INDICATORS = ['wins', 'won', 'victory', 'confirmed', 'approved', 'passed', 'nominated', 'selected', 'chosen']
        NO_INDICATORS = ['loses', 'lost', 'defeat', 'rejected', 'denied', 'failed', 'withdrew', 'dropped']

        # Analyze each article
        yes_signals = 0
        no_signals = 0
        relevant_articles = []

        for article in news:
            title = (article.get('title') or '').lower()
            desc = (article.get('description') or '').lower()
            text = f"{title} {desc}"

            # Check if article is relevant to the question
            relevance = self._calculate_relevance(text, question_lower)
            if relevance < 0.3:
                continue

            relevant_articles.append(article)

            # Check for outcome indicators
            for indicator in YES_INDICATORS:
                if indicator in text:
                    yes_signals += 1

            for indicator in NO_INDICATORS:
                if indicator in text:
                    no_signals += 1

        if not relevant_articles:
            return None

        # Calculate sentiment adjustment
        total_signals = yes_signals + no_signals
        if total_signals == 0:
            # News exists but no clear direction
            return {
                'source': 'News Monitoring',
                'probability': 0.5,
                'confidence': 0.3,
                'reasoning': f"Found {len(relevant_articles)} relevant articles but no clear direction",
                'articles': relevant_articles[:3],
            }

        # Adjust probability based on sentiment
        yes_ratio = yes_signals / total_signals
        probability = 0.5 + (yes_ratio - 0.5) * 0.3  # Small adjustment

        return {
            'source': 'News Sentiment Analysis',
            'probability': round(probability, 3),
            'confidence': min(0.6, 0.3 + len(relevant_articles) * 0.05),
            'reasoning': f"Found {len(relevant_articles)} relevant articles: {yes_signals} positive, {no_signals} negative signals",
            'articles': relevant_articles[:3],
            'yes_signals': yes_signals,
            'no_signals': no_signals,
        }

    def _calculate_relevance(self, text: str, question: str) -> float:
        """Calculate how relevant an article is to the question."""
        # Extract key terms from question
        question_words = set(re.findall(r'\b\w{4,}\b', question))

        # Count matches
        matches = sum(1 for word in question_words if word in text)

        # Relevance score
        if not question_words:
            return 0
        return matches / len(question_words)

    def get_breaking_news(self, categories: List[str] = None) -> List[Dict]:
        """Get breaking news for specified categories."""
        categories = categories or ['politics', 'crypto', 'sports']

        all_news = []
        for category in categories:
            keywords = CATEGORY_KEYWORDS.get(category, [category])
            news = self._search_news(keywords[:2])  # Use top 2 keywords
            all_news.extend(news)

        # Sort by recency (if we have dates)
        return all_news[:20]

    def close(self):
        """Close HTTP client."""
        self.client.close()


# Singleton
_source: Optional[NewsSource] = None


def get_news_source() -> NewsSource:
    """Get singleton news source."""
    global _source
    if _source is None:
        _source = NewsSource()
    return _source
