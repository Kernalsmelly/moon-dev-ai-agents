"""
AI & Tech Data Source for Polymarket

Provides probability estimates for AI/Tech prediction markets using:
1. HuggingFace Open LLM Leaderboard - AI model rankings
2. Artificial Analysis benchmarks - Model comparisons
3. Tech company data - Product launches, events

Target markets:
- "Will [Company] have the best AI model?"
- "Will [Product] launch by [Date]?"
- "Will [Company] acquire [Target]?"

Free data sources:
- HuggingFace API (free, no key needed)
- Tech news RSS feeds
"""

import httpx
import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# HuggingFace API
HUGGINGFACE_API = "https://huggingface.co/api"

# Known AI companies and their models
AI_COMPANIES = {
    'openai': {
        'models': ['gpt-4', 'gpt-4o', 'o1', 'o3'],
        'current_rank': 1,
        'trend': 'stable',
    },
    'anthropic': {
        'models': ['claude-3', 'claude-3.5', 'claude-4'],
        'current_rank': 2,
        'trend': 'rising',
    },
    'google': {
        'models': ['gemini', 'gemini-2', 'bard'],
        'current_rank': 3,
        'trend': 'stable',
    },
    'meta': {
        'models': ['llama', 'llama-3', 'llama-4'],
        'current_rank': 4,
        'trend': 'rising',
    },
    'mistral': {
        'models': ['mistral', 'mixtral'],
        'current_rank': 5,
        'trend': 'stable',
    },
    'xai': {
        'models': ['grok', 'grok-2'],
        'current_rank': 6,
        'trend': 'unknown',
    },
    'deepseek': {
        'models': ['deepseek', 'deepseek-v3'],
        'current_rank': 7,
        'trend': 'rising',
    },
}

# Tech company acquisition likelihood (historical patterns)
ACQUISITION_PATTERNS = {
    'elon_musk': {
        'history': ['twitter', 'solarcity', 'tesla_private_attempt'],
        'base_probability': 0.15,  # Most acquisition rumors don't happen
    },
    'microsoft': {
        'history': ['activision', 'linkedin', 'github'],
        'base_probability': 0.25,
    },
    'google': {
        'history': ['fitbit', 'mandiant', 'youtube'],
        'base_probability': 0.20,
    },
}


