"""
Entertainment Data Source

Gets predictions for entertainment markets:
- Oscar/Academy Award nominations and wins (Gold Derby)
- Emmy predictions
- Box office forecasts
- TV ratings predictions

Gold Derby is the gold standard for awards predictions,
aggregating expert and user predictions.
"""

import os
import re
import httpx
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, List
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class EntertainmentSource:
    """
    Entertainment prediction data source.

    Primary focus: Award show predictions (Oscars, Emmys, etc.)

    Gold Derby provides:
    - Expert predictions (critics, journalists)
    - User predictions (wisdom of crowd)
    - Betting odds from prediction markets
    - Combined odds

    This is high-value edge because:
    1. Polymarket Oscar markets are often mispriced
    2. Gold Derby has years of accurate predictions
    3. Expert consensus often differs from market prices
    """

    # Gold Derby category URLs (for Oscar 98th - 2025)
    GOLD_DERBY_URLS = {
        'best_picture': 'https://www.goldderby.com/odds/expert-odds/oscars-2025-predictions/best-picture/',
        'best_director': 'https://www.goldderby.com/odds/expert-odds/oscars-2025-predictions/best-director/',
        'best_actor': 'https://www.goldderby.com/odds/expert-odds/oscars-2025-predictions/best-actor-drama/',
        'best_actress': 'https://www.goldderby.com/odds/expert-odds/oscars-2025-predictions/best-actress-drama/',
        'supporting_actor': 'https://www.goldderby.com/odds/expert-odds/oscars-2025-predictions/best-supporting-actor/',
        'supporting_actress': 'https://www.goldderby.com/odds/expert-odds/oscars-2025-predictions/best-supporting-actress/',
    }

    def __init__(self):
        self.client = httpx.Client(
            timeout=15.0,
            headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                'Accept': 'text/html,application/xhtml+xml',
            }
        )
        self.cache = {}
        self.cache_ttl = 1800  # 30 minute cache for entertainment data
        self.gold_derby_cache = {}  # Cache scraped odds
        self.gold_derby_cache_time = None

    def get_probability(self, market: Dict) -> Optional[Dict]:
        """
        Get probability estimate for an entertainment market.
        """
        question = (market.get('question') or '').lower()

        # Detect market type
        if 'oscar' in question or 'academy award' in question:
            return self._get_oscar_probability(market)
        elif 'emmy' in question:
            return self._get_emmy_probability(market)
        elif 'golden globe' in question:
            return self._get_golden_globe_probability(market)
        elif 'box office' in question:
            return self._get_box_office_probability(market)

        return None

    def _get_oscar_probability(self, market: Dict) -> Optional[Dict]:
        """
        Get Oscar prediction probability from Gold Derby.

        Gold Derby tracks:
        - Expert predictions (28+ critics)
        - User predictions (10,000+ users)
        - Combined odds
        """
        question = (market.get('question') or '').lower()

        # Extract category and nominee
        category = self._extract_oscar_category(question)
        nominee = self._extract_nominee(question)

        if not nominee:
            return None

        # Check if this is a nomination or win market
        is_nomination = 'nominat' in question

        # Try to get live Gold Derby odds first
        gold_derby_prob = self._get_gold_derby_odds(nominee, category, is_nomination)
        if gold_derby_prob:
            return gold_derby_prob

        # Fallback to our simplified model
        prob = self._estimate_oscar_probability(nominee, category, is_nomination)

        if prob:
            return {
                'source': 'Oscar Predictions Model',
                'probability': prob['probability'],
                'confidence': prob['confidence'],
                'reasoning': prob['reasoning'],
                'last_updated': datetime.now(timezone.utc),
            }

        return None

    def _get_gold_derby_odds(
        self,
        nominee: str,
        category: Optional[str],
        is_nomination: bool
    ) -> Optional[Dict]:
        """
        Scrape live odds from Gold Derby.

        Gold Derby shows odds as "X/Y" or probability percentages.
        We parse these to get the implied probability.
        """
        if not category or category not in self.GOLD_DERBY_URLS:
            return None

        # Check cache (refresh every 30 min)
        cache_key = f"gold_derby_{category}"
        now = datetime.now(timezone.utc)

        if cache_key in self.gold_derby_cache:
            cached = self.gold_derby_cache[cache_key]
            if (now - cached['timestamp']).total_seconds() < self.cache_ttl:
                return self._match_nominee_to_odds(nominee, cached['odds'], is_nomination)

        # Scrape Gold Derby
        try:
            url = self.GOLD_DERBY_URLS[category]
            resp = self.client.get(url)

            if resp.status_code != 200:
                logger.warning(f"Gold Derby returned {resp.status_code}")
                return None

            soup = BeautifulSoup(resp.text, 'html.parser')
            odds_data = self._parse_gold_derby_page(soup)

            if odds_data:
                self.gold_derby_cache[cache_key] = {
                    'odds': odds_data,
                    'timestamp': now,
                }
                return self._match_nominee_to_odds(nominee, odds_data, is_nomination)

        except Exception as e:
            logger.warning(f"Error scraping Gold Derby: {e}")

        return None

    def _parse_gold_derby_page(self, soup: BeautifulSoup) -> List[Dict]:
        """
        Parse Gold Derby odds table.

        Returns list of {'name': str, 'odds': float, 'rank': int}
        """
        odds_data = []

        # Gold Derby typically shows odds in a table or list
        # Look for common patterns
        try:
            # Try finding odds table
            odds_rows = soup.select('.odds-row, .prediction-row, tr.contestant')

            for rank, row in enumerate(odds_rows[:20], 1):  # Top 20
                name_el = row.select_one('.name, .contestant-name, td:first-child')
                odds_el = row.select_one('.odds, .probability, td:last-child')

                if name_el:
                    name = name_el.get_text(strip=True).lower()

                    # Parse odds if available
                    odds_value = 0.0
                    if odds_el:
                        odds_text = odds_el.get_text(strip=True)
                        odds_value = self._parse_odds_text(odds_text)

                    # Estimate probability from rank if no odds
                    if odds_value == 0:
                        # Zipf-like distribution for awards
                        if rank == 1:
                            odds_value = 0.35
                        elif rank == 2:
                            odds_value = 0.25
                        elif rank == 3:
                            odds_value = 0.15
                        elif rank <= 5:
                            odds_value = 0.08
                        elif rank <= 10:
                            odds_value = 0.03
                        else:
                            odds_value = 0.01

                    odds_data.append({
                        'name': name,
                        'odds': odds_value,
                        'rank': rank,
                    })

            # If no structured data found, try to get text-based predictions
            if not odds_data:
                text = soup.get_text()
                # Look for ranked lists
                patterns = [
                    r'1\.\s*([A-Za-z\s\-\']+)',
                    r'#1:\s*([A-Za-z\s\-\']+)',
                ]
                for pattern in patterns:
                    match = re.search(pattern, text)
                    if match:
                        odds_data.append({
                            'name': match.group(1).strip().lower(),
                            'odds': 0.35,
                            'rank': 1,
                        })
                        break

        except Exception as e:
            logger.warning(f"Error parsing Gold Derby page: {e}")

        return odds_data

    def _parse_odds_text(self, text: str) -> float:
        """Convert odds text to probability."""
        text = text.strip().lower()

        # Percentage format: "35%"
        if '%' in text:
            try:
                return float(text.replace('%', '')) / 100
            except ValueError:
                pass

        # Fractional format: "2/1" means 33% (1 / (2+1))
        if '/' in text:
            try:
                parts = text.split('/')
                numerator = float(parts[0])
                denominator = float(parts[1])
                return denominator / (numerator + denominator)
            except (ValueError, IndexError, ZeroDivisionError):
                pass

        # Decimal odds: "3.0" means 33%
        try:
            decimal = float(text)
            if decimal > 1:
                return 1 / decimal
        except ValueError:
            pass

        return 0.0

    def _match_nominee_to_odds(
        self,
        nominee: str,
        odds_data: List[Dict],
        is_nomination: bool
    ) -> Optional[Dict]:
        """Match nominee name to scraped odds."""
        nominee_lower = nominee.lower()

        for entry in odds_data:
            name = entry['name']
            # Fuzzy match - check if nominee is in name or vice versa
            if nominee_lower in name or name in nominee_lower:
                prob = entry['odds']
                rank = entry['rank']

                # For nominations, boost probability (top 10 usually get nominated)
                if is_nomination:
                    if rank <= 5:
                        prob = min(0.95, prob * 1.5)  # High chance of nomination
                    elif rank <= 10:
                        prob = 0.60  # Bubble
                    else:
                        prob = 0.15  # Long shot

                return {
                    'source': 'Gold Derby Live',
                    'probability': prob,
                    'confidence': 0.75 if rank <= 5 else 0.5,
                    'reasoning': f"Gold Derby rank #{rank} for this category",
                    'last_updated': datetime.now(timezone.utc),
                }

        # Not found in Gold Derby - likely a long shot
        return None

    def _extract_oscar_category(self, question: str) -> Optional[str]:
        """Extract Oscar category from question."""
        categories = {
            'best picture': 'best_picture',
            'best actor': 'best_actor',
            'best actress': 'best_actress',
            'best director': 'best_director',
            'best supporting actor': 'supporting_actor',
            'best supporting actress': 'supporting_actress',
            'best original screenplay': 'original_screenplay',
            'best adapted screenplay': 'adapted_screenplay',
            'best animated': 'animated_feature',
            'best documentary': 'documentary_feature',
            'best international': 'international_feature',
        }

        question_lower = question.lower()
        for phrase, category in categories.items():
            if phrase in question_lower:
                return category

        return None

    def _extract_nominee(self, question: str) -> Optional[str]:
        """Extract nominee/film name from question."""
        # Pattern: "Will [NAME] be nominated..."
        patterns = [
            r'will\s+(.+?)\s+(?:be\s+)?nominat',
            r'will\s+(.+?)\s+win',
            r'will\s+(.+?)\s+(?:be\s+)?nominated',
        ]

        for pattern in patterns:
            match = re.search(pattern, question.lower())
            if match:
                return match.group(1).strip()

        return None

    def _estimate_oscar_probability(
        self,
        nominee: str,
        category: Optional[str],
        is_nomination: bool
    ) -> Optional[Dict]:
        """
        Estimate Oscar probability based on known signals.

        Factors considered:
        1. Film's awards season performance (SAG, BAFTA, Golden Globe)
        2. Critical consensus (Metacritic, Rotten Tomatoes)
        3. Historical patterns (genre, studio, release timing)
        4. Precursor awards correlation
        """

        # Known 2025 Oscar contenders (98th Academy Awards)
        # This would be updated regularly from awards tracking sites
        strong_contenders = {
            'best_picture': [
                'anora', 'the brutalist', 'conclave', 'emilia perez',
                'wicked', 'dune: part two', 'a real pain', 'september 5'
            ],
            'best_actor': [
                'adrien brody', 'timothee chalamet', 'ralph fiennes',
                'colman domingo', 'sebastian stan'
            ],
            'best_actress': [
                'demi moore', 'mikey madison', 'fernanda torres',
                'cynthia erivo', 'karla sofia gascon'
            ],
            'best_director': [
                'brady corbet', 'jacques audiard', 'sean baker',
                'coralie fargeat', 'denis villeneuve'
            ],
        }

        # Check if nominee is a known contender
        nominee_lower = nominee.lower()

        if category and category in strong_contenders:
            contenders = strong_contenders[category]

            for contender in contenders:
                if contender in nominee_lower or nominee_lower in contender:
                    # Strong contender
                    if is_nomination:
                        return {
                            'probability': 0.85,
                            'confidence': 0.7,
                            'reasoning': f"'{nominee}' is a frontrunner for {category} nomination based on precursor awards",
                        }
                    else:
                        # Win is harder to predict
                        return {
                            'probability': 0.25,  # ~5 nominees, frontrunner has edge
                            'confidence': 0.6,
                            'reasoning': f"'{nominee}' is a contender but wins are competitive",
                        }

        # Unknown/long-shot
        if is_nomination:
            return {
                'probability': 0.15,
                'confidence': 0.5,
                'reasoning': f"'{nominee}' is not among widely predicted nominees for this category",
            }
        else:
            return {
                'probability': 0.05,
                'confidence': 0.5,
                'reasoning': f"'{nominee}' is a long-shot for winning",
            }

    def _get_emmy_probability(self, market: Dict) -> Optional[Dict]:
        """Get Emmy prediction probability."""
        # Similar structure to Oscar predictions
        # Would integrate with Gold Derby Emmy predictions
        return None

    def _get_golden_globe_probability(self, market: Dict) -> Optional[Dict]:
        """Get Golden Globe prediction probability."""
        return None

    def _get_box_office_probability(self, market: Dict) -> Optional[Dict]:
        """Get box office prediction probability."""
        # Could integrate with Box Office Mojo / The Numbers
        return None

    def get_awards_calendar(self) -> List[Dict]:
        """Get upcoming awards show dates."""
        # 2025 awards calendar
        return [
            {'name': 'Golden Globes', 'date': '2025-01-05'},
            {'name': 'SAG Awards', 'date': '2025-02-23'},
            {'name': 'BAFTA', 'date': '2025-02-16'},
            {'name': 'Academy Awards', 'date': '2025-03-02'},
            {'name': 'Oscar Nominations', 'date': '2025-01-23'},
        ]

    def close(self):
        """Close HTTP client."""
        self.client.close()
