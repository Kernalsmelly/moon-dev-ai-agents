"""
🌙 Moon Dev's Configuration File
Built with love by Moon Dev 🚀
"""
import os
import json
import threading
import time

# Discord webhook used by alerts (set explicitly here for Paper Trade Battle Mode)
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1462684075688067178/TJfAPSd3oShJXg2Xct-_gzc6CnMIdZ3CHdNpuw6vNcoOsD6ZPrrU2HgFzYFSbjAWrzny"

# Prefer environment-provided RPC URL (set in .env or shell). If provided we
# honor it and do not auto-promote a PRIMARY from the pool. If not provided
# we'll attempt to load the PRIMARY entry from `config/rpc_pool.json` which is
# produced by the health-check script. A background watcher will hot-reload
# the pool and update `RPC_URL` at runtime when the file changes.
_env_rpc = os.getenv("RPC_URL") or os.getenv("RPC_ENDPOINT")

# Module-level pool and selection exposed for other modules to read.
RPC_POOL: list[dict] = []  # list of pool entry dicts as in rpc_pool.json
RPC_POOL_PATH = None
RPC_URL = None


def _load_rpc_pool_file() -> list[dict]:
    """Read `config/rpc_pool.json` and return the pool list (deduped).

    Returns an empty list if the file is missing or cannot be parsed.
    """
    try:
        base = os.path.dirname(os.path.dirname(__file__))
        pool_path = os.path.join(base, 'config', 'rpc_pool.json')
        global RPC_POOL_PATH
        RPC_POOL_PATH = pool_path
        if not os.path.exists(pool_path):
            return []
        with open(pool_path, 'r', encoding='utf-8') as fh:
            obj = json.load(fh)
        pool = []
        seen = set()
        for entry in obj.get('pool', []):
            if not isinstance(entry, dict):
                continue
            url = entry.get('url')
            if not url or url in seen:
                continue
            seen.add(url)
            pool.append(entry)
        return pool
    except Exception:
        return []


def _apply_rpc_pool(pool: list[dict]):
    """Apply a newly-loaded pool to module-level variables.

    If an explicit RPC URL was provided via environment, that value is
    preserved and the pool is not used to override it.
    """
    global RPC_POOL, RPC_URL
    RPC_POOL = pool or []
    # If user explicitly set RPC via env, respect that and do not override.
    if _env_rpc:
        RPC_URL = _env_rpc
    else:
        # pick first pool entry url if available, otherwise fallback to devnet
        if RPC_POOL and isinstance(RPC_POOL, list) and len(RPC_POOL) > 0:
            first = RPC_POOL[0]
            if isinstance(first, dict):
                RPC_URL = first.get('url') or os.getenv('RPC_URL') or os.getenv('RPC_ENDPOINT')
        if not RPC_URL:
            RPC_URL = os.getenv('RPC_URL') or os.getenv('RPC_ENDPOINT') or 'https://api.devnet.solana.com'


# initial load
_apply_rpc_pool(_load_rpc_pool_file())

def _watch_rpc_pool_file(poll_interval: float = 2.0):
    """Background thread that watches `rpc_pool.json` for changes and
    reloads the in-memory pool so other modules pick up changes without a
    process restart.
    """
    try:
        last_mtime = None
        if not RPC_POOL_PATH:
            base = os.path.dirname(os.path.dirname(__file__))
            RP = os.path.join(base, 'config', 'rpc_pool.json')
        else:
            RP = RPC_POOL_PATH
        while True:
            try:
                if os.path.exists(RP):
                    m = os.path.getmtime(RP)
                    if last_mtime is None:
                        last_mtime = m
                    elif m != last_mtime:
                        # changed -> reload
                        pool = _load_rpc_pool_file()
                        _apply_rpc_pool(pool)
                        last_mtime = m
                time.sleep(poll_interval)
            except Exception:
                time.sleep(poll_interval)
    except Exception:
        # thread should die silently on unexpected errors
        return


