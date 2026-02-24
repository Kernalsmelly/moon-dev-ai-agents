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
import math
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
import requests
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from urllib.parse import urlsplit

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Ensure `.env` is loaded even when the bot is launched without `source .env`.
# Do not override process-level env so lane-specific overrides (A/B runs, tests)
# remain effective.
load_dotenv(dotenv_path=os.path.join(PROJECT_ROOT, ".env"), override=False)

import src.config as config
import src.meme_config as meme_config
from src.meme_signal_schema import normalize_signal_metrics

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
from src import trade_executor as te
from src.solana.rpc_pool import RpcError, RpcPool
from src.solana.spl_mint import fetch_spl_mint_info

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
    from src.nice_funcs import token_overview, token_price, is_momentum_reject, token_security_raw
    HAS_NICE_FUNCS = True
except (ImportError, ValueError):
    # nice_funcs may raise ValueError if BIRDEYE_API_KEY not set
    # We don't need it - we use DexScreener instead
    HAS_NICE_FUNCS = False
    token_overview = None
    token_price = None
    is_momentum_reject = None
    token_security_raw = None

try:
    from src.trade_executor import main as execute_swap
    HAS_TRADE_EXECUTOR = True
except ImportError:
    HAS_TRADE_EXECUTOR = False

try:
    from src.meme_entry_executor import execute_entry_jito
    HAS_JITO_ENTRY = True
except ImportError:
    HAS_JITO_ENTRY = False

try:
    from src.meme_live_safeguards import LiveSafeguards, get_safeguards
    HAS_SAFEGUARDS = True
except ImportError:
    HAS_SAFEGUARDS = False

console = Console()

# Token mints
SOL_MINT = 'So11111111111111111111111111111111111111112'
# Jupiter (and most tooling) uses the wrapped SOL mint (…11112). Some codebases
# use …11111 as a sentinel, but it is not tradable on Jupiter.
WSOL_MINT = 'So11111111111111111111111111111111111111112'

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
    liquidity_estimated: bool = False
    market_cap: float = 0.0
    mcap_scout_mode: bool = False
    price: float = 0.0
    price_impact_pct: float = 0.0
    is_lp_burned: bool = False
    top_holder_concentration: float = 1.0
    vhi_score: int = 0
    graduation_score: float = 0.0
    composite_score: int = 0
    winner_score: float = 0.0
    winner_features_used: int = 0
    winner_zone_id: str = ""
    winner_zone_objective: float = 0.0
    winner_zone_bypassed: bool = False
    winner_zone_bypass_reason: str = ""
    # Momentum data
    price_change_5m: float = 0.0
    price_change_1h: float = 0.0
    buys_1h: int = 0
    sells_1h: int = 0
    txns_1h: int = 0
    buys_5m: int = 0
    sells_5m: int = 0
    txns_5m: int = 0
    volume_1h: float = 0.0
    volume_5m: float = 0.0


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
    # Optional scale-in (paper-first): enter small, then add size only if it moves in our favor.
    target_amount_sol: float = 0.0
    target_amount_usd: float = 0.0
    scale_in_enabled: bool = False
    scale_in_done: bool = True


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
    confirm_attempts: int = 0
    last_attempt_ts: float = 0.0


