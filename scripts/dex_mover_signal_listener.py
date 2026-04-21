#!/usr/bin/env python3
"""Emit post-launch "mover" signals from DexScreener into launch_signals.

Why this exists:
- The launch-only WS stream can miss runners that are not emitted there.
- This listener adds a parallel discovery lane for coins already moving.

Output format matches other signal emitters:
- Writes JSONL rows to MEME_LAUNCH_SIGNALS_FILE (default data/meme_launch_signals.jsonl)
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

import httpx
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
load_dotenv(dotenv_path=os.path.join(PROJECT_ROOT, ".env"), override=False)

from src.meme_signal_schema import build_launch_signal_payload, normalize_signal_metrics

DATA_DIR = os.path.join(PROJECT_ROOT, "data")

DEX_BASE = os.getenv("DEXSCREENER_BASE", "https://api.dexscreener.com").rstrip("/")
DEX_BOOSTS = f"{DEX_BASE}/token-boosts/latest/v1"
DEX_PROFILES = f"{DEX_BASE}/token-profiles/latest/v1"
DEX_TOKEN = f"{DEX_BASE}/latest/dex/tokens"

SIGNALS_OUT = (os.getenv("MEME_LAUNCH_SIGNALS_FILE") or "").strip() or os.path.join(DATA_DIR, "meme_launch_signals.jsonl")
STATE_PATH = os.getenv("DEX_MOVER_STATE_PATH", os.path.join(DATA_DIR, "dex_mover_state.json"))

POLL_S = float(os.getenv("MEME_DEX_MOVER_POLL_S", "8") or 8)
MAX_SEEDS = int(os.getenv("MEME_DEX_MOVER_MAX_SEEDS", "120") or 120)
MAX_DETAILS_PER_POLL = int(os.getenv("MEME_DEX_MOVER_MAX_DETAILS_PER_POLL", "18") or 18)
RECHECK_REJECT_S = float(os.getenv("MEME_DEX_MOVER_RECHECK_REJECT_S", "300") or 300)
RECHECK_FETCH_FAIL_S = float(os.getenv("MEME_DEX_MOVER_RECHECK_FETCH_FAIL_S", "120") or 120)
SEED_TTL_S = float(os.getenv("MEME_DEX_MOVER_SEED_TTL_S", "7200") or 7200)
EMIT_COOLDOWN_S = float(os.getenv("MEME_DEX_MOVER_EMIT_COOLDOWN_S", "600") or 600)

MIN_MCAP_USD = float(os.getenv("MEME_DEX_MOVER_MIN_MCAP_USD", "20000") or 20000)
MAX_MCAP_USD = float(os.getenv("MEME_DEX_MOVER_MAX_MCAP_USD", "2000000") or 2000000)
MAX_PAIR_AGE_MIN = float(os.getenv("MEME_DEX_MOVER_MAX_PAIR_AGE_MIN", "480") or 480)
MIN_BUYS_5M = int(os.getenv("MEME_DEX_MOVER_MIN_BUYS_5M", "6") or 6)
MIN_TXNS_5M = int(os.getenv("MEME_DEX_MOVER_MIN_TXNS_5M", "10") or 10)
MIN_VOL_5M_USD = float(os.getenv("MEME_DEX_MOVER_MIN_VOL_5M_USD", "2500") or 2500)
MIN_BUY_SELL_RATIO = float(os.getenv("MEME_DEX_MOVER_MIN_BUY_SELL_RATIO", "1.10") or 1.10)
MIN_NET_SOL_IN = float(os.getenv("MEME_DEX_MOVER_MIN_NET_SOL_IN", "0.60") or 0.60)
MIN_SCORE = float(os.getenv("MEME_DEX_MOVER_MIN_SCORE", "45") or 45)
MIN_MOM_5M = float(os.getenv("MEME_DEX_MOVER_MIN_MOM_5M", "-5.0") or -5.0)
SOL_USD_FALLBACK = float(os.getenv("MEME_SOL_USD_FALLBACK", "180") or 180)
REQUIRE_PATTERN = str(os.getenv("MEME_DEX_MOVER_REQUIRE_PATTERN", "true") or "true").lower() in ("1", "true", "yes")
PATTERN_MODE = str(os.getenv("MEME_DEX_MOVER_PATTERN_MODE", "breakout_or_retest") or "breakout_or_retest").strip().lower()
BREAKOUT_MIN_MOM_5M = float(os.getenv("MEME_DEX_MOVER_BREAKOUT_MIN_MOM_5M", "4.0") or 4.0)
BREAKOUT_MIN_VOL5M_SHARE = float(os.getenv("MEME_DEX_MOVER_BREAKOUT_MIN_VOL5M_SHARE", "0.06") or 0.06)
BREAKOUT_MIN_BUYS_5M = int(os.getenv("MEME_DEX_MOVER_BREAKOUT_MIN_BUYS_5M", "8") or 8)
RETEST_MIN_MOM_1H = float(os.getenv("MEME_DEX_MOVER_RETEST_MIN_MOM_1H", "12.0") or 12.0)
RETEST_MAX_PULLBACK_5M = float(os.getenv("MEME_DEX_MOVER_RETEST_MAX_PULLBACK_5M", "-4.0") or -4.0)
RETEST_MIN_BUY_SELL_RATIO = float(os.getenv("MEME_DEX_MOVER_RETEST_MIN_BUY_SELL_RATIO", "1.10") or 1.10)
RETEST_MIN_NET_SOL_IN = float(os.getenv("MEME_DEX_MOVER_RETEST_MIN_NET_SOL_IN", "0.80") or 0.80)
MAX_MOM_1H_CHASE = float(os.getenv("MEME_DEX_MOVER_MAX_MOM_1H_CHASE", "220") or 220)

USE_BOOSTS = str(os.getenv("MEME_DEX_MOVER_USE_BOOSTS", "true") or "true").lower() in ("1", "true", "yes")
USE_PROFILES = str(os.getenv("MEME_DEX_MOVER_USE_PROFILES", "true") or "true").lower() in ("1", "true", "yes")


def _load_state() -> dict[str, Any]:
    out: dict[str, Any] = {"last_emit": {}, "emitted": [], "next_check": {}, "last_seen_seed": {}}
    if not os.path.exists(STATE_PATH):
        return out
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as fh:
            raw = json.load(fh) or {}
        if isinstance(raw, dict):
            if isinstance(raw.get("emitted"), list):
                out["emitted"] = raw["emitted"]
            if isinstance(raw.get("last_emit"), dict):
                out["last_emit"] = raw["last_emit"]
            if isinstance(raw.get("next_check"), dict):
                out["next_check"] = raw["next_check"]
            if isinstance(raw.get("last_seen_seed"), dict):
                out["last_seen_seed"] = raw["last_seen_seed"]
    except Exception:
        pass
    return out


def _save_state(last_emit: dict[str, float], next_check: dict[str, float], last_seen_seed: dict[str, float]) -> None:
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        payload = {
            "last_emit": {k: float(v) for k, v in list(last_emit.items())[-50000:]},
            "next_check": {k: float(v) for k, v in list(next_check.items())[-50000:]},
            "last_seen_seed": {k: float(v) for k, v in list(last_seen_seed.items())[-50000:]},
        }
        with open(STATE_PATH, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
    except Exception:
        pass


def _seed_mints(client: httpx.Client) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def _add(mint: Any) -> None:
        if not isinstance(mint, str) or not mint:
            return
        if mint in seen:
            return
        seen.add(mint)
        out.append(mint)

    if USE_BOOSTS:
        try:
            r = client.get(DEX_BOOSTS, timeout=12.0)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    for row in data:
                        if not isinstance(row, dict):
                            continue
                        if row.get("chainId") != "solana":
                            continue
                        _add(row.get("tokenAddress"))
                        if len(out) >= MAX_SEEDS:
                            return out
        except Exception:
            pass

    if USE_PROFILES:
        try:
            r = client.get(DEX_PROFILES, timeout=12.0)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    for row in data:
                        if not isinstance(row, dict):
                            continue
                        if row.get("chainId") != "solana":
                            continue
                        _add(row.get("tokenAddress"))
                        if len(out) >= MAX_SEEDS:
                            return out
        except Exception:
            pass

    return out


def _pick_best_pair(detail: dict[str, Any]) -> dict[str, Any] | None:
    pairs = detail.get("pairs") or []
    if not isinstance(pairs, list) or not pairs:
        return None
    sol_pairs = []
    fallback = []
    for p in pairs:
        if not isinstance(p, dict):
            continue
        if p.get("chainId") != "solana":
            continue
        quote = (p.get("quoteToken") or {}).get("symbol")
        if isinstance(quote, str) and quote.upper() in ("SOL", "WSOL"):
            sol_pairs.append(p)
        else:
            fallback.append(p)
    pool = sol_pairs if sol_pairs else fallback
    if not pool:
        return None
    return max(
        pool,
        key=lambda p: float(((p.get("volume") or {}).get("m5")) or 0.0)
        + float(((p.get("liquidity") or {}).get("usd")) or 0.0) * 0.01,
    )


def _to_float(v: Any) -> float:
    try:
        return float(v or 0.0)
    except Exception:
        return 0.0


def _to_int(v: Any) -> int:
    try:
        return int(v or 0)
    except Exception:
        return 0


def _pair_metrics(pair: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    txns = pair.get("txns") or {}
    m5 = txns.get("m5") or {}
    buys_5m = _to_int(m5.get("buys"))
    sells_5m = _to_int(m5.get("sells"))
    txns_5m = buys_5m + sells_5m
    volume = pair.get("volume") or {}
    vol_5m = _to_float(volume.get("m5"))
    vol_1h = _to_float(volume.get("h1"))
    vol_5m_share = (vol_5m / vol_1h) if vol_1h > 0 else 0.0
    mcap = _to_float(pair.get("marketCap")) or _to_float(pair.get("fdv"))
    liq = _to_float((pair.get("liquidity") or {}).get("usd"))
    mom_5m = _to_float((pair.get("priceChange") or {}).get("m5"))
    mom_1h = _to_float((pair.get("priceChange") or {}).get("h1"))
    created_ms = _to_float(pair.get("pairCreatedAt"))
    age_min = ((now * 1000.0 - created_ms) / 60000.0) if created_ms > 0 else None

    bs_ratio = (float(buys_5m) / float(max(1, sells_5m))) if sells_5m > 0 else float(buys_5m)
    net_usd = 0.0
    if txns_5m > 0:
        net_usd = vol_5m * ((float(buys_5m) - float(sells_5m)) / float(txns_5m))
    net_sol_in = max(0.0, net_usd / max(1.0, SOL_USD_FALLBACK))

    # We do not have wallet-level unique buyers from DexScreener API.
    # Keep a rough activity proxy but mark it as estimated so downstream gates can
    # avoid treating it as ground truth.
    unique_buyers_proxy = max(0, min(64, buys_5m))

    # Structure classification:
    # - breakout: immediate expansion in both price and flow.
    # - retest_hold: positive 1h trend with only shallow 5m pullback while flow stays net-positive.
    pattern = "none"
    if (
        mom_5m >= BREAKOUT_MIN_MOM_5M
        and vol_5m_share >= BREAKOUT_MIN_VOL5M_SHARE
        and buys_5m >= BREAKOUT_MIN_BUYS_5M
        and bs_ratio >= MIN_BUY_SELL_RATIO
    ):
        pattern = "breakout"
    elif (
        mom_1h >= RETEST_MIN_MOM_1H
        and mom_5m >= RETEST_MAX_PULLBACK_5M
        and bs_ratio >= RETEST_MIN_BUY_SELL_RATIO
        and net_sol_in >= RETEST_MIN_NET_SOL_IN
    ):
        pattern = "retest_hold"

    score = 0.0
    score += min(25.0, 25.0 * (txns_5m / 24.0))
    score += min(20.0, 20.0 * (buys_5m / 12.0))
    score += min(20.0, 20.0 * (vol_5m / 15000.0))
    score += min(20.0, 20.0 * (net_sol_in / 3.0))
    score += min(15.0, max(0.0, (mom_5m + 5.0) / 20.0) * 15.0)
    if pattern == "breakout":
        score += 6.0
    elif pattern == "retest_hold":
        score += 4.0
    score = max(0.0, min(100.0, score))

    return {
        "source": "dex_mover",
        "pair_address": pair.get("pairAddress"),
        "dex_id": pair.get("dexId"),
        "url": pair.get("url"),
        "symbol": ((pair.get("baseToken") or {}).get("symbol") or ""),
        "name": ((pair.get("baseToken") or {}).get("name") or ""),
        "price": _to_float(pair.get("priceUsd")),
        "liquidity": liq,
        "market_cap": mcap,
        "hits": txns_5m,
        "buys": buys_5m,
        "sells": sells_5m,
        "unique_buyers": unique_buyers_proxy,
        "unique_buyers_estimated": True,
        "net_sol_in": net_sol_in,
        "top_buyer_share": None,
        "top_buyer_share_estimated": True,
        "volume_5m": vol_5m,
        "volume_1h": vol_1h,
        "volume_5m_share": vol_5m_share,
        "price_change_5m": mom_5m,
        "price_change_1h": mom_1h,
        "buy_sell_ratio": bs_ratio,
        "mover_pattern": pattern,
        "pair_age_min": age_min,
        "score": score,
    }


def _passes(m: dict[str, Any]) -> bool:
    mcap = _to_float(m.get("market_cap"))
    if mcap < MIN_MCAP_USD:
        return False
    if MAX_MCAP_USD > 0 and mcap > MAX_MCAP_USD:
        return False
    age_min = m.get("pair_age_min")
    if isinstance(age_min, (int, float)) and MAX_PAIR_AGE_MIN > 0 and float(age_min) > MAX_PAIR_AGE_MIN:
        return False
    buys = _to_int(m.get("buys"))
    sells = _to_int(m.get("sells"))
    txns_5m = _to_int(m.get("hits"))
    if buys < MIN_BUYS_5M:
        return False
    if txns_5m < MIN_TXNS_5M:
        return False
    if _to_float(m.get("volume_5m")) < MIN_VOL_5M_USD:
        return False
    bs_ratio = (float(buys) / float(max(1, sells))) if sells > 0 else float(buys)
    if bs_ratio < MIN_BUY_SELL_RATIO:
        return False
    if _to_float(m.get("net_sol_in")) < MIN_NET_SOL_IN:
        return False
    if _to_float(m.get("price_change_5m")) < MIN_MOM_5M:
        return False
    if MAX_MOM_1H_CHASE > 0 and _to_float(m.get("price_change_1h")) > MAX_MOM_1H_CHASE:
        return False
    if REQUIRE_PATTERN:
        pat = str(m.get("mover_pattern") or "none")
        if PATTERN_MODE == "breakout":
            if pat != "breakout":
                return False
        elif PATTERN_MODE == "retest":
            if pat != "retest_hold":
                return False
        else:
            if pat == "none":
                return False
    if _to_float(m.get("score")) < MIN_SCORE:
        return False
    return True


def _emit_signal(mint: str, metrics: dict[str, Any], score: float) -> None:
    now = time.time()
    payload = build_launch_signal_payload(
        mint=mint,
        metrics=normalize_signal_metrics(metrics),
        score=score,
        ts=now,
        first_seen=now,
    )
    os.makedirs(os.path.dirname(SIGNALS_OUT), exist_ok=True)
    with open(SIGNALS_OUT, "a", encoding="utf-8") as out:
        out.write(json.dumps(payload) + "\n")


def main() -> int:
    st = _load_state()
    last_emit: dict[str, float] = {}
    for k, v in (st.get("last_emit") or {}).items():
        try:
            last_emit[str(k)] = float(v)
        except Exception:
            continue
    # Backward compatibility: older state format had an "emitted" list only.
    for mint in (st.get("emitted") or []):
        if isinstance(mint, str) and mint not in last_emit:
            last_emit[mint] = 0.0
    next_check: dict[str, float] = {}
    last_seen_seed: dict[str, float] = {}
    for k, v in (st.get("next_check") or {}).items():
        try:
            next_check[str(k)] = float(v)
        except Exception:
            continue
    for k, v in (st.get("last_seen_seed") or {}).items():
        try:
            last_seen_seed[str(k)] = float(v)
        except Exception:
            continue

    backoff = 0.0
    last_log = 0.0

    with httpx.Client() as client:
        while True:
            now = time.time()
            emitted_cycle = 0
            checked_cycle = 0

            seeds = _seed_mints(client)
            for mint in seeds:
                last_seen_seed[mint] = now
            if SEED_TTL_S > 0:
                cutoff = now - SEED_TTL_S
                for mint, ts in list(last_seen_seed.items()):
                    if ts < cutoff:
                        last_seen_seed.pop(mint, None)
                        next_check.pop(mint, None)

            todo: list[str] = []
            for mint, _ in sorted(last_seen_seed.items(), key=lambda kv: kv[1], reverse=True):
                if EMIT_COOLDOWN_S > 0:
                    prev_emit = float(last_emit.get(mint, 0.0) or 0.0)
                    if prev_emit > 0 and (now - prev_emit) < EMIT_COOLDOWN_S:
                        continue
                if EMIT_COOLDOWN_S <= 0 and mint in last_emit:
                    continue
                if next_check.get(mint, 0.0) > now:
                    continue
                todo.append(mint)
                if len(todo) >= MAX_DETAILS_PER_POLL:
                    break

            for mint in todo:
                checked_cycle += 1
                try:
                    resp = client.get(f"{DEX_TOKEN}/{mint}", timeout=12.0)
                    if resp.status_code == 429:
                        backoff = min(600.0, max(10.0, backoff * 1.8))
                        break
                    if resp.status_code != 200:
                        next_check[mint] = now + RECHECK_FETCH_FAIL_S
                        continue
                    detail = resp.json() or {}
                    pair = _pick_best_pair(detail)
                    if not isinstance(pair, dict):
                        next_check[mint] = now + RECHECK_FETCH_FAIL_S
                        continue
                    metrics = _pair_metrics(pair)
                    if _passes(metrics):
                        score = _to_float(metrics.get("score"))
                        _emit_signal(mint, metrics, score)
                        last_emit[mint] = now
                        next_check.pop(mint, None)
                        emitted_cycle += 1
                    else:
                        next_check[mint] = now + RECHECK_REJECT_S
                except Exception:
                    next_check[mint] = now + RECHECK_FETCH_FAIL_S

            if now - last_log > 30:
                last_log = now
                print(
                    "dex_mover "
                    f"seeds={len(last_seen_seed)} todo={len(todo)} checked={checked_cycle} emitted={emitted_cycle} "
                    f"emit_cache={len(last_emit)} backoff_s={int(backoff)}",
                    flush=True,
                )
                _save_state(last_emit, next_check, last_seen_seed)

            sleep_s = max(1.0, POLL_S)
            if backoff > 0:
                sleep_s = max(sleep_s, backoff)
                backoff = max(0.0, backoff - 5.0)
            time.sleep(sleep_s)


if __name__ == "__main__":
    raise SystemExit(main())
