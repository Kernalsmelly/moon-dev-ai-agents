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
# Minimum liquidity for early-window signal validation
MIN_LIQUIDITY_EARLY = float(os.getenv('MEME_MIN_LIQ_EARLY', '10000'))

# Market cap range in USD
MIN_MARKET_CAP_USD = float(os.getenv('MEME_MIN_MCAP', '25000'))
MAX_MARKET_CAP_USD = float(os.getenv('MEME_MAX_MCAP', '10000000'))

# Rug check settings
MAX_TOP_HOLDER_CONCENTRATION = float(os.getenv('MEME_MAX_TOP_HOLDER', '0.25'))  # 25%

# LP Lock requirement - only trade tokens with locked/burned liquidity
# This prevents most rug pulls
LP_LOCK_REQUIRED = os.getenv('MEME_LP_LOCK_REQUIRED', 'true').lower() in ('1', 'true', 'yes')
LP_LOCK_MIN_PERCENT = float(os.getenv('MEME_LP_LOCK_MIN_PCT', '50.0'))  # Min 50% of LP locked
LP_LOCK_BIRDEYE_ENABLED = os.getenv('MEME_LP_LOCK_BIRDEYE', 'true').lower() in ('1', 'true', 'yes')
LP_LOCK_STRICT = os.getenv('MEME_LP_LOCK_STRICT', 'false').lower() in ('1', 'true', 'yes')

# Momentum filters
MIN_PRICE_CHANGE_5M = float(os.getenv('MEME_MIN_PRICE_5M', '-5.0'))  # Min 5m price change %
MIN_BUY_SELL_RATIO = float(os.getenv('MEME_MIN_BUY_SELL', '1.0'))  # Min buys/sells ratio in 1h
MIN_BUY_SELL_RATIO_5M = float(os.getenv('MEME_MIN_BUY_SELL_5M', '0.0'))  # Min buys/sells ratio in 5m
MIN_TXNS_1H = int(os.getenv('MEME_MIN_TXNS_1H', '50'))
# 5m microstructure filters
MIN_TXNS_5M = int(os.getenv('MEME_MIN_TXNS_5M', '0'))
MIN_BUYS_5M = int(os.getenv('MEME_MIN_BUYS_5M', '0'))
MIN_VOLUME_5M = float(os.getenv('MEME_MIN_VOLUME_5M', '0.0'))
# Burst ratio: require recent volume to be a meaningful share of 1h volume
MIN_VOL5M_SHARE = float(os.getenv('MEME_MIN_VOL5M_SHARE', '0.0'))

# Liquidity/volume spike filters (early momentum signal)
SPIKE_FILTER_ENABLED = os.getenv('MEME_SPIKE_FILTER', 'false').lower() in ('1', 'true', 'yes')
MIN_LIQ_SPIKE_USD = float(os.getenv('MEME_MIN_LIQ_SPIKE_USD', '0'))  # e.g., 10000
MIN_VOL_SPIKE_5M = float(os.getenv('MEME_MIN_VOL_SPIKE_5M', '0'))  # e.g., 2000

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
MIN_VHI_SCORE = int(os.getenv('MEME_MIN_VHI_SCORE', '50'))

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
# Tuned via backtest_params.py against 583 real trades:
# "wider_first" variant won ($788 P&L, 53.9% WR vs $593 for tighter_first)
# Wider first tiers let winners run further before taking profit
# TP0: +35% gain -> sell 25%
# TP1: +60% gain -> sell 25%
# TP2: +100% gain -> sell 20%
# TP3: +150% gain -> sell 10%
# TP4: +250% gain -> sell 10%
# Moon bag: 10% rides forever (never sold unless -80% from peak)
TP_TIERS = [
    (0.35, 0.25),   # TP0: +35% first take (was +25%)
    (0.60, 0.25),   # TP1: +60% (was +50%)
    (1.00, 0.20),   # TP2: +100% (was +80%)
    (1.50, 0.10),   # TP3: +150% (was +120%)
    (2.50, 0.10),   # TP4: +250% moon territory (was +200%)
]

