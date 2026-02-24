#!/usr/bin/env python3
"""Build a wallet allowlist from external leaderboard sources.

Primary goal:
- Pull top-trader/leaderboard style data (Axiom/GMGN templates/import feed).
- Normalize wallet rows.
- Maintain rolling wallet score state.
- Write allowlist JSON consumed by wallet_outlier_signal_listener.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from collections import deque
from typing import Any

import httpx
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
load_dotenv(dotenv_path=os.path.join(PROJECT_ROOT, ".env"), override=False)

DATA_DIR = os.path.join(PROJECT_ROOT, "data")
SIGNALS_FILE = (os.getenv("MEME_LAUNCH_SIGNALS_FILE") or "").strip() or os.path.join(DATA_DIR, "meme_launch_signals.jsonl")
STATE_FILE = (os.getenv("MEME_LEADERBOARD_STATE_FILE") or "").strip() or os.path.join(
    DATA_DIR, "leaderboard_wallet_discovery_state.json"
)
ALLOWLIST_FILE = (os.getenv("MEME_LEADERBOARD_ALLOWLIST_FILE") or "").strip() or os.path.join(
    DATA_DIR, "leaderboard_wallet_allowlist.json"
)
IMPORT_JSONL = (os.getenv("MEME_LEADERBOARD_IMPORT_JSONL") or "").strip() or os.path.join(
    DATA_DIR, "leaderboard_wallet_import.jsonl"
)

POLL_S = float(os.getenv("MEME_LEADERBOARD_POLL_S", "45") or 45)
SIGNAL_LOOKBACK = int(os.getenv("MEME_LEADERBOARD_SIGNAL_LOOKBACK", "3500") or 3500)
MAX_SEEDS = int(os.getenv("MEME_LEADERBOARD_MAX_SEEDS", "40") or 40)
MAX_HTTP_CALLS = int(os.getenv("MEME_LEADERBOARD_MAX_HTTP_CALLS", "80") or 80)
HTTP_TIMEOUT_S = float(os.getenv("MEME_LEADERBOARD_HTTP_TIMEOUT_S", "10") or 10)
HTTP_RETRY = int(os.getenv("MEME_LEADERBOARD_HTTP_RETRY", "1") or 1)

MIN_OBS = int(os.getenv("MEME_LEADERBOARD_MIN_OBS", "3") or 3)
MIN_SCORE = float(os.getenv("MEME_LEADERBOARD_MIN_SCORE", "67") or 67)
STALE_S = float(os.getenv("MEME_LEADERBOARD_STALE_S", "43200") or 43200)
MAX_WALLETS_STATE = int(os.getenv("MEME_LEADERBOARD_MAX_WALLETS_STATE", "15000") or 15000)
MAX_ALLOWLIST = int(os.getenv("MEME_LEADERBOARD_MAX_ALLOWLIST", "400") or 400)

ENABLE_AXIOM = str(os.getenv("MEME_LEADERBOARD_ENABLE_AXIOM", "true") or "true").lower() in ("1", "true", "yes")
ENABLE_GMGN = str(os.getenv("MEME_LEADERBOARD_ENABLE_GMGN", "true") or "true").lower() in ("1", "true", "yes")
ENABLE_SOLANATRACKER = str(os.getenv("MEME_LEADERBOARD_ENABLE_SOLANATRACKER", "true") or "true").lower() in (
    "1",
    "true",
    "yes",
)
ENABLE_IMPORT = str(os.getenv("MEME_LEADERBOARD_ENABLE_IMPORT", "true") or "true").lower() in ("1", "true", "yes")

AXIOM_HOSTS = [
    x.strip()
    for x in (
        os.getenv("MEME_LEADERBOARD_AXIOM_HOSTS")
        or "https://api.axiom.trade,https://api2.axiom.trade,https://api3.axiom.trade,https://api6.axiom.trade,https://api7.axiom.trade,https://api8.axiom.trade,https://api9.axiom.trade,https://api10.axiom.trade"
    ).split(",")
    if x.strip()
]
AXIOM_COOKIE = (os.getenv("AXIOM_COOKIE") or "").strip()
AXIOM_UA = (os.getenv("AXIOM_USER_AGENT") or "Mozilla/5.0").strip()

ENABLE_DEX_PAIR_RESOLVE = str(os.getenv("MEME_LEADERBOARD_ENABLE_DEX_PAIR_RESOLVE", "true") or "true").lower() in (
    "1",
    "true",
    "yes",
)
DEX_PAIR_LOOKUPS_PER_CYCLE = int(os.getenv("MEME_LEADERBOARD_DEX_PAIR_LOOKUPS_PER_CYCLE", "8") or 8)
DEX_PAIR_CACHE_MAX = int(os.getenv("MEME_LEADERBOARD_DEX_PAIR_CACHE_MAX", "5000") or 5000)

# Template receives {mint} and/or {pair}
GMGN_URL_TEMPLATES = [
    x.strip()
    for x in (
        os.getenv("MEME_LEADERBOARD_GMGN_URL_TEMPLATES")
        or "https://gmgn.ai/defi/quotation/v1/tokens/top_traders?chain=sol&address={mint}"
    ).split(",")
    if x.strip()
]
GMGN_COOKIE = (os.getenv("GMGN_COOKIE") or "").strip()
GMGN_UA = (os.getenv("GMGN_USER_AGENT") or AXIOM_UA).strip()

SOLANATRACKER_API_KEY = (os.getenv("SOLANATRACKER_API_KEY") or "").strip()
SOLANATRACKER_BASE_URL = (os.getenv("MEME_LEADERBOARD_SOLANATRACKER_BASE_URL") or "https://data.solanatracker.io").strip().rstrip("/")
SOLANATRACKER_PERIOD = (os.getenv("MEME_LEADERBOARD_SOLANATRACKER_PERIOD") or "24h").strip()
SOLANATRACKER_GLOBAL_LIMIT = int(os.getenv("MEME_LEADERBOARD_SOLANATRACKER_GLOBAL_LIMIT", "120") or 120)
SOLANATRACKER_TOKEN_LIMIT = int(os.getenv("MEME_LEADERBOARD_SOLANATRACKER_TOKEN_LIMIT", "80") or 80)
SOLANATRACKER_MAX_TOKEN_CALLS = int(os.getenv("MEME_LEADERBOARD_SOLANATRACKER_MAX_TOKEN_CALLS", "4") or 4)

BASELINE_WIN_RATE = float(os.getenv("MEME_LEADERBOARD_BASELINE_WIN_RATE", "0.50") or 0.50)
SIGNIFICANCE_MIN_TRADES = int(os.getenv("MEME_LEADERBOARD_SIGNIFICANCE_MIN_TRADES", "40") or 40)
SIGNIFICANCE_MIN_Z = float(os.getenv("MEME_LEADERBOARD_SIGNIFICANCE_MIN_Z", "2.0") or 2.0)
REQUIRE_SIGNIFICANCE = str(os.getenv("MEME_LEADERBOARD_REQUIRE_SIGNIFICANCE", "false") or "false").lower() in (
    "1",
    "true",
    "yes",
)

WALLET_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
SOL_ADDR_RE = WALLET_RE


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return float(default)
        return float(v)
    except Exception:
        return float(default)


def _to_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return int(default)
        return int(v)
    except Exception:
        return int(default)


def _normalize_win_rate(win_rate: float | None) -> float | None:
    if win_rate is None:
        return None
    wr = _to_float(win_rate)
    if wr > 1.0 and wr <= 100.0:
        wr = wr / 100.0
    return max(0.0, min(1.0, wr))


def _winrate_zscore(win_rate: float | None, trades: int | None, baseline: float) -> float | None:
    wr = _normalize_win_rate(win_rate)
    tr = _to_int(trades, 0) if trades is not None else 0
    if wr is None or tr <= 0:
        return None
    p0 = max(0.01, min(0.99, float(baseline)))
    se = math.sqrt(max(1e-9, p0 * (1.0 - p0) / float(max(1, tr))))
    return (wr - p0) / se


def _score_from_metrics(win_rate: float | None, pnl_usd: float | None, trades: int | None, source_hint: str) -> float:
    score = 50.0
    wr = _normalize_win_rate(win_rate)
    if wr is not None:
        score += (wr - 0.50) * 62.0
    if pnl_usd is not None:
        p = float(pnl_usd)
        if p >= 0:
            score += min(24.0, math.log1p(p) * 2.2)
        else:
            score -= min(18.0, math.log1p(abs(p)) * 2.0)
    if trades is not None and trades > 0:
        score += min(10.0, math.log1p(float(trades)) * 2.0)
        z = _winrate_zscore(wr, trades, BASELINE_WIN_RATE)
        if z is not None:
            score += max(-8.0, min(14.0, z * 2.0))
    if "gmgn" in source_hint.lower():
        score += 2.0
    if "solanatracker" in source_hint.lower():
        score += 3.0
    if "import" in source_hint.lower():
        score += 1.5
    return max(0.0, min(100.0, score))


def _load_state() -> dict[str, Any]:
    out: dict[str, Any] = {
        "import_offset": 0,
        "wallets": {},
        "pair_cache": {},
        "last_write_ts": 0.0,
    }
    if not os.path.exists(STATE_FILE):
        return out
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            raw = json.load(fh) or {}
        if isinstance(raw, dict):
            if isinstance(raw.get("import_offset"), (int, float)):
                out["import_offset"] = int(raw.get("import_offset") or 0)
            wallets = raw.get("wallets")
            if isinstance(wallets, dict):
                clean = {}
                for k, v in wallets.items():
                    if isinstance(k, str) and WALLET_RE.match(k) and isinstance(v, dict):
                        clean[k] = v
                out["wallets"] = clean
            pair_cache = raw.get("pair_cache")
            if isinstance(pair_cache, dict):
                clean_pairs: dict[str, str] = {}
                for mint, pair in pair_cache.items():
                    if isinstance(mint, str) and isinstance(pair, str) and SOL_ADDR_RE.match(mint) and SOL_ADDR_RE.match(pair):
                        clean_pairs[mint] = pair
                out["pair_cache"] = clean_pairs
            if isinstance(raw.get("last_write_ts"), (int, float)):
                out["last_write_ts"] = float(raw.get("last_write_ts") or 0.0)
    except Exception:
        return out
    return out


def _save_state(state: dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
    except Exception:
        pass


def _read_recent_seeds() -> list[dict[str, str]]:
    if not os.path.exists(SIGNALS_FILE):
        return []
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    try:
        with open(SIGNALS_FILE, "rb") as fh:
            lines = deque(fh, maxlen=max(10, SIGNAL_LOOKBACK))
        for raw in reversed(lines):
            try:
                obj = json.loads(raw.decode("utf-8", errors="ignore"))
            except Exception:
                continue
            mint = str(obj.get("mint") or "").strip()
            m = obj.get("metrics") if isinstance(obj.get("metrics"), dict) else {}
            pair = str(m.get("pair_address") or "").strip()
            if not mint and not pair:
                continue
            key = (mint, pair)
            if key in seen:
                continue
            seen.add(key)
            out.append({"mint": mint, "pair": pair})
            if len(out) >= max(1, MAX_SEEDS):
                break
    except Exception:
        return []
    return out


def _resolve_pair_addresses(client: httpx.Client, seeds: list[dict[str, str]], state: dict[str, Any]) -> int:
    if not ENABLE_DEX_PAIR_RESOLVE:
        return 0
    if not seeds:
        return 0

    cache = state.get("pair_cache")
    if not isinstance(cache, dict):
        cache = {}
    lookups = 0

    for seed in seeds:
        if lookups >= max(0, DEX_PAIR_LOOKUPS_PER_CYCLE):
            break
        mint = str(seed.get("mint") or "").strip()
        pair = str(seed.get("pair") or "").strip()
        if pair or not mint or not SOL_ADDR_RE.match(mint):
            continue
        cached = cache.get(mint)
        if isinstance(cached, str) and SOL_ADDR_RE.match(cached):
            seed["pair"] = cached
            continue

        try:
            resp = client.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=HTTP_TIMEOUT_S)
            lookups += 1
            if int(resp.status_code) != 200:
                continue
            obj = resp.json() if resp.content else {}
            pairs = obj.get("pairs") if isinstance(obj, dict) else []
            if not isinstance(pairs, list):
                continue

            best_pair = ""
            best_liq = -1.0
            for row in pairs:
                if not isinstance(row, dict):
                    continue
                chain_id = str(row.get("chainId") or "").strip().lower()
                if chain_id and chain_id != "solana":
                    continue
                p = str(row.get("pairAddress") or "").strip()
                if not SOL_ADDR_RE.match(p):
                    continue
                liq = _to_float(((row.get("liquidity") or {}).get("usd") if isinstance(row.get("liquidity"), dict) else 0.0), 0.0)
                if liq >= best_liq:
                    best_liq = liq
                    best_pair = p
            if best_pair:
                seed["pair"] = best_pair
                cache[mint] = best_pair
        except Exception:
            lookups += 1
            continue

    if len(cache) > max(1, DEX_PAIR_CACHE_MAX):
        # Keep cache bounded; preserve most recently inserted keys by re-insertion order.
        for k in list(cache.keys())[: max(0, len(cache) - DEX_PAIR_CACHE_MAX)]:
            cache.pop(k, None)

    state["pair_cache"] = cache
    return lookups


def _extract_wallet_rows(obj: Any, source: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    wallet_keys = ("wallet", "walletAddress", "wallet_address", "address", "trader", "maker", "publicKey")
    win_keys = ("winRate", "win_rate", "winPercentage", "win_percent", "wr")
    pnl_keys = ("pnl", "pnlUsd", "pnl_usd", "realizedPnl", "totalPnl", "profit")
    trades_keys = ("trades", "tradeCount", "txns", "transactions")

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            wallet = None
            for k in wallet_keys:
                v = x.get(k)
                if isinstance(v, str) and WALLET_RE.match(v.strip()):
                    wallet = v.strip()
                    break
            if wallet:
                wr = None
                pnl = None
                tr = None
                for k in win_keys:
                    if x.get(k) is not None:
                        wr = _to_float(x.get(k))
                        break
                for k in pnl_keys:
                    if x.get(k) is not None:
                        pnl = _to_float(x.get(k))
                        break
                for k in trades_keys:
                    if x.get(k) is not None:
                        tr = _to_int(x.get(k))
                        break
                if wr is None and isinstance(x.get("summary"), dict):
                    s = x.get("summary") or {}
                    for k in win_keys:
                        if s.get(k) is not None:
                            wr = _to_float(s.get(k))
                            break
                    for k in pnl_keys:
                        if s.get(k) is not None:
                            pnl = _to_float(s.get(k))
                            break
                    for k in trades_keys:
                        if s.get(k) is not None:
                            tr = _to_int(s.get(k))
                            break
                rows.append(
                    {
                        "wallet": wallet,
                        "win_rate": wr,
                        "pnl_usd": pnl,
                        "trades": tr,
                        "source": source,
                        "raw": x,
                    }
                )
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(obj)
    # de-dupe wallet rows per payload, keep first with most info
    best: dict[str, dict[str, Any]] = {}
    for r in rows:
        w = r["wallet"]
        old = best.get(w)
        if old is None:
            best[w] = r
            continue
        old_score = sum(1 for k in ("win_rate", "pnl_usd", "trades") if old.get(k) is not None)
        new_score = sum(1 for k in ("win_rate", "pnl_usd", "trades") if r.get(k) is not None)
        if new_score > old_score:
            best[w] = r
    return list(best.values())


def _http_get_json(client: httpx.Client, url: str, params: dict[str, Any], headers: dict[str, str] | None = None) -> tuple[int, Any]:
    attempt = 0
    while True:
        try:
            resp = client.get(url, params=params, headers=headers, timeout=HTTP_TIMEOUT_S)
            status = int(resp.status_code)
            if status == 200:
                try:
                    return status, resp.json()
                except Exception:
                    return status, None
            if status in (401, 403, 404):
                return status, None
            if attempt >= max(0, HTTP_RETRY):
                return status, None
            attempt += 1
            time.sleep(0.25 * attempt)
        except Exception:
            if attempt >= max(0, HTTP_RETRY):
                return 0, None
            attempt += 1
            time.sleep(0.25 * attempt)


def _http_post_json(
    client: httpx.Client, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
) -> tuple[int, Any]:
    attempt = 0
    while True:
        try:
            resp = client.post(url, json=payload, headers=headers, timeout=HTTP_TIMEOUT_S)
            status = int(resp.status_code)
            if status == 200:
                try:
                    return status, resp.json()
                except Exception:
                    return status, None
            if status in (401, 403, 404):
                return status, None
            if attempt >= max(0, HTTP_RETRY):
                return status, None
            attempt += 1
            time.sleep(0.25 * attempt)
        except Exception:
            if attempt >= max(0, HTTP_RETRY):
                return 0, None
            attempt += 1
            time.sleep(0.25 * attempt)


def _query_axiom(client: httpx.Client, seeds: list[dict[str, str]], budget: int) -> tuple[list[dict[str, Any]], int, dict[int, int]]:
    if not ENABLE_AXIOM or budget <= 0:
        return [], 0, {}
    out: list[dict[str, Any]] = []
    calls = 0
    statuses: dict[int, int] = {}

    headers = {
        "user-agent": AXIOM_UA,
        "accept": "application/json,text/plain,*/*",
        "origin": "https://axiom.trade",
        "referer": "https://axiom.trade/",
    }
    if AXIOM_COOKIE:
        headers["cookie"] = AXIOM_COOKIE

    paths_get = ["/top-traders-v5", "/top-traders-v4", "/top-traders", "/api/top-traders-v5", "/api/top-traders"]
    for seed in seeds:
        mint = seed.get("mint") or ""
        pair = seed.get("pair") or ""
        if not mint and not pair:
            continue
        for host in AXIOM_HOSTS:
            host = host.rstrip("/")
            for path in paths_get:
                if calls >= budget:
                    return out, calls, statuses
                calls += 1
                params = {}
                # v5 routes mostly expect pairAddress; some older routes use tokenAddress.
                if pair and "v5" in path:
                    params["pairAddress"] = pair
                elif mint:
                    params["tokenAddress"] = mint
                elif pair:
                    params["pairAddress"] = pair
                # try both modes expected by Axiom UI.
                params["onlyTrackedWallets"] = "false"
                status, obj = _http_get_json(client, f"{host}{path}", params=params, headers=headers)
                statuses[status] = _to_int(statuses.get(status), 0) + 1
                if status != 200 or obj is None:
                    continue
                rows = _extract_wallet_rows(obj, source="axiom")
                out.extend(rows)

                if calls >= budget:
                    return out, calls, statuses
                calls += 1
                params2 = dict(params)
                params2["onlyTrackedWallets"] = "true"
                status2, obj2 = _http_get_json(client, f"{host}{path}", params=params2, headers=headers)
                statuses[status2] = _to_int(statuses.get(status2), 0) + 1
                if status2 == 200 and obj2 is not None:
                    out.extend(_extract_wallet_rows(obj2, source="axiom"))

                # Trader tx context from the best first wallets.
                top_wallets = [r["wallet"] for r in rows[: min(8, len(rows))] if isinstance(r.get("wallet"), str)]
                if top_wallets and pair and mint and calls < budget:
                    calls += 1
                    post_path = "/transactions-from-traders-v2"
                    status3, obj3 = _http_post_json(
                        client,
                        f"{host}{post_path}",
                        payload={"pairAddress": pair, "tokenAddress": mint, "traders": top_wallets},
                        headers=headers,
                    )
                    statuses[status3] = _to_int(statuses.get(status3), 0) + 1
                    if status3 == 200 and obj3 is not None:
                        out.extend(_extract_wallet_rows(obj3, source="axiom_tx"))
    return out, calls, statuses


def _query_gmgn(client: httpx.Client, seeds: list[dict[str, str]], budget: int) -> tuple[list[dict[str, Any]], int, dict[int, int]]:
    if not ENABLE_GMGN or budget <= 0:
        return [], 0, {}
    out: list[dict[str, Any]] = []
    calls = 0
    statuses: dict[int, int] = {}
    headers = {
        "user-agent": GMGN_UA,
        "accept": "application/json,text/plain,*/*",
        "origin": "https://gmgn.ai",
        "referer": "https://gmgn.ai/",
    }
    if GMGN_COOKIE:
        headers["cookie"] = GMGN_COOKIE
    for seed in seeds:
        mint = seed.get("mint") or ""
        pair = seed.get("pair") or ""
        if not mint and not pair:
            continue
        for tpl in GMGN_URL_TEMPLATES:
            if calls >= budget:
                return out, calls, statuses
            url = tpl.replace("{mint}", mint).replace("{pair}", pair)
            if not url.startswith("http"):
                continue
            calls += 1
            status, obj = _http_get_json(client, url, params={}, headers=headers)
            statuses[status] = _to_int(statuses.get(status), 0) + 1
            if status == 200 and obj is not None:
                out.extend(_extract_wallet_rows(obj, source="gmgn"))
    return out, calls, statuses


def _query_solanatracker(
    client: httpx.Client, seeds: list[dict[str, str]], budget: int
) -> tuple[list[dict[str, Any]], int, dict[int, int]]:
    if not ENABLE_SOLANATRACKER or budget <= 0:
        return [], 0, {}
    if not SOLANATRACKER_API_KEY:
        # Keep explicit status marker for observability in logs.
        return [], 0, {-1: 1}

    out: list[dict[str, Any]] = []
    calls = 0
    statuses: dict[int, int] = {}
    headers = {
        "accept": "application/json,text/plain,*/*",
        "user-agent": AXIOM_UA,
        "x-api-key": SOLANATRACKER_API_KEY,
    }

    def call(path: str, params: dict[str, Any]) -> None:
        nonlocal calls
        if calls >= budget:
            return
        calls += 1
        status, obj = _http_get_json(client, f"{SOLANATRACKER_BASE_URL}{path}", params=params, headers=headers)
        statuses[status] = _to_int(statuses.get(status), 0) + 1
        if status == 200 and obj is not None:
            out.extend(_extract_wallet_rows(obj, source="solanatracker"))

    global_params: dict[str, Any] = {
        "period": SOLANATRACKER_PERIOD,
        "limit": max(1, SOLANATRACKER_GLOBAL_LIMIT),
    }
    call("/top-traders/all", global_params)
    if calls >= budget:
        return out, calls, statuses

    token_calls = 0
    for seed in seeds:
        if calls >= budget or token_calls >= max(0, SOLANATRACKER_MAX_TOKEN_CALLS):
            break
        mint = str(seed.get("mint") or "").strip()
        if not mint or not SOL_ADDR_RE.match(mint):
            continue
        token_calls += 1
        call(
            f"/top-traders/{mint}",
            {
                "period": SOLANATRACKER_PERIOD,
                "limit": max(1, SOLANATRACKER_TOKEN_LIMIT),
            },
        )

    return out, calls, statuses


def _ingest_import_feed(state: dict[str, Any]) -> int:
    if not ENABLE_IMPORT:
        return 0
    if not IMPORT_JSONL or not os.path.exists(IMPORT_JSONL):
        return 0
    offset = _to_int(state.get("import_offset"), 0)
    rows = 0
    try:
        with open(IMPORT_JSONL, "r", encoding="utf-8") as fh:
            if offset > 0:
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
                ext_rows = _extract_wallet_rows(obj, source="import")
                _update_wallet_state(state, ext_rows)
                rows += len(ext_rows)
    except Exception:
        pass
    state["import_offset"] = offset
    return rows


def _update_wallet_state(state: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    wallets = state.get("wallets")
    if not isinstance(wallets, dict):
        wallets = {}
        state["wallets"] = wallets
    now = time.time()
    for row in rows:
        wallet = str(row.get("wallet") or "").strip()
        if not WALLET_RE.match(wallet):
            continue
        wr = row.get("win_rate")
        pnl = row.get("pnl_usd")
        tr = row.get("trades")
        src = str(row.get("source") or "unknown")
        obs_score = _score_from_metrics(
            win_rate=_to_float(wr) if wr is not None else None,
            pnl_usd=_to_float(pnl) if pnl is not None else None,
            trades=_to_int(tr) if tr is not None else None,
            source_hint=src,
        )
        cur = wallets.get(wallet)
        if not isinstance(cur, dict):
            cur = {
                "n": 0,
                "score_ema": obs_score,
                "score_mean": 0.0,
                "impossible_ema": obs_score,
                "win_rate_ema": None,
                "pnl_ema": None,
                "trades_ema": None,
                "z_ema": None,
                "pnl_per_trade_ema": None,
                "last_seen": 0.0,
                "sources": {},
            }
        n_prev = _to_int(cur.get("n"), 0)
        n_new = n_prev + 1
        alpha = 0.2
        cur["score_ema"] = _to_float(cur.get("score_ema"), obs_score) + alpha * (obs_score - _to_float(cur.get("score_ema"), obs_score))
        cur["score_mean"] = ((_to_float(cur.get("score_mean"), 0.0) * n_prev) + obs_score) / float(max(1, n_new))
        cur["impossible_ema"] = _to_float(cur.get("impossible_ema"), obs_score) + alpha * (
            obs_score - _to_float(cur.get("impossible_ema"), obs_score)
        )
        if wr is not None:
            wr_f = _to_float(wr)
            if wr_f > 1.0 and wr_f <= 100.0:
                wr_f = wr_f / 100.0
            prev = cur.get("win_rate_ema")
            cur["win_rate_ema"] = wr_f if prev is None else (_to_float(prev) + alpha * (wr_f - _to_float(prev)))
        if pnl is not None:
            pnl_f = _to_float(pnl)
            prev = cur.get("pnl_ema")
            cur["pnl_ema"] = pnl_f if prev is None else (_to_float(prev) + alpha * (pnl_f - _to_float(prev)))
        if tr is not None:
            tr_f = float(_to_int(tr))
            prev = cur.get("trades_ema")
            cur["trades_ema"] = tr_f if prev is None else (_to_float(prev) + alpha * (tr_f - _to_float(prev)))
            tr_i = _to_int(tr, 0)
            if tr_i > 0 and pnl is not None:
                per_trade = _to_float(pnl) / float(max(1, tr_i))
                prev = cur.get("pnl_per_trade_ema")
                cur["pnl_per_trade_ema"] = per_trade if prev is None else (_to_float(prev) + alpha * (per_trade - _to_float(prev)))
            z = _winrate_zscore(wr, tr_i, BASELINE_WIN_RATE)
            if z is not None:
                prev = cur.get("z_ema")
                cur["z_ema"] = z if prev is None else (_to_float(prev) + alpha * (z - _to_float(prev)))
        cur["n"] = n_new
        cur["last_seen"] = now
        src_map = cur.get("sources")
        if not isinstance(src_map, dict):
            src_map = {}
        src_map[src] = _to_int(src_map.get(src), 0) + 1
        cur["sources"] = src_map
        wallets[wallet] = cur
    state["wallets"] = wallets


def _prune_wallets(state: dict[str, Any]) -> None:
    wallets = state.get("wallets")
    if not isinstance(wallets, dict):
        return
    now = time.time()
    stale_cut = now - max(1.0, STALE_S * 3.0)
    for w, v in list(wallets.items()):
        if not isinstance(v, dict):
            wallets.pop(w, None)
            continue
        if _to_float(v.get("last_seen"), 0.0) < stale_cut:
            wallets.pop(w, None)
    if len(wallets) > MAX_WALLETS_STATE:
        ranked = sorted(
            wallets.items(),
            key=lambda kv: (_to_float((kv[1] or {}).get("score_ema"), 0.0), _to_int((kv[1] or {}).get("n"), 0)),
            reverse=True,
        )
        keep = {k for k, _ in ranked[:MAX_WALLETS_STATE]}
        for w in list(wallets.keys()):
            if w not in keep:
                wallets.pop(w, None)
    state["wallets"] = wallets


def _write_allowlist(state: dict[str, Any]) -> int:
    wallets = state.get("wallets")
    if not isinstance(wallets, dict):
        return 0
    now = time.time()
    ranked: list[dict[str, Any]] = []
    for w, v in wallets.items():
        if not isinstance(v, dict):
            continue
        n = _to_int(v.get("n"), 0)
        score_ema = _to_float(v.get("score_ema"), 0.0)
        impossible_ema = _to_float(v.get("impossible_ema"), score_ema)
        s = max(score_ema, impossible_ema)
        last = _to_float(v.get("last_seen"), 0.0)
        wr_ema = None if v.get("win_rate_ema") is None else _to_float(v.get("win_rate_ema"))
        pnl_ema = None if v.get("pnl_ema") is None else _to_float(v.get("pnl_ema"))
        trades_ema = None if v.get("trades_ema") is None else _to_float(v.get("trades_ema"))
        z_ema = None if v.get("z_ema") is None else _to_float(v.get("z_ema"))
        pnl_per_trade_ema = None if v.get("pnl_per_trade_ema") is None else _to_float(v.get("pnl_per_trade_ema"))
        if n < MIN_OBS:
            continue
        if s < MIN_SCORE:
            continue
        if REQUIRE_SIGNIFICANCE:
            if trades_ema is None or trades_ema < float(max(1, SIGNIFICANCE_MIN_TRADES)):
                continue
            if z_ema is None or z_ema < SIGNIFICANCE_MIN_Z:
                continue
        if STALE_S > 0 and last > 0 and (now - last) > STALE_S:
            continue
        ranked.append(
            {
                "wallet": w,
                "score": round(s, 2),
                "score_ema": round(score_ema, 2),
                "impossible_ema": round(impossible_ema, 2),
                "n": n,
                "last_seen": last,
                "score_mean": round(_to_float(v.get("score_mean")), 2),
                "win_rate_ema": None if wr_ema is None else round(wr_ema, 4),
                "pnl_ema": None if pnl_ema is None else round(pnl_ema, 4),
                "trades_ema": None if trades_ema is None else round(trades_ema, 3),
                "z_ema": None if z_ema is None else round(z_ema, 3),
                "pnl_per_trade_ema": None if pnl_per_trade_ema is None else round(pnl_per_trade_ema, 5),
                "sources": v.get("sources") if isinstance(v.get("sources"), dict) else {},
            }
        )
    ranked.sort(key=lambda x: (float(x["score"]), int(x["n"])), reverse=True)
    ranked = ranked[: max(1, MAX_ALLOWLIST)]

    by_wallet = {r["wallet"]: {k: v for k, v in r.items() if k != "wallet"} for r in ranked}
    out = {
        "ts": time.time(),
        "count": len(ranked),
        "params": {
            "min_obs": MIN_OBS,
            "min_score": MIN_SCORE,
            "stale_s": STALE_S,
            "baseline_win_rate": BASELINE_WIN_RATE,
            "require_significance": REQUIRE_SIGNIFICANCE,
            "significance_min_trades": SIGNIFICANCE_MIN_TRADES,
            "significance_min_z": SIGNIFICANCE_MIN_Z,
        },
        "wallets": ranked,
        "by_wallet": by_wallet,
    }
    try:
        os.makedirs(os.path.dirname(ALLOWLIST_FILE), exist_ok=True)
        with open(ALLOWLIST_FILE, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2)
    except Exception:
        return 0
    return len(ranked)


def main() -> int:
    state = _load_state()
    last_log = 0.0
    with httpx.Client() as client:
        while True:
            now = time.time()
            seeds = _read_recent_seeds()
            pair_lookups = _resolve_pair_addresses(client, seeds, state)
            total_budget = max(1, MAX_HTTP_CALLS)
            budget_map = {"axiom": 0, "gmgn": 0, "solanatracker": 0}
            enabled_names: list[str] = []
            if ENABLE_AXIOM:
                enabled_names.append("axiom")
            if ENABLE_GMGN:
                enabled_names.append("gmgn")
            if ENABLE_SOLANATRACKER:
                enabled_names.append("solanatracker")
            if enabled_names:
                per = total_budget // len(enabled_names)
                rem = total_budget % len(enabled_names)
                for idx, name in enumerate(enabled_names):
                    budget_map[name] = per + (1 if idx < rem else 0)
            total_rows = 0
            calls = 0

            rows_a, calls_a, axiom_status = _query_axiom(client, seeds, budget=budget_map["axiom"])
            calls += calls_a
            total_rows += len(rows_a)
            _update_wallet_state(state, rows_a)

            rows_g, calls_g, gmgn_status = _query_gmgn(client, seeds, budget=budget_map["gmgn"])
            calls += calls_g
            total_rows += len(rows_g)
            _update_wallet_state(state, rows_g)

            rows_st, calls_st, st_status = _query_solanatracker(client, seeds, budget=budget_map["solanatracker"])
            calls += calls_st
            total_rows += len(rows_st)
            _update_wallet_state(state, rows_st)

            import_rows = _ingest_import_feed(state)
            _prune_wallets(state)
            allow_n = _write_allowlist(state)
            state["last_write_ts"] = time.time()
            _save_state(state)

            if (now - last_log) >= 20:
                last_log = now
                wallets_n = len(state.get("wallets") or {})
                print(
                    "leaderboard_wallet_discovery "
                    f"seeds={len(seeds)} pair_lookups={pair_lookups} calls={calls} rows={total_rows} import_rows={import_rows} "
                    f"wallets_state={wallets_n} allowlist={allow_n} "
                    f"budgets={budget_map} "
                    f"axiom_status={axiom_status} gmgn_status={gmgn_status} st_status={st_status}",
                    flush=True,
                )

            time.sleep(max(3.0, POLL_S))


if __name__ == "__main__":
    raise SystemExit(main())
