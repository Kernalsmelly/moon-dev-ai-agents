"""
Polymarket Micro-Edge Agent

Main orchestrator for the micro-capital Polymarket strategy.

INVERTS the logic from polymarket_agent.py:
- Original agent tracks BIG money (>$500 trades)
- We hunt in the LONG TAIL: low liquidity, niche categories, near-resolution

Discovery Cycle:
1. Fetch ALL markets from Gamma API (not just WebSocket trades)
2. Apply niche classifier (score 0-10)
3. Filter for targets (score >= 6)
4. Check resolution timing for edge
5. Run correlation checks for arbitrage
6. Validate with AI swarm (optional)
7. Generate signals
8. Execute via limit orders (dry-run by default)

Run: python -m src.agents.polymarket_micro_edge_agent
"""

import os
import sys
import time
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional
from pathlib import Path

# Add project root to path
project_root = str(Path(__file__).parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from termcolor import cprint

from src.config.polymarket_micro_config import (
    get_config_summary,
    MARKET_SCAN_INTERVAL_SECONDS,
    SIGNAL_CHECK_INTERVAL_SECONDS,
    MIN_NICHE_SCORE,
    MIN_EDGE_PCT,
    ARBITRAGE_THRESHOLD,
    DRY_RUN_MODE,
    MICRO_POSITION_SIZE_USD,
)
from src.polymarket.gamma_api import GammaAPI
from src.polymarket.market_classifier import NicheClassifier
from src.polymarket.liquidity_analyzer import LiquidityAnalyzer
from src.polymarket.resolution_timer import ResolutionTimer
from src.polymarket.correlation_engine import CorrelationEngine
from src.polymarket.micro_store import MicroEdgeStore, get_store

# Optional: AI swarm for validation
try:
    from src.agents.swarm_agent import SwarmAgent
    HAS_SWARM = True
except ImportError:
    HAS_SWARM = False
    SwarmAgent = None

# Optional: Executor for live trading
try:
    from src.agents.polymarket_executor import PolymarketExecutor
    HAS_EXECUTOR = True
except ImportError:
    HAS_EXECUTOR = False
    PolymarketExecutor = None

# Optional: Discord alerts
try:
    from src.alerts import send_system_alert
    HAS_DISCORD = True
except ImportError:
    HAS_DISCORD = False
    send_system_alert = None

# Optional: External data sources for real edge
try:
    from src.polymarket.data_sources import DataSourceManager
    HAS_DATA_SOURCES = True
except ImportError:
    HAS_DATA_SOURCES = False
    DataSourceManager = None

logger = logging.getLogger(__name__)


class PolymarketMicroEdgeAgent:
    """
    Orchestrates the micro-edge discovery and trading strategy.

    Key difference from polymarket_agent.py:
    - Scans ALL markets via Gamma API (not just WebSocket trades)
    - Targets LOW liquidity (<$5k) instead of filtering for high volume
    - INCLUDES near-resolution markets instead of excluding them
    - INCLUDES niche sports (esports, MMA, golf) instead of filtering them
    """

    def __init__(
        self,
        dry_run: bool = None,
        use_swarm: bool = False,
    ):
        cprint("\n" + "=" * 80, "cyan")
        cprint("Polymarket Micro-Edge Agent - Initializing", "cyan", attrs=['bold'])
        cprint("=" * 80, "cyan")

        self.dry_run = dry_run if dry_run is not None else DRY_RUN_MODE
        self.use_swarm = use_swarm and HAS_SWARM

        # Initialize components
        self.api = GammaAPI()
        self.classifier = NicheClassifier()
        self.liquidity = LiquidityAnalyzer()
        self.resolution = ResolutionTimer()
        self.correlator = CorrelationEngine()
        self.store = get_store()

        # Optional components
        self.swarm = SwarmAgent() if self.use_swarm else None
        self.executor = None
        if HAS_EXECUTOR:
            self.executor = PolymarketExecutor(dry_run=self.dry_run)

        # Data sources for real edge calculation
        self.data_sources = None
        if HAS_DATA_SOURCES:
            self.data_sources = DataSourceManager()
            cprint("External Data Sources: Enabled", "green")
        else:
            cprint("External Data Sources: Disabled (no API keys)", "yellow")

        # Tracking
        self.last_scan_time = None
        self.markets_scanned = 0
        self.signals_generated = 0
        self.positions_opened = 0

        cprint(get_config_summary(), "white")
        cprint(f"Dry Run Mode: {'YES' if self.dry_run else 'NO (LIVE TRADING)'}", "yellow" if self.dry_run else "red", attrs=['bold'])
        cprint(f"AI Swarm: {'Enabled' if self.use_swarm else 'Disabled'}", "cyan")
        cprint("=" * 80 + "\n", "cyan")

        # Send startup notification to Discord
        if HAS_DISCORD and send_system_alert:
            send_system_alert(
                title='Polymarket Micro-Edge Agent Started',
                description='Hunting for micro-edges in low-liquidity prediction markets',
                level='success',
                fields=[
                    {'name': 'Mode', 'value': 'DRY RUN' if self.dry_run else 'LIVE', 'inline': True},
                    {'name': 'Position Size', 'value': f'${MICRO_POSITION_SIZE_USD}', 'inline': True},
                    {'name': 'Min Niche Score', 'value': str(MIN_NICHE_SCORE), 'inline': True},
                ],
            )

    def run_discovery_cycle(self) -> Dict:
        """
        Main discovery cycle - finds opportunities across all markets.

        Returns summary of discoveries.
        """
        cprint("\n" + "=" * 80, "magenta")
        cprint(f"Discovery Cycle @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "magenta", attrs=['bold'])
        cprint("=" * 80, "magenta")

        results = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'markets_fetched': 0,
            'niche_targets': 0,
            'resolution_opportunities': 0,
            'arbitrage_opportunities': 0,
            'signals_generated': 0,
        }

        # Step 1: Fetch truly active markets with upcoming resolutions
        cprint("\n[1/5] Fetching ACTIVE markets with upcoming resolutions...", "cyan")
        markets = self.api.get_active_markets_with_upcoming_resolution(max_pages=10)

        if not markets:
            cprint("No active markets found - API may be down or all markets resolved", "red")
            return results

        # Markets are already parsed and filtered by the API method
        results['markets_fetched'] = len(markets)
        cprint(f"   Found {len(markets)} truly active markets with upcoming resolution", "green")

        # Step 2: Apply niche classifier
        cprint("\n[2/5] Applying niche classifier...", "cyan")
        niche_targets = self.classifier.filter_target_markets(markets)
        results['niche_targets'] = len(niche_targets)
        cprint(f"   Found {len(niche_targets)} niche targets (score >= {MIN_NICHE_SCORE})", "green")

        # Show top niche markets
        if niche_targets:
            cprint("\n   Top 5 Niche Markets:", "yellow")
            for i, m in enumerate(niche_targets[:5]):
                score = m.get('niche_score', 0)
                cat = m.get('niche_category', 'general')
                question = (m.get('question') or '')[:60]
                cprint(f"   {i+1}. [{score:.1f}] ({cat}) {question}...", "white")

        # Step 3: Calculate REAL edge from external data sources
        data_edge_opps = []
        if self.data_sources:
            cprint("\n[3/6] Calculating REAL edge from external data...", "cyan")
            for market in niche_targets[:100]:  # Check top 100 niche targets
                edge_data = self.data_sources.calculate_real_edge(market)
                if edge_data and edge_data.get('has_edge'):
                    market['real_edge'] = edge_data
                    data_edge_opps.append(market)

            results['data_edge_opportunities'] = len(data_edge_opps)
            cprint(f"   Found {len(data_edge_opps)} opportunities with REAL external edge", "green")

            if data_edge_opps:
                cprint("\n   Top Data-Backed Opportunities:", "yellow")
                for i, m in enumerate(data_edge_opps[:3]):
                    edge = m['real_edge']
                    question = (m.get('question') or '')[:45]
                    cprint(f"   {i+1}. {edge['recommended_side']} @ {edge['edge_pct']:.1f}% edge ({edge['source']}) | {question}...", "white")
        else:
            cprint("\n[3/6] External data sources not configured - skipping real edge calc", "yellow")
            results['data_edge_opportunities'] = 0

        # Step 4: Check resolution timing
        cprint("\n[4/6] Checking resolution timing opportunities...", "cyan")
        resolution_opps = self.resolution.find_resolution_opportunities(niche_targets)
        results['resolution_opportunities'] = len(resolution_opps)
        cprint(f"   Found {len(resolution_opps)} near-resolution opportunities", "green")

        if resolution_opps:
            cprint("\n   Top Resolution Plays:", "yellow")
            for i, m in enumerate(resolution_opps[:3]):
                analysis = m.get('resolution_analysis', {})
                hours = analysis.get('hours_to_resolution', 0)
                edge = analysis.get('best_edge_pct', 0)
                side = analysis.get('best_side', '?')
                question = (m.get('question') or '')[:50]
                cprint(f"   {i+1}. {hours:.0f}h | {side} @ {edge:.1f}% edge | {question}...", "white")

        # Step 5: Check correlation arbitrage
        cprint("\n[5/6] Scanning for correlation arbitrage...", "cyan")
        arb_opps = self.correlator.find_all_arbitrage_opportunities(niche_targets)
        results['arbitrage_opportunities'] = len(arb_opps)
        cprint(f"   Found {len(arb_opps)} arbitrage opportunities", "green")

        if arb_opps:
            cprint("\n   Arbitrage Opportunities:", "yellow")
            for i, arb in enumerate(arb_opps[:3]):
                pct = arb.get('arbitrage_pct', 0)
                strategy = arb.get('strategy', '')[:60]
                cprint(f"   {i+1}. {pct:.1f}% arb - {strategy}...", "white")

        # Step 6: Generate signals (prioritize data-backed edges)
        cprint("\n[6/6] Generating signals...", "cyan")
        signals = self._generate_signals(resolution_opps, arb_opps, data_edge_opps)
        results['signals_generated'] = len(signals)
        cprint(f"   Generated {len(signals)} trading signals", "green")

        # Store markets and signals in database
        self._persist_discoveries(niche_targets, signals)

        # Update tracking
        self.last_scan_time = datetime.now(timezone.utc)
        self.markets_scanned += results['markets_fetched']
        self.signals_generated += results['signals_generated']

        # Summary
        cprint("\n" + "=" * 80, "green")
        cprint("Discovery Cycle Complete", "green", attrs=['bold'])
        cprint(f"   Markets: {results['markets_fetched']} fetched, {results['niche_targets']} niche targets", "white")
        cprint(f"   Opportunities: {results['resolution_opportunities']} resolution, {results['arbitrage_opportunities']} arbitrage", "white")
        cprint(f"   Signals: {results['signals_generated']} generated", "white")
        cprint("=" * 80 + "\n", "green")

        # Send Discord notification if opportunities found
        self._send_discord_summary(results, niche_targets, resolution_opps, arb_opps)

        return results

    def _send_discord_summary(
        self,
        results: Dict,
        niche_targets: List[Dict],
        resolution_opps: List[Dict],
        arb_opps: List[Dict],
    ):
        """Send discovery summary to Discord if configured."""
        if not HAS_DISCORD or send_system_alert is None:
            return

        # Only send if we found something interesting
        total_opps = results['resolution_opportunities'] + results['arbitrage_opportunities']
        if total_opps == 0 and results['signals_generated'] == 0:
            return  # Skip silent cycles

        # Build fields
        fields = [
            {'name': 'Markets Scanned', 'value': str(results['markets_fetched']), 'inline': True},
            {'name': 'Niche Targets', 'value': str(results['niche_targets']), 'inline': True},
            {'name': 'Signals', 'value': str(results['signals_generated']), 'inline': True},
        ]

        # Add top opportunities
        if resolution_opps:
            top_res = resolution_opps[0]
            analysis = top_res.get('resolution_analysis', {})
            fields.append({
                'name': 'Top Resolution Play',
                'value': f"{analysis.get('best_side', '?')} @ {analysis.get('best_edge_pct', 0):.1f}% edge - {top_res.get('question', '')[:50]}...",
                'inline': False,
            })

        if arb_opps:
            top_arb = arb_opps[0]
            fields.append({
                'name': 'Top Arbitrage',
                'value': f"{top_arb.get('arbitrage_pct', 0):.1f}% - {top_arb.get('strategy', '')[:60]}",
                'inline': False,
            })

        send_system_alert(
            title='Polymarket Micro-Edge Scan',
            description=f"Found {total_opps} opportunities across {results['niche_targets']} niche markets",
            level='success' if results['signals_generated'] > 0 else 'info',
            fields=fields,
        )

    def _generate_signals(
        self,
        resolution_opps: List[Dict],
        arb_opps: List[Dict],
        data_edge_opps: List[Dict] = None,
    ) -> List[Dict]:
        """
        Generate trading signals from discovered opportunities.

        Priority order:
        1. Data-backed edge (highest confidence - external data)
        2. Resolution timing
        3. Arbitrage
        """
        signals = []
        data_edge_opps = data_edge_opps or []
        seen_markets = set()  # Track (condition_id, side) to avoid duplicates

        # Minimum entry price to filter lottery tickets
        MIN_ENTRY_PRICE = 0.10

        # DATA-BACKED signals (highest priority - real external edge)
        for market in data_edge_opps:
            edge_data = market.get('real_edge', {})
            if not edge_data.get('has_edge'):
                continue

            side = edge_data.get('recommended_side', 'YES')
            if side == 'YES':
                entry_price = market.get('yes_price', 0.5)
            else:
                entry_price = market.get('no_price', 0) or (1 - market.get('yes_price', 0.5))

            # Skip lottery tickets
            if entry_price < MIN_ENTRY_PRICE:
                continue

            # Skip if we already have a signal for this market/side
            market_key = (market.get('condition_id'), side)
            if market_key in seen_markets:
                continue
            seen_markets.add(market_key)

            signal = {
                'condition_id': market.get('condition_id'),
                'signal_type': 'data_edge',  # New type - data-backed
                'side': side,
                'entry_price': entry_price,
                'target_price': edge_data.get('external_probability', 0.5),
                'confidence': edge_data.get('confidence', 0.7),
                'edge_pct': edge_data.get('edge_pct', 0),
                'notes': f"[{edge_data.get('source', 'External')}] {edge_data.get('reasoning', '')[:80]}",
            }
            signals.append(signal)

        # Resolution timing signals
        for market in resolution_opps:
            analysis = market.get('resolution_analysis', {})
            if not analysis.get('is_opportunity'):
                continue

            # Get entry price based on best side
            best_side = analysis.get('best_side', 'YES')
            inner_analysis = analysis.get('analysis', {})
            if best_side == 'YES':
                entry_price = inner_analysis.get('yes_analysis', {}).get('entry_price', 0)
                if entry_price == 0:
                    entry_price = market.get('yes_price', 0)
            else:
                entry_price = inner_analysis.get('no_analysis', {}).get('entry_price', 0)
                if entry_price == 0:
                    entry_price = market.get('no_price', 0) or (1 - market.get('yes_price', 0.5))

            # Skip lottery tickets
            if entry_price < MIN_ENTRY_PRICE:
                continue

            # Skip if we already have a signal for this market/side
            market_key = (market.get('condition_id'), best_side)
            if market_key in seen_markets:
                continue
            seen_markets.add(market_key)

            signal = {
                'condition_id': market.get('condition_id'),
                'signal_type': 'resolution',
                'side': best_side,
                'entry_price': entry_price,
                'target_price': 1.0,  # Resolution = full payout
                'confidence': min(analysis.get('best_edge_pct', 0) / 20, 0.95),  # Convert edge to confidence
                'edge_pct': analysis.get('best_edge_pct', 0),
                'notes': f"Resolution in {analysis.get('hours_to_resolution', 0):.0f}h",
            }
            signals.append(signal)

        # Arbitrage signals
        for arb in arb_opps:
            # For pair arbitrage, create signal for the underpriced side
            if 'market_a' in arb:
                entry_price = arb['market_a']['yes_price']
                side = 'YES' if arb['actual_sum'] < 1.0 else 'NO'

                # Skip lottery tickets
                if entry_price < MIN_ENTRY_PRICE:
                    continue

                # Skip if we already have a signal for this market/side
                market_key = (arb['market_a']['condition_id'], side)
                if market_key in seen_markets:
                    continue
                seen_markets.add(market_key)

                signal = {
                    'condition_id': arb['market_a']['condition_id'],
                    'signal_type': 'correlation',
                    'side': side,
                    'entry_price': entry_price,
                    'target_price': arb['expected_sum'] / 2,  # Fair value
                    'confidence': 0.9,  # High confidence for arb
                    'edge_pct': arb['arbitrage_pct'],
                    'notes': arb.get('strategy', '')[:100],
                }
                signals.append(signal)

        # Sort signals by edge (highest first) before returning
        signals.sort(key=lambda x: x.get('edge_pct', 0), reverse=True)

        return signals

    def _persist_discoveries(
        self,
        markets: List[Dict],
        signals: List[Dict],
    ):
        """Save discovered markets and signals to database."""
        # Store markets
        for market in markets:
            self.store.upsert_market(market)

        # Store signals
        for signal in signals:
            self.store.create_signal(
                condition_id=signal['condition_id'],
                signal_type=signal['signal_type'],
                side=signal['side'],
                entry_price=signal['entry_price'],
                target_price=signal['target_price'],
                confidence=signal['confidence'],
                edge_pct=signal['edge_pct'],
                notes=signal.get('notes', ''),
            )

    def execute_pending_signals(self) -> int:
        """
        Execute pending signals via limit orders.

        Returns number of orders placed.
        """
        if self.executor is None:
            cprint("No executor available - cannot execute signals", "yellow")
            return 0

        pending = self.store.get_pending_signals(limit=100)  # Get more, then filter

        # Filter out lottery tickets (entry < $0.10) and sort by edge
        quality_signals = [
            s for s in pending
            if s.get('entry_price', 0) >= 0.10
        ]

        # Sort by edge percentage (highest first)
        quality_signals.sort(key=lambda x: x.get('edge_pct', 0), reverse=True)

        # Deduplicate - only one signal per market (keep highest edge)
        seen_markets = set()
        unique_signals = []
        for sig in quality_signals:
            market_key = (sig.get('condition_id'), sig.get('side'))
            if market_key not in seen_markets:
                seen_markets.add(market_key)
                unique_signals.append(sig)

        # Take top 10 unique signals
        quality_signals = unique_signals[:10]

        executed = 0

        for signal in quality_signals:
            cprint(f"\nExecuting signal: {signal['signal_type']} {signal['side']} @ ${signal['entry_price']:.2f} ({signal.get('edge_pct', 0):.1f}% edge)", "cyan")
            cprint(f"   Market: {signal.get('question', '')[:50]}...", "white")

            if self.dry_run:
                cprint("   [DRY RUN] Would place limit order", "yellow")
                self.store.update_signal_status(signal['id'], 'active')
                executed += 1
            else:
                # Place actual order
                result = self.executor.place_limit_order(
                    market_token_id=signal['condition_id'],
                    side='buy' if signal['side'] == 'YES' else 'sell',
                    price=signal['entry_price'],
                    size=MICRO_POSITION_SIZE_USD / signal['entry_price'],
                )

                if result.get('success'):
                    cprint(f"   Order placed: {result.get('result')}", "green")
                    self.store.update_signal_status(signal['id'], 'active')
                    executed += 1
                else:
                    cprint(f"   Order failed: {result.get('error')}", "red")

        return executed

    def get_status(self) -> Dict:
        """Get current agent status."""
        open_positions = self.store.get_open_positions()
        pending_signals = self.store.get_pending_signals()
        performance = self.store.get_performance_summary()

        return {
            'last_scan': self.last_scan_time.isoformat() if self.last_scan_time else None,
            'markets_scanned_total': self.markets_scanned,
            'signals_generated_total': self.signals_generated,
            'open_positions': len(open_positions),
            'pending_signals': len(pending_signals),
            'performance': performance,
            'dry_run': self.dry_run,
        }

    def print_performance_dashboard(self):
        """Print a detailed performance dashboard."""
        cprint("\n" + "=" * 80, "cyan")
        cprint("PERFORMANCE DASHBOARD", "cyan", attrs=['bold'])
        cprint("=" * 80, "cyan")

        # Overall performance
        perf = self.store.get_performance_summary()
        cprint("\n📊 OVERALL PERFORMANCE:", "yellow", attrs=['bold'])
        cprint(f"   Total Trades: {perf['total_trades']}", "white")
        cprint(f"   Win Rate: {perf['win_rate']:.1f}%", "green" if perf['win_rate'] >= 50 else "red")
        cprint(f"   Total PnL: ${perf['total_pnl']:.2f}", "green" if perf['total_pnl'] >= 0 else "red")
        cprint(f"   Avg PnL/Trade: ${perf['avg_pnl']:.2f}", "white")
        cprint(f"   Best Trade: ${perf.get('best_trade', 0):.2f}", "green")
        cprint(f"   Worst Trade: ${perf.get('worst_trade', 0):.2f}", "red")

        # Open positions
        open_pos = self.store.get_open_positions()
        cprint(f"\n📈 OPEN POSITIONS ({len(open_pos)}):", "yellow", attrs=['bold'])
        if open_pos:
            for pos in open_pos[:5]:
                current_price = pos.get('yes_price') if pos['side'] == 'YES' else pos.get('no_price', 0) or (1 - pos.get('yes_price', 0.5))
                unrealized = pos['shares'] * (current_price - pos['entry_price']) if pos['side'] == 'YES' else pos['shares'] * (pos['entry_price'] - current_price)
                question = (pos.get('question') or '')[:40]
                cprint(f"   {pos['side']} @ ${pos['entry_price']:.2f} → ${current_price:.2f} ({unrealized:+.2f}) | {question}...",
                       "green" if unrealized >= 0 else "red")
        else:
            cprint("   No open positions", "white")

        # Pending signals
        pending = self.store.get_pending_signals(limit=10)
        cprint(f"\n⏳ PENDING SIGNALS ({len(pending)}):", "yellow", attrs=['bold'])
        if pending:
            for sig in pending[:5]:
                question = (sig.get('question') or '')[:35]
                cprint(f"   {sig['signal_type']} {sig['side']} @ ${sig['entry_price']:.2f} ({sig['edge_pct']:.1f}% edge) | {question}...", "white")
        else:
            cprint("   No pending signals", "white")

        # Signal breakdown by type
        cprint("\n📋 SIGNAL BREAKDOWN:", "yellow", attrs=['bold'])
        conn = self.store._get_conn()
        cur = conn.execute('''
            SELECT signal_type, COUNT(*) as count,
                   AVG(edge_pct) as avg_edge
            FROM signals
            GROUP BY signal_type
        ''')
        for row in cur.fetchall():
            cprint(f"   {row['signal_type']}: {row['count']} signals (avg edge: {row['avg_edge']:.1f}%)", "white")

        cprint("\n" + "=" * 80 + "\n", "cyan")

    def run_continuous(self, scan_interval: int = None):
        """
        Run continuous discovery and execution loop.

        Press Ctrl+C to stop.
        """
        scan_interval = scan_interval or MARKET_SCAN_INTERVAL_SECONDS

        cprint("\n" + "=" * 80, "cyan")
        cprint("Starting Continuous Micro-Edge Discovery", "cyan", attrs=['bold'])
        cprint(f"   Scan Interval: {scan_interval} seconds", "white")
        cprint(f"   Dry Run: {self.dry_run}", "white")
        cprint("   Press Ctrl+C to stop", "yellow")
        cprint("=" * 80 + "\n", "cyan")

        cycle_count = 0
        dashboard_interval = 10  # Show dashboard every N cycles

        try:
            while True:
                cycle_count += 1

                # Run discovery
                self.run_discovery_cycle()

                # Execute any pending signals
                executed = self.execute_pending_signals()
                if executed > 0:
                    cprint(f"Executed {executed} signals", "green")

                # Show status
                status = self.get_status()
                cprint(f"\nStatus: {status['open_positions']} positions, {status['pending_signals']} pending signals", "cyan")

                # Show dashboard periodically
                if cycle_count % dashboard_interval == 0:
                    self.print_performance_dashboard()

                # Wait for next cycle
                next_scan = datetime.now().timestamp() + scan_interval
                next_scan_str = datetime.fromtimestamp(next_scan).strftime('%H:%M:%S')
                cprint(f"Next scan at {next_scan_str}\n", "magenta")

                time.sleep(scan_interval)

        except KeyboardInterrupt:
            cprint("\n\nStopping micro-edge agent...", "yellow")
            self.print_performance_dashboard()  # Show final dashboard
            self.api.close()
            cprint("Agent stopped.", "cyan")


def main():
    """Entry point for the micro-edge agent."""
    import argparse

    parser = argparse.ArgumentParser(description='Polymarket Micro-Edge Agent')
    parser.add_argument('--live', action='store_true', help='Enable live trading (default: dry run)')
    parser.add_argument('--swarm', action='store_true', help='Use AI swarm for validation')
    parser.add_argument('--once', action='store_true', help='Run single discovery cycle and exit')
    parser.add_argument('--interval', type=int, default=300, help='Scan interval in seconds')
    parser.add_argument('--dashboard', action='store_true', help='Show performance dashboard and exit')
    args = parser.parse_args()

    agent = PolymarketMicroEdgeAgent(
        dry_run=not args.live,
        use_swarm=args.swarm,
    )

    if args.dashboard:
        agent.print_performance_dashboard()
    elif args.once:
        agent.run_discovery_cycle()
    else:
        agent.run_continuous(scan_interval=args.interval)


if __name__ == "__main__":
    main()