# Moon bag settings - never sell the last 10%, let it ride
MOON_BAG_ENABLED = os.getenv('MEME_MOON_BAG', 'true').lower() in ('1', 'true', 'yes')
MOON_BAG_FRACTION = float(os.getenv('MEME_MOON_BAG_FRACTION', '0.10'))  # Keep 10%
MOON_BAG_STOP = float(os.getenv('MEME_MOON_BAG_STOP', '-0.80'))  # Only exit moon bag at -80%
# When true, TP sell fractions are interpreted against original position size.
# Example with tiers 25/25/20/10/10 means cumulative 90% sold of original, ~10% moon bag left.
TP_FRACTIONS_AS_ORIGINAL = os.getenv('MEME_TP_FRACTIONS_AS_ORIGINAL', 'true').lower() in ('1', 'true', 'yes')
# Prevent tiny "penny exits" on partial profit takes.
EXIT_MIN_SLICE_USD = float(os.getenv('MEME_EXIT_MIN_SLICE_USD', '2.50'))
# Enforce a minimum moon bag notional on non-risk exits.
MOON_BAG_MIN_USD = float(os.getenv('MEME_MOON_BAG_MIN_USD', '1.00'))

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
# Widened to -25% so normal scaled stops fire first at -15%
GAP_PROTECTION_THRESHOLD = float(os.getenv('MEME_GAP_PROTECTION', '-0.25'))  # -25%

# Breakeven trigger: once +8% gain, move stop to entry
# Tuned via backtest: 0.08 outperformed 0.10/0.12/0.15 across all TP variants
# Matches trailing activation to eliminate the dead zone between breakeven and trailing
BREAKEVEN_TRIGGER = float(os.getenv('MEME_BREAKEVEN_TRIGGER', '0.08'))

# Trailing stop activation: once +8% gain, activate trailing
# Tuned via backtest: earlier trailing (0.08) protects gains better than 0.12
# Aligned with breakeven to eliminate dead zone
TRAILING_ACTIVATION = float(os.getenv('MEME_TRAILING_ACTIVATION', '0.08'))

# Dynamic trailing: different distances based on gain level
# At +15-30%: tight trail (-8%) to protect gains
# At +30-60%: moderate trail (-12%)
# At +60%+: wide trail (-18%) to let winners run
DYNAMIC_TRAILING_ENABLED = os.getenv('MEME_DYNAMIC_TRAILING', 'true').lower() in ('1', 'true', 'yes')
TRAILING_DISTANCE_TIGHT = float(os.getenv('MEME_TRAILING_TIGHT', '-0.08'))   # -8% for small gains
TRAILING_DISTANCE_MODERATE = float(os.getenv('MEME_TRAILING_MODERATE', '-0.12'))  # -12% for medium gains
TRAILING_DISTANCE_WIDE = float(os.getenv('MEME_TRAILING_WIDE', '-0.18'))   # -18% for big gains

# Micro trailing (early plateau protection)
# Some meme tokens peak around +3% to +10% and then dump; this protects small winners
# without requiring the +8% trailing activation.
MICRO_TRAIL_ENABLED = os.getenv('MEME_MICRO_TRAIL', 'true').lower() in ('1', 'true', 'yes')
MICRO_TRAIL_ACTIVATION = float(os.getenv('MEME_MICRO_TRAIL_ACTIVATION', '0.05'))  # +5% activates
MICRO_TRAIL_DISTANCE = float(os.getenv('MEME_MICRO_TRAIL_DISTANCE', '-0.04'))  # -4% from micro-peak
MICRO_TRAIL_SELL_FRACTION = float(os.getenv('MEME_MICRO_TRAIL_SELL', '0.30'))  # sell 30%

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

# Fail-fast exit: if no early momentum after entry, cut quickly
FAIL_FAST_ENABLED = os.getenv('MEME_FAIL_FAST', 'true').lower() in ('1', 'true', 'yes')
FAIL_FAST_WINDOW_SECONDS = int(os.getenv('MEME_FAIL_FAST_WINDOW', '180'))  # 3 minutes
FAIL_FAST_MIN_GAIN_PCT = float(os.getenv('MEME_FAIL_FAST_MIN_GAIN', '0.5'))  # % gain required
FAIL_FAST_SELL_FRACTION = float(os.getenv('MEME_FAIL_FAST_SELL', '0.50'))
FAIL_FAST_MIN_HOLD_SECONDS = int(os.getenv('MEME_FAIL_FAST_MIN_HOLD', '30'))  # don't fail-fast instantly
# Optional override: only trigger fail-fast when pnl is below this threshold.
# Example: -1.0 means "only fail-fast if down more than 1%".
_ff_tb = os.getenv('MEME_FAIL_FAST_TRIGGER_BELOW', '').strip()
FAIL_FAST_TRIGGER_BELOW_PCT = float(_ff_tb) if _ff_tb else None

