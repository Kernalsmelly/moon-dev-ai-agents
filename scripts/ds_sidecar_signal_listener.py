#!/usr/bin/env python3
"""Emit auxiliary DexScreener-ranked Solana signals into the launch signal tape.

This sidecar uses the external `dexscreener-cli-mcp-tool` repo for discovery and
ranking, then fetches fresh DexScreener pair data to emit signals in the same
schema the bot already consumes.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(dotenv_path=str(PROJECT_ROOT / ".env"), override=False)

EXTERNAL_REPO = Path(
    str(os.getenv("DS_SIDECAR_REPO_PATH", "") or "").strip() or PROJECT_ROOT / "external" / "dexscreener-cli-mcp-tool"
)
if str(EXTERNAL_REPO) not in sys.path:
    sys.path.insert(0, str(EXTERNAL_REPO))

from src.meme_signal_schema import build_launch_signal_payload, normalize_signal_metrics

from dexscreener_cli.client import DexScreenerClient
from dexscreener_cli.config import ScanFilters
from dexscreener_cli.scanner import HotScanner

DATA_DIR = PROJECT_ROOT / "data"
SIGNALS_OUT = Path(
    str(os.getenv("MEME_LAUNCH_SIGNALS_FILE", "") or "").strip() or DATA_DIR / "meme_launch_signals.jsonl"
)
STATE_PATH = Path(
    str(os.getenv("DS_SIDECAR_STATE_PATH", "") or "").strip() or DATA_DIR / "ds_sidecar_state.json"
)
LOG_LABEL = "ds_sidecar"

POLL_S = float(os.getenv("DS_SIDECAR_POLL_S", "25") or 25)
SCAN_LIMIT = int(os.getenv("DS_SIDECAR_SCAN_LIMIT", "28") or 28)
EMIT_COOLDOWN_S = float(os.getenv("DS_SIDECAR_EMIT_COOLDOWN_S", "900") or 900)
SOURCE_LABEL = str(os.getenv("DS_SIDECAR_SOURCE", LOG_LABEL) or LOG_LABEL).strip() or LOG_LABEL

SCAN_MIN_LIQUIDITY_USD = float(os.getenv("DS_SIDECAR_SCAN_MIN_LIQUIDITY_USD", "4000") or 4000)
SCAN_MIN_VOLUME_H24_USD = float(os.getenv("DS_SIDECAR_SCAN_MIN_VOLUME_H24_USD", "500") or 500)
SCAN_MIN_TXNS_H1 = int(os.getenv("DS_SIDECAR_SCAN_MIN_TXNS_H1", "4") or 4)
SCAN_MIN_PRICE_CHANGE_H1 = float(os.getenv("DS_SIDECAR_SCAN_MIN_PRICE_CHANGE_H1", "-8") or -8)

MIN_SCANNER_SCORE = float(os.getenv("DS_SIDECAR_MIN_SCANNER_SCORE", "52") or 52)
MIN_BREAKOUT_READINESS = float(os.getenv("DS_SIDECAR_MIN_BREAKOUT_READINESS", "48") or 48)
MIN_RELATIVE_STRENGTH = float(os.getenv("DS_SIDECAR_MIN_RELATIVE_STRENGTH", "2") or 2)
MIN_RISK_SCORE = float(os.getenv("DS_SIDECAR_MIN_RISK_SCORE", "42") or 42)
MAX_CANDIDATE_AGE_HOURS = float(os.getenv("DS_SIDECAR_MAX_CANDIDATE_AGE_HOURS", "1.5") or 1.5)
ALLOW_FAST_DECAY = str(os.getenv("DS_SIDECAR_ALLOW_FAST_DECAY", "false") or "false").lower() in ("1", "true", "yes")

MIN_MCAP_USD = float(os.getenv("DS_SIDECAR_MIN_MCAP_USD", "40000") or 40000)
MAX_MCAP_USD = float(os.getenv("DS_SIDECAR_MAX_MCAP_USD", "300000") or 300000)
MAX_PAIR_AGE_MIN = float(os.getenv("DS_SIDECAR_MAX_PAIR_AGE_MIN", "90") or 90)
MIN_BUYS_5M = int(os.getenv("DS_SIDECAR_MIN_BUYS_5M", "4") or 4)
MIN_TXNS_5M = int(os.getenv("DS_SIDECAR_MIN_TXNS_5M", "8") or 8)
MIN_VOL_5M_USD = float(os.getenv("DS_SIDECAR_MIN_VOL_5M_USD", "1200") or 1200)
MIN_BUY_SELL_RATIO = float(os.getenv("DS_SIDECAR_MIN_BUY_SELL_RATIO", "1.05") or 1.05)
MIN_NET_SOL_IN = float(os.getenv("DS_SIDECAR_MIN_NET_SOL_IN", "0.60") or 0.60)
MIN_MOM_5M = float(os.getenv("DS_SIDECAR_MIN_MOM_5M", "-5") or -5)
MAX_MOM_1H_CHASE = float(os.getenv("DS_SIDECAR_MAX_MOM_1H_CHASE", "0") or 0)
SOL_USD_FALLBACK = float(os.getenv("MEME_SOL_USD_FALLBACK", "180") or 180)


def _load_state() -> dict[str, float]:
    out: dict[str, float] = {}
    if not STATE_PATH.exists():
        return out
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        last_emit = raw.get("last_emit") if isinstance(raw, dict) else {}
        if isinstance(last_emit, dict):
            for key, value in last_emit.items():
                try:
                    out[str(key)] = float(value)
                except Exception:
                    continue
    except Exception:
        return {}
    return out


def _save_state(last_emit: dict[str, float]) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {"last_emit": {k: float(v) for k, v in list(last_emit.items())[-50000:]}}
        STATE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        pass


def _to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _pick_best_pair(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    sol_pairs: list[dict[str, Any]] = []
    fallback: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("chainId", "")).lower() != "solana":
            continue
        quote = str(((row.get("quoteToken") or {}) if isinstance(row.get("quoteToken"), dict) else {}).get("symbol", "")).upper()
        if quote in ("SOL", "WSOL"):
            sol_pairs.append(row)
        else:
            fallback.append(row)
    pool = sol_pairs if sol_pairs else fallback
    if not pool:
        return None
    return max(
        pool,
        key=lambda row: _to_float(((row.get("volume") or {}) if isinstance(row.get("volume"), dict) else {}).get("m5"))
        + _to_float(((row.get("liquidity") or {}) if isinstance(row.get("liquidity"), dict) else {}).get("usd")) * 0.01,
    )


def _pair_metrics(pair: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    txns = pair.get("txns") or {}
    m5 = (txns.get("m5") or {}) if isinstance(txns, dict) else {}
    volume = pair.get("volume") or {}
    liquidity = pair.get("liquidity") or {}
    price_change = pair.get("priceChange") or {}
    buys_5m = _to_int(m5.get("buys"))
    sells_5m = _to_int(m5.get("sells"))
    txns_5m = buys_5m + sells_5m
    vol_5m = _to_float((volume if isinstance(volume, dict) else {}).get("m5"))
    vol_1h = _to_float((volume if isinstance(volume, dict) else {}).get("h1"))
    vol_5m_share = (vol_5m / vol_1h) if vol_1h > 0 else 0.0
    liq = _to_float((liquidity if isinstance(liquidity, dict) else {}).get("usd"))
    mcap = _to_float(pair.get("marketCap")) or _to_float(pair.get("fdv"))
    mom_5m = _to_float((price_change if isinstance(price_change, dict) else {}).get("m5"))
    mom_1h = _to_float((price_change if isinstance(price_change, dict) else {}).get("h1"))
    created_ms = _to_float(pair.get("pairCreatedAt"))
    age_min = ((now * 1000.0 - created_ms) / 60000.0) if created_ms > 0 else None
    bs_ratio = (float(buys_5m) / float(max(1, sells_5m))) if sells_5m > 0 else float(buys_5m)
    net_usd = 0.0
    if txns_5m > 0:
        net_usd = vol_5m * ((float(buys_5m) - float(sells_5m)) / float(txns_5m))
    net_sol_in = max(0.0, net_usd / max(1.0, SOL_USD_FALLBACK))
    pattern = "none"
    if mom_5m >= 5.0 and vol_5m_share >= 0.05 and buys_5m >= max(4, MIN_BUYS_5M) and bs_ratio >= max(1.05, MIN_BUY_SELL_RATIO):
        pattern = "breakout"
    elif mom_1h >= 10.0 and mom_5m >= -3.0 and net_sol_in >= max(0.6, MIN_NET_SOL_IN) and bs_ratio >= max(1.0, MIN_BUY_SELL_RATIO):
        pattern = "retest_hold"

    score = 0.0
    score += min(25.0, 25.0 * (txns_5m / 24.0))
    score += min(20.0, 20.0 * (buys_5m / 12.0))
    score += min(20.0, 20.0 * (vol_5m / 15000.0))
    score += min(20.0, 20.0 * (net_sol_in / 3.0))
    score += min(15.0, max(0.0, (mom_5m + 5.0) / 20.0) * 15.0)
    score = max(0.0, min(100.0, score))

    return {
        "source": SOURCE_LABEL,
        "pair_address": pair.get("pairAddress"),
        "dex_id": pair.get("dexId"),
        "url": pair.get("url"),
        "symbol": ((pair.get("baseToken") or {}) if isinstance(pair.get("baseToken"), dict) else {}).get("symbol") or "",
        "name": ((pair.get("baseToken") or {}) if isinstance(pair.get("baseToken"), dict) else {}).get("name") or "",
        "price": _to_float(pair.get("priceUsd")),
        "liquidity": liq,
        "market_cap": mcap,
        "hits": txns_5m,
        "buys": buys_5m,
        "sells": sells_5m,
        "unique_buyers": max(0, min(64, buys_5m)),
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
        "pair_age_min": age_min,
        "mover_pattern": pattern,
        "score": score,
    }


def _passes_scanner_candidate(candidate: Any) -> bool:
    try:
        pair = candidate.pair
        analytics = candidate.analytics
    except Exception:
        return False
    if str(getattr(pair, "chain_id", "")).lower() != "solana":
        return False
    mcap = _to_float(getattr(pair, "market_cap", 0.0) or getattr(pair, "fdv", 0.0))
    if mcap < MIN_MCAP_USD:
        return False
    if MAX_MCAP_USD > 0 and mcap > MAX_MCAP_USD:
        return False
    if _to_float(getattr(candidate, "score", 0.0)) < MIN_SCANNER_SCORE:
        return False
    if _to_float(getattr(analytics, "risk_score", 0.0)) < MIN_RISK_SCORE:
        return False
    if bool(getattr(analytics, "fast_decay", False)) and not ALLOW_FAST_DECAY:
        return False
    age_h = getattr(pair, "age_hours", None)
    if isinstance(age_h, (int, float)) and MAX_CANDIDATE_AGE_HOURS > 0 and float(age_h) > MAX_CANDIDATE_AGE_HOURS:
        return False
    ready = _to_float(getattr(analytics, "breakout_readiness", 0.0))
    rs = _to_float(getattr(analytics, "relative_strength", 0.0))
    if ready < MIN_BREAKOUT_READINESS and rs < MIN_RELATIVE_STRENGTH:
        return False
    return True


def _passes_pair_metrics(metrics: dict[str, Any]) -> bool:
    mcap = _to_float(metrics.get("market_cap"))
    if mcap < MIN_MCAP_USD:
        return False
    if MAX_MCAP_USD > 0 and mcap > MAX_MCAP_USD:
        return False
    age_min = metrics.get("pair_age_min")
    if isinstance(age_min, (int, float)) and MAX_PAIR_AGE_MIN > 0 and float(age_min) > MAX_PAIR_AGE_MIN:
        return False
    hits = _to_int(metrics.get("hits"))
    buys = _to_int(metrics.get("buys"))
    sells = _to_int(metrics.get("sells"))
    if buys < MIN_BUYS_5M:
        return False
    if hits < MIN_TXNS_5M:
        return False
    if _to_float(metrics.get("volume_5m")) < MIN_VOL_5M_USD:
        return False
    bs_ratio = (float(buys) / float(max(1, sells))) if sells > 0 else float(buys)
    if bs_ratio < MIN_BUY_SELL_RATIO:
        return False
    if _to_float(metrics.get("net_sol_in")) < MIN_NET_SOL_IN:
        return False
    if _to_float(metrics.get("price_change_5m")) < MIN_MOM_5M:
        return False
    if MAX_MOM_1H_CHASE > 0 and _to_float(metrics.get("price_change_1h")) > MAX_MOM_1H_CHASE:
        return False
    if str(metrics.get("mover_pattern") or "none") == "none":
        return False
    return True


def _merged_score(metrics: dict[str, Any], candidate: Any) -> float:
    try:
        ds_score = _to_float(getattr(candidate, "score", 0.0))
        breakout = _to_float(getattr(candidate.analytics, "breakout_readiness", 0.0))
        risk = _to_float(getattr(candidate.analytics, "risk_score", 0.0))
    except Exception:
        ds_score = 0.0
        breakout = 0.0
        risk = 0.0
    local = _to_float(metrics.get("score"))
    blend = (local * 0.50) + (ds_score * 0.30) + (breakout * 0.15) + (min(risk, 100.0) * 0.05)
    return round(max(0.0, min(100.0, blend)), 2)


def _candidate_metadata(candidate: Any) -> dict[str, Any]:
    analytics = getattr(candidate, "analytics", None)
    pair = getattr(candidate, "pair", None)
    breakout_readiness = _to_float(getattr(analytics, "breakout_readiness", 0.0))
    relative_strength = _to_float(getattr(analytics, "relative_strength", 0.0))
    volume_velocity = _to_float(getattr(analytics, "volume_velocity", 0.0))
    txn_velocity = _to_float(getattr(analytics, "txn_velocity", 0.0))
    ds_pattern = "none"
    if breakout_readiness >= 58.0 and relative_strength >= 6.0:
        ds_pattern = "breakout"
    elif relative_strength >= 4.0 and volume_velocity >= 0.9 and txn_velocity >= 0.9:
        ds_pattern = "retest_hold"
    return {
        "ds_score": _to_float(getattr(candidate, "score", 0.0)),
        "ds_discovery": str(getattr(candidate, "discovery", "") or ""),
        "ds_tags": list(getattr(candidate, "tags", []) or []),
        "ds_breakout_readiness": breakout_readiness,
        "ds_relative_strength": relative_strength,
        "ds_volume_velocity": volume_velocity,
        "ds_txn_velocity": txn_velocity,
        "ds_risk_score": _to_float(getattr(analytics, "risk_score", 0.0)),
        "ds_risk_penalty": _to_float(getattr(analytics, "risk_penalty", 0.0)),
        "ds_fast_decay": bool(getattr(analytics, "fast_decay", False)),
        "ds_mover_pattern": ds_pattern,
        "ds_boost_total": _to_float(getattr(candidate, "boost_total", 0.0)),
        "ds_boost_count": _to_int(getattr(candidate, "boost_count", 0)),
        "ds_has_profile": bool(getattr(candidate, "has_profile", False)),
        "ds_pair_liquidity_usd": _to_float(getattr(pair, "liquidity_usd", 0.0)),
        "ds_pair_volume_h24_usd": _to_float(getattr(pair, "volume_h24", 0.0)),
        "ds_pair_txns_h1": _to_int(getattr(pair, "txns_h1", 0)),
        "ds_pair_mcap_usd": _to_float(getattr(pair, "market_cap", 0.0) or getattr(pair, "fdv", 0.0)),
        "ds_pair_age_hours": getattr(pair, "age_hours", None),
    }


async def _scan_candidates() -> tuple[int, int, list[Any]]:
    filters = ScanFilters(
        chains=("solana",),
        limit=SCAN_LIMIT,
        min_liquidity_usd=SCAN_MIN_LIQUIDITY_USD,
        min_volume_h24_usd=SCAN_MIN_VOLUME_H24_USD,
        min_txns_h1=SCAN_MIN_TXNS_H1,
        min_price_change_h1=SCAN_MIN_PRICE_CHANGE_H1,
    )
    async with DexScreenerClient() as client:
        scanner = HotScanner(client)
        ranked = await scanner.scan(filters)
        selected = [candidate for candidate in ranked if _passes_scanner_candidate(candidate)]
        selected = selected[: max(1, SCAN_LIMIT)]
        mints = [
            str(getattr(getattr(candidate, "pair", None), "base_address", "") or "").strip()
            for candidate in selected
            if str(getattr(getattr(candidate, "pair", None), "base_address", "") or "").strip()
        ]
        raw_rows = await client.get_pairs_for_tokens("solana", mints) if mints else []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        base = row.get("baseToken") or {}
        mint = str((base if isinstance(base, dict) else {}).get("address", "") or "").strip()
        if not mint:
            continue
        grouped.setdefault(mint, []).append(row)
    out: list[tuple[Any, dict[str, Any]]] = []
    for candidate in selected:
        mint = str(getattr(getattr(candidate, "pair", None), "base_address", "") or "").strip()
        rows = grouped.get(mint) or []
        pair = _pick_best_pair(rows)
        if not isinstance(pair, dict):
            continue
        metrics = _pair_metrics(pair)
        if not _passes_pair_metrics(metrics):
            continue
        metrics.update(_candidate_metadata(candidate))
        if str(metrics.get("mover_pattern") or "none") == "none":
            ds_pattern = str(metrics.get("ds_mover_pattern") or "none")
            if ds_pattern != "none":
                metrics["mover_pattern"] = ds_pattern
        metrics["score"] = _merged_score(metrics, candidate)
        out.append((candidate, metrics))
    return len(ranked), len(selected), out


def _emit_signal(mint: str, metrics: dict[str, Any]) -> None:
    now = time.time()
    score = _to_float(metrics.get("score"))
    payload = build_launch_signal_payload(
        mint=mint,
        metrics=normalize_signal_metrics(metrics),
        score=score,
        ts=now,
        first_seen=now,
    )
    SIGNALS_OUT.parent.mkdir(parents=True, exist_ok=True)
    with SIGNALS_OUT.open("a", encoding="utf-8") as out:
        out.write(json.dumps(payload) + "\n")


async def _run_once(last_emit: dict[str, float]) -> tuple[int, int]:
    now = time.time()
    checked = 0
    emitted = 0
    raw_count = 0
    selected_count = 0
    try:
        raw_count, selected_count, rows = await _scan_candidates()
    except Exception as exc:
        print(f"{LOG_LABEL} scan_error={type(exc).__name__}: {exc}", flush=True)
        return 0, 0
    for candidate, metrics in rows:
        mint = str(getattr(getattr(candidate, "pair", None), "base_address", "") or "").strip()
        if not mint:
            continue
        checked += 1
        prev = float(last_emit.get(mint, 0.0) or 0.0)
        if EMIT_COOLDOWN_S > 0 and prev > 0 and (now - prev) < EMIT_COOLDOWN_S:
            continue
        _emit_signal(mint, metrics)
        last_emit[mint] = now
        emitted += 1
    print(
        f"{LOG_LABEL} raw={raw_count} selected={selected_count} qualified={checked} emitted={emitted}",
        flush=True,
    )
    return checked, emitted


async def _main_loop(once: bool) -> int:
    last_emit = _load_state()
    last_log = 0.0
    while True:
        checked, emitted = await _run_once(last_emit)
        now = time.time()
        if once or (now - last_log) >= 30:
            last_log = now
            print(
                f"{LOG_LABEL} checked={checked} emitted={emitted} emit_cache={len(last_emit)} source={SOURCE_LABEL}",
                flush=True,
            )
            _save_state(last_emit)
        if once:
            return 0
        await asyncio.sleep(max(5.0, POLL_S))


def main() -> int:
    once = "--once" in sys.argv[1:]
    if not EXTERNAL_REPO.exists():
        print(f"{LOG_LABEL} missing external repo: {EXTERNAL_REPO}", flush=True)
        return 1
    return asyncio.run(_main_loop(once))


if __name__ == "__main__":
    raise SystemExit(main())
