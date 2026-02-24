#!/usr/bin/env python3
"""WS listener for Pump program logs that emits launch signals directly.

Uses WSS (HELIUS_WS_URL) for logsSubscribe and RPC_URL for getTransaction.
Writes to data/meme_launch_signals.jsonl and optional mints file.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from typing import Any
from collections import deque

from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Load `.env` when the script is launched directly (supervisor also loads it).
# Default behavior remains override=True; set DOTENV_OVERRIDE=0 for sidecar runs
# where process-level env vars must take precedence.
_dotenv_override = (os.getenv("DOTENV_OVERRIDE", "1") or "1").strip().lower() in ("1", "true", "yes")
load_dotenv(dotenv_path=os.path.join(PROJECT_ROOT, ".env"), override=_dotenv_override)

from src.solana.helius_ws import run_multi_log_subscribe, run_block_subscribe_mentions
from src.solana.rpc_pool import RpcError, RpcPool
from src.meme_signal_schema import build_launch_signal_payload
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

_signals_raw = (os.getenv("MEME_LAUNCH_SIGNALS_FILE") or "").strip()
SIGNALS_OUT = _signals_raw or os.path.join(DATA_DIR, "meme_launch_signals.jsonl")
_mints_raw = (os.getenv("MEME_LAUNCH_MINTS_FILE") or "").strip()
MINTS_OUT = _mints_raw or os.path.join(DATA_DIR, "meme_launch_mints.jsonl")
STATE_PATH = os.getenv("PUMP_WS_STATE", os.path.join(DATA_DIR, "pump_ws_state.json"))

PROGRAMS_FILE = os.getenv("PUMP_WS_PROGRAMS", os.path.join(PROJECT_ROOT, "config", "helius_programs.json"))

MAX_TX_PER_SEC = float(os.getenv("PUMP_WS_MAX_TX_PER_SEC", "6") or 6)
# Keep at least one-hit evaluation path active; MIN_HITS=1 should not switch to
# degraded-only mode.
MIN_HITS = max(1, int(os.getenv("PUMP_SIGNAL_MIN_HITS", "3") or 3))
HIT_WINDOW_S = float(os.getenv("PUMP_SIGNAL_HIT_WINDOW_S", "30") or 30)
MIN_BUYS = int(os.getenv("PUMP_SIGNAL_MIN_BUYS", "2") or 2)
MIN_NET_SOL_IN = float(os.getenv("PUMP_SIGNAL_MIN_NET_SOL_IN", "0.3") or 0.3)
# Allow strong net SOL inflow to bypass the buy-count gate (classification can undercount buys).
BUY_BYPASS_NET_SOL = float(os.getenv("PUMP_SIGNAL_BUY_BYPASS_NET_SOL", "0.5") or 0.5)
MAX_BUY_SAMPLES = int(os.getenv("PUMP_SIGNAL_MAX_BUY_SAMPLES", "50") or 50)
WALLET_SAMPLE_MAX = int(os.getenv("PUMP_SIGNAL_WALLET_SAMPLE_MAX", "4") or 4)
MIN_BUY_ACCEL = float(os.getenv("PUMP_SIGNAL_MIN_BUY_ACCEL", "0") or 0)  # buys/sec delta (0 disables)
MAX_TOP_BUYER_SHARE = float(os.getenv("PUMP_SIGNAL_MAX_TOP_BUYER_SHARE", "0.0") or 0.0)  # 0 disables
DROP_AFTER_HITS = int(os.getenv("PUMP_SIGNAL_DROP_AFTER_HITS", "15") or 15)
DROP_AFTER_AGE_S = float(os.getenv("PUMP_SIGNAL_DROP_AFTER_AGE_S", "25") or 25)
EMIT_MAX_HITS = int(os.getenv("PUMP_SIGNAL_EMIT_MAX_HITS", "0") or 0)  # 0 disables
EMIT_MAX_UNIQUE_BUYERS = int(os.getenv("PUMP_SIGNAL_EMIT_MAX_UNIQUE_BUYERS", "0") or 0)  # 0 disables
EMIT_MAX_NET_SOL_IN = float(os.getenv("PUMP_SIGNAL_EMIT_MAX_NET_SOL_IN", "0") or 0.0)  # 0 disables
EMIT_MAX_SELLS = int(os.getenv("PUMP_SIGNAL_EMIT_MAX_SELLS", "0") or 0)  # 0 disables
MIN_T_FIRST_SELL_S = float(os.getenv("PUMP_SIGNAL_MIN_T_FIRST_SELL_S", "0") or 0.0)  # 0 disables

SOL_LAMPORTS = 1_000_000_000
PUMP_WS_DEFER_TX_FETCH = (os.getenv("PUMP_WS_DEFER_TX_FETCH", "true") or "true").strip().lower() in ("1", "true", "yes")
PUMP_WS_EVAL_TX_SAMPLES = int(os.getenv("PUMP_WS_EVAL_TX_SAMPLES", "5") or 5)
PUMP_WS_REJECT_COOLDOWN_S = float(os.getenv("PUMP_WS_REJECT_COOLDOWN_S", "600") or 600)
PUMP_WS_USE_BLOCK_SUBSCRIBE = (os.getenv("PUMP_WS_USE_BLOCK_SUBSCRIBE", "true") or "true").strip().lower() in ("1", "true", "yes")
PUMP_WS_AUTO_FALLBACK_TO_LOGS = (os.getenv("PUMP_WS_AUTO_FALLBACK_TO_LOGS", "true") or "true").strip().lower() in ("1", "true", "yes")
# When false, do not emit "degraded" signals with missing demand metrics.
# This keeps the launch-signal stream high-quality and reduces downstream API waste.
PUMP_SIGNAL_ALLOW_DEGRADED_EMIT = (os.getenv("PUMP_SIGNAL_ALLOW_DEGRADED_EMIT", "false") or "false").strip().lower() in (
    "1",
    "true",
    "yes",
)

_PUMP_MINT_RE = re.compile(r"([1-9A-HJ-NP-Za-km-z]{32,44}pump)")


def _is_pump_mint(addr: Any) -> bool:
    if not isinstance(addr, str):
        return False
    if not addr.endswith("pump"):
        return False
    # Base58 pubkeys for mints are typically ~44 chars. Keep a narrow band.
    if len(addr) < 40 or len(addr) > 48:
        return False
    return True


def extract_mints_from_logs(logs: Any) -> list[str]:
    """Best-effort mint extraction from logsNotification 'logs' lines.

    This avoids calling getTransaction for every signature, which quickly burns free-tier RPC.
    If we can extract a mint here, we only fetch tx details when a mint has enough hits to evaluate.
    """
    if not isinstance(logs, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for ln in logs:
        if not isinstance(ln, str):
            continue
        for m in _PUMP_MINT_RE.findall(ln):
            if m not in seen and _is_pump_mint(m):
                seen.add(m)
                out.append(m)
    return out


def load_program_ids() -> list[str]:
    with open(PROGRAMS_FILE, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    pids = data.get("program_ids", [])
    if not pids:
        raise SystemExit("No program IDs configured")
    return pids


def load_state() -> set[str]:
    seen = set()
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as fh:
                st = json.load(fh)
                for m in st.get("seen_mints", []):
                    seen.add(m)
        except Exception:
            pass
    return seen


def save_state(seen: set[str]) -> None:
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as fh:
            json.dump({"seen_mints": list(seen)[-5000:]}, fh)
    except Exception:
        pass


def extract_mints(tx: dict) -> list[str]:
    mints: list[str] = []
    keys = tx.get("transaction", {}).get("message", {}).get("accountKeys", []) or []
    for k in keys:
        if isinstance(k, dict):
            addr = k.get("pubkey")
        else:
            addr = k
        if _is_pump_mint(addr):
            mints.append(addr)
    return mints


def _addr_from_key(k: Any) -> str | None:
    if isinstance(k, dict):
        v = k.get("pubkey")
        return v if isinstance(v, str) else None
    return k if isinstance(k, str) else None


def _fee_payer(tx: dict) -> str | None:
    keys = tx.get("transaction", {}).get("message", {}).get("accountKeys", []) or []
    if not keys:
        return None
    return _addr_from_key(keys[0])

def _signer_pubkeys(tx: dict) -> list[str]:
    msg = tx.get("transaction", {}).get("message", {}) or {}
    keys = msg.get("accountKeys", []) or []
    header = msg.get("header", {}) or {}
    n = header.get("numRequiredSignatures")
    try:
        n = int(n)
    except Exception:
        n = None
    out: list[str] = []
    if n is not None and n > 0:
        for k in keys[:n]:
            a = _addr_from_key(k)
            if a:
                out.append(a)
        return out
    # Fallback: some encodings include signer flags per key.
    for k in keys:
        if isinstance(k, dict) and k.get("signer"):
            a = _addr_from_key(k)
            if a:
                out.append(a)
    return out


def _sol_delta_for_owner(tx: dict, owner: str) -> float | None:
    meta = tx.get("meta", {}) or {}
    pre = meta.get("preBalances", []) or []
    post = meta.get("postBalances", []) or []
    keys = tx.get("transaction", {}).get("message", {}).get("accountKeys", []) or []
    addrs = [_addr_from_key(k) for k in keys]
    try:
        idx = addrs.index(owner)
    except Exception:
        return None
    if idx >= len(pre) or idx >= len(post):
        return None
    try:
        return (float(post[idx]) - float(pre[idx])) / SOL_LAMPORTS
    except Exception:
        return None


def _ui_amount(obj: Any) -> float:
    if not isinstance(obj, dict):
        return 0.0
    u = obj.get("uiTokenAmount") or {}
    if isinstance(u, dict):
        # Prefer uiAmount if present, fallback to amount + decimals.
        if u.get("uiAmount") is not None:
            try:
                return float(u.get("uiAmount") or 0.0)
            except Exception:
                return 0.0
        try:
            amt = float(u.get("amount") or 0.0)
            dec = int(u.get("decimals") or 0)
            return amt / (10 ** dec) if dec >= 0 else 0.0
        except Exception:
            return 0.0
    return 0.0


def _token_delta_for_owner(tx: dict, mint: str, owner: str) -> float | None:
    meta = tx.get("meta", {}) or {}
    pre = meta.get("preTokenBalances", []) or []
    post = meta.get("postTokenBalances", []) or []
    # Sum any token accounts for this owner+mint (can be multiple).
    pre_sum = 0.0
    post_sum = 0.0
    found = False
    for b in pre:
        if not isinstance(b, dict):
            continue
        if b.get("mint") != mint:
            continue
        if b.get("owner") != owner:
            continue
        pre_sum += _ui_amount(b)
        found = True
    for b in post:
        if not isinstance(b, dict):
            continue
        if b.get("mint") != mint:
            continue
        if b.get("owner") != owner:
            continue
        post_sum += _ui_amount(b)
        found = True
    if not found:
        return None
    return post_sum - pre_sum


def _classify_trade(sol_delta: float | None, token_delta: float | None) -> str:
    # Heuristic: buys spend SOL and gain token; sells gain SOL and lose token.
    if sol_delta is None or token_delta is None:
        return "unknown"
    if token_delta > 0 and sol_delta < -0.0005:
        return "buy"
    if token_delta < 0 and sol_delta > 0.0005:
        return "sell"
    return "unknown"

def _token_owner_deltas(tx: dict, mint: str) -> dict[str, float]:
    """Return owner->delta for token balance changes for the given mint."""
    meta = tx.get("meta", {}) or {}
    pre = meta.get("preTokenBalances", []) or []
    post = meta.get("postTokenBalances", []) or []
    owners: set[str] = set()
    for b in pre:
        if isinstance(b, dict) and b.get("mint") == mint and isinstance(b.get("owner"), str):
            owners.add(b["owner"])
    for b in post:
        if isinstance(b, dict) and b.get("mint") == mint and isinstance(b.get("owner"), str):
            owners.add(b["owner"])
    out: dict[str, float] = {}
    for o in owners:
        d = _token_delta_for_owner(tx, mint, o)
        if d is None:
            continue
        if abs(float(d)) < 1e-12:
            continue
        out[o] = float(d)
    return out


def _sol_spend_signers(tx: dict) -> float:
    """Estimate SOL spent in a tx by summing negative SOL deltas over signers."""
    spend = 0.0
    for s in _signer_pubkeys(tx):
        d = _sol_delta_for_owner(tx, s)
        if d is None:
            continue
        if d < 0:
            spend += (-d)
    return spend


def _top_wallet_samples(buyer_sol: dict[str, Any] | None, net_sol_in: float) -> tuple[list[str], list[float]]:
    """Build a compact top-wallet sample list from buyer SOL attribution."""
    if not isinstance(buyer_sol, dict) or not buyer_sol:
        return [], []
    try:
        ranked = sorted(
            ((str(k), float(v or 0.0)) for k, v in buyer_sol.items() if isinstance(k, str)),
            key=lambda kv: kv[1],
            reverse=True,
        )
    except Exception:
        return [], []
    wallets: list[str] = []
    shares: list[float] = []
    for w, spend in ranked:
        if not w:
            continue
        wallets.append(w)
        if net_sol_in > 0:
            shares.append(max(0.0, min(1.0, float(spend) / float(net_sol_in))))
        else:
            shares.append(0.0)
        if len(wallets) >= max(1, WALLET_SAMPLE_MAX):
            break
    return wallets, shares


def append_signal(
    mint: str,
    program_id: str,
    signature: str,
    *,
    hits: int | None = None,
    buys: int | None = None,
    sells: int | None = None,
    unique_buyers: int | None = None,
    net_sol_in: float | None = None,
    last_action: str | None = None,
    buy_accel: float | None = None,
    top_buyer_share: float | None = None,
    buy_p50_sol: float | None = None,
    buy_p90_sol: float | None = None,
    buy_max_sol: float | None = None,
    t_first_sell_s: float | None = None,
    buyer_wallets: list[str] | None = None,
    buyer_wallet_shares: list[float] | None = None,
) -> None:
    now = time.time()
    os.makedirs(os.path.dirname(SIGNALS_OUT), exist_ok=True)
    # Final safety: never emit signals that violate the emit caps, even if a caller path misses them.
    try:
        if EMIT_MAX_SELLS and sells and int(sells) > int(EMIT_MAX_SELLS):
            return
        if MIN_T_FIRST_SELL_S and t_first_sell_s is not None and float(t_first_sell_s) < float(MIN_T_FIRST_SELL_S):
            return
    except Exception:
        pass
    # Compute a lightweight 0-100 signal score so downstream can bucket outcomes.
    # This is intentionally heuristic and should be treated as "early demand quality",
    # not a guarantee of profitability.
    score = 0.0
    try:
        h = float(hits or 0)
        ub = float(unique_buyers or 0)
        nsi = float(net_sol_in or 0.0)
        ts = float(top_buyer_share) if top_buyer_share is not None else None
        ba = float(buy_accel) if buy_accel is not None else None
        tfs = float(t_first_sell_s) if t_first_sell_s is not None else None

        score += min(20.0, h * 3.0)          # 0..20
        score += min(20.0, ub * 5.0)         # 0..20
        if nsi >= 0.10:
            score += 10.0
        if nsi >= 0.25:
            score += 10.0
        if nsi >= 0.50:
            score += 10.0
        if nsi >= 1.00:
            score += 10.0                   # net_sol_in steps => 0..40

        if ba is not None:
            # buy_accel is approx (recent_buy_rate - earlier_buy_rate); bonus for sustained acceleration.
            score += max(0.0, min(10.0, ba * 20.0))

        if ts is not None:
            # Penalize single-wallet dominated flows.
            if ts >= 0.85:
                score -= 25.0
            elif ts >= 0.70:
                score -= 15.0
            elif ts >= 0.55:
                score -= 7.0

        if tfs is not None:
            # If the first sell arrives instantly, it's often a quick dump or wash.
            if tfs <= 10.0:
                score -= 12.0
            elif tfs <= 20.0:
                score -= 6.0

        if last_action == "sell":
            score -= 6.0
    except Exception:
        score = 0.0

    score = float(max(0.0, min(100.0, score)))

    payload = build_launch_signal_payload(
        mint=mint,
        score=score,
        ts=now,
        first_seen=now,
        metrics={
            "program_id": program_id,
            "source": "ws_logs",
            "signature": signature,
            "hits": hits,
            "hit_window_s": HIT_WINDOW_S,
            "buys": buys,
            "sells": sells,
            "unique_buyers": unique_buyers,
            "net_sol_in": net_sol_in,
            "last_action": last_action,
            "buy_accel": buy_accel,
            "top_buyer_share": top_buyer_share,
            "buy_p50_sol": buy_p50_sol,
            "buy_p90_sol": buy_p90_sol,
            "buy_max_sol": buy_max_sol,
            "t_first_sell_s": t_first_sell_s,
            "buyer_wallets": list(buyer_wallets or [])[: max(1, WALLET_SAMPLE_MAX)],
            "buyer_wallet_shares": list(buyer_wallet_shares or [])[: max(1, WALLET_SAMPLE_MAX)],
        },
    )

    with open(SIGNALS_OUT, "a", encoding="utf-8") as out:
        out.write(json.dumps(payload) + "\n")

    # optional mints file for debugging
    if MINTS_OUT and MINTS_OUT != "-":
        os.makedirs(os.path.dirname(MINTS_OUT), exist_ok=True)
        with open(MINTS_OUT, "a", encoding="utf-8") as mf:
            mf.write(
                json.dumps(
                    {
                        "ts": now,
                        "mint": mint,
                        "program_id": program_id,
                        "signature": signature,
                    }
                )
                + "\n"
            )


async def main() -> None:
    ws_env = (os.getenv("HELIUS_WS_URL") or os.getenv("HELIUS_WS") or "").strip()
    qn_env = (os.getenv("QUICKNODE_WSS_URL") or "").strip()
    print(
        f"env ws_url_starts_dollar={ws_env.startswith('$') or ws_env.startswith('${')} "
        f"quicknode_wss_set={bool(qn_env)} block_subscribe={PUMP_WS_USE_BLOCK_SUBSCRIBE} "
        f"auto_fallback_to_logs={PUMP_WS_AUTO_FALLBACK_TO_LOGS} "
        f"emit_max_sells={EMIT_MAX_SELLS} min_t_first_sell_s={MIN_T_FIRST_SELL_S} "
        f"allow_degraded_emit={PUMP_SIGNAL_ALLOW_DEGRADED_EMIT}",
        flush=True,
    )
    program_ids = load_program_ids()
    use_block_subscribe = bool(PUMP_WS_USE_BLOCK_SUBSCRIBE and len(program_ids) == 1)
    consecutive_stale = 0
    seen = load_state()
    last_log = 0.0
    backoff = 0
    pool = RpcPool(timeout_s=12.0, max_attempts=3)
    last_tx_call = 0.0
    cooldown_until = 0.0
    # Per-mint rolling window stats used to decide which mints are worth emitting.
    # Structure:
    #   {"first": ts, "hits": int, "buys": int, "sells": int, "net_sol_in": float, "buyers": set[str]}
    mint_hit_state: dict[str, dict[str, Any]] = {}
    recent_reject: dict[str, float] = {}
    # Periodic emit/debug stats so we can tune gates without guessing.
    stat_window_started = time.time()
    stat_emitted = 0
    stat_eval = 0
    stat_reject_buys = 0
    stat_reject_net = 0
    stat_reject_top = 0
    stat_reject_accel = 0
    stat_ws_msgs = 0
    stat_tx_calls = 0
    last_msg_at = time.time()

    # Keep the WS session honest. In blockSubscribe mode, we can otherwise sit
    # silently on a dead connection and the rest of the pipeline looks "idle".
    HEARTBEAT_S = float(os.getenv("PUMP_WS_HEARTBEAT_S", "15") or 15)
    STALE_S = float(os.getenv("PUMP_WS_STALE_S", "90") or 90)

    def _pct(xs: list[float], q: float) -> float | None:
        if not xs:
            return None
        ys = sorted(xs)
        if len(ys) == 1:
            return ys[0]
        k = (len(ys) - 1) * q
        f = int(k)
        c = min(len(ys) - 1, f + 1)
        if f == c:
            return ys[f]
        return ys[f] * (c - k) + ys[c] * (k - f)

    async def handle(msg: dict) -> None:
        nonlocal backoff, last_log, seen, last_tx_call, cooldown_until
        nonlocal stat_window_started, stat_emitted, stat_eval
        nonlocal stat_reject_buys, stat_reject_net, stat_reject_top, stat_reject_accel
        nonlocal stat_ws_msgs, stat_tx_calls
        nonlocal last_msg_at
        if msg.get("method") != "logsNotification":
            return
        last_msg_at = time.time()
        stat_ws_msgs += 1
        now = time.time()
        if cooldown_until and now < cooldown_until:
            return
        params = msg.get("params", {}) or {}
        result = params.get("result", {}) or {}
        value = result.get("value", {}) or {}
        signature = value.get("signature")
        logs = value.get("logs")
        program_id = msg.get("program_id") or ""
        if not signature:
            return

        tx_inline = msg.get("_tx")
        if isinstance(tx_inline, dict):
            tx = tx_inline
        else:
            tx = None

        # Fast-path: if we can identify the mint(s) from logs, we can defer expensive tx fetch.
        mints_from_logs = extract_mints_from_logs(logs)
        if tx is None and PUMP_WS_DEFER_TX_FETCH and mints_from_logs:
            for mint in mints_from_logs:
                if mint in seen:
                    continue
                until = recent_reject.get(mint, 0.0)
                if until and time.time() < until:
                    continue

                st = mint_hit_state.get(mint)
                if not st:
                    st = {
                        "first": time.time(),
                        "hits": 0,
                        "sigs": deque(maxlen=max(10, PUMP_WS_EVAL_TX_SAMPLES * 2)),
                    }
                    mint_hit_state[mint] = st
                now = time.time()
                if (now - float(st.get("first", now))) > HIT_WINDOW_S:
                    st["first"] = now
                    st["hits"] = 0
                    st["sigs"] = deque(maxlen=max(10, PUMP_WS_EVAL_TX_SAMPLES * 2))
                    st.pop("evaluated", None)

                st["hits"] = int(st.get("hits", 0) or 0) + 1
                try:
                    if isinstance(st.get("sigs"), deque):
                        st["sigs"].append(signature)
                except Exception:
                    pass

                if int(st.get("hits", 0) or 0) < MIN_HITS:
                    continue
                if st.get("evaluated"):
                    continue
                st["evaluated"] = True

                # Evaluate using a small sample of txs for this mint.
                # This is where we spend RPC: N calls per mint instead of 1 call per WS msg.
                eval_sigs = list(st.get("sigs") or [])[-max(1, PUMP_WS_EVAL_TX_SAMPLES) :]
                # Initialize rolling stats for evaluation.
                eval_state: dict[str, Any] = {
                    "first": float(st.get("first") or now),
                    "hits": int(st.get("hits", 0) or 0),
                    "buys": 0,
                    "sells": 0,
                    "net_sol_in": 0.0,
                    "buyers": set(),
                    "buy_sizes": deque(maxlen=max(5, MAX_BUY_SAMPLES)),
                    "buyer_sol": {},
                    "buy_times": deque(maxlen=max(5, MAX_BUY_SAMPLES)),
                    "first_sell_ts": None,
                }

                async def _fetch_tx(sig: str) -> dict | None:
                    nonlocal backoff, last_tx_call, cooldown_until, stat_tx_calls
                    try:
                        if MAX_TX_PER_SEC > 0:
                            min_dt = 1.0 / MAX_TX_PER_SEC
                            dt = time.time() - last_tx_call
                            if dt < min_dt:
                                await asyncio.sleep(min_dt - dt)
                        tx = await asyncio.to_thread(
                            pool.call,
                            "getTransaction",
                            [sig, {"encoding": "json", "maxSupportedTransactionVersion": 0}],
                        )
                        stat_tx_calls += 1
                        last_tx_call = time.time()
                        return tx
                    except RpcError as e:
                        if e.kind == "rate_limited":
                            backoff = min(300, backoff + 10)
                            cooldown_until = time.time() + backoff
                        return None
                    except Exception:
                        return None

                # Update eval_state from each tx (reuse existing attribution heuristics).
                for sig in eval_sigs:
                    tx = await _fetch_tx(sig)
                    if not tx:
                        continue
                    t_event = time.time()
                    payer = _fee_payer(tx)
                    action = "unknown"
                    sol_delta = None
                    tok_delta = None
                    if payer:
                        sol_delta = _sol_delta_for_owner(tx, payer)
                        tok_delta = _token_delta_for_owner(tx, mint, payer)
                        action = _classify_trade(sol_delta, tok_delta)
                        if action == "buy":
                            eval_state["buys"] = int(eval_state.get("buys", 0) or 0) + 1
                            if isinstance(eval_state.get("buyers"), set) and len(eval_state["buyers"]) < 200:
                                eval_state["buyers"].add(payer)
                            if sol_delta is not None and sol_delta < 0:
                                spend = (-sol_delta)
                                eval_state["net_sol_in"] = float(eval_state.get("net_sol_in", 0.0) or 0.0) + spend
                                try:
                                    eval_state["buy_sizes"].append(float(spend))
                                    eval_state["buy_times"].append(float(t_event))
                                    bs = eval_state.get("buyer_sol")
                                    if isinstance(bs, dict):
                                        bs[payer] = float(bs.get(payer, 0.0) or 0.0) + float(spend)
                                except Exception:
                                    pass
                        elif action == "sell":
                            eval_state["sells"] = int(eval_state.get("sells", 0) or 0) + 1
                            if eval_state.get("first_sell_ts") is None:
                                eval_state["first_sell_ts"] = float(t_event)
                    if action == "unknown":
                        try:
                            od = _token_owner_deltas(tx, mint)
                            pos = [o for (o, d) in od.items() if d > 0]
                            neg = [o for (o, d) in od.items() if d < 0]
                            if pos:
                                eval_state["buys"] = int(eval_state.get("buys", 0) or 0) + 1
                                if isinstance(eval_state.get("buyers"), set):
                                    for o in pos[:5]:
                                        if len(eval_state["buyers"]) >= 200:
                                            break
                                        eval_state["buyers"].add(o)
                                spend = _sol_spend_signers(tx)
                                if spend > 0:
                                    eval_state["net_sol_in"] = float(eval_state.get("net_sol_in", 0.0) or 0.0) + spend
                                    try:
                                        eval_state["buy_sizes"].append(float(spend))
                                        eval_state["buy_times"].append(float(t_event))
                                        bs = eval_state.get("buyer_sol")
                                        if isinstance(bs, dict):
                                            per = float(spend) / max(1, len(pos))
                                            for o in pos[:5]:
                                                bs[o] = float(bs.get(o, 0.0) or 0.0) + per
                                    except Exception:
                                        pass
                            elif neg:
                                eval_state["sells"] = int(eval_state.get("sells", 0) or 0) + 1
                                if eval_state.get("first_sell_ts") is None:
                                    eval_state["first_sell_ts"] = float(t_event)
                        except Exception:
                            pass

                hits = int(eval_state.get("hits", 0) or 0)
                buys = int(eval_state.get("buys", 0) or 0)
                sells = int(eval_state.get("sells", 0) or 0)
                unique_buyers = len(eval_state.get("buyers") or []) if isinstance(eval_state.get("buyers"), set) else 0
                net_sol_in = float(eval_state.get("net_sol_in", 0.0) or 0.0)

                # Derived features:
                buy_accel = None
                try:
                    bt = list(eval_state.get("buy_times") or [])
                    if len(bt) >= 6:
                        t0 = float(eval_state.get("first") or now)
                        t1 = float(bt[-1])
                        if t1 > t0:
                            mid = t0 + (t1 - t0) * 0.5
                            early = sum(1 for t in bt if t <= mid)
                            late = sum(1 for t in bt if t > mid)
                            early_dt = max(1e-3, mid - t0)
                            late_dt = max(1e-3, t1 - mid)
                            buy_accel = (late / late_dt) - (early / early_dt)
                except Exception:
                    buy_accel = None

                top_buyer_share = None
                buyer_wallets: list[str] = []
                buyer_wallet_shares: list[float] = []
                try:
                    bs = eval_state.get("buyer_sol")
                    if isinstance(bs, dict) and net_sol_in > 0:
                        top = max(float(v or 0.0) for v in bs.values()) if bs else 0.0
                        top_buyer_share = top / net_sol_in if net_sol_in > 0 else None
                        buyer_wallets, buyer_wallet_shares = _top_wallet_samples(bs, net_sol_in)
                except Exception:
                    top_buyer_share = None
                    buyer_wallets, buyer_wallet_shares = [], []

                buy_p50 = buy_p90 = buy_max = None
                try:
                    sizes = list(eval_state.get("buy_sizes") or [])
                    if sizes:
                        buy_p50 = _pct(sizes, 0.50)
                        buy_p90 = _pct(sizes, 0.90)
                        buy_max = max(sizes)
                except Exception:
                    pass

                t_first_sell_s = None
                try:
                    fst = eval_state.get("first_sell_ts")
                    if fst is not None:
                        t_first_sell_s = float(fst) - float(eval_state.get("first") or fst)
                except Exception:
                    t_first_sell_s = None

                stat_eval += 1
                rejected = False
                eff_buys = max(buys, unique_buyers)
                if eff_buys < MIN_BUYS and not (net_sol_in >= BUY_BYPASS_NET_SOL):
                    stat_reject_buys += 1
                    rejected = True
                if not rejected and net_sol_in < MIN_NET_SOL_IN:
                    stat_reject_net += 1
                    rejected = True
                if not rejected and MIN_BUY_ACCEL and buy_accel is not None and buy_accel < MIN_BUY_ACCEL:
                    stat_reject_accel += 1
                    rejected = True
                if not rejected and MAX_TOP_BUYER_SHARE and top_buyer_share is not None and top_buyer_share > MAX_TOP_BUYER_SHARE:
                    stat_reject_top += 1
                    rejected = True

                if rejected:
                    # Cooldown so we don't repeatedly spend tx calls on the same mint.
                    recent_reject[mint] = time.time() + PUMP_WS_REJECT_COOLDOWN_S
                    mint_hit_state.pop(mint, None)
                    continue

                # If we only satisfy demand gates after the mint is already overcrowded,
                # drop it instead of emitting a "late" signal.
                if EMIT_MAX_HITS and hits > EMIT_MAX_HITS:
                    recent_reject[mint] = time.time() + PUMP_WS_REJECT_COOLDOWN_S
                    mint_hit_state.pop(mint, None)
                    continue
                if EMIT_MAX_UNIQUE_BUYERS and unique_buyers and unique_buyers > EMIT_MAX_UNIQUE_BUYERS:
                    recent_reject[mint] = time.time() + PUMP_WS_REJECT_COOLDOWN_S
                    mint_hit_state.pop(mint, None)
                    continue
                if EMIT_MAX_NET_SOL_IN and net_sol_in and net_sol_in > EMIT_MAX_NET_SOL_IN:
                    recent_reject[mint] = time.time() + PUMP_WS_REJECT_COOLDOWN_S
                    mint_hit_state.pop(mint, None)
                    continue
                if EMIT_MAX_SELLS and sells and sells > EMIT_MAX_SELLS:
                    recent_reject[mint] = time.time() + PUMP_WS_REJECT_COOLDOWN_S
                    mint_hit_state.pop(mint, None)
                    continue
                if MIN_T_FIRST_SELL_S and t_first_sell_s is not None and t_first_sell_s < MIN_T_FIRST_SELL_S:
                    recent_reject[mint] = time.time() + PUMP_WS_REJECT_COOLDOWN_S
                    mint_hit_state.pop(mint, None)
                    continue

                append_signal(
                    mint,
                    program_id,
                    signature,
                    hits=hits,
                    buys=buys,
                    sells=sells,
                    unique_buyers=unique_buyers,
                    net_sol_in=net_sol_in,
                    last_action=None,
                    buy_accel=buy_accel,
                    top_buyer_share=top_buyer_share,
                    buy_p50_sol=buy_p50,
                    buy_p90_sol=buy_p90,
                    buy_max_sol=buy_max,
                    t_first_sell_s=t_first_sell_s,
                    buyer_wallets=buyer_wallets,
                    buyer_wallet_shares=buyer_wallet_shares,
                )
                stat_emitted += 1
                seen.add(mint)
                mint_hit_state.pop(mint, None)
            return

        if tx is None:
            try:
                if MAX_TX_PER_SEC > 0:
                    min_dt = 1.0 / MAX_TX_PER_SEC
                    dt = time.time() - last_tx_call
                    if dt < min_dt:
                        await asyncio.sleep(min_dt - dt)
                # Run blocking HTTP off the event loop. Blocking here causes WS keepalive ping timeouts.
                tx = await asyncio.to_thread(
                    pool.call,
                    "getTransaction",
                    [signature, {"encoding": "json", "maxSupportedTransactionVersion": 0}],
                )
                stat_tx_calls += 1
                last_tx_call = time.time()
            except RpcError as e:
                if e.kind == "rate_limited":
                    backoff = min(300, backoff + 10)
                    cooldown_until = time.time() + backoff
                return

        if not tx:
            return

        mints = extract_mints(tx)
        payer = _fee_payer(tx)
        for mint in mints:
            if mint in seen:
                continue
            until = recent_reject.get(mint, 0.0)
            if until and time.time() < until:
                continue
            # Always run the full per-tx demand evaluation path (including MIN_HITS=1).
            if MIN_HITS >= 1:
                st = mint_hit_state.get(mint)
                if not st:
                    st = {
                        "first": now,
                        "hits": 0,
                        "buys": 0,
                        "sells": 0,
                        "net_sol_in": 0.0,
                        "buyers": set(),
                        "buy_sizes": deque(maxlen=max(5, MAX_BUY_SAMPLES)),
                        "buyer_sol": {},
                        "buy_times": deque(maxlen=max(5, MAX_BUY_SAMPLES)),
                        "first_sell_ts": None,
                    }
                    mint_hit_state[mint] = st
                # reset window if stale
                if (now - float(st.get("first", now))) > HIT_WINDOW_S:
                    st["first"] = now
                    st["hits"] = 0
                    st["buys"] = 0
                    st["sells"] = 0
                    st["net_sol_in"] = 0.0
                    st["buyers"] = set()
                    st["buy_sizes"] = deque(maxlen=max(5, MAX_BUY_SAMPLES))
                    st["buyer_sol"] = {}
                    st["buy_times"] = deque(maxlen=max(5, MAX_BUY_SAMPLES))
                    st["first_sell_ts"] = None
                st["hits"] = int(st.get("hits", 0) or 0) + 1

                # Attach cheap early-demand features based on deltas for the fee payer.
                action = "unknown"
                sol_delta = None
                tok_delta = None
                if payer:
                    sol_delta = _sol_delta_for_owner(tx, payer)
                    tok_delta = _token_delta_for_owner(tx, mint, payer)
                    action = _classify_trade(sol_delta, tok_delta)
                    if action == "buy":
                        st["buys"] = int(st.get("buys", 0) or 0) + 1
                        if isinstance(st.get("buyers"), set) and len(st["buyers"]) < 200:
                            st["buyers"].add(payer)
                        if sol_delta is not None and sol_delta < 0:
                            spend = (-sol_delta)
                            st["net_sol_in"] = float(st.get("net_sol_in", 0.0) or 0.0) + spend
                            try:
                                if isinstance(st.get("buy_sizes"), deque):
                                    st["buy_sizes"].append(float(spend))
                                if isinstance(st.get("buy_times"), deque):
                                    st["buy_times"].append(float(now))
                                bs = st.get("buyer_sol")
                                if isinstance(bs, dict):
                                    bs[payer] = float(bs.get(payer, 0.0) or 0.0) + float(spend)
                            except Exception:
                                pass
                    elif action == "sell":
                        st["sells"] = int(st.get("sells", 0) or 0) + 1
                        if st.get("first_sell_ts") is None:
                            st["first_sell_ts"] = float(now)

                # Fallback classification: fee payer isn't always the buyer/seller.
                # Use token balance owner deltas and signer SOL spend to infer early demand.
                if action == "unknown":
                    try:
                        od = _token_owner_deltas(tx, mint)
                        pos = [o for (o, d) in od.items() if d > 0]
                        neg = [o for (o, d) in od.items() if d < 0]
                        if pos:
                            action = "buy"
                            st["buys"] = int(st.get("buys", 0) or 0) + 1
                            if isinstance(st.get("buyers"), set):
                                for o in pos[:5]:
                                    if len(st["buyers"]) >= 200:
                                        break
                                    st["buyers"].add(o)
                            spend = _sol_spend_signers(tx)
                            if spend > 0:
                                st["net_sol_in"] = float(st.get("net_sol_in", 0.0) or 0.0) + spend
                                try:
                                    if isinstance(st.get("buy_sizes"), deque):
                                        st["buy_sizes"].append(float(spend))
                                    if isinstance(st.get("buy_times"), deque):
                                        st["buy_times"].append(float(now))
                                    bs = st.get("buyer_sol")
                                    if isinstance(bs, dict):
                                        per = float(spend) / max(1, len(pos))
                                        for o in pos[:5]:
                                            bs[o] = float(bs.get(o, 0.0) or 0.0) + per
                                except Exception:
                                    pass
                        elif neg:
                            action = "sell"
                            st["sells"] = int(st.get("sells", 0) or 0) + 1
                            if st.get("first_sell_ts") is None:
                                st["first_sell_ts"] = float(now)
                    except Exception:
                        pass

                if int(st["hits"]) < MIN_HITS:
                    continue
                hits = int(st["hits"])
                buys = int(st.get("buys", 0) or 0)
                sells = int(st.get("sells", 0) or 0)
                unique_buyers = len(st.get("buyers") or []) if isinstance(st.get("buyers"), set) else 0
                net_sol_in = float(st.get("net_sol_in", 0.0) or 0.0)

                # Derived features:
                # - buy acceleration: compare recent vs earlier window buy rates
                buy_accel = None
                try:
                    bt = list(st.get("buy_times") or [])
                    if len(bt) >= 6:
                        t0 = float(st.get("first") or now)
                        t1 = float(bt[-1])
                        if t1 > t0:
                            mid = t0 + (t1 - t0) * 0.5
                            early = sum(1 for t in bt if t <= mid)
                            late = sum(1 for t in bt if t > mid)
                            early_dt = max(1e-3, mid - t0)
                            late_dt = max(1e-3, t1 - mid)
                            buy_accel = (late / late_dt) - (early / early_dt)
                except Exception:
                    buy_accel = None

                # - top buyer share (concentration)
                top_buyer_share = None
                buyer_wallets: list[str] = []
                buyer_wallet_shares: list[float] = []
                try:
                    bs = st.get("buyer_sol")
                    if isinstance(bs, dict) and net_sol_in > 0:
                        top = max(float(v or 0.0) for v in bs.values()) if bs else 0.0
                        top_buyer_share = top / net_sol_in if net_sol_in > 0 else None
                        buyer_wallets, buyer_wallet_shares = _top_wallet_samples(bs, net_sol_in)
                except Exception:
                    top_buyer_share = None
                    buyer_wallets, buyer_wallet_shares = [], []

                # - buy size stats
                buy_p50 = buy_p90 = buy_max = None
                try:
                    sizes = list(st.get("buy_sizes") or [])
                    if sizes:
                        buy_p50 = _pct(sizes, 0.50)
                        buy_p90 = _pct(sizes, 0.90)
                        buy_max = max(sizes)
                except Exception:
                    pass

                # - time to first sell
                t_first_sell_s = None
                try:
                    fst = st.get("first_sell_ts")
                    if fst is not None:
                        t_first_sell_s = float(fst) - float(st.get("first") or fst)
                except Exception:
                    t_first_sell_s = None

                # Only emit signals when there's evidence of actual buy demand, otherwise
                # we drown the bot in sell-side churn.
                stat_eval += 1
                rejected = False
                # Some encodings make it hard to attribute buys to the fee payer; treat
                # unique buyer count as a buy proxy to avoid discarding real demand.
                eff_buys = max(buys, unique_buyers)
                if eff_buys < MIN_BUYS and not (net_sol_in >= BUY_BYPASS_NET_SOL):
                    stat_reject_buys += 1
                    rejected = True
                if not rejected and net_sol_in < MIN_NET_SOL_IN:
                    stat_reject_net += 1
                    rejected = True
                if not rejected and MIN_BUY_ACCEL and buy_accel is not None and buy_accel < MIN_BUY_ACCEL:
                    stat_reject_accel += 1
                    rejected = True
                if not rejected and MAX_TOP_BUYER_SHARE and top_buyer_share is not None and top_buyer_share > MAX_TOP_BUYER_SHARE:
                    stat_reject_top += 1
                    rejected = True
                if rejected:
                    # If the early window isn't meeting demand requirements, drop it instead of
                    # letting hits accumulate and emitting an over-late, overcrowded signal.
                    try:
                        age = now - float(st.get("first") or now)
                    except Exception:
                        age = 0.0
                    if (DROP_AFTER_HITS and hits >= DROP_AFTER_HITS) or (DROP_AFTER_AGE_S and age >= DROP_AFTER_AGE_S):
                        recent_reject[mint] = time.time() + PUMP_WS_REJECT_COOLDOWN_S
                        mint_hit_state.pop(mint, None)
                    continue

                # Emit caps (mirrors bot-side caps). If we only manage to satisfy demand gates
                # when the mint is already crowded, drop it instead of emitting a late signal.
                if EMIT_MAX_HITS and hits > EMIT_MAX_HITS:
                    recent_reject[mint] = time.time() + PUMP_WS_REJECT_COOLDOWN_S
                    mint_hit_state.pop(mint, None)
                    continue
                if EMIT_MAX_UNIQUE_BUYERS and unique_buyers and unique_buyers > EMIT_MAX_UNIQUE_BUYERS:
                    recent_reject[mint] = time.time() + PUMP_WS_REJECT_COOLDOWN_S
                    mint_hit_state.pop(mint, None)
                    continue
                if EMIT_MAX_NET_SOL_IN and net_sol_in and net_sol_in > EMIT_MAX_NET_SOL_IN:
                    recent_reject[mint] = time.time() + PUMP_WS_REJECT_COOLDOWN_S
                    mint_hit_state.pop(mint, None)
                    continue
            else:
                if not PUMP_SIGNAL_ALLOW_DEGRADED_EMIT:
                    continue
                hits = 1
                buys = None
                sells = None
                unique_buyers = None
                net_sol_in = None
                action = "unknown"
                buy_accel = None
                top_buyer_share = None
                buy_p50 = None
                buy_p90 = None
                buy_max = None
                t_first_sell_s = None
                buyer_wallets = []
                buyer_wallet_shares = []

            append_signal(
                mint,
                program_id,
                signature,
                hits=hits,
                buys=buys,
                sells=sells,
                unique_buyers=unique_buyers,
                net_sol_in=net_sol_in,
                last_action=action,
                buy_accel=buy_accel,
                top_buyer_share=top_buyer_share,
                buy_p50_sol=buy_p50,
                buy_p90_sol=buy_p90,
                buy_max_sol=buy_max,
                t_first_sell_s=t_first_sell_s,
                buyer_wallets=buyer_wallets,
                buyer_wallet_shares=buyer_wallet_shares,
            )
            stat_emitted += 1
            seen.add(mint)
            mint_hit_state.pop(mint, None)

        if now - last_log > 30:
            last_log = now
            mode = "block" if use_block_subscribe else "logs"
            print(f"WS status: seen={len(seen)} backoff={backoff}s mode={mode}", flush=True)
            save_state(seen)

        # Emit a stats line every ~60s so we can tune thresholds when signal rate drops.
        if (now - stat_window_started) >= 60:
            dt = max(1.0, now - stat_window_started)
            rate_h = stat_emitted * (3600.0 / dt)
            print(
                "WS emit_stats "
                f"emitted={stat_emitted} rate_h={rate_h:.1f} eval={stat_eval} "
                f"rej_buys={stat_reject_buys} rej_net={stat_reject_net} rej_top={stat_reject_top} rej_accel={stat_reject_accel} "
                f"ws_msgs={stat_ws_msgs} tx_calls={stat_tx_calls} in_window={len(mint_hit_state)}",
                flush=True,
            )
            stat_window_started = now
            stat_emitted = 0
            stat_eval = 0
            stat_reject_buys = 0
            stat_reject_net = 0
            stat_reject_top = 0
            stat_reject_accel = 0
            stat_ws_msgs = 0
            stat_tx_calls = 0

    async def heartbeat() -> None:
        """Periodic status + stale-connection detector (especially important for blockSubscribe)."""
        nonlocal last_log, seen
        nonlocal stat_window_started, stat_emitted, stat_eval
        nonlocal stat_reject_buys, stat_reject_net, stat_reject_top, stat_reject_accel
        nonlocal stat_ws_msgs, stat_tx_calls
        nonlocal last_msg_at, backoff
        while True:
            await asyncio.sleep(max(1.0, HEARTBEAT_S))
            now = time.time()

            # Persist state + status line even when no messages arrive.
            if now - last_log > 30:
                last_log = now
                mode = "block" if use_block_subscribe else "logs"
                print(f"WS status: seen={len(seen)} backoff={backoff}s mode={mode}", flush=True)
                save_state(seen)

            if (now - stat_window_started) >= 60:
                dt = max(1.0, now - stat_window_started)
                rate_h = stat_emitted * (3600.0 / dt)
                print(
                    "WS emit_stats "
                    f"emitted={stat_emitted} rate_h={rate_h:.1f} eval={stat_eval} "
                    f"rej_buys={stat_reject_buys} rej_net={stat_reject_net} rej_top={stat_reject_top} rej_accel={stat_reject_accel} "
                    f"ws_msgs={stat_ws_msgs} tx_calls={stat_tx_calls} in_window={len(mint_hit_state)}",
                    flush=True,
                )
                stat_window_started = now
                stat_emitted = 0
                stat_eval = 0
                stat_reject_buys = 0
                stat_reject_net = 0
                stat_reject_top = 0
                stat_reject_accel = 0
                stat_ws_msgs = 0
                stat_tx_calls = 0

            # If we haven't seen any messages for a while, force a reconnect.
            stale_for = now - float(last_msg_at or now)
            if STALE_S > 0 and stale_for >= STALE_S:
                raise RuntimeError(f"stale_ws_no_msgs_for={stale_for:.1f}s")

    while True:
        try:
            # Reset stale timer per reconnect attempt so "stale_ws_no_msgs_for" reflects
            # the current connection, not a multi-hour monotonic counter.
            last_msg_at = time.time()
            hb_task = asyncio.create_task(heartbeat())
            sub_task: asyncio.Task
            if use_block_subscribe:
                pid = program_ids[0]

                async def _on_tx(tx: dict, sig: str) -> None:
                    await handle(
                        {
                            "method": "logsNotification",
                            "program_id": pid,
                            "_tx": tx,
                            "params": {"result": {"value": {"signature": sig}}},
                        }
                    )

                sub_task = asyncio.create_task(run_block_subscribe_mentions(pid, _on_tx))
            else:
                sub_task = asyncio.create_task(run_multi_log_subscribe(program_ids, handle))

            done, pending = await asyncio.wait({hb_task, sub_task}, return_when=asyncio.FIRST_EXCEPTION)
            for t in pending:
                t.cancel()
            for t in done:
                exc = t.exception()
                if exc:
                    raise exc
        except Exception as e:
            err = str(e)
            if "stale_ws_no_msgs_for=" in err and use_block_subscribe:
                consecutive_stale += 1
                if PUMP_WS_AUTO_FALLBACK_TO_LOGS and consecutive_stale >= 3:
                    use_block_subscribe = False
                    consecutive_stale = 0
                    print("WS mode fallback: blockSubscribe -> logsSubscribe (repeated stale sessions)", flush=True)
            elif "stale_ws_no_msgs_for=" not in err:
                consecutive_stale = 0
            # Keep the loop alive and add a small backoff so repeated reconnect failures
            # do not tight-loop and burn CPU / provider quotas.
            try:
                backoff = min(60, max(int(backoff or 0), 0) + 5)
            except Exception:
                backoff = 5
            print(f"WS error: {e}", flush=True)
            last_msg_at = time.time()
            await asyncio.sleep(min(30, max(5, int(backoff or 5))))


if __name__ == "__main__":
    asyncio.run(main())