# Cooldown after consecutive losses
LOSS_COOLDOWN_ENABLED = os.getenv('MEME_LOSS_COOLDOWN', 'true').lower() in ('1', 'true', 'yes')
LOSS_COOLDOWN_THRESHOLD = int(os.getenv('MEME_LOSS_COOLDOWN_THRESHOLD', '2'))  # losses in a row
LOSS_COOLDOWN_SECONDS = int(os.getenv('MEME_LOSS_COOLDOWN_SECONDS', '600'))  # 10 minutes

# Session risk controls
MAX_ENTRIES_PER_HOUR = int(os.getenv('MEME_MAX_ENTRIES_PER_HOUR', '0'))  # 0 = disabled
MAX_LOSS_PER_HOUR_USD = float(os.getenv('MEME_MAX_LOSS_PER_HOUR', '0.0'))  # 0 = disabled
LOSS_HALT_SECONDS = int(os.getenv('MEME_LOSS_HALT_SECONDS', '1800'))  # 30 min pause

# Momentum dump exit: if price drops X% in 5 minutes AND sell pressure confirms
# Relaxed from -10% to -20%: a -10% wick is normal for meme coins
MOMENTUM_DUMP_THRESHOLD = float(os.getenv('MEME_DUMP_THRESHOLD', '-20.0'))  # -20% in 5m triggers exit
MOMENTUM_DUMP_SELL_RATIO_MIN = float(os.getenv('MEME_DUMP_SELL_RATIO', '1.5'))  # Also require 1.5x sell pressure
MOMENTUM_DUMP_FIRST_FRACTION = float(os.getenv('MEME_DUMP_FIRST_FRACTION', '0.50'))  # Exit 50%, not 100%

# Volume collapse exit: exit if activity dries up and price is slipping
VOLUME_COLLAPSE_ENABLED = os.getenv('MEME_VOL_COLLAPSE', 'true').lower() in ('1', 'true', 'yes')
VOLUME_COLLAPSE_MIN_5M = float(os.getenv('MEME_VOL_COLLAPSE_MIN_5M', '1500'))  # USD volume in 5m
VOLUME_COLLAPSE_PRICE_DROP = float(os.getenv('MEME_VOL_COLLAPSE_DROP', '-3.0'))  # % in 5m
VOLUME_COLLAPSE_SELL_FRACTION = float(os.getenv('MEME_VOL_COLLAPSE_SELL', '0.50'))
# Only trigger volume-collapse exits when PnL is below this threshold (percent).
# Default: 0.0 => don't churn winners due to transient volume dips.
VOLUME_COLLAPSE_ONLY_IF_PNL_BELOW_PCT = float(os.getenv('MEME_VOL_COLLAPSE_ONLY_IF_PNL_BELOW_PCT', '0.0'))

# ============================================================================
# RISK MANAGEMENT
# ============================================================================
# Daily loss limit - stop trading after this much loss
DAILY_LOSS_LIMIT = float(os.getenv('MEME_DAILY_LOSS_LIMIT', '-100.0'))  # -$100
DAILY_LOSS_ENABLED = os.getenv('MEME_DAILY_LOSS_ENABLED', 'true').lower() in ('1', 'true', 'yes')

