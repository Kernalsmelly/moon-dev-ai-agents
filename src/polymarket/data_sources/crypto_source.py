"""
Crypto Data Source for Polymarket

Provides probability estimates for crypto-related prediction markets using:
1. Fear & Greed Index - market sentiment indicator
2. CoinGecko data - price, volume, market cap trends
3. Historical patterns - how often BTC hits certain prices
4. Funding Rates - leverage/sentiment from perpetual futures (KEY EDGE)
5. Open Interest - market positioning data

Free APIs used:
- Alternative.me Fear & Greed Index (free, no key)
- CoinGecko (free tier: 10-30 calls/min)
- Binance Futures (free, no key for public endpoints)
- Hyperliquid (free, no key)

Funding Rate Edge:
- High positive funding (>0.05%) = overleveraged longs, expect pullback
- High negative funding (<-0.05%) = overleveraged shorts, expect bounce
- This is how top crypto traders time entries
"""

import os
import httpx
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# API endpoints (no keys needed)
FEAR_GREED_API = "https://api.alternative.me/fng/"
COINGECKO_API = "https://api.coingecko.com/api/v3"
BINANCE_FUTURES_API = "https://fapi.binance.com/fapi/v1"
HYPERLIQUID_API = "https://api.hyperliquid.xyz/info"

# Price target patterns with futures symbols
PRICE_PATTERNS = {
    'bitcoin': {
        'symbol': 'BTC',
        'coingecko_id': 'bitcoin',
        'binance_symbol': 'BTCUSDT',
        'hyperliquid_symbol': 'BTC',
        'ath': 108000,  # Approximate ATH
        'current_range': (90000, 110000),  # Typical range
    },
    'ethereum': {
        'symbol': 'ETH',
        'coingecko_id': 'ethereum',
        'binance_symbol': 'ETHUSDT',
        'hyperliquid_symbol': 'ETH',
        'ath': 4800,
        'current_range': (3000, 4000),
    },
    'solana': {
        'symbol': 'SOL',
        'coingecko_id': 'solana',
        'binance_symbol': 'SOLUSDT',
        'hyperliquid_symbol': 'SOL',
        'ath': 260,
        'current_range': (150, 250),
    },
}

# Funding rate thresholds (annualized)
# High funding = overleveraged, expect mean reversion
FUNDING_THRESHOLDS = {
    'extreme_positive': 0.10,   # >10% annualized = very overleveraged long
    'high_positive': 0.05,      # >5% = overleveraged long
    'neutral_high': 0.02,       # Normal bullish
    'neutral_low': -0.02,       # Normal bearish
    'high_negative': -0.05,     # Overleveraged short
    'extreme_negative': -0.10,  # Very overleveraged short
}


