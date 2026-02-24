"""Minimal Helius websocket scaffold for low-latency signals.

This module intentionally avoids hard dependencies; it's a thin placeholder
for future real-time subscriptions (logsSubscribe / programSubscribe).
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import time
from urllib.parse import urlsplit
from typing import Callable, Awaitable


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except Exception:
        return default


def _resolve_ws_urls() -> list[str]:
    """Resolve websocket URLs from env with a couple of practical niceties.

    Supported env vars:
    - HELIUS_WS_URLS: comma-separated list of ws(s) URLs (preferred)
    - HELIUS_WS_URL / HELIUS_WS: single URL

    Some dotenv setups leave shell-style indirection literals like `$QUICKNODE_WSS_URL`
    in the environment. We resolve those at runtime so the pipeline doesn't wedge.
    """
    raw = (os.getenv("HELIUS_WS_URLS") or os.getenv("HELIUS_WS_URL") or os.getenv("HELIUS_WS") or "").strip()
    if not raw:
        return []

    # Split CSV (also tolerate whitespace-separated).
    parts: list[str] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts.extend([p.strip() for p in chunk.split() if p.strip()])

    urls: list[str] = []
    for p in parts:
        v = p
        if v.startswith("${") and v.endswith("}"):
            v = v[2:-1]
        if v.startswith("$") and len(v) > 1:
            v = os.getenv(v[1:], v).strip()
        v = v.strip()
        if v.startswith(("ws://", "wss://")):
            urls.append(v)

    # Provider niceties: derive alternative WSS endpoints from known RPC URLs
    # so users don't have to duplicate secrets in multiple env vars.
    #
    # GetBlock: if GETBLOCK_RPC_URL is like https://go.getblock.us/<token> then the
    # websocket endpoint is typically wss://go.getblock.io/<token>/.
    if os.getenv("HELIUS_WS_GETBLOCK_FALLBACK", "false").lower() in ("1", "true", "yes"):
        gb = (os.getenv("GETBLOCK_RPC_URL") or os.getenv("GETBLOCK_URL") or "").strip()
        if gb.startswith(("http://", "https://")):
            try:
                parts_gb = urlsplit(gb)
                segs = [s for s in (parts_gb.path or "").split("/") if s]
                if segs:
                    token = segs[0]
                    urls.append(f"wss://go.getblock.io/{token}/")
            except Exception:
                pass

    # Public fallback (last resort). Disabled by default to avoid burning rate limits.
    if os.getenv("HELIUS_WS_PUBLIC_FALLBACK", "false").lower() in ("1", "true", "yes"):
        urls.append("wss://api.mainnet-beta.solana.com/")

    # De-dupe while preserving order.
    out: list[str] = []
    seen: set[str] = set()
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def _ping_settings() -> tuple[float, float]:
    return _env_float("HELIUS_WS_PING_INTERVAL_S", 20.0), _env_float("HELIUS_WS_PING_TIMEOUT_S", 20.0)


def _quarantine_seconds() -> float:
    return _env_float("HELIUS_WS_QUARANTINE_S", 1800.0)


def _is_quarantine_error(exc: Exception) -> bool:
    msg = str(exc or "").lower()
    needles = (
        "http 401",
        "http 403",
        "http 429",
        "forbidden",
        "unauthorized",
        "too many requests",
        "quota",
        "usage limit",
        "plan",
        "compute units",
    )
    return any(n in msg for n in needles)


async def run_log_subscribe(program_id: str, on_message: Callable[[dict], Awaitable[None]]):
    """Subscribe to logs for a program and invoke on_message for each event.

    Requires `websockets` package to be installed. This is a stub scaffold
    and is safe to skip if dependency is missing.
    """
    if not _resolve_ws_urls():
        raise RuntimeError("HELIUS_WS_URL not set")

    try:
        import websockets  # type: ignore
    except Exception as e:
        raise RuntimeError("websockets not installed") from e

    await run_multi_log_subscribe([program_id], on_message)


async def run_multi_log_subscribe(program_ids: list[str], on_message: Callable[[dict], Awaitable[None]]):
    """Subscribe to logs for multiple programs and invoke on_message for each event."""
    ws_urls = _resolve_ws_urls()
    if not ws_urls:
        raise RuntimeError("HELIUS_WS_URL not set")

    try:
        import websockets  # type: ignore
    except Exception as e:
        raise RuntimeError("websockets not installed") from e

    if not program_ids:
        raise RuntimeError("No program IDs provided")

    ping_interval_s, ping_timeout_s = _ping_settings()
    ping_interval = None if ping_interval_s <= 0 else ping_interval_s
    ping_timeout = None if ping_timeout_s <= 0 else ping_timeout_s
    # Some providers/proxies don't like frequent pings; allow disabling by setting interval <= 0.
    # Also increase open_timeout for occasionally slow handshakes.
    url_idx = 0
    backoff_s = 1.0
    quarantine_until: dict[str, float] = {}
    while True:
        now = time.time()
        available = [u for u in ws_urls if float(quarantine_until.get(u, 0.0) or 0.0) <= now]
        if not available:
            wake_at = min(float(v) for v in quarantine_until.values()) if quarantine_until else (now + 1.0)
            await asyncio.sleep(max(0.5, min(15.0, wake_at - now)))
            available = list(ws_urls)

        ws_url = available[url_idx % len(available)]
        url_idx += 1
        try:
            # Map request id -> program id, and subscription id -> program id
            req_map: dict[int, str] = {}
            sub_map: dict[int, str] = {}
            async with websockets.connect(
                ws_url,
                ping_interval=ping_interval,
                ping_timeout=ping_timeout,
                close_timeout=10,
                open_timeout=20,
                max_queue=256,
            ) as ws:
                backoff_s = 1.0
                for idx, pid in enumerate(program_ids, 1):
                    req_id = idx
                    req_map[req_id] = pid
                    sub_msg = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "method": "logsSubscribe",
                        "params": [{"mentions": [pid]}, {"commitment": "processed"}],
                    }
                    await ws.send(json.dumps(sub_msg))

                while True:
                    raw = await ws.recv()
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue

                    # Subscription response
                    if "id" in msg and "result" in msg and msg.get("id") in req_map:
                        try:
                            sub_id = int(msg.get("result"))
                            sub_map[sub_id] = req_map[int(msg.get("id"))]
                        except Exception:
                            pass

                    # Logs notification
                    if msg.get("method") == "logsNotification":
                        try:
                            sub_id = int(msg.get("params", {}).get("subscription"))
                            pid = sub_map.get(sub_id)
                            if pid:
                                msg["program_id"] = pid
                        except Exception:
                            pass
                    try:
                        await on_message(msg)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        # Never let a downstream parse bug kill the WS connection loop.
                        continue
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if _is_quarantine_error(e):
                quarantine_until[ws_url] = time.time() + max(30.0, _quarantine_seconds())
            # Rotate to the next URL and back off.
            await asyncio.sleep(min(30.0, backoff_s) + random.uniform(0.0, 0.25))
            backoff_s = min(60.0, backoff_s * 1.6)


async def run_block_subscribe_mentions(mention: str, on_tx: Callable[[dict, str], Awaitable[None]]):
    """Subscribe to blocks mentioning an account/program and stream full tx objects.

    This is a practical workaround for free-tier survival:
    - logsSubscribe gives signatures, but forces 1 HTTP getTransaction per signature.
    - blockSubscribe can deliver full transactions over WS, eliminating the HTTP fan-out.

    Note: blockSubscribe is an "unstable" Solana pubsub method and may not be enabled by all providers.
    """
    ws_urls = _resolve_ws_urls()
    if not ws_urls:
        raise RuntimeError("HELIUS_WS_URL not set")

    try:
        import websockets  # type: ignore
    except Exception as e:
        raise RuntimeError("websockets not installed") from e

    ping_interval_s, ping_timeout_s = _ping_settings()
    ping_interval = None if ping_interval_s <= 0 else ping_interval_s
    ping_timeout = None if ping_timeout_s <= 0 else ping_timeout_s

    url_idx = 0
    backoff_s = 1.0
    quarantine_until: dict[str, float] = {}
    while True:
        now = time.time()
        available = [u for u in ws_urls if float(quarantine_until.get(u, 0.0) or 0.0) <= now]
        if not available:
            wake_at = min(float(v) for v in quarantine_until.values()) if quarantine_until else (now + 1.0)
            await asyncio.sleep(max(0.5, min(15.0, wake_at - now)))
            available = list(ws_urls)

        ws_url = available[url_idx % len(available)]
        url_idx += 1
        try:
            async with websockets.connect(
                ws_url,
                ping_interval=ping_interval,
                ping_timeout=ping_timeout,
                close_timeout=10,
                open_timeout=20,
                max_queue=256,
            ) as ws:
                backoff_s = 1.0
                # Request json encoding + full tx details so downstream can reuse getTransaction-shaped parsing.
                req = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "blockSubscribe",
                    "params": [
                        {"mentionsAccountOrProgram": mention},
                        {
                            "commitment": "confirmed",
                            "encoding": "json",
                            "transactionDetails": "full",
                            "maxSupportedTransactionVersion": 0,
                            "showRewards": False,
                        },
                    ],
                }
                await ws.send(json.dumps(req))

                # Consume subscription response
                sub_id = None
                while sub_id is None:
                    raw = await ws.recv()
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    if "id" in msg and msg.get("id") == 1 and "result" in msg:
                        sub_id = msg.get("result")
                        break

                while True:
                    raw = await ws.recv()
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    if msg.get("method") != "blockNotification":
                        continue
                    try:
                        value = (msg.get("params", {}) or {}).get("result", {}) or {}
                        v = (value.get("value", {}) or {})
                        block = v.get("block") or {}
                        txs = block.get("transactions") or []
                        if not isinstance(txs, list):
                            continue
                        for item in txs:
                            if not isinstance(item, dict):
                                continue
                            tx_obj = item.get("transaction")
                            meta = item.get("meta")
                            # Some encodings return transaction as [base64, meta]; we only support json here.
                            if not isinstance(tx_obj, dict):
                                continue
                            sigs = tx_obj.get("signatures") or []
                            sig = sigs[0] if isinstance(sigs, list) and sigs else None
                            if not isinstance(sig, str):
                                continue
                            tx = {"transaction": tx_obj, "meta": meta or {}}
                            await on_tx(tx, sig)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        continue
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if _is_quarantine_error(e):
                quarantine_until[ws_url] = time.time() + max(30.0, _quarantine_seconds())
            await asyncio.sleep(min(30.0, backoff_s) + random.uniform(0.0, 0.25))
            backoff_s = min(60.0, backoff_s * 1.6)


def run_log_subscribe_sync(program_id: str, on_message: Callable[[dict], Awaitable[None]]):
    """Sync helper for scripts."""
    asyncio.run(run_log_subscribe(program_id, on_message))
