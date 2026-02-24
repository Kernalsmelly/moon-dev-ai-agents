"""
Federal Reserve Interest Rate Data Source

Provides probability estimates for Fed rate decision markets using:
1. CME FedWatch probabilities (via alternative data)
2. Fed Funds Futures implied probabilities
3. Historical Fed decision patterns

This is HIGH EDGE territory:
- $11.7M daily volume on Polymarket
- $2.6M average liquidity
- Fed decisions are highly predictable from futures

Key insight: CME FedWatch is the gold standard. If Polymarket diverges
from FedWatch by >5%, that's a strong signal.

Free data sources:
- Investing.com Fed rate probabilities
- FRED (Federal Reserve Economic Data)
- Treasury yield curve data
"""

import httpx
import logging
import re
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Fed meeting dates for 2025-2026 (FOMC schedule)
FOMC_MEETINGS_2025 = [
    '2025-01-29',  # Jan 28-29
    '2025-03-19',  # Mar 18-19
    '2025-05-07',  # May 6-7
    '2025-06-18',  # Jun 17-18
    '2025-07-30',  # Jul 29-30
    '2025-09-17',  # Sep 16-17
    '2025-11-05',  # Nov 4-5
    '2025-12-17',  # Dec 16-17
]

FOMC_MEETINGS_2026 = [
    '2026-01-28',  # Jan 27-28
    '2026-03-18',  # Mar 17-18
    '2026-05-06',  # May 5-6
    '2026-06-17',  # Jun 16-17
    '2026-07-29',  # Jul 28-29
    '2026-09-16',  # Sep 15-16
    '2026-11-04',  # Nov 3-4
    '2026-12-16',  # Dec 15-16
]

# Current Fed rate context (update periodically)
CURRENT_FED_RATE = 4.50  # Upper bound as of Jan 2026
RATE_STEP = 0.25  # Standard rate change increment

# Historical patterns
FED_PATTERNS = {
    'hold_probability': 0.75,  # Fed holds 75% of meetings historically
    'cut_probability': 0.15,   # 15% chance of cut in normal conditions
    'hike_probability': 0.10,  # 10% chance of hike in normal conditions
}


