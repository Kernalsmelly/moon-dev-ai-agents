#!/usr/bin/env python3
"""Record forward returns for each launch signal.

Reads MEME_LAUNCH_SIGNALS_FILE incrementally and, for each new signal,
fetches price via Jupiter quotes at fixed horizons (e.g. 30s/2m/5m/15m).

Outputs JSONL to data/signal_outcomes.jsonl.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

import requests
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

_dotenv_override = (os.getenv("DOTENV_OVERRIDE", "1") or "1").strip().lower() in ("1", "true", "yes")
load_dotenv(dotenv_path=os.path.join(PROJECT_ROOT, ".env"), override=_dotenv_override)

from src.solana.rpc_pool import RpcError, RpcPool

BASE = "/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents"
SIGNALS_IN = os.getenv("MEME_LAUNCH_SIGNALS_FILE", f"{BASE}/data/meme_launch_signals.jsonl")
OUT = os.getenv("SIGNAL_OUTCOMES_FILE", f"{BASE}/data/signal_outcomes.jsonl")
STATE = os.getenv("SIGNAL_OUTCOME_STATE", f"{BASE}/data/signal_outcome_state.json")

JUPITER_QUOTE = "https://api.jup.ag/swap/v1/quote"
SOL_MINT = "So11111111111111111111111111111111111111112"

HORIZONS = os.getenv("SIGNAL_OUTCOME_HORIZONS", "30,120,300,900")
HORIZONS_S = [int(x.strip()) for x in HORIZONS.split(",") if x.strip().isdigit()]
POLL = max(2, int(os.getenv("SIGNAL_OUTCOME_POLL", "2")))

JUPITER_TIMEOUT_S = float(os.getenv("JUPITER_TIMEOUT_S", "15") or 15)
COINGECKO_TIMEOUT_S = float(os.getenv("COINGECKO_TIMEOUT_S", "10") or 10)
MAX_PENDING = max(10, int(os.getenv("SIGNAL_OUTCOME_MAX_PENDING", "300") or 300))
INGEST_PER_TICK = max(1, int(os.getenv("SIGNAL_OUTCOME_INGEST_PER_TICK", "50") or 50))
EVAL_PER_TICK = max(1, int(os.getenv("SIGNAL_OUTCOME_EVAL_PER_TICK", "12") or 12))


class NoRoute(RuntimeError):
    pass


class RateLimited(RuntimeError):
    pass


pool = RpcPool(timeout_s=12.0, max_attempts=3)

def _parse_supply_ui(value: dict[str, Any] | None) -> float | None:
    if not isinstance(value, dict):
        return None
    ui = value.get("uiAmount")
    if ui is not None:
        try:
            return float(ui)
        except Exception:
            return None
    # Some RPCs return uiAmount=null but provide uiAmountString.
    s = value.get("uiAmountString")
    if s is not None:
        try:
            return float(str(s))
        except Exception:
            return None
    return None


def token_supply_ui(mint: str) -> float | None:
    """Fetch total supply (uiAmount) for a mint via RPC pool."""
    try:
        res = pool.call("getTokenSupply", [mint])
        v = (res or {}).get("value") or {}
        return _parse_supply_ui(v)
    except RpcError:
        return None
    except Exception:
        return None


def load_state() -> dict[str, Any]:
    if os.path.exists(STATE):
        try:
            with open(STATE, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}
    return {}


def save_state(st: dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        with open(STATE, "w", encoding="utf-8") as fh:
            json.dump(st, fh)
    except Exception:
        pass


def build_signal_key(signal: dict[str, Any]) -> str | None:
    """Build a stable per-signal key so repeated mints can be tracked independently."""
    try:
        mint = str(signal.get("mint") or "").strip()
        if not mint:
            return None
        ts = float(signal.get("ts") or 0.0)
        if ts <= 0:
            return None
        metrics = signal.get("metrics") if isinstance(signal.get("metrics"), dict) else {}
        src = str(metrics.get("source") or signal.get("source") or "unknown").strip() or "unknown"
        sig = str(metrics.get("signature") or "").strip()
        # Round timestamp so equivalent JSON float representations hash to same key.
        ts_bucket = f"{ts:.6f}"
        if sig:
            return f"{mint}|{ts_bucket}|{src}|{sig}"
        return f"{mint}|{ts_bucket}|{src}"
    except Exception:
        return None


def migrate_pending_state(pending: dict[str, Any]) -> dict[str, Any]:
    """Migrate legacy mint-keyed pending map to signal-keyed entries."""
    out: dict[str, Any] = {}
    if not isinstance(pending, dict):
        return out
    for old_key, raw in pending.items():
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        mint = str(item.get("mint") or old_key or "").strip()
        if not mint:
            continue
        item["mint"] = mint
        try:
            ts = float(item.get("signal_ts") or 0.0)
        except Exception:
            ts = 0.0
        if ts <= 0:
            ts = time.time()
            item["signal_ts"] = ts
        metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
        src = str(metrics.get("source") or "unknown").strip() or "unknown"
        sig = str(metrics.get("signature") or "").strip()
        base = f"{mint}|{ts:.6f}|{src}"
        key = f"{base}|{sig}" if sig else base
        # If keys collide (rare), keep both by adding a deterministic suffix.
        if key in out:
            i = 2
            while f"{key}#{i}" in out:
                i += 1
            key = f"{key}#{i}"
        out[key] = item
    return out


_dec_cache: dict[str, int] = {}
_supply_cache: dict[str, float] = {}


def rpc_token_decimals(mint: str) -> int | None:
    if mint in _dec_cache:
        return _dec_cache[mint]
    try:
        res = pool.call("getTokenSupply", [mint]) or {}
        v = (res.get("value") or {})
        dec = int((v.get("decimals")))
        _dec_cache[mint] = dec
        # Cache supply from the same response to avoid extra RPC calls.
        su = _parse_supply_ui(v)
        if su is not None:
            _supply_cache[mint] = su
        return dec
    except RpcError as e:
        if e.kind == "rate_limited":
            raise RateLimited(str(e))
        return None
    except Exception:
        return None


_sol_usd_cache: tuple[float | None, float] = (None, 0.0)  # (price, ts)


def sol_usd_price() -> float | None:
    global _sol_usd_cache
    cached, ts = _sol_usd_cache
    now = time.time()
    if cached and (now - ts) < 60:
        return cached
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "solana", "vs_currencies": "usd"},
            timeout=COINGECKO_TIMEOUT_S,
        )
        if resp.status_code == 429:
            raise RateLimited("coingecko_429")
        if resp.status_code != 200:
            return None
        data = resp.json() or {}
        v = (data.get("solana") or {}).get("usd")
        out = float(v) if v else None
        _sol_usd_cache = (out, now)
        return out
    except Exception:
        return None


def jupiter_price_usd(mint: str, in_sol: float = 0.05) -> tuple[float | None, float | None]:
    dec = rpc_token_decimals(mint)
    if dec is None:
        raise NoRoute("no_decimals")
    amt = int(max(0.001, in_sol) * 1e9)
    params = {
        "inputMint": SOL_MINT,
        "outputMint": mint,
        "amount": str(amt),
        "slippageBps": "50",
    }
    headers = None
    jupiter_key = os.getenv("JUPITER_API_KEY") or os.getenv("JUPITER_KEY")
    if jupiter_key:
        headers = {"x-api-key": jupiter_key}
    try:
        resp = requests.get(JUPITER_QUOTE, params=params, headers=headers, timeout=JUPITER_TIMEOUT_S)
        if resp.status_code == 429:
            raise RateLimited("jupiter_429")
        if resp.status_code != 200:
            raise NoRoute(f"jupiter_http_{resp.status_code}")
        q = resp.json() or {}
        out_amt = q.get("outAmount")
        if not out_amt:
            raise NoRoute("no_outAmount")
        out_tokens = float(out_amt) / (10 ** dec)
        if out_tokens <= 0:
            raise NoRoute("out_tokens_le_0")
        price_sol = (amt / 1e9) / out_tokens
        sol_usd = sol_usd_price()
        if not sol_usd:
            raise NoRoute("no_sol_usd")
        price_usd = price_sol * sol_usd
        impact = None
        try:
            impact = float(q.get("priceImpactPct", 0) or 0)
        except Exception:
            impact = None
        return float(price_usd), impact
    except (NoRoute, RateLimited):
        raise
    except Exception as e:
        raise NoRoute(str(e))


def append_outcome(obj: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj) + "\n")


def main() -> int:
    print("signal_outcome_recorder: start", flush=True)
    st = load_state()
    offset = int(st.get("offset", 0) or 0)
    pending_raw = st.get("pending", {}) if isinstance(st.get("pending"), dict) else {}
    pending: dict[str, Any] = migrate_pending_state(pending_raw)
    if len(pending) > MAX_PENDING:
        # Keep the most recent signals so we don't get stuck labeling an unbounded backlog.
        items = sorted(
            pending.items(),
            key=lambda kv: float((kv[1] or {}).get("signal_ts", 0.0) if isinstance(kv[1], dict) else 0.0),
            reverse=True,
        )
        pending = dict(items[:MAX_PENDING])

    backoff = 0
    last_log = 0.0

    while True:
        # ingest new signals
        try:
            with open(SIGNALS_IN, "r", encoding="utf-8") as fh:
                if offset:
                    try:
                        if os.path.getsize(SIGNALS_IN) < offset:
                            offset = 0
                        fh.seek(offset)
                    except Exception:
                        offset = 0
                ingested = 0
                while True:
                    if len(pending) >= MAX_PENDING:
                        break
                    line = fh.readline()
                    if not line:
                        break
                    offset = fh.tell()
                    try:
                        sig = json.loads(line)
                    except Exception:
                        continue
                    mint = sig.get("mint")
                    ts = float(sig.get("ts", time.time()))
                    if not mint:
                        continue
                    signal_key = build_signal_key(sig)
                    if not signal_key:
                        continue
                    try:
                        signal_score = float(sig.get("score", 0.0) or 0.0)
                    except Exception:
                        signal_score = 0.0
                    metrics = sig.get("metrics") if isinstance(sig.get("metrics"), dict) else {}
                    if signal_key in pending:
                        continue
                    if len(pending) >= MAX_PENDING:
                        break
                    supply_cached = _supply_cache.get(mint)
                    pending[signal_key] = {
                        "mint": str(mint),
                        "signal_ts": ts,
                        "signal_score": signal_score,
                        "price0": None,
                        "impact0": None,
                        "supply_ui": supply_cached,
                        "done": [],
                        # Preserve the full metrics object so analysis can evolve
                        # (buy acceleration, concentration, etc.) without replaying signals.
                        "metrics": (metrics or {}) if isinstance(metrics, dict) else {},
                    }
                    ingested += 1
                    if ingested >= INGEST_PER_TICK:
                        break
        except Exception:
            pass

        now = time.time()
        # evaluate due horizons
        eval_budget = EVAL_PER_TICK
        for signal_key, info in list(pending.items()):
            if eval_budget <= 0:
                break
            mint = str(info.get("mint") or "").strip()
            if not mint:
                pending.pop(signal_key, None)
                continue
            sig_ts = float(info.get("signal_ts", now))
            p0_raw = info.get("price0")
            p0 = float(p0_raw or 0.0)
            done = set(info.get("done", []) if isinstance(info.get("done"), list) else [])
            if not p0_raw:
                try:
                    p0_new, imp0 = jupiter_price_usd(mint)
                    info["price0"] = p0_new
                    info["impact0"] = imp0
                    p0 = float(p0_new or 0.0)
                    if info.get("supply_ui") is None:
                        info["supply_ui"] = _supply_cache.get(mint) or token_supply_ui(mint)
                    eval_budget -= 1
                except RateLimited:
                    backoff = min(300, backoff * 2 + 2)
                    break
                except NoRoute:
                    # Unpriceable/untradable; drop it so it doesn't stall the labeling loop.
                    pending.pop(signal_key, None)
                    continue
                except Exception:
                    pending.pop(signal_key, None)
                    continue
            for h in HORIZONS_S:
                if h in done:
                    continue
                if now < (sig_ts + h):
                    continue
                try:
                    p1, imp1 = jupiter_price_usd(mint)
                    eval_budget -= 1
                except RateLimited:
                    backoff = min(300, backoff * 2 + 2)
                    break
                except NoRoute:
                    pending.pop(signal_key, None)
                    break
                except Exception:
                    pending.pop(signal_key, None)
                    break
                ret = None
                if p0 > 0:
                    ret = (float(p1) / p0) - 1.0
                supply_ui = info.get("supply_ui")
                mcap0 = (float(supply_ui) * float(p0)) if isinstance(supply_ui, (int, float)) and p0 > 0 else None
                mcap1 = (float(supply_ui) * float(p1)) if isinstance(supply_ui, (int, float)) and p1 > 0 else None
                m = info.get("metrics") if isinstance(info.get("metrics"), dict) else {}
                # Flat aliases improve compatibility with older analysis scripts
                # that do not parse the nested `metrics` object.
                try:
                    hits0 = int(m.get("hits") or 0) if "hits" in m else None
                except Exception:
                    hits0 = None
                try:
                    buys0 = int(m.get("buys") or 0) if "buys" in m else None
                except Exception:
                    buys0 = None
                try:
                    sells0 = int(m.get("sells") or 0) if "sells" in m else None
                except Exception:
                    sells0 = None
                try:
                    uniq0 = int(m.get("unique_buyers") or 0) if "unique_buyers" in m else None
                except Exception:
                    uniq0 = None
                try:
                    net_sol_in0 = float(m.get("net_sol_in") or 0.0) if "net_sol_in" in m else None
                except Exception:
                    net_sol_in0 = None
                try:
                    top_buyer_share0 = (
                        float(m.get("top_buyer_share"))
                        if m.get("top_buyer_share") is not None
                        else None
                    )
                except Exception:
                    top_buyer_share0 = None
                try:
                    liq0 = (
                        float(m.get("liquidity"))
                        if m.get("liquidity") is not None
                        else None
                    )
                except Exception:
                    liq0 = None
                append_outcome({
                    "ts": now,
                    "mint": mint,
                    "signal_key": signal_key,
                    "signal_ts": sig_ts,
                    "signal_score": info.get("signal_score"),
                    "score0": info.get("signal_score"),
                    "horizon_s": h,
                    "price0": p0,
                    "price1": p1,
                    "impact0": info.get("impact0"),
                    "impact1": imp1,
                    "ret": ret,
                    "source": "jupiter_quote",
                    "metrics": m or None,
                    "hits0": hits0,
                    "buys0": buys0,
                    "sells0": sells0,
                    "uniq0": uniq0,
                    "net_sol_in0": net_sol_in0,
                    "top_buyer_share0": top_buyer_share0,
                    "supply_ui": supply_ui,
                    "liq0": liq0,
                    "mcap0": mcap0,
                    "marketcap0": mcap0,
                    "marketcap1": mcap1,
                })
                done.add(h)
                info["done"] = sorted(done)
                if eval_budget <= 0:
                    break

            # remove after max horizon
            if all(h in done for h in HORIZONS_S):
                pending.pop(signal_key, None)

        if time.time() - last_log > 30:
            last_log = time.time()
            print(f"outcomes: pending={len(pending)} offset={offset} backoff={backoff}s", flush=True)

        save_state({"offset": offset, "pending": pending})

        if backoff > 0:
            time.sleep(backoff)
            backoff = max(0, int(backoff * 0.6))
        else:
            time.sleep(POLL)


if __name__ == "__main__":
    raise SystemExit(main())
