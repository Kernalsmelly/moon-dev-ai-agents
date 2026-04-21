"""Meme Exit Manager: Handles all exit logic for meme coin positions.

Exit logic includes:
- Take profit tiers (TP1 @ +100%, TP2 @ +200%, TP3 @ +300%)
- Stop loss (hard -25%, trailing -20% from peak)
- Breakeven stop (once +30%, move stop to entry)
- Time-based (max 6 hours, stagnation 30 min)

Usage:
    from src.meme_exit_manager import MemeExitManager

    manager = MemeExitManager()
    result = manager.check_exit(position)
    # result: {'should_exit': True, 'reason': 'TP1', 'sell_fraction': 0.4}
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import src.meme_config as meme_config


@dataclass
class PositionState:
    """Tracks state for a single position."""
    mint: str
    symbol: str
    entry_price: float
    entry_time: float  # Unix timestamp
    amount_tokens: float
    amount_usd: float
    initial_amount_tokens: float = 0.0
    initial_amount_usd: float = 0.0
    highest_price_seen: float = 0.0
    tp0_hit: bool = False  # TP0: +15% early profit lock
    tp1_hit: bool = False
    tp2_hit: bool = False
    tp3_hit: bool = False
    tp4_hit: bool = False  # TP4 for moonshots
    time_profit_taken: bool = False  # Time-based profit already taken
    is_moon_bag: bool = False  # True when position is down to moon bag only
    first_stop_hit: bool = False  # Scaled stops: first 50% exit taken
    first_stop_price: float = 0.0  # Price where first stop was hit
    breakeven_hit: bool = False  # Once triggered, stop stays at entry price
    trailing_activated: bool = False  # Once trailing is on, don't revert to breakeven
    last_price_check: float = 0.0
    last_price_check_time: float = 0.0
    score: int = 0  # Entry score for position sizing decisions
    initial_stop_pct: float = -0.15  # Position-specific stop (tighter for large positions)
    tp_stages_log: list = field(default_factory=list)  # TP stage history
    market_cap_entry: float = 0.0  # USD market cap estimate at entry (signal-first)
    market_cap_current: float = 0.0  # Updated during monitoring when available
    market_cap_highest: float = 0.0  # Track highest seen (optional plateau logic)
    # Persisted signal metadata (do not depend on launch-signal TTL at exit time).
    signal_score: float = 0.0
    signal_tier: str = ""
    # Persist key launch-signal metrics for analysis and exit tuning.
    signal_hits: int = 0
    signal_buys: int = 0
    signal_sells: int = 0
    signal_unique_buyers: int = 0
    signal_net_sol_in: float = 0.0
    signal_top_buyer_share: float = 0.0
    signal_t_first_sell_s: Optional[float] = None
    signal_source: str = ""
    signal_source_family: str = ""
    signal_rank_score: float = 0.0
    signal_rank_matches: int = 0
    signal_rank_negative_hits: int = 0
    signal_profile: str = ""
    signal_pair_age_min: float = 0.0
    signal_mover_pattern: str = ""
    signal_unique_buyers_estimated: bool = False
    signal_top_buyer_share_estimated: bool = False
    winner_score: float = 0.0
    winner_features_used: int = 0
    winner_size_mult: float = 1.0
    liquidity_estimated: bool = False
    mcap_scout_mode: bool = False
    mcap_scout_size_mult: float = 1.0

    def __post_init__(self):
        if self.highest_price_seen == 0.0:
            self.highest_price_seen = self.entry_price
        if self.initial_amount_tokens <= 0.0:
            self.initial_amount_tokens = float(self.amount_tokens)
        if self.initial_amount_usd <= 0.0:
            self.initial_amount_usd = float(self.amount_usd)
        if self.market_cap_highest == 0.0 and self.market_cap_entry:
            self.market_cap_highest = float(self.market_cap_entry)


@dataclass
class ExitResult:
    """Result of an exit check."""
    should_exit: bool = False
    reason: str = ''
    sell_fraction: float = 0.0  # Fraction of position to sell (0-1)
    details: dict = field(default_factory=dict)


class MemeExitManager:
    """Manages exit logic for meme coin positions."""

    def __init__(self):
        """Initialize the exit manager."""
        # Load config values
        self.tp_tiers = meme_config.TP_TIERS
        self.initial_stop_loss = meme_config.INITIAL_STOP_LOSS
        self.breakeven_trigger = meme_config.BREAKEVEN_TRIGGER
        self.trailing_activation = meme_config.TRAILING_ACTIVATION
        self.trailing_distance = meme_config.TRAILING_DISTANCE
        self.max_hold_time = meme_config.MAX_HOLD_TIME_SECONDS
        self.stagnation_minutes = meme_config.STAGNATION_CHECK_MINUTES
        self.stagnation_threshold = meme_config.STAGNATION_THRESHOLD_PCT
        # Dynamic max-loss cap by entry quality score.
        # Purpose: keep weak setups from consuming full hard-loss budget.
        self.dynamic_max_loss_enabled = os.getenv("MEME_DYNAMIC_MAX_LOSS_ENABLED", "true").lower() in (
            "1",
            "true",
            "yes",
        )
        self.dynamic_max_loss_score_mid = float(os.getenv("MEME_DYNAMIC_MAX_LOSS_SCORE_MID", "90") or 90)
        self.dynamic_max_loss_score_low = float(os.getenv("MEME_DYNAMIC_MAX_LOSS_SCORE_LOW", "80") or 80)
        self.dynamic_max_loss_mult_mid = float(os.getenv("MEME_DYNAMIC_MAX_LOSS_MULT_MID", "0.75") or 0.75)
        self.dynamic_max_loss_mult_low = float(os.getenv("MEME_DYNAMIC_MAX_LOSS_MULT_LOW", "0.55") or 0.55)
        self.dynamic_max_loss_floor_usd = float(os.getenv("MEME_DYNAMIC_MAX_LOSS_FLOOR_USD", "0.20") or 0.20)

    def check_exit(self, position: PositionState, current_price: float) -> ExitResult:
        """Check if position should exit based on current price.

        Args:
            position: Current position state
            current_price: Current token price

        Returns:
            ExitResult with exit decision and details
        """
        # Update highest price seen
        if current_price > position.highest_price_seen:
            position.highest_price_seen = current_price

        # Calculate P&L
        pnl_pct = (current_price - position.entry_price) / position.entry_price

        # Check in order of priority
        # 1. Check take profits
        tp_result = self._check_take_profits(position, pnl_pct)
        if tp_result.should_exit:
            return tp_result

        # 2. Check stop loss (handles breakeven and trailing)
        sl_result = self._check_stop_loss(position, current_price, pnl_pct)
        if sl_result.should_exit:
            return sl_result

        # 3. Check time-based exits
        time_result = self._check_time_exits(position, current_price)
        if time_result.should_exit:
            return time_result

        return ExitResult(should_exit=False)

    def _check_take_profits(self, position: PositionState, pnl_pct: float) -> ExitResult:
        """Check take profit levels with moon bag support.

        TP tiers (with moon bag enabled):
        - TP0: +15% -> sell 25% (early profit lock)
        - TP1: +30% -> sell 25%
        - TP2: +60% -> sell 20%
        - TP3: +100% -> sell 10%
        - TP4: +150% -> sell 10%
        - Moon bag: 10% rides forever

        Args:
            position: Current position state
            pnl_pct: Current P&L as fraction (0.5 = +50%)

        Returns:
            ExitResult if TP hit, otherwise empty result
        """
        # If this is a moon bag, don't take more profits (let it ride)
        if position.is_moon_bag:
            return ExitResult(should_exit=False)

        # Moon bag settings
        moon_bag_enabled = getattr(meme_config, 'MOON_BAG_ENABLED', True)
        moon_bag_fraction = getattr(meme_config, 'MOON_BAG_FRACTION', 0.10)

        tp_stages = (
            ("tp0_hit", "TP0"),
            ("tp1_hit", "TP1"),
            ("tp2_hit", "TP2"),
            ("tp3_hit", "TP3"),
            ("tp4_hit", "TP4"),
        )
        for idx, (hit_attr, tier_name) in enumerate(tp_stages):
            if idx >= len(self.tp_tiers):
                break
            if getattr(position, hit_attr):
                continue

            target_pct, configured_sell_fraction = self.tp_tiers[idx]
            if pnl_pct < target_pct:
                continue

            sell_fraction, sell_fraction_original = self._resolve_tp_sell_fraction(
                position=position,
                stage_idx=idx,
                configured_sell_fraction=configured_sell_fraction,
                moon_bag_enabled=moon_bag_enabled,
                moon_bag_fraction=moon_bag_fraction,
            )
            if sell_fraction <= 0.0:
                # No sellable size left in this stage (already at moon bag floor).
                setattr(position, hit_attr, True)
                if moon_bag_enabled:
                    position.is_moon_bag = True
                return ExitResult(should_exit=False)

            setattr(position, hit_attr, True)

            cumulative_after = self._calc_cumulative_sold_original(position) + sell_fraction_original
            if moon_bag_enabled and cumulative_after >= (1.0 - moon_bag_fraction - 1e-6):
                position.is_moon_bag = True

            self._log_tp_stage(
                position=position,
                tier=tier_name,
                pnl_pct=pnl_pct,
                sold_fraction=sell_fraction,
                sold_fraction_original=sell_fraction_original,
            )
            return ExitResult(
                should_exit=True,
                reason='TP4_MOON_BAG' if (tier_name == 'TP4' and moon_bag_enabled) else tier_name,
                sell_fraction=sell_fraction,
                details={
                    'pnl_pct': pnl_pct * 100,
                    'target_pct': target_pct * 100,
                    'moon_bag': moon_bag_enabled and position.is_moon_bag,
                    'sell_fraction_original': round(sell_fraction_original, 6),
                }
            )

        return ExitResult(should_exit=False)

    def _check_stop_loss(
        self,
        position: PositionState,
        current_price: float,
        pnl_pct: float
    ) -> ExitResult:
        """Check stop loss with smart logic and dynamic trailing.

        Smart stop logic:
        - Moon bags: only exit at -80% from peak (let them ride)
        - Regular positions:
          - If unrealized P&L < +10%: Hard stop at -15%
          - If +10-15%: Move stop to breakeven (entry price)
          - If > +15%: Dynamic trailing based on gain level

        Dynamic trailing:
        - At +15-30%: tight trail (-8%) to protect gains
        - At +30-60%: moderate trail (-12%)
        - At +60%+: wide trail (-18%) to let winners run

        Args:
            position: Current position state
            current_price: Current token price
            pnl_pct: Current P&L as fraction

        Returns:
            ExitResult if stop hit, otherwise empty result
        """
        # Moon bag special handling - very wide stop, let it ride
        if position.is_moon_bag:
            moon_bag_stop = getattr(meme_config, 'MOON_BAG_STOP', -0.80)
            # Calculate drop from peak for moon bag
            drop_from_peak = (current_price - position.highest_price_seen) / position.highest_price_seen
            if drop_from_peak <= moon_bag_stop:
                return ExitResult(
                    should_exit=True,
                    reason='MOON_BAG_STOP',
                    sell_fraction=1.0,
                    details={
                        'pnl_pct': pnl_pct * 100,
                        'drop_from_peak': drop_from_peak * 100,
                        'threshold': moon_bag_stop * 100,
                    }
                )
            # Moon bags don't use other stops
            return ExitResult(should_exit=False)

        # Check max loss per trade (prevents catastrophic single losses)
        max_loss_usd = float(getattr(meme_config, 'MAX_LOSS_PER_TRADE_USD', 10.0) or 10.0)
        score_used = float(getattr(position, "signal_score", 0.0) or getattr(position, "score", 0.0) or 0.0)
        if self.dynamic_max_loss_enabled and max_loss_usd > 0:
            if score_used > 0 and score_used < self.dynamic_max_loss_score_low:
                max_loss_usd = max(float(self.dynamic_max_loss_floor_usd), max_loss_usd * float(self.dynamic_max_loss_mult_low))
            elif score_used > 0 and score_used < self.dynamic_max_loss_score_mid:
                max_loss_usd = max(float(self.dynamic_max_loss_floor_usd), max_loss_usd * float(self.dynamic_max_loss_mult_mid))
        current_loss_usd = pnl_pct * position.amount_usd
        if current_loss_usd <= -max_loss_usd:
            return ExitResult(
                should_exit=True,
                reason='MAX_LOSS_CAP',
                sell_fraction=1.0,
                details={
                    'loss_usd': abs(current_loss_usd),
                    'max_loss_usd': max_loss_usd,
                    'score_used': score_used,
                    'pnl_pct': pnl_pct * 100,
                }
            )

        # Calculate stop price based on current state
        # IMPORTANT: Once breakeven or trailing is triggered, it LOCKS IN and doesn't revert
        if pnl_pct >= self.trailing_activation:
            # Mark trailing as activated (locks in)
            position.trailing_activated = True
            position.breakeven_hit = True  # Also mark breakeven since we passed it
            # Dynamic trailing: different distances based on gain level
            dynamic_enabled = getattr(meme_config, 'DYNAMIC_TRAILING_ENABLED', True)
            if dynamic_enabled:
                if pnl_pct >= 0.60:
                    # Big gains (+60%+): wide trail to let it run
                    trail_distance = getattr(meme_config, 'TRAILING_DISTANCE_WIDE', -0.18)
                    stop_type = 'TRAILING_WIDE'
                elif pnl_pct >= 0.30:
                    # Medium gains (+30-60%): moderate trail
                    trail_distance = getattr(meme_config, 'TRAILING_DISTANCE_MODERATE', -0.12)
                    stop_type = 'TRAILING_MODERATE'
                else:
                    # Small gains (+15-30%): tight trail to protect
                    trail_distance = getattr(meme_config, 'TRAILING_DISTANCE_TIGHT', -0.08)
                    stop_type = 'TRAILING_TIGHT'
            else:
                trail_distance = self.trailing_distance
                stop_type = 'TRAILING_STOP'
            stop_price = position.highest_price_seen * (1 + trail_distance)
        elif pnl_pct >= self.breakeven_trigger or position.breakeven_hit:
            # Breakeven stop: entry price (LOCKS IN once triggered)
            position.breakeven_hit = True
            stop_price = position.entry_price
            stop_type = 'BREAKEVEN_STOP'
        elif position.trailing_activated:
            # Trailing was activated but price dropped - use trailing from highest seen
            trail_distance = getattr(meme_config, 'TRAILING_DISTANCE_TIGHT', -0.08)
            stop_price = position.highest_price_seen * (1 + trail_distance)
            stop_type = 'TRAILING_TIGHT'
        else:
            # Initial hard stop: use position-specific stop (tighter for large positions)
            stop_pct = position.initial_stop_pct if position.initial_stop_pct else self.initial_stop_loss
            stop_price = position.entry_price * (1 + stop_pct)
            stop_type = 'HARD_STOP'

        # Check if current price is below stop
        if current_price <= stop_price:
            # Scaled stops: exit 50% on first stop, keep 50% for potential recovery
            scaled_stops_enabled = getattr(meme_config, 'SCALED_STOPS_ENABLED', True)

            if scaled_stops_enabled and not position.first_stop_hit:
                # First stop: sell 50%, keep rest for potential recovery
                first_fraction = getattr(meme_config, 'SCALED_STOP_FIRST_FRACTION', 0.50)
                position.first_stop_hit = True
                position.first_stop_price = current_price
                return ExitResult(
                    should_exit=True,
                    reason=f'{stop_type}_SCALED_1',
                    sell_fraction=first_fraction,
                    details={
                        'current_price': current_price,
                        'stop_price': stop_price,
                        'pnl_pct': pnl_pct * 100,
                        'highest_seen': position.highest_price_seen,
                        'scaled_stop': 'first',
                    }
                )
            elif scaled_stops_enabled and position.first_stop_hit:
                # Second stop: check if dropped further from first stop
                second_drop = getattr(meme_config, 'SCALED_STOP_SECOND_DROP', -0.05)
                second_stop_price = position.first_stop_price * (1 + second_drop)

                if current_price <= second_stop_price:
                    # Price dropped more - exit remaining position
                    return ExitResult(
                        should_exit=True,
                        reason=f'{stop_type}_SCALED_2',
                        sell_fraction=1.0,  # Sell all remaining
                        details={
                            'current_price': current_price,
                            'stop_price': second_stop_price,
                            'first_stop_price': position.first_stop_price,
                            'pnl_pct': pnl_pct * 100,
                            'scaled_stop': 'second',
                        }
                    )
                # Price between first and second stop - hold remaining, might recover
                return ExitResult(should_exit=False)
            else:
                # Scaled stops disabled - full exit
                return ExitResult(
                    should_exit=True,
                    reason=stop_type,
                    sell_fraction=1.0,
                    details={
                        'current_price': current_price,
                        'stop_price': stop_price,
                        'pnl_pct': pnl_pct * 100,
                        'highest_seen': position.highest_price_seen,
                    }
                )

        # Gap protection: catches extreme crashes that gap through normal stops
        # Checked AFTER the stop loss state machine so scaled stops fire first
        gap_threshold = getattr(meme_config, 'GAP_PROTECTION_THRESHOLD', -0.25)
        if pnl_pct <= gap_threshold:
            return ExitResult(
                should_exit=True,
                reason='GAP_PROTECTION',
                sell_fraction=1.0,
                details={
                    'pnl_pct': pnl_pct * 100,
                    'threshold_pct': gap_threshold * 100,
                }
            )

        return ExitResult(should_exit=False)

    def _check_time_exits(
        self,
        position: PositionState,
        current_price: float
    ) -> ExitResult:
        """Check time-based exit conditions including time-based profit taking.

        Args:
            position: Current position state
            current_price: Current token price

        Returns:
            ExitResult if time condition met, otherwise empty result
        """
        now = time.time()
        hold_time = now - position.entry_time
        pnl_pct = (current_price - position.entry_price) / position.entry_price

        # Time-based profit taking: if up X% after Y minutes, take profit
        # This locks in gains on positions that are profitable but not hitting TPs
        time_profit_enabled = getattr(meme_config, 'TIME_PROFIT_ENABLED', True)
        if time_profit_enabled and not position.time_profit_taken and not position.is_moon_bag:
            time_profit_minutes = getattr(meme_config, 'TIME_PROFIT_MINUTES', 10)
            time_profit_threshold = getattr(meme_config, 'TIME_PROFIT_THRESHOLD', 0.05)
            time_profit_sell = getattr(meme_config, 'TIME_PROFIT_SELL_FRACTION', 0.20)

            if hold_time >= time_profit_minutes * 60 and pnl_pct >= time_profit_threshold:
                position.time_profit_taken = True
                return ExitResult(
                    should_exit=True,
                    reason='TIME_PROFIT',
                    sell_fraction=time_profit_sell,
                    details={
                        'hold_time_min': hold_time / 60,
                        'pnl_pct': pnl_pct * 100,
                        'threshold_pct': time_profit_threshold * 100,
                    }
                )

        # Max hold time check (6 hours) - but not for moon bags
        if not position.is_moon_bag and hold_time >= self.max_hold_time:
            pnl_pct = (current_price - position.entry_price) / position.entry_price
            return ExitResult(
                should_exit=True,
                reason='MAX_HOLD_TIME',
                sell_fraction=1.0,
                details={
                    'hold_time_hours': hold_time / 3600,
                    'max_hours': self.max_hold_time / 3600,
                    'pnl_pct': pnl_pct * 100,
                }
            )

        # Stagnation check: if price hasn't moved 5% in 30 minutes (not for moon bags)
        if not position.is_moon_bag and position.last_price_check_time > 0:
            time_since_check = now - position.last_price_check_time
            if time_since_check >= self.stagnation_minutes * 60:
                price_change = abs(current_price - position.last_price_check) / position.last_price_check
                if price_change < self.stagnation_threshold:
                    pnl_pct = (current_price - position.entry_price) / position.entry_price
                    return ExitResult(
                        should_exit=True,
                        reason='STAGNATION',
                        sell_fraction=1.0,
                        details={
                            'price_change_pct': price_change * 100,
                            'threshold_pct': self.stagnation_threshold * 100,
                            'stagnation_minutes': self.stagnation_minutes,
                            'pnl_pct': pnl_pct * 100,
                        }
                    )
                # Reset stagnation check
                position.last_price_check = current_price
                position.last_price_check_time = now
        else:
            # Initialize stagnation tracking
            position.last_price_check = current_price
            position.last_price_check_time = now

        return ExitResult(should_exit=False)

    def _resolve_tp_sell_fraction(
        self,
        position: PositionState,
        stage_idx: int,
        configured_sell_fraction: float,
        moon_bag_enabled: bool,
        moon_bag_fraction: float,
    ) -> tuple[float, float]:
        """Resolve sell fraction for a TP stage.

        Returns:
            Tuple of:
            - sell fraction of CURRENT remaining position (used for execution)
            - sell fraction of ORIGINAL position (used for cumulative tracking)
        """
        remaining_fraction = self._current_remaining_fraction(position)
        if remaining_fraction <= 0.0:
            return 0.0, 0.0

        # Cap cumulative sells so moon bag floor is preserved on profit exits.
        max_cumulative = 1.0
        if moon_bag_enabled:
            max_cumulative = max(0.0, min(1.0, 1.0 - max(0.0, moon_bag_fraction)))

        sold_before = self._calc_cumulative_sold_original(position)
        use_original = getattr(meme_config, 'TP_FRACTIONS_AS_ORIGINAL', True)

        if use_original:
            # Interpret TP fractions as original-position targets.
            target_cumulative = sum(
                max(0.0, float(self.tp_tiers[i][1]))
                for i in range(min(stage_idx + 1, len(self.tp_tiers)))
            )
            target_cumulative = min(max_cumulative, target_cumulative)
            sell_original = max(0.0, target_cumulative - sold_before)
        else:
            # Legacy behavior: fraction of remaining position at the moment TP hits.
            sell_remaining = max(0.0, min(1.0, float(configured_sell_fraction)))
            sell_original = remaining_fraction * sell_remaining
            if moon_bag_enabled:
                max_sell_original = max(0.0, max_cumulative - sold_before)
                sell_original = min(sell_original, max_sell_original)

        sell_remaining = max(0.0, min(1.0, sell_original / remaining_fraction))
        return sell_remaining, sell_original

    def _current_remaining_fraction(self, position: PositionState) -> float:
        """Current fraction of original position still open."""
        try:
            initial_tokens = float(getattr(position, "initial_amount_tokens", 0.0) or 0.0)
            current_tokens = float(getattr(position, "amount_tokens", 0.0) or 0.0)
        except Exception:
            return 1.0
        if initial_tokens <= 0.0:
            return 1.0
        return max(0.0, min(1.0, current_tokens / initial_tokens))

    def _log_tp_stage(
        self,
        position: PositionState,
        tier: str,
        pnl_pct: float,
        sold_fraction: float,
        sold_fraction_original: float | None = None,
    ):
        """Append a TP stage entry to the position's tp_stages_log."""
        if sold_fraction_original is None:
            sold_fraction_original = sold_fraction
        cumulative = self._calc_cumulative_sold_original(position) + sold_fraction_original
        position.tp_stages_log.append({
            'tier': tier,
            'timestamp': time.time(),
            'price': position.entry_price * (1 + pnl_pct),
            'pnl_pct': round(pnl_pct * 100, 2),
            'sold_fraction': sold_fraction,
            'sold_fraction_original': sold_fraction_original,
            'cumulative_sold': round(cumulative, 4),
        })

    def _calc_cumulative_sold_original(self, position: PositionState) -> float:
        """Return cumulative sold fraction against the original position size."""
        cumulative = 0.0
        tier_to_index = {'TP0': 0, 'TP1': 1, 'TP2': 2, 'TP3': 3, 'TP4': 4}
        for stage in position.tp_stages_log:
            sold_original = stage.get('sold_fraction_original')
            if sold_original is None:
                # Backward compatibility for older TP logs that stored only sold_fraction.
                tier = str(stage.get('tier', ''))
                idx = tier_to_index.get(tier)
                if idx is not None and idx < len(self.tp_tiers):
                    sold_original = max(0.0, float(self.tp_tiers[idx][1]))
                else:
                    sold_original = max(0.0, float(stage.get('sold_fraction', 0.0) or 0.0))
            cumulative += max(0.0, float(sold_original or 0.0))
        return max(0.0, min(1.0, cumulative))

    def _calc_cumulative_sold(self, position: PositionState) -> float:
        """Compatibility shim: cumulative sold fraction vs original position."""
        return self._calc_cumulative_sold_original(position)

    def get_tp_stage_summary(self, position: PositionState) -> dict:
        """Return a summary of TP stage progression for a position."""
        stages_hit = len(position.tp_stages_log)
        cumulative_sold = self._calc_cumulative_sold(position)
        return {
            'stages_hit': stages_hit,
            'stages_total': len(self.tp_tiers),
            'cumulative_sold': round(cumulative_sold, 4),
            'is_moon_bag': position.is_moon_bag,
            'stage_details': list(position.tp_stages_log),
        }

    def adjust_tp_tiers_for_momentum(self, momentum_score: float) -> list[tuple]:
        """Widen TP tiers when momentum is strong. Disabled by default.

        When DYNAMIC_TP_ENABLED and momentum_score >= 70, multiplies each tier's
        gain target by DYNAMIC_TP_MOMENTUM_MULTIPLIER. Sell fractions stay the same.

        Args:
            momentum_score: 0-100 momentum indicator

        Returns:
            Adjusted TP tiers list (or original if disabled/below threshold)
        """
        enabled = getattr(meme_config, 'DYNAMIC_TP_ENABLED', False)
        if not enabled or momentum_score < 70:
            return list(self.tp_tiers)

        multiplier = getattr(meme_config, 'DYNAMIC_TP_MOMENTUM_MULTIPLIER', 1.3)
        return [(gain * multiplier, sell) for gain, sell in self.tp_tiers]

    def get_current_stop_price(self, position: PositionState, current_price: float) -> float:
        """Calculate current stop price for display/logging.

        Args:
            position: Current position state
            current_price: Current token price

        Returns:
            Current stop price
        """
        # Moon bags have very wide stop
        if position.is_moon_bag:
            moon_bag_stop = getattr(meme_config, 'MOON_BAG_STOP', -0.80)
            return position.highest_price_seen * (1 + moon_bag_stop)

        pnl_pct = (current_price - position.entry_price) / position.entry_price

        if pnl_pct >= self.trailing_activation or position.trailing_activated:
            # Dynamic trailing (use current gain level for distance calculation)
            dynamic_enabled = getattr(meme_config, 'DYNAMIC_TRAILING_ENABLED', True)
            if dynamic_enabled:
                if pnl_pct >= 0.60:
                    trail_distance = getattr(meme_config, 'TRAILING_DISTANCE_WIDE', -0.18)
                elif pnl_pct >= 0.30:
                    trail_distance = getattr(meme_config, 'TRAILING_DISTANCE_MODERATE', -0.12)
                else:
                    trail_distance = getattr(meme_config, 'TRAILING_DISTANCE_TIGHT', -0.08)
            else:
                trail_distance = self.trailing_distance
            return position.highest_price_seen * (1 + trail_distance)
        elif pnl_pct >= self.breakeven_trigger or position.breakeven_hit:
            # Breakeven locked in
            return position.entry_price
        else:
            # Use position-specific stop (tighter for large positions)
            stop_pct = position.initial_stop_pct if position.initial_stop_pct else self.initial_stop_loss
            return position.entry_price * (1 + stop_pct)

    def get_next_tp_target(self, position: PositionState) -> Optional[tuple]:
        """Get next take profit target.

        Args:
            position: Current position state

        Returns:
            Tuple of (target_price, sell_fraction) or None if all TPs hit
        """
        stage_flags = (
            position.tp0_hit,
            position.tp1_hit,
            position.tp2_hit,
            position.tp3_hit,
            position.tp4_hit,
        )
        for idx, stage_hit in enumerate(stage_flags):
            if idx >= len(self.tp_tiers):
                break
            if not stage_hit:
                target_pct, sell_fraction = self.tp_tiers[idx]
                return (position.entry_price * (1 + target_pct), sell_fraction)
        return None


# Quick test
if __name__ == '__main__':
    manager = MemeExitManager()

    # Create test position
    pos = PositionState(
        mint='TEST123',
        symbol='TEST',
        entry_price=0.001,
        entry_time=time.time() - 3600,  # 1 hour ago
        amount_tokens=1000000,
        amount_usd=25.0,
    )

    # Test various scenarios
    print("Testing exit manager...")

    # Test 1: No exit at entry price
    result = manager.check_exit(pos, 0.001)
    print(f"At entry: should_exit={result.should_exit}")

    # Test 2: TP1 at +100%
    result = manager.check_exit(pos, 0.002)
    print(f"At +100%: should_exit={result.should_exit}, reason={result.reason}")

    # Test 3: Stop loss at -25%
    pos2 = PositionState(
        mint='TEST456',
        symbol='TEST2',
        entry_price=0.001,
        entry_time=time.time(),
        amount_tokens=1000000,
        amount_usd=25.0,
    )
    result = manager.check_exit(pos2, 0.00074)
    print(f"At -26%: should_exit={result.should_exit}, reason={result.reason}")

    print("Exit manager tests complete!")