class CryptoSource:
    """
    Crypto market data for prediction market edge.

    Uses sentiment and price data to estimate probabilities for:
    - "Will BTC reach $X by date Y?"
    - "Will there be a crypto crash?"
    - "Will ETH flip BTC?"
    """

    def __init__(self):
        self.client = httpx.Client(timeout=30.0)
        self.cache = {}
        self.cache_ttl = 300  # 5 minute cache

    def get_probability(self, market: Dict) -> Optional[Dict]:
        """Get probability estimate for a crypto market."""
        question = (market.get('question') or '').lower()

        # Detect crypto type
        crypto = self._detect_crypto(question)
        if not crypto:
            return None

        # Detect question type
        if self._is_price_target(question):
            return self._estimate_price_target(question, crypto)
        elif self._is_sentiment_question(question):
            return self._estimate_sentiment(question)
        else:
            return self._estimate_general(question, crypto)

    def _detect_crypto(self, question: str) -> Optional[Dict]:
        """Detect which cryptocurrency the question is about."""
        question_lower = question.lower()

        for name, data in PRICE_PATTERNS.items():
            if name in question_lower or data['symbol'].lower() in question_lower:
                return data

        # Check for generic crypto terms
        if any(term in question_lower for term in ['crypto', 'cryptocurrency', 'defi']):
            return PRICE_PATTERNS['bitcoin']  # Default to BTC

        return None

    def _is_price_target(self, question: str) -> bool:
        """Check if question is about price target."""
        patterns = ['reach', 'hit', 'price', '$', 'above', 'below', 'exceed']
        return any(p in question.lower() for p in patterns)

    def _is_sentiment_question(self, question: str) -> bool:
        """Check if question is about market sentiment."""
        patterns = ['crash', 'bull', 'bear', 'rally', 'dump', 'moon', 'collapse']
        return any(p in question.lower() for p in patterns)

    def _estimate_price_target(self, question: str, crypto: Dict) -> Optional[Dict]:
        """Estimate probability of hitting a price target."""
        # Extract target price
        target = self._extract_price(question)
        if not target:
            return None

        # Get current price
        current = self._get_current_price(crypto['coingecko_id'])
        if not current:
            return None

        # Get fear/greed for sentiment adjustment
        fg = self._get_fear_greed()

        # Calculate base probability
        # Higher target = lower probability
        # Use ratio to ATH as baseline
        ath = crypto.get('ath', current * 2)

        if target <= current:
            # Already above target
            probability = 0.95
            reasoning = f"Already above target (current: ${current:,.0f})"
        elif target <= ath:
            # Below ATH - reasonable target
            # Linear interpolation: current = 90%, ATH = 40%
            ratio = (target - current) / (ath - current) if ath > current else 0.5
            probability = 0.90 - (ratio * 0.50)
            reasoning = f"Target ${target:,} is {ratio*100:.0f}% of the way to ATH"
        else:
            # Above ATH - harder
            above_ath_ratio = (target - ath) / ath
            probability = max(0.05, 0.40 - above_ath_ratio * 0.30)
            reasoning = f"Target ${target:,} is {above_ath_ratio*100:.0f}% above ATH"

        # Adjust for sentiment (Fear & Greed)
        if fg:
            fg_value = fg.get('value', 50)
            # High fear = lower probability of hitting targets
            # High greed = higher probability
            sentiment_adj = (fg_value - 50) / 200  # -0.25 to +0.25
            probability = max(0.05, min(0.95, probability + sentiment_adj))
            reasoning += f" | Sentiment: {fg.get('classification', 'Neutral')} ({fg_value})"

        # KEY EDGE: Adjust for funding rates
        # This is how top crypto traders time the market
        funding = self._get_funding_rate(crypto)
        if funding:
            price_bias = funding.get('price_bias', 0)

            # For upside targets: negative funding (shorts overleveraged) = higher probability
            # For downside targets: positive funding (longs overleveraged) = higher probability
            if target > current:
                # Upside target
                probability = max(0.05, min(0.95, probability + price_bias))
            else:
                # Downside target (already at/above)
                probability = max(0.05, min(0.95, probability - price_bias))

            reasoning += f" | Funding: {funding['annualized']:.1f}% ({funding['sentiment']})"

        # Confidence based on data freshness and target reasonableness
        confidence = 0.6
        if target <= ath:
            confidence = 0.7
        if fg:
            confidence += 0.05
        if funding:
            confidence += 0.10  # Funding adds significant edge

        return {
            'source': f'Crypto Model ({crypto["symbol"]})',
            'probability': round(probability, 3),
            'confidence': min(0.85, confidence),
            'reasoning': reasoning,
            'current_price': current,
            'target_price': target,
            'fear_greed': fg,
            'funding_rate': funding,
        }

    def _estimate_sentiment(self, question: str) -> Optional[Dict]:
        """Estimate probability for sentiment-based questions."""
        fg = self._get_fear_greed()
        if not fg:
            return None

        fg_value = fg.get('value', 50)
        classification = fg.get('classification', 'Neutral')

        question_lower = question.lower()

        # Crash/dump questions
        if any(w in question_lower for w in ['crash', 'dump', 'collapse', 'bear']):
            # High fear = higher probability of continued downside
            if fg_value < 25:  # Extreme fear
                probability = 0.35  # But often bottoms form here
                reasoning = f"Extreme fear ({fg_value}) - often marks bottoms"
            elif fg_value < 40:  # Fear
                probability = 0.45
                reasoning = f"Fear zone ({fg_value}) - elevated crash risk"
            else:
                probability = 0.25
                reasoning = f"Neutral/Greed ({fg_value}) - lower crash probability"

        # Bull/rally/moon questions
        elif any(w in question_lower for w in ['bull', 'rally', 'moon', 'pump']):
            if fg_value > 75:  # Extreme greed
                probability = 0.40  # Often tops form here
                reasoning = f"Extreme greed ({fg_value}) - often marks tops"
            elif fg_value > 55:  # Greed
                probability = 0.55
                reasoning = f"Greed zone ({fg_value}) - momentum continues"
            else:
                probability = 0.35
                reasoning = f"Fear/Neutral ({fg_value}) - rally less likely"
        else:
            probability = 0.50
            reasoning = f"Neutral estimate - F&G: {fg_value}"

        return {
            'source': 'Crypto Fear & Greed Index',
            'probability': probability,
            'confidence': 0.55,
            'reasoning': reasoning,
            'fear_greed': fg,
        }

    def _estimate_general(self, question: str, crypto: Dict) -> Optional[Dict]:
        """General estimate for other crypto questions."""
        fg = self._get_fear_greed()
        current = self._get_current_price(crypto['coingecko_id'])

        # Default to neutral with low confidence
        return {
            'source': f'Crypto Baseline ({crypto["symbol"]})',
            'probability': 0.50,
            'confidence': 0.40,
            'reasoning': f"No specific model - current price ${current:,.0f}, F&G: {fg.get('value', 'N/A') if fg else 'N/A'}",
            'current_price': current,
            'fear_greed': fg,
        }

    def _extract_price(self, question: str) -> Optional[float]:
        """Extract price target from question."""
        # Match patterns like $100k, $100,000, 100000
        patterns = [
            r'\$(\d{1,3}(?:,\d{3})*(?:\.\d+)?)[k]?',  # $100,000 or $100k
            r'(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?:dollars|usd)',  # 100000 dollars
            r'(\d+)[k]\b',  # 100k
        ]

        for pattern in patterns:
            match = re.search(pattern, question.lower().replace(',', ''))
            if match:
                value = match.group(1).replace(',', '')
                num = float(value)
                # Handle 'k' suffix
                if 'k' in question.lower()[match.start():match.end()+2]:
                    num *= 1000
                # Sanity check for crypto prices
                if 1000 <= num <= 10000000:
                    return num

        return None

    def _get_current_price(self, coingecko_id: str) -> Optional[float]:
        """Get current price from CoinGecko."""
        cache_key = f"price_{coingecko_id}"

        if cache_key in self.cache:
            cached = self.cache[cache_key]
            if (datetime.now(timezone.utc) - cached['time']).seconds < self.cache_ttl:
                return cached['price']

        try:
            url = f"{COINGECKO_API}/simple/price"
            params = {'ids': coingecko_id, 'vs_currencies': 'usd'}
            resp = self.client.get(url, params=params)

            if resp.status_code == 200:
                data = resp.json()
                price = data.get(coingecko_id, {}).get('usd')
                if price:
                    self.cache[cache_key] = {
                        'price': price,
                        'time': datetime.now(timezone.utc)
                    }
                    return price
        except Exception as e:
            logger.debug(f"CoinGecko error: {e}")

        return None

    def _get_fear_greed(self) -> Optional[Dict]:
        """Get current Fear & Greed Index."""
        cache_key = "fear_greed"

        if cache_key in self.cache:
            cached = self.cache[cache_key]
            if (datetime.now(timezone.utc) - cached['time']).seconds < self.cache_ttl:
                return cached['data']

        try:
            resp = self.client.get(FEAR_GREED_API)
            if resp.status_code == 200:
                data = resp.json()
                if data.get('data'):
                    fg = data['data'][0]
                    result = {
                        'value': int(fg.get('value', 50)),
                        'classification': fg.get('value_classification', 'Neutral'),
                        'timestamp': fg.get('timestamp'),
                    }
                    self.cache[cache_key] = {
                        'data': result,
                        'time': datetime.now(timezone.utc)
                    }
                    return result
        except Exception as e:
            logger.debug(f"Fear & Greed API error: {e}")

        return None

    def _get_funding_rate(self, crypto: Dict) -> Optional[Dict]:
        """
        Get current funding rate from Binance Futures.

        Funding rate is the key edge indicator:
        - Positive = longs pay shorts (bullish sentiment, potential top)
        - Negative = shorts pay longs (bearish sentiment, potential bottom)

        Returns annualized funding rate and interpretation.
        """
        cache_key = f"funding_{crypto['symbol']}"

        if cache_key in self.cache:
            cached = self.cache[cache_key]
            if (datetime.now(timezone.utc) - cached['time']).seconds < self.cache_ttl:
                return cached['data']

        binance_symbol = crypto.get('binance_symbol', f"{crypto['symbol']}USDT")

        try:
            # Binance funding rate endpoint
            url = f"{BINANCE_FUTURES_API}/fundingRate"
            params = {'symbol': binance_symbol, 'limit': 1}
            resp = self.client.get(url, params=params)

            if resp.status_code == 200:
                data = resp.json()
                if data:
                    rate = float(data[0].get('fundingRate', 0))

                    # Convert to annualized (funding is every 8 hours = 3x daily = 1095x yearly)
                    annualized = rate * 1095

                    # Interpret the rate
                    if annualized > FUNDING_THRESHOLDS['extreme_positive']:
                        sentiment = 'extreme_greed'
                        interpretation = 'Extremely overleveraged longs - HIGH crash risk'
                        price_bias = -0.15  # Expect 15% pullback probability boost
                    elif annualized > FUNDING_THRESHOLDS['high_positive']:
                        sentiment = 'greed'
                        interpretation = 'Overleveraged longs - elevated pullback risk'
                        price_bias = -0.08
                    elif annualized < FUNDING_THRESHOLDS['extreme_negative']:
                        sentiment = 'extreme_fear'
                        interpretation = 'Extremely overleveraged shorts - HIGH bounce probability'
                        price_bias = 0.15
                    elif annualized < FUNDING_THRESHOLDS['high_negative']:
                        sentiment = 'fear'
                        interpretation = 'Overleveraged shorts - elevated bounce probability'
                        price_bias = 0.08
                    else:
                        sentiment = 'neutral'
                        interpretation = 'Balanced positioning'
                        price_bias = 0.0

                    result = {
                        'rate': round(rate * 100, 4),  # As percentage
                        'annualized': round(annualized * 100, 2),  # Annualized %
                        'sentiment': sentiment,
                        'interpretation': interpretation,
                        'price_bias': price_bias,
                        'timestamp': data[0].get('fundingTime'),
                    }

                    self.cache[cache_key] = {
                        'data': result,
                        'time': datetime.now(timezone.utc)
                    }
                    return result

        except Exception as e:
            logger.debug(f"Binance funding rate error: {e}")

        # Fallback to Hyperliquid
        return self._get_hyperliquid_funding(crypto)

    def _get_hyperliquid_funding(self, crypto: Dict) -> Optional[Dict]:
        """Fallback: Get funding from Hyperliquid."""
        try:
            # Hyperliquid meta endpoint
            resp = self.client.post(
                HYPERLIQUID_API,
                json={'type': 'metaAndAssetCtxs'},
                headers={'Content-Type': 'application/json'}
            )

            if resp.status_code == 200:
                data = resp.json()
                if len(data) >= 2:
                    asset_ctxs = data[1]
                    universe = data[0].get('universe', [])

                    # Find our asset
                    hl_symbol = crypto.get('hyperliquid_symbol', crypto['symbol'])
                    for i, asset in enumerate(universe):
                        if asset.get('name') == hl_symbol:
                            ctx = asset_ctxs[i]
                            funding = float(ctx.get('funding', 0))
                            annualized = funding * 24 * 365  # Hourly funding

                            # Same interpretation as Binance
                            if annualized > 0.05:
                                sentiment = 'greed'
                                price_bias = -0.08
                            elif annualized < -0.05:
                                sentiment = 'fear'
                                price_bias = 0.08
                            else:
                                sentiment = 'neutral'
                                price_bias = 0.0

                            return {
                                'rate': round(funding * 100, 4),
                                'annualized': round(annualized * 100, 2),
                                'sentiment': sentiment,
                                'interpretation': f'Hyperliquid funding: {sentiment}',
                                'price_bias': price_bias,
                                'source': 'hyperliquid',
                            }

        except Exception as e:
            logger.debug(f"Hyperliquid funding error: {e}")

        return None

    def _get_open_interest(self, crypto: Dict) -> Optional[Dict]:
        """Get open interest data from Binance."""
        try:
            binance_symbol = crypto.get('binance_symbol', f"{crypto['symbol']}USDT")
            url = f"{BINANCE_FUTURES_API}/openInterest"
            params = {'symbol': binance_symbol}

            resp = self.client.get(url, params=params)
            if resp.status_code == 200:
                data = resp.json()
                oi = float(data.get('openInterest', 0))

                return {
                    'open_interest': oi,
                    'symbol': binance_symbol,
                }

        except Exception as e:
            logger.debug(f"Open interest error: {e}")

        return None

    def get_market_overview(self) -> Dict:
        """Get current crypto market overview with funding rates."""
        fg = self._get_fear_greed()
        btc = self._get_current_price('bitcoin')
        eth = self._get_current_price('ethereum')

        # Get funding rates for major assets
        btc_funding = self._get_funding_rate(PRICE_PATTERNS['bitcoin'])
        eth_funding = self._get_funding_rate(PRICE_PATTERNS['ethereum'])
        sol_funding = self._get_funding_rate(PRICE_PATTERNS['solana'])

        return {
            'fear_greed': fg,
            'btc_price': btc,
            'eth_price': eth,
            'funding_rates': {
                'btc': btc_funding,
                'eth': eth_funding,
                'sol': sol_funding,
            },
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }

    def get_funding_summary(self) -> Dict:
        """
        Get a summary of funding rates across major assets.

        This is THE key edge indicator for crypto prediction markets.
        Use this to adjust probabilities for price target questions.
        """
        summary = {
            'overall_sentiment': 'neutral',
            'assets': {},
            'edge_signal': None,
        }

        sentiments = []

        for name, crypto in PRICE_PATTERNS.items():
            funding = self._get_funding_rate(crypto)
            if funding:
                summary['assets'][name] = funding
                sentiments.append(funding.get('sentiment', 'neutral'))

        # Determine overall sentiment
        if sentiments.count('extreme_greed') >= 2 or sentiments.count('greed') >= 3:
            summary['overall_sentiment'] = 'overleveraged_long'
            summary['edge_signal'] = 'Fade upside targets - market overleveraged long'
        elif sentiments.count('extreme_fear') >= 2 or sentiments.count('fear') >= 3:
            summary['overall_sentiment'] = 'overleveraged_short'
            summary['edge_signal'] = 'Favor upside targets - market overleveraged short'
        else:
            summary['overall_sentiment'] = 'neutral'
            summary['edge_signal'] = 'No clear funding edge'

        return summary

    def close(self):
        """Close HTTP client."""
        self.client.close()


# Singleton
_source: Optional[CryptoSource] = None


def get_crypto_source() -> CryptoSource:
    """Get singleton crypto source."""
    global _source
    if _source is None:
        _source = CryptoSource()
    return _source
