"""
Polymarket Micro-Edge Configuration

INVERTED LOGIC from src/agents/polymarket_agent.py:
- Current agent filters for BIG trades -> We target LOW liquidity
- Current agent excludes near-resolution -> We TARGET near-resolution
- Current agent excludes sports -> We TARGET niche sports
"""

import os
from pathlib import Path

# === PATHS ===
PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "src" / "data" / "polymarket_micro"
DB_PATH = DATA_DIR / "niche_markets.db"

# === POSITION SIZING (Micro Capital) ===
MICRO_CAPITAL_TOTAL = float(os.getenv("POLYMARKET_CAPITAL", "250"))
MICRO_POSITION_SIZE_USD = float(os.getenv("POLYMARKET_POSITION_SIZE", "10"))
MICRO_MAX_POSITIONS = int(os.getenv("POLYMARKET_MAX_POSITIONS", "15"))
MICRO_MAX_SINGLE_MARKET = float(os.getenv("POLYMARKET_MAX_SINGLE", "25"))

# === NICHE TARGETING (Inverted from main agent) ===
# Target LOW liquidity markets (opposite of $500 min trade threshold)
MAX_LIQUIDITY_TARGET = float(os.getenv("POLYMARKET_MAX_LIQUIDITY", "5000"))
MIN_LIQUIDITY_FLOOR = float(os.getenv("POLYMARKET_MIN_LIQUIDITY", "100"))
MAX_VOLUME_24H = float(os.getenv("POLYMARKET_MAX_VOLUME_24H", "2000"))

# Near-resolution targets (opposite of 0.02 filter in polymarket_agent.py:254)
# LOOSENED: 12h-168h (7 days) instead of 24-72h
RESOLUTION_HOURS_MIN = float(os.getenv("POLYMARKET_RESOLUTION_MIN_HOURS", "12"))
RESOLUTION_HOURS_MAX = float(os.getenv("POLYMARKET_RESOLUTION_MAX_HOURS", "168"))
# TIGHTENED: 0.15-0.85 to avoid lottery tickets (<15%) and near-certainties (>85%)
# This focuses on markets with actual uncertainty where edges exist
PRICE_CONVERGENCE_MIN = float(os.getenv("POLYMARKET_PRICE_MIN", "0.15"))
PRICE_CONVERGENCE_MAX = float(os.getenv("POLYMARKET_PRICE_MAX", "0.85"))

# === NICHE SPORTS (RE-ENABLED - opposite of polymarket_agent.py:44-54) ===
# These were EXCLUDED in the main agent, we INCLUDE them
NICHE_SPORTS_INCLUDE = [
    'esports', 'e-sports', 'league of legends', 'dota', 'valorant', 'csgo', 'cs2',
    'mma', 'ufc', 'bellator', 'pfl',
    'golf', 'pga', 'lpga', 'masters', 'open championship',
    'college', 'ncaa', 'march madness', 'college football',
    'womens', "women's", 'wnba', 'nwsl',
    'olympic', 'olympics', 'paralympic',
    'darts', 'pdc', 'snooker', 'pool',
    'cycling', 'tour de france', 'giro',
    'rowing', 'swimming', 'track and field',
    'cricket', 'ipl', 't20', 'test match',
    'rugby', 'six nations',
    'motorsport', 'indycar', 'nascar', 'rally',
]

# === TARGET NICHE CATEGORIES ===
# Obscure categories with potential edge
NICHE_CATEGORIES_TARGET = [
    # Politics - local/obscure
    'state election', 'local election', 'primary', 'runoff',
    'ballot measure', 'proposition', 'referendum',
    'governor', 'senate race', 'house race',
    'international election', 'uk election', 'france election',

    # Entertainment - awards and events
    'award show', 'oscar', 'academy award', 'grammy', 'emmy', 'golden globe',
    'tony award', 'bafta', 'cannes', 'sundance',
    'box office', 'streaming', 'netflix', 'viewer',
    'album', 'billboard', 'chart',

    # Science/Tech
    'spacex', 'nasa', 'rocket', 'launch', 'starship', 'falcon',
    'fda', 'fda approval', 'drug approval', 'clinical trial',
    'fed', 'fomc', 'interest rate', 'fed rate',
    'sec', 'regulatory', 'antitrust',

    # Weather/Climate
    'weather', 'hurricane', 'tropical storm', 'typhoon',
    'temperature', 'hottest', 'coldest', 'record',
    'wildfire', 'earthquake', 'flood',

    # Business/Tech
    'ipo', 'acquisition', 'merger', 'buyout',
    'layoff', 'ceo', 'resignation', 'appointment',
    'earnings', 'stock price', 'market cap',

    # Misc
    'summit', 'treaty', 'agreement', 'ceasefire',
    'pope', 'royal', 'monarchy',
]

