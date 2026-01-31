#!/usr/bin/env python3
"""Meme Coin Trading Bot: Automated discovery, scoring, and trading of Solana meme coins.

Architecture:
    DISCOVERY -> FILTER -> SCORE -> DECIDE -> EXECUTE -> MONITOR
        |          |         |         |          |          |
    DexScreener   Rug      VHI     Entry     Jupiter   ExitManager
    (free API)    Liq     Grad   Criteria    Swap     TP/SL/Time
                  Age    Score  RiskAgent   Paper    PositionStore

Usage:
    python src/meme_bot.py              # Run in paper mode (default)
    MEME_PAPER_MODE=false python src/meme_bot.py  # Run in live mode

"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
import requests
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import src.config as config
import src.meme_config as meme_config

# Default excluded tokens (SOL, USDC, USDT) - used if config doesn't have EXCLUDED_TOKENS
DEFAULT_EXCLUDED_TOKENS = {
    'So11111111111111111111111111111111111111112',  # SOL
    'So11111111111111111111111111111111111111111',  # WSOL
    'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',  # USDC
    'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB',  # USDT
}
EXCLUDED_TOKENS = getattr(config, 'EXCLUDED_TOKENS', DEFAULT_EXCLUDED_TOKENS)
from src.meme_exit_manager import MemeExitManager, PositionState, ExitResult
from src.strategies.volume_heat import VolumeHeatIndex
from src.strategies.graduation_sniper import GraduationSniper

# Conditionally import components
try:
    from src.position_store import PositionStore, get_store, record_trade
    HAS_POSITION_STORE = True
except ImportError:
    HAS_POSITION_STORE = False

try:
    from src.alerts import send_trade_alert, send_system_alert
    HAS_ALERTS = True
except ImportError:
    HAS_ALERTS = False

try:
    from src.nice_funcs import token_overview, token_price
    HAS_NICE_FUNCS = True
except (ImportError, ValueError):
    # nice_funcs may raise ValueError if BIRDEYE_API_KEY not set
    # We don't need it - we use DexScreener instead
    HAS_NICE_FUNCS = False
    token_overview = None
    token_price = None

try:
    from src.trade_executor import main as execute_swap
    HAS_TRADE_EXECUTOR = True
except ImportError:
    HAS_TRADE_EXECUTOR = False

try:
    from src.meme_live_safeguards import LiveSafeguards, get_safeguards
    HAS_SAFEGUARDS = True
except ImportError:
    HAS_SAFEGUARDS = False

console = Console()

# Token mints
SOL_MINT = 'So11111111111111111111111111111111111111112'
WSOL_MINT = 'So11111111111111111111111111111111111111111'

# DexScreener API endpoints (no API key required)
DEXSCREENER_BASE = 'https://api.dexscreener.com'
DEXSCREENER_TOKEN_PROFILES = f'{DEXSCREENER_BASE}/token-profiles/latest/v1'
DEXSCREENER_TOKEN_BOOSTS = f'{DEXSCREENER_BASE}/token-boosts/latest/v1'
DEXSCREENER_TOKEN = f'{DEXSCREENER_BASE}/latest/dex/tokens'  # For token details
DEXSCREENER_SEARCH = f'{DEXSCREENER_BASE}/dex/search'


@dataclass
class TokenCandidate:
    """Token candidate for potential entry."""
    mint: str
    symbol: str = ''
    discovered_at: float = 0.0
    liquidity: float = 0.0
    market_cap: float = 0.0
    price: float = 0.0
    is_lp_burned: bool = False
    top_holder_concentration: float = 1.0
    vhi_score: int = 0
    graduation_score: float = 0.0
    composite_score: int = 0
    # Momentum data
    price_change_5m: float = 0.0
    price_change_1h: float = 0.0
    buys_1h: int = 0
    sells_1h: int = 0
    txns_1h: int = 0
    volume_1h: float = 0.0


@dataclass
class ActivePosition:
    """Active trading position."""
    mint: str
    symbol: str
    entry_price: float
    entry_time: float
    amount_tokens: float
    amount_sol: float
    amount_usd: float
    state: PositionState = None
    current_price: float = 0.0
    unrealized_pnl_usd: float = 0.0
    unrealized_pnl_pct: float = 0.0


@dataclass
class ReentryCandidate:
    """Token that was stopped out but may be re-entered on recovery."""
    mint: str
    symbol: str
    exit_price: float  # Price when we exited
    exit_time: float  # When we exited
    original_score: int  # Score when originally entered
    original_size_sol: float  # Original position size
    reentry_attempts: int = 0  # How many times we've re-entered


@dataclass
class PendingEntry:
    """Token awaiting confirmation before entry."""
    mint: str
    symbol: str
    candidate: TokenCandidate
    signal_time: float
    signal_price: float


class MemeCoinBot:
    """Main meme coin trading bot."""

    def __init__(self, paper_mode: bool = True):
        """Initialize the meme coin bot.

        Args:
            paper_mode: If True, simulate trades without executing
        """
        self.paper_mode = paper_mode or meme_config.MEME_PAPER_MODE
        self.running = False

        # Initialize components
        self.vhi = VolumeHeatIndex()
        self.graduation_sniper = GraduationSniper(
            max_top_holder_concentration=meme_config.MAX_TOP_HOLDER_CONCENTRATION
        )
        self.exit_manager = MemeExitManager()

        # State tracking
        self.seen_tokens: set = set()
        self.active_positions: dict[str, ActivePosition] = {}
        self.session_pnl: float = 0.0
        self.session_trades: int = 0
        self.session_wins: int = 0
        self.session_losses: int = 0

        # Persistent stats file
        self._stats_file = Path(os.getenv('MEME_STATS_FILE', 'data/meme_stats.json'))
        self._load_stats()

        # Re-entry watch list: tokens we exited that may recover
        self.reentry_watch: dict[str, ReentryCandidate] = {}

        # Pending entries: tokens waiting for confirmation delay
        self.pending_entries: dict[str, PendingEntry] = {}

        # SOL price cache
        self._sol_price: float = 100.0  # Default fallback
        self._sol_price_updated: float = 0

        # Live trading safeguards
        self.safeguards = get_safeguards() if HAS_SAFEGUARDS and not self.paper_mode else None

        console.print(Panel(
            f"Meme Coin Bot Initialized\n"
            f"Mode: {'PAPER' if self.paper_mode else 'LIVE'}\n"
            f"Max Positions: {meme_config.MAX_POSITIONS}\n"
            f"Min Score: {meme_config.MIN_VHI_SCORE}",
            title="Meme Bot",
            style="cyan"
        ))

    def _load_stats(self):
        """Load persisted session stats from disk."""
        try:
            if self._stats_file.exists():
                data = json.loads(self._stats_file.read_text())
                self.session_pnl = data.get('pnl', 0.0)
                self.session_wins = data.get('wins', 0)
                self.session_losses = data.get('losses', 0)
                self.session_trades = data.get('trades', 0)
                console.print(f"[cyan]Loaded stats: {self.session_wins}W/{self.session_losses}L | P&L: ${self.session_pnl:+.2f}[/cyan]")
        except Exception:
            pass  # Start fresh if file is corrupted

    def _save_stats(self):
        """Persist session stats to disk."""
        try:
            self._stats_file.parent.mkdir(parents=True, exist_ok=True)
            self._stats_file.write_text(json.dumps({
                'pnl': round(self.session_pnl, 2),
                'wins': self.session_wins,
                'losses': self.session_losses,
                'trades': self.session_trades,
                'updated': datetime.now(timezone.utc).isoformat(),
            }))
        except Exception:
            pass

    async def discover_tokens(self) -> list[TokenCandidate]:
        """Fetch new Solana tokens from DexScreener API.

        Uses DexScreener's token-profiles and token-boosts endpoints
        to discover trending/new tokens.

        Returns:
            List of token candidates
        """
        candidates = []
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                # Fetch from both endpoints for better coverage
                all_tokens = []

                # 1. Get latest token profiles (new tokens with metadata)
                try:
                    resp = await client.get(DEXSCREENER_TOKEN_PROFILES)
                    if resp.status_code == 200:
                        data = resp.json()
                        if isinstance(data, list):
                            for item in data:
                                if item.get('chainId') == 'solana':
                                    all_tokens.append({
                                        'address': item.get('tokenAddress'),
                                        'source': 'profiles'
                                    })
                except Exception as e:
                    if meme_config.VERBOSE_LOGGING:
                        console.print(f"[yellow]Token profiles fetch error: {e}[/yellow]")

                # 2. Get boosted tokens (trending tokens)
                try:
                    resp = await client.get(DEXSCREENER_TOKEN_BOOSTS)
                    if resp.status_code == 200:
                        data = resp.json()
                        if isinstance(data, list):
                            for item in data:
                                if item.get('chainId') == 'solana':
                                    all_tokens.append({
                                        'address': item.get('tokenAddress'),
                                        'source': 'boosts'
                                    })
                except Exception as e:
                    if meme_config.VERBOSE_LOGGING:
                        console.print(f"[yellow]Token boosts fetch error: {e}[/yellow]")

                # Process discovered tokens
                for token_info in all_tokens:
                    try:
                        mint = token_info.get('address', '')
                        if not mint:
                            continue

                        # Skip already seen tokens
                        if mint in self.seen_tokens:
                            continue

                        # Skip excluded tokens (SOL, USDC, etc.)
                        if mint in EXCLUDED_TOKENS:
                            continue

                        self.seen_tokens.add(mint)

                        # Create candidate with basic info (details fetched in filter step)
                        candidate = TokenCandidate(
                            mint=mint,
                            discovered_at=time.time(),  # We don't have exact creation time
                        )
                        candidates.append(candidate)

                    except Exception as e:
                        if meme_config.VERBOSE_LOGGING:
                            console.print(f"[yellow]Error processing token: {e}[/yellow]")
                        continue

        except Exception as e:
            console.print(f"[red]Error discovering tokens: {e}[/red]")

        if candidates:
            console.print(f"[green]Discovered {len(candidates)} new tokens[/green]")

        return candidates

    async def filter_token(self, candidate: TokenCandidate) -> bool:
        """Apply filtering criteria to a token.

        Uses data already fetched from DexScreener, with optional
        additional data fetch for detailed filtering.

        Args:
            candidate: Token to filter

        Returns:
            True if token passes all filters
        """
        try:
            # If we don't have data yet, fetch from DexScreener
            if candidate.liquidity == 0 and candidate.market_cap == 0:
                await self._fetch_token_data(candidate)

            # Filter 1: Minimum liquidity
            if candidate.liquidity < meme_config.MIN_LIQUIDITY_USD:
                if meme_config.VERBOSE_LOGGING:
                    console.print(f"[yellow]Low liquidity: {candidate.symbol} (${candidate.liquidity:,.0f})[/yellow]")
                return False

            # Filter 2: Market cap range
            if candidate.market_cap < meme_config.MIN_MARKET_CAP_USD:
                if meme_config.VERBOSE_LOGGING:
                    console.print(f"[yellow]Low mcap: {candidate.symbol} (${candidate.market_cap:,.0f})[/yellow]")
                return False

            if candidate.market_cap > meme_config.MAX_MARKET_CAP_USD:
                if meme_config.VERBOSE_LOGGING:
                    console.print(f"[yellow]High mcap: {candidate.symbol} (${candidate.market_cap:,.0f})[/yellow]")
                return False

            # Filter 3: Token age
            age_seconds = time.time() - candidate.discovered_at
            if age_seconds > meme_config.MAX_TOKEN_AGE_SECONDS:
                if meme_config.VERBOSE_LOGGING:
                    console.print(f"[yellow]Too old: {candidate.symbol} ({age_seconds/60:.1f} min)[/yellow]")
                return False

            # Filter 4: Vampire/copycat token filter - block names of established coins
            vampire_names = getattr(meme_config, 'VAMPIRE_TOKEN_NAMES', set())
            if candidate.symbol.upper() in vampire_names:
                if meme_config.VERBOSE_LOGGING:
                    console.print(f"[yellow]Vampire token: {candidate.symbol} (copying established name)[/yellow]")
                return False

            # Filter 5: Must have a valid price
            if candidate.price <= 0:
                if meme_config.VERBOSE_LOGGING:
                    console.print(f"[yellow]No price: {candidate.symbol}[/yellow]")
                return False

            # Filter 6: Price momentum - reject tokens dumping hard
            if candidate.price_change_5m < meme_config.MIN_PRICE_CHANGE_5M:
                if meme_config.VERBOSE_LOGGING:
                    console.print(f"[yellow]Dumping: {candidate.symbol} ({candidate.price_change_5m:+.1f}% 5m)[/yellow]")
                return False

            # Filter 7: Buy/sell ratio - more buyers than sellers
            if candidate.sells_1h > 0:
                buy_sell_ratio = candidate.buys_1h / candidate.sells_1h
                if buy_sell_ratio < meme_config.MIN_BUY_SELL_RATIO:
                    if meme_config.VERBOSE_LOGGING:
                        console.print(f"[yellow]Sell pressure: {candidate.symbol} (B/S: {buy_sell_ratio:.2f})[/yellow]")
                    return False

            # Filter 8: Minimum activity
            if candidate.txns_1h < meme_config.MIN_TXNS_1H:
                if meme_config.VERBOSE_LOGGING:
                    console.print(f"[yellow]Low activity: {candidate.symbol} ({candidate.txns_1h} txns/1h)[/yellow]")
                return False

            # Filter 9: Pullback entry - don't chase pumps
            if getattr(meme_config, 'PULLBACK_ENTRY_ENABLED', True):
                max_5m_pump = getattr(meme_config, 'MAX_5M_PUMP', 30.0)
                if candidate.price_change_5m > max_5m_pump:
                    if meme_config.VERBOSE_LOGGING:
                        console.print(f"[yellow]Chasing pump: {candidate.symbol} ({candidate.price_change_5m:+.1f}% 5m > {max_5m_pump}%)[/yellow]")
                    return False

            # Filter 10: SOL correlation - pause entries during SOL dumps
            if getattr(meme_config, 'SOL_CORRELATION_ENABLED', True):
                sol_change = await self._get_sol_price_change()
                sol_threshold = getattr(meme_config, 'SOL_DUMP_THRESHOLD', -3.0)
                if sol_change is not None and sol_change < sol_threshold:
                    console.print(f"[yellow]SOL dumping: {sol_change:+.1f}% 1h (threshold: {sol_threshold}%) - pausing entries[/yellow]")
                    return False

            # Filter 11: LP Lock check - prefer tokens with locked/burned LP
            if getattr(meme_config, 'LP_LOCK_REQUIRED', True):
                lp_locked = await self._check_lp_lock(candidate)
                if not lp_locked:
                    console.print(f"[yellow]LP not locked: {candidate.symbol} (rug risk)[/yellow]")
                    return False

            console.print(f"[green]PASSED FILTERS: {candidate.symbol} | Liq: ${candidate.liquidity:,.0f} | MCap: ${candidate.market_cap:,.0f} | 5m: {candidate.price_change_5m:+.1f}% | B/S: {candidate.buys_1h}/{candidate.sells_1h}[/green]")
            return True

        except Exception as e:
            console.print(f"[red]Error filtering token: {e}[/red]")
            return False

    async def _fetch_token_data(self, candidate: TokenCandidate):
        """Fetch detailed token data from DexScreener.

        Args:
            candidate: Token candidate to update with data
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                url = f"{DEXSCREENER_TOKEN}/{candidate.mint}"
                resp = await client.get(url)
                if resp.status_code != 200:
                    return

                data = resp.json()
                pairs = data.get('pairs', [])
                if not pairs:
                    return

                # Use the highest liquidity pair
                best_pair = max(pairs, key=lambda p: float(p.get('liquidity', {}).get('usd', 0) or 0))

                # Basic data
                liquidity = best_pair.get('liquidity', {})
                candidate.liquidity = float(liquidity.get('usd', 0) or 0)
                candidate.market_cap = float(best_pair.get('fdv', 0) or 0)
                candidate.price = float(best_pair.get('priceUsd', 0) or 0)

                base_token = best_pair.get('baseToken', {})
                if base_token.get('symbol'):
                    candidate.symbol = base_token['symbol']

                # Momentum data - price changes
                price_change = best_pair.get('priceChange', {})
                candidate.price_change_5m = float(price_change.get('m5', 0) or 0)
                candidate.price_change_1h = float(price_change.get('h1', 0) or 0)

                # Transaction data
                txns = best_pair.get('txns', {})
                h1_txns = txns.get('h1', {})
                candidate.buys_1h = int(h1_txns.get('buys', 0) or 0)
                candidate.sells_1h = int(h1_txns.get('sells', 0) or 0)
                candidate.txns_1h = candidate.buys_1h + candidate.sells_1h

                # Volume data
                volume = best_pair.get('volume', {})
                candidate.volume_1h = float(volume.get('h1', 0) or 0)

                # Pair creation time - use actual on-chain creation, not discovery time
                pair_created_at = best_pair.get('pairCreatedAt')
                if pair_created_at:
                    # pairCreatedAt is in milliseconds
                    candidate.discovered_at = pair_created_at / 1000.0

        except Exception as e:
            if meme_config.VERBOSE_LOGGING:
                console.print(f"[yellow]Error fetching token data: {e}[/yellow]")

    async def _get_sol_price_change(self) -> float | None:
        """Get SOL price change in the last hour.

        Uses DexScreener to get SOL/USDC pair data.

        Returns:
            Price change percentage (e.g., -3.5 for -3.5%) or None if unavailable
        """
        try:
            # Cache SOL price change for 60 seconds to avoid excessive API calls
            now = time.time()
            if hasattr(self, '_sol_price_cache'):
                cache_time, cache_value = self._sol_price_cache
                if now - cache_time < 60:
                    return cache_value

            # SOL/USDC on Raydium - most liquid pair
            sol_mint = "So11111111111111111111111111111111111111112"
            async with httpx.AsyncClient(timeout=10.0) as client:
                url = f"{DEXSCREENER_TOKEN}/{sol_mint}"
                resp = await client.get(url)
                if resp.status_code != 200:
                    return None

                data = resp.json()
                pairs = data.get('pairs', [])
                if not pairs:
                    return None

                # Find highest liquidity USDC or USDT pair
                best_pair = None
                best_liq = 0
                for pair in pairs:
                    quote = pair.get('quoteToken', {}).get('symbol', '').upper()
                    if quote in ('USDC', 'USDT'):
                        liq = float(pair.get('liquidity', {}).get('usd', 0) or 0)
                        if liq > best_liq:
                            best_liq = liq
                            best_pair = pair

                if not best_pair:
                    return None

                price_change = best_pair.get('priceChange', {})
                sol_1h_change = float(price_change.get('h1', 0) or 0)

                # Cache the result
                self._sol_price_cache = (now, sol_1h_change)
                return sol_1h_change

        except Exception as e:
            if meme_config.VERBOSE_LOGGING:
                console.print(f"[yellow]Error fetching SOL price: {e}[/yellow]")
            return None

    async def _check_lp_lock(self, candidate: TokenCandidate) -> bool:
        """Check if token has locked/burned liquidity to prevent rugs.

        Uses multiple signals:
        1. Pool age > 1 hour (very new pools are risky)
        2. High transaction count (indicates legitimate activity)
        3. Liquidity depth (higher liq = harder to rug)

        Args:
            candidate: Token to check

        Returns:
            True if LP appears safe (locked/burned or low rug risk)
        """
        try:
            # Signal 1: Pool must be at least 1 hour old
            # Very new pools are much more likely to rug
            min_pool_age_seconds = 3600  # 1 hour
            pool_age = time.time() - candidate.discovered_at
            if pool_age < min_pool_age_seconds:
                if meme_config.VERBOSE_LOGGING:
                    console.print(f"[yellow]Pool too new: {candidate.symbol} ({pool_age/60:.0f} min old)[/yellow]")
                return False

            # Signal 2: Require substantial transaction history
            # Rugs typically have few transactions before pulling
            min_txns = 200  # Require at least 200 transactions in 1h
            if candidate.txns_1h < min_txns:
                if meme_config.VERBOSE_LOGGING:
                    console.print(f"[yellow]Low tx count: {candidate.symbol} ({candidate.txns_1h} txns)[/yellow]")
                return False

            # Signal 3: Higher liquidity = harder to rug
            # Tokens with $15k+ liquidity are less likely to be quick rugs
            # (We already filter by min liquidity, but this is a safety check)
            min_liq_for_trust = getattr(meme_config, 'MIN_LIQUIDITY_USD', 15000)
            if candidate.liquidity < min_liq_for_trust:
                return False

            # Signal 4: Check buy/sell balance - heavy sell pressure = potential rug setup
            if candidate.sells_1h > 0:
                ratio = candidate.buys_1h / candidate.sells_1h
                if ratio < 0.8:  # More sells than buys = suspicious
                    if meme_config.VERBOSE_LOGGING:
                        console.print(f"[yellow]Sell pressure suspicious: {candidate.symbol} (B/S: {ratio:.2f})[/yellow]")
                    return False

            return True

        except Exception as e:
            if meme_config.VERBOSE_LOGGING:
                console.print(f"[yellow]LP check error: {e}[/yellow]")
            # Default to allowing if check fails (don't block on errors)
            return True

    async def score_token(self, candidate: TokenCandidate) -> int:
        """Calculate composite score for a token.

        Uses momentum, volume, and transaction data for scoring.

        Args:
            candidate: Token candidate

        Returns:
            Composite score 0-100
        """
        try:
            # Fetch detailed pair data if not already fetched
            if candidate.volume_1h == 0:
                await self._fetch_token_data(candidate)

            # === MOMENTUM SCORE (0-40 points) ===
            momentum_score = 0

            # Price change 5m: +10% = 20 points, 0% = 10 points, -5% = 0 points
            if candidate.price_change_5m >= 10:
                momentum_score += 20
            elif candidate.price_change_5m >= 0:
                momentum_score += 10 + candidate.price_change_5m
            elif candidate.price_change_5m >= -5:
                momentum_score += 10 + (candidate.price_change_5m * 2)

            # Price change 1h: +50% = 20 points, 0% = 5 points
            if candidate.price_change_1h >= 50:
                momentum_score += 20
            elif candidate.price_change_1h >= 0:
                momentum_score += 5 + min(15, candidate.price_change_1h * 0.3)

            # === VOLUME SCORE (0-30 points) ===
            volume_score = 0

            # Transaction count: 200+ txns = 15 points
            if candidate.txns_1h >= 200:
                volume_score += 15
            elif candidate.txns_1h >= 100:
                volume_score += 10
            elif candidate.txns_1h >= 50:
                volume_score += 5

            # Buy/sell ratio: 2.0+ = 15 points, 1.5 = 10 points, 1.0 = 5 points
            if candidate.sells_1h > 0:
                bs_ratio = candidate.buys_1h / candidate.sells_1h
                if bs_ratio >= 2.0:
                    volume_score += 15
                elif bs_ratio >= 1.5:
                    volume_score += 10
                elif bs_ratio >= 1.0:
                    volume_score += 5

            # === LIQUIDITY SCORE (0-20 points) ===
            liquidity_score = 0

            # Higher liquidity = safer
            if candidate.liquidity >= 100000:
                liquidity_score += 20
            elif candidate.liquidity >= 50000:
                liquidity_score += 15
            elif candidate.liquidity >= 20000:
                liquidity_score += 10
            elif candidate.liquidity >= 10000:
                liquidity_score += 5

            # === SOCIAL SCORE (0-10 points) ===
            social_score = 0
            if candidate.is_lp_burned:
                social_score += 10

            # Compute final score
            composite = momentum_score + volume_score + liquidity_score + social_score
            candidate.composite_score = int(min(100, max(0, composite)))

            # Store component scores for display
            candidate.vhi_score = int(momentum_score)  # Repurpose as momentum
            candidate.graduation_score = float(volume_score)  # Repurpose as volume

            if meme_config.VERBOSE_LOGGING or candidate.composite_score >= meme_config.MIN_VHI_SCORE:
                console.print(f"[cyan]SCORE: {candidate.symbol} = {candidate.composite_score} (Mom:{momentum_score}, Vol:{volume_score}, Liq:{liquidity_score}, Soc:{social_score})[/cyan]")

            return candidate.composite_score

        except Exception as e:
            console.print(f"[red]Error scoring token: {e}[/red]")
            return 0

    async def should_enter(self, candidate: TokenCandidate) -> bool:
        """Determine if we should enter a position.

        Args:
            candidate: Token candidate

        Returns:
            True if entry criteria are met
        """
        # Check score threshold
        if candidate.composite_score < meme_config.MIN_VHI_SCORE:
            return False

        # Check max positions
        if len(self.active_positions) >= meme_config.MAX_POSITIONS:
            console.print(f"[yellow]Max positions reached ({meme_config.MAX_POSITIONS})[/yellow]")
            return False

        # Check if already holding this token
        if candidate.mint in self.active_positions:
            return False

        return True

    async def execute_entry(self, candidate: TokenCandidate) -> Optional[ActivePosition]:
        """Execute entry into a position.

        Args:
            candidate: Token to buy

        Returns:
            ActivePosition if successful, None otherwise
        """
        try:
            # Calculate position size based on score
            size_sol = meme_config.get_position_size_for_score(candidate.composite_score)
            size_sol = max(meme_config.MIN_POSITION_SIZE_SOL, min(meme_config.MAX_POSITION_SIZE_SOL, size_sol))

            # Get real SOL price for USD calculation
            sol_price_usd = await self._get_sol_price()
            size_usd = size_sol * sol_price_usd

            console.print(Panel(
                f"ENTRY SIGNAL: {candidate.symbol}\n"
                f"Score: {candidate.composite_score}\n"
                f"Size: {size_sol:.2f} SOL (${size_usd:.2f})\n"
                f"Price: ${candidate.price:.10f}\n"
                f"Liquidity: ${candidate.liquidity:,.0f}\n"
                f"Market Cap: ${candidate.market_cap:,.0f}",
                title="BUY SIGNAL",
                style="green"
            ))

            if self.paper_mode:
                # Paper trade: simulate entry
                amount_tokens = size_usd / candidate.price if candidate.price > 0 else 0

                position = ActivePosition(
                    mint=candidate.mint,
                    symbol=candidate.symbol,
                    entry_price=candidate.price,
                    entry_time=time.time(),
                    amount_tokens=amount_tokens,
                    amount_sol=size_sol,
                    amount_usd=size_usd,
                    current_price=candidate.price,
                )

                # Create state for exit manager with score-based stop loss
                initial_stop = meme_config.get_stop_loss_for_score(candidate.composite_score)
                position.state = PositionState(
                    mint=candidate.mint,
                    symbol=candidate.symbol,
                    entry_price=candidate.price,
                    entry_time=time.time(),
                    amount_tokens=amount_tokens,
                    amount_usd=size_usd,
                    score=candidate.composite_score,
                    initial_stop_pct=initial_stop,
                )

                self.active_positions[candidate.mint] = position

                # Store in position store
                if HAS_POSITION_STORE:
                    store = get_store()
                    store.update_position(
                        mint=candidate.mint,
                        symbol=candidate.symbol,
                        entry_price=candidate.price,
                        amount_tokens=amount_tokens,
                        amount_usd=size_usd,
                        status='open',
                        metadata={'paper_mode': True, 'score': candidate.composite_score}
                    )

                # Send Discord alert
                if HAS_ALERTS and meme_config.DISCORD_ALERTS_ENABLED:
                    send_system_alert(
                        f"PAPER ENTRY: {candidate.symbol}",
                        f"Score: {candidate.composite_score} | Size: {size_sol:.2f} SOL",
                        level='info'
                    )

                console.print(f"[green]PAPER ENTRY: {candidate.symbol} @ ${candidate.price:.10f}[/green]")
                return position

            else:
                # Live trade: execute via Jupiter
                if not HAS_TRADE_EXECUTOR:
                    console.print("[red]Trade executor not available[/red]")
                    return None

                # Pre-trade safety checks
                if self.safeguards:
                    safe, reason = await self.safeguards.pre_trade_checks(size_sol, candidate.mint)
                    if not safe:
                        console.print(f"[red]TRADE BLOCKED: {reason}[/red]")
                        return None

                # Execute swap: SOL -> Token
                console.print(f"[yellow]LIVE TRADE: Swapping {size_sol:.4f} SOL -> {candidate.symbol}[/yellow]")
                await execute_swap(
                    amount=size_sol,
                    input_mint=WSOL_MINT,
                    output_mint=candidate.mint,
                    live=True
                )

                # TODO: Confirm transaction and get actual token amount received
                # For now, estimate based on price
                if self.safeguards:
                    # Could add tx confirmation here
                    pass

                # Record position (actual amount will be confirmed on-chain)
                position = ActivePosition(
                    mint=candidate.mint,
                    symbol=candidate.symbol,
                    entry_price=candidate.price,
                    entry_time=time.time(),
                    amount_tokens=0,  # Will be updated from chain
                    amount_sol=size_sol,
                    amount_usd=size_usd,
                )

                # Create state for exit manager with score-based stop loss
                initial_stop = meme_config.get_stop_loss_for_score(candidate.composite_score)
                position.state = PositionState(
                    mint=candidate.mint,
                    symbol=candidate.symbol,
                    entry_price=candidate.price,
                    entry_time=time.time(),
                    amount_tokens=0,
                    amount_usd=size_usd,
                    score=candidate.composite_score,
                    initial_stop_pct=initial_stop,
                )

                self.active_positions[candidate.mint] = position

                console.print(f"[green]LIVE ENTRY: {candidate.symbol} @ ${candidate.price:.10f}[/green]")
                return position

        except Exception as e:
            console.print(f"[red]Error executing entry: {e}[/red]")
            return None

    async def execute_exit(
        self,
        position: ActivePosition,
        exit_result: ExitResult
    ) -> bool:
        """Execute exit from a position.

        Args:
            position: Position to exit
            exit_result: Exit decision from exit manager

        Returns:
            True if exit successful
        """
        try:
            sell_amount = position.amount_tokens * exit_result.sell_fraction
            exit_price = position.current_price

            # Calculate P&L
            entry_value = position.amount_usd * exit_result.sell_fraction
            exit_value = sell_amount * exit_price
            pnl_usd = exit_value - entry_value
            pnl_pct = (pnl_usd / entry_value * 100) if entry_value > 0 else 0

            console.print(Panel(
                f"EXIT SIGNAL: {position.symbol}\n"
                f"Reason: {exit_result.reason}\n"
                f"Sell: {exit_result.sell_fraction * 100:.0f}%\n"
                f"P&L: ${pnl_usd:+.2f} ({pnl_pct:+.1f}%)",
                title="SELL SIGNAL",
                style="red" if pnl_usd < 0 else "green"
            ))

            if self.paper_mode:
                # Paper trade: simulate exit
                self.session_pnl += pnl_usd
                self.session_trades += 1
                if pnl_usd > 0:
                    self.session_wins += 1
                else:
                    self.session_losses += 1
                self._save_stats()

                # Update position
                position.amount_tokens -= sell_amount
                position.amount_usd -= entry_value

                # Record trade with detailed metadata for analysis
                if HAS_POSITION_STORE:
                    hold_time_sec = time.time() - position.entry_time
                    trade_metadata = {
                        'paper_mode': True,
                        'entry_score': position.state.score if position.state else 0,
                        'hold_time_sec': hold_time_sec,
                        'hold_time_min': round(hold_time_sec / 60, 1),
                        'sell_fraction': exit_result.sell_fraction,
                        'highest_price_seen': position.state.highest_price_seen if position.state else 0,
                        'tp1_hit': position.state.tp1_hit if position.state else False,
                        'tp2_hit': position.state.tp2_hit if position.state else False,
                        'tp3_hit': position.state.tp3_hit if position.state else False,
                        'tp4_hit': position.state.tp4_hit if position.state else False,
                        'exit_details': exit_result.details,
                    }
                    record_trade(
                        mint=position.mint,
                        symbol=position.symbol,
                        side='SELL',
                        entry_price=position.entry_price,
                        exit_price=exit_price,
                        amount_usd=entry_value,
                        exit_reason=exit_result.reason,
                        metadata=trade_metadata
                    )

                # Send Discord alert
                if HAS_ALERTS and meme_config.DISCORD_ALERTS_ENABLED:
                    send_trade_alert(
                        symbol=position.symbol,
                        side='SELL',
                        entry_price=position.entry_price,
                        exit_price=exit_price,
                        pnl_usd=pnl_usd,
                        pnl_pct=pnl_pct,
                        exit_reason=exit_result.reason,
                        amount_usd=entry_value,
                        mint=position.mint,
                        session_pnl=self.session_pnl,
                        session_record=f"{self.session_wins}W/{self.session_losses}L"
                    )

                # Remove position if fully exited
                if position.amount_tokens <= 0 or exit_result.sell_fraction >= 1.0:
                    # Add to re-entry watch list if it was a stop loss (not TP)
                    reentry_enabled = getattr(meme_config, 'REENTRY_ENABLED', True)
                    is_stop_exit = 'STOP' in exit_result.reason or 'DUMP' in exit_result.reason
                    if reentry_enabled and is_stop_exit and pnl_usd < 0:
                        max_attempts = getattr(meme_config, 'REENTRY_MAX_ATTEMPTS', 1)
                        existing = self.reentry_watch.get(position.mint)
                        attempts = existing.reentry_attempts if existing else 0
                        if attempts < max_attempts:
                            self.reentry_watch[position.mint] = ReentryCandidate(
                                mint=position.mint,
                                symbol=position.symbol,
                                exit_price=exit_price,
                                exit_time=time.time(),
                                original_score=position.state.score if position.state else 50,
                                original_size_sol=position.amount_sol,
                                reentry_attempts=attempts,
                            )
                            console.print(f"[cyan]REENTRY WATCH: {position.symbol} added (exit: ${exit_price:.10f})[/cyan]")

                    del self.active_positions[position.mint]
                    if HAS_POSITION_STORE:
                        store = get_store()
                        store.close_position(position.mint)

                console.print(f"[{'green' if pnl_usd > 0 else 'red'}]PAPER EXIT: {position.symbol} P&L: ${pnl_usd:+.2f}[/]")
                return True

            else:
                # Live trade: execute via Jupiter
                if not HAS_TRADE_EXECUTOR:
                    console.print("[red]Trade executor not available[/red]")
                    return False

                # Calculate SOL amount to receive (estimate)
                sol_amount_estimate = sell_amount * exit_price / 100  # Assuming SOL = $100

                await execute_swap(
                    amount=sol_amount_estimate,
                    input_mint=position.mint,
                    output_mint=WSOL_MINT,
                    live=True
                )

                # Update tracking
                self.session_pnl += pnl_usd
                self.session_trades += 1
                if pnl_usd > 0:
                    self.session_wins += 1
                else:
                    self.session_losses += 1
                self._save_stats()

                if exit_result.sell_fraction >= 1.0:
                    del self.active_positions[position.mint]

                console.print(f"[green]LIVE EXIT: {position.symbol}[/green]")
                return True

        except Exception as e:
            console.print(f"[red]Error executing exit: {e}[/red]")
            return False

    async def check_reentry_opportunities(self):
        """Check if any stopped-out tokens have recovered enough to re-enter.

        Re-entry conditions:
        - Token has recovered X% from exit price
        - Cooldown period has passed
        - We haven't exceeded max re-entry attempts
        - We have room for more positions
        """
        if not getattr(meme_config, 'REENTRY_ENABLED', True):
            return

        now = time.time()
        cooldown = getattr(meme_config, 'REENTRY_COOLDOWN_SECONDS', 300)
        recovery_threshold = getattr(meme_config, 'REENTRY_RECOVERY_PCT', 0.20)
        max_attempts = getattr(meme_config, 'REENTRY_MAX_ATTEMPTS', 1)
        size_fraction = getattr(meme_config, 'REENTRY_SIZE_FRACTION', 0.50)

        # Check position limit
        max_positions = getattr(meme_config, 'MAX_POSITIONS', 5)
        if len(self.active_positions) >= max_positions:
            return

        # Clean up old watch entries (older than 30 minutes)
        expired = [mint for mint, c in self.reentry_watch.items()
                   if now - c.exit_time > 1800]
        for mint in expired:
            del self.reentry_watch[mint]

        # Check each candidate
        for mint, candidate in list(self.reentry_watch.items()):
            try:
                # Skip if already in position
                if mint in self.active_positions:
                    continue

                # Skip if cooldown not passed
                if now - candidate.exit_time < cooldown:
                    continue

                # Skip if max attempts exceeded
                if candidate.reentry_attempts >= max_attempts:
                    continue

                # Get current price
                price_data = await self._get_price_and_momentum(mint)
                if not price_data:
                    continue

                current_price = price_data.get('price', 0)
                if not current_price or current_price <= 0:
                    continue

                # Check if recovered enough from exit price
                recovery = (current_price - candidate.exit_price) / candidate.exit_price
                if recovery >= recovery_threshold:
                    # Re-enter with reduced size
                    reentry_size_sol = candidate.original_size_sol * size_fraction
                    sol_price = await self._get_sol_price()
                    reentry_size_usd = reentry_size_sol * sol_price

                    # Cap re-entry position to limit max loss
                    max_reentry_usd = getattr(meme_config, 'REENTRY_MAX_POSITION_USD', 20.0)
                    if reentry_size_usd > max_reentry_usd:
                        reentry_size_usd = max_reentry_usd
                        reentry_size_sol = reentry_size_usd / sol_price if sol_price > 0 else 0

                    console.print(Panel(
                        f"RE-ENTRY: {candidate.symbol}\n"
                        f"Recovery: {recovery*100:+.1f}% from exit\n"
                        f"Size: {reentry_size_sol:.2f} SOL (${reentry_size_usd:.2f})\n"
                        f"Current: ${current_price:.10f}\n"
                        f"Exit was: ${candidate.exit_price:.10f}",
                        title="RE-ENTRY SIGNAL",
                        style="cyan"
                    ))

                    if self.paper_mode:
                        # Paper re-entry
                        amount_tokens = reentry_size_usd / current_price if current_price > 0 else 0

                        position = ActivePosition(
                            mint=mint,
                            symbol=candidate.symbol,
                            entry_price=current_price,
                            entry_time=now,
                            amount_tokens=amount_tokens,
                            amount_sol=reentry_size_sol,
                            amount_usd=reentry_size_usd,
                            current_price=current_price,
                        )

                        # Create state with wider stop for re-entry (more room to breathe)
                        position.state = PositionState(
                            mint=mint,
                            symbol=candidate.symbol,
                            entry_price=current_price,
                            entry_time=now,
                            amount_tokens=amount_tokens,
                            amount_usd=reentry_size_usd,
                            score=candidate.original_score,
                            initial_stop_pct=-0.20,  # Wider stop for re-entry
                        )

                        self.active_positions[mint] = position

                        # Update re-entry attempts
                        candidate.reentry_attempts += 1
                        del self.reentry_watch[mint]

                        console.print(f"[cyan]PAPER RE-ENTRY: {candidate.symbol} @ ${current_price:.10f}[/cyan]")

            except Exception as e:
                if meme_config.VERBOSE_LOGGING:
                    console.print(f"[yellow]Error checking re-entry for {mint[:8]}: {e}[/yellow]")

    async def _check_holder_concentration(self, mint: str) -> tuple[bool, str]:
        """Check if token has dangerous holder concentration via Solana RPC.

        Uses getTokenLargestAccounts to find top holders.

        Args:
            mint: Token mint address

        Returns:
            (True, "") if OK, (False, reason) if concentrated
        """
        if not meme_config.HOLDER_CHECK_ENABLED:
            return True, ""

        rpc_url = os.getenv('HELIUS_URL') or os.getenv('RPC_ENDPOINT', '')
        if not rpc_url:
            return True, ""  # No RPC configured, allow through

        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTokenLargestAccounts",
                    "params": [mint],
                }
                resp = await client.post(rpc_url, json=payload)
                if resp.status_code != 200:
                    return True, ""  # RPC error, allow through

                data = resp.json()
                result = data.get("result", {})
                accounts = result.get("value", [])
                if not accounts:
                    return True, ""  # No data, allow through

                # Calculate total supply from all returned accounts
                # getTokenLargestAccounts returns up to 20 largest holders
                amounts = []
                for acc in accounts:
                    amount_str = acc.get("amount", "0")
                    amounts.append(int(amount_str))

                total = sum(amounts)
                if total == 0:
                    return True, ""

                # Check single wallet concentration
                max_single = amounts[0] / total  # Accounts are sorted largest first
                if max_single > meme_config.HOLDER_MAX_SINGLE_WALLET:
                    return False, f"single wallet holds {max_single*100:.1f}% (max: {meme_config.HOLDER_MAX_SINGLE_WALLET*100:.0f}%)"

                # Check top 5 concentration
                top5_sum = sum(amounts[:5])
                top5_pct = top5_sum / total
                if top5_pct > meme_config.HOLDER_MAX_TOP5_CONCENTRATION:
                    return False, f"top 5 hold {top5_pct*100:.1f}% (max: {meme_config.HOLDER_MAX_TOP5_CONCENTRATION*100:.0f}%)"

                return True, ""

        except Exception as e:
            if meme_config.VERBOSE_LOGGING:
                console.print(f"[yellow]Holder check error for {mint[:8]}: {e}[/yellow]")
            return True, ""  # Network error, allow through

    async def check_pending_entries(self):
        """Check pending entries that have waited the confirmation delay.

        For each pending entry where CONFIRMATION_DELAY_SECONDS has elapsed:
        1. Re-fetch price: reject if dropped > CONFIRMATION_MAX_PRICE_DROP %
        2. Check 5m sell pressure: reject if sells > 2x buys
        3. Check holder concentration: reject if too concentrated
        4. All pass: execute_entry()

        Stale entries (>120s) are removed automatically.
        """
        if not self.pending_entries:
            return

        now = time.time()
        to_remove = []

        for mint, pending in list(self.pending_entries.items()):
            elapsed = now - pending.signal_time

            # Remove stale entries (>120s without confirmation)
            if elapsed > 120:
                console.print(f"[yellow]EXPIRED: {pending.symbol} pending entry timed out ({elapsed:.0f}s)[/yellow]")
                to_remove.append(mint)
                continue

            # Not ready yet
            if elapsed < meme_config.CONFIRMATION_DELAY_SECONDS:
                continue

            # Skip if already in position (could have entered via re-entry)
            if mint in self.active_positions:
                to_remove.append(mint)
                continue

            # Skip if max positions reached
            if len(self.active_positions) >= meme_config.MAX_POSITIONS:
                to_remove.append(mint)
                continue

            # --- Confirmation checks ---
            rejected = False

            # Check 1: Price still holding?
            price_data = await self._get_price_and_momentum(mint)
            if not price_data or not price_data.get('price'):
                console.print(f"[yellow]REJECTED: {pending.symbol} - no price data after wait[/yellow]")
                to_remove.append(mint)
                continue

            current_price = price_data['price']
            price_change_pct = ((current_price - pending.signal_price) / pending.signal_price) * 100 if pending.signal_price > 0 else 0

            if price_change_pct < meme_config.CONFIRMATION_MAX_PRICE_DROP:
                console.print(f"[red]REJECTED: {pending.symbol} - price dropped {price_change_pct:+.1f}% during wait (signal: ${pending.signal_price:.10f} -> now: ${current_price:.10f})[/red]")
                to_remove.append(mint)
                rejected = True

            # Check 2: 5m sell pressure
            if not rejected:
                buys_5m = price_data.get('buys_5m', 0)
                sells_5m = price_data.get('sells_5m', 0)
                if buys_5m > 0 and sells_5m > buys_5m * meme_config.ANTI_RUG_SELL_RATIO:
                    console.print(f"[red]REJECTED: {pending.symbol} - sell pressure during wait (buys: {buys_5m}, sells: {sells_5m})[/red]")
                    to_remove.append(mint)
                    rejected = True

            # Check 3: Holder concentration
            if not rejected:
                holder_ok, holder_reason = await self._check_holder_concentration(mint)
                if not holder_ok:
                    console.print(f"[red]REJECTED: {pending.symbol} - holder concentration: {holder_reason}[/red]")
                    to_remove.append(mint)
                    rejected = True

            # All checks passed - execute entry
            if not rejected:
                # Update candidate price to current for accurate entry
                pending.candidate.price = current_price
                console.print(f"[green bold]CONFIRMED: {pending.symbol} after {elapsed:.0f}s wait (price {price_change_pct:+.1f}%)[/green bold]")
                await self.execute_entry(pending.candidate)
                to_remove.append(mint)

        # Clean up processed entries
        for mint in to_remove:
            self.pending_entries.pop(mint, None)

    async def monitor_positions(self):
        """Monitor all active positions for exit conditions."""
        # Check daily loss limit first
        if meme_config.DAILY_LOSS_ENABLED and self.session_pnl <= meme_config.DAILY_LOSS_LIMIT:
            if not getattr(self, '_daily_limit_hit', False):
                self._daily_limit_hit = True
                console.print(f"[red bold]DAILY LOSS LIMIT HIT: ${self.session_pnl:.2f} (limit: ${meme_config.DAILY_LOSS_LIMIT})[/red bold]")
                console.print("[yellow]Closing all positions and stopping new entries...[/yellow]")
                # Close all positions
                for mint, position in list(self.active_positions.items()):
                    limit_exit = ExitResult(
                        should_exit=True,
                        reason='DAILY_LIMIT',
                        sell_fraction=1.0,
                        details={'session_pnl': self.session_pnl}
                    )
                    await self.execute_exit(position, limit_exit)
            return

        for mint, position in list(self.active_positions.items()):
            try:
                # Get current price and momentum from DexScreener
                price_data = await self._get_price_and_momentum(mint)
                if not price_data:
                    continue

                price = price_data.get('price', 0)
                price_change_5m = price_data.get('price_change_5m', 0)
                buys_5m = price_data.get('buys_5m', 0)
                sells_5m = price_data.get('sells_5m', 0)

                if price and price > 0:
                    position.current_price = price
                else:
                    continue

                # Update P&L
                if position.entry_price > 0:
                    position.unrealized_pnl_pct = (
                        (position.current_price - position.entry_price) / position.entry_price * 100
                    )
                    position.unrealized_pnl_usd = (
                        position.amount_tokens * position.current_price - position.amount_usd
                    )

                pnl_fraction = position.unrealized_pnl_pct / 100

                # 1. Quick Scalp Check - if +15% in first 5 minutes, take partial profit
                if meme_config.QUICK_SCALP_ENABLED:
                    time_held = time.time() - position.entry_time
                    if time_held <= meme_config.QUICK_SCALP_WINDOW_SECONDS:
                        if pnl_fraction >= meme_config.QUICK_SCALP_GAIN and not getattr(position, '_scalped', False):
                            position._scalped = True
                            scalp_exit = ExitResult(
                                should_exit=True,
                                reason='QUICK_SCALP',
                                sell_fraction=meme_config.QUICK_SCALP_SELL_FRACTION,
                                details={
                                    'pnl_pct': position.unrealized_pnl_pct,
                                    'time_held_sec': time_held,
                                }
                            )
                            console.print(f"[green bold]QUICK SCALP: {position.symbol} +{position.unrealized_pnl_pct:.1f}% in {time_held:.0f}s![/green bold]")
                            await self.execute_exit(position, scalp_exit)
                            continue

                # 2. Anti-Rug Check - if sells >> buys in 5min, exit
                if meme_config.ANTI_RUG_ENABLED and buys_5m > 0:
                    sell_buy_ratio = sells_5m / buys_5m if buys_5m > 0 else sells_5m
                    if sell_buy_ratio >= meme_config.ANTI_RUG_SELL_RATIO:
                        rug_exit = ExitResult(
                            should_exit=True,
                            reason='ANTI_RUG',
                            sell_fraction=1.0,
                            details={
                                'sells_5m': sells_5m,
                                'buys_5m': buys_5m,
                                'ratio': sell_buy_ratio,
                            }
                        )
                        console.print(f"[red bold]ANTI-RUG: {position.symbol} sells({sells_5m}) >> buys({buys_5m})![/red bold]")
                        await self.execute_exit(position, rug_exit)
                        continue

                # 3. Momentum dump exit
                if price_change_5m <= meme_config.MOMENTUM_DUMP_THRESHOLD:
                    dump_exit = ExitResult(
                        should_exit=True,
                        reason='MOMENTUM_DUMP',
                        sell_fraction=1.0,
                        details={
                            'price_change_5m': price_change_5m,
                            'threshold': meme_config.MOMENTUM_DUMP_THRESHOLD,
                            'pnl_pct': position.unrealized_pnl_pct,
                        }
                    )
                    console.print(f"[red]MOMENTUM DUMP: {position.symbol} dropped {price_change_5m:.1f}% in 5m![/red]")
                    await self.execute_exit(position, dump_exit)
                    continue

                # 4. Regular exit conditions (TP, SL, Time)
                if position.state:
                    exit_result = self.exit_manager.check_exit(position.state, position.current_price)
                    if exit_result.should_exit:
                        await self.execute_exit(position, exit_result)

            except Exception as e:
                console.print(f"[red]Error monitoring {mint[:8]}: {e}[/red]")

    async def _get_current_price(self, mint: str) -> Optional[float]:
        """Get current price for a token from DexScreener.

        Args:
            mint: Token mint address

        Returns:
            Current price in USD, or None if unavailable
        """
        data = await self._get_price_and_momentum(mint)
        return data.get('price') if data else None

    async def _get_price_and_momentum(self, mint: str) -> Optional[dict]:
        """Get current price and momentum data from DexScreener.

        Args:
            mint: Token mint address

        Returns:
            Dict with price and momentum data, or None if unavailable
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                url = f"{DEXSCREENER_TOKEN}/{mint}"
                resp = await client.get(url)
                if resp.status_code != 200:
                    return None

                data = resp.json()
                pairs = data.get('pairs', [])
                if not pairs:
                    return None

                # Use highest liquidity pair
                best_pair = max(pairs, key=lambda p: float(p.get('liquidity', {}).get('usd', 0) or 0))

                price = best_pair.get('priceUsd')
                price_change = best_pair.get('priceChange', {})
                txns = best_pair.get('txns', {})
                m5_txns = txns.get('m5', {})

                return {
                    'price': float(price) if price else 0,
                    'price_change_5m': float(price_change.get('m5', 0) or 0),
                    'price_change_1h': float(price_change.get('h1', 0) or 0),
                    'buys_5m': int(m5_txns.get('buys', 0) or 0),
                    'sells_5m': int(m5_txns.get('sells', 0) or 0),
                }

        except Exception:
            return None

    async def _get_sol_price(self) -> float:
        """Get current SOL price in USD from DexScreener.

        Caches the price for 60 seconds to avoid excessive API calls.

        Returns:
            SOL price in USD
        """
        now = time.time()
        # Return cached price if fresh (less than 60 seconds old)
        if now - self._sol_price_updated < 60:
            return self._sol_price

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # SOL/USDC pair on Raydium
                url = f"{DEXSCREENER_TOKEN}/{WSOL_MINT}"
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    pairs = data.get('pairs', [])
                    if pairs:
                        # Find SOL/USDC pair
                        for pair in pairs:
                            quote = pair.get('quoteToken', {})
                            if quote.get('symbol') in ('USDC', 'USDT'):
                                price = pair.get('priceUsd')
                                if price:
                                    self._sol_price = float(price)
                                    self._sol_price_updated = now
                                    return self._sol_price
        except Exception:
            pass

        return self._sol_price  # Return cached/default

    def display_status(self):
        """Display current bot status."""
        table = Table(title="Active Positions")
        table.add_column("Symbol", style="cyan")
        table.add_column("Entry", justify="right")
        table.add_column("Current", justify="right")
        table.add_column("P&L %", justify="right")
        table.add_column("P&L $", justify="right")
        table.add_column("Stop", justify="right")

        for mint, pos in self.active_positions.items():
            pnl_style = "green" if pos.unrealized_pnl_pct >= 0 else "red"
            stop_price = self.exit_manager.get_current_stop_price(pos.state, pos.current_price) if pos.state else 0

            table.add_row(
                pos.symbol,
                f"${pos.entry_price:.8f}",
                f"${pos.current_price:.8f}",
                f"[{pnl_style}]{pos.unrealized_pnl_pct:+.1f}%[/]",
                f"[{pnl_style}]${pos.unrealized_pnl_usd:+.2f}[/]",
                f"${stop_price:.8f}",
            )

        console.print(table)

        # Show pending entries
        if self.pending_entries:
            now = time.time()
            pending_parts = []
            for mint, pe in self.pending_entries.items():
                wait = now - pe.signal_time
                remaining = max(0, meme_config.CONFIRMATION_DELAY_SECONDS - wait)
                pending_parts.append(f"{pe.symbol} ({remaining:.0f}s)")
            console.print(f"[cyan]Pending: {', '.join(pending_parts)}[/cyan]")

        console.print(f"\nSession: {self.session_wins}W/{self.session_losses}L | P&L: ${self.session_pnl:+.2f}")

    async def main_loop(self):
        """Main bot loop."""
        self.running = True
        console.print(Panel(
            f"Starting Meme Bot\n"
            f"Mode: {'PAPER' if self.paper_mode else 'LIVE'}\n"
            f"Poll Interval: {meme_config.POLL_INTERVAL_SECONDS}s",
            title="Bot Started",
            style="green"
        ))

        # Send startup alert
        if HAS_ALERTS and meme_config.DISCORD_ALERTS_ENABLED:
            send_system_alert(
                'Meme Bot Started',
                f"Mode: {'PAPER' if self.paper_mode else 'LIVE'} | Max Positions: {meme_config.MAX_POSITIONS}",
                level='success'
            )

        while self.running:
            try:
                # 1. Discover new tokens
                candidates = await self.discover_tokens()

                # 2. Process each candidate
                for candidate in candidates:
                    # Filter
                    if not await self.filter_token(candidate):
                        continue

                    # Score
                    score = await self.score_token(candidate)

                    # Entry decision
                    if await self.should_enter(candidate):
                        if meme_config.CONFIRMATION_ENABLED:
                            # Queue for confirmation instead of immediate entry
                            if candidate.mint not in self.pending_entries:
                                self.pending_entries[candidate.mint] = PendingEntry(
                                    mint=candidate.mint,
                                    symbol=candidate.symbol,
                                    candidate=candidate,
                                    signal_time=time.time(),
                                    signal_price=candidate.price,
                                )
                                console.print(f"[cyan]PENDING: {candidate.symbol} queued for {meme_config.CONFIRMATION_DELAY_SECONDS}s confirmation (price: ${candidate.price:.10f})[/cyan]")
                        else:
                            await self.execute_entry(candidate)

                # 3. Check pending entries for confirmation
                await self.check_pending_entries()

                # 4. Monitor existing positions
                await self.monitor_positions()

                # 5. Check for re-entry opportunities on recovered tokens
                await self.check_reentry_opportunities()

                # 6. Display status
                if self.active_positions or self.pending_entries:
                    self.display_status()

                # 7. Sleep until next poll
                await asyncio.sleep(meme_config.POLL_INTERVAL_SECONDS)

            except KeyboardInterrupt:
                console.print("\n[yellow]Shutting down...[/yellow]")
                self.running = False
            except Exception as e:
                console.print(f"[red]Main loop error: {e}[/red]")
                await asyncio.sleep(5)

        console.print("[yellow]Meme Bot stopped[/yellow]")


async def main():
    """Entry point."""
    paper_mode = meme_config.MEME_PAPER_MODE
    bot = MemeCoinBot(paper_mode=paper_mode)
    await bot.main_loop()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted[/yellow]")
