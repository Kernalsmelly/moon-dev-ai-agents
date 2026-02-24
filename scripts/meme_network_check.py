#!/usr/bin/env python3
"""Quick network/API health check for meme bot dependencies."""
from __future__ import annotations

import json
import os
import time

import httpx


def check(url: str, headers: dict | None = None) -> tuple[bool, str]:
    try:
        r = httpx.get(url, headers=headers, timeout=10)
        ok = r.status_code == 200
        return ok, f"status={r.status_code} len={len(r.text)}"
    except Exception as e:
        return False, f"error={e}"


def main():
    tests = []
    tests.append(("DexScreener profiles", "https://api.dexscreener.com/token-profiles/latest/v1", None))
    tests.append(("DexScreener boosts", "https://api.dexscreener.com/token-boosts/latest/v1", None))

    birdeye_key = os.getenv("BIRDEYE_API_KEY")
    if birdeye_key:
        headers = {"X-API-KEY": birdeye_key}
        tests.append(("Birdeye ping (token_security)", "https://public-api.birdeye.so/defi/token_security?address=So11111111111111111111111111111111111111112", headers))

    helius_url = os.getenv("HELIUS_URL")
    if helius_url:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "getHealth"}
        try:
            r = httpx.post(helius_url, json=payload, timeout=10)
            ok = r.status_code == 200
            tests.append(("Helius RPC getHealth", helius_url, None if ok else None))
            print(f"Helius RPC getHealth: status={r.status_code} body={r.text[:200]}")
        except Exception as e:
            print(f"Helius RPC getHealth: error={e}")

    print("=== Meme Network Check ===")
    for name, url, headers in tests:
        ok, info = check(url, headers=headers)
        status = "OK" if ok else "FAIL"
        print(f"{name}: {status} ({info})")
        time.sleep(0.2)


if __name__ == "__main__":
    main()