# Start watcher thread only if no explicit RPC env is set (we shouldn't
# override a user-specified RPC_URL) and when running in a real environment.
# Tests and some CI environments may not want background threads; allow
# disabling via RPC_POOL_WATCHER_ENABLED=0 or 'false'. Default is enabled.
try:
    watcher_enabled = os.getenv('RPC_POOL_WATCHER_ENABLED', '1')
    enabled_flag = str(watcher_enabled).lower() not in ('0', 'false', 'no')
except Exception:
    enabled_flag = True

if not _env_rpc and enabled_flag:
    try:
        t = threading.Thread(target=_watch_rpc_pool_file, daemon=True, name='rpc-pool-watcher')
        t.start()
    except Exception:
        pass

# Constructed provider URLs (Hydra / Multi-provider integration)
# These prefer explicit env vars if present, otherwise build from API keys.
HELIUS_API_KEY = os.getenv('HELIUS_API_KEY', '')
HELIUS_URL = os.getenv('HELIUS_URL') or (f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}" if HELIUS_API_KEY else '')

CHAINSTACK_API_KEY = os.getenv('CHAINSTACK_API_KEY', '')
CHAINSTACK_URL = os.getenv('CHAINSTACK_URL') or (f"https://solana-mainnet.core.chainstack.com/{CHAINSTACK_API_KEY}" if CHAINSTACK_API_KEY else '')

ANKR_API_KEY = os.getenv('ANKR_API_KEY', '')
ANKR_URL = os.getenv('ANKR_URL') or (f"https://rpc.ankr.com/solana/{ANKR_API_KEY}" if ANKR_API_KEY else '')

ALCHEMY_API_KEY = os.getenv('ALCHEMY_API_KEY', '')
ALCHEMY_URL = os.getenv('ALCHEMY_URL') or (f"https://solana-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}" if ALCHEMY_API_KEY else '')

# Multi-RPC list for load-balancing / failover.
# Priority order: first is preferred when healthy.
# Note: we reference the *_URL variables above so this never NameErrors when env vars are unset.
RPC_URLS = [u for u in (HELIUS_URL, ALCHEMY_URL, CHAINSTACK_URL, ANKR_URL) if u]

# 🔄 Exchange Selection
EXCHANGE = 'solana'  # Options: 'solana', 'hyperliquid'

# 💰 Trading Configuration
USDC_ADDRESS = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # Never trade or close
SOL_ADDRESS = "So11111111111111111111111111111111111111111"   # Never trade or close

# Create a list of addresses to exclude from trading/closing
EXCLUDED_TOKENS = [USDC_ADDRESS, SOL_ADDRESS]

# Token List for Trading 📋
# NOTE: Trading Agent now has its own token list - see src/agents/trading_agent.py lines 101-104
MONITORED_TOKENS = [
    # '9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump',    # 🌬️ FART
    # 'DitHyRMQiSDhn5cnKMJV2CDDt6sVct96YrECiM49pump'     # housecoin
]

# Moon Dev's Token Trading List 🚀
# Each token is carefully selected by Moon Dev for maximum moon potential! 🌙
tokens_to_trade = MONITORED_TOKENS  # Using the same list for trading

# ⚡ HyperLiquid Configuration
HYPERLIQUID_SYMBOLS = ['BTC', 'ETH', 'SOL']  # Symbols to trade on HyperLiquid perps
HYPERLIQUID_LEVERAGE = 5  # Default leverage for HyperLiquid trades (1-50)

# 🔄 Exchange-Specific Token Lists
# Use this to determine which tokens/symbols to trade based on active exchange
def get_active_tokens():
    """Returns the appropriate token/symbol list based on active exchange"""
    if EXCHANGE == 'hyperliquid':
        return HYPERLIQUID_SYMBOLS
    else:
        return MONITORED_TOKENS