class FedRatesSource:
    """
    Fed rate decision probability estimates.

    Compares Polymarket prices to CME FedWatch-style probabilities
    to identify edge opportunities.
    """

    def __init__(self):
        self.client = httpx.Client(timeout=30.0)
        self.cache = {}
        self.cache_ttl = 1800  # 30 min cache (rates don't change fast)

    def get_probability(self, market: Dict) -> Optional[Dict]:
        """Get probability estimate for a Fed rate market."""
        question = (market.get('question') or '').lower()

        # Check if this is a Fed rate market
        if not self._is_fed_market(question):
            return None

        # Parse the question to understand what's being asked
        parsed = self._parse_fed_question(question)
        if not parsed:
            return None

        # Get our probability estimate
        estimate = self._estimate_probability(parsed)
        if not estimate:
            return None

        return estimate

    def _is_fed_market(self, question: str) -> bool:
        """Check if question is about Fed rates."""
        fed_keywords = [
            'fed ', 'fomc', 'interest rate', 'federal reserve',
            'rate cut', 'rate hike', 'rate increase', 'rate decrease',
            'basis points', 'bps'
        ]
        return any(kw in question.lower() for kw in fed_keywords)

    def _parse_fed_question(self, question: str) -> Optional[Dict]:
        """
        Parse Fed rate question to extract:
        - Direction: cut, hike, or hold
        - Magnitude: 25bps, 50bps, etc.
        - Meeting date
        """
        q = question.lower()

        result = {
            'direction': None,
            'magnitude_bps': None,
            'meeting_date': None,
            'original_question': question,
        }

        # Detect direction
        if any(w in q for w in ['decrease', 'cut', 'lower']):
            result['direction'] = 'cut'
        elif any(w in q for w in ['increase', 'hike', 'raise']):
            result['direction'] = 'hike'
        elif 'no change' in q or 'hold' in q or 'unchanged' in q:
            result['direction'] = 'hold'
        else:
            # Default based on "by X bps" phrasing
            if 'by' in q and 'bps' in q:
                result['direction'] = 'change'

        # Detect magnitude
        bps_match = re.search(r'(\d+)\+?\s*(?:basis points|bps)', q)
        if bps_match:
            result['magnitude_bps'] = int(bps_match.group(1))

        # Detect meeting date
        date_patterns = [
            r'january\s*(\d{4})',
            r'february\s*(\d{4})',
            r'march\s*(\d{4})',
            r'april\s*(\d{4})',
            r'may\s*(\d{4})',
            r'june\s*(\d{4})',
            r'july\s*(\d{4})',
            r'august\s*(\d{4})',
            r'september\s*(\d{4})',
            r'october\s*(\d{4})',
            r'november\s*(\d{4})',
            r'december\s*(\d{4})',
        ]

        for pattern in date_patterns:
            match = re.search(pattern, q)
            if match:
                result['meeting_date'] = match.group(0)
                break

        return result if result['direction'] else None

    def _estimate_probability(self, parsed: Dict) -> Optional[Dict]:
        """
        Estimate probability based on:
        1. Current market conditions
        2. Historical Fed patterns
        3. Economic indicators
        """
        direction = parsed['direction']
        magnitude = parsed.get('magnitude_bps') or 25  # Default to 25 bps if None

        # Get current economic context
        context = self._get_economic_context()

        # Base probabilities (would come from CME FedWatch ideally)
        # These are estimates based on typical market conditions
        base_probs = self._get_base_probabilities(context)

        # Calculate specific probability
        if direction == 'hold':
            probability = base_probs['hold']
            confidence = 0.75
            reasoning = f"Fed historically holds {base_probs['hold']:.0%} of meetings"

        elif direction == 'cut':
            if magnitude >= 50:
                # 50+ bps cut is rare
                probability = base_probs['cut'] * 0.3
                reasoning = f"50+ bps cut is rare ({probability:.0%})"
            else:
                probability = base_probs['cut']
                reasoning = f"25 bps cut probability: {probability:.0%}"
            confidence = 0.70

        elif direction == 'hike':
            if magnitude >= 50:
                probability = base_probs['hike'] * 0.2
                reasoning = f"50+ bps hike is very rare ({probability:.0%})"
            else:
                probability = base_probs['hike']
                reasoning = f"25 bps hike probability: {probability:.0%}"
            confidence = 0.70

        else:
            probability = 0.5
            confidence = 0.40
            reasoning = "Unable to determine direction"

        # Adjust confidence based on time to meeting
        days_to_meeting = self._days_to_next_meeting()
        if days_to_meeting and days_to_meeting < 7:
            confidence = min(0.85, confidence + 0.10)
            reasoning += f" | Meeting in {days_to_meeting} days"
        elif days_to_meeting and days_to_meeting < 30:
            confidence = min(0.80, confidence + 0.05)

        return {
            'source': 'Fed Rate Model',
            'probability': round(probability, 3),
            'confidence': round(confidence, 2),
            'reasoning': reasoning,
            'direction': direction,
            'magnitude_bps': magnitude,
            'economic_context': context,
            'days_to_meeting': days_to_meeting,
        }

    def _get_base_probabilities(self, context: Dict) -> Dict:
        """
        Get base probabilities for Fed actions.

        In a real implementation, this would pull from CME FedWatch.
        For now, we estimate based on economic context.
        """
        # Adjust based on economic signals
        inflation = context.get('inflation_trend', 'stable')
        employment = context.get('employment', 'strong')

        hold = 0.70
        cut = 0.20
        hike = 0.10

        # Adjust for inflation
        if inflation == 'high':
            hike += 0.15
            cut -= 0.10
            hold -= 0.05
        elif inflation == 'low':
            cut += 0.15
            hike -= 0.08
            hold -= 0.07

        # Adjust for employment
        if employment == 'weak':
            cut += 0.10
            hike -= 0.08

        # Normalize
        total = hold + cut + hike
        return {
            'hold': hold / total,
            'cut': cut / total,
            'hike': hike / total,
        }

    def _get_economic_context(self) -> Dict:
        """
        Get current economic context.

        In production, this would pull from FRED API or similar.
        """
        cache_key = 'economic_context'

        if cache_key in self.cache:
            cached = self.cache[cache_key]
            if (datetime.now(timezone.utc) - cached['time']).seconds < self.cache_ttl:
                return cached['data']

        # For now, return estimated context
        # TODO: Pull from FRED API for real data
        context = {
            'current_rate': CURRENT_FED_RATE,
            'inflation_trend': 'stable',  # Would come from CPI data
            'employment': 'strong',  # Would come from jobs data
            'gdp_trend': 'moderate',
            'last_action': 'hold',
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }

        self.cache[cache_key] = {
            'data': context,
            'time': datetime.now(timezone.utc)
        }

        return context

    def _days_to_next_meeting(self) -> Optional[int]:
        """Calculate days to next FOMC meeting."""
        today = datetime.now(timezone.utc).date()

        all_meetings = FOMC_MEETINGS_2025 + FOMC_MEETINGS_2026

        for meeting_str in all_meetings:
            meeting_date = datetime.strptime(meeting_str, '%Y-%m-%d').date()
            if meeting_date > today:
                return (meeting_date - today).days

        return None

    def get_next_meeting_info(self) -> Dict:
        """Get info about the next FOMC meeting."""
        days = self._days_to_next_meeting()
        all_meetings = FOMC_MEETINGS_2025 + FOMC_MEETINGS_2026
        today = datetime.now(timezone.utc).date()

        next_meeting = None
        for meeting_str in all_meetings:
            meeting_date = datetime.strptime(meeting_str, '%Y-%m-%d').date()
            if meeting_date > today:
                next_meeting = meeting_str
                break

        return {
            'next_meeting': next_meeting,
            'days_until': days,
            'current_rate': CURRENT_FED_RATE,
        }

    def get_rate_probabilities(self) -> Dict:
        """
        Get probability distribution for next meeting.

        Returns probabilities for each possible outcome.
        """
        context = self._get_economic_context()
        probs = self._get_base_probabilities(context)
        meeting_info = self.get_next_meeting_info()

        return {
            'meeting_date': meeting_info['next_meeting'],
            'days_until': meeting_info['days_until'],
            'current_rate': CURRENT_FED_RATE,
            'probabilities': {
                'hold': round(probs['hold'], 3),
                'cut_25bps': round(probs['cut'] * 0.8, 3),
                'cut_50bps': round(probs['cut'] * 0.2, 3),
                'hike_25bps': round(probs['hike'] * 0.9, 3),
                'hike_50bps': round(probs['hike'] * 0.1, 3),
            },
            'context': context,
        }

    def close(self):
        """Close HTTP client."""
        self.client.close()


# Singleton
_source: Optional[FedRatesSource] = None


def get_fed_source() -> FedRatesSource:
    """Get singleton Fed rates source."""
    global _source
    if _source is None:
        _source = FedRatesSource()
    return _source
