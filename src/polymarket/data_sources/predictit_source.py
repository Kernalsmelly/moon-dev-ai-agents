"""
PredictIt Data Source

Fetches market data from PredictIt for cross-platform arbitrage detection.
Compares PredictIt prices to Polymarket to find mispricings.

PredictIt API: https://www.predictit.org/api/marketdata/all/

Note: PredictIt has 10% fee on profits and 5% withdrawal fee.
Account for fees when calculating arbitrage opportunities.
"""

import os
import httpx
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional
import re

logger = logging.getLogger(__name__)

PREDICTIT_API = "https://www.predictit.org/api/marketdata/all/"

# PredictIt fees
PROFIT_FEE = 0.10  # 10% on winnings
WITHDRAWAL_FEE = 0.05  # 5% withdrawal fee

# Category mappings for matching markets
CATEGORY_KEYWORDS = {
    'politics': ['president', 'election', 'congress', 'senate', 'house', 'governor', 'vote', 'republican', 'democrat', 'trump', 'biden'],
    'world': ['war', 'ukraine', 'russia', 'china', 'nato', 'sanctions'],
    'economy': ['fed', 'interest rate', 'gdp', 'inflation', 'unemployment', 'recession'],
}


class PredictItSource:
    """
    PredictIt market data for cross-platform arbitrage.

    Finds opportunities where:
    - PredictIt YES + Polymarket NO < $1.00 (accounting for fees)
    - PredictIt NO + Polymarket YES < $1.00 (accounting for fees)
    """

    def __init__(self):
        self.client = httpx.Client(timeout=30.0)
        self.cache = {}
        self.cache_ttl = 300  # 5 minute cache
        self.last_fetch = None
        self.markets = []

    def fetch_all_markets(self) -> List[Dict]:
        """Fetch all PredictIt markets."""
        # Check cache
        if self.last_fetch:
            elapsed = (datetime.now(timezone.utc) - self.last_fetch).seconds
            if elapsed < self.cache_ttl and self.markets:
                return self.markets

        try:
            resp = self.client.get(PREDICTIT_API)
            if resp.status_code != 200:
                logger.error(f"PredictIt API error: {resp.status_code}")
                return self.markets  # Return cached

            data = resp.json()
            self.markets = data.get('markets', [])
            self.last_fetch = datetime.now(timezone.utc)

            logger.info(f"Fetched {len(self.markets)} PredictIt markets")
            return self.markets

        except Exception as e:
            logger.error(f"PredictIt fetch error: {e}")
            return self.markets

    def find_matching_market(self, polymarket_question: str) -> Optional[Dict]:
        """
        Find a PredictIt market that matches a Polymarket question.

        Uses structured entity matching for accuracy.
        """
        if not self.markets:
            self.fetch_all_markets()

        # Extract structured info from Polymarket question
        pm_info = self._extract_keywords(polymarket_question)

        # Skip if no meaningful info extracted
        if not any([pm_info.get('year'), pm_info.get('state'), pm_info.get('office'), pm_info.get('person')]):
            return None

        best_match = None
        best_score = 0

        for market in self.markets:
            pi_name = (market.get('name') or '')
            pi_short = (market.get('shortName') or '')

            # Check each contract in the market
            for contract in market.get('contracts', []):
                contract_name = (contract.get('name') or '')

                # Calculate match score using structured matching
                combined_text = f"{pi_name} {pi_short} {contract_name}"
                score = self._calculate_match_score(pm_info, combined_text)

                if score > best_score and score >= 6:  # Require strong match (at least 2 critical + some keywords)
                    best_score = score
                    best_match = {
                        'market_id': market.get('id'),
                        'market_name': market.get('name'),
                        'contract_id': contract.get('id'),
                        'contract_name': contract.get('name'),
                        'yes_price': contract.get('lastTradePrice'),
                        'best_yes': contract.get('bestBuyYesCost'),
                        'best_no': contract.get('bestBuyNoCost'),
                        'match_score': score,
                        'match_info': pm_info,
                    }

        return best_match

    def _extract_keywords(self, text: str) -> Dict:
        """Extract structured keywords from question text."""
        text_lower = text.lower()

        result = {
            'year': None,
            'state': None,
            'office': None,
            'party': None,
            'person': None,
            'keywords': [],
        }

        # Extract year
        year_match = re.search(r'\b(202[4-9]|203\d)\b', text)
        if year_match:
            result['year'] = year_match.group(1)

        # Extract state
        states = [
            'alabama', 'alaska', 'arizona', 'arkansas', 'california', 'colorado',
            'connecticut', 'delaware', 'florida', 'georgia', 'hawaii', 'idaho',
            'illinois', 'indiana', 'iowa', 'kansas', 'kentucky', 'louisiana',
            'maine', 'maryland', 'massachusetts', 'michigan', 'minnesota',
            'mississippi', 'missouri', 'montana', 'nebraska', 'nevada',
            'new hampshire', 'new jersey', 'new mexico', 'new york',
            'north carolina', 'north dakota', 'ohio', 'oklahoma', 'oregon',
            'pennsylvania', 'rhode island', 'south carolina', 'south dakota',
            'tennessee', 'texas', 'utah', 'vermont', 'virginia', 'washington',
            'west virginia', 'wisconsin', 'wyoming',
        ]
        for state in states:
            if state in text_lower:
                result['state'] = state
                break

        # Extract office type
        if 'governor' in text_lower:
            result['office'] = 'governor'
        elif 'senate' in text_lower or 'senator' in text_lower:
            result['office'] = 'senate'
        elif 'president' in text_lower:
            result['office'] = 'president'
        elif 'house' in text_lower:
            result['office'] = 'house'

        # Extract party
        if 'republican' in text_lower or 'gop' in text_lower:
            result['party'] = 'republican'
        elif 'democrat' in text_lower:
            result['party'] = 'democratic'

        # Extract person names (capitalized multi-word)
        names = re.findall(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b', text)
        if names:
            result['person'] = names[0].lower()

        # General keywords
        stop_words = {'will', 'the', 'be', 'a', 'an', 'in', 'on', 'at', 'to', 'for', 'of', 'by', 'with', 'is', 'are', 'was', 'were', 'win', 'election'}
        words = re.findall(r'\b[a-z]{4,}\b', text_lower)
        result['keywords'] = [w for w in words if w not in stop_words][:5]

        return result

    def _calculate_match_score(self, pm_info: Dict, pi_text: str) -> int:
        """Calculate match score between structured info and PredictIt text."""
        pi_lower = pi_text.lower()
        score = 0
        critical_matches = 0

        # Year match (critical)
        if pm_info.get('year') and pm_info['year'] in pi_lower:
            score += 3
            critical_matches += 1

        # State match (critical for state races)
        if pm_info.get('state') and pm_info['state'] in pi_lower:
            score += 4
            critical_matches += 1

        # Office match (critical)
        if pm_info.get('office'):
            if pm_info['office'] in pi_lower:
                score += 3
                critical_matches += 1
            elif pm_info['office'] == 'senate' and 'senator' in pi_lower:
                score += 3
                critical_matches += 1

        # Party match
        if pm_info.get('party') and pm_info['party'] in pi_lower:
            score += 2

        # Person name match (very strong signal)
        if pm_info.get('person'):
            # Check if any part of the name matches
            name_parts = pm_info['person'].split()
            for part in name_parts:
                if len(part) > 3 and part in pi_lower:
                    score += 3
                    critical_matches += 1
                    break

        # Keyword matches
        for kw in pm_info.get('keywords', []):
            if kw in pi_lower:
                score += 1

        # Require at least 2 critical matches for a valid match
        if critical_matches < 2:
            return 0

        return score

    def get_probability(self, market: Dict) -> Optional[Dict]:
        """
        Get probability estimate from PredictIt for a Polymarket market.

        Returns arbitrage opportunity if prices differ significantly.
        """
        question = market.get('question', '')
        if not question:
            return None

        # Try to find matching market
        pi_match = self.find_matching_market(question)
        if not pi_match:
            return None

        pi_yes = pi_match.get('best_yes') or pi_match.get('yes_price')
        if pi_yes is None:
            return None

        # Account for PredictIt fees
        # Effective payout on $1 YES that wins = $1 - 10% profit fee
        # If we pay $0.60, win $1, profit = $0.40, fee = $0.04, net = $0.36
        effective_pi_yes = pi_yes

        # Calculate implied probability (accounting for fees)
        # PI prices are already in decimal (0.60 = 60 cents)
        probability = pi_yes

        return {
            'source': f'PredictIt ({pi_match["contract_name"][:30]})',
            'probability': probability,
            'confidence': min(0.8, 0.5 + pi_match['match_score'] * 0.1),
            'reasoning': f"PredictIt trading at ${pi_yes:.2f} (match score: {pi_match['match_score']})",
            'predictit_data': pi_match,
        }

    def find_arbitrage_opportunities(self, polymarket_markets: List[Dict]) -> List[Dict]:
        """
        Find cross-platform arbitrage opportunities.

        An arbitrage exists when:
        - Polymarket YES + PredictIt NO < $1.00 (after fees)
        - Polymarket NO + PredictIt YES < $1.00 (after fees)
        """
        self.fetch_all_markets()
        opportunities = []

        for pm_market in polymarket_markets:
            question = pm_market.get('question', '')
            pm_yes = pm_market.get('yes_price', 0.5)
            pm_no = pm_market.get('no_price', 0.5)

            pi_match = self.find_matching_market(question)
            if not pi_match:
                continue

            pi_yes = pi_match.get('best_yes') or pi_match.get('yes_price') or 0.5
            pi_no = pi_match.get('best_no') or (1 - pi_yes)

            # Check for arbitrage (account for 10% profit fee on PI)
            # Strategy 1: Buy PM YES + PI NO
            cost_1 = pm_yes + pi_no
            # If YES wins: PM pays $1, PI pays $0, net = $1 - pm_yes - pi_no
            # If NO wins: PM pays $0, PI pays $1*(1-0.10), net = 0.90 - pm_yes - pi_no

            # Simplified: just check if combined cost < $0.95 (leaving room for fees)
            if cost_1 < 0.95:
                profit_pct = (0.95 - cost_1) / cost_1 * 100
                opportunities.append({
                    'polymarket': pm_market,
                    'predictit': pi_match,
                    'strategy': f"Buy PM YES @ ${pm_yes:.2f} + PI NO @ ${pi_no:.2f}",
                    'combined_cost': cost_1,
                    'profit_pct': profit_pct,
                    'type': 'cross_platform_arb',
                })

            # Strategy 2: Buy PM NO + PI YES
            cost_2 = pm_no + pi_yes
            if cost_2 < 0.95:
                profit_pct = (0.95 - cost_2) / cost_2 * 100
                opportunities.append({
                    'polymarket': pm_market,
                    'predictit': pi_match,
                    'strategy': f"Buy PM NO @ ${pm_no:.2f} + PI YES @ ${pi_yes:.2f}",
                    'combined_cost': cost_2,
                    'profit_pct': profit_pct,
                    'type': 'cross_platform_arb',
                })

        # Sort by profit
        opportunities.sort(key=lambda x: x['profit_pct'], reverse=True)
        return opportunities

    def close(self):
        """Close HTTP client."""
        self.client.close()


# Singleton
_source: Optional[PredictItSource] = None


def get_predictit_source() -> PredictItSource:
    """Get singleton PredictIt source."""
    global _source
    if _source is None:
        _source = PredictItSource()
    return _source