# Token to Exchange Mapping (for future hybrid trading)
TOKEN_EXCHANGE_MAP = {
    'BTC': 'hyperliquid',
    'ETH': 'hyperliquid',
    'SOL': 'hyperliquid',
    # All other tokens default to Solana
}

# Token and wallet settings
symbol = '9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump'
address = '4wgfCBf2WwLSRKLef9iW7JXZ2AfkxUxGM4XcKpHm3Sin' # YOUR WALLET ADDRESS HERE

# Position sizing 🎯
usd_size = 25  # Size of position to hold
max_usd_order_size = 3  # Max order size
tx_sleep = 30  # Sleep between transactions
slippage = 199  # Slippage settings

# Volatility-adjusted position sizing
# Base size in SOL to use when no adjustment is applied
BASE_POSITION_SIZE_SOL = float(os.getenv('BASE_POSITION_SIZE_SOL', '1.0'))
# Reduce size by this factor in high volatility (e.g., 0.5 = 50%)
SIZE_REDUCTION_FACTOR = float(os.getenv('SIZE_REDUCTION_FACTOR', '0.5'))
# Boost size by this factor in low volatility (e.g., 1.25 = +25%)
SIZE_BOOST_FACTOR = float(os.getenv('SIZE_BOOST_FACTOR', '1.25'))

# Risk Management Settings 🛡️
CASH_PERCENTAGE = 20  # Minimum % to keep in USDC as safety buffer (0-100)
MAX_POSITION_PERCENTAGE = 30  # Maximum % allocation per position (0-100)
STOPLOSS_PRICE = 1 # NOT USED YET 1/5/25    
BREAKOUT_PRICE = .0001 # NOT USED YET 1/5/25
SLEEP_AFTER_CLOSE = 600  # Prevent overtrading

MAX_LOSS_GAIN_CHECK_HOURS = 12  # How far back to check for max loss/gain limits (in hours)
SLEEP_BETWEEN_RUNS_MINUTES = 15  # How long to sleep between agent runs 🕒


# Max Loss/Gain Settings FOR RISK AGENT 1/5/25
USE_PERCENTAGE = False  # If True, use percentage-based limits. If False, use USD-based limits

# USD-based limits (used if USE_PERCENTAGE is False)
MAX_LOSS_USD = 25  # Maximum loss in USD before stopping trading
MAX_GAIN_USD = 25 # Maximum gain in USD before stopping trading

# USD MINIMUM BALANCE RISK CONTROL
MINIMUM_BALANCE_USD = 50  # If balance falls below this, risk agent will consider closing all positions
USE_AI_CONFIRMATION = True  # If True, consult AI before closing positions. If False, close immediately on breach

# Percentage-based limits (used if USE_PERCENTAGE is True)
MAX_LOSS_PERCENT = 5  # Maximum loss as percentage (e.g., 20 = 20% loss)
MAX_GAIN_PERCENT = 5  # Maximum gain as percentage (e.g., 50 = 50% gain)

# Transaction settings ⚡
slippage = 199  # 500 = 5% and 50 = .5% slippage
PRIORITY_FEE = 100000  # ~0.02 USD at current SOL prices
orders_per_open = 3  # Multiple orders for better fill rates

# Market maker settings 📊
buy_under = .0946
sell_over = 1

# Data collection settings 📈
DAYSBACK_4_DATA = 3
DATA_TIMEFRAME = '1H'  # 1m, 3m, 5m, 15m, 30m, 1H, 2H, 4H, 6H, 8H, 12H, 1D, 3D, 1W, 1M
SAVE_OHLCV_DATA = False  # 🌙 Set to True to save data permanently, False will only use temp data during run

# AI Model Settings 🤖
AI_MODEL = "claude-3-haiku-20240307"  # Model Options:
                                     # - claude-3-haiku-20240307 (Fast, efficient Claude model)
                                     # - claude-3-sonnet-20240229 (Balanced Claude model)
                                     # - claude-3-opus-20240229 (Most powerful Claude model)
