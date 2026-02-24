#!/usr/bin/env python3
"""WS listener for Raydium AMM v4 pool initialize events.

Goal: discovery of *tradable* new pairs (post-launch) without DexScreener/Birdeye.

Mechanism:
- Subscribe to `logsSubscribe` for the Raydium AMM v4 program id.
- Filter notifications by log lines that mention `initialize2` (cheap).
- Fetch the full transaction via RPC only for matching signatures.
- Extract coin_mint and pc_mint from the initialize2 accounts list, and emit a signal for
  the non-SOL mint.

This keeps request volume low and focuses the bot on where Jupiter routes exist sooner.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Any

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.solana.helius_ws import run_multi_log_subscribe
from src.solana.rpc_pool import RpcError, RpcPool
from src.meme_signal_schema import build_launch_signal_payload

DATA_DIR = os.path.join(PROJECT_ROOT, "data")

SIGNALS_OUT = os.getenv("MEME_LAUNCH_SIGNALS_FILE", os.path.join(DATA_DIR, "meme_launch_signals.jsonl"))
STATE_PATH = os.getenv("RAYDIUM_WS_STATE", os.path.join(DATA_DIR, "raydium_ws_state.json"))

# Raydium "classic" AMM v4 (initialize2) program id.
RAYDIUM_AMM_V4_PROGRAM_ID = os.getenv("RAYDIUM_AMM_PROGRAM_ID", "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8").strip()
# Raydium CPMM (v7) program id (initialize). Many newer launches use CPMM pools.
RAYDIUM_CPMM_PROGRAM_ID = os.getenv("RAYDIUM_CPMM_PROGRAM_ID", "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C").strip()

# Allow overriding the subscribe set via CSV. Defaults to AMM v4 + CPMM.
_pids_raw = os.getenv("RAYDIUM_WS_PROGRAM_IDS", "").strip()
if _pids_raw:
    RAYDIUM_PROGRAM_IDS = [p.strip() for p in _pids_raw.split(",") if p.strip()]
else:
    RAYDIUM_PROGRAM_IDS = [p for p in (RAYDIUM_AMM_V4_PROGRAM_ID, RAYDIUM_CPMM_PROGRAM_ID) if p]

WSOL_MINT = os.getenv("WSOL_MINT", "So11111111111111111111111111111111111111112").strip()

MAX_TX_PER_SEC = float(os.getenv("RAYDIUM_WS_MAX_TX_PER_SEC", "4") or 4)


def _load_state() -> dict[str, Any]:
    if not os.path.exists(STATE_PATH):
        return {"seen_sigs": []}
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as fh:
            st = json.load(fh) or {}
        if not isinstance(st, dict):
            return {"seen_sigs": []}
        st.setdefault("seen_sigs", [])
        return st
    except Exception:
        return {"seen_sigs": []}


def _save_state(seen_sigs: set[str]) -> None:
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as fh:
            json.dump({"seen_sigs": list(seen_sigs)[-8000:]}, fh)
    except Exception:
        pass


def _extract_pubkeys(message: dict) -> list[str]:
    keys = message.get("accountKeys") or []
    out: list[str] = []
    for k in keys:
        if isinstance(k, str):
            out.append(k)
        elif isinstance(k, dict) and isinstance(k.get("pubkey"), str):
            out.append(k["pubkey"])
    return out


def _emit_signal(mint: str, signature: str) -> None:
    now = time.time()
    payload = build_launch_signal_payload(
        mint=mint,
        score=3.0,
        ts=now,
        first_seen=now,
        metrics={
            "program_id": RAYDIUM_AMM_V4_PROGRAM_ID,
            "source": "raydium_pool",
            "signature": signature,
        },
    )
    os.makedirs(os.path.dirname(SIGNALS_OUT), exist_ok=True)
    with open(SIGNALS_OUT, "a", encoding="utf-8") as out:
        out.write(json.dumps(payload) + "\n")


def _pick_candidate_mint(coin_mint: str, pc_mint: str) -> str | None:
    # Most meme launches pair vs SOL (WSOL). If so, return the other side.
    if coin_mint == WSOL_MINT and pc_mint != WSOL_MINT:
        return pc_mint
    if pc_mint == WSOL_MINT and coin_mint != WSOL_MINT:
        return coin_mint
    # Otherwise skip for now (USDC pairs, etc. are still tradable, but we keep it simple).
    return None


def _mints_from_token_balances(tx: dict[str, Any]) -> set[str]:
    mints: set[str] = set()
    try:
        meta = (tx or {}).get("meta") or {}
        for k in ("preTokenBalances", "postTokenBalances"):
            arr = meta.get(k) or []
            if not isinstance(arr, list):
                continue
            for row in arr:
                if not isinstance(row, dict):
                    continue
                mint = row.get("mint")
                if isinstance(mint, str) and mint:
                    mints.add(mint)
    except Exception:
        pass
    return mints


async def main() -> None:
    pool = RpcPool(timeout_s=12.0, max_attempts=3)
    st = _load_state()
    seen_sigs = set(st.get("seen_sigs", []) if isinstance(st.get("seen_sigs"), list) else [])

    last_tx_call = 0.0
    backoff = 0
    cooldown_until = 0.0
    last_log = 0.0
    notif_count = 0
    init2_hits = 0
    init_hits = 0

    async def handle(msg: dict) -> None:
        nonlocal last_tx_call, backoff, cooldown_until, last_log, seen_sigs, notif_count, init2_hits, init_hits
        if msg.get("method") != "logsNotification":
            return
        now = time.time()
        notif_count += 1
        if now - last_log > 30:
            last_log = now
            _save_state(seen_sigs)
            print(
                f"raydium_ws heartbeat: notif={notif_count} init2={init2_hits} init={init_hits} "
                f"seen_sigs={len(seen_sigs)} backoff={backoff}s",
                flush=True,
            )
        if cooldown_until and now < cooldown_until:
            return

        params = msg.get("params", {}) or {}
        result = params.get("result", {}) or {}
        value = result.get("value", {}) or {}
        signature = value.get("signature")
        if not signature or signature in seen_sigs:
            return

        # Cheap pre-filter: only chase pool-init looking logs.
        # Note: logsSubscribe is on the program id, but the transaction logs still include
        # other programs' "InitializeAccount" strings. Keep this filter tight.
        logs = value.get("logs") or []
        pid_from_sub = msg.get("program_id")
        if isinstance(logs, list):
            joined = "\n".join([str(x) for x in logs[-30:]])
            jlow = joined.lower()
            # AMM v4 pool init typically logs "initialize2"
            if pid_from_sub == RAYDIUM_AMM_V4_PROGRAM_ID:
                if "initialize2" not in jlow:
                    return
                init2_hits += 1
            # CPMM pool init typically logs "instruction: initialize"
            elif pid_from_sub == RAYDIUM_CPMM_PROGRAM_ID:
                if "instruction: initialize" not in jlow and " initialize" not in jlow:
                    return
                init_hits += 1
            else:
                # Unknown Raydium program id; default to requiring initialize2 to avoid spam.
                if "initialize2" not in jlow:
                    return

        seen_sigs.add(signature)

        try:
            if MAX_TX_PER_SEC > 0:
                min_dt = 1.0 / MAX_TX_PER_SEC
                dt = time.time() - last_tx_call
                if dt < min_dt:
                    await asyncio.sleep(min_dt - dt)
            tx = pool.call(
                "getTransaction",
                [signature, {"encoding": "json", "maxSupportedTransactionVersion": 0}],
            )
            last_tx_call = time.time()
        except RpcError as e:
            if e.kind == "rate_limited":
                backoff = min(300, backoff + 10)
                cooldown_until = time.time() + backoff
            return
        except Exception:
            return

        try:
            message = (tx or {}).get("transaction", {}).get("message", {}) or {}
            keys = _extract_pubkeys(message)
            if not keys:
                return
            ixs = message.get("instructions") or []
            if not isinstance(ixs, list):
                return
            for ix in ixs:
                if not isinstance(ix, dict):
                    continue
                pid = None
                if isinstance(ix.get("programId"), str):
                    pid = ix.get("programId")
                elif isinstance(ix.get("programIdIndex"), int) and ix.get("programIdIndex") < len(keys):
                    pid = keys[int(ix.get("programIdIndex"))]
                if pid not in (RAYDIUM_AMM_V4_PROGRAM_ID, RAYDIUM_CPMM_PROGRAM_ID):
                    continue
                accounts = ix.get("accounts") or []
                if not isinstance(accounts, list) or len(accounts) < 21:
                    continue
                if pid == RAYDIUM_AMM_V4_PROGRAM_ID:
                    # AMM v4 initialize2 accounts order: [.., lp_mint, coin_mint, pc_mint, ..]
                    try:
                        coin_mint = keys[int(accounts[8])]
                        pc_mint = keys[int(accounts[9])]
                    except Exception:
                        continue
                    mint = _pick_candidate_mint(coin_mint, pc_mint)
                    if mint:
                        _emit_signal(mint, signature)
                        break
                if pid == RAYDIUM_CPMM_PROGRAM_ID:
                    # CPMM init layout differs. Use token balance mints as a practical heuristic:
                    # if WSOL is involved, emit the non-WSOL mint.
                    mints = _mints_from_token_balances(tx)
                    if not mints:
                        continue
                    if WSOL_MINT in mints:
                        other = [m for m in mints if m != WSOL_MINT]
                        if len(other) == 1:
                            _emit_signal(other[0], signature)
                            break
        finally:
            # heartbeat is handled above; keep this block for future per-sig debug if needed.
            pass

    while True:
        try:
            await run_multi_log_subscribe(RAYDIUM_PROGRAM_IDS, handle)
        except Exception as e:
            print(f"raydium_ws error: {e}", flush=True)
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
