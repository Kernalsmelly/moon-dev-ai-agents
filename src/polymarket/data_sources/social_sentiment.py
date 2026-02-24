"""
Social Sentiment Aggregator for Polymarket

Aggregates sentiment from multiple free social sources:
1. Reddit - Via public JSON endpoints (no API key needed)
2. Google Trends - Interest over time
3. Twitter/X - Via Nitter mirrors (limited)

Key Edge Signals:
- Sudden spike in Reddit mentions = breaking news
- Google Trends rising = increased attention
- Social sentiment diverging from market = opportunity

Note: These are supplementary signals. Combine with other data sources.
"""

import httpx
import logging
import re
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)

# Reddit public JSON (no API key needed)
REDDIT_BASE = "https://www.reddit.com"

# Subreddits to monitor by category
SUBREDDITS = {
    'crypto': ['cryptocurrency', 'bitcoin', 'ethereum', 'solana', 'defi'],
    'politics': ['politics', 'news', 'worldnews'],
    'sports': ['nba', 'nfl', 'baseball', 'hockey', 'mma'],
    'entertainment': ['movies', 'oscars', 'television', 'entertainment'],
    'tech': ['technology', 'artificial', 'stocks'],
}

# Sentiment keywords
BULLISH_WORDS = [
    'moon', 'bullish', 'pump', 'buy', 'long', 'winner', 'wins', 'victory',
    'up', 'surge', 'rally', 'breakout', 'ath', 'huge', 'amazing', 'love'
]

BEARISH_WORDS = [
    'crash', 'bearish', 'dump', 'sell', 'short', 'loser', 'loses', 'defeat',
    'down', 'plunge', 'collapse', 'scam', 'dead', 'terrible', 'hate', 'fear'
]


