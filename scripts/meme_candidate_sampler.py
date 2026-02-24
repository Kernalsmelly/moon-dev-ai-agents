#!/usr/bin/env python3
"""Sample DexScreener candidates and show which gates are starving throughput.

This is an *offline* diagnostic: it does not trade or write to the DB.
It answers: with the current `.env` thresholds, do we have enough candidates that
could possibly pass filters?
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv


BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
load_dotenv(dotenv_path=str(BASE / ".env"), override=True)

if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

import src.meme_config as meme_config  # noqa: E402


DEXSCREENER_BASE = "https://api.dexscreener.com"
TOKEN_BOOSTS = f"{DEXSCREENER_BASE}/token-boosts/latest/v1"
TOKEN_PROFILES = f"{DEXSCREENER_BASE}/token-profiles/latest/v1"
TOKEN_DETAIL = f"{DEXSCREENER_BASE}/latest/dex/tokens"


@dataclass
class Cand:
    mint: str
    symbol: str
    liquidity: float
    mcap: float
    price_change_5m: float
    buys_5m: int
    sells_5m: int
    txns_5m: int
    volume_5m: float
    volume_1h: float


def _is_truthy(v: str) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes")


def _gate_counts() -> dict[str, int]:
    return {
        "total": 0,
        "liq_ok": 0,
        "mcap_ok": 0,
        "mom_ok": 0,
        "bs5m_ok": 0,
        "tx5m_ok": 0,
        "buys5m_ok": 0,
        "vol5m_ok": 0,
        "vol5m_share_ok": 0,
        "pump_ok": 0,
        "pass_all": 0,
    }


def _passes(c: Cand) -> tuple[bool, str]:
    if c.liquidity < meme_config.MIN_LIQUIDITY_USD:
        return False, "liq_low"
    if c.mcap < meme_config.MIN_MARKET_CAP_USD:
        return False, "mcap_low"
    if c.price_change_5m < meme_config.MIN_PRICE_CHANGE_5M:
        return False, "mom5m_low"
    if getattr(meme_config, "MIN_BUY_SELL_RATIO_5M", 0.0) > 0 and c.sells_5m > 0:
        bs5m = c.buys_5m / c.sells_5m
        if bs5m < meme_config.MIN_BUY_SELL_RATIO_5M:
            return False, "bs5m_low"
    if getattr(meme_config, "MIN_TXNS_5M", 0) > 0 and c.txns_5m < meme_config.MIN_TXNS_5M:
        return False, "tx5m_low"
    if getattr(meme_config, "MIN_BUYS_5M", 0) > 0 and c.buys_5m < meme_config.MIN_BUYS_5M:
        return False, "buys5m_low"
    if getattr(meme_config, "MIN_VOLUME_5M", 0.0) > 0 and c.volume_5m < meme_config.MIN_VOLUME_5M:
        return False, "vol5m_low"
    if getattr(meme_config, "MIN_VOL5M_SHARE", 0.0) > 0 and c.volume_1h > 0:
        share = c.volume_5m / max(c.volume_1h, 1e-9)
        if share < meme_config.MIN_VOL5M_SHARE:
            return False, "vol5m_share_low"
    if getattr(meme_config, "PULLBACK_ENTRY_ENABLED", True):
        max_5m_pump = getattr(meme_config, "MAX_5M_PUMP", 30.0)
        if c.price_change_5m > max_5m_pump:
            return False, "pump5m_too_high"
    return True, "pass"


async def _fetch_mints(client: httpx.AsyncClient, n: int) -> list[str]:
    use_profiles = _is_truthy(os.getenv("MEME_DEX_USE_PROFILES", "false"))
    use_boosts = _is_truthy(os.getenv("MEME_DEX_USE_BOOSTS", "true"))
    out: list[str] = []
    if use_boosts:
        try:
            r = await client.get(TOKEN_BOOSTS)
            if r.status_code == 200 and isinstance(r.json(), list):
                for item in r.json():
                    if item.get("chainId") == "solana" and item.get("tokenAddress"):
                        out.append(str(item["tokenAddress"]))
                        if len(out) >= n:
                            return out
        except Exception:
            pass
    if use_profiles:
        try:
            r = await client.get(TOKEN_PROFILES)
            if r.status_code == 200 and isinstance(r.json(), list):
                for item in r.json():
                    if item.get("chainId") == "solana" and item.get("tokenAddress"):
                        out.append(str(item["tokenAddress"]))
                        if len(out) >= n:
                            return out
        except Exception:
            pass
    return out[:n]


async def _fetch_one(client: httpx.AsyncClient, mint: str) -> Cand | None:
    try:
        r = await client.get(f"{TOKEN_DETAIL}/{mint}")
        if r.status_code != 200:
            return None
        data = r.json() or {}
        pairs = data.get("pairs") or []
        if not pairs:
            return None
        best = max(pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0.0))
        liq = float((best.get("liquidity") or {}).get("usd") or 0.0)
        mcap = float(best.get("fdv") or 0.0)
        sym = str((best.get("baseToken") or {}).get("symbol") or mint[:4])
        pc = float((best.get("priceChange") or {}).get("m5") or 0.0)
        txns = best.get("txns") or {}
        m5 = txns.get("m5") or {}
        buys_5m = int(m5.get("buys") or 0)
        sells_5m = int(m5.get("sells") or 0)
        vol = best.get("volume") or {}
        vol5 = float(vol.get("m5") or 0.0)
        vol1 = float(vol.get("h1") or 0.0)
        return Cand(
            mint=mint,
            symbol=sym,
            liquidity=liq,
            mcap=mcap,
            price_change_5m=pc,
            buys_5m=buys_5m,
            sells_5m=sells_5m,
            txns_5m=buys_5m + sells_5m,
            volume_5m=vol5,
            volume_1h=vol1,
        )
    except Exception:
        return None


async def main() -> int:
    n = int(os.getenv("MEME_SAMPLE_N", "60") or 60)
    conc = int(os.getenv("MEME_SAMPLE_CONCURRENCY", "10") or 10)

    counts = _gate_counts()
    fails: dict[str, int] = {}
    passing: list[Cand] = []

    async with httpx.AsyncClient(timeout=12.0) as client:
        mints = await _fetch_mints(client, n)
        sem = asyncio.Semaphore(conc)

        async def work(m: str) -> Cand | None:
            async with sem:
                return await _fetch_one(client, m)

        rows = [r for r in await asyncio.gather(*[work(m) for m in mints]) if r]

    for c in rows:
        counts["total"] += 1
        if c.liquidity >= meme_config.MIN_LIQUIDITY_USD:
            counts["liq_ok"] += 1
        if c.mcap >= meme_config.MIN_MARKET_CAP_USD:
            counts["mcap_ok"] += 1
        if c.price_change_5m >= meme_config.MIN_PRICE_CHANGE_5M:
            counts["mom_ok"] += 1
        if getattr(meme_config, "MIN_BUY_SELL_RATIO_5M", 0.0) <= 0 or c.sells_5m <= 0 or (c.buys_5m / max(c.sells_5m, 1)) >= meme_config.MIN_BUY_SELL_RATIO_5M:
            counts["bs5m_ok"] += 1
        if getattr(meme_config, "MIN_TXNS_5M", 0) <= 0 or c.txns_5m >= meme_config.MIN_TXNS_5M:
            counts["tx5m_ok"] += 1
        if getattr(meme_config, "MIN_BUYS_5M", 0) <= 0 or c.buys_5m >= meme_config.MIN_BUYS_5M:
            counts["buys5m_ok"] += 1
        if getattr(meme_config, "MIN_VOLUME_5M", 0.0) <= 0 or c.volume_5m >= meme_config.MIN_VOLUME_5M:
            counts["vol5m_ok"] += 1
        if getattr(meme_config, "MIN_VOL5M_SHARE", 0.0) <= 0 or c.volume_1h <= 0 or (c.volume_5m / max(c.volume_1h, 1e-9)) >= meme_config.MIN_VOL5M_SHARE:
            counts["vol5m_share_ok"] += 1
        if not getattr(meme_config, "PULLBACK_ENTRY_ENABLED", True) or c.price_change_5m <= getattr(meme_config, "MAX_5M_PUMP", 30.0):
            counts["pump_ok"] += 1

        ok, reason = _passes(c)
        if ok:
            counts["pass_all"] += 1
            passing.append(c)
        else:
            fails[reason] = int(fails.get(reason) or 0) + 1

    print("DexScreener Candidate Sampler")
    print("")
    print("Current gates:")
    print(f"  MIN_MCAP_USD={meme_config.MIN_MARKET_CAP_USD:g}")
    print(f"  MIN_LIQUIDITY_USD={meme_config.MIN_LIQUIDITY_USD:g}")
    print(f"  MIN_TXNS_5M={getattr(meme_config,'MIN_TXNS_5M',0)} MIN_BUYS_5M={getattr(meme_config,'MIN_BUYS_5M',0)} MIN_VOLUME_5M={getattr(meme_config,'MIN_VOLUME_5M',0.0):g}")
    print(f"  MIN_BUY_SELL_5M={getattr(meme_config,'MIN_BUY_SELL_RATIO_5M',0.0):g} MAX_5M_PUMP={getattr(meme_config,'MAX_5M_PUMP',30.0):g}")
    print("")
    print(f"Sampled mints: {len(rows)}")
    print("Gate hits (independent):")
    for k in ["liq_ok","mcap_ok","mom_ok","bs5m_ok","tx5m_ok","buys5m_ok","vol5m_ok","vol5m_share_ok","pump_ok","pass_all"]:
        print(f"  {k}: {counts[k]}/{counts['total']}")
    print("")
    if fails:
        print("Top failure reasons:")
        for k, v in sorted(fails.items(), key=lambda kv: kv[1], reverse=True)[:10]:
            print(f"  {k}: {v}")
        print("")
    if passing:
        passing.sort(key=lambda c: (c.volume_5m, c.liquidity), reverse=True)
        print("Examples that pass all gates (top by vol5m):")
        for c in passing[:10]:
            bs = (c.buys_5m / c.sells_5m) if c.sells_5m else float("inf")
            print(
                f"  {c.symbol:10s} mcap=${c.mcap:,.0f} liq=${c.liquidity:,.0f} "
                f"pc5m={c.price_change_5m:+.1f}% tx5m={c.txns_5m} bs5m={bs:.2f} vol5m=${c.volume_5m:,.0f}"
            )
    else:
        print("No candidates passed all gates in this sample.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