# Anti-rug detection: exit if sell volume spikes with concurrent price drop
# Relaxed from 2x to 3.5x: 2x triggers on normal profit-taking
ANTI_RUG_ENABLED = os.getenv('MEME_ANTI_RUG', 'true').lower() in ('1', 'true', 'yes')
ANTI_RUG_SELL_RATIO = float(os.getenv('MEME_ANTI_RUG_RATIO', '3.5'))  # Exit if sells > 3.5x buys in 5min
ANTI_RUG_MIN_PRICE_DROP = float(os.getenv('MEME_ANTI_RUG_MIN_DROP', '-5.0'))  # Require concurrent price drop
ANTI_RUG_MIN_VOLUME = int(os.getenv('MEME_ANTI_RUG_MIN_VOL', '20'))  # Minimum 5m txns to avoid noise

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
CONFIRMATION_MAX_ATTEMPTS = int(os.getenv('MEME_CONFIRMATION_MAX_ATTEMPTS', '3'))
CONFIRMATION_RETRY_DELAY_SECONDS = int(os.getenv('MEME_CONFIRMATION_RETRY_DELAY', '10'))

# ============================================================================
# PRE-ENTRY HOLDER CHECK
# ============================================================================
# Query on-chain holder concentration during confirmation wait
HOLDER_CHECK_ENABLED = os.getenv('MEME_HOLDER_CHECK', 'true').lower() in ('1', 'true', 'yes')
HOLDER_MAX_TOP5_CONCENTRATION = float(os.getenv('MEME_HOLDER_MAX_TOP5', '0.50'))  # Reject if top 5 hold >50%

# ============================================================================
# PRE-ENTRY MINT/FREEZE CHECK (On-chain)
# ============================================================================
# If enabled, reject tokens with mint authority or freeze authority enabled
# (common rug vectors). We fetch this directly from RPC by decoding the SPL
# Token Mint account, so it works without paid/off-chain providers.
MINT_FREEZE_CHECK_ENABLED = os.getenv('MEME_MINT_FREEZE_CHECK', 'true').lower() in ('1', 'true', 'yes')
# If strict, any check failure (network/API) rejects; otherwise it soft-passes.
MINT_FREEZE_STRICT = os.getenv('MEME_MINT_FREEZE_STRICT', 'false').lower() in ('1', 'true', 'yes')
HOLDER_MAX_SINGLE_WALLET = float(os.getenv('MEME_HOLDER_MAX_SINGLE', '0.30'))  # Reject if any single wallet >30%

# ============================================================================
# DYNAMIC TP ADJUSTMENT (disabled by default)
# ============================================================================
# When enabled, widens TP tiers by multiplier when momentum_score >= 70
DYNAMIC_TP_ENABLED = os.getenv('MEME_DYNAMIC_TP', 'false').lower() in ('1', 'true', 'yes')
DYNAMIC_TP_MOMENTUM_MULTIPLIER = float(os.getenv('MEME_DYNAMIC_TP_MULTIPLIER', '1.3'))

# ============================================================================
# JITO ENTRY BUNDLING (disabled by default)
# ============================================================================
# When enabled, entry swaps are submitted via Jito bundle for MEV protection
JITO_ENTRY_ENABLED = os.getenv('MEME_JITO_ENTRY', 'false').lower() in ('1', 'true', 'yes')

# ============================================================================
# LOGGING & DEBUG
# ============================================================================
# Verbose logging
VERBOSE_LOGGING = os.getenv('MEME_VERBOSE', 'false').lower() in ('1', 'true', 'yes')

# Log file path
LOG_FILE_PATH = os.getenv('MEME_LOG_FILE', 'data/meme_bot.log')


def get_position_size_for_score(score: int) -> float:
    """Get position size in SOL using linear interpolation from MIN to MAX.

    Size scales proportionally based on how far the score exceeds MIN_VHI_SCORE.
    At MIN_VHI_SCORE -> MIN_POSITION_SIZE_SOL, at 100 -> MAX_POSITION_SIZE_SOL.

    Args:
        score: Composite score 0-100

    Returns:
        Position size in SOL
    """
    if score <= MIN_VHI_SCORE:
        return MIN_POSITION_SIZE_SOL
    if score >= 100:
        return MAX_POSITION_SIZE_SOL
    # Linear interpolation between min and max
    range_score = 100 - MIN_VHI_SCORE
    if range_score <= 0:
        return MIN_POSITION_SIZE_SOL
    frac = (score - MIN_VHI_SCORE) / range_score
    size = MIN_POSITION_SIZE_SOL + frac * (MAX_POSITION_SIZE_SOL - MIN_POSITION_SIZE_SOL)
    return round(size, 4)


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