AI_MAX_TOKENS = 1024  # Max tokens for response
AI_TEMPERATURE = 0.7  # Creativity vs precision (0-1)

# Trading Strategy Agent Settings - MAY NOT BE USED YET 1/5/25
ENABLE_STRATEGIES = True  # Set this to True to use strategies
STRATEGY_MIN_CONFIDENCE = 0.7  # Minimum confidence to act on strategy signals

# Paper / Live trading toggles
# Ensure paper trading by default for safety; allow realtime data
# to still be used for signals and telemetry.
LIVE_TRADING_ENABLED = False
USE_REAL_TIME_DATA = True
USE_PAPER_TRADING = True
# Dynamic thresholds for strategy filtering (e.g., higher noise during NY open)
DYNAMIC_THRESHOLD = True

# Sleep time between main agent runs
SLEEP_BETWEEN_RUNS_MINUTES = 15  # How long to sleep between agent runs 🕒

# in our nice_funcs in token over view we look for minimum trades last hour
MIN_TRADES_LAST_HOUR = 2


# Real-Time Clips Agent Settings 🎬
REALTIME_CLIPS_ENABLED = True
REALTIME_CLIPS_OBS_FOLDER = '/Volumes/Moon 26/OBS'  # Your OBS recording folder
REALTIME_CLIPS_AUTO_INTERVAL = 120  # Check every N seconds (120 = 2 minutes)
REALTIME_CLIPS_LENGTH = 2  # Minutes to analyze per check
REALTIME_CLIPS_AI_MODEL = 'groq'  # Model type: groq, openai, claude, deepseek, xai, ollama
REALTIME_CLIPS_AI_MODEL_NAME = None  # None = use default for model type
REALTIME_CLIPS_TWITTER = True  # Auto-open Twitter compose after clip

# Future variables (not active yet) 🔮
sell_at_multiple = 3
USDC_SIZE = 1
limit = 49
timeframe = '15m'
stop_loss_percentage = 20  # percent used in pnl/SL calculations (e.g., 20 means -20% stop)
dont_trade_list = []  # list of token mint addresses to never trade
EXIT_ALL_POSITIONS = False
CLOSED_POSITIONS_TXT = '777'
minimum_trades_in_last_hour = 777

# Shadow mode: if no private keys are present in the environment, default to
# SHADOW_MODE=True to prevent accidental live transactions. You can override
# explicitly by setting the SHADOW_MODE environment variable to '1'/'true'.
_explicit_shadow = os.getenv('SHADOW_MODE')
if _explicit_shadow is not None:
    SHADOW_MODE = str(_explicit_shadow).lower() in ('1', 'true', 'yes')
else:
    # Consider common private-key env vars used across the project
    _has_wallet_key = bool(os.getenv('SOLANA_PRIVATE_KEY') or os.getenv('FUNDER_PRIVATE_KEY'))
    SHADOW_MODE = not _has_wallet_key

# Watchlist path (can be set to an absolute path outside the repo; default is relative to data/)
WATCHLIST_PATH = os.getenv('WATCHLIST_PATH', 'watchlist.json')

# Path to execution events CSV used by telemetry/heartbeat. Can be absolute or
# relative to the repository data directory. Tests should override this to a
# temp path for hermetic runs.
EXECUTION_LOG_PATH = os.getenv('EXECUTION_LOG_PATH', 'data/execution_events.csv')

# Jito tipping strategy: 'adaptive' will attempt to fetch recent tip percentiles
# to bid competitively; 'fixed' uses JITO_DEFAULT_TIP_LAMPORTS.
JITO_TIP_STRATEGY = os.getenv('JITO_TIP_STRATEGY', 'adaptive')
# Default tip lamports used as fallback when adaptive data is unavailable
JITO_DEFAULT_TIP_LAMPORTS = int(os.getenv('JITO_DEFAULT_TIP_LAMPORTS', '10000'))
# API endpoint (optional) that returns recent tip amounts as JSON list for percentile calculation
JITO_TIP_API_URL = os.getenv('JITO_TIP_API_URL', '')
# percentile to target when adaptive (e.g., 95 means 95th percentile)
JITO_TIP_PERCENTILE = int(os.getenv('JITO_TIP_PERCENTILE', '95'))