class SocialSentiment:
    """
    Aggregate sentiment from social media sources.

    Provides supplementary edge for prediction markets by
    detecting social momentum before price moves.
    """

    def __init__(self):
        self.client = httpx.Client(
            timeout=15.0,
            headers={
                'User-Agent': 'Mozilla/5.0 (compatible; PredictionBot/1.0)'
            }
        )
        self.cache = {}
        self.cache_ttl = 600  # 10 minute cache

    def get_probability(self, market: Dict) -> Optional[Dict]:
        """Get probability adjustment from social sentiment."""
        question = (market.get('question') or '').lower()
        category = self._detect_category(question)

        if not category:
            return None

        # Get Reddit sentiment
        reddit = self.get_reddit_sentiment(question, category)

        if not reddit:
            return None

        return reddit

    def _detect_category(self, question: str) -> Optional[str]:
        """Detect market category from question."""
        q = question.lower()

        if any(w in q for w in ['bitcoin', 'btc', 'ethereum', 'eth', 'crypto', 'solana']):
            return 'crypto'
        if any(w in q for w in ['election', 'president', 'senate', 'congress', 'vote']):
            return 'politics'
        if any(w in q for w in ['nba', 'nfl', 'mlb', 'nhl', 'ufc', 'super bowl']):
            return 'sports'
        if any(w in q for w in ['oscar', 'emmy', 'movie', 'film', 'netflix']):
            return 'entertainment'

        return None

    def get_reddit_sentiment(
        self,
        question: str,
        category: str = None,
        hours: int = 24,
    ) -> Optional[Dict]:
        """
        Get sentiment from Reddit posts and comments.

        Uses Reddit's public JSON endpoints (no API key needed).
        """
        cache_key = f"reddit_{category}_{hash(question) % 10000}"

        if cache_key in self.cache:
            cached = self.cache[cache_key]
            if (datetime.now(timezone.utc) - cached['time']).seconds < self.cache_ttl:
                return cached['data']

        # Get relevant subreddits
        subreddits = SUBREDDITS.get(category, ['all'])

        # Extract search terms from question
        terms = self._extract_search_terms(question)
        if not terms:
            return None

        all_posts = []

        # Search each subreddit
        for sub in subreddits[:3]:  # Limit to 3 subreddits
            posts = self._search_subreddit(sub, terms)
            all_posts.extend(posts)

        if not all_posts:
            return None

        # Analyze sentiment
        sentiment = self._analyze_reddit_sentiment(all_posts, question)

        self.cache[cache_key] = {
            'data': sentiment,
            'time': datetime.now(timezone.utc)
        }

        return sentiment

    def _extract_search_terms(self, question: str) -> List[str]:
        """Extract key terms for searching."""
        # Remove common words
        stop_words = {'will', 'the', 'a', 'an', 'is', 'be', 'to', 'in', 'by', 'on', 'at', 'for', 'or', 'and'}

        words = re.findall(r'\b\w{3,}\b', question.lower())
        terms = [w for w in words if w not in stop_words]

        # Prioritize proper nouns and numbers
        proper_nouns = re.findall(r'\b[A-Z][a-z]+\b', question)

        # Combine, prioritizing proper nouns
        result = proper_nouns[:2] + [t for t in terms if t.lower() not in [p.lower() for p in proper_nouns]][:2]

        return result[:4]  # Max 4 terms

    def _search_subreddit(self, subreddit: str, terms: List[str]) -> List[Dict]:
        """Search a subreddit for relevant posts."""
        try:
            query = '+'.join(terms)
            url = f"{REDDIT_BASE}/r/{subreddit}/search.json"
            params = {
                'q': query,
                'restrict_sr': 'on',
                'sort': 'new',
                't': 'day',
                'limit': 25,
            }

            resp = self.client.get(url, params=params)

            if resp.status_code == 200:
                data = resp.json()
                posts = data.get('data', {}).get('children', [])

                return [
                    {
                        'title': p['data'].get('title', ''),
                        'selftext': p['data'].get('selftext', '')[:500],
                        'score': p['data'].get('score', 0),
                        'num_comments': p['data'].get('num_comments', 0),
                        'subreddit': subreddit,
                        'created': p['data'].get('created_utc', 0),
                    }
                    for p in posts
                ]

        except Exception as e:
            logger.debug(f"Reddit search error: {e}")

        return []

    def _analyze_reddit_sentiment(self, posts: List[Dict], question: str) -> Dict:
        """Analyze sentiment from Reddit posts."""
        if not posts:
            return None

        total_bullish = 0
        total_bearish = 0
        total_engagement = 0
        relevant_posts = 0

        question_lower = question.lower()

        for post in posts:
            text = f"{post['title']} {post['selftext']}".lower()

            # Weight by engagement (score + comments)
            engagement = max(1, post['score'] + post['num_comments'])
            total_engagement += engagement

            # Count sentiment words
            bullish = sum(1 for w in BULLISH_WORDS if w in text)
            bearish = sum(1 for w in BEARISH_WORDS if w in text)

            # Weight by engagement
            total_bullish += bullish * engagement
            total_bearish += bearish * engagement

            if bullish > 0 or bearish > 0:
                relevant_posts += 1

        if total_bullish + total_bearish == 0:
            return {
                'source': 'Reddit Sentiment',
                'probability': 0.50,
                'confidence': 0.30,
                'reasoning': f"Found {len(posts)} posts but no clear sentiment signal",
                'post_count': len(posts),
            }

        # Calculate sentiment ratio
        sentiment_score = total_bullish / (total_bullish + total_bearish)

        # Adjust probability (sentiment influences but doesn't dominate)
        probability = 0.50 + (sentiment_score - 0.5) * 0.2

        # Confidence based on volume
        confidence = min(0.60, 0.30 + relevant_posts * 0.02 + (total_engagement / 1000) * 0.05)

        # Determine sentiment label
        if sentiment_score > 0.65:
            sentiment_label = 'bullish'
        elif sentiment_score < 0.35:
            sentiment_label = 'bearish'
        else:
            sentiment_label = 'neutral'

        return {
            'source': 'Reddit Social Sentiment',
            'probability': round(probability, 3),
            'confidence': round(confidence, 3),
            'reasoning': f"Reddit: {sentiment_label} ({relevant_posts} relevant posts, {total_engagement:,} engagement)",
            'sentiment_score': round(sentiment_score, 3),
            'sentiment_label': sentiment_label,
            'post_count': len(posts),
            'relevant_posts': relevant_posts,
            'total_engagement': total_engagement,
            'bullish_signals': total_bullish,
            'bearish_signals': total_bearish,
        }

    def get_trending_topics(self, category: str = None) -> List[Dict]:
        """
        Get trending topics from Reddit.

        Useful for identifying markets with high social attention.
        """
        subreddits = SUBREDDITS.get(category, ['all'])
        topics = []

        for sub in subreddits[:2]:
            try:
                url = f"{REDDIT_BASE}/r/{sub}/hot.json"
                params = {'limit': 10}

                resp = self.client.get(url, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    posts = data.get('data', {}).get('children', [])

                    for p in posts[:5]:
                        topics.append({
                            'title': p['data'].get('title', ''),
                            'subreddit': sub,
                            'score': p['data'].get('score', 0),
                            'comments': p['data'].get('num_comments', 0),
                        })

            except Exception as e:
                logger.debug(f"Trending fetch error: {e}")

        # Sort by engagement
        topics.sort(key=lambda x: x['score'] + x['comments'], reverse=True)
        return topics[:10]

    def get_sentiment_summary(self, category: str) -> Dict:
        """
        Get overall sentiment summary for a category.

        Useful for identifying if market sentiment diverges from prices.
        """
        subreddits = SUBREDDITS.get(category, [category])

        total_bullish = 0
        total_bearish = 0
        total_posts = 0

        for sub in subreddits[:3]:
            try:
                url = f"{REDDIT_BASE}/r/{sub}/new.json"
                params = {'limit': 50}

                resp = self.client.get(url, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    posts = data.get('data', {}).get('children', [])

                    for p in posts:
                        text = f"{p['data'].get('title', '')} {p['data'].get('selftext', '')}".lower()

                        bullish = sum(1 for w in BULLISH_WORDS if w in text)
                        bearish = sum(1 for w in BEARISH_WORDS if w in text)

                        total_bullish += bullish
                        total_bearish += bearish
                        total_posts += 1

            except Exception as e:
                logger.debug(f"Sentiment summary error: {e}")

        if total_bullish + total_bearish == 0:
            return {
                'category': category,
                'sentiment': 'neutral',
                'score': 0.50,
                'posts_analyzed': total_posts,
            }

        score = total_bullish / (total_bullish + total_bearish)

        if score > 0.60:
            sentiment = 'bullish'
        elif score < 0.40:
            sentiment = 'bearish'
        else:
            sentiment = 'neutral'

        return {
            'category': category,
            'sentiment': sentiment,
            'score': round(score, 3),
            'posts_analyzed': total_posts,
            'bullish_signals': total_bullish,
            'bearish_signals': total_bearish,
        }

    def close(self):
        """Close HTTP client."""
        self.client.close()


# Singleton
_source: Optional[SocialSentiment] = None


def get_social_sentiment() -> SocialSentiment:
    """Get singleton social sentiment source."""
    global _source
    if _source is None:
        _source = SocialSentiment()
    return _source
