#!/usr/bin/env python3
"""Offline replay/backtest for the meme bot using DexScreener snapshots.

Expected input: JSONL with normalized snapshot records from
scripts/meme_snapshot_recorder.py.

This is a deterministic, single-pass replay that:
- Applies meme bot filter + scoring rules (simplified, no network calls)
- Simulates entries and exits using MemeExitManager
- Records trades to CSV for analysis

Usage:
  python scripts/meme_replay.py --input data/meme_snapshots.jsonl
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass
from typing import Optional, Iterable

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import src.meme_config as meme_config
from src.meme_exit_manager import MemeExitManager, PositionState, ExitResult


@dataclass
class Position:
    mint: str
    symbol: str
    entry_price: float
    entry_ts: float
    amount_tokens: float
    amount_usd: float
    amount_sol: float
    state: PositionState
    current_price: float = 0.0
    last_price_ts: float = 0.0


def load_snapshots(path: str) -> list[dict]:
    rows = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    rows.sort(key=lambda r: float(r.get("ts", 0)))
    return rows


def load_regime_windows(path: str) -> list[dict]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def is_hot_regime(ts: float, regimes: list[dict]) -> bool:
    for r in regimes:
        try:
            if r.get("regime") != "hot":
                continue
            start = float(r.get("window_start") or 0)
            end = float(r.get("window_end") or 0)
            if start <= ts < end:
                return True
        except Exception:
            continue
    return False


def estimate_slippage_pct(liquidity_usd: float, volume_1h: float) -> float:
    """Crude slippage model based on liquidity and volume.

    Returns a percent (e.g., 0.8 for 0.8%).
    """
    try:
        liq = max(1.0, float(liquidity_usd or 0))
        vol = max(1.0, float(volume_1h or 0))
        # Higher liquidity and higher volume => lower slippage.
        # Tune the constants later once we compare against live data.
        sl = 1.0 + (15000.0 / liq) + (5000.0 / vol)
        return max(0.2, min(6.0, sl))
    except Exception:
        return 1.5


def estimate_liquidity_accel(curr_liq: float, prev_liq: float | None) -> float:
    """Liquidity acceleration as pct change from previous snapshot."""
    if prev_liq is None or prev_liq <= 0:
        return 0.0
    try:
        return ((curr_liq - prev_liq) / prev_liq) * 100.0
    except Exception:
        return 0.0


def estimate_volume_accel(curr_vol: float, prev_vol: float | None) -> float:
    """Volume acceleration as pct change from previous snapshot."""
    if prev_vol is None or prev_vol <= 0:
        return 0.0
    try:
        return ((curr_vol - prev_vol) / prev_vol) * 100.0
    except Exception:
        return 0.0


def filter_snapshot(
    s: dict,
    cfg: dict | None = None,
    ignore_lp_lock: bool = True,
    ignore_sol_corr: bool = True,
    prev_state: dict | None = None,
) -> bool:
    cfg = cfg or {}
    # Basic required fields
    price = float(s.get("price", 0) or 0)
    liquidity = float(s.get("liquidity", 0) or 0)
    mcap = float(s.get("market_cap", 0) or 0)
    volume_1h = float(s.get("volume_1h", 0) or 0)
    volume_5m = float(s.get("volume_5m", 0) or 0)
    buys_5m = int(s.get("buys_5m", 0) or 0)
    sells_5m = int(s.get("sells_5m", 0) or 0)
    txns_5m = buys_5m + sells_5m

    if price <= 0:
        return False
    min_liq = float(cfg.get("MIN_LIQUIDITY_USD", meme_config.MIN_LIQUIDITY_USD))
    if liquidity < min_liq:
        return False
    min_mcap = float(cfg.get("MIN_MARKET_CAP_USD", meme_config.MIN_MARKET_CAP_USD))
    max_mcap = float(cfg.get("MAX_MARKET_CAP_USD", meme_config.MAX_MARKET_CAP_USD))
    if mcap < min_mcap or mcap > max_mcap:
        return False
    max_mcap_liq_ratio = float(cfg.get("MAX_MCAP_LIQ_RATIO", 0) or 0)
    if max_mcap_liq_ratio > 0 and liquidity > 0:
        if (mcap / liquidity) > max_mcap_liq_ratio:
            return False

    # Spike filter: require liquidity/volume burst vs previous snapshot
    spike_enabled = bool(cfg.get("SPIKE_FILTER_ENABLED", False))
    min_liq_spike = float(cfg.get("MIN_LIQ_SPIKE_USD", 0) or 0)
    min_vol_spike = float(cfg.get("MIN_VOL_SPIKE_5M", 0) or 0)
    if prev_state and (spike_enabled or min_liq_spike > 0 or min_vol_spike > 0):
        prev_liq = float(prev_state.get("liquidity", 0) or 0)
        prev_vol5 = float(prev_state.get("volume_5m", 0) or 0)
        dliq = liquidity - prev_liq
        dvol5 = volume_5m - prev_vol5
        if min_liq_spike > 0 and dliq < min_liq_spike:
            return False
        if min_vol_spike > 0 and dvol5 < min_vol_spike:
            return False

    # Token age
    discovered_at = float(s.get("discovered_at", 0) or 0)
    age_seconds = max(0.0, float(s.get("ts", 0) or 0) - discovered_at)
    if age_seconds > meme_config.MAX_TOKEN_AGE_SECONDS:
        return False

    # Price momentum filters
    price_change_5m = float(s.get("price_change_5m", 0) or 0)
    min_5m = float(cfg.get("MIN_PRICE_CHANGE_5M", meme_config.MIN_PRICE_CHANGE_5M))
    if price_change_5m < min_5m:
        return False
    max_5m_1h_gap = float(cfg.get("MAX_5M_1H_GAP", 0) or 0)
    if max_5m_1h_gap > 0:
        price_change_1h = float(s.get("price_change_1h", 0) or 0)
        if (price_change_5m - price_change_1h) > max_5m_1h_gap:
            return False

    # Buy/sell ratio
    buys_1h = int(s.get("buys_1h", 0) or 0)
    sells_1h = int(s.get("sells_1h", 0) or 0)
    if sells_1h > 0:
        if (buys_1h / sells_1h) < meme_config.MIN_BUY_SELL_RATIO:
            return False

    # Min activity
    txns_1h = int(s.get("txns_1h", 0) or 0)
    if txns_1h < meme_config.MIN_TXNS_1H:
        return False
    min_txns_5m = int(cfg.get("MIN_TXNS_5M", 0) or 0)
    if min_txns_5m > 0 and txns_5m < min_txns_5m:
        return False
    min_buys_5m = int(cfg.get("MIN_BUYS_5M", 0) or 0)
    if min_buys_5m > 0 and buys_5m < min_buys_5m:
        return False
    min_buy_sell_5m = float(cfg.get("MIN_BUY_SELL_RATIO_5M", 0) or 0)
    if min_buy_sell_5m > 0 and sells_5m > 0:
        if (buys_5m / sells_5m) < min_buy_sell_5m:
            return False
    min_vol_1h = float(cfg.get("MIN_VOLUME_1H", 0) or 0)
    if min_vol_1h > 0 and volume_1h < min_vol_1h:
        return False
    min_vol_5m = float(cfg.get("MIN_VOLUME_5M", 0) or 0)
    if min_vol_5m > 0 and volume_5m < min_vol_5m:
        return False
    min_vol5m_share = float(cfg.get("MIN_VOL5M_SHARE", 0) or 0)
    if min_vol5m_share > 0 and volume_1h > 0:
        if (volume_5m / volume_1h) < min_vol5m_share:
            return False

    # Pullback entry: avoid extreme 5m pumps
    if getattr(meme_config, "PULLBACK_ENTRY_ENABLED", True):
        max_5m_pump = float(cfg.get("MAX_5M_PUMP", getattr(meme_config, "MAX_5M_PUMP", 30.0)))
        if price_change_5m > max_5m_pump:
            return False

    # LP lock and SOL correlation are skipped in replay by default
    if not ignore_lp_lock:
        # Simple safety using Birdeye security fields if present
        mint_auth = s.get("mint_authority")
        freeze_auth = s.get("freeze_authority")
        freezeable = s.get("freezeable")

        # Reject if mint authority or freeze authority exists
        if mint_auth:
            return False
        if freeze_auth or freezeable:
            return False
        # Top10 holder concentration check (optional)
        if cfg.get("USE_TOP10_CHECK"):
            top10_holder = s.get("top10_holder_pct")
            if top10_holder is not None:
                try:
                    if float(top10_holder) > float(cfg.get("MAX_TOP10_HOLDER_PCT", 0.65)):
                        return False
                except Exception:
                    pass
    if not ignore_sol_corr:
        pass

    # Optional liquidity/volume acceleration filters
    if prev_state:
        min_liq_accel = float(cfg.get("MIN_LIQ_ACCEL_PCT", 0.0))
        min_vol_accel = float(cfg.get("MIN_VOL_ACCEL_PCT", 0.0))

        liq_accel = estimate_liquidity_accel(liquidity, prev_state.get("liquidity"))
        vol_accel = estimate_volume_accel(float(s.get("volume_1h", 0) or 0), prev_state.get("volume_1h"))

        if min_liq_accel > 0 and liq_accel < min_liq_accel:
            return False
        if min_vol_accel > 0 and vol_accel < min_vol_accel:
            return False

    # Optional momentum confirmation (require consecutive positive snapshots)
    confirm_n = int(cfg.get("CONFIRM_N", 0) or 0)
    if confirm_n > 1 and prev_state:
        pos_count = int(prev_state.get("pos_count", 0))
        price_change_5m = float(s.get("price_change_5m", 0) or 0)
        if price_change_5m > 0:
            pos_count += 1
        else:
            pos_count = 0
        prev_state["pos_count"] = pos_count
        if pos_count < confirm_n:
            return False

    # Momentum alignment filter (avoid 5m-only spikes)
    min_1h = float(cfg.get("MIN_PRICE_CHANGE_1H", -999))
    if min_1h > -999:
        price_change_1h = float(s.get("price_change_1h", 0) or 0)
        if price_change_1h < min_1h:
            return False

    # Post-boost decay filter: if token was discovered long ago and is fading, skip
    max_age_seconds = float(cfg.get("MAX_TOKEN_AGE_SECONDS_OVERRIDE", 0) or 0)
    if max_age_seconds > 0:
        discovered_at = float(s.get("discovered_at", 0) or 0)
        age_seconds = max(0.0, float(s.get("ts", 0) or 0) - discovered_at)
        if age_seconds > max_age_seconds:
            return False
    max_boost_age_seconds = float(cfg.get("MAX_BOOST_AGE_SECONDS", 0) or 0)
    if max_boost_age_seconds > 0 and str(s.get("source", "")).lower() == "boosts":
        discovered_at = float(s.get("discovered_at", 0) or 0)
        age_seconds = max(0.0, float(s.get("ts", 0) or 0) - discovered_at)
        if age_seconds > max_boost_age_seconds:
            return False

    return True


def score_snapshot(s: dict) -> int:
    price_change_5m = float(s.get("price_change_5m", 0) or 0)
    price_change_1h = float(s.get("price_change_1h", 0) or 0)
    txns_1h = int(s.get("txns_1h", 0) or 0)
    buys_1h = int(s.get("buys_1h", 0) or 0)
    sells_1h = int(s.get("sells_1h", 0) or 0)
    buys_5m = int(s.get("buys_5m", 0) or 0)
    sells_5m = int(s.get("sells_5m", 0) or 0)
    txns_5m = buys_5m + sells_5m
    liquidity = float(s.get("liquidity", 0) or 0)
    volume_1h = float(s.get("volume_1h", 0) or 0)
    volume_5m = float(s.get("volume_5m", 0) or 0)

    # Momentum score (0-40)
    momentum_score = 0
    if price_change_5m >= 10:
        momentum_score += 20
    elif price_change_5m >= 0:
        momentum_score += 10 + price_change_5m
    elif price_change_5m >= -5:
        momentum_score += 10 + (price_change_5m * 2)

    if price_change_1h >= 50:
        momentum_score += 20
    elif price_change_1h >= 0:
        momentum_score += 5 + min(15, price_change_1h * 0.3)

    # Volume score (0-30)
    volume_score = 0
    if txns_1h >= 200:
        volume_score += 15
    elif txns_1h >= 100:
        volume_score += 10
    elif txns_1h >= 50:
        volume_score += 5

    if sells_1h > 0:
        bs_ratio = buys_1h / sells_1h
        if bs_ratio >= 2.0:
            volume_score += 15
        elif bs_ratio >= 1.5:
            volume_score += 10
        elif bs_ratio >= 1.0:
            volume_score += 5
    if sells_5m > 0:
        bs_ratio_5m = buys_5m / sells_5m
        if bs_ratio_5m >= 2.0:
            volume_score += 8
        elif bs_ratio_5m >= 1.3:
            volume_score += 5
        elif bs_ratio_5m >= 1.0:
            volume_score += 3
    if txns_5m >= 30:
        volume_score += 5
    elif txns_5m >= 15:
        volume_score += 3

    if volume_1h >= 250000:
        volume_score += 6
    elif volume_1h >= 100000:
        volume_score += 4
    elif volume_1h >= 50000:
        volume_score += 2
    if volume_5m >= 5000:
        volume_score += 2

    # Liquidity score (0-20)
    liquidity_score = 0
    if liquidity >= 100000:
        liquidity_score += 20
    elif liquidity >= 50000:
        liquidity_score += 15
    elif liquidity >= 20000:
        liquidity_score += 10
    elif liquidity >= 10000:
        liquidity_score += 5

    # Social score (unused in snapshots)
    social_score = 0

    divergence_penalty = 0
    if price_change_5m > 10 and price_change_1h < 3:
        divergence_penalty = 15

    composite = momentum_score + volume_score + liquidity_score + social_score - divergence_penalty
    return int(min(100, max(0, composite)))


def iter_variants(base: dict, variant_overrides: Optional[list[dict]]) -> Iterable[dict]:
    if not variant_overrides:
        yield {"name": "baseline", "config": base}
        return
    for idx, ov in enumerate(variant_overrides, 1):
        cfg = dict(base)
        cfg.update(ov)
        name = ov.get("name") or f"variant_{idx}"
        yield {"name": name, "config": cfg}


def write_trade(csv_writer, trade: dict):
    csv_writer.writerow(trade)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="JSONL snapshot file")
    parser.add_argument("--out", default="data/meme_replay_trades.csv", help="Output CSV (baseline)")
    parser.add_argument("--sol-price", type=float, default=float(os.getenv("MEME_SOL_PRICE", "100")), help="SOL price for sizing")
    parser.add_argument("--use-lp-lock", action="store_true", help="Enable LP lock checks (disabled by default)")
    parser.add_argument("--use-top10", action="store_true", help="Enable top10 holder concentration check")
    parser.add_argument("--use-sol-corr", action="store_true", help="Enable SOL correlation checks (disabled by default)")
    parser.add_argument("--fee-usd", type=float, default=0.15, help="Flat fee per trade (USD)")
    parser.add_argument("--slippage-mult", type=float, default=1.0, help="Multiply slippage model (e.g., 0.5 = half)")
    parser.add_argument("--variants", type=str, default="", help="JSON list of config overrides for A/B testing")
    parser.add_argument("--variants-file", type=str, default="", help="Path to JSON list of config overrides")
    parser.add_argument("--config-file", type=str, default="", help="Optional base config JSON to apply")
    parser.add_argument("--regime-file", type=str, default="", help="Optional regime tags JSON (hot/cold)")
    parser.add_argument("--hot-only", action="store_true", help="Only trade during hot regimes")
    parser.add_argument("--scan-interval", type=int, default=60, help="Seconds between full position scans")
    args = parser.parse_args()

    snapshots = load_snapshots(args.input)
    regimes = load_regime_windows(args.regime_file) if args.regime_file else []
    if not snapshots:
        print("No snapshots loaded.")
        return

    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    config_overrides = {}
    if args.config_file:
        try:
            with open(args.config_file, "r", encoding="utf-8") as fh:
                cfg_obj = json.load(fh)
            config_overrides = cfg_obj.get("parameters", cfg_obj) if isinstance(cfg_obj, dict) else {}
            if isinstance(config_overrides, dict):
                for k, v in config_overrides.items():
                    try:
                        setattr(meme_config, k, v)
                    except Exception:
                        pass
        except Exception:
            config_overrides = {}

    base_cfg = {
        "MIN_VHI_SCORE": meme_config.MIN_VHI_SCORE,
        "MAX_POSITIONS": meme_config.MAX_POSITIONS,
        "MIN_MARKET_CAP_USD": meme_config.MIN_MARKET_CAP_USD,
        "MAX_MARKET_CAP_USD": meme_config.MAX_MARKET_CAP_USD,
        "MIN_PRICE_CHANGE_5M": meme_config.MIN_PRICE_CHANGE_5M,
        "MAX_5M_PUMP": getattr(meme_config, "MAX_5M_PUMP", 30.0),
        "MIN_LIQUIDITY_USD": meme_config.MIN_LIQUIDITY_USD,
        "MAX_TOP10_HOLDER_PCT": 0.65,
        "MIN_LIQ_ACCEL_PCT": 0.0,
        "MIN_VOL_ACCEL_PCT": 0.0,
        "MIN_PRICE_CHANGE_1H": -999,
        "MAX_5M_1H_GAP": 0.0,
        "MIN_TXNS_5M": 0,
        "MIN_BUYS_5M": 0,
        "MIN_BUY_SELL_RATIO_5M": 0.0,
        "MIN_VOLUME_1H": 0.0,
        "MIN_VOLUME_5M": 0.0,
        "MIN_VOL5M_SHARE": 0.0,
        "MAX_MCAP_LIQ_RATIO": 0.0,
        "MAX_TOKEN_AGE_SECONDS_OVERRIDE": 0.0,
        "MAX_BOOST_AGE_SECONDS": 0.0,
        "SCORE_DECAY_PER_HOUR": 0.0,
        "USE_TOP10_CHECK": False,
        "ENTRY_COOLDOWN_SECONDS": 0,
        "CONFIRM_N": 0,
        "SPIKE_FILTER_ENABLED": getattr(meme_config, "SPIKE_FILTER_ENABLED", False),
        "MIN_LIQ_SPIKE_USD": getattr(meme_config, "MIN_LIQ_SPIKE_USD", 0.0),
        "MIN_VOL_SPIKE_5M": getattr(meme_config, "MIN_VOL_SPIKE_5M", 0.0),
    }
    if isinstance(config_overrides, dict):
        base_cfg.update(config_overrides)
    variants = []
    if args.variants_file:
        try:
            with open(args.variants_file, "r", encoding="utf-8") as fh:
                variants = json.load(fh)
        except Exception:
            variants = []
    elif args.variants:
        try:
            variants = json.loads(args.variants)
        except Exception:
            variants = []

    for variant in iter_variants(base_cfg, variants):
        name = variant["name"]
        cfg = variant["config"]

        # Allow exit parameter overrides (TP tiers and trailing)
        tp_override = cfg.get("TP_TIERS")
        trail_tight = cfg.get("TRAILING_DISTANCE_TIGHT")
        trail_mod = cfg.get("TRAILING_DISTANCE_MODERATE")
        trail_wide = cfg.get("TRAILING_DISTANCE_WIDE")
        if tp_override:
            meme_config.TP_TIERS = tp_override
        if trail_tight is not None:
            meme_config.TRAILING_DISTANCE_TIGHT = trail_tight
        if trail_mod is not None:
            meme_config.TRAILING_DISTANCE_MODERATE = trail_mod
        if trail_wide is not None:
            meme_config.TRAILING_DISTANCE_WIDE = trail_wide

        exit_manager = MemeExitManager()
        prev_by_mint: dict[str, dict] = {}
        positions: dict[str, Position] = {}
        last_exit_by_mint: dict[str, float] = {}
        stats = {"trades": 0, "wins": 0, "losses": 0, "pnl_usd": 0.0}
        last_scan_ts = 0.0

        out_path = args.out
        if name != "baseline":
            root, ext = os.path.splitext(args.out)
            out_path = f"{root}.{name}{ext or '.csv'}"

        with open(out_path, "w", newline="", encoding="utf-8") as fh:
            fieldnames = [
                "mint", "symbol", "entry_ts", "exit_ts", "entry_price", "exit_price",
                "amount_usd", "pnl_usd", "pnl_pct", "exit_reason"
            ]
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()

            for s in snapshots:
                mint = s.get("mint")
                if not mint:
                    continue
                ts = float(s.get("ts", 0) or 0)
                if args.hot_only and regimes and not is_hot_regime(ts, regimes):
                    continue

                # Update existing position (exit checks)
                if mint in positions:
                    pos = positions[mint]
                    current_price = float(s.get("price", 0) or 0)
                    if current_price > 0:
                        pos.current_price = current_price
                        pos.last_price_ts = ts
                        exit_result = exit_manager.check_exit(pos.state, current_price)
                        if exit_result.should_exit:
                            sell_fraction = exit_result.sell_fraction
                            # Apply slippage on exit (worse price)
                            slip_pct = estimate_slippage_pct(float(s.get("liquidity", 0) or 0), float(s.get("volume_1h", 0) or 0)) * args.slippage_mult
                            exit_price = current_price * (1 - slip_pct / 100.0)
                            exit_amount_usd = pos.amount_usd * sell_fraction
                            pnl_usd = (exit_price - pos.entry_price) * (pos.amount_tokens * sell_fraction)
                            pnl_usd -= args.fee_usd  # fee model
                            pnl_pct = (pnl_usd / exit_amount_usd * 100) if exit_amount_usd > 0 else 0.0

                            stats["trades"] += 1
                            stats["pnl_usd"] += pnl_usd
                            if pnl_usd > 0:
                                stats["wins"] += 1
                            else:
                                stats["losses"] += 1

                            write_trade(writer, {
                                "mint": mint,
                                "symbol": pos.symbol,
                                "entry_ts": pos.entry_ts,
                                "exit_ts": s.get("ts"),
                                "entry_price": pos.entry_price,
                                "exit_price": exit_price,
                                "amount_usd": exit_amount_usd,
                                "pnl_usd": round(pnl_usd, 4),
                                "pnl_pct": round(pnl_pct, 2),
                                "exit_reason": exit_result.reason,
                            })

                            # Reduce or remove position
                            pos.amount_tokens *= (1 - sell_fraction)
                            pos.amount_usd *= (1 - sell_fraction)
                            if pos.amount_tokens <= 0 or sell_fraction >= 1.0:
                                del positions[mint]
                                last_exit_by_mint[mint] = ts
                    continue

                # Entry logic
                if len(positions) >= int(cfg.get("MAX_POSITIONS", meme_config.MAX_POSITIONS)):
                    continue
                cooldown = float(cfg.get("ENTRY_COOLDOWN_SECONDS", 0) or 0)
                if cooldown > 0:
                    last_exit = last_exit_by_mint.get(mint)
                    if last_exit and (ts - last_exit) < cooldown:
                        continue

                prev_state = prev_by_mint.get(mint)
                if not filter_snapshot(
                    s,
                    cfg=cfg,
                    ignore_lp_lock=not args.use_lp_lock,
                    ignore_sol_corr=not args.use_sol_corr,
                    prev_state=prev_state,
                ):
                    # keep prev state updated for next observation
                    prev_by_mint[mint] = {
                        "liquidity": float(s.get("liquidity", 0) or 0),
                        "volume_1h": float(s.get("volume_1h", 0) or 0),
                        "volume_5m": float(s.get("volume_5m", 0) or 0),
                        "pos_count": prev_by_mint.get(mint, {}).get("pos_count", 0),
                    }
                    continue

                score = score_snapshot(s)
                decay_per_hour = float(cfg.get("SCORE_DECAY_PER_HOUR", 0.0))
                if decay_per_hour > 0:
                    discovered_at = float(s.get("discovered_at", 0) or 0)
                    age_hours = max(0.0, (ts - discovered_at) / 3600.0)
                    score = max(0, int(score - (decay_per_hour * age_hours)))
                min_score = int(cfg.get("MIN_VHI_SCORE", meme_config.MIN_VHI_SCORE))
                if score < min_score:
                    continue

                price = float(s.get("price", 0) or 0)
                if price <= 0:
                    continue

                size_sol = meme_config.get_position_size_for_score(score)
                size_usd = size_sol * args.sol_price

                # Apply slippage to entry price (worse fill)
                slip_pct = estimate_slippage_pct(float(s.get("liquidity", 0) or 0), float(s.get("volume_1h", 0) or 0)) * args.slippage_mult
                entry_price = price * (1 + slip_pct / 100.0)
                amount_tokens = size_usd / entry_price if entry_price > 0 else 0

                state = PositionState(
                    mint=mint,
                    symbol=s.get("symbol", ""),
                    entry_price=entry_price,
                    entry_time=ts,
                    amount_tokens=amount_tokens,
                    amount_usd=size_usd,
                    score=score,
                    initial_stop_pct=meme_config.get_stop_loss_for_score(score),
                )

                positions[mint] = Position(
                    mint=mint,
                    symbol=s.get("symbol", ""),
                    entry_price=entry_price,
                    entry_ts=ts,
                    amount_tokens=amount_tokens,
                    amount_usd=size_usd,
                    amount_sol=size_sol,
                    state=state,
                    current_price=price,
                    last_price_ts=ts,
                )

                # update prev state
                prev_by_mint[mint] = {
                    "liquidity": float(s.get("liquidity", 0) or 0),
                    "volume_1h": float(s.get("volume_1h", 0) or 0),
                    "volume_5m": float(s.get("volume_5m", 0) or 0),
                    "pos_count": prev_by_mint.get(mint, {}).get("pos_count", 0),
                }

                # Periodic full scan (time-based exits even if token isn't updating)
                if ts - last_scan_ts >= float(args.scan_interval):
                    last_scan_ts = ts
                    for pmint, p in list(positions.items()):
                        if p.current_price <= 0:
                            continue
                        exit_result = exit_manager.check_exit(p.state, p.current_price)
                        if exit_result.should_exit:
                            sell_fraction = exit_result.sell_fraction
                            exit_price = p.current_price
                            exit_amount_usd = p.amount_usd * sell_fraction
                            pnl_usd = (exit_price - p.entry_price) * (p.amount_tokens * sell_fraction)
                            pnl_usd -= args.fee_usd
                            pnl_pct = (pnl_usd / exit_amount_usd * 100) if exit_amount_usd > 0 else 0.0

                            stats["trades"] += 1
                            stats["pnl_usd"] += pnl_usd
                            if pnl_usd > 0:
                                stats["wins"] += 1
                            else:
                                stats["losses"] += 1

                            write_trade(writer, {
                                "mint": pmint,
                                "symbol": p.symbol,
                                "entry_ts": p.entry_ts,
                                "exit_ts": ts,
                                "entry_price": p.entry_price,
                                "exit_price": exit_price,
                                "amount_usd": exit_amount_usd,
                                "pnl_usd": round(pnl_usd, 4),
                                "pnl_pct": round(pnl_pct, 2),
                                "exit_reason": exit_result.reason,
                            })

                            p.amount_tokens *= (1 - sell_fraction)
                            p.amount_usd *= (1 - sell_fraction)
                            if p.amount_tokens <= 0 or sell_fraction >= 1.0:
                                del positions[pmint]
                                last_exit_by_mint[pmint] = ts

        win_rate = (stats["wins"] / stats["trades"] * 100) if stats["trades"] else 0.0
        print(f"Replay complete ({name}).")
        print(f"Trades: {stats['trades']} | Wins: {stats['wins']} | Losses: {stats['losses']} | Win Rate: {win_rate:.2f}%")
        print(f"Net PnL (USD): {stats['pnl_usd']:.2f}")


if __name__ == "__main__":
    main()
