"""Meme Coin Trading Bot Configuration

Configuration settings for the meme coin trading bot including:
- Discovery: poll interval, max token age
- Filtering: min liquidity, market cap range
- Scoring: thresholds, weights
- Position: max positions, sizing
- Exits: TP/SL levels, trailing, time limits
- Mode: paper/live toggle
"""
import os

# ============================================================================
# MODE SETTINGS
# ============================================================================
# Paper trading mode (default: True for safety)
MEME_PAPER_MODE = os.getenv('MEME_PAPER_MODE', 'true').lower() in ('1', 'true', 'yes')

# Enable/disable the meme bot entirely
MEME_BOT_ENABLED = os.getenv('MEME_BOT_ENABLED', 'true').lower() in ('1', 'true', 'yes')

# ============================================================================
# DISCOVERY SETTINGS
# ============================================================================
# How often to poll for new tokens (seconds)
# REDUCED from 15 to 5 to catch crashes faster
POLL_INTERVAL_SECONDS = int(os.getenv('MEME_POLL_INTERVAL', '5'))

# Max token age to consider for entry (seconds)
# Set high to allow established tokens - vampire filter handles scam copies
MAX_TOKEN_AGE_SECONDS = int(os.getenv('MEME_MAX_TOKEN_AGE', '604800'))  # 7 days

# Token discovery API (DexScreener - no API key required)
DEXSCREENER_API_URL = os.getenv('DEXSCREENER_API_URL', 'https://api.dexscreener.com')

# Helius API for on-chain data (uses HELIUS_API_KEY from env)
HELIUS_API_URL = os.getenv('HELIUS_URL', '')

# ============================================================================
# FILTERING SETTINGS
# ============================================================================
# Vampire/copycat token filter - block tokens using names of established coins
# These are likely scam tokens trying to piggyback on real project names
VAMPIRE_TOKEN_NAMES = {
    'TRUMP', 'MELANIA', 'DOGE', 'SHIB', 'PEPE', 'BONK', 'WIF', 'POPCAT',
    'FLOKI', 'BRETT', 'MEME', 'SNEK', 'MYRO', 'WEN', 'BOME', 'SLERF',
    'MEW', 'PONKE', 'GIGA', 'MUMU', 'PENG', 'FWOG', 'GOAT', 'MOODENG',
    'PNUT', 'CHILLGUY', 'FARTCOIN', 'AI16Z', 'ZEREBRO', 'GRIFFAIN',
    'BTC', 'ETH', 'SOL', 'USDT', 'USDC', 'BNB', 'XRP', 'ADA', 'AVAX',
}

# Minimum liquidity in USD
MIN_LIQUIDITY_USD = float(os.getenv('MEME_MIN_LIQUIDITY', '15000'))

# Market cap range in USD
MIN_MARKET_CAP_USD = float(os.getenv('MEME_MIN_MCAP', '25000'))
MAX_MARKET_CAP_USD = float(os.getenv('MEME_MAX_MCAP', '10000000'))

# Rug check settings
MAX_TOP_HOLDER_CONCENTRATION = float(os.getenv('MEME_MAX_TOP_HOLDER', '0.25'))  # 25%

# LP Lock requirement - only trade tokens with locked/burned liquidity
# This prevents most rug pulls
LP_LOCK_REQUIRED = os.getenv('MEME_LP_LOCK_REQUIRED', 'true').lower() in ('1', 'true', 'yes')
LP_LOCK_MIN_PERCENT = float(os.getenv('MEME_LP_LOCK_MIN_PCT', '50.0'))  # Min 50% of LP locked

# Momentum filters
MIN_PRICE_CHANGE_5M = float(os.getenv('MEME_MIN_PRICE_5M', '-5.0'))  # Min 5m price change %
MIN_BUY_SELL_RATIO = float(os.getenv('MEME_MIN_BUY_SELL', '1.0'))  # Min buys/sells ratio in 1h
MIN_TXNS_1H = int(os.getenv('MEME_MIN_TXNS_1H', '50'))

# Pullback entry: Don't chase pumps - prefer tokens pulling back from highs
# Require 1h change > 5m change (momentum slowing = safer entry)
PULLBACK_ENTRY_ENABLED = os.getenv('MEME_PULLBACK_ENTRY', 'true').lower() in ('1', 'true', 'yes')
MAX_5M_PUMP = float(os.getenv('MEME_MAX_5M_PUMP', '30.0'))  # Reject if 5m pump > 30% (chasing)

