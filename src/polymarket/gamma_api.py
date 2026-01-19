"""
Polymarket Gamma API Wrapper

Fetches ALL markets from Polymarket's Gamma API, not just WebSocket trades.
This is critical for micro-edge strategy: we need to scan the full universe
of markets to find low-liquidity opportunities.

API Documentation: https://docs.polymarket.com/
"""

import os
import time
import json
import logging
from typing import List, Dict, Optional, Any
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

# Default endpoints
GAMMA_API_BASE = os.getenv("GAMMA_API_BASE", "https://gamma-api.polymarket.com")
CLOB_API_BASE = os.getenv("CLOB_API_BASE", "https://clob.polymarket.com")


class GammaAPI:
    """
    Wrapper for Polymarket Gamma API to fetch all markets.

    Unlike WebSocket which only shows trades, Gamma API gives us:
    - All active markets (including low-volume ones)
    - Liquidity and volume metrics
    - Resolution dates
    - Token IDs for trading
    """

    def __init__(
        self,
        gamma_base: str = GAMMA_API_BASE,
        clob_base: str = CLOB_API_BASE,
        timeout: float = 30.0,
        max_retries: int = 3,
    ):
        self.gamma_base = gamma_base.rstrip('/')
        self.clob_base = clob_base.rstrip('/')
        self.timeout = timeout
        self.max_retries = max_retries
        self._client: Optional[httpx.Client] = None

    def _get_client(self) -> httpx.Client:
        """Get or create HTTP client with connection pooling."""
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.timeout,
                headers={
                    'Accept': 'application/json',
                    'User-Agent': 'MoonDev-MicroEdge/1.0',
                },
            )
        return self._client

    def _request_with_retry(
        self,
        method: str,
        url: str,
        params: Optional[Dict] = None,
        json_data: Optional[Dict] = None,
    ) -> Optional[Dict]:
        """Make HTTP request with retry logic."""
        client = self._get_client()
        last_error = None

        for attempt in range(self.max_retries):
            try:
                if method.upper() == 'GET':
                    response = client.get(url, params=params)
                elif method.upper() == 'POST':
                    response = client.post(url, params=params, json=json_data)
                else:
                    raise ValueError(f"Unsupported method: {method}")

                response.raise_for_status()
                return response.json()

            except httpx.HTTPStatusError as e:
                last_error = e
                logger.warning(f"HTTP {e.response.status_code} on attempt {attempt + 1}: {url}")
                if e.response.status_code == 429:  # Rate limited
                    time.sleep(2 ** attempt)
                elif e.response.status_code >= 500:
                    time.sleep(1)
                else:
                    break  # Don't retry 4xx errors

            except httpx.RequestError as e:
                last_error = e
                logger.warning(f"Request error on attempt {attempt + 1}: {e}")
                time.sleep(1)

        logger.error(f"All {self.max_retries} attempts failed for {url}: {last_error}")
        return None

    def get_all_markets(
        self,
        active_only: bool = True,
        limit: int = 500,
        offset: int = 0,
        max_pages: int = 5,
    ) -> List[Dict]:
        """
        Fetch all markets from Gamma API.

        Args:
            active_only: Only return active (non-resolved) markets
            limit: Max markets per request (API may cap this)
            offset: Pagination offset

        Returns:
            List of market dictionaries with:
            - id: Market/condition ID
            - question: Market question text
            - slug: URL slug
            - endDate: Resolution date
            - liquidity: Total liquidity
            - volume: Trading volume
            - outcomes: YES/NO prices and token IDs
        """
        all_markets = []
        current_offset = offset
        pages_fetched = 0

        while pages_fetched < max_pages:
            url = f"{self.gamma_base}/markets"
            params = {
                'limit': limit,
                'offset': current_offset,
                'closed': 'false',  # Get only open/non-resolved markets
            }

            data = self._request_with_retry('GET', url, params=params)

            if data is None:
                logger.error("Failed to fetch markets from Gamma API")
                break

            # Handle different response formats
            markets = data if isinstance(data, list) else data.get('data', data.get('markets', []))

            if not markets:
                break

            all_markets.extend(markets)
            pages_fetched += 1
            logger.info(f"Fetched {len(markets)} markets (total: {len(all_markets)}, page {pages_fetched}/{max_pages})")

            # Check if we got a full page
            if len(markets) < limit:
                break

            current_offset += limit
            time.sleep(0.1)  # Rate limit courtesy

        return all_markets

    def get_active_markets_with_upcoming_resolution(
        self,
        max_pages: int = 10,
    ) -> List[Dict]:
        """
        Fetch and filter for truly active markets with upcoming resolution dates.

        Filters out:
        - Closed/resolved markets
        - Markets with past end dates
        - Markets with no end date

        Returns markets sorted by end_date (soonest first).
        """
        raw_markets = self.get_all_markets(active_only=True, max_pages=max_pages)
        now = datetime.now(timezone.utc)

        active_markets = []
        for raw in raw_markets:
            parsed = self.parse_market(raw)

            # Skip closed/resolved markets (should already be filtered by API, but double-check)
            if parsed.get('closed') or parsed.get('resolved'):
                continue

            # Skip markets with no end date
            if not parsed.get('end_date'):
                continue

            # Skip markets with past end date
            if parsed['end_date'] < now:
                continue

            # Skip markets with no prices (likely inactive)
            if parsed.get('yes_price') is None or parsed.get('no_price') is None:
                continue

            # Skip markets with zero liquidity
            if parsed.get('liquidity', 0) <= 0:
                continue

            active_markets.append(parsed)

        # Sort by end_date (soonest first)
        active_markets.sort(key=lambda m: m['end_date'])

        logger.info(f"Filtered to {len(active_markets)} truly active markets with upcoming resolution")
        return active_markets

    def get_market_by_id(self, condition_id: str) -> Optional[Dict]:
        """Fetch a single market by condition ID."""
        url = f"{self.gamma_base}/markets/{condition_id}"
        return self._request_with_retry('GET', url)

    def get_market_by_slug(self, slug: str) -> Optional[Dict]:
        """Fetch a single market by URL slug."""
        url = f"{self.gamma_base}/markets"
        params = {'slug': slug}
        data = self._request_with_retry('GET', url, params=params)

        if data and isinstance(data, list) and len(data) > 0:
            return data[0]
        return data

    def get_orderbook(self, token_id: str) -> Optional[Dict]:
        """
        Fetch orderbook for a specific token (YES or NO outcome).

        Returns:
            Dict with 'bids' and 'asks' arrays
        """
        url = f"{self.clob_base}/book"
        params = {'token_id': token_id}
        return self._request_with_retry('GET', url, params=params)

    def get_prices(self, token_ids: List[str]) -> Optional[Dict]:
        """
        Fetch current prices for multiple tokens.

        Args:
            token_ids: List of token IDs

        Returns:
            Dict mapping token_id -> price
        """
        url = f"{self.clob_base}/prices"
        params = {'token_ids': ','.join(token_ids)}
        return self._request_with_retry('GET', url, params=params)

    def search_markets(self, query: str, limit: int = 20) -> List[Dict]:
        """
        Search markets by keyword.

        Useful for finding niche markets by category.
        """
        url = f"{self.gamma_base}/markets"
        params = {
            'search': query,
            'limit': limit,
            'active': 'true',
        }

        data = self._request_with_retry('GET', url, params=params)

        if data is None:
            return []

        return data if isinstance(data, list) else data.get('data', data.get('markets', []))

    def get_events(self, limit: int = 100) -> List[Dict]:
        """
        Fetch events (groups of related markets).

        Events contain multiple markets for the same underlying event,
        useful for correlation detection.
        """
        url = f"{self.gamma_base}/events"
        params = {
            'limit': limit,
            'active': 'true',
        }

        data = self._request_with_retry('GET', url, params=params)

        if data is None:
            return []

        return data if isinstance(data, list) else data.get('data', data.get('events', []))

    def parse_market(self, raw_market: Dict) -> Dict:
        """
        Parse raw API response into standardized market dict.

        Handles different API response formats and normalizes fields.
        """
        # Extract basic info
        market = {
            'condition_id': raw_market.get('id') or raw_market.get('conditionId') or raw_market.get('condition_id'),
            'question': raw_market.get('question') or raw_market.get('title') or '',
            'description': raw_market.get('description') or '',
            'slug': raw_market.get('slug') or raw_market.get('eventSlug') or '',
            'category': raw_market.get('category') or raw_market.get('tags', [''])[0] if raw_market.get('tags') else '',
            # Track if market is closed/resolved
            'closed': raw_market.get('closed', False),
            'resolved': raw_market.get('resolved', False),
            'active': raw_market.get('active', True),
        }

        # Extract timestamps
        end_date_str = raw_market.get('endDate') or raw_market.get('end_date') or raw_market.get('resolutionDate')
        if end_date_str:
            try:
                if isinstance(end_date_str, str):
                    market['end_date'] = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
                else:
                    market['end_date'] = datetime.fromtimestamp(end_date_str, tz=timezone.utc)
            except (ValueError, TypeError):
                market['end_date'] = None
        else:
            market['end_date'] = None

        # Extract liquidity metrics
        market['liquidity'] = float(raw_market.get('liquidity') or raw_market.get('liquidityNum') or 0)
        market['volume'] = float(raw_market.get('volume') or raw_market.get('volumeNum') or 0)
        market['volume_24h'] = float(raw_market.get('volume24hr') or raw_market.get('volume_24h') or 0)

        # Extract prices and outcomes
        outcomes = raw_market.get('outcomes') or raw_market.get('tokens') or []

        # Handle case where outcomes is a JSON string instead of a list
        if isinstance(outcomes, str):
            try:
                outcomes = json.loads(outcomes)
            except (json.JSONDecodeError, TypeError):
                outcomes = []

        # Ensure outcomes is a list
        if not isinstance(outcomes, list):
            outcomes = []

        yes_outcome = None
        no_outcome = None

        for outcome in outcomes:
            # Skip if outcome is not a dict (could be a string)
            if not isinstance(outcome, dict):
                continue
            name = (outcome.get('outcome') or outcome.get('name') or '').lower()
            if name == 'yes':
                yes_outcome = outcome
            elif name == 'no':
                no_outcome = outcome

        # If no explicit outcomes (just strings like ["Yes","No"]), try to extract from other fields
        if yes_outcome is None and 'outcomePrices' in raw_market:
            prices = raw_market.get('outcomePrices', [])
            # Handle JSON string
            if isinstance(prices, str):
                try:
                    prices = json.loads(prices)
                except (json.JSONDecodeError, TypeError):
                    prices = []
            if isinstance(prices, list) and len(prices) >= 2:
                try:
                    market['yes_price'] = float(prices[0])
                    market['no_price'] = float(prices[1])
                except (ValueError, TypeError):
                    market['yes_price'] = None
                    market['no_price'] = None
            else:
                market['yes_price'] = None
                market['no_price'] = None
        else:
            market['yes_price'] = float(yes_outcome.get('price') or 0) if yes_outcome else None
            market['no_price'] = float(no_outcome.get('price') or 0) if no_outcome else None

        # Extract token IDs for trading
        market['yes_token_id'] = yes_outcome.get('id') or yes_outcome.get('token_id') if yes_outcome else None
        market['no_token_id'] = no_outcome.get('id') or no_outcome.get('token_id') if no_outcome else None

        # Fallback to clobTokenIds if available
        if market['yes_token_id'] is None and 'clobTokenIds' in raw_market:
            tokens = raw_market.get('clobTokenIds', [])
            # Handle JSON string
            if isinstance(tokens, str):
                try:
                    tokens = json.loads(tokens)
                except (json.JSONDecodeError, TypeError):
                    tokens = []
            if isinstance(tokens, list) and len(tokens) >= 2:
                market['yes_token_id'] = tokens[0]
                market['no_token_id'] = tokens[1]

        return market

    def close(self):
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# Convenience function for quick market fetch
def fetch_all_markets(active_only: bool = True) -> List[Dict]:
    """Fetch and parse all active markets."""
    with GammaAPI() as api:
        raw_markets = api.get_all_markets(active_only=active_only)
        return [api.parse_market(m) for m in raw_markets]