# Minimum SOL balance (in SOL) required to allow Salami exits / tip payments.
# If the running wallet falls below this, Salami exits will be disabled to
# preserve a 'moonbag' for fees/tips. Default is 0.01 SOL.
MIN_TRADE_BALANCE_SOL = float(os.getenv('MIN_TRADE_BALANCE_SOL', '0.01'))

# Moon bag and slippage configuration
# Fractional profit at which we secure an initial moon-bag (e.g., 1.0 == 100% gain)
INITIAL_OUT_THRESHOLD = float(os.getenv('INITIAL_OUT_THRESHOLD', '1.0'))
# Fraction of the position to sell to secure the initial moon-bag (50% = 0.5)
MOON_BAG_PERCENT = float(os.getenv('MOON_BAG_PERCENT', '0.50'))
# Hard cap for dynamic slippage in basis points (500 = 5%)
MAX_SLIPPAGE_BPS = int(os.getenv('MAX_SLIPPAGE_BPS', '500'))


# Pydantic-backed Jito configuration for validation and safety caps
try:
    from pydantic import BaseModel, Field

    class JitoConfig(BaseModel):
        tip_strategy: str = Field(os.getenv('JITO_TIP_STRATEGY', 'adaptive'))
        default_tip_lamports: int = Field(int(os.getenv('JITO_DEFAULT_TIP_LAMPORTS', '10000')), le=100_000_000)
        tip_api_url: str = Field(os.getenv('JITO_TIP_API_URL', ''))
        percentile: int = Field(int(os.getenv('JITO_TIP_PERCENTILE', '95')))
        tip_receiver: str = Field(os.getenv('JITO_TIP_RECEIVER', ''))

    try:
        JITO = JitoConfig()
    except Exception:
        # Fall back to safe defaults if validation fails
        JITO = JitoConfig()

    # Backwards-compatible module-level names
    JITO_TIP_STRATEGY = JITO.tip_strategy
    JITO_DEFAULT_TIP_LAMPORTS = int(JITO.default_tip_lamports)
    JITO_TIP_API_URL = JITO.tip_api_url
    JITO_TIP_PERCENTILE = int(JITO.percentile)
    JITO_TIP_RECEIVER = JITO.tip_receiver
except Exception:
    # pydantic not available or something else failed — keep existing raw values
    pass

    # Verified Jito tip accounts (operator-configurable)
    JITO_VERIFIED_TIP_ACCOUNTS = [
        '96g9sBY9m9S774oW97uEHSAnp7N37Pcy92h9U6Tz6y7',
        'HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe',
    ]

    # Path to JSONL trades log (source of truth)
    TRADES_JSONL_PATH = os.getenv('TRADES_JSONL_PATH', os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'trades.jsonl'))

    # Interval (seconds) to check Jito bundle status when monitoring
    JITO_STATUS_CHECK_INTERVAL = int(os.getenv('JITO_STATUS_CHECK_INTERVAL', '2'))

    # Jito Block Engine and tip configuration (MEV protection)
    # Official block engine URL for Jito (can be environment-overridden)
    JITO_BLOCK_ENGINE_URL = os.getenv('JITO_BLOCK_ENGINE_URL', 'https://mainnet.block-engine.jito.wtf')
    # Tip amount in SOL to include as bribe for private inclusion
    JITO_TIP_AMOUNT_SOL = float(os.getenv('JITO_TIP_AMOUNT_SOL', '0.001'))
    # Enable sending via Jito (default True to enable MEV protection when keys available)
    ENABLE_JITO = str(os.getenv('ENABLE_JITO', 'True')).lower() in ('1', 'true', 'yes')