# === CATEGORIES TO EXCLUDE (Still saturated/efficient) ===
# Major sports and crypto - too competitive
CATEGORIES_EXCLUDE = [
    # Major crypto price markets (saturated by quant bots)
    'bitcoin price', 'btc price', 'btc above', 'btc below',
    'ethereum price', 'eth price', 'eth above', 'eth below',
    'solana price', 'sol above', 'sol below',

    # Major sports finals (saturated by sports bettors)
    'super bowl winner', 'nba finals', 'nba champion',
    'world series winner', 'stanley cup',
    'premier league winner', 'champions league winner',
    'world cup winner',
]

# === SIGNAL THRESHOLDS ===
# LOOSENED: 3% edge instead of 5%
MIN_EDGE_PCT = float(os.getenv("POLYMARKET_MIN_EDGE", "3.0"))
# LOOSENED: 5/10 niche score instead of 6/10
MIN_NICHE_SCORE = float(os.getenv("POLYMARKET_MIN_NICHE_SCORE", "5.0"))
MIN_SWARM_CONFIDENCE = float(os.getenv("POLYMARKET_MIN_CONFIDENCE", "0.7"))
# LOOSENED: 2% arb threshold instead of 3%
ARBITRAGE_THRESHOLD = float(os.getenv("POLYMARKET_ARB_THRESHOLD", "0.02"))

# === NEWS MATCHING ===
NEWS_STALENESS_MINUTES = int(os.getenv("POLYMARKET_NEWS_STALE_MINS", "30"))
NEWS_RELEVANCE_THRESHOLD = float(os.getenv("POLYMARKET_NEWS_RELEVANCE", "0.7"))
NEWS_FEEDS_ENABLED = os.getenv("POLYMARKET_NEWS_ENABLED", "true").lower() == "true"

# === EXECUTION ===
USE_LIMIT_ORDERS = True  # Never market order on thin markets
LIMIT_ORDER_OFFSET = 0.01  # Place 1 cent better than best bid/ask
ORDER_TIMEOUT_HOURS = 4  # Cancel unfilled orders after this
DRY_RUN_MODE = os.getenv("POLYMARKET_DRY_RUN", "true").lower() == "true"

# === API ENDPOINTS ===
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"
DATA_API_BASE = "https://data-api.polymarket.com"

# === POLLING INTERVALS ===
MARKET_SCAN_INTERVAL_SECONDS = int(os.getenv("POLYMARKET_SCAN_INTERVAL", "300"))
SIGNAL_CHECK_INTERVAL_SECONDS = int(os.getenv("POLYMARKET_SIGNAL_INTERVAL", "60"))


def get_config_summary() -> str:
    """Return a summary of current configuration for logging."""
    return f"""
Polymarket Micro-Edge Configuration:
  Capital: ${MICRO_CAPITAL_TOTAL} total, ${MICRO_POSITION_SIZE_USD}/position
  Max Positions: {MICRO_MAX_POSITIONS}

  Liquidity Target: ${MIN_LIQUIDITY_FLOOR} - ${MAX_LIQUIDITY_TARGET}
  Resolution Window: {RESOLUTION_HOURS_MIN}h - {RESOLUTION_HOURS_MAX}h
  Price Range: {PRICE_CONVERGENCE_MIN} - {PRICE_CONVERGENCE_MAX}

  Min Edge: {MIN_EDGE_PCT}%
  Min Niche Score: {MIN_NICHE_SCORE}/10
  Arbitrage Threshold: {ARBITRAGE_THRESHOLD * 100}%

  Dry Run: {DRY_RUN_MODE}
  News Feeds: {NEWS_FEEDS_ENABLED}
"""
