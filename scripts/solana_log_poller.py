#!/usr/bin/env python3
"""HTTP poller for Solana program logs via RPC.

Fallback when WebSocket logsSubscribe is unavailable.
Writes events to data/helius_events.jsonl in the same shape as WS listener.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any

import sys
import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.solana.helius_event_store import append_event
from src.solana.rpc_pool import RpcError, RpcPool


def _is_pump_mint(addr: Any) -> bool:
    if not isinstance(addr, str):
        return False
    if not addr.endswith("pump"):
        return False
    if len(addr) < 40 or len(addr) > 48:
        return False
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--programs-file", default="config/helius_programs.json")
    parser.add_argument("--poll", type=int, default=10)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--mints-out", default="data/meme_launch_mints.jsonl")
    parser.add_argument("--state", default="data/solana_log_poller_state.json")
    parser.add_argument("--max-tx-per-tick", type=int, default=40, help="Cap getTransaction calls per tick")
    args = parser.parse_args()

    pool = RpcPool(timeout_s=12.0, max_attempts=3)

    with open(args.programs_file, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    program_ids = data.get("program_ids", [])
    if not program_ids:
        raise SystemExit("No program IDs configured")

    last_seen: dict[str, str] = {}
    seen_mints: set[str] = set()
    try:
        if os.path.exists(args.state):
            with open(args.state, "r", encoding="utf-8") as fh:
                st = json.load(fh)
                for m in st.get("seen_mints", []):
                    seen_mints.add(m)
                if isinstance(st.get("last_seen"), dict):
                    for k, v in st.get("last_seen", {}).items():
                        if isinstance(k, str) and isinstance(v, str):
                            last_seen[k] = v
    except Exception:
        pass

    last_status = 0.0
    backoff = 0
    while True:
        tx_budget = max(1, int(args.max_tx_per_tick))
        for pid in program_ids:
            try:
                sigs = pool.call("getSignaturesForAddress", [pid, {"limit": args.limit}]) or []
            except RpcError as e:
                if time.time() - last_status > 10:
                    print(f"RPC error on getSignaturesForAddress: {e}", flush=True)
                if e.kind == "rate_limited":
                    backoff = min(300, backoff + 30)
                continue
            new = []
            for item in sigs:
                sig = item.get("signature")
                if not sig:
                    continue
                if last_seen.get(pid) == sig:
                    break
                new.append(sig)
            if sigs:
                last_seen[pid] = sigs[0].get("signature")

            for sig in reversed(new):
                try:
                    if tx_budget <= 0:
                        break
                    tx = pool.call("getTransaction", [sig, {"encoding": "json", "maxSupportedTransactionVersion": 0}])
                    tx_budget -= 1
                except RpcError as e:
                    if time.time() - last_status > 10:
                        print(f"RPC error on getTransaction: {e}", flush=True)
                    if e.kind == "rate_limited":
                        backoff = min(300, backoff + 30)
                    continue
                if not tx:
                    continue
                meta = tx.get("meta") or {}
                # try to extract mint-like addresses from account keys
                try:
                    keys = tx.get("transaction", {}).get("message", {}).get("accountKeys", []) or []
                    for k in keys:
                        if isinstance(k, dict):
                            addr = k.get("pubkey")
                        else:
                            addr = k
                        if _is_pump_mint(addr) and addr not in seen_mints:
                            with open(args.mints_out, "a", encoding="utf-8") as mf:
                                mf.write(json.dumps({
                                    "ts": time.time(),
                                    "mint": addr,
                                    "program_id": pid,
                                    "signature": sig,
                                }) + "\\n")
                            seen_mints.add(addr)
                except Exception:
                    pass
                evt = {
                    "program_id": pid,
                    "signature": sig,
                    "err": meta.get("err"),
                    "logs": meta.get("logMessages") or [],
                    "slot": tx.get("slot"),
                }
                append_event(evt)
        if time.time() - last_status > 10:
            last_status = time.time()
            print("Poller tick", flush=True)
            try:
                with open(args.state, "w", encoding="utf-8") as sf:
                    json.dump({"seen_mints": list(seen_mints)[-5000:], "last_seen": last_seen}, sf)
            except Exception:
                pass
        if backoff > 0:
            time.sleep(backoff)
            backoff = max(0, backoff - 15)
        else:
            time.sleep(max(2, args.poll))


if __name__ == "__main__":
    main()