# SOL correlation filter: Pause entries when SOL is dumping
SOL_CORRELATION_ENABLED = os.getenv('MEME_SOL_CORRELATION', 'true').lower() in ('1', 'true', 'yes')
SOL_DUMP_THRESHOLD = float(os.getenv('MEME_SOL_DUMP_THRESHOLD', '-3.0'))  # Pause if SOL down > 3% in 1h

# ============================================================================
# SCORING SETTINGS
# ============================================================================
# Minimum VolumeHeatIndex score to consider entry
MIN_VHI_SCORE = int(os.getenv('MEME_MIN_VHI_SCORE', '40'))

# High conviction score (skip corroboration)
HIGH_CONVICTION_SCORE = int(os.getenv('MEME_HIGH_CONVICTION', '60'))

# Weights for composite score (VHI weight + Graduation weight = 1.0)
VHI_WEIGHT = float(os.getenv('MEME_VHI_WEIGHT', '0.6'))
GRADUATION_WEIGHT = float(os.getenv('MEME_GRADUATION_WEIGHT', '0.4'))

# ============================================================================
# POSITION SETTINGS
# ============================================================================
# Maximum concurrent positions
MAX_POSITIONS = int(os.getenv('MEME_MAX_POSITIONS', '5'))

# Position sizing in SOL based on score
# Score 40-50: 0.06 SOL, 50-60: 0.12 SOL, 60-70: 0.18 SOL, 70+: 0.25 SOL
POSITION_SIZE_TIERS = [
    (40, 50, 0.06),    # (min_score, max_score, size_sol) ~$6
    (50, 60, 0.12),    # ~$12
    (60, 70, 0.18),    # ~$18
    (70, 100, 0.25),   # ~$25
]

# Fallback position size if no tier matches
DEFAULT_POSITION_SIZE_SOL = float(os.getenv('MEME_DEFAULT_SIZE', '0.06'))

# Minimum and maximum position size in SOL
MIN_POSITION_SIZE_SOL = float(os.getenv('MEME_MIN_SIZE', '0.03'))
MAX_POSITION_SIZE_SOL = float(os.getenv('MEME_MAX_SIZE', '0.25'))

# ============================================================================
# EXIT SETTINGS - TAKE PROFIT
# ============================================================================
# Take profit tiers: (gain_pct, sell_pct)
# TP0: +25% gain -> sell 25%
# TP1: +50% gain -> sell 25%
# TP2: +80% gain -> sell 20%
# TP3: +120% gain -> sell 10%
# TP4: +200% gain -> sell 10%
# Moon bag: 10% rides forever (never sold unless -80% from peak)
TP_TIERS = [
    (0.25, 0.25),   # TP0: +25% first take
    (0.50, 0.25),   # TP1: +50%
    (0.80, 0.20),   # TP2: +80%
    (1.20, 0.10),   # TP3: +120%
    (2.00, 0.10),   # TP4: +200% moon territory
]

# Moon bag settings - never sell the last 10%, let it ride
MOON_BAG_ENABLED = os.getenv('MEME_MOON_BAG', 'true').lower() in ('1', 'true', 'yes')
MOON_BAG_FRACTION = float(os.getenv('MEME_MOON_BAG_FRACTION', '0.10'))  # Keep 10%
MOON_BAG_STOP = float(os.getenv('MEME_MOON_BAG_STOP', '-0.80'))  # Only exit moon bag at -80%

# Quick scalp: take quick profit if token pumps fast
QUICK_SCALP_ENABLED = os.getenv('MEME_QUICK_SCALP', 'true').lower() in ('1', 'true', 'yes')
QUICK_SCALP_GAIN = float(os.getenv('MEME_QUICK_SCALP_GAIN', '0.12'))
QUICK_SCALP_WINDOW_SECONDS = int(os.getenv('MEME_QUICK_SCALP_WINDOW', '300'))
QUICK_SCALP_SELL_FRACTION = float(os.getenv('MEME_QUICK_SCALP_SELL', '0.30'))

# Time-based profit taking: +5% after 10 min, sell 20%
TIME_PROFIT_ENABLED = os.getenv('MEME_TIME_PROFIT', 'true').lower() in ('1', 'true', 'yes')
TIME_PROFIT_MINUTES = int(os.getenv('MEME_TIME_PROFIT_MINUTES', '10'))  # Check after 10 min
TIME_PROFIT_THRESHOLD = float(os.getenv('MEME_TIME_PROFIT_THRESHOLD', '0.05'))  # +5% gain
TIME_PROFIT_SELL_FRACTION = float(os.getenv('MEME_TIME_PROFIT_SELL', '0.20'))  # Sell 20%

