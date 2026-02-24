#!/usr/bin/env python3
"""Track early-window stats for launch mints and emit signal candidates.

Reads launch mints JSONL and periodically queries DexScreener for
5m buy/sell/volume metrics to decide if a mint is "hot" early.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import httpx

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import src.meme_config as meme_config

DEXSCREENER_BASE = os.getenv("DEXSCREENER_BASE", "https://api.dexscreener.com")
DEXSCREENER_TOKEN = f"{DEXSCREENER_BASE}/latest/dex/tokens"

BIRDEYE_BASE = os.getenv("BIRDEYE_BASE", "https://public-api.birdeye.so")
BIRDEYE_TOKEN = f"{BIRDEYE_BASE}/defi/token_overview"
BIRDEYE_KEY = os.getenv("BIRDEYE_API_KEY") or os.getenv("BIRDEYE_KEY") or ""


def fetch_dexscreener(client: httpx.Client, mint: str) -> dict | None:
    try:
        resp = client.get(f"{DEXSCREENER_TOKEN}/{mint}", timeout=10.0)
        if resp.status_code != 200:
            return None
        data = resp.json()
        pairs = data.get("pairs", [])
        if not pairs:
            return None
        best = max(pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0))
        txns = best.get("txns", {}) or {}
        m5 = txns.get("m5", {}) or {}
        vol = best.get("volume", {}) or {}
        price_change = best.get("priceChange", {}) or {}
        return {
            "price": float(best.get("priceUsd", 0) or 0),
            "liquidity": float(best.get("liquidity", {}).get("usd", 0) or 0),
            "buys_5m": int(m5.get("buys", 0) or 0),
            "sells_5m": int(m5.get("sells", 0) or 0),
            "volume_5m": float(vol.get("m5", 0) or 0),
            "volume_1h": float(vol.get("h1", 0) or 0),
            "price_change_5m": float(price_change.get("m5", 0) or 0),
        }
    except Exception:
        return None


def fetch_birdeye(client: httpx.Client, mint: str) -> dict | None:
    if not BIRDEYE_KEY:
        return None
    try:
        headers = {"X-API-KEY": BIRDEYE_KEY}
        resp = client.get(f"{BIRDEYE_TOKEN}?address={mint}", headers=headers, timeout=10.0)
        if resp.status_code in (429, 400):
            # Likely compute-unit or rate-limit exhaustion
            return {"_rate_limited": True}
        if resp.status_code != 200:
            return None
        data = resp.json()
        if isinstance(data, dict) and data.get("success") is False:
            msg = str(data.get("message", "")).lower()
            if "limit" in msg or "rate" in msg or "compute" in msg:
                return {"_rate_limited": True}
            return None
        if not data or "data" not in data:
            return None
        d = data["data"] or {}
        # Birdeye returns 24h stats; use price and liquidity, and fall back for short windows
        return {
            "price": float(d.get("price", 0) or 0),
            "liquidity": float(d.get("liquidity", 0) or 0),
            "buys_5m": int(d.get("buy_count_5m", d.get("buy_count_1h", 0)) or 0),
            "sells_5m": int(d.get("sell_count_5m", d.get("sell_count_1h", 0)) or 0),
            "volume_5m": float(d.get("volume_5m", d.get("volume_1h", 0)) or 0),
            "volume_1h": float(d.get("volume_1h", 0) or 0),
            "price_change_5m": float(d.get("price_change_5m", d.get("price_change_1h", 0)) or 0),
        }
    except Exception:
        return None


def fetch_metrics(client: httpx.Client, mint: str) -> dict | None:
    # Prefer Birdeye because DNS to DexScreener can be flaky here
    metrics = fetch_birdeye(client, mint)
    if isinstance(metrics, dict) and metrics.get("_rate_limited"):
        return metrics
    if metrics:
        return metrics
    return fetch_dexscreener(client, mint)


def passes_thresholds(m: dict) -> bool:
    if m is None:
        return False
    if m.get("buys_5m", 0) < meme_config.MIN_BUYS_5M:
        return False
    if (m.get("buys_5m", 0) + m.get("sells_5m", 0)) < meme_config.MIN_TXNS_5M:
        return False
    if m.get("volume_5m", 0.0) < getattr(meme_config, "MIN_VOLUME_5M", 0.0):
        return False
    if m.get("liquidity", 0.0) < getattr(meme_config, "MIN_LIQUIDITY_EARLY", 0.0):
        return False
    vol_1h = m.get("volume_1h", 0.0) or 0.0
    if getattr(meme_config, "MIN_VOL5M_SHARE", 0.0) > 0 and vol_1h > 0:
        share = m.get("volume_5m", 0.0) / vol_1h
        if share < meme_config.MIN_VOL5M_SHARE:
            return False
    if getattr(meme_config, "MIN_BUY_SELL_RATIO_5M", 0.0) > 0 and m.get("sells_5m", 0) > 0:
        ratio = m.get("buys_5m", 0) / m.get("sells_5m", 0)
        if ratio < meme_config.MIN_BUY_SELL_RATIO_5M:
            return False
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mints", default="data/meme_launch_mints.jsonl", help="Launch mints JSONL")
    parser.add_argument("--out", default="data/meme_launch_signals.jsonl", help="Output signals JSONL")
    parser.add_argument("--window-sec", type=int, default=120, help="Early window seconds")
    parser.add_argument("--poll", type=int, default=3, help="Polling interval")
    parser.add_argument("--min-score", type=float, default=2.5, help="Min signal score to emit")
    args = parser.parse_args()

    if not os.path.exists(args.mints):
        print(f"No mints file: {args.mints}")
        return

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    state = {}  # mint -> first_ts
    offset = 0

    backoff = 0
    def log(msg: str) -> None:
        print(msg, flush=True)

    last_status_log = 0.0
    with httpx.Client() as client:
        while True:
            try:
                with open(args.mints, "r", encoding="utf-8") as fh:
                    if offset:
                        fh.seek(offset)
                    while True:
                        line = fh.readline()
                        if not line:
                            break
                        offset = fh.tell()
                        try:
                            obj = json.loads(line)
                        except Exception:
                            continue
                        mint = obj.get("mint")
                        ts = float(obj.get("ts", time.time()))
                        if mint and mint not in state:
                            state[mint] = ts
            except Exception:
                pass

            # Evaluate active mints within window
            now = time.time()
            to_drop = []
            for mint, first_ts in list(state.items()):
                if (now - first_ts) > args.window_sec:
                    to_drop.append(mint)
                    continue
                metrics = fetch_metrics(client, mint)
                if isinstance(metrics, dict) and metrics.get("_rate_limited"):
                    backoff = min(600, backoff * 2 + 5)
                    log(f"Rate limited. Backing off {backoff}s")
                    break
                if passes_thresholds(metrics):
                    # Confidence score: weighted burst metrics
                    score = 0.0
                    score += min(5.0, (metrics.get("buys_5m", 0) / max(1.0, meme_config.MIN_BUYS_5M))) * 1.0
                    score += min(5.0, ((metrics.get("buys_5m", 0) + metrics.get("sells_5m", 0)) / max(1.0, meme_config.MIN_TXNS_5M))) * 0.7
                    score += min(5.0, (metrics.get("volume_5m", 0.0) / max(1.0, getattr(meme_config, "MIN_VOLUME_5M", 1.0)))) * 1.2
                    if score < args.min_score:
                        to_drop.append(mint)
                        continue
                    with open(args.out, "a", encoding="utf-8") as out:
                        out.write(json.dumps({
                            "ts": now,
                            "mint": mint,
                            "first_seen": first_ts,
                            "metrics": metrics,
                            "score": round(score, 3),
                        }) + "\n")
                    log(f"Signal emitted for {mint} score={round(score,3)}")
                    to_drop.append(mint)

            for m in to_drop:
                state.pop(m, None)

            if time.time() - last_status_log > 30:
                last_status_log = time.time()
                log(f"Status: active={len(state)} backoff={backoff}s")
            if backoff > 0:
                time.sleep(backoff)
                backoff = max(0, backoff - 5)
            else:
                time.sleep(max(1, args.poll))


if __name__ == "__main__":
    main()