class MemeCoinBot:
    """Main meme coin trading bot."""

    def __init__(self, paper_mode: bool = True):
        """Initialize the meme coin bot.

        Args:
            paper_mode: If True, simulate trades without executing
        """
        self.paper_mode = paper_mode or meme_config.MEME_PAPER_MODE
        # Hard safety gate: never allow live trading unless explicitly enabled.
        if not self.paper_mode:
            live_ok = os.getenv('MEME_LIVE_ENABLED', 'false').lower() in ('1', 'true', 'yes')
            if not live_ok:
                self.paper_mode = True
                console.print(Panel(
                    "Live trading is disabled (set MEME_LIVE_ENABLED=true to enable). "
                    "Falling back to PAPER mode.",
                    title="Safety Gate",
                    style="yellow"
                ))
        self.running = False
        # Run identifier for experiment tracking (persisted into trade metadata).
        # If MEME_RUN_ID is not set, generate a new one per process start.
        self.run_id = os.getenv("MEME_RUN_ID", "").strip() or f"run_{int(time.time())}"

        # Lightweight signal-first debug logger (JSONL) to avoid guessing why we aren't entering.
        self.signal_debug = os.getenv("MEME_SIGNAL_DEBUG", "false").lower() in ("1", "true", "yes")
        self.signal_debug_max_per_min = int(os.getenv("MEME_SIGNAL_DEBUG_MAX_PER_MIN", "30") or 30)
        self._signal_debug_window_start = time.time()
        self._signal_debug_in_window = 0

        # DexScreener-mode filter attribution without log spam.
        self.filter_debug = os.getenv("MEME_FILTER_DEBUG", "true").strip().lower() in ("1", "true", "yes")
        try:
            self.filter_debug_interval_s = float(os.getenv("MEME_FILTER_DEBUG_INTERVAL_S", "60") or 60.0)
        except Exception:
            self.filter_debug_interval_s = 60.0
        self._filter_reject_counts: dict[str, int] = {}
        self._filter_reject_last_report: float = 0.0

        # Optional: load runtime config overrides from JSON
        cfg_path = os.getenv("MEME_CONFIG_FILE", "").strip()
        if cfg_path:
            try:
                with open(cfg_path, "r", encoding="utf-8") as fh:
                    cfg_obj = json.load(fh)
                params = cfg_obj.get("parameters", cfg_obj)
                if isinstance(params, dict):
                    for k, v in params.items():
                        setattr(meme_config, k, v)
                    console.print(Panel(
                        f"Loaded meme config overrides from {cfg_path}",
                        title="Config Override",
                        style="cyan"
                    ))
            except Exception as e:
                console.print(Panel(f"Failed to load MEME_CONFIG_FILE: {e}", style="yellow"))

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
        self.loss_streak: int = 0
        self.cooldown_until: float = 0.0
        # Regime guard: pause new entries when short-horizon expectancy degrades.
        self.regime_guard_enabled = os.getenv("MEME_REGIME_GUARD_ENABLED", "true").lower() in ("1", "true", "yes")
        self.regime_block_until: float = 0.0
        self.regime_eval_interval_s = float(os.getenv("MEME_REGIME_EVAL_INTERVAL_S", "30") or 30)
        # Optional pause escalation: repeated unhealthy windows increase pause duration.
        self.regime_escalation_enabled = os.getenv("MEME_REGIME_ESCALATION_ENABLED", "true").lower() in ("1", "true", "yes")
        self.regime_escalation_step_s = float(os.getenv("MEME_REGIME_ESCALATION_STEP_SECONDS", "120") or 120)
        self.regime_escalation_max_mult = float(os.getenv("MEME_REGIME_ESCALATION_MAX_MULT", "4.0") or 4.0)
        self.regime_escalation_reset_minutes = float(os.getenv("MEME_REGIME_ESCALATION_RESET_MINUTES", "60") or 60)
        self.regime_escalation_reset_healthy_cycles = int(
            os.getenv("MEME_REGIME_ESCALATION_RESET_HEALTHY_CYCLES", "3") or 3
        )
        self.regime_scope_run_id = os.getenv("MEME_REGIME_SCOPE_RUN_ID", "true").lower() in ("1", "true", "yes")
        self.regime_cluster_brake_enabled = os.getenv("MEME_REGIME_CLUSTER_BRAKE_ENABLED", "true").lower() in (
            "1",
            "true",
            "yes",
        )
        self.regime_cluster_min_trades = int(os.getenv("MEME_REGIME_CLUSTER_MIN_TRADES", "12") or 12)
        self.regime_cluster_min_clusters = int(os.getenv("MEME_REGIME_CLUSTER_MIN_CLUSTERS", "4") or 4)
        self.regime_cluster_entry_tolerance_s = int(
            os.getenv("MEME_REGIME_CLUSTER_ENTRY_TOLERANCE_SEC", "180") or 180
        )
        self.regime_cluster_gap_fallback_s = int(os.getenv("MEME_REGIME_CLUSTER_GAP_FALLBACK_SEC", "900") or 900)
        self.regime_max_loss_cluster_share = float(
            os.getenv("MEME_REGIME_MAX_LOSS_CLUSTER_SHARE", "0.65") or 0.65
        )
        self.regime_max_dominant_cluster_leg_share = float(
            os.getenv("MEME_REGIME_MAX_DOMINANT_CLUSTER_LEG_SHARE", "0.60") or 0.60
        )
        self._regime_block_streak: int = 0
        self._regime_last_block_ts: float = 0.0
        self._regime_healthy_streak: int = 0
        self._regime_last_eval_ts: float = 0.0
        self._regime_last_log_ts: float = 0.0
        self._regime_snapshot: dict[str, float | int | bool | str] = {
            "allow": True,
            "n": 0,
            "wins": 0,
            "wr": 0.0,
            "avg_pnl": 0.0,
            "sum_pnl": 0.0,
            "cluster_count": 0,
            "loss_cluster_share": 0.0,
            "dominant_cluster_leg_share": 0.0,
            "reasons": "",
            "streak": 0,
            "pause_s": 0.0,
        }
        # Winner-first profile/ranking gate (reverse-engineered from historical winners).
        self.winner_profile_enabled = os.getenv("MEME_WINNER_PROFILE_ENABLED", "true").lower() in ("1", "true", "yes")
        self.winner_profile_path = os.getenv("MEME_WINNER_PROFILE_PATH", "data/meme_winner_profile.json")
        self.winner_profile_reload_s = float(os.getenv("MEME_WINNER_PROFILE_RELOAD_S", "120") or 120)
        self.winner_min_score_env_set = bool(str(os.getenv("MEME_WINNER_MIN_SCORE", "")).strip())
        self.winner_min_score = float(os.getenv("MEME_WINNER_MIN_SCORE", "58") or 58)
        self.winner_score_weight = float(os.getenv("MEME_WINNER_SCORE_WEIGHT", "20") or 20)
        self.winner_min_features = int(os.getenv("MEME_WINNER_MIN_FEATURES", "3") or 3)
        self.winner_require_min_features = os.getenv("MEME_WINNER_REQUIRE_MIN_FEATURES", "false").lower() in ("1", "true", "yes")
        # Winner-first execution controls: prioritize and size stronger winner profiles.
        self.winner_prioritize_enabled = os.getenv("MEME_WINNER_PRIORITIZE", "true").lower() in ("1", "true", "yes")
        self.winner_top_k_per_tick = int(os.getenv("MEME_WINNER_TOP_K_PER_TICK", "3") or 3)
        self.winner_size_enabled = os.getenv("MEME_WINNER_SIZE_ENABLED", "true").lower() in ("1", "true", "yes")
        self.winner_size_min_mult = float(os.getenv("MEME_WINNER_SIZE_MIN_MULT", "0.70") or 0.70)
        self.winner_size_max_mult = float(os.getenv("MEME_WINNER_SIZE_MAX_MULT", "1.25") or 1.25)
        self.winner_size_score_center = float(os.getenv("MEME_WINNER_SIZE_SCORE_CENTER", "60") or 60)
        self.winner_size_score_span = float(os.getenv("MEME_WINNER_SIZE_SCORE_SPAN", "50") or 50)
        # Winner-aware fail-fast behavior.
        self.winner_failfast_relax_enabled = os.getenv("MEME_WINNER_FAILFAST_RELAX_ENABLED", "true").lower() in ("1", "true", "yes")
        self.winner_failfast_relax_score = float(os.getenv("MEME_WINNER_FAILFAST_RELAX_SCORE", "68") or 68)
        self.winner_failfast_relax_trigger_pct = float(os.getenv("MEME_WINNER_FAILFAST_RELAX_TRIGGER_PCT", "-1.20") or -1.20)
        self.winner_failfast_relax_extra_hold_s = float(os.getenv("MEME_WINNER_FAILFAST_RELAX_EXTRA_HOLD_S", "30") or 30)
        self.winner_failfast_tighten_score = float(os.getenv("MEME_WINNER_FAILFAST_TIGHTEN_SCORE", "45") or 45)
        self.winner_failfast_tighten_trigger_pct = float(os.getenv("MEME_WINNER_FAILFAST_TIGHTEN_TRIGGER_PCT", "-0.30") or -0.30)
        self._winner_profile: dict | None = None
        self._winner_profile_mtime: float = 0.0
        self._winner_profile_last_reload: float = 0.0
        self._winner_profile_loaded_once: bool = False
        # Winner-zone gate: learned allowlist from prior outcomes (score/net/top-share/mcap bins).
        self.winner_zone_enabled = os.getenv("MEME_WINNER_ZONE_ENABLED", "false").lower() in ("1", "true", "yes")
        self.winner_zone_enforce = os.getenv("MEME_WINNER_ZONE_ENFORCE", "true").lower() in ("1", "true", "yes")
        self.winner_zone_block_when_missing = os.getenv("MEME_WINNER_ZONE_BLOCK_WHEN_MISSING", "false").lower() in (
            "1",
            "true",
            "yes",
        )
        self.winner_zone_path = os.getenv("MEME_WINNER_ZONE_PATH", "data/meme_winner_zones.json")
        self.winner_zone_reload_s = float(os.getenv("MEME_WINNER_ZONE_RELOAD_S", "120") or 120)
        self.winner_zone_min_n = int(os.getenv("MEME_WINNER_ZONE_MIN_N", "0") or 0)
        self.winner_zone_require_top_share = os.getenv("MEME_WINNER_ZONE_REQUIRE_TOP_SHARE", "false").lower() in (
            "1",
            "true",
            "yes",
        )
        self.winner_zone_match_allow_unknown_mcap = os.getenv(
            "MEME_WINNER_ZONE_MATCH_ALLOW_UNKNOWN_MCAP", "false"
        ).lower() in ("1", "true", "yes")
        # Controlled bypass when no zone matches. Keeps lane alive in sparse/novel conditions
        # while still requiring stronger-than-normal prequote demand.
        self.winner_zone_bypass_enabled = os.getenv("MEME_WINNER_ZONE_BYPASS_ENABLED", "false").lower() in (
            "1",
            "true",
            "yes",
        )
        # Test mode: disable direct zone matches and allow entries only via bypass logic.
        self.winner_zone_force_bypass_only = os.getenv(
            "MEME_WINNER_ZONE_FORCE_BYPASS_ONLY", "false"
        ).lower() in ("1", "true", "yes")
        self.winner_zone_bypass_min_signal_score = float(
            os.getenv("MEME_WINNER_ZONE_BYPASS_MIN_SIGNAL_SCORE", "78") or 78
        )
        self.winner_zone_bypass_min_hits = int(os.getenv("MEME_WINNER_ZONE_BYPASS_MIN_HITS", "6") or 6)
        self.winner_zone_bypass_min_unique_buyers = int(
            os.getenv("MEME_WINNER_ZONE_BYPASS_MIN_UNIQUE_BUYERS", "4") or 4
        )
        self.winner_zone_bypass_min_net_sol_in = float(
            os.getenv("MEME_WINNER_ZONE_BYPASS_MIN_NET_SOL_IN", "2.5") or 2.5
        )
        self.winner_zone_bypass_min_mcap_usd = float(
            os.getenv("MEME_WINNER_ZONE_BYPASS_MIN_MCAP_USD", "12000") or 12000
        )
        self.winner_zone_bypass_allow_unknown_mcap = os.getenv(
            "MEME_WINNER_ZONE_BYPASS_ALLOW_UNKNOWN_MCAP", "true"
        ).lower() in ("1", "true", "yes")
        self.winner_zone_bypass_max_top_buyer_share = float(
            os.getenv("MEME_WINNER_ZONE_BYPASS_MAX_TOP_BUYER_SHARE", "0.40") or 0.40
        )
        self._winner_zones: list[dict[str, Any]] = []
        self._winner_zone_mtime: float = 0.0
        self._winner_zone_last_reload: float = 0.0
        self._winner_zone_loaded_once: bool = False
        self.prev_candidate_state: dict[str, dict] = {}
        self.launch_mints_file = os.getenv("MEME_LAUNCH_MINTS_FILE", "").strip()
        self._launch_offset = 0
        self.launch_signals_file = os.getenv("MEME_LAUNCH_SIGNALS_FILE", "").strip()
        self.signal_debug_file = os.getenv("MEME_SIGNAL_DEBUG_FILE", "data/meme_signal_debug.jsonl").strip()
        self._signal_offset = 0
        self.launch_signal_ttl = int(os.getenv("MEME_LAUNCH_SIGNAL_TTL", "600"))
        self.launch_signal_ignore_history = os.getenv("MEME_LAUNCH_SIGNAL_IGNORE_HISTORY", "true").lower() in ("1", "true", "yes")
        self.launch_signal_cooldown = int(os.getenv("MEME_LAUNCH_SIGNAL_COOLDOWN", "900"))
        self.launch_signal_mints: dict[str, float] = {}
        self.launch_signal_scores: dict[str, float] = {}
        self.launch_signal_metrics: dict[str, dict] = {}
        # Keep first-seen launch time per mint (stable age anchor).
        # `launch_signal_mints` is updated on every new hit and should be treated as last-seen.
        self.launch_signal_first_seen: dict[str, float] = {}
        self.launch_signal_seen: set[str] = set()
        self.launch_signal_last_used: dict[str, float] = {}
        self.signal_reject_cooldown_s = float(os.getenv("MEME_SIGNAL_REJECT_COOLDOWN_S", "120") or 120)
        reject_reasons_raw = os.getenv(
            "MEME_SIGNAL_REJECT_COOLDOWN_REASONS",
            (
                "mcap_high,"
                "prequote_score,prequote_net,prequote_hits,prequote_buys,prequote_uniq,"
                "prequote_top_share,prequote_bs_ratio,prequote_mcap_low,"
                "core_metrics,liq_missing_signal,liq_low_signal,"
                "winner_zone,winner_zone_missing,age,mcap_low"
            ),
        )
        self.signal_reject_cooldown_reasons = {
            str(x).strip() for x in str(reject_reasons_raw or "").split(",") if str(x).strip()
        }
        self._last_signal_log: float = 0.0
        # If enabled, only consider tokens present in launch signals, instead of mass discovery.
        env_sf = os.getenv("MEME_SIGNAL_FIRST", "").strip().lower()
        self.signal_first = (env_sf in ("1", "true", "yes")) if env_sf else bool(self.launch_signals_file)
        self.signal_eval_cooldown = float(os.getenv("MEME_SIGNAL_EVAL_COOLDOWN_SECONDS", "20"))
        self._signal_last_attempt: dict[str, float] = {}
        # Fairness cursor for signal-first candidate scheduling.
        # Prevents starvation from strict newest-first ordering when max candidates per tick is capped.
        self._signal_rr_cursor: int = 0
        self.signal_quote_fail_cooldown_s = float(os.getenv("MEME_SIGNAL_QUOTE_FAIL_COOLDOWN_S", "600") or 600)
        self._signal_quote_fail_until: dict[str, float] = {}
        self.entry_reject_cooldown_s = float(os.getenv("MEME_ENTRY_REJECT_COOLDOWN_S", "45") or 45)
        self.entry_reject_holder_cooldown_s = float(
            os.getenv("MEME_ENTRY_REJECT_HOLDER_COOLDOWN_S", "120") or 120
        )
        self.entry_reject_mint_freeze_cooldown_s = float(
            os.getenv("MEME_ENTRY_REJECT_MINT_FREEZE_COOLDOWN_S", "180") or 180
        )
        self.entry_reject_sellability_cooldown_s = float(
            os.getenv("MEME_ENTRY_REJECT_SELLABILITY_COOLDOWN_S", "90") or 90
        )
        self._entry_reject_until: dict[str, float] = {}
        self.signal_mcap_recheck_cooldown_s = float(os.getenv("MEME_SIGNAL_MCAP_RECHECK_COOLDOWN_S", "60") or 60)
        self.signal_mcap_recheck_backoff_enabled = os.getenv(
            "MEME_SIGNAL_MCAP_RECHECK_BACKOFF_ENABLED", "true"
        ).lower() in ("1", "true", "yes")
        self.signal_mcap_recheck_max_s = float(os.getenv("MEME_SIGNAL_MCAP_RECHECK_MAX_S", "300") or 300)
        self._signal_mcap_recheck_until: dict[str, float] = {}
        self._signal_mcap_recheck_counts: dict[str, int] = {}
        self.signal_mcap_confirm_seconds = float(os.getenv("MEME_SIGNAL_MCAP_CONFIRM_SECONDS", "0") or 0.0)
        self.signal_mcap_confirm_recheck_s = float(os.getenv("MEME_SIGNAL_MCAP_CONFIRM_RECHECK_S", "8") or 8.0)
        self._signal_mcap_above_since: dict[str, float] = {}
        self.signal_min_age_seconds = int(os.getenv("MEME_SIGNAL_MIN_AGE_SECONDS", "0") or 0)
        self.signal_max_age_seconds = int(os.getenv("MEME_SIGNAL_MAX_AGE_SECONDS", "900"))
        # Optional staged age checkpoints (seconds since first-seen), e.g. "60,300,600".
        # When configured, signal-first evaluations are only attempted around these windows.
        # This avoids immediate launch snipes and aligns entries with 1m/5m/10m checks.
        cp_raw = str(os.getenv("MEME_SIGNAL_AGE_CHECKPOINTS_S", "") or "").strip()
        cps: list[float] = []
        if cp_raw:
            for tok in cp_raw.split(","):
                tok = str(tok).strip()
                if not tok:
                    continue
                try:
                    v = float(tok)
                except Exception:
                    continue
                if v > 0:
                    cps.append(v)
        self.signal_age_checkpoints_s = sorted(set(cps))
        self.signal_age_checkpoint_grace_s = float(os.getenv("MEME_SIGNAL_AGE_CHECKPOINT_GRACE_S", "30") or 30)
        self._signal_age_checkpoint_idx: dict[str, int] = {}
        # Optional late-entry window: allow older mints only when demand remains strong.
        # 0 disables the late window entirely.
        self.signal_late_max_age_seconds = int(os.getenv("MEME_SIGNAL_LATE_MAX_AGE_SECONDS", "0") or 0)
        self.signal_late_min_unique_buyers = int(os.getenv("MEME_SIGNAL_LATE_MIN_UNIQUE_BUYERS", "0") or 0)
        self.signal_late_min_net_sol_in = float(os.getenv("MEME_SIGNAL_LATE_MIN_NET_SOL_IN", "0") or 0.0)
        self.signal_late_max_top_buyer_share = float(os.getenv("MEME_SIGNAL_LATE_MAX_TOP_BUYER_SHARE", "0") or 0.0)
        self.signal_late_min_signal_score = float(os.getenv("MEME_SIGNAL_LATE_MIN_SIGNAL_SCORE", "0") or 0.0)
        # Data-integrity gate: block entries unless core signal/tradability fields are present.
        # Keep liquidity requirements consistent across the prequote gate and core-metrics gate
        # so we do not silently reject candidates when liquidity is intentionally optional.
        self.signal_require_liquidity = os.getenv("MEME_SIGNAL_REQUIRE_LIQUIDITY", "true").lower() in (
            "1",
            "true",
            "yes",
        )
        self.signal_require_core_metrics = os.getenv("MEME_SIGNAL_REQUIRE_CORE_METRICS", "true").lower() in ("1", "true", "yes")
        self.signal_core_require_positive = os.getenv("MEME_SIGNAL_CORE_REQUIRE_POSITIVE", "true").lower() in ("1", "true", "yes")
        self.signal_core_require_liquidity = os.getenv(
            "MEME_SIGNAL_CORE_REQUIRE_LIQUIDITY",
            "true" if self.signal_require_liquidity else "false",
        ).lower() in ("1", "true", "yes")
        # In signal-first flow, market cap is often hydrated later via quote/data fetch.
        # Keep prequote core-metric checks strict on demand integrity, but allow mcap to be
        # validated in the dedicated market-cap gate after hydration.
        self.signal_core_require_mcap_prequote = os.getenv("MEME_SIGNAL_CORE_REQUIRE_MCAP_PREQUOTE", "false").lower() in ("1", "true", "yes")
        self.signal_core_min_hits = int(os.getenv("MEME_SIGNAL_CORE_MIN_HITS", "1") or 1)
        self.signal_core_min_unique_buyers = int(os.getenv("MEME_SIGNAL_CORE_MIN_UNIQUE_BUYERS", "1") or 1)
        self.signal_core_min_net_sol_in = float(os.getenv("MEME_SIGNAL_CORE_MIN_NET_SOL_IN", "0.0") or 0.0)
        # Liquidity fallback for sparse provider payloads: estimate tradability from
        # launch-signal demand metrics when explicit liquidity is temporarily unavailable.
        self.signal_liq_fallback_enabled = os.getenv("MEME_SIGNAL_LIQUIDITY_FALLBACK_ENABLED", "true").lower() in ("1", "true", "yes")
        self.signal_liq_fallback_min_hits = int(os.getenv("MEME_SIGNAL_LIQ_FALLBACK_MIN_HITS", "3") or 3)
        self.signal_liq_fallback_min_unique_buyers = int(
            os.getenv("MEME_SIGNAL_LIQ_FALLBACK_MIN_UNIQUE_BUYERS", "3") or 3
        )
        self.signal_liq_fallback_min_net_sol_in = float(os.getenv("MEME_SIGNAL_LIQ_FALLBACK_MIN_NET_SOL_IN", "1.25") or 1.25)
        self.signal_liq_fallback_usd_per_sol = float(os.getenv("MEME_SIGNAL_LIQ_FALLBACK_USD_PER_SOL", "10000") or 10000)
        self.signal_liq_fallback_buyer_bonus_usd = float(
            os.getenv("MEME_SIGNAL_LIQ_FALLBACK_BUYER_BONUS_USD", "1200") or 1200
        )
        self.signal_liq_fallback_buyer_bonus_cap_usd = float(
            os.getenv("MEME_SIGNAL_LIQ_FALLBACK_BUYER_BONUS_CAP_USD", "6000") or 6000
        )
        self.signal_liq_fallback_min_usd = float(os.getenv("MEME_SIGNAL_LIQ_FALLBACK_MIN_USD", "12000") or 12000)
        self.signal_liq_fallback_max_usd = float(os.getenv("MEME_SIGNAL_LIQ_FALLBACK_MAX_USD", "45000") or 45000)
        self.signal_estimated_liq_size_mult = float(os.getenv("MEME_SIGNAL_EST_LIQ_SIZE_MULT", "0.70") or 0.70)
        # Crowding gate: allow controlled relaxation when demand is very strong.
        self.signal_dynamic_crowd_gate = os.getenv("MEME_SIGNAL_DYNAMIC_CROWD_GATE", "true").lower() in ("1", "true", "yes")
        self.signal_crowd_relax_net_sol_in = float(os.getenv("MEME_SIGNAL_CROWD_RELAX_NET_SOL_IN", "5.0") or 5.0)
        self.signal_crowd_relax_net_sol_step = float(os.getenv("MEME_SIGNAL_CROWD_RELAX_NET_SOL_STEP", "2.0") or 2.0)
        self.signal_crowd_relax_buy_accel = float(os.getenv("MEME_SIGNAL_CROWD_RELAX_BUY_ACCEL", "0.12") or 0.12)
        self.signal_crowd_relax_top_share = float(os.getenv("MEME_SIGNAL_CROWD_RELAX_TOP_SHARE", "0.35") or 0.35)
        self.signal_crowd_relax_max_bonus = int(os.getenv("MEME_SIGNAL_CROWD_RELAX_MAX_BONUS", "3") or 3)
        # Market-cap split lane: strict lane for >= min mcap, scout lane for 10k-25k with stronger demand.
        self.signal_mcap_scout_enabled = os.getenv("MEME_SIGNAL_MCAP_SCOUT_ENABLED", "true").lower() in ("1", "true", "yes")
        self.signal_scout_min_mcap_usd = float(os.getenv("MEME_SIGNAL_SCOUT_MIN_MCAP_USD", "10000") or 10000)
        self.signal_scout_min_hits = int(os.getenv("MEME_SIGNAL_SCOUT_MIN_HITS", "4") or 4)
        self.signal_scout_min_unique_buyers = int(os.getenv("MEME_SIGNAL_SCOUT_MIN_UNIQUE_BUYERS", "4") or 4)
        self.signal_scout_min_net_sol_in = float(os.getenv("MEME_SIGNAL_SCOUT_MIN_NET_SOL_IN", "2.5") or 2.5)
        self.signal_scout_max_top_buyer_share = float(os.getenv("MEME_SIGNAL_SCOUT_MAX_TOP_BUYER_SHARE", "0.45") or 0.45)
        self.signal_scout_max_sell_buy_ratio = float(os.getenv("MEME_SIGNAL_SCOUT_MAX_SELL_BUY_RATIO", "1.2") or 1.2)
        self.signal_scout_min_signal_score = float(os.getenv("MEME_SIGNAL_SCOUT_MIN_SIGNAL_SCORE", "0.0") or 0.0)
        self.signal_scout_size_mult = float(os.getenv("MEME_SIGNAL_SCOUT_SIZE_MULT", "0.35") or 0.35)
        # Hybrid mode: use launch signals for discovery, but hydrate microstructure (liq/mcap/txns/vol)
        # via DexScreener before allowing entries. This avoids trading "unknown mcap/liquidity" mints.
        self.signal_hybrid_dex = os.getenv("MEME_SIGNAL_HYBRID_DEX", "true").lower() in ("1", "true", "yes")
        # In hybrid mode, preserve Jupiter-quote price (tradability + impact-aware) and use Dex only
        # for contextual metrics (liq/mcap/txns/volume). This keeps sizing/exits consistent with
        # the quote-based pipeline while still enforcing quality gates.
        self.signal_preserve_jup_price = os.getenv("MEME_SIGNAL_PRESERVE_JUP_PRICE", "true").lower() in ("1", "true", "yes")
        try:
            self.dex_cache_ttl_s = float(os.getenv("MEME_DEX_CACHE_TTL_S", "12") or 12.0)
        except Exception:
            self.dex_cache_ttl_s = 12.0
        self._dex_cache: dict[str, dict] = {}
        # Quote size used for signal-first pricing/impact probing.
        # Keep this small: large quotes inflate `priceImpactPct` and can starve entries on new launches.
        self.signal_quote_sol = float(os.getenv("MEME_SIGNAL_QUOTE_SOL", "0.002"))
        self.signal_max_impact_pct = float(os.getenv("MEME_SIGNAL_MAX_IMPACT_PCT", "0.35"))
        self.signal_min_momentum_5m = float(os.getenv("MEME_SIGNAL_MIN_MOMENTUM_5M", "0.0"))
        self.signal_quote_retry_count = int(os.getenv("MEME_SIGNAL_QUOTE_RETRY_COUNT", "1") or 1)
        try:
            self.signal_quote_retry_delay_s = float(os.getenv("MEME_SIGNAL_QUOTE_RETRY_DELAY_S", "0.25") or 0.25)
        except Exception:
            self.signal_quote_retry_delay_s = 0.25
        self._mint_decimals: dict[str, int] = {}
        self._mint_supply_ui: dict[str, float] = {}
        self.signal_max_candidates_per_tick = int(os.getenv("MEME_SIGNAL_MAX_CANDIDATES_PER_TICK", "20"))
        env_sources = os.getenv("MEME_SIGNAL_SOURCES", "").strip()
        self.signal_source_allowlist = {s.strip() for s in env_sources.split(",") if s.strip()} if env_sources else set()
        env_cap_bypass_sources = os.getenv("MEME_SIGNAL_CAP_BYPASS_SOURCES", "dex_mover").strip()
        self.signal_cap_bypass_sources = (
            {s.strip() for s in env_cap_bypass_sources.split(",") if s.strip()} if env_cap_bypass_sources else set()
        )
        # Winner-first prequote gate: reject low-demand signals before expensive quote/hydration calls.
        # This improves free-tier efficiency by spending API budget only on stronger early momentum.
        self.signal_prequote_require_demand = os.getenv("MEME_SIGNAL_PREQUOTE_REQUIRE_DEMAND", "true").lower() in (
            "1",
            "true",
            "yes",
        )
        self.signal_prequote_min_signal_score = float(
            os.getenv("MEME_SIGNAL_PREQUOTE_MIN_SIGNAL_SCORE", "65") or 65
        )
        self.signal_prequote_min_hits = int(os.getenv("MEME_SIGNAL_PREQUOTE_MIN_HITS", "3") or 3)
        self.signal_prequote_min_buys = int(os.getenv("MEME_SIGNAL_PREQUOTE_MIN_BUYS", "4") or 4)
        self.signal_prequote_min_unique_buyers = int(
            os.getenv("MEME_SIGNAL_PREQUOTE_MIN_UNIQUE_BUYERS", "3") or 3
        )
        self.signal_prequote_min_mcap_usd = float(
            os.getenv("MEME_SIGNAL_PREQUOTE_MIN_MCAP_USD", "0") or 0
        )
        self.signal_prequote_min_net_sol_in = float(
            os.getenv("MEME_SIGNAL_PREQUOTE_MIN_NET_SOL_IN", "1.0") or 1.0
        )
        self.signal_prequote_min_buy_sell_ratio = float(
            os.getenv("MEME_SIGNAL_PREQUOTE_MIN_BUY_SELL_RATIO", "0") or 0
        )
        self.signal_prequote_max_top_buyer_share = float(
            os.getenv("MEME_SIGNAL_PREQUOTE_MAX_TOP_BUYER_SHARE", "0") or 0
        )
        # Optional score bypass for very strong explicit demand metrics.
        # This avoids over-trusting a single heuristic score when raw demand is clearly strong.
        self.signal_prequote_score_bypass_enabled = os.getenv(
            "MEME_SIGNAL_PREQUOTE_SCORE_BYPASS_ENABLED",
            "true",
        ).lower() in ("1", "true", "yes")
        self.signal_prequote_score_bypass_min_hits = int(
            os.getenv("MEME_SIGNAL_PREQUOTE_SCORE_BYPASS_MIN_HITS", "6") or 6
        )
        self.signal_prequote_score_bypass_min_buys = int(
            os.getenv("MEME_SIGNAL_PREQUOTE_SCORE_BYPASS_MIN_BUYS", "4") or 4
        )
        self.signal_prequote_score_bypass_min_unique_buyers = int(
            os.getenv("MEME_SIGNAL_PREQUOTE_SCORE_BYPASS_MIN_UNIQUE_BUYERS", "4") or 4
        )
        self.signal_prequote_score_bypass_min_net_sol_in = float(
            os.getenv("MEME_SIGNAL_PREQUOTE_SCORE_BYPASS_MIN_NET_SOL_IN", "2.0") or 2.0
        )
        self.signal_prequote_score_bypass_max_top_buyer_share = float(
            os.getenv("MEME_SIGNAL_PREQUOTE_SCORE_BYPASS_MAX_TOP_BUYER_SHARE", "0.45") or 0.45
        )
        # Request budgeting: keep the bot alive on free tiers by bounding quote/RPC traffic.
        self._jup_window_start = 0.0
        self._jup_calls_in_window = 0
        # Separate budget for position monitoring so exits don't get starved by entry probing.
        self._jup_pos_window_start = 0.0
        self._jup_pos_calls_in_window = 0
        self._jup_cooldown_until = 0.0
        self._jup_backoff_s = 0.0
        self.jup_max_calls_per_min = int(os.getenv("MEME_JUPITER_MAX_CALLS_PER_MIN", "60"))
        self.jup_reserved_pos_calls_per_min = int(os.getenv("MEME_JUPITER_RESERVED_FOR_POSITIONS", "12") or 12)
        self.rpc_pool = RpcPool(timeout_s=float(os.getenv("MEME_RPC_TIMEOUT_S", "10") or 10), max_attempts=3)
        # Quote confirmation before entry (signal-first)
        self.confirm_enabled = os.getenv("MEME_CONFIRM_ENABLED", "true").lower() in ("1", "true", "yes")
        self.confirm_samples = int(os.getenv("MEME_CONFIRM_SAMPLES", "3"))
        self.confirm_interval_s = float(os.getenv("MEME_CONFIRM_INTERVAL_SECONDS", "8"))
        self.confirm_min_up_pct = float(os.getenv("MEME_CONFIRM_MIN_UP_PCT", "1.0"))
        self.confirm_max_impact_worsen = float(os.getenv("MEME_CONFIRM_MAX_IMPACT_WORSEN", "0.10"))
        self.confirm_require_up_ticks = int(os.getenv("MEME_CONFIRM_REQUIRE_UP_TICKS", "2"))
        # Entry-time hard risk checks (applies to paper + live):
        # - mint/freeze authority
        # - holder concentration
        # - buy->sell routeability sanity (honeypot/untradable guard)
        self.entry_hard_risk_checks_enabled = os.getenv(
            "MEME_ENTRY_HARD_RISK_CHECKS_ENABLED", "true"
        ).lower() in ("1", "true", "yes")
        self.entry_sellability_check_enabled = os.getenv(
            "MEME_ENTRY_SELLABILITY_CHECK_ENABLED", "true"
        ).lower() in ("1", "true", "yes")
        self.entry_sellability_min_back_pct = float(
            os.getenv("MEME_ENTRY_SELLABILITY_MIN_BACK_PCT", "0.55") or 0.55
        )
        self.entry_sellability_max_sell_impact_pct = float(
            os.getenv("MEME_ENTRY_SELLABILITY_MAX_SELL_IMPACT_PCT", "0.75") or 0.75
        )
        self.entry_sellability_slippage_bps = int(
            os.getenv("MEME_ENTRY_SELLABILITY_SLIPPAGE_BPS", "80") or 80
        )
        self.entry_sellability_probe_sol = float(
            os.getenv("MEME_ENTRY_SELLABILITY_PROBE_SOL", "0.0") or 0.0
        )
        self.entry_sellability_cache_s = float(
            os.getenv("MEME_ENTRY_SELLABILITY_CACHE_S", "300") or 300
        )
        self.holder_check_cache_s = float(os.getenv("MEME_HOLDER_CHECK_CACHE_S", "600") or 600)
        self._entry_sellability_cache: dict[str, tuple[float, bool, str, float, float]] = {}
        self._holder_check_cache: dict[str, tuple[float, bool, str]] = {}
        # Discrete market-cap level model (matches meme-coin "ladder then dump" behavior).
        self.mcap_levels_enabled = os.getenv("MEME_MCAP_LEVELS_ENABLED", "true").lower() in ("1", "true", "yes")
        levels_raw = os.getenv(
            "MEME_MCAP_LEVELS",
            "15000,30000,60000,100000,200000,500000,1000000",
        )
        levels: list[float] = []
        for x in str(levels_raw).split(","):
            s = str(x).strip().replace("_", "")
            if not s:
                continue
            try:
                v = float(s)
            except Exception:
                continue
            if v > 0:
                levels.append(v)
        self.mcap_levels = sorted(set(levels))
        self.mcap_level_retrace_pct = float(os.getenv("MEME_MCAP_LEVEL_RETRACE_PCT", "0.18") or 0.18)
        self.mcap_level_retrace_confirm_s = float(os.getenv("MEME_MCAP_LEVEL_RETRACE_CONFIRM_S", "8") or 8)
        self.mcap_level_min_hold_s = float(os.getenv("MEME_MCAP_LEVEL_MIN_HOLD_SECONDS", "45") or 45)
        self.mcap_level_sell_fraction = float(os.getenv("MEME_MCAP_LEVEL_SELL_FRACTION", "1.0") or 1.0)
        # Entry pattern gate (stateful): require either
        # - impulse level transition with strong demand, or
        # - base build + breakout with strong demand.
        self.entry_pattern_gate_enabled = os.getenv("MEME_ENTRY_PATTERN_GATE_ENABLED", "true").lower() in (
            "1",
            "true",
            "yes",
        )
        pattern_levels_raw = os.getenv("MEME_ENTRY_PATTERN_LEVELS", "").strip()
        if pattern_levels_raw:
            p_levels: list[float] = []
            for x in pattern_levels_raw.split(","):
                s = str(x).strip().replace("_", "")
                if not s:
                    continue
                try:
                    v = float(s)
                except Exception:
                    continue
                if v > 0:
                    p_levels.append(v)
            self.entry_pattern_levels = sorted(set(p_levels))
        else:
            self.entry_pattern_levels = list(self.mcap_levels)
        self.entry_pattern_min_level = float(os.getenv("MEME_ENTRY_PATTERN_MIN_LEVEL", "30000") or 30000)
        self.entry_pattern_lookback_s = float(os.getenv("MEME_ENTRY_PATTERN_LOOKBACK_S", "1800") or 1800)
        self.entry_pattern_cooldown_s = float(os.getenv("MEME_ENTRY_PATTERN_COOLDOWN_S", "600") or 600)
        # Impulse branch
        self.entry_pattern_impulse_window_s = float(
            os.getenv("MEME_ENTRY_PATTERN_IMPULSE_WINDOW_S", "180") or 180
        )
        self.entry_pattern_impulse_min_mom5m = float(
            os.getenv("MEME_ENTRY_PATTERN_IMPULSE_MIN_MOM5M", "1.0") or 1.0
        )
        # Base-breakout branch
        self.entry_pattern_base_min_span_s = float(
            os.getenv("MEME_ENTRY_PATTERN_BASE_MIN_SPAN_S", "300") or 300
        )
        self.entry_pattern_base_min_touches = int(
            os.getenv("MEME_ENTRY_PATTERN_BASE_MIN_TOUCHES", "3") or 3
        )
        self.entry_pattern_base_band_pct = float(
            os.getenv("MEME_ENTRY_PATTERN_BASE_BAND_PCT", "0.18") or 0.18
        )
        self.entry_pattern_base_breakout_pct = float(
            os.getenv("MEME_ENTRY_PATTERN_BASE_BREAKOUT_PCT", "0.05") or 0.05
        )
        # Demand floor shared by both branches
        self.entry_pattern_min_hits = int(os.getenv("MEME_ENTRY_PATTERN_MIN_HITS", "2") or 2)
        self.entry_pattern_min_uniq = int(os.getenv("MEME_ENTRY_PATTERN_MIN_UNIQUE_BUYERS", "2") or 2)
        self.entry_pattern_min_net_sol_in = float(
            os.getenv("MEME_ENTRY_PATTERN_MIN_NET_SOL_IN", "1.0") or 1.0
        )
        self.entry_pattern_min_signal_score = float(
            os.getenv("MEME_ENTRY_PATTERN_MIN_SIGNAL_SCORE", "0") or 0.0
        )
        self._entry_pattern_state: dict[str, dict[str, Any]] = {}
        # Scale-in (paper-first): do not take full risk until we see it move.
        self.scale_in_enabled = os.getenv("MEME_SCALE_IN_ENABLED", "false").lower() in ("1", "true", "yes")
        try:
            self.scale_in_initial_fraction = float(os.getenv("MEME_SCALE_IN_INITIAL_FRACTION", "0.25") or 0.25)
        except Exception:
            self.scale_in_initial_fraction = 0.25
        try:
            self.scale_in_add_threshold_pct = float(os.getenv("MEME_SCALE_IN_ADD_THRESHOLD_PCT", "2.0") or 2.0)
        except Exception:
            self.scale_in_add_threshold_pct = 2.0
        try:
            self.scale_in_abort_below_pct = float(os.getenv("MEME_SCALE_IN_ABORT_BELOW_PCT", "-1.0") or -1.0)
        except Exception:
            self.scale_in_abort_below_pct = -1.0
        try:
            self.scale_in_window_seconds = int(os.getenv("MEME_SCALE_IN_WINDOW_SECONDS", "60") or 60)
        except Exception:
            self.scale_in_window_seconds = 60
        # Entry pacing: keep fills selective when signal flow is bursty.
        # 0 disables the guard.
        self.max_new_entries_per_tick = int(os.getenv("MEME_MAX_NEW_ENTRIES_PER_TICK", "0") or 0)
        try:
            self.min_seconds_between_entries = float(os.getenv("MEME_MIN_SECONDS_BETWEEN_ENTRIES", "0") or 0.0)
        except Exception:
            self.min_seconds_between_entries = 0.0
        self._last_entry_ts: float = 0.0
        # Config guardrails: detect gate drift/mismatch early so we don't run for hours
        # with contradictory entry constraints.
        try:
            self._validate_config_guardrails()
        except Exception as e:
            console.print(f"[red]Config guardrail error: {e}[/red]")
            raise
        self.entry_timestamps: list[float] = []
        self.recent_pnl: list[tuple[float, float]] = []
        self.loss_halt_until: float = 0.0
        if self.launch_signals_file and self.launch_signal_ignore_history:
            try:
                with open(self.launch_signals_file, "r", encoding="utf-8") as fh:
                    fh.seek(0, os.SEEK_END)
                    self._signal_offset = fh.tell()
                self.launch_signal_seen.clear()
            except Exception:
                pass

        try:
            console.print(
                f"[dim]config signal_first={self.signal_first} "
                f"launch_signals_file={os.path.basename(self.launch_signals_file) or 'none'} "
                f"launch_mints_file={os.path.basename(self.launch_mints_file) or 'none'} "
                f"run_id={self.run_id}[/dim]"
            )
        except Exception:
            pass

        # Persist a minimal, redacted manifest of this run so later analysis can be run-scoped.
        try:
            self._write_run_manifest()
        except Exception:
            pass

        # Persistent stats file
        self._stats_file = Path(os.getenv('MEME_STATS_FILE', 'data/meme_stats.json'))
        self._load_stats()
        # Track deltas for this process run (useful while iterating config rapidly).
        self._run_started_at = time.time()
        self._run_baseline = {
            "pnl": float(self.session_pnl or 0.0),
            "wins": int(self.session_wins or 0),
            "losses": int(self.session_losses or 0),
            "trades": int(self.session_trades or 0),
        }

        # Restore open positions from the store so a restart doesn't "forget" risk.
        # This is most important in PAPER mode while iterating, and is also a prerequisite
        # for any reliable live mode.
        try:
            self._restore_open_positions_from_store()
        except Exception:
            pass

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

    def _restore_open_positions_from_store(self) -> None:
        if not (self.paper_mode and HAS_POSITION_STORE):
            return
        if os.getenv("MEME_RESTORE_OPEN_POSITIONS", "true").strip().lower() not in ("1", "true", "yes"):
            return
        try:
            max_age_h = float(os.getenv("MEME_RESTORE_MAX_AGE_HOURS", "6") or 6.0)
        except Exception:
            max_age_h = 6.0
        try:
            max_n = int(os.getenv("MEME_RESTORE_MAX_POSITIONS", "25") or 25)
        except Exception:
            max_n = 25

        now = time.time()
        store = get_store()
        opens = store.get_all_positions(status="open") or {}
        restored = 0
        for mint, row in list(opens.items())[: max_n]:
            try:
                if not mint or mint in self.active_positions:
                    continue
                symbol = str(row.get("symbol") or "") or mint[:8]
                entry_price = float(row.get("entry_price") or 0.0)
                if entry_price <= 0:
                    continue
                amount_tokens = float(row.get("amount_tokens") or 0.0)
                amount_usd = float(row.get("amount_usd") or 0.0)
                highest = float(row.get("highest_price_seen") or entry_price)
                entry_ts = str(row.get("entry_timestamp") or "").strip()
                entry_time = now
                if entry_ts:
                    try:
                        # stored as local isoformat without tz
                        dt = datetime.fromisoformat(entry_ts)
                        entry_time = dt.timestamp()
                    except Exception:
                        entry_time = now
                age_h = (now - float(entry_time)) / 3600.0 if entry_time else 0.0
                if max_age_h > 0 and age_h > max_age_h:
                    continue

                meta = row.get("metadata") or {}
                if not isinstance(meta, dict):
                    meta = {}
                score = int(meta.get("score") or 0)
                initial_stop = meme_config.get_stop_loss_for_score(score) if score else meme_config.INITIAL_STOP_LOSS

                pos = ActivePosition(
                    mint=mint,
                    symbol=symbol,
                    entry_price=entry_price,
                    entry_time=float(entry_time),
                    amount_tokens=amount_tokens,
                    amount_sol=0.0,  # display-only; computed opportunistically elsewhere
                    amount_usd=amount_usd,
                    current_price=entry_price,
                )
                pos.state = PositionState(
                    mint=mint,
                    symbol=symbol,
                    entry_price=entry_price,
                    entry_time=float(entry_time),
                    amount_tokens=amount_tokens,
                    amount_usd=amount_usd,
                    score=score,
                    initial_stop_pct=float(initial_stop),
                )
                # Preserve original run attribution for restored positions.
                # If missing, mark as preexisting so later exits are not attributed
                # to the current process run by accident.
                try:
                    restored_run_id = str(meta.get("run_id") or "").strip()
                except Exception:
                    restored_run_id = ""
                if not restored_run_id:
                    try:
                        run_started = float(getattr(self, "_run_started_at", now) or now)
                    except Exception:
                        run_started = now
                    if float(entry_time) < (run_started - 30.0):
                        restored_run_id = "restored_preexisting"
                    else:
                        restored_run_id = str(getattr(self, "run_id", "") or "").strip()
                pos.state.run_id = restored_run_id
                for k, caster in (
                    ("liquidity_entry", float),
                    ("market_cap_entry", float),
                    ("price_change_5m_entry", float),
                    ("volume_5m_entry", float),
                ):
                    try:
                        if k in meta and meta.get(k) is not None:
                            setattr(pos.state, k, caster(meta.get(k)))
                    except Exception:
                        pass
                for k, caster in (
                    ("buys_5m_entry", int),
                    ("sells_5m_entry", int),
                    ("txns_5m_entry", int),
                ):
                    try:
                        if k in meta and meta.get(k) is not None:
                            setattr(pos.state, k, caster(meta.get(k)))
                    except Exception:
                        pass
                try:
                    pos.state.market_cap_entry = float(meta.get("market_cap_entry") or 0.0)
                except Exception:
                    pass
                try:
                    pos.state.signal_score = float(meta.get("signal_score") or 0.0)
                    pos.state.signal_tier = str(meta.get("signal_tier") or "")
                    pos.state.signal_hits = int(meta.get("signal_hits") or 0)
                    pos.state.signal_buys = int(meta.get("signal_buys") or 0)
                    pos.state.signal_sells = int(meta.get("signal_sells") or 0)
                    pos.state.signal_unique_buyers = int(meta.get("signal_unique_buyers") or 0)
                    pos.state.signal_net_sol_in = float(meta.get("signal_net_sol_in") or 0.0)
                    pos.state.signal_top_buyer_share = float(meta.get("signal_top_buyer_share") or 0.0)
                    tfs = meta.get("signal_t_first_sell_s")
                    pos.state.signal_t_first_sell_s = float(tfs) if tfs is not None else None
                    pos.state.signal_mcap_size_mult = float(meta.get("signal_mcap_size_mult") or 0.0)
                except Exception:
                    pass
                try:
                    pos.state.highest_price_seen = float(highest)
                except Exception:
                    pass
                try:
                    pos.highest_price_seen = float(highest)  # not a field; ignore if not present
                except Exception:
                    pass

                # Restore scale-in flags if present (primarily signal-first, but harmless).
                if meta.get("scale_in_enabled"):
                    pos.scale_in_enabled = True
                    try:
                        pos.target_amount_usd = float(meta.get("scale_in_target_usd") or 0.0)
                        pos.target_amount_sol = float(meta.get("scale_in_target_sol") or 0.0)
                    except Exception:
                        pass
                    pos.scale_in_done = bool(meta.get("scale_in_added") or False)

                self.active_positions[mint] = pos
                self.seen_tokens.add(mint)
                restored += 1
            except Exception:
                continue

        if restored:
            console.print(f"[cyan]Restored {restored} open position(s) from PositionStore[/cyan]")

    def _guardrail_mode(self) -> str:
        raw = str(os.getenv("MEME_CONFIG_GUARDRAILS_MODE", "warn") or "warn").strip().lower()
        if raw in ("off", "0", "false", "no"):
            return "off"
        if raw in ("strict", "error", "raise"):
            return "strict"
        return "warn"

    def _guardrail_emit(self, code: str, message: str, *, level: str = "warn") -> None:
        mode = self._guardrail_mode()
        if mode == "off":
            return
        tag = f"GUARDRAIL[{code}]"
        if level == "error":
            console.print(f"[red]{tag} {message}[/red]")
            if mode == "strict":
                raise ValueError(f"{code}: {message}")
        else:
            console.print(f"[yellow]{tag} {message}[/yellow]")

    def _validate_config_guardrails(self) -> None:
        if not (self.signal_first and self.launch_signals_file):
            return

        # Effective final-stage filters.
        sig_min_default = float(getattr(meme_config, "MIN_MARKET_CAP_USD", 0.0) or 0.0)
        final_min_mcap = float(os.getenv("MEME_SIGNAL_MIN_MCAP_USD", str(sig_min_default)) or sig_min_default)
        final_max_top_share = float(os.getenv("MEME_SIGNAL_MAX_TOP_BUYER_SHARE", "0") or 0.0)
        final_min_net = float(os.getenv("MEME_SIGNAL_MIN_NET_SOL_IN", "0") or 0.0)
        final_require_demand = str(os.getenv("MEME_SIGNAL_REQUIRE_DEMAND_METRICS", "false") or "false").lower() in (
            "1",
            "true",
            "yes",
        )

        # 1) MCap drift: final gate should not be looser than prequote mcap gate.
        if self.signal_prequote_min_mcap_usd > 0 and final_min_mcap > 0 and final_min_mcap < self.signal_prequote_min_mcap_usd:
            self._guardrail_emit(
                "mcap_floor_drift",
                (
                    f"final MEME_SIGNAL_MIN_MCAP_USD={final_min_mcap:.0f} is below "
                    f"prequote MEME_SIGNAL_PREQUOTE_MIN_MCAP_USD={self.signal_prequote_min_mcap_usd:.0f}"
                ),
                level="error",
            )

        # 2) Scout lane consistency: scout floor must be below strict final floor to be reachable.
        if self.signal_mcap_scout_enabled and self.signal_scout_min_mcap_usd >= final_min_mcap > 0:
            self._guardrail_emit(
                "mcap_scout_unreachable",
                (
                    f"MEME_SIGNAL_SCOUT_MIN_MCAP_USD={self.signal_scout_min_mcap_usd:.0f} "
                    f">= MEME_SIGNAL_MIN_MCAP_USD={final_min_mcap:.0f}; scout lane will rarely/never trigger"
                ),
                level="warn",
            )

        # 3) Concentration drift: final top-share cap looser than prequote top-share cap.
        if (
            self.signal_prequote_max_top_buyer_share > 0
            and final_max_top_share > 0
            and final_max_top_share > self.signal_prequote_max_top_buyer_share
        ):
            self._guardrail_emit(
                "top_share_drift",
                (
                    f"final MEME_SIGNAL_MAX_TOP_BUYER_SHARE={final_max_top_share:.3f} is looser than "
                    f"prequote MEME_SIGNAL_PREQUOTE_MAX_TOP_BUYER_SHARE={self.signal_prequote_max_top_buyer_share:.3f}"
                ),
                level="warn",
            )

        # 4) Demand-metric drift: prequote requires demand but later stage allows missing metrics.
        if self.signal_prequote_require_demand and not final_require_demand:
            self._guardrail_emit(
                "demand_metrics_drift",
                "MEME_SIGNAL_PREQUOTE_REQUIRE_DEMAND=true but MEME_SIGNAL_REQUIRE_DEMAND_METRICS=false",
                level="warn",
            )

        # 5) Net-sol drift: final demand floor looser than prequote floor.
        if self.signal_prequote_min_net_sol_in > 0 and final_min_net > 0 and final_min_net < self.signal_prequote_min_net_sol_in:
            self._guardrail_emit(
                "net_sol_drift",
                (
                    f"final MEME_SIGNAL_MIN_NET_SOL_IN={final_min_net:.3f} is below "
                    f"prequote MEME_SIGNAL_PREQUOTE_MIN_NET_SOL_IN={self.signal_prequote_min_net_sol_in:.3f}"
                ),
                level="warn",
            )

    def _write_run_manifest(self) -> None:
        base = Path(PROJECT_ROOT)
        out_dir = base / "data" / "meme_runs"
        out_dir.mkdir(parents=True, exist_ok=True)

        def _redact_url(u: str) -> str:
            try:
                p = urlsplit(u)
                # keep scheme + hostname only
                host = p.netloc
                if not host:
                    return ""
                return f"{p.scheme}://{host}"
            except Exception:
                return ""

        # Only capture non-sensitive config knobs. Avoid dumping the full environment.
        keys = [
            "MEME_CONFIG_FILE",
            "MEME_SIGNAL_FIRST",
            "MEME_PAPER_MODE",
            "MEME_MAX_POSITIONS",
            "MEME_MIN_VHI_SCORE",
            "MEME_MIN_MCAP",
            "MEME_MAX_MCAP",
            "MEME_MIN_LIQUIDITY",
            "MEME_MIN_TXNS_5M",
            "MEME_MIN_BUYS_5M",
            "MEME_MIN_VOLUME_5M",
            "MEME_MIN_BUY_SELL_5M",
            "MEME_MAX_5M_PUMP",
            "MEME_SCALE_IN_ENABLED",
            "MEME_SCALE_IN_INITIAL_FRACTION",
            "MEME_SCALE_IN_ADD_THRESHOLD_PCT",
            "MEME_SCALE_IN_ABORT_BELOW_PCT",
            "MEME_SCALE_IN_WINDOW_SECONDS",
            "MEME_MAX_LOSS_PER_TRADE",
            "MEME_FAIL_FAST",
            "MEME_FAIL_FAST_WINDOW",
            "MEME_FAIL_FAST_MIN_GAIN",
            "MEME_FAIL_FAST_SELL",
            "MEME_FAIL_FAST_MIN_HOLD",
            "MEME_VOL_COLLAPSE_SELL",
        ]
        env = {k: (os.getenv(k) or "") for k in keys if (os.getenv(k) is not None)}
        manifest = {
            "run_id": self.run_id,
            "created_at": datetime.now().isoformat(),
            "mode": "PAPER" if self.paper_mode else "LIVE",
            "rpc_url": _redact_url(os.getenv("RPC_URL", "") or ""),
            "rpc_pool_file": os.getenv("RPC_POOL_FILE", "") or "",
            "env": env,
        }
        (out_dir / f"{self.run_id}.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    @staticmethod
    def _parse_trade_timestamp(ts_raw: Any) -> float:
        """Parse SQLite timestamp text into epoch seconds (UTC)."""
        if ts_raw is None:
            return 0.0
        s = str(ts_raw).strip()
        if not s:
            return 0.0
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return float(dt.timestamp())
        except Exception:
            return 0.0

    def _cluster_regime_rows(self, rows: list[dict[str, Any]]) -> dict[str, float | int]:
        """Cluster leg-level exits into approximate positions for concentration checks."""
        if not rows:
            return {
                "cluster_count": 0,
                "loss_cluster_share": 0.0,
                "dominant_cluster_leg_share": 0.0,
            }
        entry_tol = max(1, int(self.regime_cluster_entry_tolerance_s))
        gap_fallback = max(1, int(self.regime_cluster_gap_fallback_s))
        by_mint: dict[str, list[dict[str, Any]]] = {}

        sorted_rows = sorted(rows, key=lambda x: float(x.get("ts") or 0.0))
        for tr in sorted_rows:
            mint = str(tr.get("mint") or "").strip() or "_unknown_"
            ts = float(tr.get("ts") or 0.0)
            entry_anchor = float(tr.get("entry_anchor") or ts)
            pnl = float(tr.get("pnl") or 0.0)
            reason = str(tr.get("exit_reason") or "UNKNOWN")

            bucket = by_mint.setdefault(mint, [])
            chosen: dict[str, Any] | None = None
            for c in reversed(bucket):
                same_entry = abs(entry_anchor - float(c.get("entry_anchor") or 0.0)) <= float(entry_tol)
                close_exit = (ts - float(c.get("last_ts") or 0.0)) <= float(gap_fallback)
                if same_entry or close_exit:
                    chosen = c
                    break
            if chosen is None:
                chosen = {
                    "entry_anchor": entry_anchor,
                    "last_ts": ts,
                    "trade_count": 0,
                    "pnl_usd": 0.0,
                    "reasons": set(),
                }
                bucket.append(chosen)

            chosen["trade_count"] = int(chosen.get("trade_count") or 0) + 1
            chosen["last_ts"] = max(float(chosen.get("last_ts") or 0.0), ts)
            chosen["pnl_usd"] = float(chosen.get("pnl_usd") or 0.0) + pnl
            try:
                chosen["reasons"].add(reason)
            except Exception:
                pass

        clusters: list[dict[str, Any]] = []
        for xs in by_mint.values():
            clusters.extend(xs)

        n = len(sorted_rows)
        cn = len(clusters)
        if n <= 0 or cn <= 0:
            return {
                "cluster_count": 0,
                "loss_cluster_share": 0.0,
                "dominant_cluster_leg_share": 0.0,
            }

        dominant_cluster_leg_share = max(float(c.get("trade_count") or 0.0) for c in clusters) / float(max(1, n))
        loss_abs = [abs(float(c.get("pnl_usd") or 0.0)) for c in clusters if float(c.get("pnl_usd") or 0.0) < 0.0]
        loss_cluster_share = (max(loss_abs) / sum(loss_abs)) if loss_abs and sum(loss_abs) > 0 else 0.0

        return {
            "cluster_count": int(cn),
            "loss_cluster_share": float(loss_cluster_share),
            "dominant_cluster_leg_share": float(dominant_cluster_leg_share),
        }

    def _evaluate_entry_regime(self, *, force: bool = False) -> bool:
        """Return True when new entries are allowed under recent-performance regime.

        This check only gates *new* entries. Position monitoring/exits always run.
        """
        if not self.regime_guard_enabled:
            return True

        now = time.time()
        if self.regime_block_until and now < self.regime_block_until and not force:
            self._regime_snapshot["allow"] = False
            self._regime_snapshot["streak"] = int(self._regime_block_streak)
            self._regime_snapshot["pause_s"] = float(max(0.0, self.regime_block_until - now))
            return False

        if (not force) and self._regime_last_eval_ts and (now - self._regime_last_eval_ts) < float(self.regime_eval_interval_s):
            return bool(self._regime_snapshot.get("allow", True))
        self._regime_last_eval_ts = now

        try:
            window_min = int(os.getenv("MEME_REGIME_WINDOW_MINUTES", "30") or 30)
        except Exception:
            window_min = 30
        try:
            min_trades = int(os.getenv("MEME_REGIME_MIN_TRADES", "10") or 10)
        except Exception:
            min_trades = 10
        try:
            min_wr = float(os.getenv("MEME_REGIME_MIN_WINRATE", "0.30") or 0.30)
        except Exception:
            min_wr = 0.30
        try:
            min_avg_pnl = float(os.getenv("MEME_REGIME_MIN_AVG_PNL_USD", "-0.05") or -0.05)
        except Exception:
            min_avg_pnl = -0.05
        try:
            block_s = int(os.getenv("MEME_REGIME_BLOCK_SECONDS", "300") or 300)
        except Exception:
            block_s = 300

        db_env = str(os.getenv("MEME_POSITIONS_DB", "data/positions.db") or "data/positions.db")
        db_path = Path(db_env)
        if not db_path.is_absolute():
            db_path = Path(PROJECT_ROOT) / db_path
        if not db_path.exists():
            self._regime_snapshot = {
                "allow": True,
                "n": 0,
                "wins": 0,
                "wr": 0.0,
                "avg_pnl": 0.0,
                "sum_pnl": 0.0,
                "cluster_count": 0,
                "loss_cluster_share": 0.0,
                "dominant_cluster_leg_share": 0.0,
                "reasons": "",
            }
            return True

        try:
            con = sqlite3.connect(str(db_path))
            con.row_factory = sqlite3.Row
            try:
                cur = con.cursor()
                rows = cur.execute(
                    """
                    SELECT pnl_usd, mint, exit_reason, metadata, created_at
                    FROM trades
                    WHERE side='SELL' AND datetime(created_at) >= datetime('now', ?)
                    ORDER BY datetime(created_at) ASC
                    """,
                    (f"-{int(window_min)} minutes",),
                ).fetchall()
            finally:
                con.close()
        except Exception:
            # Fail-open if DB is unavailable.
            return True

        scoped_rows: list[dict[str, Any]] = []
        for r in rows or []:
            try:
                pnl = float(r["pnl_usd"] or 0.0)
            except Exception:
                pnl = 0.0
            mint = str(r["mint"] or "")
            exit_reason = str(r["exit_reason"] or "")
            md_raw = r["metadata"] or "{}"
            try:
                md = json.loads(md_raw) if isinstance(md_raw, str) else (md_raw if isinstance(md_raw, dict) else {})
            except Exception:
                md = {}

            rid = str((md or {}).get("run_id") or "").strip()
            if self.regime_scope_run_id:
                this_run = str(getattr(self, "run_id", "") or "").strip()
                if not rid or rid != this_run:
                    continue

            ts = float(self._parse_trade_timestamp(r["created_at"]))
            try:
                hold_s = float((md or {}).get("hold_time_sec") or 0.0)
            except Exception:
                hold_s = 0.0
            entry_anchor = float(ts - hold_s) if hold_s > 0 else float(ts)
            scoped_rows.append(
                {
                    "pnl": pnl,
                    "mint": mint,
                    "exit_reason": exit_reason,
                    "ts": ts,
                    "entry_anchor": entry_anchor,
                }
            )

        n = len(scoped_rows)
        wins = sum(1 for tr in scoped_rows if float(tr.get("pnl") or 0.0) > 0.0)
        sum_pnl = float(sum(float(tr.get("pnl") or 0.0) for tr in scoped_rows))
        avg_pnl = (sum_pnl / float(max(1, n))) if n > 0 else 0.0
        wr = (float(wins) / float(n)) if n > 0 else 0.0

        cluster_stats = {
            "cluster_count": 0,
            "loss_cluster_share": 0.0,
            "dominant_cluster_leg_share": 0.0,
        }
        cluster_ready = False
        if self.regime_cluster_brake_enabled and n >= max(1, int(self.regime_cluster_min_trades)):
            cluster_stats = self._cluster_regime_rows(scoped_rows)
            cluster_ready = int(cluster_stats.get("cluster_count") or 0) >= max(1, int(self.regime_cluster_min_clusters))

        if n < max(1, min_trades):
            # If we no longer have meaningful recent sample, decay stale escalation state.
            try:
                reset_s = float(self.regime_escalation_reset_minutes) * 60.0
            except Exception:
                reset_s = 3600.0
            if self._regime_block_streak > 0 and self._regime_last_block_ts > 0 and (now - self._regime_last_block_ts) > reset_s:
                self._regime_block_streak = 0
                self._regime_healthy_streak = 0
            self._regime_snapshot = {
                "allow": True,
                "n": n,
                "wins": wins,
                "wr": wr,
                "avg_pnl": avg_pnl,
                "sum_pnl": sum_pnl,
                "cluster_count": int(cluster_stats.get("cluster_count") or 0),
                "loss_cluster_share": float(cluster_stats.get("loss_cluster_share") or 0.0),
                "dominant_cluster_leg_share": float(cluster_stats.get("dominant_cluster_leg_share") or 0.0),
                "reasons": "",
                "streak": int(self._regime_block_streak),
                "pause_s": 0.0,
            }
            return True

        reasons: list[str] = []
        if wr < float(min_wr):
            reasons.append("wr")
        if avg_pnl < float(min_avg_pnl):
            reasons.append("avg_pnl")
        if cluster_ready:
            loss_cluster_share = float(cluster_stats.get("loss_cluster_share") or 0.0)
            dominant_cluster_leg_share = float(cluster_stats.get("dominant_cluster_leg_share") or 0.0)
            if (
                self.regime_max_loss_cluster_share > 0
                and loss_cluster_share > float(self.regime_max_loss_cluster_share)
            ):
                reasons.append("loss_cluster_share")
            if (
                self.regime_max_dominant_cluster_leg_share > 0
                and dominant_cluster_leg_share > float(self.regime_max_dominant_cluster_leg_share)
            ):
                reasons.append("dominant_cluster_leg_share")

        unhealthy = len(reasons) > 0
        allow = not unhealthy
        if not allow:
            self._regime_healthy_streak = 0
            pause_s = float(block_s)
            if self.regime_escalation_enabled:
                try:
                    reset_s = float(self.regime_escalation_reset_minutes) * 60.0
                except Exception:
                    reset_s = 3600.0
                if self._regime_last_block_ts > 0 and (now - self._regime_last_block_ts) <= reset_s:
                    self._regime_block_streak = int(self._regime_block_streak) + 1
                else:
                    self._regime_block_streak = 1
                cap_s = float(block_s) * max(1.0, float(self.regime_escalation_max_mult or 1.0))
                pause_s = min(cap_s, float(block_s) + max(0.0, float(self._regime_block_streak - 1)) * float(self.regime_escalation_step_s))
            else:
                self._regime_block_streak = 1
            self._regime_last_block_ts = now
            self.regime_block_until = max(float(self.regime_block_until or 0.0), now + float(pause_s))
            if (now - float(self._regime_last_log_ts or 0.0)) >= 60.0:
                self._regime_last_log_ts = now
                console.print(
                    f"[yellow]REGIME PAUSE: {window_min}m n={n} wr={wr*100:.1f}% "
                    f"avg_pnl=${avg_pnl:+.3f} sum=${sum_pnl:+.2f}; "
                    f"clusters={int(cluster_stats.get('cluster_count') or 0)} "
                    f"tail={float(cluster_stats.get('loss_cluster_share') or 0.0)*100.0:.1f}% "
                    f"dom_legs={float(cluster_stats.get('dominant_cluster_leg_share') or 0.0)*100.0:.1f}% "
                    f"reasons={','.join(reasons)} "
                    f"pause {pause_s:.0f}s (streak={self._regime_block_streak})[/yellow]"
                )
        else:
            # Decay/reset escalation after sustained healthy windows.
            self._regime_healthy_streak = int(self._regime_healthy_streak) + 1
            if self._regime_block_streak > 0 and self._regime_healthy_streak >= int(
                max(1, self.regime_escalation_reset_healthy_cycles)
            ):
                self._regime_block_streak = 0
                self._regime_healthy_streak = 0

        self._regime_snapshot = {
            "allow": allow,
            "n": n,
            "wins": wins,
            "wr": wr,
            "avg_pnl": avg_pnl,
            "sum_pnl": sum_pnl,
            "cluster_count": int(cluster_stats.get("cluster_count") or 0),
            "loss_cluster_share": float(cluster_stats.get("loss_cluster_share") or 0.0),
            "dominant_cluster_leg_share": float(cluster_stats.get("dominant_cluster_leg_share") or 0.0),
            "reasons": ",".join(reasons),
            "streak": int(self._regime_block_streak),
            "pause_s": float(max(0.0, self.regime_block_until - now) if not allow else 0.0),
        }
        return bool(allow)

    def _load_stats(self):
        """Load persisted session stats from disk."""
        try:
            if self._stats_file.exists():
                data = json.loads(self._stats_file.read_text())
                self.session_pnl = data.get('pnl', 0.0)
                self.session_wins = data.get('wins', 0)
                self.session_losses = data.get('losses', 0)
                self.session_trades = data.get('trades', 0)
                console.print(
                    f"[cyan]Loaded cumulative stats: {self.session_wins}W/{self.session_losses}L | "
                    f"P&L: ${self.session_pnl:+.2f}[/cyan]"
                )
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

    def _run_delta(self) -> tuple[int, int, int, float]:
        """Return (wins, losses, trades, pnl) deltas since this process started."""
        base = getattr(self, "_run_baseline", None) or {}
        try:
            dw = int(self.session_wins or 0) - int(base.get("wins") or 0)
            dl = int(self.session_losses or 0) - int(base.get("losses") or 0)
            dt = int(self.session_trades or 0) - int(base.get("trades") or 0)
            dp = float(self.session_pnl or 0.0) - float(base.get("pnl") or 0.0)
            return dw, dl, dt, dp
        except Exception:
            return 0, 0, 0, 0.0

    def _signal_debug_write(self, kind: str, candidate: TokenCandidate, extra: dict | None = None) -> None:
        """Write a signal-first debug event (rate-limited) to data/meme_signal_debug.jsonl."""
        if not self.signal_debug:
            return
        now = time.time()
        if (now - self._signal_debug_window_start) >= 60:
            self._signal_debug_window_start = now
            self._signal_debug_in_window = 0
        if self._signal_debug_in_window >= self.signal_debug_max_per_min:
            return
        self._signal_debug_in_window += 1
        try:
            metrics = self.launch_signal_metrics.get(candidate.mint, {}) if self.launch_signals_file else {}
            m = metrics if isinstance(metrics, dict) else {}
            out_path = self.signal_debug_file or "data/meme_signal_debug.jsonl"
            source_run_id = str(m.get("run_id") or "").strip() if isinstance(m, dict) else ""
            evt = {
                "ts": now,
                "run_id": self.run_id,
                "source_run_id": source_run_id or None,
                "schema_version": 2,
                "kind": kind,
                "mint": candidate.mint,
                "symbol": getattr(candidate, "symbol", "") or candidate.mint[:4],
                "score": getattr(candidate, "composite_score", 0) or 0,
                "winner_score": float(getattr(candidate, "winner_score", 0.0) or 0.0),
                "winner_features_used": int(getattr(candidate, "winner_features_used", 0) or 0),
                "winner_zone_id": str(getattr(candidate, "winner_zone_id", "") or ""),
                "winner_zone_objective": float(getattr(candidate, "winner_zone_objective", 0.0) or 0.0),
                "winner_zone_bypassed": bool(getattr(candidate, "winner_zone_bypassed", False)),
                "winner_zone_bypass_reason": str(getattr(candidate, "winner_zone_bypass_reason", "") or ""),
                "liquidity_estimated": bool(getattr(candidate, "liquidity_estimated", False)),
                "price": float(getattr(candidate, "price", 0.0) or 0.0),
                "impact": float(getattr(candidate, "price_impact_pct", 0.0) or 0.0),
                "market_cap": float(getattr(candidate, "market_cap", 0.0) or 0.0),
                "m": {
                    "hits": m.get("hits"),
                    "buys": m.get("buys"),
                    "sells": m.get("sells"),
                    "unique_buyers": m.get("unique_buyers"),
                    "net_sol_in": m.get("net_sol_in"),
                    "buy_accel": m.get("buy_accel"),
                    "top_buyer_share": m.get("top_buyer_share"),
                    "buy_max_sol": m.get("buy_max_sol"),
                    "t_first_sell_s": m.get("t_first_sell_s"),
                },
                "extra": extra or {},
            }
            out_dir = os.path.dirname(out_path) or "."
            os.makedirs(out_dir, exist_ok=True)
            with open(out_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(evt) + "\n")
        except Exception:
            return

    def _set_signal_mcap_recheck(self, mint: str, reason: str = "mcap_low") -> float:
        """Set market-cap recheck cooldown with optional exponential backoff per mint."""
        if not mint or self.signal_mcap_recheck_cooldown_s <= 0:
            return 0.0
        cooldown = float(self.signal_mcap_recheck_cooldown_s)
        if self.signal_mcap_recheck_backoff_enabled and reason in ("mcap_low", "mcap_missing", "mcap_scout_gate"):
            tries = int(self._signal_mcap_recheck_counts.get(mint, 0) or 0) + 1
            self._signal_mcap_recheck_counts[mint] = tries
            cooldown = min(float(self.signal_mcap_recheck_max_s), cooldown * (2 ** max(0, tries - 1)))
        self._signal_mcap_recheck_until[mint] = time.time() + float(cooldown)
        return float(cooldown)

    def _maybe_reload_winner_zones(self, force: bool = False) -> None:
        """Reload winner-zone allowlist from disk if stale/changed."""
        if not self.winner_zone_enabled:
            return
        now = time.time()
        if not force and (now - float(self._winner_zone_last_reload or 0.0)) < float(self.winner_zone_reload_s):
            return
        self._winner_zone_last_reload = now
        path = Path(self.winner_zone_path)
        if not path.exists():
            if not self._winner_zone_loaded_once:
                self._winner_zone_loaded_once = True
                console.print(
                    f"[yellow]Winner zones not found: {path}. "
                    "Build with scripts/meme_winner_zone_builder.py[/yellow]"
                )
            self._winner_zones = []
            self._winner_zone_mtime = 0.0
            return
        try:
            mtime = float(path.stat().st_mtime)
        except Exception:
            mtime = 0.0
        if (not force) and self._winner_zones and mtime > 0 and mtime == float(self._winner_zone_mtime or 0.0):
            return
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
            zones_raw = obj.get("zones") if isinstance(obj, dict) else None
            if not isinstance(zones_raw, list):
                raise ValueError("winner zones missing `zones` list")
            zones: list[dict[str, Any]] = []
            for z in zones_raw:
                if not isinstance(z, dict):
                    continue
                zid = str(z.get("id") or "").strip()
                if not zid:
                    continue
                zone = dict(z)
                try:
                    zone["objective"] = float(zone.get("objective") or 0.0)
                except Exception:
                    zone["objective"] = 0.0
                zones.append(zone)
            zones.sort(key=lambda x: float(x.get("objective") or 0.0), reverse=True)
            self._winner_zones = zones
            self._winner_zone_mtime = mtime
            self._winner_zone_loaded_once = True
            console.print(f"[cyan]Winner zones loaded: {path} ({len(zones)} zones)[/cyan]")
        except Exception as e:
            self._winner_zones = []
            self._winner_zone_mtime = 0.0
            console.print(f"[yellow]Winner zone load failed: {e}[/yellow]")

    @staticmethod
    def _winner_zone_match_range(value: float, lo: float | None, hi: float | None) -> bool:
        try:
            v = float(value)
        except Exception:
            return False
        if lo is not None:
            try:
                if v < float(lo):
                    return False
            except Exception:
                return False
        if hi is not None:
            try:
                if v >= float(hi):
                    return False
            except Exception:
                return False
        return True

    def _winner_zone_match(
        self,
        *,
        score: float,
        net_sol_in: float,
        top_buyer_share: float | None,
        mcap: float,
    ) -> tuple[str, float] | None:
        """Return (zone_id, objective) when signal metrics are inside any winner zone."""
        if not self.winner_zone_enabled:
            return None
        self._maybe_reload_winner_zones()
        if not self._winner_zones:
            return None

        top_missing = top_buyer_share is None
        top_v = -1.0 if top_missing else float(top_buyer_share)
        mcap_missing = float(mcap) <= 0.0
        for z in self._winner_zones:
            try:
                n = int(z.get("n") or 0)
            except Exception:
                n = 0
            if self.winner_zone_min_n > 0 and n < int(self.winner_zone_min_n):
                continue

            score_r = z.get("score") if isinstance(z.get("score"), dict) else {}
            net_r = z.get("net_sol_in") if isinstance(z.get("net_sol_in"), dict) else {}
            top_r = z.get("top_buyer_share") if isinstance(z.get("top_buyer_share"), dict) else {}
            mcap_r = z.get("mcap") if isinstance(z.get("mcap"), dict) else {}

            if not self._winner_zone_match_range(score, score_r.get("lo"), score_r.get("hi")):
                continue
            if not self._winner_zone_match_range(net_sol_in, net_r.get("lo"), net_r.get("hi")):
                continue
            if not top_missing or self.winner_zone_require_top_share:
                if not self._winner_zone_match_range(top_v, top_r.get("lo"), top_r.get("hi")):
                    continue
            if (not mcap_missing) or (not self.winner_zone_match_allow_unknown_mcap):
                if not self._winner_zone_match_range(mcap, mcap_r.get("lo"), mcap_r.get("hi")):
                    continue

            zid = str(z.get("id") or "").strip()
            if not zid:
                continue
            try:
                obj = float(z.get("objective") or 0.0)
            except Exception:
                obj = 0.0
            return zid, obj
        return None

    def _winner_zone_bypass_ok(
        self,
        *,
        score: float,
        hits: int,
        unique_buyers: int,
        net_sol_in: float,
        top_buyer_share: float | None,
        mcap: float,
    ) -> tuple[bool, dict[str, float | int | bool]]:
        """Evaluate controlled winner-zone bypass for strong prequote momentum."""
        top_ok = (
            top_buyer_share is None
            or self.winner_zone_bypass_max_top_buyer_share <= 0
            or float(top_buyer_share) <= float(self.winner_zone_bypass_max_top_buyer_share)
        )
        mcap_ok = (
            (float(mcap) >= float(self.winner_zone_bypass_min_mcap_usd))
            or (self.winner_zone_bypass_allow_unknown_mcap and float(mcap) <= 0.0)
        )
        ok = (
            float(score) >= float(self.winner_zone_bypass_min_signal_score)
            and int(hits) >= int(self.winner_zone_bypass_min_hits)
            and int(unique_buyers) >= int(self.winner_zone_bypass_min_unique_buyers)
            and float(net_sol_in) >= float(self.winner_zone_bypass_min_net_sol_in)
            and bool(mcap_ok)
            and bool(top_ok)
        )
        return ok, {
            "score": float(score),
            "hits": int(hits),
            "unique_buyers": int(unique_buyers),
            "net_sol_in": float(net_sol_in),
            "top_buyer_share": float(top_buyer_share) if top_buyer_share is not None else -1.0,
            "mcap": float(mcap),
            "top_ok": bool(top_ok),
            "mcap_ok": bool(mcap_ok),
            "min_score": float(self.winner_zone_bypass_min_signal_score),
            "min_hits": int(self.winner_zone_bypass_min_hits),
            "min_unique_buyers": int(self.winner_zone_bypass_min_unique_buyers),
            "min_net_sol_in": float(self.winner_zone_bypass_min_net_sol_in),
            "min_mcap": float(self.winner_zone_bypass_min_mcap_usd),
            "allow_unknown_mcap": bool(self.winner_zone_bypass_allow_unknown_mcap),
            "max_top_buyer_share": float(self.winner_zone_bypass_max_top_buyer_share),
        }

    def _maybe_reload_winner_profile(self, force: bool = False) -> None:
        """Reload winner profile JSON from disk if stale/changed."""
        if not self.winner_profile_enabled:
            return
        now = time.time()
        if not force and (now - float(self._winner_profile_last_reload or 0.0)) < float(self.winner_profile_reload_s):
            return
        self._winner_profile_last_reload = now
        path = Path(self.winner_profile_path)
        if not path.exists():
            if not self._winner_profile_loaded_once:
                self._winner_profile_loaded_once = True
                console.print(
                    f"[yellow]Winner profile not found: {path}. "
                    "Create one with scripts/meme_winner_profile.py[/yellow]"
                )
            self._winner_profile = None
            self._winner_profile_mtime = 0.0
            return
        try:
            mtime = float(path.stat().st_mtime)
        except Exception:
            mtime = 0.0
        if (not force) and self._winner_profile and mtime > 0 and mtime == float(self._winner_profile_mtime or 0.0):
            return
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
            feats = obj.get("features") if isinstance(obj, dict) else None
            if not isinstance(feats, dict) or not feats:
                raise ValueError("winner profile missing non-empty `features` dict")
            self._winner_profile = obj
            self._winner_profile_mtime = mtime
            self._winner_profile_loaded_once = True
            if not self.winner_min_score_env_set:
                try:
                    rec = float(obj.get("recommended_min_score") or 0.0)
                    if rec > 0:
                        self.winner_min_score = rec
                except Exception:
                    pass
            console.print(
                f"[cyan]Winner profile loaded: {path} ({len(feats)} features) "
                f"min_score={self.winner_min_score:.1f}[/cyan]"
            )
        except Exception as e:
            self._winner_profile = None
            self._winner_profile_mtime = 0.0
            console.print(f"[yellow]Winner profile load failed: {e}[/yellow]")

    def _winner_feature_values(self, candidate: TokenCandidate) -> dict[str, float]:
        """Build winner-feature vector from candidate + launch metrics."""
        values: dict[str, float] = {}

        def _put(k: str, v) -> None:
            try:
                fv = float(v)
            except Exception:
                return
            if math.isfinite(fv):
                values[k] = fv

        _put("market_cap", candidate.market_cap)
        _put("liquidity", candidate.liquidity)
        _put("price_change_5m", candidate.price_change_5m)
        _put("txns_5m", candidate.txns_5m)
        _put("volume_5m", candidate.volume_5m)
        _put("buys_5m", candidate.buys_5m)
        _put("sells_5m", candidate.sells_5m)

        buys_5m = float(candidate.buys_5m or 0)
        sells_5m = float(candidate.sells_5m or 0)
        if buys_5m > 0 or sells_5m > 0:
            _put("buy_sell_ratio_5m", (buys_5m + 1.0) / (sells_5m + 1.0))
            _put("sell_share_5m", sells_5m / max(1.0, buys_5m + sells_5m))

        metrics = self.launch_signal_metrics.get(candidate.mint, {}) if self.launch_signals_file else {}
        if isinstance(metrics, dict) and metrics:
            _put("signal_score", self.launch_signal_scores.get(candidate.mint, 0.0))
            _put("signal_hits", metrics.get("hits"))
            _put("signal_buys", metrics.get("buys"))
            _put("signal_sells", metrics.get("sells"))
            _put("signal_unique_buyers", metrics.get("unique_buyers"))
            _put("signal_net_sol_in", metrics.get("net_sol_in"))
            _put("signal_buy_accel", metrics.get("buy_accel"))
            _put("signal_top_buyer_share", metrics.get("top_buyer_share"))
            _put("signal_t_first_sell_s", metrics.get("t_first_sell_s"))
            try:
                sb = float(metrics.get("buys") or 0.0)
                ss = float(metrics.get("sells") or 0.0)
                if sb > 0 or ss > 0:
                    _put("signal_buy_sell_ratio", (sb + 1.0) / (ss + 1.0))
                    _put("signal_sell_share", ss / max(1.0, sb + ss))
            except Exception:
                pass
        return values

    def _check_signal_core_metrics(self, candidate: TokenCandidate, metrics: dict) -> tuple[bool, dict]:
        """Validate core metrics required for high-integrity signal-first entries."""
        details: dict[str, float | int | str | list] = {}
        missing: list[str] = []
        weak: list[str] = []
        metrics = metrics if isinstance(metrics, dict) else {}

        liq = float(getattr(candidate, "liquidity", 0.0) or 0.0)
        mcap = float(getattr(candidate, "market_cap", 0.0) or 0.0)
        sscore = float(self.launch_signal_scores.get(candidate.mint, 0.0) or 0.0)
        hits = metrics.get("hits")
        uniq = metrics.get("unique_buyers")
        net_in = metrics.get("net_sol_in")

        if self.signal_core_require_liquidity and liq <= 0:
            missing.append("liquidity")
        if self.signal_core_require_mcap_prequote and mcap <= 0:
            missing.append("market_cap")
        if sscore <= 0:
            missing.append("signal_score")

        if hits is None:
            missing.append("signal_hits")
        if uniq is None:
            missing.append("signal_unique_buyers")
        if net_in is None:
            missing.append("signal_net_sol_in")

        try:
            hits_v = int(hits) if hits is not None else 0
        except Exception:
            hits_v = 0
            weak.append("signal_hits_parse")
        try:
            uniq_v = int(uniq) if uniq is not None else 0
        except Exception:
            uniq_v = 0
            weak.append("signal_unique_buyers_parse")
        try:
            net_v = float(net_in) if net_in is not None else 0.0
        except Exception:
            net_v = 0.0
            weak.append("signal_net_sol_in_parse")

        if self.signal_core_require_positive:
            if sscore <= 0:
                weak.append("signal_score_nonpositive")
            if hits_v <= 0:
                weak.append("signal_hits_nonpositive")
            if uniq_v <= 0:
                weak.append("signal_unique_buyers_nonpositive")
        if hits_v < int(self.signal_core_min_hits):
            weak.append("signal_hits_below_min")
        if uniq_v < int(self.signal_core_min_unique_buyers):
            weak.append("signal_unique_buyers_below_min")
        if net_v < float(self.signal_core_min_net_sol_in):
            weak.append("signal_net_sol_in_below_min")

        details["missing"] = missing
        details["weak"] = weak
        details["score"] = sscore
        details["hits"] = hits_v
        details["unique_buyers"] = uniq_v
        details["net_sol_in"] = net_v
        details["liq"] = liq
        details["mcap"] = mcap
        details["require_liquidity"] = bool(self.signal_core_require_liquidity)
        ok = not missing and not weak
        return ok, details

    def _estimate_signal_liquidity(self, metrics: dict) -> float:
        """Estimate liquidity from launch-signal demand when liquidity field is missing."""
        if not self.signal_liq_fallback_enabled:
            return 0.0
        metrics = metrics if isinstance(metrics, dict) else {}
        try:
            hits = int(metrics.get("hits") or 0)
        except Exception:
            hits = 0
        try:
            uniq = int(metrics.get("unique_buyers") or 0)
        except Exception:
            uniq = 0
        try:
            net_sol_in = float(metrics.get("net_sol_in") or 0.0)
        except Exception:
            net_sol_in = 0.0
        if hits < int(self.signal_liq_fallback_min_hits):
            return 0.0
        if uniq < int(self.signal_liq_fallback_min_unique_buyers):
            return 0.0
        if net_sol_in < float(self.signal_liq_fallback_min_net_sol_in):
            return 0.0

        est = max(0.0, net_sol_in) * max(0.0, float(self.signal_liq_fallback_usd_per_sol))
        if uniq > int(self.signal_liq_fallback_min_unique_buyers):
            extra_buyers = uniq - int(self.signal_liq_fallback_min_unique_buyers)
            est += min(
                float(self.signal_liq_fallback_buyer_bonus_cap_usd),
                float(extra_buyers) * float(self.signal_liq_fallback_buyer_bonus_usd),
            )

        try:
            top_share = float(metrics.get("top_buyer_share") or 0.0)
        except Exception:
            top_share = 0.0
        if top_share >= 0.75:
            est *= 0.55
        elif top_share >= 0.60:
            est *= 0.75

        try:
            buy_max_sol = float(metrics.get("buy_max_sol") or 0.0)
        except Exception:
            buy_max_sol = 0.0
        if buy_max_sol >= 6.0:
            est *= 0.80

        try:
            buys = int(metrics.get("buys") or 0)
            sells = int(metrics.get("sells") or 0)
        except Exception:
            buys = sells = 0
        if sells > buys and (buys + sells) > 0:
            est *= 0.85

        min_usd = max(0.0, float(self.signal_liq_fallback_min_usd))
        max_usd = max(min_usd, float(self.signal_liq_fallback_max_usd))
        est = max(min_usd, min(max_usd, est))
        return float(est)

    def _maybe_apply_signal_liquidity_fallback(self, candidate: TokenCandidate, metrics: dict, context: str) -> float:
        """Apply estimated liquidity to candidate when explicit liquidity is missing."""
        try:
            liq_now = float(getattr(candidate, "liquidity", 0.0) or 0.0)
        except Exception:
            liq_now = 0.0
        if liq_now > 0:
            return liq_now
        est = float(self._estimate_signal_liquidity(metrics))
        if est <= 0:
            return 0.0
        candidate.liquidity = est
        candidate.liquidity_estimated = True
        self._signal_debug_write(
            "liquidity_fallback",
            candidate,
            {
                "context": context,
                "estimated_liq": est,
                "min_liq": float(self.signal_liq_fallback_min_usd),
                "hits": metrics.get("hits"),
                "unique_buyers": metrics.get("unique_buyers"),
                "net_sol_in": metrics.get("net_sol_in"),
            },
        )
        return est

    def _score_winner_profile(self, candidate: TokenCandidate) -> tuple[float, int]:
        """Return winner-profile score (0..100) and number of features used."""
        candidate.winner_score = 0.0
        candidate.winner_features_used = 0
        if not self.winner_profile_enabled:
            return 0.0, 0
        self._maybe_reload_winner_profile()
        profile = self._winner_profile or {}
        feats = profile.get("features") if isinstance(profile, dict) else None
        if not isinstance(feats, dict) or not feats:
            return 0.0, 0

        values = self._winner_feature_values(candidate)
        weighted_sum = 0.0
        weight_total = 0.0
        used = 0
        for name, spec in feats.items():
            if not isinstance(spec, dict):
                continue
            if name not in values:
                continue
            raw = values.get(name)
            try:
                val = float(raw)
            except Exception:
                continue
            if not math.isfinite(val):
                continue
            # p10/p90 bounds are robust against heavy tails in meme-coin features.
            lo = spec.get("p10", spec.get("min"))
            hi = spec.get("p90", spec.get("max"))
            try:
                lo_f = float(lo)
                hi_f = float(hi)
            except Exception:
                continue
            if not (math.isfinite(lo_f) and math.isfinite(hi_f)):
                continue
            if hi_f <= lo_f:
                hi_f = lo_f + 1e-9

            direction = str(spec.get("direction", "high") or "high").strip().lower()
            if direction in ("low", "lower", "min", "minimize"):
                score01 = (hi_f - val) / (hi_f - lo_f)
            else:
                score01 = (val - lo_f) / (hi_f - lo_f)
            score01 = max(0.0, min(1.0, float(score01)))

            try:
                w = float(spec.get("weight", 1.0) or 1.0)
            except Exception:
                w = 1.0
            if w <= 0:
                continue
            weighted_sum += score01 * w
            weight_total += w
            used += 1

        if weight_total <= 0 or used <= 0:
            return 0.0, 0

        out = float(weighted_sum / weight_total) * 100.0
        out = max(0.0, min(100.0, out))
        candidate.winner_score = out
        candidate.winner_features_used = used
        return out, used

    def _winner_size_multiplier(self, candidate: TokenCandidate) -> float:
        """Map winner score to a position-size multiplier."""
        if not self.winner_size_enabled:
            return 1.0
        used = int(getattr(candidate, "winner_features_used", 0) or 0)
        if used <= 0:
            return 1.0
        score = float(getattr(candidate, "winner_score", 0.0) or 0.0)
        center = float(self.winner_size_score_center)
        span = max(1.0, float(self.winner_size_score_span))
        # Linear map around center:
        # score=center => 1.0x, score=center+span => max_mult, score=center-span => min_mult
        t = (score - center) / span
        mult = 1.0 + t * (float(self.winner_size_max_mult) - 1.0)
        mult = max(float(self.winner_size_min_mult), min(float(self.winner_size_max_mult), float(mult)))
        return float(mult)

    def _discover_from_launch_signals(self) -> list[TokenCandidate]:
        """Create candidates from the in-memory launch signal hot list.

        This is the fast path: only evaluate mints that are in `launch_signal_mints`.
        A small cooldown prevents repeated evaluation hammering external APIs.
        """
        if not self.launch_signals_file:
            return []
        cutoff = time.time() - self.launch_signal_ttl
        now = time.time()
        out: list[TokenCandidate] = []

        # Start from newest signals, but rotate the start index each tick so older active
        # mints are not permanently starved when max candidates per tick is capped.
        ordered = sorted(self.launch_signal_mints.items(), key=lambda kv: kv[1], reverse=True)
        n_ordered = len(ordered)
        if n_ordered > 1:
            try:
                start = int(self._signal_rr_cursor or 0) % n_ordered
            except Exception:
                start = 0
            if start:
                ordered = ordered[start:] + ordered[:start]
            self._signal_rr_cursor = (start + 1) % n_ordered
        else:
            self._signal_rr_cursor = 0

        for mint, ts in ordered:
            if ts < cutoff:
                continue
            if not mint or mint in EXCLUDED_TOKENS:
                continue
            if self.signal_source_allowlist:
                src = (self.launch_signal_metrics.get(mint, {}) or {}).get("source")
                if src not in self.signal_source_allowlist:
                    continue
            if mint in self.launch_signal_seen:
                continue
            fail_until = self._signal_quote_fail_until.get(mint)
            if fail_until and now < fail_until:
                continue
            entry_until = self._entry_reject_until.get(mint)
            if entry_until and now < entry_until:
                continue
            mcap_until = self._signal_mcap_recheck_until.get(mint)
            if mcap_until and now < mcap_until:
                continue
            last = self._signal_last_attempt.get(mint)
            if last and (now - last) < self.signal_eval_cooldown:
                continue
            self._signal_last_attempt[mint] = now
            first_seen = float(self.launch_signal_first_seen.get(mint, ts) or ts)
            c = TokenCandidate(mint=mint, discovered_at=float(first_seen))
            # We often won't have a symbol this early; keep logs readable.
            c.symbol = (self.launch_signal_metrics.get(mint, {}) or {}).get("symbol") or mint[:4]
            out.append(c)

            if self.signal_max_candidates_per_tick and len(out) >= self.signal_max_candidates_per_tick:
                break

        return out

    async def _get_mint_decimals(self, mint: str) -> int | None:
        """Fetch token decimals via RPC (cached)."""
        if mint in self._mint_decimals:
            return self._mint_decimals[mint]
        try:
            result = await asyncio.to_thread(self.rpc_pool.call, "getTokenSupply", [mint])
            v = ((result or {}).get("value") or {})
            dec = int(v.get("decimals"))
            self._mint_decimals[mint] = dec
            # Cache supply_ui from the same RPC response so we can estimate market cap
            # in signal-first mode without additional RPC calls.
            ui = v.get("uiAmount")
            if ui is not None:
                try:
                    self._mint_supply_ui[mint] = float(ui)
                except Exception:
                    pass
            else:
                s = v.get("uiAmountString")
                if s is not None:
                    try:
                        self._mint_supply_ui[mint] = float(str(s))
                    except Exception:
                        pass
            return dec
        except RpcError:
            return None
        except Exception:
            return None

    async def _fetch_signal_quote_data(
        self,
        candidate: TokenCandidate,
        *,
        purpose: str = "candidate",
        amount_sol_override: float | None = None,
        side: str = "buy",
        token_amount_override: float | None = None,
    ) -> None:
        """Fill price and impact using Jupiter quotes (signal-first path).

        purpose:
          - "candidate": entry probing / discovery
          - "position": active position monitoring (reserved budget)
        side:
          - "buy": WSOL -> token quote (entry-side pricing)
          - "sell": token -> WSOL quote (exit-side pricing)
        """
        try:
            now = time.time()
            if self._jup_cooldown_until and now < self._jup_cooldown_until:
                return
            if self.jup_max_calls_per_min > 0:
                reserved = max(0, min(int(self.jup_reserved_pos_calls_per_min), int(self.jup_max_calls_per_min)))
                cand_budget = max(0, int(self.jup_max_calls_per_min) - reserved)

                if purpose == "position":
                    if (now - self._jup_pos_window_start) >= 60:
                        self._jup_pos_window_start = now
                        self._jup_pos_calls_in_window = 0
                    if self._jup_pos_calls_in_window >= reserved:
                        return
                else:
                    if (now - self._jup_window_start) >= 60:
                        self._jup_window_start = now
                        self._jup_calls_in_window = 0
                    if self._jup_calls_in_window >= cand_budget:
                        return

            side = str(side or "buy").strip().lower()
            if side not in ("buy", "sell"):
                side = "buy"

            # Default buy-quote size is tiny (discovery/monitoring). For entries we may override
            # this to the intended position size to avoid "looks liquid at 0.002 SOL" traps.
            amount_lamports = 0
            params: dict[str, str]
            if side == "sell":
                token_amt = float(token_amount_override or 0.0)
                if token_amt <= 0:
                    if purpose != "position" and self.signal_quote_fail_cooldown_s > 0:
                        self._signal_quote_fail_until[candidate.mint] = time.time() + float(self.signal_quote_fail_cooldown_s)
                    return
                dec = await self._get_mint_decimals(candidate.mint)
                if dec is None:
                    if purpose != "position" and self.signal_quote_fail_cooldown_s > 0:
                        self._signal_quote_fail_until[candidate.mint] = time.time() + float(self.signal_quote_fail_cooldown_s)
                    return
                try:
                    token_atomic = int(max(1, round(token_amt * (10 ** int(dec)))))
                except Exception:
                    token_atomic = 0
                if token_atomic <= 0:
                    if purpose != "position" and self.signal_quote_fail_cooldown_s > 0:
                        self._signal_quote_fail_until[candidate.mint] = time.time() + float(self.signal_quote_fail_cooldown_s)
                    return
                params = {
                    "inputMint": candidate.mint,
                    "outputMint": WSOL_MINT,
                    "amount": str(token_atomic),
                    "slippageBps": "50",
                }
            else:
                q_sol = self.signal_quote_sol
                if amount_sol_override is not None:
                    try:
                        q_sol = float(amount_sol_override)
                    except Exception:
                        q_sol = self.signal_quote_sol
                amount_lamports = int(max(0.001, q_sol) * 1e9)
                params = {
                    "inputMint": WSOL_MINT,
                    "outputMint": candidate.mint,
                    "amount": str(amount_lamports),
                    "slippageBps": "50",
                }
            jupiter_key = os.getenv('JUPITER_API_KEY') or os.getenv('JUPITER_KEY')
            headers = {"x-api-key": jupiter_key} if jupiter_key else None
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(getattr(te, "JUPITER_QUOTE", "https://api.jup.ag/swap/v1/quote"), params=params, headers=headers)
                if self.jup_max_calls_per_min > 0 and purpose == "position":
                    self._jup_pos_calls_in_window += 1
                else:
                    self._jup_calls_in_window += 1
                if resp.status_code == 429:
                    self._jup_backoff_s = min(120.0, (self._jup_backoff_s * 2.0) if self._jup_backoff_s else 5.0)
                    self._jup_cooldown_until = time.time() + self._jup_backoff_s
                    return
                if resp.status_code >= 500:
                    self._jup_backoff_s = min(60.0, (self._jup_backoff_s * 1.5) if self._jup_backoff_s else 2.0)
                    self._jup_cooldown_until = time.time() + self._jup_backoff_s
                    return
                if resp.status_code != 200:
                    if purpose != "position" and self.signal_quote_fail_cooldown_s > 0:
                        self._signal_quote_fail_until[candidate.mint] = time.time() + float(self.signal_quote_fail_cooldown_s)
                    return
                quote = resp.json()
            self._jup_backoff_s = 0.0
            out_amt = quote.get("outAmount")
            if not out_amt:
                if purpose != "position" and self.signal_quote_fail_cooldown_s > 0:
                    self._signal_quote_fail_until[candidate.mint] = time.time() + float(self.signal_quote_fail_cooldown_s)
                return
            if side == "sell":
                token_amt = float(token_amount_override or 0.0)
                if token_amt <= 0:
                    if purpose != "position" and self.signal_quote_fail_cooldown_s > 0:
                        self._signal_quote_fail_until[candidate.mint] = time.time() + float(self.signal_quote_fail_cooldown_s)
                    return
                out_sol = float(out_amt) / 1e9
                if out_sol <= 0:
                    if purpose != "position" and self.signal_quote_fail_cooldown_s > 0:
                        self._signal_quote_fail_until[candidate.mint] = time.time() + float(self.signal_quote_fail_cooldown_s)
                    return
                price_sol = out_sol / token_amt
            else:
                dec = await self._get_mint_decimals(candidate.mint)
                if dec is None:
                    if purpose != "position" and self.signal_quote_fail_cooldown_s > 0:
                        self._signal_quote_fail_until[candidate.mint] = time.time() + float(self.signal_quote_fail_cooldown_s)
                    return
                out_tokens = float(out_amt) / (10 ** dec)
                if out_tokens <= 0:
                    if purpose != "position" and self.signal_quote_fail_cooldown_s > 0:
                        self._signal_quote_fail_until[candidate.mint] = time.time() + float(self.signal_quote_fail_cooldown_s)
                    return
                in_sol = amount_lamports / 1e9
                price_sol = in_sol / out_tokens
            sol_usd = await self._get_sol_price()
            candidate.price = float(price_sol) * float(sol_usd)
            if purpose != "position":
                # Quote succeeded, clear any previous mint-level quote cooldown.
                self._signal_quote_fail_until.pop(candidate.mint, None)
            try:
                candidate.price_impact_pct = float(quote.get("priceImpactPct", 0) or 0)
            except Exception:
                candidate.price_impact_pct = 0.0
            # Market cap estimate for signal-first mode (supply via cached getTokenSupply).
            try:
                if candidate.market_cap <= 0:
                    su = self._mint_supply_ui.get(candidate.mint)
                    if su and candidate.price > 0:
                        candidate.market_cap = float(su) * float(candidate.price)
            except Exception:
                pass

            # Local momentum estimate from last quote sample (not true market 5m).
            prev = self.prev_candidate_state.get(candidate.mint) or {}
            prev_p = float(prev.get("price", 0) or 0)
            prev_t = float(prev.get("ts", 0) or 0)
            if prev_p > 0 and prev_t > 0 and (time.time() - prev_t) <= 300:
                candidate.price_change_5m = ((candidate.price / prev_p) - 1.0) * 100.0
            else:
                # Keep previously hydrated market momentum (from signal/Dex payloads)
                # when we do not yet have two quote points to derive local drift.
                try:
                    candidate.price_change_5m = float(getattr(candidate, "price_change_5m", 0.0) or 0.0)
                except Exception:
                    candidate.price_change_5m = 0.0
            self.prev_candidate_state[candidate.mint] = {
                **prev,
                "price": candidate.price,
                "ts": time.time(),
                "liquidity": candidate.liquidity,
                "volume_5m": candidate.volume_5m,
            }
        except Exception:
            if purpose != "position" and self.signal_quote_fail_cooldown_s > 0:
                self._signal_quote_fail_until[candidate.mint] = time.time() + float(self.signal_quote_fail_cooldown_s)
            return

    async def _confirm_signal_entry(self, candidate: TokenCandidate) -> bool:
        """Multi-sample quote confirmation for signal-first entries.

        Goal: avoid entering illiquid/noise listings. We require a short burst
        of upward drift with non-worsening impact.
        """
        if not self.confirm_enabled:
            return True
        n = max(2, self.confirm_samples)
        interval = max(1.0, self.confirm_interval_s)

        prices: list[float] = []
        impacts: list[float] = []

        for i in range(n):
            await self._fetch_signal_quote_data(candidate)
            if candidate.price <= 0:
                if self.signal_first and self.launch_signals_file:
                    self._signal_debug_write("reject_confirm_quote", candidate, {"i": i, "n": n})
                return False
            prices.append(float(candidate.price))
            impacts.append(float(candidate.price_impact_pct or 0.0))
            if i < (n - 1):
                await asyncio.sleep(interval)

        # Up-ticks count
        up = 0
        for i in range(1, len(prices)):
            if prices[i] > prices[i - 1] * 1.0005:
                up += 1

        total_up_pct = ((prices[-1] / prices[0]) - 1.0) * 100.0 if prices[0] > 0 else 0.0
        impact_worsen = impacts[-1] - impacts[0]

        if up < self.confirm_require_up_ticks:
            if self.signal_first and self.launch_signals_file:
                self._signal_debug_write(
                    "reject_confirm_up_ticks",
                    candidate,
                    {"up": up, "need": self.confirm_require_up_ticks, "total_up_pct": round(total_up_pct, 3)},
                )
            return False
        if total_up_pct < self.confirm_min_up_pct:
            if self.signal_first and self.launch_signals_file:
                self._signal_debug_write(
                    "reject_confirm_min_up",
                    candidate,
                    {"total_up_pct": round(total_up_pct, 3), "min_up_pct": float(self.confirm_min_up_pct)},
                )
            return False
        if impact_worsen > self.confirm_max_impact_worsen:
            if self.signal_first and self.launch_signals_file:
                self._signal_debug_write(
                    "reject_confirm_impact_worsen",
                    candidate,
                    {"impact_worsen": round(float(impact_worsen), 4), "max_worsen": float(self.confirm_max_impact_worsen)},
                )
            return False

        return True

    async def _check_entry_sellability(self, candidate: TokenCandidate, size_sol: float) -> tuple[bool, str]:
        """Probe round-trip routeability (SOL->token->SOL) before entry.

        This is a practical honeypot/untradable guard: if we cannot get a sell quote
        back to SOL for the bought amount, we should not enter.
        """
        if not self.entry_sellability_check_enabled:
            return True, ""
        try:
            probe_sol = float(self.entry_sellability_probe_sol)
        except Exception:
            probe_sol = 0.0
        probe_sol = probe_sol if probe_sol > 0 else float(size_sol or 0.0)
        probe_sol = max(0.001, probe_sol)
        in_lamports = int(probe_sol * 1e9)
        cache_key = f"{candidate.mint}:{probe_sol:.4f}"
        now = time.time()
        hit = self._entry_sellability_cache.get(cache_key)
        if hit:
            ts, ok, reason, back_pct, sell_impact = hit
            if (now - float(ts)) <= float(self.entry_sellability_cache_s):
                if not ok:
                    return False, reason
                return True, f"back={back_pct:.2%} impact={sell_impact:.3f}"

        params_buy = {
            "inputMint": WSOL_MINT,
            "outputMint": candidate.mint,
            "amount": str(in_lamports),
            "slippageBps": str(max(10, int(self.entry_sellability_slippage_bps))),
        }
        jupiter_key = os.getenv("JUPITER_API_KEY") or os.getenv("JUPITER_KEY")
        headers = {"x-api-key": jupiter_key} if jupiter_key else None
        endpoint = getattr(te, "JUPITER_QUOTE", "https://api.jup.ag/swap/v1/quote")

        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                buy_resp = await client.get(endpoint, params=params_buy, headers=headers)
                if buy_resp.status_code != 200:
                    reason = f"sellability buy-quote status {buy_resp.status_code}"
                    self._entry_sellability_cache[cache_key] = (now, False, reason, 0.0, 0.0)
                    return False, reason
                buy_quote = buy_resp.json() or {}
                buy_out_raw = buy_quote.get("outAmount")
                if not buy_out_raw:
                    reason = "sellability no buy route"
                    self._entry_sellability_cache[cache_key] = (now, False, reason, 0.0, 0.0)
                    return False, reason
                try:
                    buy_out_atomic = int(str(buy_out_raw))
                except Exception:
                    buy_out_atomic = 0
                if buy_out_atomic <= 0:
                    reason = "sellability empty buy outAmount"
                    self._entry_sellability_cache[cache_key] = (now, False, reason, 0.0, 0.0)
                    return False, reason

                params_sell = {
                    "inputMint": candidate.mint,
                    "outputMint": WSOL_MINT,
                    "amount": str(buy_out_atomic),
                    "slippageBps": str(max(10, int(self.entry_sellability_slippage_bps))),
                }
                sell_resp = await client.get(endpoint, params=params_sell, headers=headers)
                if sell_resp.status_code != 200:
                    reason = f"sellability sell-quote status {sell_resp.status_code}"
                    self._entry_sellability_cache[cache_key] = (now, False, reason, 0.0, 0.0)
                    return False, reason
                sell_quote = sell_resp.json() or {}
                sell_out_raw = sell_quote.get("outAmount")
                if not sell_out_raw:
                    reason = "sellability no sell route"
                    self._entry_sellability_cache[cache_key] = (now, False, reason, 0.0, 0.0)
                    return False, reason
                try:
                    sell_out_lamports = int(str(sell_out_raw))
                except Exception:
                    sell_out_lamports = 0
                if sell_out_lamports <= 0:
                    reason = "sellability empty sell outAmount"
                    self._entry_sellability_cache[cache_key] = (now, False, reason, 0.0, 0.0)
                    return False, reason

                back_pct = float(sell_out_lamports) / float(max(1, in_lamports))
                try:
                    sell_impact = float(sell_quote.get("priceImpactPct", 0) or 0.0)
                except Exception:
                    sell_impact = 0.0

                if back_pct < float(self.entry_sellability_min_back_pct):
                    reason = (
                        f"sellability weak roundtrip back={back_pct:.2%} "
                        f"< min={float(self.entry_sellability_min_back_pct):.2%}"
                    )
                    self._entry_sellability_cache[cache_key] = (now, False, reason, back_pct, sell_impact)
                    return False, reason
                if float(self.entry_sellability_max_sell_impact_pct) > 0 and sell_impact > float(
                    self.entry_sellability_max_sell_impact_pct
                ):
                    reason = (
                        f"sellability high sell impact={sell_impact:.3f} "
                        f"> max={float(self.entry_sellability_max_sell_impact_pct):.3f}"
                    )
                    self._entry_sellability_cache[cache_key] = (now, False, reason, back_pct, sell_impact)
                    return False, reason

                self._entry_sellability_cache[cache_key] = (now, True, "", back_pct, sell_impact)
                return True, f"back={back_pct:.2%} impact={sell_impact:.3f}"
        except Exception as e:
            reason = f"sellability check error: {e}"
            self._entry_sellability_cache[cache_key] = (now, False, reason, 0.0, 0.0)
            return False, reason

    def _entry_pattern_level_index(self, mcap: float) -> int:
        levels = self.entry_pattern_levels if self.entry_pattern_levels else self.mcap_levels
        idx = 0
        for lv in levels:
            if mcap >= float(lv):
                idx += 1
            else:
                break
        return int(idx)

    def _entry_pattern_gate(self, candidate: TokenCandidate) -> tuple[bool, dict[str, Any]]:
        """Stateful entry gate for meme lifecycle patterns.

        Returns:
        - (True, details) when pattern criteria pass.
        - (False, details) when we should skip this tick.
        """
        details: dict[str, Any] = {}
        if not self.entry_pattern_gate_enabled:
            details["mode"] = "disabled"
            return True, details
        if not (self.signal_first and self.launch_signals_file):
            details["mode"] = "non_signal_flow"
            return True, details

        mint = str(getattr(candidate, "mint", "") or "")
        if not mint:
            details["mode"] = "missing_mint"
            return False, details

        try:
            mcap = float(getattr(candidate, "market_cap", 0.0) or 0.0)
        except Exception:
            mcap = 0.0
        if mcap <= 0:
            details["mode"] = "mcap_missing"
            return False, details

        now = time.time()
        st = self._entry_pattern_state.get(mint)
        if not isinstance(st, dict):
            st = {"points": []}
            self._entry_pattern_state[mint] = st

        points = st.get("points")
        if not isinstance(points, list):
            points = []
            st["points"] = points
        points.append((float(now), float(mcap)))
        lookback_s = max(60.0, float(self.entry_pattern_lookback_s))
        cutoff = float(now) - lookback_s
        points = [(t, v) for (t, v) in points if t >= cutoff and v > 0]
        st["points"] = points
        if len(points) > 512:
            st["points"] = points[-512:]
            points = st["points"]

        curr_level_idx = self._entry_pattern_level_index(mcap)
        prev_level_idx = int(st.get("last_level_idx", 0) or 0)
        if curr_level_idx > prev_level_idx:
            st["last_cross_ts"] = float(now)
            st["last_cross_from"] = int(prev_level_idx)
            st["last_cross_to"] = int(curr_level_idx)
        st["last_level_idx"] = int(curr_level_idx)

        # Shared demand checks
        metrics = self.launch_signal_metrics.get(mint, {}) if self.launch_signals_file else {}
        try:
            hits = int((metrics or {}).get("hits") or 0)
        except Exception:
            hits = 0
        try:
            uniq = int((metrics or {}).get("unique_buyers") or 0)
        except Exception:
            uniq = 0
        try:
            net = float((metrics or {}).get("net_sol_in") or 0.0)
        except Exception:
            net = 0.0
        try:
            sig_score = float(self.launch_signal_scores.get(mint, 0.0) or 0.0)
        except Exception:
            sig_score = 0.0
        try:
            mom5m = float(getattr(candidate, "price_change_5m", 0.0) or 0.0)
        except Exception:
            mom5m = 0.0

        demand_ok = (
            hits >= int(self.entry_pattern_min_hits)
            and uniq >= int(self.entry_pattern_min_uniq)
            and net >= float(self.entry_pattern_min_net_sol_in)
            and sig_score >= float(self.entry_pattern_min_signal_score)
        )
        min_level = float(self.entry_pattern_min_level)
        level_ok = mcap >= min_level

        details.update(
            {
                "mcap": float(mcap),
                "level_idx": int(curr_level_idx),
                "hits": int(hits),
                "unique_buyers": int(uniq),
                "net_sol_in": float(net),
                "signal_score": float(sig_score),
                "mom5m": float(mom5m),
                "demand_ok": bool(demand_ok),
                "level_ok": bool(level_ok),
            }
        )

        # Branch 1: impulse level transition
        impulse_ok = False
        cross_ts = float(st.get("last_cross_ts", 0.0) or 0.0)
        cross_from = int(st.get("last_cross_from", 0) or 0)
        cross_to = int(st.get("last_cross_to", 0) or 0)
        if (
            demand_ok
            and level_ok
            and cross_ts > 0
            and (float(now) - cross_ts) <= float(self.entry_pattern_impulse_window_s)
            and cross_to > cross_from
            and mom5m >= float(self.entry_pattern_impulse_min_mom5m)
        ):
            impulse_ok = True

        # Branch 2: base build + breakout around nearest level.
        base_ok = False
        base_meta: dict[str, Any] = {}
        levels = self.entry_pattern_levels if self.entry_pattern_levels else self.mcap_levels
        if levels and demand_ok and level_ok and len(points) >= 3:
            # Anchor to the nearest level at or below current mcap.
            anchor = 0.0
            for lv in levels:
                if mcap >= float(lv):
                    anchor = float(lv)
                else:
                    break
            if anchor > 0:
                band = max(0.03, float(self.entry_pattern_base_band_pct))
                lo = anchor * (1.0 - band)
                hi = anchor * (1.0 + band)
                in_band = [(t, v) for (t, v) in points if lo <= v <= hi]
                touches = len(in_band)
                base_span = (in_band[-1][0] - in_band[0][0]) if touches >= 2 else 0.0
                base_high = max((v for _, v in in_band), default=0.0)
                breakout_line = base_high * (1.0 + float(self.entry_pattern_base_breakout_pct))
                if (
                    touches >= int(self.entry_pattern_base_min_touches)
                    and base_span >= float(self.entry_pattern_base_min_span_s)
                    and base_high > 0
                    and mcap >= breakout_line
                ):
                    base_ok = True
                base_meta = {
                    "base_anchor": float(anchor),
                    "base_touches": int(touches),
                    "base_span_s": round(float(base_span), 1),
                    "base_high": float(base_high),
                    "breakout_line": float(breakout_line) if base_high > 0 else 0.0,
                }

        details.update(
            {
                "impulse_ok": bool(impulse_ok),
                "base_ok": bool(base_ok),
                **base_meta,
            }
        )

        allow = bool(impulse_ok or base_ok)
        if allow:
            last_trigger = float(st.get("last_trigger_ts", 0.0) or 0.0)
            if last_trigger > 0 and (float(now) - last_trigger) < float(self.entry_pattern_cooldown_s):
                details["mode"] = "cooldown"
                return False, details
            st["last_trigger_ts"] = float(now)
            details["mode"] = "impulse" if impulse_ok else "base_breakout"
            return True, details

        details["mode"] = "no_pattern"
        return False, details

    def _entry_pattern_clear_cooldown(self, mint: str, reason: str = "") -> None:
        """Clear pattern cooldown when an entry was not actually opened."""
        if not mint:
            return
        st = self._entry_pattern_state.get(mint)
        if not isinstance(st, dict):
            return
        if "last_trigger_ts" in st:
            st.pop("last_trigger_ts", None)
            st["last_trigger_clear_ts"] = float(time.time())
            if reason:
                st["last_trigger_clear_reason"] = str(reason)

    def _entry_reject_cooldown_for_reason(self, reason: str = "") -> float:
        r = str(reason or "").lower()
        if "holder" in r:
            return float(self.entry_reject_holder_cooldown_s)
        if "mint_freeze" in r or "mint/freeze" in r:
            return float(self.entry_reject_mint_freeze_cooldown_s)
        if "sellability" in r:
            return float(self.entry_reject_sellability_cooldown_s)
        return float(self.entry_reject_cooldown_s)

    def _set_entry_reject_cooldown(self, mint: str, reason: str = "") -> float:
        if not mint:
            return 0.0
        cooldown = max(0.0, float(self._entry_reject_cooldown_for_reason(reason)))
        if cooldown <= 0.0:
            self._entry_reject_until.pop(mint, None)
            return 0.0
        self._entry_reject_until[mint] = time.time() + cooldown
        # Keep launch-signal reuse cooldown aligned with entry reject cooldown.
        self._mark_launch_signal_reject_cooldown(mint, cooldown)
        return cooldown

    def _mark_launch_signal_reject_cooldown(self, mint: str, cooldown_s: float) -> None:
        """Project an entry-level reject cooldown into launch-signal reuse cooldown."""
        if not mint:
            return
        if not (self.signal_first and self.launch_signals_file):
            return
        try:
            rc_s = max(0.0, float(cooldown_s or 0.0))
            launch_cd = max(0.0, float(self.launch_signal_cooldown or 0.0))
            if rc_s <= 0.0 or launch_cd <= 0.0:
                return
            eff = min(rc_s, launch_cd)
            synthetic_last_used = time.time() - launch_cd + eff
            prev = float(self.launch_signal_last_used.get(mint) or 0.0)
            # Keep the longer remaining cooldown if one is already active.
            if synthetic_last_used > prev:
                self.launch_signal_last_used[mint] = synthetic_last_used
        except Exception:
            return

    async def discover_tokens(self) -> list[TokenCandidate]:
        """Fetch new Solana tokens from DexScreener API.

        Uses DexScreener's token-profiles and token-boosts endpoints
        to discover trending/new tokens.

        Returns:
            List of token candidates
        """
        candidates = []

        # Optional: ingest launch signals (event-driven hot list)
        if self.launch_signals_file:
            try:
                self._ingest_launch_signals()
            except Exception as e:
                if meme_config.VERBOSE_LOGGING:
                    console.print(f"[yellow]Launch signals ingest error: {e}[/yellow]")

        # Signal-first mode: skip mass discovery and only evaluate signaled mints.
        if self.signal_first and self.launch_signals_file:
            try:
                now = time.time()
                last = getattr(self, "_last_discovery_mode_log", 0.0) or 0.0
                if (now - last) > 60:
                    self._last_discovery_mode_log = now
                    console.print("[dim]discovery mode=signal-first (launch signals)[/dim]")
            except Exception:
                pass
            return self._discover_from_launch_signals()

        # Optional: ingest launch mints from a JSONL file (event-driven)
        if self.launch_mints_file:
            try:
                candidates.extend(self._discover_from_launch_file())
            except Exception as e:
                if meme_config.VERBOSE_LOGGING:
                    console.print(f"[yellow]Launch file discover error: {e}[/yellow]")
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                # Fetch from both endpoints for better coverage
                all_tokens = []
                use_profiles = os.getenv("MEME_DEX_USE_PROFILES", "false").strip().lower() in ("1", "true", "yes")
                use_boosts = os.getenv("MEME_DEX_USE_BOOSTS", "true").strip().lower() in ("1", "true", "yes")
                try:
                    max_profiles = int(os.getenv("MEME_DEX_PROFILES_MAX", "200") or 200)
                except Exception:
                    max_profiles = 200
                try:
                    max_boosts = int(os.getenv("MEME_DEX_BOOSTS_MAX", "200") or 200)
                except Exception:
                    max_boosts = 200
                try:
                    max_total = int(os.getenv("MEME_DEX_DISCOVERY_MAX", "300") or 300)
                except Exception:
                    max_total = 300

                # 1. Get latest token profiles (new tokens with metadata)
                if use_profiles and max_profiles > 0:
                    try:
                        resp = await client.get(DEXSCREENER_TOKEN_PROFILES)
                        if resp.status_code == 200:
                            data = resp.json()
                            if isinstance(data, list):
                                n = 0
                                for item in data:
                                    if item.get('chainId') == 'solana':
                                        all_tokens.append({
                                            'address': item.get('tokenAddress'),
                                            'source': 'profiles'
                                        })
                                        n += 1
                                        if n >= max_profiles:
                                            break
                    except Exception as e:
                        if meme_config.VERBOSE_LOGGING:
                            console.print(f"[yellow]Token profiles fetch error: {e}[/yellow]")

                # 2. Get boosted tokens (trending tokens)
                if use_boosts and max_boosts > 0:
                    try:
                        resp = await client.get(DEXSCREENER_TOKEN_BOOSTS)
                        if resp.status_code == 200:
                            data = resp.json()
                            if isinstance(data, list):
                                n = 0
                                for item in data:
                                    if item.get('chainId') == 'solana':
                                        all_tokens.append({
                                            'address': item.get('tokenAddress'),
                                            'source': 'boosts'
                                        })
                                        n += 1
                                        if n >= max_boosts:
                                            break
                    except Exception as e:
                        if meme_config.VERBOSE_LOGGING:
                            console.print(f"[yellow]Token boosts fetch error: {e}[/yellow]")

                # Process discovered tokens
                # Prefer boosted tokens first; de-dupe; cap total per cycle.
                all_tokens.sort(key=lambda x: 0 if x.get("source") == "boosts" else 1)
                seen_addrs: set[str] = set()
                processed = 0
                for token_info in all_tokens:
                    try:
                        mint = token_info.get('address', '')
                        if not mint:
                            continue
                        if mint in seen_addrs:
                            continue
                        seen_addrs.add(mint)

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
                        processed += 1
                        if max_total > 0 and processed >= max_total:
                            break

                    except Exception as e:
                        if meme_config.VERBOSE_LOGGING:
                            console.print(f"[yellow]Error processing token: {e}[/yellow]")
                        continue

        except Exception as e:
            console.print(f"[red]Error discovering tokens: {e}[/red]")

        if candidates:
            console.print(f"[green]Discovered {len(candidates)} new tokens[/green]")

        return candidates

    def _prune_recent(self, now: float) -> None:
        cutoff = now - 3600
        self.entry_timestamps = [t for t in self.entry_timestamps if t >= cutoff]
        self.recent_pnl = [(t, p) for (t, p) in self.recent_pnl if t >= cutoff]

    def _can_enter_now(self) -> bool:
        now = time.time()
        self._prune_recent(now)
        if self.loss_halt_until and now < self.loss_halt_until:
            return False
        max_entries = getattr(meme_config, "MAX_ENTRIES_PER_HOUR", 0)
        if max_entries and len(self.entry_timestamps) >= max_entries:
            return False
        max_loss = getattr(meme_config, "MAX_LOSS_PER_HOUR_USD", 0.0)
        if max_loss and self.recent_pnl:
            pnl_sum = sum(p for _, p in self.recent_pnl)
            if pnl_sum <= -abs(max_loss):
                self.loss_halt_until = now + getattr(meme_config, "LOSS_HALT_SECONDS", 1800)
                console.print(f"[yellow]LOSS HALT: -${abs(pnl_sum):.2f} last hour. Pausing {meme_config.LOSS_HALT_SECONDS}s[/yellow]")
                return False
        return True

    def _entry_pacing_allows(self, candidate: Optional[TokenCandidate] = None) -> bool:
        if self.min_seconds_between_entries <= 0 or self._last_entry_ts <= 0:
            return True
        elapsed = time.time() - float(self._last_entry_ts)
        if elapsed >= float(self.min_seconds_between_entries):
            return True
        if candidate is not None and self.signal_first and self.launch_signals_file:
            self._signal_debug_write(
                "reject_entry_spacing",
                candidate,
                {
                    "elapsed_s": round(float(elapsed), 2),
                    "min_spacing_s": float(self.min_seconds_between_entries),
                },
            )
        return False

    def _signal_tier(self, score: float) -> str:
        if score >= 10:
            return "A"
        if score >= 5:
            return "B"
        if score >= 2:
            return "C"
        return "D"

    def _discover_from_launch_file(self) -> list[TokenCandidate]:
        """Read new mints from launch mints JSONL file."""
        path = self.launch_mints_file
        if not path or not os.path.exists(path):
            return []
        out: list[TokenCandidate] = []
        try:
            with open(path, "r", encoding="utf-8") as fh:
                if self._launch_offset:
                    fh.seek(self._launch_offset)
                while True:
                    line = fh.readline()
                    if not line:
                        break
                    self._launch_offset = fh.tell()
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    mint = obj.get("mint")
                    if not mint or mint in self.seen_tokens or mint in EXCLUDED_TOKENS:
                        continue
                    self.seen_tokens.add(mint)
                    c = TokenCandidate(
                        mint=mint,
                        discovered_at=float(obj.get("ts", time.time())),
                    )
                    c.symbol = str(obj.get("symbol") or "") or mint[:4]
                    out.append(c)
        except Exception:
            return out
        return out

    def _ingest_launch_signals(self) -> None:
        """Read launch signals and update in-memory hot mint set."""
        path = self.launch_signals_file
        if not path or not os.path.exists(path):
            return
        # Recover from file truncation/rotation: if our read offset is beyond EOF,
        # rewind so new signals become visible again.
        try:
            fsize = os.path.getsize(path)
            if self._signal_offset and self._signal_offset > fsize:
                self._signal_offset = 0
        except Exception:
            pass
        cutoff = time.time() - self.launch_signal_ttl
        new_count = 0
        try:
            with open(path, "r", encoding="utf-8") as fh:
                if self._signal_offset:
                    fh.seek(self._signal_offset)
                while True:
                    line = fh.readline()
                    if not line:
                        break
                    self._signal_offset = fh.tell()
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    mint = obj.get("mint")
                    if mint:
                        ts = float(obj.get("ts", time.time()))
                        if ts >= cutoff:
                            self.launch_signal_mints[mint] = ts
                            # first_seen is the stable age anchor; preserve earliest known timestamp.
                            try:
                                first_seen = float(obj.get("first_seen", ts) or ts)
                            except Exception:
                                first_seen = ts
                            prev_first = self.launch_signal_first_seen.get(mint)
                            if prev_first is None:
                                self.launch_signal_first_seen[mint] = first_seen
                            else:
                                self.launch_signal_first_seen[mint] = min(float(prev_first), float(first_seen))
                            try:
                                self.launch_signal_scores[mint] = float(obj.get("score", 0) or 0)
                            except Exception:
                                self.launch_signal_scores[mint] = 0.0
                            if isinstance(obj.get("metrics"), dict):
                                sm = normalize_signal_metrics(obj.get("metrics") or {})
                                rid = str(obj.get("run_id") or "").strip()
                                if rid:
                                    sm["run_id"] = rid
                                self.launch_signal_metrics[mint] = sm
                            new_count += 1
        except Exception:
            return

        # prune expired signals
        for mint, ts in list(self.launch_signal_mints.items()):
            if ts < cutoff:
                self.launch_signal_mints.pop(mint, None)
                self.launch_signal_scores.pop(mint, None)
                self.launch_signal_metrics.pop(mint, None)
                self.launch_signal_first_seen.pop(mint, None)
                self._signal_age_checkpoint_idx.pop(mint, None)
                self.launch_signal_seen.discard(mint)

        now = time.time()
        if now - self._last_signal_log > 60:
            self._last_signal_log = now
            console.print(
                f"[cyan]Launch signals: new={new_count} active={len(self.launch_signal_mints)} seen={len(self.launch_signal_seen)}[/cyan]"
            )

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
            def _reject(reason: str) -> bool:
                # Avoid repeatedly reevaluating the same mint after hard signal rejects.
                # We use a synthetic "last_used" timestamp so the existing launch cooldown
                # logic enforces a shorter reject-specific backoff window.
                if (
                    reason
                    and self.signal_first
                    and self.launch_signals_file
                    and candidate.mint
                    and reason in self.signal_reject_cooldown_reasons
                ):
                    try:
                        rc_s = max(0.0, float(self.signal_reject_cooldown_s or 0.0))
                        if rc_s > 0 and float(self.launch_signal_cooldown or 0) > 0:
                            eff = min(rc_s, float(self.launch_signal_cooldown))
                            synthetic_last_used = time.time() - float(self.launch_signal_cooldown) + eff
                            self.launch_signal_last_used[candidate.mint] = synthetic_last_used
                    except Exception:
                        pass
                if self.filter_debug and reason:
                    try:
                        self._filter_reject_counts[reason] = int(self._filter_reject_counts.get(reason) or 0) + 1
                        now = time.time()
                        last = float(getattr(self, "_filter_reject_last_report", 0.0) or 0.0)
                        if (now - last) >= float(self.filter_debug_interval_s):
                            self._filter_reject_last_report = now
                            items = sorted(self._filter_reject_counts.items(), key=lambda kv: kv[1], reverse=True)[:8]
                            if items:
                                parts = " ".join([f"{k}={v}" for k, v in items])
                                console.print(
                                    f"[dim]filter rejects (last {int(self.filter_debug_interval_s)}s window): {parts}[/dim]"
                                )
                            self._filter_reject_counts.clear()
                    except Exception:
                        pass
                return False

            # Signal-first: hydrate from launch signal metrics when available.
            if self.signal_first and self.launch_signals_file:
                metrics = self.launch_signal_metrics.get(candidate.mint, {})
                # Reject-cooldown should short-circuit before expensive prequote checks.
                # The late cooldown check near signal acceptance is not sufficient because
                # most rejects happen earlier in the prequote block.
                last_used = self.launch_signal_last_used.get(candidate.mint)
                if last_used and (time.time() - last_used) < self.launch_signal_cooldown:
                    return False
                # Prequote demand gate: drop weak/missing-demand signals before any hydration.
                if self.signal_prequote_require_demand:
                    missing = []
                    if metrics.get("hits") is None:
                        missing.append("hits")
                    if metrics.get("unique_buyers") is None:
                        missing.append("unique_buyers")
                    if metrics.get("net_sol_in") is None:
                        missing.append("net_sol_in")
                    if missing:
                        self._signal_debug_write(
                            "reject_prequote_missing_demand",
                            candidate,
                            {"missing": missing, "source": metrics.get("source")},
                        )
                        return _reject("prequote_missing_demand")

                try:
                    sig_score = float(
                        metrics.get("score")
                        if metrics.get("score") is not None
                        else self.launch_signal_scores.get(candidate.mint, 0.0)
                    )
                except Exception:
                    sig_score = 0.0
                try:
                    sig_hits = int(metrics.get("hits") or 0)
                except Exception:
                    sig_hits = 0
                try:
                    sig_buys = int(metrics.get("buys") or 0)
                except Exception:
                    sig_buys = 0
                try:
                    sig_sells = int(metrics.get("sells") or 0)
                except Exception:
                    sig_sells = 0
                try:
                    sig_uniq = int(metrics.get("unique_buyers") or 0)
                except Exception:
                    sig_uniq = 0
                try:
                    sig_net = float(metrics.get("net_sol_in") or 0.0)
                except Exception:
                    sig_net = 0.0
                try:
                    sig_mcap = float(
                        metrics.get("market_cap")
                        or metrics.get("mcap")
                        or metrics.get("fdv")
                        or 0.0
                    )
                except Exception:
                    sig_mcap = 0.0
                try:
                    _mom5 = metrics.get("price_change_5m")
                    if _mom5 is None:
                        _mom5 = metrics.get("momentum_5m_pct")
                    sig_mom5 = float(_mom5) if _mom5 is not None else None
                except Exception:
                    sig_mom5 = None
                try:
                    _mom1h = metrics.get("price_change_1h")
                    if _mom1h is None:
                        _mom1h = metrics.get("momentum_1h_pct")
                    sig_mom1h = float(_mom1h) if _mom1h is not None else None
                except Exception:
                    sig_mom1h = None
                try:
                    top_share_raw = metrics.get("top_buyer_share")
                    sig_top_share = float(top_share_raw) if top_share_raw is not None else None
                except Exception:
                    sig_top_share = None

                # Winner-zone checks are evaluated after all baseline prequote filters pass.
                # This prevents zone-gate noise on candidates that are already rejected by core gates.
                if self.winner_zone_enabled:
                    candidate.winner_zone_id = ""
                    candidate.winner_zone_objective = 0.0
                    candidate.winner_zone_bypassed = False
                    candidate.winner_zone_bypass_reason = ""

                if self.signal_prequote_min_hits > 0 and sig_hits < int(self.signal_prequote_min_hits):
                    self._signal_debug_write(
                        "reject_prequote_hits",
                        candidate,
                        {"hits": sig_hits, "min_hits": int(self.signal_prequote_min_hits)},
                    )
                    return _reject("prequote_hits")
                if self.signal_prequote_min_buys > 0 and sig_buys < int(self.signal_prequote_min_buys):
                    self._signal_debug_write(
                        "reject_prequote_buys",
                        candidate,
                        {"buys": sig_buys, "min_buys": int(self.signal_prequote_min_buys)},
                    )
                    return _reject("prequote_buys")
                if self.signal_prequote_min_unique_buyers > 0 and sig_uniq < int(self.signal_prequote_min_unique_buyers):
                    self._signal_debug_write(
                        "reject_prequote_uniq",
                        candidate,
                        {
                            "unique_buyers": sig_uniq,
                            "min_unique_buyers": int(self.signal_prequote_min_unique_buyers),
                        },
                    )
                    return _reject("prequote_uniq")
                if self.signal_prequote_min_net_sol_in > 0 and sig_net < float(self.signal_prequote_min_net_sol_in):
                    self._signal_debug_write(
                        "reject_prequote_net",
                        candidate,
                        {"net_sol_in": sig_net, "min_net_sol_in": float(self.signal_prequote_min_net_sol_in)},
                    )
                    return _reject("prequote_net")
                if self.signal_prequote_min_mcap_usd > 0 and sig_mcap > 0 and sig_mcap < float(self.signal_prequote_min_mcap_usd):
                    self._signal_debug_write(
                        "reject_prequote_mcap_low",
                        candidate,
                        {"mcap": sig_mcap, "min_mcap": float(self.signal_prequote_min_mcap_usd)},
                    )
                    return _reject("prequote_mcap_low")
                if (
                    self.signal_prequote_min_buy_sell_ratio > 0
                    and sig_sells > 0
                    and (float(sig_buys) / float(sig_sells)) < float(self.signal_prequote_min_buy_sell_ratio)
                ):
                    bs_ratio = float(sig_buys) / float(sig_sells)
                    self._signal_debug_write(
                        "reject_prequote_bs_ratio",
                        candidate,
                        {"buy_sell_ratio": bs_ratio, "min_buy_sell_ratio": float(self.signal_prequote_min_buy_sell_ratio)},
                    )
                    return _reject("prequote_bs_ratio")
                if (
                    self.signal_prequote_max_top_buyer_share > 0
                    and sig_top_share is not None
                    and float(sig_top_share) > float(self.signal_prequote_max_top_buyer_share)
                ):
                    self._signal_debug_write(
                        "reject_prequote_top_share",
                        candidate,
                        {"top_buyer_share": float(sig_top_share), "max_top_buyer_share": float(self.signal_prequote_max_top_buyer_share)},
                    )
                    return _reject("prequote_top_share")

                score_bypassed = False
                if self.signal_prequote_min_signal_score > 0 and sig_score < float(self.signal_prequote_min_signal_score):
                    if self.signal_prequote_score_bypass_enabled:
                        top_ok = (
                            sig_top_share is None
                            or self.signal_prequote_score_bypass_max_top_buyer_share <= 0
                            or float(sig_top_share) <= float(self.signal_prequote_score_bypass_max_top_buyer_share)
                        )
                        score_bypassed = (
                            sig_hits >= int(self.signal_prequote_score_bypass_min_hits)
                            and sig_buys >= int(self.signal_prequote_score_bypass_min_buys)
                            and sig_uniq >= int(self.signal_prequote_score_bypass_min_unique_buyers)
                            and sig_net >= float(self.signal_prequote_score_bypass_min_net_sol_in)
                            and top_ok
                        )
                    if not score_bypassed:
                        self._signal_debug_write(
                            "reject_prequote_score",
                            candidate,
                            {
                                "score": sig_score,
                                "min_score": float(self.signal_prequote_min_signal_score),
                                "source": metrics.get("source"),
                            },
                        )
                        return _reject("prequote_score")
                    self._signal_debug_write(
                        "prequote_score_bypass",
                        candidate,
                        {
                            "score": sig_score,
                            "min_score": float(self.signal_prequote_min_signal_score),
                            "hits": sig_hits,
                            "buys": sig_buys,
                            "unique_buyers": sig_uniq,
                            "net_sol_in": sig_net,
                            "top_buyer_share": sig_top_share,
                        },
                    )

                # Winner-zone gate (post-baseline): only runs after core prequote filters pass.
                if self.winner_zone_enabled:
                    z = self._winner_zone_match(
                        score=sig_score,
                        net_sol_in=sig_net,
                        top_buyer_share=sig_top_share,
                        mcap=sig_mcap,
                    )
                    if self.winner_zone_force_bypass_only and z is not None:
                        self._signal_debug_write(
                            "winner_zone_match_suppressed",
                            candidate,
                            {
                                "winner_zone_id": str(z[0] or ""),
                                "winner_zone_objective": float(z[1] or 0.0),
                            },
                        )
                        z = None
                    if z is None:
                        candidate.winner_zone_id = ""
                        candidate.winner_zone_objective = 0.0
                        candidate.winner_zone_bypassed = False
                        candidate.winner_zone_bypass_reason = ""
                        if not self._winner_zones:
                            self._signal_debug_write(
                                "winner_zone_missing",
                                candidate,
                                {"zone_path": self.winner_zone_path},
                            )
                            if self.winner_zone_enforce and self.winner_zone_block_when_missing:
                                return _reject("winner_zone_missing")
                        else:
                            bypass_ok = False
                            bypass_meta: dict[str, float | int | bool] = {}
                            if self.winner_zone_bypass_enabled:
                                bypass_ok, bypass_meta = self._winner_zone_bypass_ok(
                                    score=sig_score,
                                    hits=sig_hits,
                                    unique_buyers=sig_uniq,
                                    net_sol_in=sig_net,
                                    top_buyer_share=sig_top_share,
                                    mcap=sig_mcap,
                                )
                            if bypass_ok:
                                candidate.winner_zone_bypassed = True
                                candidate.winner_zone_bypass_reason = "strong_prequote"
                                self._signal_debug_write("pass_winner_zone_bypass", candidate, bypass_meta)
                            else:
                                self._signal_debug_write(
                                    "reject_winner_zone",
                                    candidate,
                                    {
                                        "score": sig_score,
                                        "net_sol_in": sig_net,
                                        "top_buyer_share": sig_top_share,
                                        "mcap": sig_mcap,
                                    },
                                )
                                if self.winner_zone_enforce:
                                    return _reject("winner_zone")
                    else:
                        candidate.winner_zone_id = str(z[0] or "")
                        candidate.winner_zone_objective = float(z[1] or 0.0)
                        candidate.winner_zone_bypassed = False
                        candidate.winner_zone_bypass_reason = ""
                        self._signal_debug_write(
                            "pass_winner_zone",
                            candidate,
                            {
                                "winner_zone_id": candidate.winner_zone_id,
                                "winner_zone_objective": candidate.winner_zone_objective,
                                "score": sig_score,
                                "net_sol_in": sig_net,
                                "top_buyer_share": sig_top_share,
                                "mcap": sig_mcap,
                            },
                        )
                self._signal_debug_write(
                    "pass_prequote",
                    candidate,
                    {
                        "score": sig_score,
                        "hits": sig_hits,
                        "buys": sig_buys,
                        "sells": sig_sells,
                        "buy_sell_ratio": (float(sig_buys) / float(sig_sells)) if sig_sells > 0 else None,
                        "unique_buyers": sig_uniq,
                        "net_sol_in": sig_net,
                        "mcap": sig_mcap,
                        "momentum_5m_pct": sig_mom5,
                        "momentum_1h_pct": sig_mom1h,
                        "top_buyer_share": sig_top_share,
                        "score_bypassed": score_bypassed,
                        "winner_zone_id": str(getattr(candidate, "winner_zone_id", "") or ""),
                        "winner_zone_objective": float(getattr(candidate, "winner_zone_objective", 0.0) or 0.0),
                        "winner_zone_bypassed": bool(getattr(candidate, "winner_zone_bypassed", False)),
                        "winner_zone_bypass_reason": str(getattr(candidate, "winner_zone_bypass_reason", "") or ""),
                        "source": metrics.get("source"),
                    },
                )

                try:
                    if candidate.liquidity <= 0 and metrics.get("liquidity") is not None:
                        candidate.liquidity = float(metrics.get("liquidity", 0) or 0)
                except Exception:
                    pass
                try:
                    if not candidate.symbol and metrics.get("symbol"):
                        candidate.symbol = str(metrics.get("symbol") or "")
                except Exception:
                    pass
                # Preserve market momentum from signal payload when provided.
                # Jupiter quote sampling may not have a prior point for local 5m drift
                # and would otherwise default to 0.0, masking real market momentum.
                try:
                    if sig_mom5 is not None:
                        candidate.price_change_5m = float(sig_mom5)
                except Exception:
                    pass
                try:
                    if sig_mom1h is not None:
                        candidate.price_change_1h = float(sig_mom1h)
                except Exception:
                    pass
                # Hybrid: hydrate microstructure from DexScreener so we can enforce liquidity/mcap gates.
                if self.signal_hybrid_dex:
                    try:
                        if (candidate.liquidity <= 0) or (candidate.market_cap <= 0) or (candidate.txns_5m <= 0) or (candidate.volume_5m <= 0):
                            await self._fetch_token_data(candidate)
                    except Exception:
                        pass
                # Hard liquidity gates for signal-first mode.
                # We require known liquidity and a minimum floor before spending more quote budget.
                try:
                    sig_min_liq_default = float(getattr(meme_config, "MIN_LIQUIDITY_EARLY", 0.0) or 0.0)
                except Exception:
                    sig_min_liq_default = 0.0
                try:
                    sig_min_liq = float(os.getenv("MEME_SIGNAL_MIN_LIQUIDITY_USD", str(sig_min_liq_default)) or sig_min_liq_default)
                except Exception:
                    sig_min_liq = sig_min_liq_default
                require_sig_liq = bool(self.signal_require_liquidity)
                try:
                    liq_now = float(getattr(candidate, "liquidity", 0.0) or 0.0)
                except Exception:
                    liq_now = 0.0
                if require_sig_liq and liq_now <= 0.0:
                    liq_now = self._maybe_apply_signal_liquidity_fallback(
                        candidate,
                        metrics if isinstance(metrics, dict) else {},
                        context="filter",
                    )
                if require_sig_liq and liq_now <= 0.0:
                    self._signal_debug_write("reject_liq_missing_signal", candidate, {"min_liq": sig_min_liq})
                    return _reject("liq_missing_signal")
                if sig_min_liq > 0.0 and liq_now > 0.0 and liq_now < sig_min_liq:
                    self._signal_debug_write("reject_liq_low_signal", candidate, {"liq": liq_now, "min_liq": sig_min_liq})
                    return _reject("liq_low_signal")

                if self.signal_require_core_metrics:
                    ok_core, core_details = self._check_signal_core_metrics(candidate, metrics if isinstance(metrics, dict) else {})
                    if not ok_core:
                        self._signal_debug_write("reject_core_metrics", candidate, core_details)
                        return _reject("core_metrics")

            # If we don't have data yet, fetch it. In signal-first mode, avoid DexScreener and
            # rely on Jupiter quotes (bounded by budgets) to keep discovery lightweight.
            if not (self.signal_first and self.launch_signals_file and not self.signal_hybrid_dex):
                if (candidate.liquidity == 0 and candidate.market_cap == 0) or candidate.price <= 0:
                    await self._fetch_token_data(candidate)
            # Spike filter using previous snapshot state (liquidity/volume_5m)
            if meme_config.SPIKE_FILTER_ENABLED:
                prev = self.prev_candidate_state.get(candidate.mint)
                if prev:
                    dliq = candidate.liquidity - float(prev.get("liquidity", 0) or 0)
                    dvol5 = candidate.volume_5m - float(prev.get("volume_5m", 0) or 0)
                    if meme_config.MIN_LIQ_SPIKE_USD > 0 and dliq < meme_config.MIN_LIQ_SPIKE_USD:
                        return False
                    if meme_config.MIN_VOL_SPIKE_5M > 0 and dvol5 < meme_config.MIN_VOL_SPIKE_5M:
                        return False

            # Filter 1: Minimum liquidity
            if candidate.liquidity < meme_config.MIN_LIQUIDITY_USD:
                # Signal-first may not have listing liquidity; use impact gate instead.
                if self.signal_first and self.launch_signals_file and candidate.liquidity <= 0:
                    # Hybrid mode requires Dex hydration to validate liquidity. If Dex still can't
                    # provide it, skip and allow re-eval later.
                    if self.signal_hybrid_dex:
                        self._signal_debug_write("reject_liq_missing", candidate, {"min_liq": meme_config.MIN_LIQUIDITY_USD})
                        return _reject("liq_missing")
                if not (self.signal_first and self.launch_signals_file and candidate.liquidity <= 0):
                    if meme_config.VERBOSE_LOGGING:
                        console.print(f"[yellow]Low liquidity: {candidate.symbol} (${candidate.liquidity:,.0f})[/yellow]")
                    return _reject("liq_low")

            # Signal-first simplified filters: avoid DexScreener/Birdeye-heavy microstructure gates.
            if self.signal_first and self.launch_signals_file:
                age_seconds = time.time() - candidate.discovered_at
                metrics = (self.launch_signal_metrics.get(candidate.mint, {}) or {})
                if self.signal_age_checkpoints_s:
                    grace_s = max(0.0, float(self.signal_age_checkpoint_grace_s))
                    cps = self.signal_age_checkpoints_s
                    idx = int(self._signal_age_checkpoint_idx.get(candidate.mint, 0) or 0)
                    # Advance past missed windows.
                    while idx < len(cps) and age_seconds > (float(cps[idx]) + grace_s):
                        idx += 1
                    self._signal_age_checkpoint_idx[candidate.mint] = idx
                    if idx >= len(cps):
                        self._signal_debug_write(
                            "reject_age_checkpoint_done",
                            candidate,
                            {"age_s": round(age_seconds, 1), "checkpoints_s": cps, "grace_s": grace_s},
                        )
                        return _reject("age")
                    target_s = float(cps[idx])
                    if age_seconds < target_s:
                        self._signal_debug_write(
                            "wait_age_checkpoint",
                            candidate,
                            {"age_s": round(age_seconds, 1), "target_s": target_s, "wait_s": round(target_s - age_seconds, 1)},
                        )
                        return False
                    # Candidate is in checkpoint window now. If it later fails other gates,
                    # wait for the next checkpoint instead of immediately reattempting.
                    self._signal_age_checkpoint_idx[candidate.mint] = idx + 1
                    self._signal_debug_write(
                        "pass_age_checkpoint",
                        candidate,
                        {"age_s": round(age_seconds, 1), "target_s": target_s, "grace_s": grace_s},
                    )
                if self.signal_min_age_seconds > 0 and age_seconds < self.signal_min_age_seconds:
                    self._signal_debug_write(
                        "reject_age_fresh",
                        candidate,
                        {"age_s": round(age_seconds, 1), "min_age_s": self.signal_min_age_seconds},
                    )
                    return _reject("age_fresh")
                if age_seconds > self.signal_max_age_seconds:
                    late_ok = False
                    late_details: dict[str, Any] = {}
                    if (
                        self.signal_late_max_age_seconds > self.signal_max_age_seconds
                        and age_seconds <= float(self.signal_late_max_age_seconds)
                    ):
                        try:
                            late_uniq = int(metrics.get("unique_buyers") or 0)
                        except Exception:
                            late_uniq = 0
                        try:
                            late_net = float(metrics.get("net_sol_in") or 0.0)
                        except Exception:
                            late_net = 0.0
                        top_raw = metrics.get("top_buyer_share")
                        try:
                            late_top = float(top_raw) if top_raw is not None else None
                        except Exception:
                            late_top = None
                        try:
                            late_score = float(
                                metrics.get("score")
                                if metrics.get("score") is not None
                                else self.launch_signal_scores.get(candidate.mint, 0.0)
                            )
                        except Exception:
                            late_score = 0.0

                        uniq_ok = late_uniq >= int(self.signal_late_min_unique_buyers)
                        net_ok = late_net >= float(self.signal_late_min_net_sol_in)
                        score_ok = late_score >= float(self.signal_late_min_signal_score)
                        top_ok = (
                            late_top is None
                            or self.signal_late_max_top_buyer_share <= 0
                            or float(late_top) <= float(self.signal_late_max_top_buyer_share)
                        )
                        late_ok = bool(uniq_ok and net_ok and score_ok and top_ok)
                        late_details = {
                            "age_s": round(age_seconds, 1),
                            "max_age_s": self.signal_max_age_seconds,
                            "late_max_age_s": self.signal_late_max_age_seconds,
                            "signal_score": float(late_score),
                            "min_signal_score": float(self.signal_late_min_signal_score),
                            "unique_buyers": int(late_uniq),
                            "min_unique_buyers": int(self.signal_late_min_unique_buyers),
                            "net_sol_in": float(late_net),
                            "min_net_sol_in": float(self.signal_late_min_net_sol_in),
                            "top_buyer_share": float(late_top) if late_top is not None else None,
                            "max_top_buyer_share": float(self.signal_late_max_top_buyer_share),
                        }
                    if not late_ok:
                        extra = {"age_s": round(age_seconds, 1), "max_age_s": self.signal_max_age_seconds}
                        if late_details:
                            extra.update(late_details)
                        self._signal_debug_write("reject_age", candidate, extra)
                        return _reject("age")
                    self._signal_debug_write("pass_age_late_window", candidate, late_details)
                # Prefer mints with some immediate activity (set by the WS listener).
                try:
                    hits = int(metrics.get("hits") or 0)
                    min_hits = int(os.getenv("PUMP_SIGNAL_MIN_HITS") or os.getenv("MEME_SIGNAL_MIN_HITS") or "3")
                    if hits and hits < min_hits:
                        self._signal_debug_write("reject_hits", candidate, {"hits": hits, "min_hits": min_hits})
                        return False
                    # Optional: require a minimum WS launch signal score (0-100) before spending quote budget.
                    try:
                        min_sig_score = float(os.getenv("MEME_SIGNAL_MIN_SCORE", "0") or 0.0)
                    except Exception:
                        min_sig_score = 0.0
                    if min_sig_score > 0:
                        try:
                            sig_score = float(self.launch_signal_scores.get(candidate.mint, 0.0) or 0.0)
                        except Exception:
                            sig_score = 0.0
                        if sig_score < min_sig_score:
                            self._signal_debug_write("reject_sig_score", candidate, {"sig_score": sig_score, "min_sig_score": min_sig_score})
                            return False
                    # Demand-burst gates (if available).
                    min_buys = int(os.getenv("MEME_SIGNAL_MIN_BUYS", "2") or 2)
                    min_net_sol = float(os.getenv("MEME_SIGNAL_MIN_NET_SOL_IN", "0.3") or 0.3)
                    # Caps help avoid late/overcrowded launches where the edge is mostly gone.
                    # Default 0 disables.
                    try:
                        max_hits = int(os.getenv("MEME_SIGNAL_MAX_HITS", "0") or 0)
                    except Exception:
                        max_hits = 0
                    try:
                        max_uniq = int(os.getenv("MEME_SIGNAL_MAX_UNIQUE_BUYERS", "0") or 0)
                    except Exception:
                        max_uniq = 0
                    try:
                        max_net_sol = float(os.getenv("MEME_SIGNAL_MAX_NET_SOL_IN", "0") or 0.0)
                    except Exception:
                        max_net_sol = 0.0
                    min_buy_accel = float(os.getenv("MEME_SIGNAL_MIN_BUY_ACCEL", "0.0") or 0.0)
                    max_top_share = float(os.getenv("MEME_SIGNAL_MAX_TOP_BUYER_SHARE", "0.0") or 0.0)
                    min_uniq = int(os.getenv("MEME_SIGNAL_MIN_UNIQUE_BUYERS", "1") or 1)
                    sig_source = str(metrics.get("source") or "")
                    bypass_caps_for_source = bool(sig_source and sig_source in self.signal_cap_bypass_sources)
                    require_demand_metrics = str(os.getenv("MEME_SIGNAL_REQUIRE_DEMAND_METRICS", "false") or "false").lower() in (
                        "1",
                        "true",
                        "yes",
                    )
                    # Optional: require that the first sell does not happen "immediately".
                    # Very fast first sells are often dev/insider dumps that nuke expectancy.
                    try:
                        min_t_first_sell_s = float(os.getenv("MEME_SIGNAL_MIN_T_FIRST_SELL_S", "0") or 0.0)
                    except Exception:
                        min_t_first_sell_s = 0.0
                    buys = int(metrics.get("buys") or 0)
                    sells = int(metrics.get("sells") or 0)
                    uniq = int(metrics.get("unique_buyers") or 0)
                    net_sol_in = float(metrics.get("net_sol_in") or 0.0)
                    try:
                        buy_accel = float(metrics.get("buy_accel")) if metrics.get("buy_accel") is not None else None
                    except Exception:
                        buy_accel = None
                    try:
                        top_buyer_share = (
                            float(metrics.get("top_buyer_share")) if metrics.get("top_buyer_share") is not None else None
                        )
                    except Exception:
                        top_buyer_share = None
                    try:
                        max_sells = int(os.getenv("MEME_SIGNAL_MAX_SELLS", "0") or 0)
                    except Exception:
                        max_sells = 0
                    try:
                        hard_max_hits = int(os.getenv("MEME_SIGNAL_HARD_MAX_HITS", "0") or 0)
                    except Exception:
                        hard_max_hits = 0
                    try:
                        hard_max_net_sol = float(os.getenv("MEME_SIGNAL_HARD_MAX_NET_SOL_IN", "0") or 0.0)
                    except Exception:
                        hard_max_net_sol = 0.0
                    if hard_max_hits > 0 and hits and hits > hard_max_hits:
                        self._signal_debug_write(
                            "reject_hits_hard",
                            candidate,
                            {"hits": hits, "hard_max_hits": hard_max_hits, "source": sig_source},
                        )
                        return False
                    if hard_max_net_sol > 0.0 and "net_sol_in" in metrics and net_sol_in > hard_max_net_sol:
                        self._signal_debug_write(
                            "reject_net_sol_hard",
                            candidate,
                            {"net_sol_in": net_sol_in, "hard_max_net_sol_in": hard_max_net_sol, "source": sig_source},
                        )
                        return False
                    if (not bypass_caps_for_source) and max_hits > 0 and hits and hits > max_hits:
                        self._signal_debug_write("reject_hits_high", candidate, {"hits": hits, "max_hits": max_hits})
                        return False
                    max_uniq_effective = max_uniq
                    crowd_bonus = 0
                    if max_uniq > 0 and self.signal_dynamic_crowd_gate:
                        if net_sol_in >= float(self.signal_crowd_relax_net_sol_in):
                            crowd_bonus += 1
                            step = max(0.1, float(self.signal_crowd_relax_net_sol_step))
                            crowd_bonus += int((net_sol_in - float(self.signal_crowd_relax_net_sol_in)) / step)
                        if buy_accel is not None and buy_accel >= float(self.signal_crowd_relax_buy_accel):
                            crowd_bonus += 1
                        if (
                            top_buyer_share is not None
                            and top_buyer_share > 0
                            and top_buyer_share <= float(self.signal_crowd_relax_top_share)
                        ):
                            crowd_bonus += 1
                        if (buys + sells) > 0 and sells <= buys:
                            crowd_bonus += 1
                        crowd_bonus = max(0, min(int(self.signal_crowd_relax_max_bonus), int(crowd_bonus)))
                        max_uniq_effective = int(max_uniq) + int(crowd_bonus)
                    if (not bypass_caps_for_source) and max_uniq_effective > 0 and "unique_buyers" in metrics and uniq > max_uniq_effective:
                        self._signal_debug_write(
                            "reject_uniq_high",
                            candidate,
                            {
                                "uniq": uniq,
                                "max_uniq": max_uniq_effective,
                                "max_uniq_base": max_uniq,
                                "crowd_bonus": crowd_bonus,
                                "net_sol_in": net_sol_in,
                                "buy_accel": buy_accel,
                                "top_buyer_share": top_buyer_share,
                            },
                        )
                        return False
                    if (not bypass_caps_for_source) and max_net_sol > 0.0 and "net_sol_in" in metrics and net_sol_in > max_net_sol:
                        self._signal_debug_write("reject_net_sol_high", candidate, {"net_sol_in": net_sol_in, "max_net_sol_in": max_net_sol})
                        return False
                    if (not bypass_caps_for_source) and max_sells > 0 and "sells" in metrics and sells > max_sells:
                        self._signal_debug_write("reject_sells_high", candidate, {"sells": sells, "max_sells": max_sells})
                        return False
                    if require_demand_metrics:
                        # If we require demand metrics, do not proceed when the WS listener hasn't produced them yet.
                        if not any(k in metrics for k in ("buys", "unique_buyers", "net_sol_in")):
                            self._signal_debug_write("reject_missing_demand_metrics", candidate, {"keys": sorted(list(metrics.keys()))[:12]})
                            return False
                        if min_uniq > 0 and "unique_buyers" not in metrics:
                            self._signal_debug_write("reject_missing_unique_buyers", candidate, {"min_unique_buyers": min_uniq})
                            return False
                        if min_net_sol > 0 and "net_sol_in" not in metrics:
                            self._signal_debug_write("reject_missing_net_sol_in", candidate, {"min_net_sol_in": min_net_sol})
                            return False
                    # Use unique buyers as a buy proxy (WS classification can undercount buys).
                    eff_buys = max(buys, uniq)
                    if "buys" in metrics and eff_buys < min_buys:
                        self._signal_debug_write("reject_buys", candidate, {"eff_buys": eff_buys, "min_buys": min_buys, "buys": buys, "uniq": uniq})
                        return False
                    min_uniq_effective = int(min_uniq)
                    if bool(getattr(candidate, "liquidity_estimated", False)) and min_uniq_effective > 1:
                        # For fallback-liquidity candidates, allow one less unique buyer
                        # because provider sparsity often delays full buyer attribution.
                        min_uniq_effective = max(1, min_uniq_effective - 1)
                    if "unique_buyers" in metrics and uniq < min_uniq_effective:
                        self._signal_debug_write(
                            "reject_uniq",
                            candidate,
                            {
                                "uniq": uniq,
                                "min_uniq": min_uniq_effective,
                                "min_uniq_base": min_uniq,
                                "liq_estimated": bool(getattr(candidate, "liquidity_estimated", False)),
                            },
                        )
                        return False
                    if "net_sol_in" in metrics and net_sol_in < min_net_sol:
                        self._signal_debug_write("reject_net_sol", candidate, {"net_sol_in": net_sol_in, "min_net_sol_in": min_net_sol})
                        return False
                    # Optional: require the first observed sell to arrive after a minimum delay.
                    if min_t_first_sell_s > 0.0 and "t_first_sell_s" in metrics:
                        try:
                            tfs = metrics.get("t_first_sell_s")
                            # NOTE: This metric can be noisy for very early windows (and can
                            # over-trigger based on attribution heuristics). Prefer using it
                            # as a *soft penalty* in scoring unless explicitly required.
                            if tfs is not None and float(tfs) < min_t_first_sell_s:
                                self._signal_debug_write(
                                    "reject_t_first_sell",
                                    candidate,
                                    {"t_first_sell_s": float(tfs), "min_t_first_sell_s": min_t_first_sell_s},
                                )
                                return False
                        except Exception:
                            # If the metric is present but unparsable, treat it as a reject when the gate is enabled.
                            self._signal_debug_write(
                                "reject_t_first_sell_parse",
                                candidate,
                                {"min_t_first_sell_s": min_t_first_sell_s},
                            )
                            return False
                    # Optional: require accelerating buy flow (when present).
                    if min_buy_accel > 0.0 and "buy_accel" in metrics:
                        try:
                            ba = metrics.get("buy_accel")
                            if ba is not None and float(ba) < min_buy_accel:
                                self._signal_debug_write("reject_buy_accel", candidate, {"buy_accel": ba, "min_buy_accel": min_buy_accel})
                                return False
                        except Exception:
                            self._signal_debug_write("reject_buy_accel_parse", candidate, {"min_buy_accel": min_buy_accel})
                            return False
                    # Optional: exclude single-wallet dominated flow (when present).
                    if max_top_share > 0.0 and "top_buyer_share" in metrics:
                        try:
                            ts = metrics.get("top_buyer_share")
                            if ts is not None and float(ts) > max_top_share:
                                self._signal_debug_write("reject_top_share", candidate, {"top_buyer_share": ts, "max_top_buyer_share": max_top_share})
                                return False
                        except Exception:
                            self._signal_debug_write("reject_top_share_parse", candidate, {"max_top_buyer_share": max_top_share})
                            return False
                except Exception:
                    pass

                await self._fetch_signal_quote_data(candidate)
                retry_n = max(0, int(self.signal_quote_retry_count))
                retry_delay = max(0.05, float(self.signal_quote_retry_delay_s))
                for i in range(retry_n):
                    if candidate.price > 0:
                        break
                    # Transient quote misses are common right after launch; retry briefly.
                    await asyncio.sleep(retry_delay * float(i + 1))
                    await self._fetch_signal_quote_data(candidate)
                if candidate.price <= 0:
                    self._signal_debug_write("reject_quote", candidate, {"retries": int(retry_n)})
                    return False
                if candidate.price_impact_pct and candidate.price_impact_pct > self.signal_max_impact_pct:
                    self._signal_debug_write("reject_impact", candidate, {"impact": candidate.price_impact_pct, "max_impact": self.signal_max_impact_pct})
                    return False
                mom5m_floor = float(self.signal_min_momentum_5m)
                try:
                    sig_source = str(metrics.get("source") or "")
                    if sig_source and sig_source in self.signal_cap_bypass_sources:
                        mom5m_floor = float(
                            os.getenv("MEME_SIGNAL_MIN_MOMENTUM_5M_MOVER", str(self.signal_min_momentum_5m))
                            or self.signal_min_momentum_5m
                        )
                except Exception:
                    mom5m_floor = float(self.signal_min_momentum_5m)
                sig_mom_dbg = None
                try:
                    sig_mom_dbg = metrics.get("price_change_5m")
                    if sig_mom_dbg is None:
                        sig_mom_dbg = metrics.get("momentum_5m_pct")
                except Exception:
                    sig_mom_dbg = None

                # In signal-first mode, 5m momentum is often unavailable at discovery time.
                # Avoid treating "missing" as literal 0.0 momentum; only reject when we have
                # a real momentum datapoint (from signal payload or hydrated candidate).
                mom5m_now = float(getattr(candidate, "price_change_5m", 0.0) or 0.0)
                if self.signal_first and self.launch_signals_file and sig_mom_dbg is None and abs(mom5m_now) < 1e-9:
                    self._signal_debug_write(
                        "skip_mom5m_missing",
                        candidate,
                        {"mom5m": mom5m_now, "min_mom5m": mom5m_floor, "signal_mom5m": None},
                    )
                elif mom5m_now < mom5m_floor:
                    self._signal_debug_write(
                        "reject_mom5m",
                        candidate,
                        {"mom5m": mom5m_now, "min_mom5m": mom5m_floor, "signal_mom5m": sig_mom_dbg},
                    )
                    return False
                # Keep launch signal hotlist rules (cooldown/ttl/seen) below.

            # Filter 2: Market cap range
            # Signal-first: default to not filtering on market cap unless explicitly configured.
            # Many Pump-style launches start below traditional MIN_MCAP thresholds.
            if self.signal_first and self.launch_signals_file:
                # In hybrid mode, default to the global MIN_MCAP gate unless explicitly overridden.
                # This prevents trading unknown/ultra-microcap listings that dominate loss tails.
                sig_min_default = float(getattr(meme_config, "MIN_MARKET_CAP_USD", 0.0) or 0.0)
                sig_min_mcap = float(os.getenv("MEME_SIGNAL_MIN_MCAP_USD", str(sig_min_default)) or sig_min_default)
                sig_max_mcap = float(os.getenv("MEME_SIGNAL_MAX_MCAP_USD", "0") or 0)
                candidate.mcap_scout_mode = False
                # If we require a minimum market cap, don't allow "unknown mcap" through.
                # Missing supply/price data should behave like below-threshold and be rechecked later.
                if sig_min_mcap > 0 and (candidate.market_cap <= 0):
                    # If we haven't tried Dex hydration yet, try once before rejecting.
                    if self.signal_hybrid_dex:
                        try:
                            await self._fetch_token_data(candidate)
                        except Exception:
                            pass
                    self._signal_mcap_above_since.pop(candidate.mint, None)
                    recheck_s = self._set_signal_mcap_recheck(candidate.mint, reason="mcap_missing")
                    self._signal_debug_write(
                        "reject_mcap_missing",
                        candidate,
                        {"min_mcap": sig_min_mcap, "recheck_s": recheck_s},
                    )
                    return False
                if candidate.market_cap > 0:
                    if sig_min_mcap > 0 and candidate.market_cap >= sig_min_mcap:
                        self._signal_mcap_recheck_counts.pop(candidate.mint, None)
                        if self.signal_mcap_confirm_seconds > 0:
                            now_m = time.time()
                            since = float(self._signal_mcap_above_since.get(candidate.mint) or 0.0)
                            if since <= 0.0:
                                self._signal_mcap_above_since[candidate.mint] = now_m
                                recheck_s = max(
                                    1.0,
                                    min(float(self.signal_mcap_confirm_recheck_s), float(self.signal_mcap_confirm_seconds)),
                                )
                                self._signal_mcap_recheck_until[candidate.mint] = now_m + recheck_s
                                self._signal_debug_write(
                                    "reject_mcap_confirm",
                                    candidate,
                                    {"mcap": candidate.market_cap, "min_mcap": sig_min_mcap, "confirm_s": self.signal_mcap_confirm_seconds, "waited_s": 0.0, "recheck_s": recheck_s},
                                )
                                return False
                            waited_s = now_m - since
                            if waited_s < float(self.signal_mcap_confirm_seconds):
                                recheck_s = max(
                                    1.0,
                                    min(
                                        float(self.signal_mcap_confirm_recheck_s),
                                        float(self.signal_mcap_confirm_seconds) - float(waited_s),
                                    ),
                                )
                                self._signal_mcap_recheck_until[candidate.mint] = now_m + recheck_s
                                self._signal_debug_write(
                                    "reject_mcap_confirm",
                                    candidate,
                                    {
                                        "mcap": candidate.market_cap,
                                        "min_mcap": sig_min_mcap,
                                        "confirm_s": self.signal_mcap_confirm_seconds,
                                        "waited_s": round(waited_s, 2),
                                        "recheck_s": recheck_s,
                                    },
                                )
                                return False
                        self._signal_mcap_above_since.pop(candidate.mint, None)
                    if sig_min_mcap > 0 and candidate.market_cap < sig_min_mcap:
                        self._signal_mcap_above_since.pop(candidate.mint, None)
                        scout_ok = False
                        scout_details: dict[str, float | int | bool | str] = {}
                        if self.signal_mcap_scout_enabled and candidate.market_cap >= float(self.signal_scout_min_mcap_usd):
                            metrics = (self.launch_signal_metrics.get(candidate.mint, {}) or {})
                            sig_score = float(self.launch_signal_scores.get(candidate.mint, 0.0) or 0.0)
                            hits = int(metrics.get("hits") or 0)
                            buys = int(metrics.get("buys") or 0)
                            sells = int(metrics.get("sells") or 0)
                            uniq = int(metrics.get("unique_buyers") or 0)
                            net_sol_in = float(metrics.get("net_sol_in") or 0.0)
                            top_share_raw = metrics.get("top_buyer_share")
                            top_share = float(top_share_raw) if top_share_raw is not None else 0.0
                            sell_buy_ratio = (float(sells) / float(max(1, buys))) if sells > 0 else 0.0

                            scout_ok = True
                            if hits < int(self.signal_scout_min_hits):
                                scout_ok = False
                            if uniq < int(self.signal_scout_min_unique_buyers):
                                scout_ok = False
                            if net_sol_in < float(self.signal_scout_min_net_sol_in):
                                scout_ok = False
                            if top_share > 0 and top_share > float(self.signal_scout_max_top_buyer_share):
                                scout_ok = False
                            if sell_buy_ratio > float(self.signal_scout_max_sell_buy_ratio):
                                scout_ok = False
                            if sig_score < float(self.signal_scout_min_signal_score):
                                scout_ok = False

                            scout_details = {
                                "mcap": float(candidate.market_cap),
                                "strict_min_mcap": float(sig_min_mcap),
                                "scout_min_mcap": float(self.signal_scout_min_mcap_usd),
                                "hits": hits,
                                "uniq": uniq,
                                "net_sol_in": net_sol_in,
                                "top_buyer_share": top_share,
                                "sell_buy_ratio": round(sell_buy_ratio, 4),
                                "signal_score": sig_score,
                            }
                        if scout_ok:
                            candidate.mcap_scout_mode = True
                            self._signal_debug_write("mcap_scout_pass", candidate, scout_details)
                            self._signal_mcap_recheck_counts.pop(candidate.mint, None)
                        else:
                            kind = "reject_mcap_scout_gate" if candidate.market_cap >= float(self.signal_scout_min_mcap_usd) else "reject_mcap_low"
                            reason = "mcap_scout_gate" if kind == "reject_mcap_scout_gate" else "mcap_low"
                            recheck_s = self._set_signal_mcap_recheck(candidate.mint, reason=reason)
                            self._signal_debug_write(
                                kind,
                                candidate,
                                {"mcap": candidate.market_cap, "min_mcap": sig_min_mcap, "recheck_s": recheck_s, **scout_details},
                            )
                            # Avoid repeatedly hammering Jupiter quotes for the same below-threshold mint.
                            # Keep the mint eligible for re-eval later in case it crosses the threshold.
                            return False
                    elif sig_min_mcap > 0:
                        self._signal_mcap_recheck_counts.pop(candidate.mint, None)
                    if sig_max_mcap > 0 and candidate.market_cap > sig_max_mcap:
                        self._signal_debug_write("reject_mcap_high", candidate, {"mcap": candidate.market_cap, "max_mcap": sig_max_mcap})
                        return False
            else:
                if candidate.market_cap < meme_config.MIN_MARKET_CAP_USD:
                    if meme_config.VERBOSE_LOGGING:
                        console.print(f"[yellow]Low mcap: {candidate.symbol} (${candidate.market_cap:,.0f})[/yellow]")
                    return _reject("mcap_low")

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

            # Optional: require mint to appear in launch signal hot list
            if self.launch_signals_file:
                ts = self.launch_signal_mints.get(candidate.mint)
                if not ts:
                    return False
                if (time.time() - ts) > self.launch_signal_ttl:
                    return False
                if candidate.mint in self.launch_signal_seen:
                    return False
                # Optional: use signal metrics liquidity as an early risk gate
                metrics = self.launch_signal_metrics.get(candidate.mint, {})
                try:
                    sig_liq = float(metrics.get("liquidity", 0) or 0)
                    if sig_liq and sig_liq < getattr(meme_config, "MIN_LIQUIDITY_EARLY", 0.0):
                        return False
                except Exception:
                    pass
                last_used = self.launch_signal_last_used.get(candidate.mint)
                if last_used and (time.time() - last_used) < self.launch_signal_cooldown:
                    return False

                # In signal-first mode we intentionally skip the DexScreener-style
                # microstructure filters (txns/volume) because we may not have them.
                if self.signal_first and not self.signal_hybrid_dex:
                    metrics = self.launch_signal_metrics.get(candidate.mint, {}) or {}
                    accept_extra: dict[str, Any] = {}
                    for k in (
                        "source",
                        "hits",
                        "buys",
                        "sells",
                        "unique_buyers",
                        "net_sol_in",
                        "top_buyer_share",
                        "buy_accel",
                        "liquidity",
                        "market_cap",
                        "score",
                        "momentum_5m_pct",
                        "momentum_1h_pct",
                        "entry_pattern",
                    ):
                        try:
                            v = metrics.get(k)
                        except Exception:
                            v = None
                        if v is not None:
                            accept_extra[k] = v
                    if "mcap" not in accept_extra:
                        try:
                            accept_extra["mcap"] = float(candidate.market_cap or 0.0)
                        except Exception:
                            accept_extra["mcap"] = 0.0
                    if "score" not in accept_extra:
                        try:
                            accept_extra["score"] = float(self.launch_signal_scores.get(candidate.mint, 0.0) or 0.0)
                        except Exception:
                            accept_extra["score"] = 0.0
                    self._signal_debug_write("accept", candidate, accept_extra)
                    return True

            # Filter 6: Price momentum - reject tokens dumping hard
            if candidate.price_change_5m < meme_config.MIN_PRICE_CHANGE_5M:
                if meme_config.VERBOSE_LOGGING:
                    console.print(f"[yellow]Dumping: {candidate.symbol} ({candidate.price_change_5m:+.1f}% 5m)[/yellow]")
                return _reject("mom5m_low")

            # Filter 7: Buy/sell ratio - more buyers than sellers
            if candidate.sells_1h > 0:
                buy_sell_ratio = candidate.buys_1h / candidate.sells_1h
                if buy_sell_ratio < meme_config.MIN_BUY_SELL_RATIO:
                    if meme_config.VERBOSE_LOGGING:
                        console.print(f"[yellow]Sell pressure: {candidate.symbol} (B/S: {buy_sell_ratio:.2f})[/yellow]")
                    return False
            if getattr(meme_config, 'MIN_BUY_SELL_RATIO_5M', 0.0) > 0 and candidate.sells_5m > 0:
                buy_sell_ratio_5m = candidate.buys_5m / candidate.sells_5m
                if buy_sell_ratio_5m < meme_config.MIN_BUY_SELL_RATIO_5M:
                    if meme_config.VERBOSE_LOGGING:
                        console.print(f"[yellow]5m sell pressure: {candidate.symbol} (B/S 5m: {buy_sell_ratio_5m:.2f})[/yellow]")
                    return _reject("bs5m_low")

            # Filter 8: Minimum activity
            if candidate.txns_1h < meme_config.MIN_TXNS_1H:
                if meme_config.VERBOSE_LOGGING:
                    console.print(f"[yellow]Low activity: {candidate.symbol} ({candidate.txns_1h} txns/1h)[/yellow]")
                return False

            # Filter 8b: 5m microstructure filters (burst activity)
            if getattr(meme_config, 'MIN_TXNS_5M', 0) > 0:
                if candidate.txns_5m < meme_config.MIN_TXNS_5M:
                    if meme_config.VERBOSE_LOGGING:
                        console.print(f"[yellow]Low 5m txns: {candidate.symbol} ({candidate.txns_5m} txns/5m)[/yellow]")
                    return _reject("tx5m_low")
            if getattr(meme_config, 'MIN_BUYS_5M', 0) > 0:
                if candidate.buys_5m < meme_config.MIN_BUYS_5M:
                    if meme_config.VERBOSE_LOGGING:
                        console.print(f"[yellow]Low 5m buys: {candidate.symbol} ({candidate.buys_5m} buys/5m)[/yellow]")
                    return _reject("buys5m_low")
            if getattr(meme_config, 'MIN_VOLUME_5M', 0.0) > 0:
                if candidate.volume_5m < meme_config.MIN_VOLUME_5M:
                    if meme_config.VERBOSE_LOGGING:
                        console.print(f"[yellow]Low 5m volume: {candidate.symbol} (${candidate.volume_5m:,.0f})[/yellow]")
                    return _reject("vol5m_low")
            if getattr(meme_config, 'MIN_VOL5M_SHARE', 0.0) > 0 and candidate.volume_1h > 0:
                share = candidate.volume_5m / max(candidate.volume_1h, 1e-9)
                if share < meme_config.MIN_VOL5M_SHARE:
                    if meme_config.VERBOSE_LOGGING:
                        console.print(f"[yellow]Low vol burst: {candidate.symbol} (5m share {share:.2%})[/yellow]")
                    return _reject("vol5m_share_low")

            # Filter 9: Pullback entry - don't chase pumps
            if getattr(meme_config, 'PULLBACK_ENTRY_ENABLED', True):
                max_5m_pump = getattr(meme_config, 'MAX_5M_PUMP', 30.0)
                if candidate.price_change_5m > max_5m_pump:
                    if meme_config.VERBOSE_LOGGING:
                        console.print(f"[yellow]Chasing pump: {candidate.symbol} ({candidate.price_change_5m:+.1f}% 5m > {max_5m_pump}%)[/yellow]")
                    return _reject("pump5m_too_high")

            # Filter 10: SOL correlation - pause entries during SOL dumps
            if getattr(meme_config, 'SOL_CORRELATION_ENABLED', True):
                sol_change = await self._get_sol_price_change()
                sol_threshold = getattr(meme_config, 'SOL_DUMP_THRESHOLD', -3.0)
                if sol_change is not None and sol_change < sol_threshold:
                    console.print(f"[yellow]SOL dumping: {sol_change:+.1f}% 1h (threshold: {sol_threshold}%) - pausing entries[/yellow]")
                    return _reject("sol_dump")

            # Filter 11: LP Lock check - prefer tokens with locked/burned LP
            if getattr(meme_config, 'LP_LOCK_REQUIRED', True):
                lp_locked = await self._check_lp_lock(candidate)
                if not lp_locked:
                    console.print(f"[yellow]LP not locked: {candidate.symbol} (rug risk)[/yellow]")
                    return _reject("lp_unlocked")

            console.print(f"[green]PASSED FILTERS: {candidate.symbol} | Liq: ${candidate.liquidity:,.0f} | MCap: ${candidate.market_cap:,.0f} | 5m: {candidate.price_change_5m:+.1f}% | B/S: {candidate.buys_1h}/{candidate.sells_1h}[/green]")
            return True

        except Exception as e:
            console.print(f"[red]Error filtering token: {e}[/red]")
            return False
        finally:
            try:
                c = locals().get("candidate")
                if c and getattr(c, "mint", None):
                    self.prev_candidate_state[c.mint] = {
                        "liquidity": float(getattr(c, "liquidity", 0) or 0),
                        "volume_5m": float(getattr(c, "volume_5m", 0) or 0),
                        "ts": time.time(),
                    }
            except Exception:
                pass

    async def _fetch_token_data(self, candidate: TokenCandidate):
        """Fetch detailed token data from DexScreener.

        Args:
            candidate: Token candidate to update with data
        """
        try:
            # Lightweight cache to avoid hammering DexScreener on hot mints.
            now = time.time()
            cached = self._dex_cache.get(candidate.mint)
            if cached:
                ts = float(cached.get("ts") or 0.0)
                if ts and (now - ts) <= float(self.dex_cache_ttl_s):
                    # Apply cached fields (preserve quote-based price in signal-first when configured).
                    if not (self.signal_first and self.launch_signals_file and self.signal_preserve_jup_price and candidate.price > 0):
                        candidate.price = float(cached.get("price") or candidate.price or 0.0)
                    candidate.liquidity = float(cached.get("liquidity") or candidate.liquidity or 0.0)
                    candidate.market_cap = float(cached.get("market_cap") or candidate.market_cap or 0.0)
                    if cached.get("symbol"):
                        candidate.symbol = str(cached.get("symbol") or candidate.symbol or "")
                    candidate.price_change_5m = float(cached.get("price_change_5m") or candidate.price_change_5m or 0.0)
                    candidate.price_change_1h = float(cached.get("price_change_1h") or candidate.price_change_1h or 0.0)
                    candidate.buys_1h = int(cached.get("buys_1h") or candidate.buys_1h or 0)
                    candidate.sells_1h = int(cached.get("sells_1h") or candidate.sells_1h or 0)
                    candidate.txns_1h = int(cached.get("txns_1h") or candidate.txns_1h or 0)
                    candidate.buys_5m = int(cached.get("buys_5m") or candidate.buys_5m or 0)
                    candidate.sells_5m = int(cached.get("sells_5m") or candidate.sells_5m or 0)
                    candidate.txns_5m = int(cached.get("txns_5m") or candidate.txns_5m or 0)
                    candidate.volume_1h = float(cached.get("volume_1h") or candidate.volume_1h or 0.0)
                    candidate.volume_5m = float(cached.get("volume_5m") or candidate.volume_5m or 0.0)
                    # candidate.discovered_at is used as pool age; do not overwrite with cached value.
                    return

            async with httpx.AsyncClient(timeout=10.0) as client:
                url = f"{DEXSCREENER_TOKEN}/{candidate.mint}"
                resp = await client.get(url)
                if resp.status_code != 200:
                    # Fallback: in signal-first mode we may have no DexScreener access.
                    if self.signal_first and self.launch_signals_file:
                        await self._fetch_signal_quote_data(candidate)
                    return

                data = resp.json()
                pairs = data.get('pairs', [])
                if not pairs:
                    if self.signal_first and self.launch_signals_file:
                        await self._fetch_signal_quote_data(candidate)
                    return

                # Use the highest liquidity pair
                best_pair = max(pairs, key=lambda p: float(p.get('liquidity', {}).get('usd', 0) or 0))

                # Basic data
                liquidity = best_pair.get('liquidity', {})
                candidate.liquidity = float(liquidity.get('usd', 0) or 0)
                # DexScreener uses `fdv` for many pairs; treat as a market-cap proxy.
                candidate.market_cap = float(best_pair.get('fdv', 0) or 0)
                # In signal-first hybrid mode, keep the quote-based price unless missing.
                if not (self.signal_first and self.launch_signals_file and self.signal_preserve_jup_price and candidate.price > 0):
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
                m5_txns = txns.get('m5', {})
                candidate.buys_1h = int(h1_txns.get('buys', 0) or 0)
                candidate.sells_1h = int(h1_txns.get('sells', 0) or 0)
                candidate.txns_1h = candidate.buys_1h + candidate.sells_1h
                candidate.buys_5m = int(m5_txns.get('buys', 0) or 0)
                candidate.sells_5m = int(m5_txns.get('sells', 0) or 0)
                candidate.txns_5m = candidate.buys_5m + candidate.sells_5m

                # Volume data
                volume = best_pair.get('volume', {})
                candidate.volume_1h = float(volume.get('h1', 0) or 0)
                candidate.volume_5m = float(volume.get('m5', 0) or 0)

                # Pair creation time - use actual on-chain creation, not discovery time
                pair_created_at = best_pair.get('pairCreatedAt')
                if pair_created_at:
                    # pairCreatedAt is in milliseconds
                    candidate.discovered_at = pair_created_at / 1000.0

                # Cache the extracted metrics (avoid storing sensitive data).
                try:
                    self._dex_cache[candidate.mint] = {
                        "ts": time.time(),
                        "price": float(best_pair.get('priceUsd', 0) or 0),
                        "liquidity": float(liquidity.get('usd', 0) or 0),
                        "market_cap": float(best_pair.get('fdv', 0) or 0),
                        "symbol": base_token.get('symbol') or "",
                        "price_change_5m": float(price_change.get('m5', 0) or 0),
                        "price_change_1h": float(price_change.get('h1', 0) or 0),
                        "buys_1h": int(h1_txns.get('buys', 0) or 0),
                        "sells_1h": int(h1_txns.get('sells', 0) or 0),
                        "txns_1h": int((h1_txns.get('buys', 0) or 0) + (h1_txns.get('sells', 0) or 0)),
                        "buys_5m": int(m5_txns.get('buys', 0) or 0),
                        "sells_5m": int(m5_txns.get('sells', 0) or 0),
                        "txns_5m": int((m5_txns.get('buys', 0) or 0) + (m5_txns.get('sells', 0) or 0)),
                        "volume_1h": float(volume.get('h1', 0) or 0),
                        "volume_5m": float(volume.get('m5', 0) or 0),
                    }
                except Exception:
                    pass

        except Exception as e:
            if meme_config.VERBOSE_LOGGING:
                console.print(f"[yellow]Error fetching token data: {e}[/yellow]")
            if self.signal_first and self.launch_signals_file:
                try:
                    await self._fetch_signal_quote_data(candidate)
                except Exception:
                    pass

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
            # Optional Birdeye lockInfo check (if enabled)
            if meme_config.LP_LOCK_BIRDEYE_ENABLED and HAS_NICE_FUNCS and token_security_raw is not None:
                try:
                    sec = await asyncio.to_thread(token_security_raw, candidate.mint)
                    if sec:
                        lock_info = sec.get("lockInfo") or sec.get("lock_info") or sec.get("lpLock") or sec.get("lp_lock")
                        if isinstance(lock_info, dict):
                            locked_flag = lock_info.get("locked") or lock_info.get("isLocked")
                            locked_pct = lock_info.get("lockedPercent") or lock_info.get("locked_pct") or lock_info.get("lockPercent")
                            if locked_flag is True:
                                return True
                            if locked_pct is not None:
                                try:
                                    if float(locked_pct) >= meme_config.LP_LOCK_MIN_PERCENT:
                                        return True
                                except Exception:
                                    pass
                            # If Birdeye explicitly says not locked and strict, reject
                            if meme_config.LP_LOCK_STRICT:
                                return False
                        elif lock_info is not None and meme_config.LP_LOCK_STRICT:
                            return False
                except Exception:
                    if meme_config.LP_LOCK_STRICT:
                        return False

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
            if self.signal_first and self.launch_signals_file:
                # Signal-first scoring: liquidity + quote impact + local momentum.
                await self._fetch_signal_quote_data(candidate)
                if candidate.price <= 0:
                    candidate.composite_score = 0
                    return 0

                liq = float(candidate.liquidity or 0.0)
                impact = float(candidate.price_impact_pct or 0.0)
                mom = float(candidate.price_change_5m or 0.0)

                # Signal-first score prioritizes:
                # - tradability (quote impact)
                # - immediate drift (local quote momentum)
                # - early demand from WS-derived metrics (hits/buys/unique buyers/net SOL in)
                #
                # We keep this lightweight: no DexScreener/Birdeye dependency.
                if self.signal_max_impact_pct > 0:
                    impact_score = max(
                        0.0,
                        min(60.0, (self.signal_max_impact_pct - impact) / self.signal_max_impact_pct * 60.0),
                    )
                else:
                    impact_score = 0.0

                # Local momentum from the last quote sample, bounded (10% -> 20 pts).
                mom_score = max(0.0, min(20.0, mom * 2.0))

                metrics = self.launch_signal_metrics.get(candidate.mint, {}) or {}
                try:
                    hits = int(metrics.get("hits") or 0)
                    buys = int(metrics.get("buys") or 0)
                    sells = int(metrics.get("sells") or 0)
                    uniq = int(metrics.get("unique_buyers") or 0)
                    net_sol_in = float(metrics.get("net_sol_in") or 0.0)
                    buy_accel = metrics.get("buy_accel")
                    top_share = metrics.get("top_buyer_share")
                    buy_max_sol = metrics.get("buy_max_sol")
                    t_first_sell_s = metrics.get("t_first_sell_s")
                except Exception:
                    hits = buys = sells = uniq = 0
                    net_sol_in = 0.0
                    buy_accel = None
                    top_share = None
                    buy_max_sol = None
                    t_first_sell_s = None

                demand = 0.0
                demand += min(6.0, float(hits))  # 0..6
                demand += min(6.0, float(uniq) * 2.0)  # 0..6
                # Coarse step function keeps it robust to noisy approximations.
                if net_sol_in >= 0.10:
                    demand += 3.0
                if net_sol_in >= 0.25:
                    demand += 3.0
                if net_sol_in >= 0.50:
                    demand += 3.0
                if net_sol_in >= 1.00:
                    demand += 3.0
                # Small bonus for liquidity when known (often unknown in signal-first).
                if liq > 0:
                    demand += min(2.0, (liq / 20000.0) * 2.0)
                # Penalize obvious sell pressure in the early window.
                if sells > buys:
                    demand -= min(8.0, float(sells - buys) * 2.0)
                # Penalize very fast first sells (soft penalty, avoids hard starvation).
                try:
                    if sells > 0 and t_first_sell_s is not None:
                        tfs = float(t_first_sell_s)
                        if tfs <= 0.5:
                            demand -= 6.0
                        elif tfs <= 2.0:
                            demand -= 3.0
                except Exception:
                    pass

                # Reward accelerating buy flow (when available).
                try:
                    if buy_accel is not None:
                        demand += max(0.0, min(4.0, float(buy_accel) * 8.0))
                except Exception:
                    pass

                # Penalize single-wallet concentration (when available).
                try:
                    if top_share is not None and float(top_share) > 0:
                        if float(top_share) >= 0.70:
                            demand -= 6.0
                        elif float(top_share) >= 0.55:
                            demand -= 3.0
                except Exception:
                    pass

                # Penalize one huge early buy, which often precedes a rug/manip dump.
                try:
                    if buy_max_sol is not None and float(buy_max_sol) >= 5.0:
                        demand -= 3.0
                except Exception:
                    pass
                demand_score = max(0.0, min(20.0, demand))

                base_composite = impact_score + mom_score + demand_score
                winner_score, winner_used = self._score_winner_profile(candidate)
                if self.winner_profile_enabled and winner_used > 0 and self.winner_score_weight > 0:
                    w = max(0.0, min(100.0, float(self.winner_score_weight))) / 100.0
                    composite = ((1.0 - w) * float(base_composite)) + (w * float(winner_score))
                else:
                    composite = float(base_composite)
                candidate.composite_score = int(min(100, max(0, composite)))
                candidate.vhi_score = int(mom_score + demand_score)
                candidate.graduation_score = float(impact_score)
                return candidate.composite_score

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

            # Momentum divergence penalty: short-term pump without longer trend confirmation
            # catches pump-and-dumps where 5m spikes but 1h is flat
            divergence_penalty = 0
            if candidate.price_change_5m > 10 and candidate.price_change_1h < 3:
                divergence_penalty = 15

            # Compute final score
            base_composite = momentum_score + volume_score + liquidity_score + social_score - divergence_penalty
            winner_score, winner_used = self._score_winner_profile(candidate)
            if self.winner_profile_enabled and winner_used > 0 and self.winner_score_weight > 0:
                w = max(0.0, min(100.0, float(self.winner_score_weight))) / 100.0
                composite = ((1.0 - w) * float(base_composite)) + (w * float(winner_score))
            else:
                composite = float(base_composite)
            candidate.composite_score = int(min(100, max(0, composite)))

            # Store component scores for display
            candidate.vhi_score = int(momentum_score)  # Repurpose as momentum
            candidate.graduation_score = float(volume_score)  # Repurpose as volume

            if meme_config.VERBOSE_LOGGING or candidate.composite_score >= meme_config.MIN_VHI_SCORE:
                div_str = f", Div:-{divergence_penalty}" if divergence_penalty else ""
                winner_str = ""
                if self.winner_profile_enabled and candidate.winner_features_used > 0:
                    winner_str = f", Win:{candidate.winner_score:.1f}/{candidate.winner_features_used}"
                console.print(
                    f"[cyan]SCORE: {candidate.symbol} = {candidate.composite_score} "
                    f"(Mom:{momentum_score}, Vol:{volume_score}, Liq:{liquidity_score}, "
                    f"Soc:{social_score}{div_str}{winner_str})[/cyan]"
                )

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
            if self.signal_first and self.launch_signals_file:
                self._signal_debug_write("reject_min_score", candidate, {"min_score": meme_config.MIN_VHI_SCORE})
            return False

        # Winner-first gate: require candidate to resemble historical winners.
        if self.winner_profile_enabled:
            used = int(getattr(candidate, "winner_features_used", 0) or 0)
            wscore = float(getattr(candidate, "winner_score", 0.0) or 0.0)
            if self.winner_require_min_features and used < int(self.winner_min_features):
                if self.signal_first and self.launch_signals_file:
                    self._signal_debug_write(
                        "reject_winner_features",
                        candidate,
                        {"used": used, "min_features": int(self.winner_min_features)},
                    )
                return False
            if used > 0 and wscore < float(self.winner_min_score):
                if self.signal_first and self.launch_signals_file:
                    self._signal_debug_write(
                        "reject_winner_score",
                        candidate,
                        {"winner_score": wscore, "min_winner_score": float(self.winner_min_score), "used": used},
                    )
                return False

        # Secondary guard: if winner-zone enforcement is on in signal-first mode,
        # block entries that did not receive a matching zone id during filtering.
        if self.winner_zone_enabled and self.winner_zone_enforce and self.signal_first and self.launch_signals_file:
            winner_zone_id = str(getattr(candidate, "winner_zone_id", "") or "")
            winner_zone_bypassed = bool(getattr(candidate, "winner_zone_bypassed", False))
            if (not winner_zone_id) and (not winner_zone_bypassed):
                self._signal_debug_write("reject_winner_zone_enter", candidate, {})
                return False

        # Stateful entry pattern gate (impulse level-transition OR base-breakout).
        pattern_ok, pattern_meta = self._entry_pattern_gate(candidate)
        if not pattern_ok:
            if self.signal_first and self.launch_signals_file:
                self._signal_debug_write("reject_entry_pattern", candidate, pattern_meta)
                self._set_entry_reject_cooldown(candidate.mint, reason="reject_entry_pattern")
            return False
        if self.signal_first and self.launch_signals_file:
            self._signal_debug_write("pass_entry_pattern", candidate, pattern_meta)

        # Check max positions
        if len(self.active_positions) >= meme_config.MAX_POSITIONS:
            console.print(f"[yellow]Max positions reached ({meme_config.MAX_POSITIONS})[/yellow]")
            self._entry_pattern_clear_cooldown(candidate.mint, reason="max_positions")
            return False

        # Check if already holding this token
        if candidate.mint in self.active_positions:
            self._entry_pattern_clear_cooldown(candidate.mint, reason="already_holding")
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
            # Keep symbol non-empty for logs/Discord even when we only have a mint.
            if not getattr(candidate, "symbol", ""):
                candidate.symbol = candidate.mint[:4]

            # Signal-first hard pre-trade liquidity guard.
            # This prevents accidental entries on unknown/zero-liquidity candidates.
            if self.signal_first and self.launch_signals_file:
                try:
                    sig_min_liq_default = float(getattr(meme_config, "MIN_LIQUIDITY_EARLY", 0.0) or 0.0)
                except Exception:
                    sig_min_liq_default = 0.0
                try:
                    sig_min_liq = float(os.getenv("MEME_SIGNAL_MIN_LIQUIDITY_USD", str(sig_min_liq_default)) or sig_min_liq_default)
                except Exception:
                    sig_min_liq = sig_min_liq_default
                require_sig_liq = str(os.getenv("MEME_SIGNAL_REQUIRE_LIQUIDITY", "true") or "true").lower() in (
                    "1",
                    "true",
                    "yes",
                )
                try:
                    liq_now = float(getattr(candidate, "liquidity", 0.0) or 0.0)
                except Exception:
                    liq_now = 0.0
                if require_sig_liq and liq_now <= 0.0:
                    metrics = self.launch_signal_metrics.get(candidate.mint, {}) or {}
                    liq_now = self._maybe_apply_signal_liquidity_fallback(candidate, metrics, context="entry")
                if require_sig_liq and liq_now <= 0.0:
                    console.print(f"[yellow]REJECTED ENTRY: {candidate.symbol} missing liquidity metric[/yellow]")
                    self._entry_pattern_clear_cooldown(candidate.mint, reason="reject_entry_liq_missing")
                    self._set_entry_reject_cooldown(candidate.mint, reason="reject_entry_liq_missing")
                    return None
                if sig_min_liq > 0.0 and liq_now > 0.0 and liq_now < sig_min_liq:
                    console.print(
                        f"[yellow]REJECTED ENTRY: {candidate.symbol} liquidity below signal floor "
                        f"(${liq_now:,.0f} < ${sig_min_liq:,.0f})[/yellow]"
                    )
                    self._entry_pattern_clear_cooldown(candidate.mint, reason="reject_entry_liq_low")
                    self._set_entry_reject_cooldown(candidate.mint, reason="reject_entry_liq_low")
                    return None

            # Signal-first: quote confirmation gate
            if self.signal_first and self.launch_signals_file:
                ok = await self._confirm_signal_entry(candidate)
                if not ok:
                    if meme_config.VERBOSE_LOGGING:
                        console.print(f"[yellow]CONFIRM FAIL: {candidate.mint[:8]} {candidate.symbol}[/yellow]")
                    self._entry_pattern_clear_cooldown(candidate.mint, reason="reject_entry_confirm")
                    self._set_entry_reject_cooldown(candidate.mint, reason="reject_entry_confirm")
                    return None

            # Calculate position size based on score
            size_sol = meme_config.get_position_size_for_score(candidate.composite_score)
            size_sol = max(meme_config.MIN_POSITION_SIZE_SOL, min(meme_config.MAX_POSITION_SIZE_SOL, size_sol))
            winner_size_mult = float(self._winner_size_multiplier(candidate))
            if winner_size_mult != 1.0:
                size_sol *= winner_size_mult
                size_sol = max(meme_config.MIN_POSITION_SIZE_SOL, min(meme_config.MAX_POSITION_SIZE_SOL, size_sol))
            if bool(getattr(candidate, "liquidity_estimated", False)):
                est_mult = max(0.20, min(1.0, float(self.signal_estimated_liq_size_mult)))
                if est_mult < 1.0:
                    size_sol *= est_mult
                    size_sol = max(meme_config.MIN_POSITION_SIZE_SOL, min(meme_config.MAX_POSITION_SIZE_SOL, size_sol))

            # Signal-first: scale size down for tiny market caps to reduce MAX_LOSS_CAP bleed.
            # We don't hard-reject microcaps by default; we just risk less.
            mcap_size_mult = 1.0
            if self.signal_first and self.launch_signals_file:
                try:
                    mcap = float(candidate.market_cap or 0.0)
                except Exception:
                    mcap = 0.0
                if mcap > 0:
                    try:
                        ref = float(os.getenv("MEME_SIGNAL_MCAP_SIZE_REF_USD", "30000") or 30000)
                    except Exception:
                        ref = 30000.0
                    try:
                        min_mult = float(os.getenv("MEME_SIGNAL_MCAP_SIZE_MIN_MULT", "0.25") or 0.25)
                    except Exception:
                        min_mult = 0.25
                    if ref > 0:
                        # sqrt scaling: 7.5k -> 0.5x, 30k -> 1.0x, 120k -> 1.0x
                        mcap_size_mult = math.sqrt(max(0.0, mcap) / ref)
                        mcap_size_mult = max(float(min_mult), min(1.0, float(mcap_size_mult)))
                        size_sol *= mcap_size_mult
            scout_size_mult = 1.0
            if self.signal_first and self.launch_signals_file and bool(getattr(candidate, "mcap_scout_mode", False)):
                scout_size_mult = max(0.10, min(1.0, float(self.signal_scout_size_mult)))
                if scout_size_mult < 1.0:
                    size_sol *= scout_size_mult

            # Get real SOL price for USD calculation (cached)
            sol_price_usd = await self._get_sol_price()

            # Global risk clamps (all modes):
            # 1) Absolute USD cap to bound worst-case gap losses (meme coins can go to ~0 instantly).
            # 2) Liquidity-relative cap so we don't take "too big for this pool" entries.
            try:
                max_pos_usd = float(os.getenv("MEME_MAX_POSITION_USD", "0") or 0.0)
            except Exception:
                max_pos_usd = 0.0
            try:
                max_pos_liq_pct = float(os.getenv("MEME_MAX_POSITION_LIQ_PCT", "0") or 0.0)
            except Exception:
                max_pos_liq_pct = 0.0

            if sol_price_usd and sol_price_usd > 0:
                est_usd = float(size_sol) * float(sol_price_usd)

                liq_usd = 0.0
                try:
                    liq_usd = float(getattr(candidate, "liquidity", 0.0) or 0.0)
                except Exception:
                    liq_usd = 0.0

                cap_usd = None
                if max_pos_usd > 0:
                    cap_usd = float(max_pos_usd)
                if max_pos_liq_pct > 0 and liq_usd > 0:
                    liq_cap = float(liq_usd) * float(max_pos_liq_pct)
                    cap_usd = liq_cap if cap_usd is None else min(float(cap_usd), float(liq_cap))

                if cap_usd is not None and cap_usd > 0 and est_usd > float(cap_usd):
                    size_sol = float(cap_usd) / float(sol_price_usd)
                    size_sol = max(meme_config.MIN_POSITION_SIZE_SOL, min(meme_config.MAX_POSITION_SIZE_SOL, size_sol))

            # Signal-first: cap size in USD so MAX_LOSS/FAIL_FAST can't blow out sessions.
            # This is intentionally conservative until the pipeline shows stable edge.
            if self.signal_first and self.launch_signals_file:
                try:
                    max_usd = float(os.getenv("MEME_SIGNAL_MAX_POSITION_USD", "6.0") or 6.0)
                except Exception:
                    max_usd = 6.0
                if max_usd > 0 and sol_price_usd > 0:
                    est_usd = float(size_sol) * float(sol_price_usd)
                    if est_usd > max_usd:
                        size_sol = float(max_usd) / float(sol_price_usd)
                        size_sol = max(meme_config.MIN_POSITION_SIZE_SOL, min(meme_config.MAX_POSITION_SIZE_SOL, size_sol))

            # Signal-first: optional minimum position floor in USD.
            # If the computed size falls below this floor, skip the entry
            # to avoid "dust-size" trades that are not economically useful.
            if self.signal_first and self.launch_signals_file:
                try:
                    min_usd = float(os.getenv("MEME_SIGNAL_MIN_POSITION_USD", "0") or 0.0)
                except Exception:
                    min_usd = 0.0
                if min_usd > 0 and sol_price_usd > 0:
                    est_usd = float(size_sol) * float(sol_price_usd)
                    if est_usd < float(min_usd):
                        if meme_config.VERBOSE_LOGGING:
                            console.print(
                                f"[yellow]SKIP ENTRY: {candidate.symbol} size below floor "
                                f"(${est_usd:.2f} < ${min_usd:.2f})[/yellow]"
                            )
                        if self.signal_first and self.launch_signals_file:
                            self._signal_debug_write(
                                "skip_entry_size_floor",
                                candidate,
                                {"size_usd": float(est_usd), "size_floor_usd": float(min_usd)},
                            )
                        self._entry_pattern_clear_cooldown(candidate.mint, reason="skip_entry_size_floor")
                        self._set_entry_reject_cooldown(candidate.mint, reason="skip_entry_size_floor")
                        return None

            # Signal-first: entry-time liquidity/impact check at the intended size.
            # If impact is too high, scale the size down instead of taking a full-size flyer.
            if self.signal_first and self.launch_signals_file:
                try:
                    entry_max_impact = float(os.getenv("MEME_ENTRY_MAX_IMPACT_PCT", "0.25") or 0.25)
                except Exception:
                    entry_max_impact = 0.25
                if entry_max_impact > 0:
                    for _ in range(4):
                        await self._fetch_signal_quote_data(candidate, purpose="candidate", amount_sol_override=float(size_sol))
                        if not getattr(candidate, "price", 0):
                            self._entry_pattern_clear_cooldown(candidate.mint, reason="reject_entry_quote")
                            self._set_entry_reject_cooldown(candidate.mint, reason="reject_entry_quote")
                            return None
                        try:
                            imp = float(getattr(candidate, "price_impact_pct", 0.0) or 0.0)
                        except Exception:
                            imp = 0.0
                        # If impact is high, scale down size proportionally (bounded) and re-quote.
                        if imp > entry_max_impact and size_sol > meme_config.MIN_POSITION_SIZE_SOL:
                            scale = max(0.20, min(1.0, float(entry_max_impact) / float(imp)))
                            size_sol = max(meme_config.MIN_POSITION_SIZE_SOL, float(size_sol) * scale)
                            continue
                        break

            # Optional scale-in: enter with a fraction, then add only if it moves in our favor.
            target_size_sol = float(size_sol)
            # Enable scale-in in *all* modes when configured. In signal-first mode we may also
            # re-quote at the smaller entry size to avoid full-size slippage assumptions.
            scale_in = bool(self.scale_in_enabled)
            entry_size_sol = float(size_sol)
            if scale_in:
                frac = max(0.05, min(1.0, float(self.scale_in_initial_fraction)))
                entry_size_sol = max(float(meme_config.MIN_POSITION_SIZE_SOL), float(target_size_sol) * frac)
                entry_size_sol = min(float(target_size_sol), float(entry_size_sol))
                # Re-quote at the actual entry size so paper fills don't inherit full-size slippage.
                try:
                    if (self.signal_first and self.launch_signals_file) and abs(entry_size_sol - target_size_sol) > 1e-6:
                        await self._fetch_signal_quote_data(candidate, purpose="candidate", amount_sol_override=float(entry_size_sol))
                except Exception:
                    pass
                size_sol = float(entry_size_sol)

            # Final entry-size floor check after all scaling/caps (including impact+scale-in).
            # This guarantees the actual ticket is not a dust-size fill.
            if self.signal_first and self.launch_signals_file:
                try:
                    min_usd = float(os.getenv("MEME_SIGNAL_MIN_POSITION_USD", "0") or 0.0)
                except Exception:
                    min_usd = 0.0
                if min_usd > 0 and sol_price_usd > 0:
                    entry_usd = float(size_sol) * float(sol_price_usd)
                    if entry_usd < float(min_usd):
                        floor_sol = float(min_usd) / float(sol_price_usd)
                        if floor_sol <= float(target_size_sol) + 1e-9:
                            size_sol = max(float(meme_config.MIN_POSITION_SIZE_SOL), float(floor_sol))
                        else:
                            if meme_config.VERBOSE_LOGGING:
                                console.print(
                                    f"[yellow]SKIP ENTRY: {candidate.symbol} final size below floor "
                                    f"(${entry_usd:.2f} < ${min_usd:.2f})[/yellow]"
                                )
                            self._signal_debug_write(
                                "skip_entry_size_floor_final",
                                candidate,
                                {
                                    "entry_usd": float(entry_usd),
                                    "size_floor_usd": float(min_usd),
                                    "target_usd": float(target_size_sol) * float(sol_price_usd),
                                },
                            )
                            self._entry_pattern_clear_cooldown(candidate.mint, reason="skip_entry_size_floor_final")
                            self._set_entry_reject_cooldown(candidate.mint, reason="skip_entry_size_floor_final")
                            return None

            size_usd = size_sol * sol_price_usd
            target_usd = float(target_size_sol) * float(sol_price_usd)

            # Entry-time hard risk checks:
            # - mint/freeze authority
            # - holder concentration
            # - routeability back to SOL (anti-honeypot/untradable guard)
            if self.entry_hard_risk_checks_enabled:
                mf_ok, mf_reason = await self._check_mint_freeze_risk(candidate.mint)
                if not mf_ok:
                    console.print(f"[red]REJECTED ENTRY: {candidate.symbol} mint/freeze risk: {mf_reason}[/red]")
                    if self.signal_first and self.launch_signals_file:
                        self._signal_debug_write("reject_entry_mint_freeze", candidate, {"reason": mf_reason})
                    self._entry_pattern_clear_cooldown(candidate.mint, reason="reject_entry_mint_freeze")
                    self._set_entry_reject_cooldown(candidate.mint, reason="reject_entry_mint_freeze")
                    return None

                holder_ok, holder_reason = await self._check_holder_concentration(candidate.mint)
                if not holder_ok:
                    console.print(f"[red]REJECTED ENTRY: {candidate.symbol} holder concentration: {holder_reason}[/red]")
                    if self.signal_first and self.launch_signals_file:
                        self._signal_debug_write("reject_entry_holder", candidate, {"reason": holder_reason})
                    self._entry_pattern_clear_cooldown(candidate.mint, reason="reject_entry_holder")
                    self._set_entry_reject_cooldown(candidate.mint, reason="reject_entry_holder")
                    return None

                sell_ok, sell_reason = await self._check_entry_sellability(candidate, float(size_sol))
                if not sell_ok:
                    console.print(f"[red]REJECTED ENTRY: {candidate.symbol} sellability: {sell_reason}[/red]")
                    if self.signal_first and self.launch_signals_file:
                        self._signal_debug_write("reject_entry_sellability", candidate, {"reason": sell_reason})
                    self._entry_pattern_clear_cooldown(candidate.mint, reason="reject_entry_sellability")
                    self._set_entry_reject_cooldown(candidate.mint, reason="reject_entry_sellability")
                    return None
                if self.signal_first and self.launch_signals_file:
                    self._signal_debug_write("entry_sellability_pass", candidate, {"detail": sell_reason})

            entry_text = (
                f"ENTRY SIGNAL: {candidate.symbol}\n"
                f"Score: {candidate.composite_score}\n"
                f"Size: {size_sol:.2f} SOL (${size_usd:.2f})\n"
            )
            if scale_in and target_size_sol > size_sol:
                entry_text += f"Target: {target_size_sol:.2f} SOL (${target_usd:.2f})\n"
            entry_text += (
                f"Price: ${candidate.price:.10f}\n"
                f"Liquidity: ${candidate.liquidity:,.0f}\n"
                f"Market Cap: ${candidate.market_cap:,.0f}"
            )
            console.print(Panel(entry_text, title="BUY SIGNAL", style="green"))

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
                if scale_in and target_size_sol > size_sol:
                    position.target_amount_sol = float(target_size_sol)
                    position.target_amount_usd = float(target_usd)
                    position.scale_in_enabled = True
                    position.scale_in_done = False

                # Create state for exit manager with score-based stop loss
                initial_stop = meme_config.get_stop_loss_for_score(candidate.composite_score)
                sig_score = 0.0
                sig_tier = ""
                sig_hits = 0
                sig_buys = 0
                sig_sells = 0
                sig_uniq = 0
                sig_net_sol = 0.0
                sig_top_share_f = 0.0
                sig_tfs_f = None
                sig_source = ""
                winner_score = float(getattr(candidate, "winner_score", 0.0) or 0.0)
                winner_features_used = int(getattr(candidate, "winner_features_used", 0) or 0)
                winner_zone_id = str(getattr(candidate, "winner_zone_id", "") or "")
                winner_zone_objective = float(getattr(candidate, "winner_zone_objective", 0.0) or 0.0)
                winner_zone_bypassed = bool(getattr(candidate, "winner_zone_bypassed", False))
                winner_zone_bypass_reason = str(getattr(candidate, "winner_zone_bypass_reason", "") or "")
                if self.launch_signals_file:
                    try:
                        sig_score = float(self.launch_signal_scores.get(candidate.mint, 0.0) or 0.0)
                    except Exception:
                        sig_score = 0.0
                    try:
                        sig_tier = self._signal_tier(sig_score)
                    except Exception:
                        sig_tier = ""
                    sig_metrics = self.launch_signal_metrics.get(candidate.mint, {}) or {}
                    try:
                        sig_hits = int(sig_metrics.get("hits") or 0)
                        sig_buys = int(sig_metrics.get("buys") or 0)
                        sig_sells = int(sig_metrics.get("sells") or 0)
                        sig_uniq = int(sig_metrics.get("unique_buyers") or 0)
                        sig_net_sol = float(sig_metrics.get("net_sol_in") or 0.0)
                        sig_top_share = sig_metrics.get("top_buyer_share")
                        sig_top_share_f = float(sig_top_share) if sig_top_share is not None else 0.0
                        sig_tfs = sig_metrics.get("t_first_sell_s")
                        sig_tfs_f = float(sig_tfs) if sig_tfs is not None else None
                        sig_source = str(sig_metrics.get("source") or "") if isinstance(sig_metrics, dict) else ""
                    except Exception:
                        sig_hits = sig_buys = sig_sells = sig_uniq = 0
                        sig_net_sol = 0.0
                        sig_top_share_f = 0.0
                        sig_tfs_f = None
                        sig_source = ""
                position.state = PositionState(
                    mint=candidate.mint,
                    symbol=candidate.symbol,
                    entry_price=candidate.price,
                    entry_time=time.time(),
                    amount_tokens=amount_tokens,
                    amount_usd=size_usd,
                    score=candidate.composite_score,
                    initial_stop_pct=initial_stop,
                    market_cap_entry=float(candidate.market_cap or 0.0),
                    signal_score=sig_score,
                    signal_tier=sig_tier,
                    signal_hits=sig_hits,
                    signal_buys=sig_buys,
                    signal_sells=sig_sells,
                    signal_unique_buyers=sig_uniq,
                    signal_net_sol_in=sig_net_sol,
                    signal_top_buyer_share=sig_top_share_f,
                    signal_t_first_sell_s=sig_tfs_f,
                )
                try:
                    position.state.winner_score = float(winner_score)
                    position.state.winner_features_used = int(winner_features_used)
                    position.state.winner_zone_id = str(winner_zone_id)
                    position.state.winner_zone_objective = float(winner_zone_objective)
                    position.state.winner_zone_bypassed = bool(winner_zone_bypassed)
                    position.state.winner_zone_bypass_reason = str(winner_zone_bypass_reason)
                except Exception:
                    pass
                try:
                    position.state.run_id = str(getattr(self, "run_id", "") or "")
                except Exception:
                    pass
                try:
                    position.state.signal_mcap_size_mult = float(mcap_size_mult)
                except Exception:
                    pass
                try:
                    position.state.signal_source = str(sig_source or "")
                except Exception:
                    pass
                try:
                    position.state.mcap_scout_mode = bool(getattr(candidate, "mcap_scout_mode", False))
                    position.state.mcap_scout_size_mult = float(scout_size_mult)
                except Exception:
                    pass
                try:
                    position.state.winner_size_mult = float(winner_size_mult)
                except Exception:
                    pass
                try:
                    position.state.liquidity_estimated = bool(getattr(candidate, "liquidity_estimated", False))
                except Exception:
                    pass
                # Capture entry microstructure features for later attribution.
                try:
                    position.state.liquidity_entry = float(getattr(candidate, "liquidity", 0.0) or 0.0)
                    position.state.price_change_5m_entry = float(getattr(candidate, "price_change_5m", 0.0) or 0.0)
                    position.state.buys_5m_entry = int(getattr(candidate, "buys_5m", 0) or 0)
                    position.state.sells_5m_entry = int(getattr(candidate, "sells_5m", 0) or 0)
                    position.state.txns_5m_entry = int(getattr(candidate, "txns_5m", 0) or 0)
                    position.state.volume_5m_entry = float(getattr(candidate, "volume_5m", 0.0) or 0.0)
                except Exception:
                    pass

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
                        metadata={
                            'paper_mode': True,
                            'run_id': self.run_id,
                            'score': candidate.composite_score,
                            'liquidity_entry': float(getattr(candidate, "liquidity", 0.0) or 0.0),
                            'market_cap_entry': float(getattr(candidate, "market_cap", 0.0) or 0.0),
                            'price_change_5m_entry': float(getattr(candidate, "price_change_5m", 0.0) or 0.0),
                            'buys_5m_entry': int(getattr(candidate, "buys_5m", 0) or 0),
                            'sells_5m_entry': int(getattr(candidate, "sells_5m", 0) or 0),
                            'txns_5m_entry': int(getattr(candidate, "txns_5m", 0) or 0),
                            'volume_5m_entry': float(getattr(candidate, "volume_5m", 0.0) or 0.0),
                            'signal_score': sig_score,
                            'signal_tier': sig_tier,
                            'signal_hits': sig_hits,
                            'signal_buys': sig_buys,
                            'signal_sells': sig_sells,
                            'signal_unique_buyers': sig_uniq,
                            'signal_net_sol_in': sig_net_sol,
                            'signal_top_buyer_share': sig_top_share_f,
                            'signal_t_first_sell_s': sig_tfs_f,
                            'winner_score': float(winner_score),
                            'winner_features_used': int(winner_features_used),
                            'winner_zone_id': str(winner_zone_id),
                            'winner_zone_objective': float(winner_zone_objective),
                            'winner_zone_bypassed': bool(winner_zone_bypassed),
                            'winner_zone_bypass_reason': str(winner_zone_bypass_reason),
                            'winner_size_mult': float(winner_size_mult),
                            'liquidity_estimated': bool(getattr(candidate, "liquidity_estimated", False)),
                            'signal_mcap_size_mult': float(mcap_size_mult),
                            'mcap_scout_mode': bool(getattr(candidate, "mcap_scout_mode", False)),
                            'mcap_scout_size_mult': float(scout_size_mult),
                            'scale_in_enabled': bool(position.scale_in_enabled),
                            'scale_in_target_usd': float(position.target_amount_usd or 0.0),
                            'scale_in_target_sol': float(position.target_amount_sol or 0.0),
                        }
                    )

                # Send Discord alert
                if HAS_ALERTS and meme_config.DISCORD_ALERTS_ENABLED:
                    send_system_alert(
                        f"PAPER ENTRY: {candidate.symbol}",
                        f"Score: {candidate.composite_score} | Size: {size_sol:.2f} SOL",
                        level='info'
                    )

                # Signal latency log (event-driven)
                if self.launch_signals_file:
                    sig_ts = self.launch_signal_mints.get(candidate.mint)
                    sig_score = self.launch_signal_scores.get(candidate.mint, 0.0)
                    sig_metrics = self.launch_signal_metrics.get(candidate.mint, {})
                    if sig_ts:
                        latency = time.time() - sig_ts
                        sig_price = float(sig_metrics.get("price", 0) or 0) if isinstance(sig_metrics, dict) else 0
                        entry_price = float(candidate.price or 0)
                        drift_pct = ((entry_price - sig_price) / sig_price * 100) if sig_price > 0 else None
                        try:
                            with open("data/meme_signal_latency.jsonl", "a", encoding="utf-8") as fh:
                                fh.write(json.dumps({
                                    "ts": time.time(),
                                    "mint": candidate.mint,
                                    "signal_ts": sig_ts,
                                    "entry_ts": time.time(),
                                    "latency_sec": round(latency, 2),
                                    "signal_score": sig_score,
                                    "signal_tier": self._signal_tier(sig_score),
                                    "signal_price": sig_price,
                                    "entry_price": entry_price,
                                    "entry_drift_pct": round(drift_pct, 2) if drift_pct is not None else None,
                                }) + "\n")
                        except Exception:
                            pass
                if self.launch_signals_file:
                    self.launch_signal_seen.add(candidate.mint)

                entry_ts = time.time()
                self.entry_timestamps.append(entry_ts)
                self._last_entry_ts = float(entry_ts)
                self._entry_reject_until.pop(candidate.mint, None)
                console.print(f"[green]PAPER ENTRY: {candidate.symbol} @ ${candidate.price:.10f}[/green]")
                return position

            else:
                # Live trade: execute via Jupiter
                if not HAS_TRADE_EXECUTOR:
                    console.print("[red]Trade executor not available[/red]")
                    self._entry_pattern_clear_cooldown(candidate.mint, reason="reject_entry_no_executor")
                    self._set_entry_reject_cooldown(candidate.mint, reason="reject_entry_no_executor")
                    return None

                # Pre-trade safety checks
                if self.safeguards:
                    safe, reason = await self.safeguards.pre_trade_checks(size_sol, candidate.mint)
                    if not safe:
                        console.print(f"[red]TRADE BLOCKED: {reason}[/red]")
                        self._entry_pattern_clear_cooldown(candidate.mint, reason="reject_entry_safeguard")
                        self._set_entry_reject_cooldown(candidate.mint, reason="reject_entry_safeguard")
                        return None

                # Execute swap: SOL -> Token
                # Try Jito-bundled entry first if enabled
                jito_entry_enabled = getattr(meme_config, 'JITO_ENTRY_ENABLED', False)
                if jito_entry_enabled and HAS_JITO_ENTRY and hasattr(self, 'brain') and self.brain:
                    console.print(f"[yellow]JITO ENTRY: Bundling {size_sol:.4f} SOL -> {candidate.symbol}[/yellow]")
                    jito_result = await execute_entry_jito(
                        self.brain, candidate.mint, size_sol, candidate.symbol
                    )
                    if not jito_result.get('success'):
                        console.print(f"[yellow]Jito entry failed ({jito_result.get('error', 'unknown')}), using direct swap[/yellow]")
                        await execute_swap(
                            amount=size_sol,
                            input_mint=WSOL_MINT,
                            output_mint=candidate.mint,
                            live=True
                        )
                else:
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
                    market_cap_entry=float(candidate.market_cap or 0.0),
                )
                try:
                    position.state.winner_score = float(getattr(candidate, "winner_score", 0.0) or 0.0)
                    position.state.winner_features_used = int(getattr(candidate, "winner_features_used", 0) or 0)
                    position.state.winner_size_mult = float(winner_size_mult)
                    position.state.liquidity_estimated = bool(getattr(candidate, "liquidity_estimated", False))
                    position.state.mcap_scout_mode = bool(getattr(candidate, "mcap_scout_mode", False))
                    position.state.mcap_scout_size_mult = float(scout_size_mult)
                except Exception:
                    pass

                self.active_positions[candidate.mint] = position

                # Signal latency log (event-driven)
                if self.launch_signals_file:
                    sig_ts = self.launch_signal_mints.get(candidate.mint)
                    sig_score = self.launch_signal_scores.get(candidate.mint, 0.0)
                    sig_metrics = self.launch_signal_metrics.get(candidate.mint, {})
                    if sig_ts:
                        latency = time.time() - sig_ts
                        sig_price = float(sig_metrics.get("price", 0) or 0) if isinstance(sig_metrics, dict) else 0
                        entry_price = float(candidate.price or 0)
                        drift_pct = ((entry_price - sig_price) / sig_price * 100) if sig_price > 0 else None
                        try:
                            with open("data/meme_signal_latency.jsonl", "a", encoding="utf-8") as fh:
                                fh.write(json.dumps({
                                    "ts": time.time(),
                                    "mint": candidate.mint,
                                    "signal_ts": sig_ts,
                                    "entry_ts": time.time(),
                                    "latency_sec": round(latency, 2),
                                    "signal_score": sig_score,
                                    "signal_tier": self._signal_tier(sig_score),
                                    "signal_price": sig_price,
                                    "entry_price": entry_price,
                                    "entry_drift_pct": round(drift_pct, 2) if drift_pct is not None else None,
                                }) + "\n")
                        except Exception:
                            pass
                if self.launch_signals_file:
                    self.launch_signal_seen.add(candidate.mint)

                entry_ts = time.time()
                self.entry_timestamps.append(entry_ts)
                self._last_entry_ts = float(entry_ts)
                self._entry_reject_until.pop(candidate.mint, None)
                console.print(f"[green]LIVE ENTRY: {candidate.symbol} @ ${candidate.price:.10f}[/green]")
                return position

        except Exception as e:
            console.print(f"[red]Error executing entry: {e}[/red]")
            try:
                self._entry_pattern_clear_cooldown(candidate.mint, reason="reject_entry_exception")
                self._set_entry_reject_cooldown(candidate.mint, reason="reject_entry_exception")
            except Exception:
                pass
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
            requested_sell_fraction = max(0.0, min(1.0, float(exit_result.sell_fraction or 0.0)))
            if requested_sell_fraction <= 0.0:
                return False

            exit_price = float(position.current_price or 0.0)
            current_notional = float(position.amount_tokens or 0.0) * max(0.0, exit_price)
            if current_notional <= 0.0:
                current_notional = float(position.amount_usd or 0.0)

            reason_upper = str(exit_result.reason or "").upper()
            risk_forced_exit = any(
                token in reason_upper
                for token in (
                    "STOP",
                    "DUMP",
                    "LOSS",
                    "RUG",
                    "MAX_HOLD",
                    "GAP",
                    "STAGNATION",
                    "VOLUME_COLLAPSE",
                    "FAIL_FAST",
                    "ABORT",
                    "TIMEOUT",
                    "MAX_LOSS",
                )
            )

            effective_sell_fraction = requested_sell_fraction
            moon_bag_enabled = bool(getattr(meme_config, 'MOON_BAG_ENABLED', True))
            moon_bag_floor = max(0.0, min(0.95, float(getattr(meme_config, 'MOON_BAG_FRACTION', 0.10) or 0.10)))
            moon_bag_min_usd = max(0.0, float(getattr(meme_config, 'MOON_BAG_MIN_USD', 0.0) or 0.0))
            max_nonrisk_sell_fraction = 1.0

            if moon_bag_enabled and not risk_forced_exit and effective_sell_fraction < 1.0 and current_notional > 0.0:
                keep_fraction_floor = moon_bag_floor
                if moon_bag_min_usd > 0.0:
                    keep_fraction_floor = max(
                        keep_fraction_floor,
                        min(0.99, moon_bag_min_usd / current_notional),
                    )
                max_nonrisk_sell_fraction = max(0.0, min(1.0, 1.0 - keep_fraction_floor))
                effective_sell_fraction = min(effective_sell_fraction, max_nonrisk_sell_fraction)

            min_slice_usd = max(0.0, float(getattr(meme_config, 'EXIT_MIN_SLICE_USD', 0.0) or 0.0))
            if (
                min_slice_usd > 0.0
                and not risk_forced_exit
                and effective_sell_fraction < 1.0
                and current_notional > 0.0
            ):
                min_fraction = min(1.0, min_slice_usd / current_notional)
                if effective_sell_fraction < min_fraction:
                    bumped = min(max_nonrisk_sell_fraction, min_fraction)
                    if bumped < min_fraction - 1e-6 or bumped <= 0.0:
                        console.print(
                            f"[yellow]SKIP EXIT ({position.symbol}): slice would be ${current_notional * effective_sell_fraction:.2f}, "
                            f"below MEME_EXIT_MIN_SLICE_USD=${min_slice_usd:.2f}[/yellow]"
                        )
                        return False
                    effective_sell_fraction = bumped

            effective_sell_fraction = max(0.0, min(1.0, effective_sell_fraction))
            if effective_sell_fraction <= 0.0:
                return False

            sell_amount = position.amount_tokens * effective_sell_fraction

            # Calculate P&L
            entry_value = position.amount_usd * effective_sell_fraction
            exit_value = sell_amount * exit_price
            pnl_usd = exit_value - entry_value
            pnl_pct = (pnl_usd / entry_value * 100) if entry_value > 0 else 0
            fraction_adjusted = abs(effective_sell_fraction - requested_sell_fraction) > 1e-6
            if fraction_adjusted:
                exit_result.details = dict(exit_result.details or {})
                exit_result.details["requested_sell_fraction"] = requested_sell_fraction
                exit_result.details["effective_sell_fraction"] = effective_sell_fraction

            console.print(Panel(
                f"EXIT SIGNAL: {position.symbol}\n"
                f"Reason: {exit_result.reason}\n"
                f"Sell: {effective_sell_fraction * 100:.0f}%"
                + (
                    f" (req {requested_sell_fraction * 100:.0f}%)\n"
                    if fraction_adjusted
                    else "\n"
                )
                +
                f"P&L: ${pnl_usd:+.2f} ({pnl_pct:+.1f}%)",
                title="SELL SIGNAL",
                style="red" if pnl_usd < 0 else "green"
            ))

            if self.paper_mode:
                # Paper trade: simulate exit
                self.session_pnl += pnl_usd
                self.session_trades += 1
                self.recent_pnl.append((time.time(), pnl_usd))
                if pnl_usd > 0:
                    self.session_wins += 1
                    self.loss_streak = 0
                else:
                    self.session_losses += 1
                    self.loss_streak += 1

                if meme_config.LOSS_COOLDOWN_ENABLED and self.loss_streak >= meme_config.LOSS_COOLDOWN_THRESHOLD:
                    self.cooldown_until = time.time() + meme_config.LOSS_COOLDOWN_SECONDS
                    console.print(f"[yellow]COOLDOWN: {self.loss_streak} losses in a row. Pausing entries for {meme_config.LOSS_COOLDOWN_SECONDS}s[/yellow]")
                self._save_stats()

                # Update remaining position size after this slice.
                # Keep ActivePosition and PositionState in sync; if PositionState carries stale
                # notional, MAX_LOSS_CAP can fire against the original size after partial exits.
                position.amount_tokens = max(0.0, float(position.amount_tokens) - float(sell_amount))
                position.amount_usd = max(0.0, float(position.amount_usd) - float(entry_value))
                if position.state:
                    try:
                        position.state.amount_tokens = float(position.amount_tokens)
                        position.state.amount_usd = float(position.amount_usd)
                    except Exception:
                        pass

                # Record trade with detailed metadata for analysis
                if HAS_POSITION_STORE:
                    hold_time_sec = time.time() - position.entry_time
                    # Build TP stage summary if exit manager is available
                    tp_stages = []
                    tp_summary = {}
                    if position.state and hasattr(position.state, 'tp_stages_log'):
                        tp_stages = list(position.state.tp_stages_log)
                        try:
                            tp_summary = self.exit_manager.get_tp_stage_summary(position.state)
                        except Exception:
                            tp_summary = {}
                    trade_metadata = {
                        'paper_mode': True,
                        # Prefer the position's run_id so restored positions attribute correctly.
                        'run_id': (
                            str(getattr(position.state, "run_id", "") or "").strip()
                            if position.state is not None
                            else ""
                        ) or str(getattr(self, "run_id", "") or "").strip(),
                        'entry_score': position.state.score if position.state else 0,
                        'hold_time_sec': hold_time_sec,
                        'hold_time_min': round(hold_time_sec / 60, 1),
                        'sell_fraction': effective_sell_fraction,
                        'highest_price_seen': position.state.highest_price_seen if position.state else 0,
                        'tp0_hit': position.state.tp0_hit if position.state else False,
                        'tp1_hit': position.state.tp1_hit if position.state else False,
                        'tp2_hit': position.state.tp2_hit if position.state else False,
                        'tp3_hit': position.state.tp3_hit if position.state else False,
                        'tp4_hit': position.state.tp4_hit if position.state else False,
                        'tp_stages': tp_stages,
                        'tp_summary': tp_summary,
                        'exit_details': exit_result.details,
                    }
                    if position.state:
                        # Market cap context (signal-first computes this via supply*price).
                        try:
                            trade_metadata['market_cap_entry'] = float(getattr(position.state, "market_cap_entry", 0.0) or 0.0)
                        except Exception:
                            trade_metadata['market_cap_entry'] = 0.0
                        try:
                            trade_metadata['market_cap_current'] = float(getattr(position.state, "market_cap_current", 0.0) or 0.0)
                        except Exception:
                            trade_metadata['market_cap_current'] = 0.0
                        try:
                            trade_metadata['market_cap_highest'] = float(getattr(position.state, "market_cap_highest", 0.0) or 0.0)
                        except Exception:
                            trade_metadata['market_cap_highest'] = 0.0
                        try:
                            trade_metadata['signal_score'] = float(getattr(position.state, "signal_score", 0.0) or 0.0)
                        except Exception:
                            trade_metadata['signal_score'] = 0.0
                        try:
                            trade_metadata['signal_tier'] = str(getattr(position.state, "signal_tier", "") or "")
                        except Exception:
                            trade_metadata['signal_tier'] = ""
                        # Persisted signal metrics
                        try:
                            trade_metadata['signal_hits'] = int(getattr(position.state, "signal_hits", 0) or 0)
                        except Exception:
                            trade_metadata['signal_hits'] = 0
                        try:
                            trade_metadata['signal_buys'] = int(getattr(position.state, "signal_buys", 0) or 0)
                        except Exception:
                            trade_metadata['signal_buys'] = 0
                        try:
                            trade_metadata['signal_sells'] = int(getattr(position.state, "signal_sells", 0) or 0)
                        except Exception:
                            trade_metadata['signal_sells'] = 0
                        try:
                            trade_metadata['signal_unique_buyers'] = int(getattr(position.state, "signal_unique_buyers", 0) or 0)
                        except Exception:
                            trade_metadata['signal_unique_buyers'] = 0
                        try:
                            trade_metadata['signal_net_sol_in'] = float(getattr(position.state, "signal_net_sol_in", 0.0) or 0.0)
                        except Exception:
                            trade_metadata['signal_net_sol_in'] = 0.0
                        try:
                            trade_metadata['signal_top_buyer_share'] = float(getattr(position.state, "signal_top_buyer_share", 0.0) or 0.0)
                        except Exception:
                            trade_metadata['signal_top_buyer_share'] = 0.0
                        try:
                            tfs = getattr(position.state, "signal_t_first_sell_s", None)
                            trade_metadata['signal_t_first_sell_s'] = float(tfs) if tfs is not None else None
                        except Exception:
                            trade_metadata['signal_t_first_sell_s'] = None
                        try:
                            sig_src = getattr(position.state, "signal_source", None)
                            if not sig_src:
                                sig_src = ((self.launch_signal_metrics.get(position.mint, {}) or {}).get("source"))
                            trade_metadata['signal_source'] = str(sig_src) if sig_src else None
                        except Exception:
                            trade_metadata['signal_source'] = None
                        try:
                            trade_metadata['signal_mcap_size_mult'] = float(getattr(position.state, "signal_mcap_size_mult", 1.0) or 1.0)
                        except Exception:
                            trade_metadata['signal_mcap_size_mult'] = 1.0
                        try:
                            trade_metadata['winner_zone_id'] = str(getattr(position.state, "winner_zone_id", "") or "")
                        except Exception:
                            trade_metadata['winner_zone_id'] = ""
                        try:
                            trade_metadata['winner_zone_objective'] = float(
                                getattr(position.state, "winner_zone_objective", 0.0) or 0.0
                            )
                        except Exception:
                            trade_metadata['winner_zone_objective'] = 0.0
                        try:
                            trade_metadata['winner_zone_bypassed'] = bool(
                                getattr(position.state, "winner_zone_bypassed", False)
                            )
                        except Exception:
                            trade_metadata['winner_zone_bypassed'] = False
                        try:
                            trade_metadata['winner_zone_bypass_reason'] = str(
                                getattr(position.state, "winner_zone_bypass_reason", "") or ""
                            )
                        except Exception:
                            trade_metadata['winner_zone_bypass_reason'] = ""
                        # Entry microstructure context (DexScreener mode).
                        for k in (
                            "liquidity_entry",
                            "price_change_5m_entry",
                            "buys_5m_entry",
                            "sells_5m_entry",
                            "txns_5m_entry",
                            "volume_5m_entry",
                        ):
                            try:
                                if hasattr(position.state, k):
                                    trade_metadata[k] = getattr(position.state, k)
                            except Exception:
                                pass
                    record_trade(
                        mint=position.mint,
                        symbol=position.symbol,
                        side='SELL',
                        entry_price=position.entry_price,
                        exit_price=exit_price,
                        entry_timestamp=datetime.fromtimestamp(position.entry_time).isoformat(),
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
                if position.amount_tokens <= 0 or effective_sell_fraction >= 1.0:
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
                self.recent_pnl.append((time.time(), pnl_usd))
                if pnl_usd > 0:
                    self.session_wins += 1
                    self.loss_streak = 0
                else:
                    self.session_losses += 1
                    self.loss_streak += 1

                if meme_config.LOSS_COOLDOWN_ENABLED and self.loss_streak >= meme_config.LOSS_COOLDOWN_THRESHOLD:
                    self.cooldown_until = time.time() + meme_config.LOSS_COOLDOWN_SECONDS
                    console.print(f"[yellow]COOLDOWN: {self.loss_streak} losses in a row. Pausing entries for {meme_config.LOSS_COOLDOWN_SECONDS}s[/yellow]")
                self._save_stats()

                if effective_sell_fraction >= 1.0:
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
        try:
            now = time.time()
            hit = self._holder_check_cache.get(mint)
            if hit:
                ts, ok, reason = hit
                if (now - float(ts)) <= float(self.holder_check_cache_s):
                    return bool(ok), str(reason or "")
        except Exception:
            pass

        # Prefer standard RPC_URL (used everywhere else) over HELIUS_URL.
        # HELIUS_URL is often a paid/limited endpoint and can 401/429 which would
        # unnecessarily block entries.
        rpc_url = os.getenv("RPC_URL") or os.getenv("RPC_ENDPOINT") or os.getenv("HELIUS_URL") or ""
        if not rpc_url:
            return False, "no RPC configured for holder check"

        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                # Fetch total supply for accurate concentration math
                supply_total = None
                try:
                    supply_payload = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getTokenSupply",
                        "params": [mint],
                    }
                    supply_resp = await client.post(rpc_url, json=supply_payload)
                    if supply_resp.status_code == 200:
                        supply_data = supply_resp.json().get("result", {}).get("value", {})
                        supply_total = float(supply_data.get("uiAmount", 0) or 0)
                except Exception:
                    supply_total = None

                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTokenLargestAccounts",
                    "params": [mint],
                }
                resp = await client.post(rpc_url, json=payload)
                if resp.status_code != 200:
                    # Fail-open on rate limiting in PAPER mode to avoid blocking tests
                    if self.paper_mode and resp.status_code == 429:
                        out = (True, "holder check rate-limited (paper mode bypass)")
                        self._holder_check_cache[mint] = (time.time(), out[0], out[1])
                        return out
                    if self.paper_mode:
                        out = (True, f"holder check status {resp.status_code} (paper mode bypass)")
                        self._holder_check_cache[mint] = (time.time(), out[0], out[1])
                        return out
                    out = (False, f"RPC error status {resp.status_code}")
                    self._holder_check_cache[mint] = (time.time(), out[0], out[1])
                    return out

                data = resp.json()
                result = data.get("result", {})
                accounts = result.get("value", [])
                if not accounts:
                    out = (True, "")  # No data, allow through
                    self._holder_check_cache[mint] = (time.time(), out[0], out[1])
                    return out

                # getTokenLargestAccounts returns up to 20 largest holders
                amounts = []
                for acc in accounts:
                    # Prefer uiAmount for human-scaled comparisons
                    ui_amt = acc.get("uiAmount")
                    if ui_amt is not None:
                        amounts.append(float(ui_amt))
                        continue
                    try:
                        amount_str = acc.get("amount", "0")
                        amounts.append(float(amount_str))
                    except Exception:
                        continue

                # If supply is available, use it. Otherwise fallback to top-20 sum.
                total = supply_total if supply_total and supply_total > 0 else sum(amounts)
                if total == 0:
                    out = (True, "")
                    self._holder_check_cache[mint] = (time.time(), out[0], out[1])
                    return out

                # Check single wallet concentration
                max_single = amounts[0] / total  # Accounts are sorted largest first
                if max_single > meme_config.HOLDER_MAX_SINGLE_WALLET:
                    out = (
                        False,
                        f"single wallet holds {max_single*100:.1f}% (max: {meme_config.HOLDER_MAX_SINGLE_WALLET*100:.0f}%)",
                    )
                    self._holder_check_cache[mint] = (time.time(), out[0], out[1])
                    return out

                # Check top 5 concentration
                top5_sum = sum(amounts[:5])
                top5_pct = top5_sum / total
                if top5_pct > meme_config.HOLDER_MAX_TOP5_CONCENTRATION:
                    out = (
                        False,
                        f"top 5 hold {top5_pct*100:.1f}% (max: {meme_config.HOLDER_MAX_TOP5_CONCENTRATION*100:.0f}%)",
                    )
                    self._holder_check_cache[mint] = (time.time(), out[0], out[1])
                    return out

                out = (True, "")
                self._holder_check_cache[mint] = (time.time(), out[0], out[1])
                return out

        except Exception as e:
            if meme_config.VERBOSE_LOGGING:
                console.print(f"[yellow]Holder check error for {mint[:8]}: {e}[/yellow]")
            if self.paper_mode:
                out = (True, f"holder check network error (paper mode bypass): {e}")
                self._holder_check_cache[mint] = (time.time(), out[0], out[1])
                return out
            out = (False, f"holder check network error: {e}")
            self._holder_check_cache[mint] = (time.time(), out[0], out[1])
            return out

    async def _check_mint_freeze_risk(self, mint: str) -> tuple[bool, str]:
        """Check mint/freeze authority risk via on-chain SPL Mint decode.

        Returns (True, "") if OK, (False, reason) if reject.
        """
        if not meme_config.MINT_FREEZE_CHECK_ENABLED:
            return True, ""
        try:
            # Cache for a while, mint authority does not change for legitimate tokens.
            now = time.time()
            cache = getattr(self, "_mint_freeze_cache", None)
            if cache is None:
                cache = {}
                setattr(self, "_mint_freeze_cache", cache)
            hit = cache.get(mint)
            if isinstance(hit, tuple) and len(hit) == 3:
                ts, ok, reason = hit
                if (now - float(ts)) < 3600:
                    return (bool(ok), str(reason or ""))

            info = await asyncio.to_thread(fetch_spl_mint_info, self.rpc_pool, mint)
            if not info:
                if meme_config.MINT_FREEZE_STRICT:
                    cache[mint] = (now, False, "mint decode unavailable")
                    return False, "mint decode unavailable"
                cache[mint] = (now, True, "")
                return True, ""

            if info.mint_authority_present or info.freeze_authority_present:
                cache[mint] = (now, False, "mint/freeze authority present")
                return False, "mint/freeze authority present"

            cache[mint] = (now, True, "")
            return True, ""
        except Exception as e:
            if meme_config.MINT_FREEZE_STRICT:
                return False, f"mint/freeze check error: {e}"
            return True, ""

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

            # Backoff between price fetch attempts
            if pending.last_attempt_ts:
                if (now - pending.last_attempt_ts) < meme_config.CONFIRMATION_RETRY_DELAY_SECONDS:
                    continue

            # Check 1: Price still holding?
            price_data = await self._get_price_and_momentum(mint)
            if not price_data or not price_data.get('price'):
                pending.confirm_attempts += 1
                pending.last_attempt_ts = now
                if pending.confirm_attempts >= meme_config.CONFIRMATION_MAX_ATTEMPTS:
                    console.print(f"[yellow]REJECTED: {pending.symbol} - no price data after wait (attempts={pending.confirm_attempts})[/yellow]")
                    to_remove.append(mint)
                continue

            current_price = price_data['price']
            price_change_pct = ((current_price - pending.signal_price) / pending.signal_price) * 100 if pending.signal_price > 0 else 0

            if price_change_pct < meme_config.CONFIRMATION_MAX_PRICE_DROP:
                console.print(f"[red]REJECTED: {pending.symbol} - price dropped {price_change_pct:+.1f}% during wait (signal: ${pending.signal_price:.10f} -> now: ${current_price:.10f})[/red]")
                to_remove.append(mint)
                rejected = True

            # Check 1b: Refresh liquidity/mcap during the confirmation window.
            # This avoids entering into tokens that temporarily met thresholds but then
            # fell below our minimums before the delayed entry fires.
            if not rejected:
                try:
                    pending.candidate.price = float(current_price)
                except Exception:
                    pass
                try:
                    await self._fetch_token_data(pending.candidate)
                except Exception:
                    pass
                try:
                    min_mcap_confirm = float(meme_config.MIN_MARKET_CAP_USD)
                    if self.signal_first and self.launch_signals_file:
                        try:
                            min_mcap_confirm = float(
                                os.getenv("MEME_SIGNAL_MIN_MCAP_USD", str(min_mcap_confirm)) or min_mcap_confirm
                            )
                        except Exception:
                            pass
                    if float(getattr(pending.candidate, "market_cap", 0.0) or 0.0) < float(min_mcap_confirm):
                        console.print(
                            f"[red]REJECTED: {pending.symbol} - mcap dropped below min during wait "
                            f"(${float(getattr(pending.candidate,'market_cap',0.0) or 0.0):,.0f} < ${float(min_mcap_confirm):,.0f})[/red]"
                        )
                        to_remove.append(mint)
                        rejected = True
                except Exception:
                    pass
                if not rejected:
                    try:
                        min_liq_confirm = float(meme_config.MIN_LIQUIDITY_USD)
                        if self.signal_first and self.launch_signals_file:
                            try:
                                sig_min_liq_default = float(getattr(meme_config, "MIN_LIQUIDITY_EARLY", min_liq_confirm) or min_liq_confirm)
                            except Exception:
                                sig_min_liq_default = min_liq_confirm
                            try:
                                min_liq_confirm = float(
                                    os.getenv("MEME_SIGNAL_MIN_LIQUIDITY_USD", str(sig_min_liq_default)) or sig_min_liq_default
                                )
                            except Exception:
                                min_liq_confirm = sig_min_liq_default
                        if float(getattr(pending.candidate, "liquidity", 0.0) or 0.0) < float(min_liq_confirm):
                            console.print(
                                f"[red]REJECTED: {pending.symbol} - liquidity dropped below min during wait "
                                f"(${float(getattr(pending.candidate,'liquidity',0.0) or 0.0):,.0f} < ${float(min_liq_confirm):,.0f})[/red]"
                            )
                            to_remove.append(mint)
                            rejected = True
                    except Exception:
                        pass

            # Check 2: 5m sell pressure
            if not rejected:
                buys_5m = price_data.get('buys_5m', 0)
                sells_5m = price_data.get('sells_5m', 0)
                if buys_5m > 0 and sells_5m > buys_5m * meme_config.ANTI_RUG_SELL_RATIO:
                    console.print(f"[red]REJECTED: {pending.symbol} - sell pressure during wait (buys: {buys_5m}, sells: {sells_5m})[/red]")
                    to_remove.append(mint)
                    rejected = True

            # Check 3: Mint/freeze authority via Birdeye
            if not rejected:
                mf_ok, mf_reason = await self._check_mint_freeze_risk(mint)
                if not mf_ok:
                    console.print(f"[red]REJECTED: {pending.symbol} - mint/freeze risk: {mf_reason}[/red]")
                    self._entry_pattern_clear_cooldown(mint, reason="reject_entry_mint_freeze")
                    self._set_entry_reject_cooldown(mint, reason="reject_entry_mint_freeze")
                    to_remove.append(mint)
                    rejected = True

            # Check 4: Holder concentration
            if not rejected:
                holder_ok, holder_reason = await self._check_holder_concentration(mint)
                if not holder_ok:
                    console.print(f"[red]REJECTED: {pending.symbol} - holder concentration: {holder_reason}[/red]")
                    self._entry_pattern_clear_cooldown(mint, reason="reject_entry_holder")
                    self._set_entry_reject_cooldown(mint, reason="reject_entry_holder")
                    to_remove.append(mint)
                    rejected = True

            # All checks passed - execute entry
            if not rejected:
                # Update candidate price to current for accurate entry
                pending.candidate.price = current_price
                console.print(f"[green bold]CONFIRMED: {pending.symbol} after {elapsed:.0f}s wait (price {price_change_pct:+.1f}%)[/green bold]")
                await self.execute_entry(pending.candidate)
                # mark signal as used to avoid re-entry spam
                if self.launch_signals_file:
                    self.launch_signal_last_used[pending.candidate.mint] = time.time()
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
                # Signal-first fallback: use Jupiter quotes instead of DexScreener.
                if self.signal_first and self.launch_signals_file:
                    tmp = TokenCandidate(mint=mint, discovered_at=position.entry_time)
                    tmp.symbol = position.symbol
                    await self._fetch_signal_quote_data(
                        tmp,
                        purpose="position",
                        side="sell",
                        token_amount_override=float(position.amount_tokens or 0.0),
                    )
                    price_data = {
                        "price": tmp.price,
                        "price_change_5m": getattr(tmp, "price_change_5m", 0) or 0,
                        "buys_5m": 0,
                        "sells_5m": 0,
                    }
                else:
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

                # 0. Optional scale-in management (signal-first): add size only if it moves in our favor.
                in_probe = (
                    self.signal_first
                    and self.launch_signals_file
                    and getattr(position, "scale_in_enabled", False)
                    and not getattr(position, "scale_in_done", True)
                    and float(getattr(position, "target_amount_usd", 0.0) or 0.0) > float(position.amount_usd or 0.0) + 1e-9
                )
                if in_probe:
                    held_s = time.time() - position.entry_time
                    # Abort early if it moves against us in the probe window.
                    if held_s <= float(self.scale_in_window_seconds):
                        if float(position.unrealized_pnl_pct or 0.0) <= float(self.scale_in_abort_below_pct):
                            abort_exit = ExitResult(
                                should_exit=True,
                                reason="SCALE_IN_ABORT",
                                sell_fraction=1.0,
                                details={
                                    "held_s": round(held_s, 1),
                                    "pnl_pct": float(position.unrealized_pnl_pct or 0.0),
                                    "abort_below_pct": float(self.scale_in_abort_below_pct),
                                    "target_usd": float(getattr(position, "target_amount_usd", 0.0) or 0.0),
                                    "entry_usd": float(position.amount_usd or 0.0),
                                },
                            )
                            console.print(
                                f"[yellow]SCALE-IN ABORT: {position.symbol} pnl={position.unrealized_pnl_pct:+.2f}% "
                                f"held={held_s:.0f}s[/yellow]"
                            )
                            await self.execute_exit(position, abort_exit)
                            continue

                        # Add to target size if it confirms quickly.
                        if float(position.unrealized_pnl_pct or 0.0) >= float(self.scale_in_add_threshold_pct):
                            add_usd = float(getattr(position, "target_amount_usd", 0.0) or 0.0) - float(position.amount_usd or 0.0)
                            if add_usd > 0 and position.current_price > 0:
                                add_tokens = float(add_usd) / float(position.current_price)
                                new_tokens = float(position.amount_tokens or 0.0) + float(add_tokens)
                                new_usd = float(position.amount_usd or 0.0) + float(add_usd)
                                if new_tokens > 0:
                                    new_entry = float(new_usd) / float(new_tokens)
                                    position.amount_tokens = float(new_tokens)
                                    position.amount_usd = float(new_usd)
                                    # Best-effort SOL estimate for display only.
                                    try:
                                        sol_px = await self._get_sol_price()
                                        if sol_px and sol_px > 0:
                                            position.amount_sol = float(new_usd) / float(sol_px)
                                    except Exception:
                                        pass
                                    position.entry_price = float(new_entry)
                                    if position.state:
                                        position.state.amount_tokens = float(new_tokens)
                                        position.state.amount_usd = float(new_usd)
                                        position.state.entry_price = float(new_entry)
                                    position.scale_in_done = True
                                    console.print(
                                        f"[green]SCALE-IN ADD: {position.symbol} now=${new_usd:.2f} "
                                        f"(pnl={position.unrealized_pnl_pct:+.2f}% held={held_s:.0f}s)[/green]"
                                    )
                                    # Persist to store so analysis can correlate.
                                    if HAS_POSITION_STORE:
                                        try:
                                            store = get_store()
                                            # update_position replaces metadata (no merge), so fetch+merge to avoid
                                            # wiping the rich entry metadata (signal metrics, tiers, etc).
                                            existing = store.get_position(position.mint) or {}
                                            meta = existing.get("metadata") or {}
                                            if not isinstance(meta, dict):
                                                meta = {}
                                            meta.update({"scale_in_added": True, "scale_in_add_usd": float(add_usd)})
                                            store.update_position(
                                                mint=position.mint,
                                                symbol=position.symbol,
                                                entry_price=position.entry_price,
                                                amount_tokens=position.amount_tokens,
                                                amount_usd=position.amount_usd,
                                                status="open",
                                                metadata=meta,
                                            )
                                        except Exception:
                                            pass
                    else:
                        # Window expired; do not add size later. Prefer scratching weak/no-move probes.
                        try:
                            timeout_exit_below = float(os.getenv("MEME_SCALE_IN_TIMEOUT_EXIT_BELOW_PCT", "0.5") or 0.5)
                        except Exception:
                            timeout_exit_below = 0.5
                        if float(position.unrealized_pnl_pct or 0.0) <= float(timeout_exit_below):
                            timeout_exit = ExitResult(
                                should_exit=True,
                                reason="SCALE_IN_TIMEOUT",
                                sell_fraction=1.0,
                                details={
                                    "held_s": round(held_s, 1),
                                    "pnl_pct": float(position.unrealized_pnl_pct or 0.0),
                                    "timeout_exit_below_pct": float(timeout_exit_below),
                                    "target_usd": float(getattr(position, "target_amount_usd", 0.0) or 0.0),
                                    "entry_usd": float(position.amount_usd or 0.0),
                                },
                            )
                            console.print(
                                f"[yellow]SCALE-IN TIMEOUT: {position.symbol} pnl={position.unrealized_pnl_pct:+.2f}% "
                                f"held={held_s:.0f}s[/yellow]"
                            )
                            await self.execute_exit(position, timeout_exit)
                            continue
                        position.scale_in_done = True

                    # Strict probe mode: while scale-in is pending, do not run any other exit logic.
                    # This keeps the baseline simple and high-WR oriented.
                    if getattr(position, "scale_in_enabled", False) and not getattr(position, "scale_in_done", True):
                        continue

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

                # 1a. Micro trailing (early plateau protection)
                # For signal-first (quote-based) monitoring, many tokens top out at +3%..+10%.
                # This takes partial profit when price retraces from a small peak.
                if self.signal_first and meme_config.MICRO_TRAIL_ENABLED:
                    try:
                        # Adjust micro trailing by entry market cap "plateau band".
                        # Small caps often stall quickly; larger caps can breathe.
                        mcap = 0.0
                        try:
                            mcap = float(getattr(position.state, "market_cap_entry", 0.0) or 0.0) if position.state else 0.0
                        except Exception:
                            mcap = 0.0
                        small_max = float(os.getenv("MEME_MCAP_SMALL_MAX", "25000") or 25000)
                        mid_max = float(os.getenv("MEME_MCAP_MID_MAX", "100000") or 100000)
                        if mcap and mcap <= small_max:
                            act = float(os.getenv("MEME_MICRO_TRAIL_ACT_SMALL", "0.04") or 0.04)
                            dist = float(os.getenv("MEME_MICRO_TRAIL_DIST_SMALL", "-0.035") or -0.035)
                        elif mcap and mcap <= mid_max:
                            act = float(os.getenv("MEME_MICRO_TRAIL_ACT_MID", "0.05") or 0.05)
                            dist = float(os.getenv("MEME_MICRO_TRAIL_DIST_MID", "-0.04") or -0.04)
                        else:
                            act = float(getattr(meme_config, "MICRO_TRAIL_ACTIVATION", 0.05) or 0.05)
                            dist = float(getattr(meme_config, "MICRO_TRAIL_DISTANCE", -0.04) or -0.04)
                        sell_frac = float(getattr(meme_config, "MICRO_TRAIL_SELL_FRACTION", 0.30) or 0.30)
                    except Exception:
                        act, dist, sell_frac = 0.05, -0.04, 0.30
                    if sell_frac > 0 and pnl_fraction >= act and not getattr(position, "_micro_trail_done", False):
                        peak = float(getattr(position, "_micro_peak_price", 0.0) or 0.0)
                        if peak <= 0 or position.current_price > peak:
                            position._micro_peak_price = float(position.current_price)
                            peak = float(position._micro_peak_price)
                        # Trigger when price retraces by `dist` from the micro-peak.
                        if peak > 0 and position.current_price <= peak * (1.0 + dist):
                            position._micro_trail_done = True
                            mt_exit = ExitResult(
                                should_exit=True,
                                reason="MICRO_TRAIL",
                                sell_fraction=min(1.0, max(0.0, sell_frac)),
                                details={
                                    "pnl_pct": position.unrealized_pnl_pct,
                                    "micro_peak": peak,
                                    "micro_dist": dist,
                                    "market_cap_entry": mcap,
                                },
                            )
                            console.print(
                                f"[green]MICRO TRAIL: {position.symbol} retraced from peak "
                                f"(peak ${peak:.10f} -> now ${position.current_price:.10f})[/green]"
                            )
                            await self.execute_exit(position, mt_exit)
                            continue

                # 1a.1 Plateau exit (market-cap stall)
                # Many launches "stall" in a band; if it fails to expand quickly, free the slot.
                if self.signal_first and self.launch_signals_file and position.state:
                    try:
                        plateau_on = os.getenv("MEME_PLATEAU_ENABLED", "false").lower() in ("1", "true", "yes")
                        if plateau_on:
                            # Update market cap estimates (best-effort).
                            mcap_now = float(getattr(position.state, "market_cap_current", 0.0) or 0.0)
                            try:
                                su = self._mint_supply_ui.get(mint)
                                if not su:
                                    await self._get_mint_decimals(mint)
                                    su = self._mint_supply_ui.get(mint)
                                if su and position.current_price > 0:
                                    mcap_now = float(su) * float(position.current_price)
                                    position.state.market_cap_current = mcap_now
                                    if mcap_now > float(getattr(position.state, "market_cap_highest", 0.0) or 0.0):
                                        position.state.market_cap_highest = mcap_now
                            except Exception:
                                pass

                            entry_mcap = float(getattr(position.state, "market_cap_entry", 0.0) or 0.0)
                            held_s = time.time() - float(getattr(position.state, "entry_time", position.entry_time) or position.entry_time)

                            # Band the window by entry market cap (small caps stall faster).
                            w_default = float(os.getenv("MEME_PLATEAU_WINDOW_SECONDS", "180") or 180)
                            w_small = float(os.getenv("MEME_PLATEAU_WINDOW_SMALL_SECONDS", "120") or 120)
                            w_mid = float(os.getenv("MEME_PLATEAU_WINDOW_MID_SECONDS", "180") or 180)
                            w_large = float(os.getenv("MEME_PLATEAU_WINDOW_LARGE_SECONDS", "240") or 240)
                            small_max = float(os.getenv("MEME_MCAP_SMALL_MAX", "25000") or 25000)
                            mid_max = float(os.getenv("MEME_MCAP_MID_MAX", "100000") or 100000)
                            if entry_mcap and entry_mcap <= small_max:
                                w = w_small
                            elif entry_mcap and entry_mcap <= mid_max:
                                w = w_mid
                            elif entry_mcap:
                                w = w_large
                            else:
                                w = w_default

                            min_gain = float(os.getenv("MEME_PLATEAU_MIN_MCAP_GAIN", "0.20") or 0.20)  # 20%
                            min_pnl_hold = float(os.getenv("MEME_PLATEAU_MIN_PNL_TO_HOLD", "0.02") or 0.02)  # +2%
                            sell_frac = float(os.getenv("MEME_PLATEAU_SELL_FRACTION", "1.0") or 1.0)

                            if held_s >= w and entry_mcap > 0 and mcap_now > 0:
                                gain = (mcap_now / entry_mcap) - 1.0
                                if gain < min_gain and pnl_fraction < min_pnl_hold:
                                    plateau_exit = ExitResult(
                                        should_exit=True,
                                        reason="MCAP_PLATEAU",
                                        sell_fraction=min(1.0, max(0.0, sell_frac)),
                                        details={
                                            "held_s": round(held_s, 1),
                                            "window_s": w,
                                            "mcap_entry": entry_mcap,
                                            "mcap_now": mcap_now,
                                            "mcap_gain": round(gain, 4),
                                            "min_mcap_gain": min_gain,
                                            "pnl_pct": position.unrealized_pnl_pct,
                                            "min_pnl_hold": min_pnl_hold,
                                        },
                                    )
                                    console.print(
                                        f"[yellow]PLATEAU EXIT: {position.symbol} mcap_gain={gain*100:+.1f}% "
                                        f"pnl={position.unrealized_pnl_pct:+.1f}% held={held_s:.0f}s[/yellow]"
                                    )
                                    await self.execute_exit(position, plateau_exit)
                                    continue
                    except Exception:
                        pass

                # 1a.2 Market-cap level ladder guard:
                # Track discrete levels (15k/30k/60k/100k/...) and exit when a reached
                # level decisively fails (retrace + confirm), which is common on meme dumps.
                if self.signal_first and self.launch_signals_file and position.state and self.mcap_levels_enabled and self.mcap_levels:
                    try:
                        now_ts = time.time()
                        mcap_now = float(getattr(position.state, "market_cap_current", 0.0) or 0.0)
                        try:
                            su = self._mint_supply_ui.get(mint)
                            if not su:
                                await self._get_mint_decimals(mint)
                                su = self._mint_supply_ui.get(mint)
                            if su and position.current_price > 0:
                                mcap_now = float(su) * float(position.current_price)
                                position.state.market_cap_current = mcap_now
                                if mcap_now > float(getattr(position.state, "market_cap_highest", 0.0) or 0.0):
                                    position.state.market_cap_highest = mcap_now
                        except Exception:
                            pass

                        if mcap_now > 0:
                            curr_level = 0.0
                            high_level = 0.0
                            mcap_hi = float(getattr(position.state, "market_cap_highest", 0.0) or 0.0)
                            for lv in self.mcap_levels:
                                if mcap_now >= float(lv):
                                    curr_level = float(lv)
                                if mcap_hi >= float(lv):
                                    high_level = float(lv)

                            # Announce new level reaches once per position.
                            last_ann = float(getattr(position, "_mcap_last_announced_level", 0.0) or 0.0)
                            if curr_level > last_ann and curr_level > 0:
                                position._mcap_last_announced_level = float(curr_level)
                                console.print(
                                    f"[cyan]MCAP LEVEL: {position.symbol} reached ${curr_level:,.0f} "
                                    f"(now ${mcap_now:,.0f})[/cyan]"
                                )

                            # Keep the highest reached level for failure checks.
                            prev_high = float(getattr(position, "_mcap_highest_level_reached", 0.0) or 0.0)
                            if high_level > prev_high:
                                position._mcap_highest_level_reached = float(high_level)
                                position._mcap_level_breach_since = 0.0

                            reached = float(getattr(position, "_mcap_highest_level_reached", 0.0) or 0.0)
                            held_s = now_ts - float(getattr(position.state, "entry_time", position.entry_time) or position.entry_time)
                            if reached > 0 and held_s >= float(self.mcap_level_min_hold_s):
                                breach_line = float(reached) * (1.0 - float(self.mcap_level_retrace_pct))
                                if mcap_now <= breach_line:
                                    since = float(getattr(position, "_mcap_level_breach_since", 0.0) or 0.0)
                                    if since <= 0:
                                        position._mcap_level_breach_since = float(now_ts)
                                    elif (now_ts - since) >= float(self.mcap_level_retrace_confirm_s):
                                        lvl_exit = ExitResult(
                                            should_exit=True,
                                            reason="MCAP_LEVEL_FAIL",
                                            sell_fraction=min(1.0, max(0.0, float(self.mcap_level_sell_fraction))),
                                            details={
                                                "reached_level": reached,
                                                "mcap_now": mcap_now,
                                                "breach_line": breach_line,
                                                "retrace_pct": float(self.mcap_level_retrace_pct),
                                                "confirm_s": float(self.mcap_level_retrace_confirm_s),
                                                "held_s": round(float(held_s), 1),
                                            },
                                        )
                                        console.print(
                                            f"[yellow]MCAP LEVEL FAIL: {position.symbol} lost ${reached:,.0f} "
                                            f"(now ${mcap_now:,.0f}, line ${breach_line:,.0f})[/yellow]"
                                        )
                                        await self.execute_exit(position, lvl_exit)
                                        continue
                                else:
                                    position._mcap_level_breach_since = 0.0
                    except Exception:
                        pass

                # 1b. Fail-fast exit: if no early momentum, trim exposure
                if meme_config.FAIL_FAST_ENABLED:
                    # When using probe-entry + scale-in, fail-fast is redundant during the probe stage.
                    # The probe is already governed by SCALE_IN_ABORT; applying FAIL_FAST too early can
                    # increase churn without reducing tail risk further.
                    in_probe = bool(getattr(position, "scale_in_enabled", False) and not getattr(position, "scale_in_done", True))
                    if not in_probe:
                        time_held = time.time() - position.entry_time
                        if time_held <= meme_config.FAIL_FAST_WINDOW_SECONDS:
                            min_hold = getattr(meme_config, "FAIL_FAST_MIN_HOLD_SECONDS", 0) or 0
                            if time_held < min_hold:
                                # Give entries a moment to settle; avoids immediate churn on tiny moves.
                                pass
                            else:
                                tb = getattr(meme_config, "FAIL_FAST_TRIGGER_BELOW_PCT", None)
                                trigger = float(tb) if tb is not None else float(meme_config.FAIL_FAST_MIN_GAIN_PCT)
                                # Guardrail: do not churn on tiny noise. If trigger is configured as a
                                # positive "must be up X%", clamp to a default loss floor for fail-fast.
                                try:
                                    loss_floor = float(os.getenv("MEME_FAIL_FAST_LOSS_FLOOR_PCT", "-0.5") or -0.5)
                                except Exception:
                                    loss_floor = -0.5
                                eff_trigger = trigger if trigger < 0 else loss_floor
                                # Winner-aware fail-fast tuning:
                                # - high winner_score gets more room and a slightly longer warm-up
                                # - low winner_score gets cut faster
                                winner_score = float(getattr(position.state, "winner_score", 0.0) or 0.0) if position.state else 0.0
                                if self.winner_failfast_relax_enabled:
                                    if winner_score >= float(self.winner_failfast_relax_score):
                                        min_hold = float(min_hold) + float(self.winner_failfast_relax_extra_hold_s)
                                        eff_trigger = min(float(eff_trigger), float(self.winner_failfast_relax_trigger_pct))
                                    elif winner_score > 0 and winner_score <= float(self.winner_failfast_tighten_score):
                                        eff_trigger = max(float(eff_trigger), float(self.winner_failfast_tighten_trigger_pct))
                                if time_held < float(min_hold):
                                    # Winner-aware warm-up window may extend hold before fail-fast.
                                    pass
                                    continue
                                if position.unrealized_pnl_pct < eff_trigger and not getattr(position, '_failfast', False):
                                    position._failfast = True
                                    ff_exit = ExitResult(
                                        should_exit=True,
                                        reason='FAIL_FAST',
                                        sell_fraction=meme_config.FAIL_FAST_SELL_FRACTION,
                                        details={
                                            'pnl_pct': position.unrealized_pnl_pct,
                                            'time_held_sec': time_held,
                                            'trigger': eff_trigger,
                                            'configured_trigger': trigger,
                                            'winner_score': winner_score,
                                        }
                                    )
                                    console.print(f"[yellow]FAIL FAST: {position.symbol} {position.unrealized_pnl_pct:.2f}% after {time_held:.0f}s[/yellow]")
                                    await self.execute_exit(position, ff_exit)
                                    continue

                # 2. Anti-Rug Check - requires sell ratio AND price drop AND min volume
                if meme_config.ANTI_RUG_ENABLED and buys_5m > 0:
                    sell_buy_ratio = sells_5m / buys_5m if buys_5m > 0 else sells_5m
                    total_5m_txns = sells_5m + buys_5m
                    anti_rug_min_drop = getattr(meme_config, 'ANTI_RUG_MIN_PRICE_DROP', -5.0)
                    anti_rug_min_vol = getattr(meme_config, 'ANTI_RUG_MIN_VOLUME', 20)
                    if (sell_buy_ratio >= meme_config.ANTI_RUG_SELL_RATIO
                            and price_change_5m <= anti_rug_min_drop
                            and total_5m_txns >= anti_rug_min_vol):
                        rug_exit = ExitResult(
                            should_exit=True,
                            reason='ANTI_RUG',
                            sell_fraction=1.0,
                            details={
                                'sells_5m': sells_5m,
                                'buys_5m': buys_5m,
                                'ratio': sell_buy_ratio,
                                'price_change_5m': price_change_5m,
                            }
                        )
                        console.print(f"[red bold]ANTI-RUG: {position.symbol} sells({sells_5m}) >> buys({buys_5m}), price {price_change_5m:.1f}%![/red bold]")
                        await self.execute_exit(position, rug_exit)
                        continue

                # 3. Momentum dump exit - requires price drop AND sell pressure confirmation
                sell_ratio_5m = sells_5m / buys_5m if buys_5m > 0 else sells_5m
                dump_sell_ratio_min = getattr(meme_config, 'MOMENTUM_DUMP_SELL_RATIO_MIN', 1.5)
                dump_first_fraction = getattr(meme_config, 'MOMENTUM_DUMP_FIRST_FRACTION', 0.50)
                if price_change_5m <= meme_config.MOMENTUM_DUMP_THRESHOLD and sell_ratio_5m >= dump_sell_ratio_min:
                    dump_exit = ExitResult(
                        should_exit=True,
                        reason='MOMENTUM_DUMP',
                        sell_fraction=dump_first_fraction,
                        details={
                            'price_change_5m': price_change_5m,
                            'threshold': meme_config.MOMENTUM_DUMP_THRESHOLD,
                            'sell_ratio_5m': sell_ratio_5m,
                            'pnl_pct': position.unrealized_pnl_pct,
                        }
                    )
                    console.print(f"[red]MOMENTUM DUMP: {position.symbol} dropped {price_change_5m:.1f}% in 5m (sell ratio {sell_ratio_5m:.1f}x)![/red]")
                    await self.execute_exit(position, dump_exit)
                    continue

                # 3b. Volume collapse exit - low activity + price slipping
                if meme_config.VOLUME_COLLAPSE_ENABLED:
                    vol_5m = price_data.get('volume_5m', None)
                    # In signal-first mode we don't have real volume; only apply when present.
                    pnl_gate = getattr(meme_config, "VOLUME_COLLAPSE_ONLY_IF_PNL_BELOW_PCT", 0.0)
                    if (
                        vol_5m is not None
                        and (vol_5m < meme_config.VOLUME_COLLAPSE_MIN_5M)
                        and price_change_5m <= meme_config.VOLUME_COLLAPSE_PRICE_DROP
                        and float(position.unrealized_pnl_pct or 0.0) <= float(pnl_gate)
                    ):
                        vol_exit = ExitResult(
                            should_exit=True,
                            reason='VOLUME_COLLAPSE',
                            sell_fraction=meme_config.VOLUME_COLLAPSE_SELL_FRACTION,
                            details={
                                'volume_5m': vol_5m,
                                'price_change_5m': price_change_5m,
                                'pnl_gate_pct': pnl_gate,
                            }
                        )
                        console.print(f"[yellow]VOLUME COLLAPSE: {position.symbol} vol5m ${vol_5m:.0f}, price {price_change_5m:.1f}%[/yellow]")
                        await self.execute_exit(position, vol_exit)
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
                volume = best_pair.get('volume', {}) or {}

                return {
                    'price': float(price) if price else 0,
                    'price_change_5m': float(price_change.get('m5', 0) or 0),
                    'price_change_1h': float(price_change.get('h1', 0) or 0),
                    'buys_5m': int(m5_txns.get('buys', 0) or 0),
                    'sells_5m': int(m5_txns.get('sells', 0) or 0),
                    'volume_5m': float(volume.get('m5', 0) or 0),
                    'volume_1h': float(volume.get('h1', 0) or 0),
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

        # Fallback: CoinGecko simple price (more reliable than DexScreener in restricted environments)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get("https://api.coingecko.com/api/v3/simple/price", params={"ids": "solana", "vs_currencies": "usd"})
                if resp.status_code == 200:
                    data = resp.json() or {}
                    v = (data.get("solana") or {}).get("usd")
                    if v:
                        self._sol_price = float(v)
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

        dw, dl, dt, dp = self._run_delta()
        run_m = (time.time() - float(getattr(self, "_run_started_at", time.time()))) / 60.0
        console.print(
            f"\nSession: {self.session_wins}W/{self.session_losses}L | P&L: ${self.session_pnl:+.2f} | "
            f"Run({run_m:.0f}m): {dw}W/{dl}L ({dt} trades) | dP&L: ${dp:+.2f}"
        )

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
                # 1. Monitor existing positions FIRST.
                # Signal-first mode uses Jupiter quotes for price; if we process new candidates first,
                # we can exhaust the quote budget and notice MAX_LOSS_CAP too late.
                await self.monitor_positions()

                # 1b. Regime guard: pause *new* entries when recent expectancy degrades.
                # This does not affect position monitoring/exits.
                allow_new_entries = self._evaluate_entry_regime()
                if not allow_new_entries:
                    if self.signal_first and self.launch_signals_file:
                        try:
                            self._ingest_launch_signals()
                        except Exception:
                            pass
                    if self.active_positions or self.pending_entries:
                        self.display_status()
                    await asyncio.sleep(meme_config.POLL_INTERVAL_SECONDS)
                    continue

                # 2. Discover new tokens
                candidates = await self.discover_tokens()

                # 3. Filter + score candidates first, then rank by winner profile.
                scored_candidates: list[TokenCandidate] = []
                for candidate in candidates:
                    # Cooldown after loss streak
                    if self.cooldown_until and time.time() < self.cooldown_until:
                        continue
                    # Filter
                    if not await self.filter_token(candidate):
                        continue

                    # Score
                    _score = await self.score_token(candidate)
                    scored_candidates.append(candidate)

                if self.winner_prioritize_enabled and scored_candidates:
                    scored_candidates.sort(
                        key=lambda c: (
                            float(getattr(c, "winner_score", 0.0) or 0.0),
                            int(getattr(c, "composite_score", 0) or 0),
                            float(getattr(c, "market_cap", 0.0) or 0.0),
                            float(getattr(c, "liquidity", 0.0) or 0.0),
                        ),
                        reverse=True,
                    )
                    if self.winner_top_k_per_tick > 0 and len(scored_candidates) > self.winner_top_k_per_tick:
                        scored_candidates = scored_candidates[: int(self.winner_top_k_per_tick)]

                # 4. Entry decision on prioritized candidates.
                entries_opened_this_tick = 0
                entry_cap_this_tick = int(self.max_new_entries_per_tick or 0)
                for candidate in scored_candidates:
                    if entry_cap_this_tick > 0 and entries_opened_this_tick >= entry_cap_this_tick:
                        if self.signal_first and self.launch_signals_file:
                            self._signal_debug_write(
                                "skip_tick_entry_cap",
                                candidate,
                                {"max_new_entries_per_tick": int(entry_cap_this_tick)},
                            )
                        break
                    if not self._entry_pacing_allows(candidate):
                        continue
                    if self.signal_first and self.launch_signals_file:
                        retry_at = float(self._entry_reject_until.get(candidate.mint, 0.0) or 0.0)
                        if retry_at > time.time():
                            self._signal_debug_write(
                                "skip_entry_reject_cooldown",
                                candidate,
                                {"wait_s": round(max(0.0, retry_at - time.time()), 1)},
                            )
                            continue

                    # Entry decision
                    if await self.should_enter(candidate):
                        if not self._can_enter_now():
                            self._entry_pattern_clear_cooldown(candidate.mint, reason="entry_pacing")
                            continue
                        # Signal-first: execute directly (entry contains quote confirmation).
                        if self.signal_first and self.launch_signals_file:
                            opened = await self.execute_entry(candidate)
                            if opened:
                                entries_opened_this_tick += 1
                            else:
                                self._entry_pattern_clear_cooldown(candidate.mint, reason="entry_not_opened")
                        elif meme_config.CONFIRMATION_ENABLED:
                            # Queue for confirmation instead of immediate entry
                            if candidate.mint not in self.pending_entries:
                                self.pending_entries[candidate.mint] = PendingEntry(
                                    mint=candidate.mint,
                                    symbol=candidate.symbol,
                                    candidate=candidate,
                                    signal_time=time.time(),
                                    signal_price=candidate.price,
                                )
                                console.print(
                                    f"[cyan]PENDING: {candidate.symbol} queued for "
                                    f"{meme_config.CONFIRMATION_DELAY_SECONDS}s confirmation "
                                    f"(price: ${candidate.price:.10f})[/cyan]"
                                )
                        else:
                            opened = await self.execute_entry(candidate)
                            if opened:
                                entries_opened_this_tick += 1

                # 5. Check pending entries for confirmation
                if allow_new_entries and not (self.signal_first and self.launch_signals_file):
                    await self.check_pending_entries()

                # 6. Check for re-entry opportunities on recovered tokens
                await self.check_reentry_opportunities()

                # 7. Display status
                if self.active_positions or self.pending_entries:
                    self.display_status()

                # 8. Sleep until next poll
                await asyncio.sleep(meme_config.POLL_INTERVAL_SECONDS)

            except KeyboardInterrupt:
                console.print("\n[yellow]Shutting down...[/yellow]")
                self.running = False
            except Exception as e:
                console.print(f"[red]Main loop error: {e}[/red]")
                try:
                    import traceback
                    tb = traceback.format_exc()
                    # Rich formatting can swallow/warp multi-line tracebacks when not on a TTY.
                    # Always emit a plain traceback line-block into the log file.
                    print(tb, flush=True)
                    console.print("[red]" + tb + "[/red]")
                except Exception:
                    pass
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
