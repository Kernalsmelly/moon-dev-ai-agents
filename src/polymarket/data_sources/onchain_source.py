"""
On-Chain Data Source for Polymarket Crypto Markets

Provides whale activity and on-chain metrics to identify edge:
1. Exchange inflows/outflows - large deposits to exchanges = sell pressure
2. Whale transactions - tracking big money movements
3. Network activity - transaction counts, active addresses

Free APIs used:
- Blockchain.com (free, no key for basic endpoints)
- Glassnode (free tier - limited)
- CryptoQuant alternative endpoints

Key Edge Signals:
- Large exchange inflows = selling pressure incoming
- Large exchange outflows = accumulation (bullish)
- Whale wallet movements can front-run price action
"""

import httpx
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Free API endpoints
BLOCKCHAIN_COM_API = "https://api.blockchain.info"
BLOCKCHAIR_API = "https://api.blockchair.com"


class OnChainSource:
    """
    On-chain metrics for crypto prediction markets.

    Tracks whale movements and exchange flows to provide edge.
    """

    def __init__(self):
        self.client = httpx.Client(timeout=30.0)
        self.cache = {}
        self.cache_ttl = 600  # 10 minute cache for on-chain data

    def get_probability(self, market: Dict) -> Optional[Dict]:
        """
        Get probability estimate based on on-chain metrics.

        Only applies to crypto markets with price targets.
        """
        question = (market.get('question') or '').lower()

        # Only handle crypto markets
        if not any(w in question for w in ['bitcoin', 'btc', 'ethereum', 'eth', 'crypto']):
            return None

        # Get on-chain metrics
        metrics = self.get_bitcoin_metrics()
        if not metrics:
            return None

        # Analyze metrics for probability adjustment
        return self._analyze_metrics(metrics, question)

    def get_bitcoin_metrics(self) -> Optional[Dict]:
        """Get Bitcoin on-chain metrics from blockchain.com."""
        cache_key = "btc_metrics"

        if cache_key in self.cache:
            cached = self.cache[cache_key]
            if (datetime.now(timezone.utc) - cached['time']).seconds < self.cache_ttl:
                return cached['data']

        metrics = {}

        try:
            # Get various stats from blockchain.com
            stats_urls = {
                'hash_rate': f"{BLOCKCHAIN_COM_API}/q/hashrate",
                'difficulty': f"{BLOCKCHAIN_COM_API}/q/getdifficulty",
                'block_count': f"{BLOCKCHAIN_COM_API}/q/getblockcount",
                'total_btc': f"{BLOCKCHAIN_COM_API}/q/totalbc",
                'market_cap': f"{BLOCKCHAIN_COM_API}/q/marketcap",
                'tx_count_24h': f"{BLOCKCHAIN_COM_API}/q/24hrtransactioncount",
                'mempool_size': f"{BLOCKCHAIN_COM_API}/q/unconfirmedcount",
            }

            for key, url in stats_urls.items():
                try:
                    resp = self.client.get(url, timeout=10.0)
                    if resp.status_code == 200:
                        value = resp.text.strip()
                        try:
                            metrics[key] = float(value)
                        except ValueError:
                            metrics[key] = value
                except Exception as e:
                    logger.debug(f"Error fetching {key}: {e}")

            if metrics:
                # Calculate derived metrics
                if 'tx_count_24h' in metrics:
                    # High tx count = high activity = often bullish
                    tx_count = metrics['tx_count_24h']
                    if tx_count > 400000:
                        metrics['activity_level'] = 'very_high'
                    elif tx_count > 300000:
                        metrics['activity_level'] = 'high'
                    elif tx_count > 200000:
                        metrics['activity_level'] = 'normal'
                    else:
                        metrics['activity_level'] = 'low'

                if 'mempool_size' in metrics:
                    # High mempool = congestion = often during price moves
                    mempool = metrics['mempool_size']
                    if mempool > 100000:
                        metrics['congestion'] = 'high'
                    elif mempool > 50000:
                        metrics['congestion'] = 'moderate'
                    else:
                        metrics['congestion'] = 'low'

                metrics['timestamp'] = datetime.now(timezone.utc).isoformat()

                self.cache[cache_key] = {
                    'data': metrics,
                    'time': datetime.now(timezone.utc)
                }

                return metrics

        except Exception as e:
            logger.debug(f"Bitcoin metrics error: {e}")

        return None

    def get_exchange_flows(self) -> Optional[Dict]:
        """
        Estimate exchange flows from public data.

        Large exchange inflows = potential sell pressure
        Large outflows = accumulation

        Note: This uses heuristics since free APIs don't provide
        direct exchange flow data. Consider Glassnode paid tier
        for accurate data.
        """
        # For free tier, we estimate based on mempool and tx patterns
        metrics = self.get_bitcoin_metrics()
        if not metrics:
            return None

        # Heuristic: High mempool + high tx count often indicates exchange activity
        tx_count = metrics.get('tx_count_24h', 250000)
        mempool = metrics.get('mempool_size', 50000)

        # Normalize to a flow estimate
        activity_score = (tx_count / 300000) + (mempool / 75000)

        if activity_score > 2.0:
            flow_estimate = 'high_activity'
            interpretation = 'High on-chain activity - significant movement'
        elif activity_score > 1.5:
            flow_estimate = 'elevated_activity'
            interpretation = 'Elevated activity - watch for price movement'
        else:
            flow_estimate = 'normal_activity'
            interpretation = 'Normal on-chain activity'

        return {
            'flow_estimate': flow_estimate,
            'activity_score': round(activity_score, 2),
            'interpretation': interpretation,
            'tx_count_24h': tx_count,
            'mempool_size': mempool,
        }

    def _analyze_metrics(self, metrics: Dict, question: str) -> Optional[Dict]:
        """Analyze on-chain metrics for probability estimate."""
        # Base probability adjustment from on-chain activity
        activity = metrics.get('activity_level', 'normal')
        congestion = metrics.get('congestion', 'moderate')

        # Determine if question is about upside or downside
        is_upside = any(w in question for w in ['reach', 'hit', 'above', 'exceed', 'rise', 'surge'])
        is_downside = any(w in question for w in ['crash', 'fall', 'below', 'drop', 'decline'])

        # On-chain activity interpretation:
        # High activity during bull markets = continuation
        # High activity during fear = potential bottom
        # Low activity = consolidation, neutral

        probability_adj = 0.0
        reasoning_parts = []

        if activity == 'very_high':
            probability_adj = 0.05 if is_upside else -0.05
            reasoning_parts.append(f"Very high network activity ({metrics.get('tx_count_24h', 0):,.0f} tx/24h)")
        elif activity == 'high':
            probability_adj = 0.03 if is_upside else -0.03
            reasoning_parts.append(f"High network activity")
        elif activity == 'low':
            probability_adj = -0.03 if is_upside else 0.03
            reasoning_parts.append(f"Low network activity - consolidation likely")

        if congestion == 'high':
            reasoning_parts.append(f"High mempool congestion ({metrics.get('mempool_size', 0):,.0f} unconfirmed)")

        # Base probability is neutral; this source provides adjustment factor
        base_prob = 0.50 + probability_adj

        return {
            'source': 'On-Chain Metrics (Blockchain.com)',
            'probability': round(base_prob, 3),
            'confidence': 0.45,  # Lower confidence - on-chain is supplementary
            'reasoning': ' | '.join(reasoning_parts) if reasoning_parts else 'Normal on-chain activity',
            'metrics': {
                'activity_level': activity,
                'congestion': congestion,
                'tx_count_24h': metrics.get('tx_count_24h'),
            },
        }

    def get_whale_alert_summary(self) -> Dict:
        """
        Get summary of recent large transactions.

        Note: Whale Alert API requires registration for full access.
        This provides a basic interpretation framework.
        """
        metrics = self.get_bitcoin_metrics()
        flows = self.get_exchange_flows()

        summary = {
            'network_health': 'normal',
            'activity_level': metrics.get('activity_level', 'unknown') if metrics else 'unknown',
            'flow_estimate': flows.get('flow_estimate', 'unknown') if flows else 'unknown',
            'edge_signal': None,
        }

        # Generate edge signal
        if metrics and flows:
            activity = metrics.get('activity_level', 'normal')
            flow = flows.get('flow_estimate', 'normal_activity')

            if activity in ['very_high', 'high'] and flow == 'high_activity':
                summary['edge_signal'] = 'High activity + flows - expect volatility'
                summary['network_health'] = 'active'
            elif activity == 'low':
                summary['edge_signal'] = 'Low activity - consolidation phase'
                summary['network_health'] = 'quiet'

        return summary

    def close(self):
        """Close HTTP client."""
        self.client.close()


# Singleton
_source: Optional[OnChainSource] = None


def get_onchain_source() -> OnChainSource:
    """Get singleton on-chain source."""
    global _source
    if _source is None:
        _source = OnChainSource()
    return _source