# ============================================================================
# EXIT SETTINGS - STOP LOSS
# ============================================================================
# Initial hard stop loss (as negative fraction, -0.15 = -15%)
INITIAL_STOP_LOSS = float(os.getenv('MEME_INITIAL_SL', '-0.15'))

# Tighter stop for high conviction (large) positions: -12%
HIGH_CONVICTION_STOP_LOSS = float(os.getenv('MEME_HIGH_CONVICTION_SL', '-0.12'))

# Max loss cap per trade in USD (prevents catastrophic single losses)
MAX_LOSS_PER_TRADE_USD = float(os.getenv('MEME_MAX_LOSS_PER_TRADE', '3.00'))

# Gap protection: exit IMMEDIATELY if price drops more than this % from entry
# This catches fast crashes that gap through normal stops
# TIGHTENED from -20% to -15% for faster exit
GAP_PROTECTION_THRESHOLD = float(os.getenv('MEME_GAP_PROTECTION', '-0.15'))  # -15%

# Breakeven trigger: once +10% gain, move stop to entry (faster protection)
BREAKEVEN_TRIGGER = float(os.getenv('MEME_BREAKEVEN_TRIGGER', '0.10'))

# Trailing stop activation: once +15% gain, activate trailing (earlier)
TRAILING_ACTIVATION = float(os.getenv('MEME_TRAILING_ACTIVATION', '0.15'))

# Dynamic trailing: different distances based on gain level
# At +15-30%: tight trail (-8%) to protect gains
# At +30-60%: moderate trail (-12%)
# At +60%+: wide trail (-18%) to let winners run
DYNAMIC_TRAILING_ENABLED = os.getenv('MEME_DYNAMIC_TRAILING', 'true').lower() in ('1', 'true', 'yes')
TRAILING_DISTANCE_TIGHT = float(os.getenv('MEME_TRAILING_TIGHT', '-0.08'))   # -8% for small gains
TRAILING_DISTANCE_MODERATE = float(os.getenv('MEME_TRAILING_MODERATE', '-0.12'))  # -12% for medium gains
TRAILING_DISTANCE_WIDE = float(os.getenv('MEME_TRAILING_WIDE', '-0.18'))   # -18% for big gains

# Legacy trailing distance (used if dynamic trailing disabled)
TRAILING_DISTANCE = float(os.getenv('MEME_TRAILING_DISTANCE', '-0.10'))

# Scaled stops: exit 50% on first stop, keep 50% for potential recovery
# Prevents full shakeout before moonshots
SCALED_STOPS_ENABLED = os.getenv('MEME_SCALED_STOPS', 'true').lower() in ('1', 'true', 'yes')
SCALED_STOP_FIRST_FRACTION = float(os.getenv('MEME_SCALED_FIRST', '0.50'))  # Sell 50% on first stop
SCALED_STOP_SECOND_DROP = float(os.getenv('MEME_SCALED_SECOND_DROP', '-0.05'))  # Second stop -5% lower

# Re-entry after stop: if token recovers X% from exit price, re-enter
# Catches V-shaped recoveries that precede big runs
# DISABLED: Re-entries were causing outsized losses when tokens dump after recovery
REENTRY_ENABLED = os.getenv('MEME_REENTRY', 'false').lower() in ('1', 'true', 'yes')
REENTRY_RECOVERY_PCT = float(os.getenv('MEME_REENTRY_RECOVERY', '0.20'))  # +20% from exit = re-enter
REENTRY_SIZE_FRACTION = float(os.getenv('MEME_REENTRY_SIZE', '0.50'))  # Re-enter at 50% original size
REENTRY_COOLDOWN_SECONDS = int(os.getenv('MEME_REENTRY_COOLDOWN', '300'))  # 5 min cooldown after exit
REENTRY_MAX_ATTEMPTS = int(os.getenv('MEME_REENTRY_MAX', '1'))  # Max 1 re-entry per token
# Cap re-entry position size to limit max potential loss
# At -15% stop loss, $20 position max loss = $3
REENTRY_MAX_POSITION_USD = float(os.getenv('MEME_REENTRY_MAX_POS', '20.0'))

# ============================================================================
# EXIT SETTINGS - TIME BASED
# ============================================================================
# Maximum hold time in seconds (6 hours)
MAX_HOLD_TIME_SECONDS = int(os.getenv('MEME_MAX_HOLD_TIME', '21600'))

# Stagnation exit: if price doesn't move X% in Y minutes, exit
STAGNATION_CHECK_MINUTES = int(os.getenv('MEME_STAGNATION_MINUTES', '30'))
STAGNATION_THRESHOLD_PCT = float(os.getenv('MEME_STAGNATION_PCT', '0.05'))  # 5%

