#!/usr/bin/env python3
"""Poll Birdeye new listing endpoint and emit launch signals.

Uses BIRDEYE_API_KEY to access public-api.birdeye.so/defi/v2/tokens/new_listing.
Writes to data/meme_launch_signals.jsonl and optional mints file.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

API_KEY = os.getenv("BIRDEYE_API_KEY", "")
if not API_KEY:
    raise SystemExit("BIRDEYE_API_KEY not set")

BASE_URL = os.getenv("BIRDEYE_BASE", "https://public-api.birdeye.so")
ENDPOINT = f"{BASE_URL}/defi/v2/tokens/new_listing"

POLL_SECONDS = int(os.getenv("BIRDEYE_NEWLISTING_POLL", "10"))
SIGNALS_OUT = os.getenv("MEME_LAUNCH_SIGNALS_FILE", os.path.join(DATA_DIR, "meme_launch_signals.jsonl"))
MINTS_OUT = os.getenv("MEME_LAUNCH_MINTS_FILE", os.path.join(DATA_DIR, "meme_launch_mints.jsonl"))
STATE_PATH = os.getenv("BIRDEYE_NEWLISTING_STATE", os.path.join(DATA_DIR, "birdeye_newlisting_state.json"))

SOURCES_FILTER = os.getenv("MEME_LISTING_SOURCES", "").strip().lower()
ALLOWED_SOURCES = {s.strip() for s in SOURCES_FILTER.split(",") if s.strip()} if SOURCES_FILTER else None


def load_state() -> set[str]:
    seen = set()
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as fh:
                st = json.load(fh)
                for m in st.get("seen", []):
                    seen.add(m)
        except Exception:
            pass
    return seen


def save_state(seen: set[str]) -> None:
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as fh:
            json.dump({"seen": list(seen)[-5000:]}, fh)
    except Exception:
        pass


def emit_signal(item: dict[str, Any]) -> None:
    now = time.time()
    mint = item.get("address")
    if not mint:
        return
    metrics = {
        "source": item.get("source"),
        "symbol": item.get("symbol"),
        "name": item.get("name"),
        "liquidity": item.get("liquidity", 0),
        "logoURI": item.get("logoURI"),
        "liquidityAddedAt": item.get("liquidityAddedAt"),
    }
    os.makedirs(os.path.dirname(SIGNALS_OUT), exist_ok=True)
    with open(SIGNALS_OUT, "a", encoding="utf-8") as out:
        out.write(json.dumps({
            "ts": now,
            "mint": mint,
            "first_seen": now,
            "metrics": metrics,
            "score": 2.5,
        }) + "\n")

    # optional mints file for debugging
    os.makedirs(os.path.dirname(MINTS_OUT), exist_ok=True)
    with open(MINTS_OUT, "a", encoding="utf-8") as mf:
        mf.write(json.dumps({
            "ts": now,
            "mint": mint,
            "program_id": None,
            "signature": None,
        }) + "\n")


def main() -> int:
    seen = load_state()
    backoff = 0
    last_log = 0.0

    while True:
        headers = {"X-API-KEY": API_KEY}
        try:
            resp = requests.get(ENDPOINT, headers=headers, timeout=10)
            if resp.status_code in (429, 400):
                # compute units / rate limit
                backoff = min(600, backoff * 2 + 5)
                time.sleep(backoff)
                continue
            resp.raise_for_status()
            data = resp.json()
            # Birdeye sometimes returns HTTP 200 with {"success":false,"message":"...limit exceeded"}.
            if isinstance(data, dict) and data.get("success") is False:
                msg = str(data.get("message") or data.get("error") or "")
                ml = msg.lower()
                if ("compute" in ml and "unit" in ml) or ("usage limit" in ml) or ("limit exceeded" in ml) or ("quota" in ml):
                    backoff = min(900, backoff * 2 + 10)
                    time.sleep(backoff)
                    continue
        except Exception:
            time.sleep(5)
            continue

        items = (data.get("data") or {}).get("items") or []
        new_count = 0
        for item in items:
            mint = item.get("address")
            if not mint or mint in seen:
                continue
            src = str(item.get("source", "")).lower()
            if ALLOWED_SOURCES and src not in ALLOWED_SOURCES:
                continue
            emit_signal(item)
            seen.add(mint)
            new_count += 1

        if time.time() - last_log > 30:
            last_log = time.time()
            print(f"Birdeye new listing: new={new_count} seen={len(seen)} backoff={backoff}s", flush=True)
            save_state(seen)

        if backoff > 0:
            time.sleep(backoff)
            backoff = max(0, backoff - 5)
        else:
            time.sleep(max(3, POLL_SECONDS))


if __name__ == "__main__":
    raise SystemExit(main())
