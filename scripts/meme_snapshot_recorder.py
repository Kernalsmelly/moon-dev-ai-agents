#!/usr/bin/env python3
"""Record DexScreener snapshots for meme bot replay/backtests.

This script periodically fetches token candidates from DexScreener
and stores normalized per-token snapshots in JSONL for offline replay.

Usage:
  python scripts/meme_snapshot_recorder.py --interval 10 --iterations 360
  python scripts/meme_snapshot_recorder.py --interval 5 --duration 3600
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone

import httpx

# DexScreener endpoints
DEXSCREENER_BASE = os.getenv("DEXSCREENER_BASE", "https://api.dexscreener.com")
DEXSCREENER_TOKEN_PROFILES = f"{DEXSCREENER_BASE}/token-profiles/latest/v1"
DEXSCREENER_TOKEN_BOOSTS = f"{DEXSCREENER_BASE}/token-boosts/latest/v1"
DEXSCREENER_TOKEN = f"{DEXSCREENER_BASE}/latest/dex/tokens"

# Birdeye (optional, for mint/freeze info if API key exists)
BIRDEYE_BASE = "https://public-api.birdeye.so/defi"


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _load_existing_mints(path: str) -> set[str]:
    if not os.path.exists(path):
        return set()
    mints = set()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                    mint = obj.get("mint")
                    if mint:
                        mints.add(mint)
                except Exception:
                    continue
    except Exception:
        return set()
    return mints


def _extract_best_pair(data: dict) -> dict | None:
    pairs = data.get("pairs", []) if isinstance(data, dict) else []
    if not pairs:
        return None
    try:
        return max(pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0))
    except Exception:
        return pairs[0]


def _normalize_snapshot(mint: str, pair: dict, source: str) -> dict:
    liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
    market_cap = float(pair.get("fdv", 0) or 0)
    price = float(pair.get("priceUsd", 0) or 0)

    base_token = pair.get("baseToken", {}) or {}
    symbol = base_token.get("symbol") or ""

    price_change = pair.get("priceChange", {}) or {}
    txns = pair.get("txns", {}) or {}
    h1_txns = txns.get("h1", {}) or {}
    m5_txns = txns.get("m5", {}) or {}
    volume = pair.get("volume", {}) or {}

    pair_created_at = pair.get("pairCreatedAt")
    if pair_created_at:
        discovered_at = pair_created_at / 1000.0
    else:
        discovered_at = _now_ts()

    return {
        "ts": _now_ts(),
        "source": source,
        "mint": mint,
        "symbol": symbol,
        "liquidity": liquidity,
        "market_cap": market_cap,
        "price": price,
        "price_change_5m": float(price_change.get("m5", 0) or 0),
        "price_change_1h": float(price_change.get("h1", 0) or 0),
        "buys_1h": int(h1_txns.get("buys", 0) or 0),
        "sells_1h": int(h1_txns.get("sells", 0) or 0),
        "buys_5m": int(m5_txns.get("buys", 0) or 0),
        "sells_5m": int(m5_txns.get("sells", 0) or 0),
        "txns_1h": int(h1_txns.get("buys", 0) or 0) + int(h1_txns.get("sells", 0) or 0),
        "volume_1h": float(volume.get("h1", 0) or 0),
        "volume_5m": float(volume.get("m5", 0) or 0),
        "discovered_at": discovered_at,
        "pair_created_at": pair_created_at,
    }


def _fetch_birdeye_security(client: httpx.Client, mint: str, api_key: str) -> dict | None:
    try:
        headers = {"X-API-KEY": api_key}
        url = f"{BIRDEYE_BASE}/token_security?address={mint}"
        resp = client.get(url, headers=headers, timeout=10.0)
        if resp.status_code != 200:
            return None
        data = resp.json().get("data", {}) or {}
        return {
            "mint_authority": data.get("mintAuthority") or data.get("mint_authority"),
            "freeze_authority": data.get("freezeAuthority") or data.get("freeze_authority"),
            "freezeable": data.get("freezeable") or data.get("isFreezeable"),
            "top10_holder_pct": data.get("top10HolderPercent"),
            "top10_user_pct": data.get("top10UserPercent"),
        }
    except Exception:
        return None


def _fetch_candidates(client: httpx.Client) -> tuple[list[dict], str | None]:
    candidates: list[dict] = []
    last_error: str | None = None

    # Token profiles
    try:
        resp = client.get(DEXSCREENER_TOKEN_PROFILES, timeout=15.0)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                for item in data:
                    if item.get("chainId") == "solana":
                        candidates.append({"address": item.get("tokenAddress"), "source": "profiles"})
    except Exception as e:
        last_error = f"profiles: {e}"

    # Token boosts
    try:
        resp = client.get(DEXSCREENER_TOKEN_BOOSTS, timeout=15.0)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                for item in data:
                    if item.get("chainId") == "solana":
                        candidates.append({"address": item.get("tokenAddress"), "source": "boosts"})
    except Exception as e:
        last_error = f"boosts: {e}"

    # De-dupe by address
    seen = set()
    deduped = []
    for c in candidates:
        addr = c.get("address")
        if not addr or addr in seen:
            continue
        seen.add(addr)
        deduped.append(c)
    return deduped, last_error


def _fetch_token_snapshot(client: httpx.Client, mint: str, source: str) -> dict | None:
    try:
        resp = client.get(f"{DEXSCREENER_TOKEN}/{mint}", timeout=15.0)
        if resp.status_code != 200:
            return None
        data = resp.json()
        pair = _extract_best_pair(data)
        if not pair:
            return None
        snap = _normalize_snapshot(mint, pair, source)
        birdeye_key = os.getenv("BIRDEYE_API_KEY")
        if birdeye_key:
            sec = _fetch_birdeye_security(client, mint, birdeye_key)
            if sec:
                snap.update(sec)
        return snap
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=10, help="Seconds between snapshots")
    parser.add_argument("--iterations", type=int, default=0, help="Number of loops (0 = infinite)")
    parser.add_argument("--duration", type=int, default=0, help="Seconds to run before stopping (0 = ignore)")
    parser.add_argument("--out", type=str, default="data/meme_snapshots.jsonl", help="Output JSONL path")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    # ensure output file exists
    if not os.path.exists(args.out):
        try:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write("")
        except Exception:
            pass
    start = time.time()
    loops = 0

    # Avoid refetching the same mint over and over within a run
    seen_mints = _load_existing_mints(args.out)

    with httpx.Client() as client:
        while True:
            loops += 1
            candidates, last_error = _fetch_candidates(client)

            records = []
            for cand in candidates:
                mint = cand.get("address")
                if not mint:
                    continue
                # Allow re-snapshots for price evolution, but avoid flooding for the same mint in the same cycle.
                snap = _fetch_token_snapshot(client, mint, cand.get("source", "unknown"))
                if snap:
                    records.append(snap)

            if records:
                with open(args.out, "a", encoding="utf-8") as fh:
                    for rec in records:
                        fh.write(json.dumps(rec) + "\n")
            else:
                # lightweight heartbeat to show the recorder is running
                if last_error:
                    print(f"[snapshot] loop={loops} candidates=0 records=0 error={last_error}")
                else:
                    print(f"[snapshot] loop={loops} candidates=0 records=0")

            if args.iterations and loops >= args.iterations:
                break
            if args.duration and (time.time() - start) >= args.duration:
                break

            time.sleep(max(1, args.interval))


if __name__ == "__main__":
    main()