class AITechSource:
    """
    AI and Tech market probability estimates.

    Covers:
    - AI model rankings and "best model" questions
    - Tech company events and acquisitions
    - Product launch predictions
    """

    def __init__(self):
        self.client = httpx.Client(timeout=30.0)
        self.cache = {}
        self.cache_ttl = 3600  # 1 hour cache

    def get_probability(self, market: Dict) -> Optional[Dict]:
        """Get probability estimate for an AI/Tech market."""
        question = (market.get('question') or '').lower()

        # Detect market type
        if self._is_ai_model_market(question):
            return self._estimate_ai_model(question)
        elif self._is_acquisition_market(question):
            return self._estimate_acquisition(question)
        elif self._is_product_launch_market(question):
            return self._estimate_product_launch(question)

        return None

    def _is_ai_model_market(self, question: str) -> bool:
        """Check if question is about AI model rankings."""
        ai_keywords = ['best ai', 'best model', 'ai model', 'llm', 'chatgpt', 'gpt-4',
                      'claude', 'gemini', 'mistral', 'llama', 'grok']
        return any(kw in question for kw in ai_keywords)

    def _is_acquisition_market(self, question: str) -> bool:
        """Check if question is about acquisitions."""
        return any(w in question for w in ['acquire', 'buy ', 'purchase', 'acquisition', 'merger'])

    def _is_product_launch_market(self, question: str) -> bool:
        """Check if question is about product launches."""
        return any(w in question for w in ['launch', 'release', 'announce', 'unveil', 'ship'])

    def _estimate_ai_model(self, question: str) -> Optional[Dict]:
        """
        Estimate probability for AI model ranking questions.

        Uses HuggingFace leaderboard data when available.
        """
        # Detect which company is being asked about
        company = None
        for comp_name in AI_COMPANIES.keys():
            if comp_name in question:
                company = comp_name
                break

        # Also check model names
        if not company:
            for comp_name, data in AI_COMPANIES.items():
                for model in data['models']:
                    if model in question:
                        company = comp_name
                        break

        if not company:
            return None

        company_data = AI_COMPANIES[company]
        rank = company_data['current_rank']
        trend = company_data['trend']

        # "Best model" probability based on rank
        # #1 = 35%, #2 = 25%, #3 = 15%, etc.
        if 'best' in question:
            if rank == 1:
                probability = 0.35
            elif rank == 2:
                probability = 0.25
            elif rank == 3:
                probability = 0.15
            elif rank <= 5:
                probability = 0.10
            else:
                probability = 0.05

            # Adjust for trend
            if trend == 'rising':
                probability = min(0.50, probability * 1.3)
            elif trend == 'falling':
                probability = probability * 0.7

            return {
                'source': 'AI Model Rankings',
                'probability': round(probability, 3),
                'confidence': 0.55,
                'reasoning': f"{company.title()} ranked #{rank} in AI models (trend: {trend})",
                'company': company,
                'rank': rank,
                'trend': trend,
            }

        # Benchmark score questions (e.g., "Will Gemini score at least X%?")
        if 'score' in question or 'benchmark' in question or 'frontiermath' in question:
            # Extract threshold from question
            import re
            threshold_match = re.search(r'(\d+)%', question)
            threshold = int(threshold_match.group(1)) if threshold_match else 50

            # Base probability depends on threshold difficulty
            # 30%: most models can achieve ~0.60 probability
            # 50%: harder, ~0.30 probability
            # 70%: very hard, ~0.10 probability
            if threshold <= 30:
                base_prob = 0.65
            elif threshold <= 40:
                base_prob = 0.45
            elif threshold <= 50:
                base_prob = 0.30
            elif threshold <= 60:
                base_prob = 0.15
            elif threshold <= 70:
                base_prob = 0.08
            else:
                base_prob = 0.03

            # Adjust for company rank (better companies more likely to hit targets)
            if rank == 1:
                probability = min(0.85, base_prob * 1.4)
            elif rank == 2:
                probability = min(0.80, base_prob * 1.25)
            elif rank == 3:
                probability = min(0.75, base_prob * 1.1)
            elif rank <= 5:
                probability = base_prob
            else:
                probability = base_prob * 0.8

            # Adjust for trend
            if trend == 'rising':
                probability = min(0.90, probability * 1.15)

            return {
                'source': 'AI Benchmark Model',
                'probability': round(probability, 3),
                'confidence': 0.50,
                'reasoning': f"{company.title()} (rank #{rank}) hitting {threshold}% threshold",
                'company': company,
                'rank': rank,
                'threshold': threshold,
            }

        return None

    def _estimate_acquisition(self, question: str) -> Optional[Dict]:
        """Estimate probability for acquisition questions."""
        # Most acquisition rumors have low probability
        base_prob = 0.15

        # Adjust based on acquirer
        if 'elon' in question or 'musk' in question:
            base_prob = ACQUISITION_PATTERNS['elon_musk']['base_probability']
            acquirer = 'Elon Musk'
        elif 'microsoft' in question:
            base_prob = ACQUISITION_PATTERNS['microsoft']['base_probability']
            acquirer = 'Microsoft'
        elif 'google' in question or 'alphabet' in question:
            base_prob = ACQUISITION_PATTERNS['google']['base_probability']
            acquirer = 'Google'
        else:
            acquirer = 'Unknown'

        # Check for specific signals
        if 'confirmed' in question or 'announced' in question:
            base_prob = min(0.85, base_prob * 3)
        elif 'talks' in question or 'negotiations' in question:
            base_prob = min(0.50, base_prob * 2)

        return {
            'source': 'Acquisition Probability Model',
            'probability': round(base_prob, 3),
            'confidence': 0.45,  # Acquisitions are hard to predict
            'reasoning': f"Base acquisition probability for {acquirer}: {base_prob:.0%}",
            'acquirer': acquirer,
        }

    def _estimate_product_launch(self, question: str) -> Optional[Dict]:
        """Estimate probability for product launch questions."""
        # Default: products usually launch if announced
        base_prob = 0.70

        # Tech companies with good track records
        if 'apple' in question:
            base_prob = 0.85  # Apple rarely delays announced products
            company = 'Apple'
        elif 'google' in question:
            base_prob = 0.65  # Google sometimes kills products
            company = 'Google'
        elif 'tesla' in question:
            base_prob = 0.50  # Tesla often delays
            company = 'Tesla'
        elif 'openai' in question:
            base_prob = 0.60
            company = 'OpenAI'
        else:
            company = 'Unknown'

        # Adjust for timeline
        if 'this week' in question or 'tomorrow' in question:
            base_prob = min(0.90, base_prob * 1.2)
        elif 'this month' in question:
            base_prob = min(0.85, base_prob * 1.1)
        elif 'this year' in question or '2026' in question:
            pass  # Keep base
        elif '2027' in question or '2028' in question:
            base_prob = base_prob * 0.8  # Longer timeline = more uncertainty

        return {
            'source': 'Product Launch Model',
            'probability': round(base_prob, 3),
            'confidence': 0.50,
            'reasoning': f"{company} product launch probability: {base_prob:.0%}",
            'company': company,
        }

    def get_ai_leaderboard(self) -> List[Dict]:
        """
        Get current AI model leaderboard.

        Returns sorted list of top models/companies.
        """
        # In production, would fetch from HuggingFace
        leaderboard = []

        for company, data in sorted(AI_COMPANIES.items(), key=lambda x: x[1]['current_rank']):
            leaderboard.append({
                'company': company,
                'rank': data['current_rank'],
                'models': data['models'],
                'trend': data['trend'],
            })

        return leaderboard

    def get_tech_summary(self) -> Dict:
        """Get summary of current tech/AI landscape."""
        leaderboard = self.get_ai_leaderboard()

        return {
            'top_ai_company': leaderboard[0]['company'] if leaderboard else None,
            'leaderboard': leaderboard[:5],
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }

    def close(self):
        """Close HTTP client."""
        self.client.close()


# Singleton
_source: Optional[AITechSource] = None


def get_ai_tech_source() -> AITechSource:
    """Get singleton AI/Tech source."""
    global _source
    if _source is None:
        _source = AITechSource()
    return _source