# Momentum dump exit: if price drops X% in 5 minutes, exit early
MOMENTUM_DUMP_THRESHOLD = float(os.getenv('MEME_DUMP_THRESHOLD', '-10.0'))  # -10% in 5m triggers exit

# ============================================================================
# RISK MANAGEMENT
# ============================================================================
# Daily loss limit - stop trading after this much loss
DAILY_LOSS_LIMIT = float(os.getenv('MEME_DAILY_LOSS_LIMIT', '-100.0'))  # -$100
DAILY_LOSS_ENABLED = os.getenv('MEME_DAILY_LOSS_ENABLED', 'true').lower() in ('1', 'true', 'yes')

# Anti-rug detection: exit if sell volume spikes (sells > X * buys in 5min)
# TIGHTENED from 3x to 2x for faster rug detection
ANTI_RUG_ENABLED = os.getenv('MEME_ANTI_RUG', 'true').lower() in ('1', 'true', 'yes')
ANTI_RUG_SELL_RATIO = float(os.getenv('MEME_ANTI_RUG_RATIO', '2.0'))  # Exit if sells > 2x buys in 5min

# ============================================================================
# DISCORD ALERTS
# ============================================================================
# Enable Discord alerts
DISCORD_ALERTS_ENABLED = os.getenv('MEME_DISCORD_ALERTS', 'true').lower() in ('1', 'true', 'yes')

# ============================================================================
# ENTRY CONFIRMATION DELAY
# ============================================================================
# Wait before executing entry to verify price is sustained
CONFIRMATION_ENABLED = os.getenv('MEME_CONFIRMATION', 'true').lower() in ('1', 'true', 'yes')
CONFIRMATION_DELAY_SECONDS = int(os.getenv('MEME_CONFIRMATION_DELAY', '30'))
CONFIRMATION_MAX_PRICE_DROP = float(os.getenv('MEME_CONFIRMATION_MAX_DROP', '-5.0'))  # Reject if price dropped >5%

# ============================================================================
# PRE-ENTRY HOLDER CHECK
# ============================================================================
# Query on-chain holder concentration during confirmation wait
HOLDER_CHECK_ENABLED = os.getenv('MEME_HOLDER_CHECK', 'true').lower() in ('1', 'true', 'yes')
HOLDER_MAX_TOP5_CONCENTRATION = float(os.getenv('MEME_HOLDER_MAX_TOP5', '0.50'))  # Reject if top 5 hold >50%
HOLDER_MAX_SINGLE_WALLET = float(os.getenv('MEME_HOLDER_MAX_SINGLE', '0.30'))  # Reject if any single wallet >30%

# ============================================================================
# LOGGING & DEBUG
# ============================================================================
# Verbose logging
VERBOSE_LOGGING = os.getenv('MEME_VERBOSE', 'false').lower() in ('1', 'true', 'yes')

# Log file path
LOG_FILE_PATH = os.getenv('MEME_LOG_FILE', 'data/meme_bot.log')


def get_position_size_for_score(score: int) -> float:
    """Get position size in SOL based on score tier.

    Args:
        score: Composite score 0-100

    Returns:
        Position size in SOL
    """
    for min_score, max_score, size in POSITION_SIZE_TIERS:
        if min_score <= score < max_score:
            return size
    # Handle score >= 100 (use last tier)
    if score >= POSITION_SIZE_TIERS[-1][0]:
        return POSITION_SIZE_TIERS[-1][2]
    return DEFAULT_POSITION_SIZE_SOL


def should_enter(score: int) -> bool:
    """Check if score meets entry criteria.

    Args:
        score: Composite score 0-100

    Returns:
        True if score is high enough for entry
    """
    return score >= MIN_VHI_SCORE


def is_high_conviction(score: int) -> bool:
    """Check if score is high conviction (skip corroboration).

    Args:
        score: Composite score 0-100

    Returns:
        True if score is high enough to skip corroboration
    """
    return score >= HIGH_CONVICTION_SCORE


def get_stop_loss_for_score(score: int) -> float:
    """Get appropriate stop loss based on score/position size.

    High conviction (70+) positions use tighter stops (-12%)
    to prevent large dollar losses on bigger positions.

    Args:
        score: Composite score 0-100

    Returns:
        Stop loss as negative fraction (e.g., -0.15 for -15%)
    """
    if score >= 70:
        return HIGH_CONVICTION_STOP_LOSS  # -12% for large positions
    return INITIAL_STOP_LOSS  # -15% for smaller positions
