#!/usr/bin/env python3
"""Smoke-test RPC + WSS provider endpoints with minimal load.

This is designed for free-tier survival:
- Single-digit JSON-RPC calls per endpoint
- One short-lived WSS subscription per endpoint

Outputs a machine-readable report to data/provider_smoke_test.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import aiohttp


SENSITIVE_QUERY_KEYS = {
    "api-key",
    "apikey",
    "key",
    "token",
    "access_token",
    "auth",
}


def redact_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        q = []
        for k, v in parse_qsl(parts.query, keep_blank_values=True):
            q.append((k, "***" if k.lower() in SENSITIVE_QUERY_KEYS else v))
        query = urlencode(q)
        segs = parts.path.split("/")
        new_segs = []
        for s in segs:
            if len(s) >= 24 and all(ch.isalnum() or ch in "-_." for ch in s):
                new_segs.append("***")
            else:
                new_segs.append(s)
        path = "/".join(new_segs)
        return urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))
    except Exception:
        return url


def parse_dotenv(path: str) -> dict[str, str]:
    d: dict[str, str] = {}
    if not os.path.exists(path):
        return d
    with open(path, "r", encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            d[k.strip()] = v.strip().strip('"').strip("'")
    return d


def _split_csv(v: str) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def resolve_ws_urls(env: dict[str, str]) -> list[str]:
    raw = (env.get("HELIUS_WS_URLS") or env.get("HELIUS_WS_URL") or env.get("HELIUS_WS") or "").strip()
    urls: list[str] = []
    for p in _split_csv(raw):
        v = p
        if v.startswith("${") and v.endswith("}"):
            v = v[2:-1]
        if v.startswith("$") and len(v) > 1:
            v = env.get(v[1:], v).strip()
        if v.startswith(("ws://", "wss://")):
            urls.append(v)

    # Add explicit quicknode if present (common env naming).
    qn = (env.get("QUICKNODE_WSS_URL") or env.get("QUICKNODE_WSS") or "").strip()
    if qn and qn.startswith(("ws://", "wss://")):
        urls.append(qn)

    # Derive GetBlock WSS URL from its RPC URL when present.
    # Example: https://go.getblock.us/<token> -> wss://go.getblock.io/<token>/
    if (env.get("HELIUS_WS_GETBLOCK_FALLBACK") or "").strip().lower() in ("1", "true", "yes"):
        gb = (env.get("GETBLOCK_RPC_URL") or env.get("GETBLOCK_URL") or "").strip()
        if gb.startswith(("http://", "https://")):
            try:
                parts = urlsplit(gb)
                segs = [s for s in (parts.path or "").split("/") if s]
                if segs:
                    urls.append(f"wss://go.getblock.io/{segs[0]}/")
            except Exception:
                pass

    out: list[str] = []
    seen: set[str] = set()
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def resolve_rpc_urls(env: dict[str, str]) -> dict[str, str]:
    urls: dict[str, str] = {}
    # Common keys we care about.
    for k, v in env.items():
        if not v:
            continue
        ku = k.upper()
        if not any(x in ku for x in ("RPC", "SOLANA", "HELIUS", "QUICKNODE", "CHAINSTACK", "ANKR", "ALCHEMY", "SYNDICA", "GETBLOCK", "DRPC")):
            continue

        # Handle CSV-style list values (e.g., RPC_URLS=a,b) as individual endpoints.
        vals = _split_csv(v) if "," in v else [v]
        for i, vv in enumerate(vals):
            if not vv.startswith(("http://", "https://")):
                continue
            name = f"{k}[{i}]" if len(vals) > 1 else k
            urls[name] = vv
    return urls


async def rpc_probe(session: aiohttp.ClientSession, url: str, timeout_s: float) -> dict[str, Any]:
    out: dict[str, Any] = {"url": url, "ok": False, "rtt_ms": None, "error": None}
    payload = {"jsonrpc": "2.0", "id": 1, "method": "getLatestBlockhash", "params": []}
    start = time.perf_counter()
    try:
        async with session.post(url, json=payload, timeout=timeout_s) as resp:
            text = await resp.text()
            out["rtt_ms"] = round((time.perf_counter() - start) * 1000.0, 2)
            if resp.status != 200:
                out["error"] = f"http_{resp.status}"
                return out
            try:
                j = json.loads(text)
            except Exception:
                out["error"] = "non_json"
                return out
            if isinstance(j, dict) and j.get("error"):
                msg = str(j.get("error"))
                out["error"] = msg[:200]
                return out
            out["ok"] = True
            return out
    except Exception as e:
        out["rtt_ms"] = round((time.perf_counter() - start) * 1000.0, 2)
        out["error"] = str(e)[:200]
        return out


async def wss_probe(url: str, timeout_s: float) -> dict[str, Any]:
    out: dict[str, Any] = {"url": url, "ok": False, "rtt_ms": None, "error": None}
    try:
        import websockets  # type: ignore
    except Exception as e:
        out["error"] = f"missing_websockets: {e}"
        return out
    start = time.perf_counter()
    try:
        async with websockets.connect(url, open_timeout=timeout_s, close_timeout=5, ping_interval=None, max_queue=32) as ws:
            # slotSubscribe can be allowed even when the provider has restricted higher-load
            # subscriptions (e.g. logsSubscribe / blockSubscribe). Probe the exact method
            # we need for the meme bot pipeline so "WSS ok" is actually meaningful.
            mention = os.getenv("WSS_PROBE_MENTION") or "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
            await ws.send(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "logsSubscribe",
                        "params": [{"mentions": [mention]}, {"commitment": "processed"}],
                    }
                )
            )
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout_s)
            out["rtt_ms"] = round((time.perf_counter() - start) * 1000.0, 2)
            try:
                msg = json.loads(raw)
            except Exception:
                out["error"] = "non_json"
                return out
            if isinstance(msg, dict) and msg.get("error"):
                out["error"] = str(msg.get("error"))[:200]
                return out
            sub_id = msg.get("result")
            if sub_id is None:
                out["error"] = "missing_result"
                return out
            try:
                await ws.send(
                    json.dumps({"jsonrpc": "2.0", "id": 2, "method": "logsUnsubscribe", "params": [sub_id]})
                )
            except Exception:
                pass
            out["ok"] = True
            return out
    except Exception as e:
        out["rtt_ms"] = round((time.perf_counter() - start) * 1000.0, 2)
        out["error"] = str(e)[:200]
        return out


async def main_async(env_path: str, out_path: str, timeout_s: float) -> int:
    env = parse_dotenv(env_path)
    rpc_urls = resolve_rpc_urls(env)
    ws_urls = resolve_ws_urls(env)

    results: dict[str, Any] = {"generated_at": time.time(), "rpc": {}, "wss": []}

    async with aiohttp.ClientSession(headers={"Content-Type": "application/json"}) as session:
        tasks = {k: asyncio.create_task(rpc_probe(session, u, timeout_s=timeout_s)) for k, u in rpc_urls.items()}
        for k, t in tasks.items():
            results["rpc"][k] = await t

    if ws_urls:
        ws_tasks = [asyncio.create_task(wss_probe(u, timeout_s=timeout_s)) for u in ws_urls]
        results["wss"] = [await t for t in ws_tasks]

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)

    # Console summary (redacted).
    ok_rpc = sum(1 for r in results["rpc"].values() if isinstance(r, dict) and r.get("ok"))
    ok_wss = sum(1 for r in results["wss"] if isinstance(r, dict) and r.get("ok"))
    print(f"RPC ok={ok_rpc}/{len(results['rpc'])} | WSS ok={ok_wss}/{len(results['wss'])}", flush=True)
    for name, r in sorted(results["rpc"].items()):
        if not isinstance(r, dict):
            continue
        status = "OK" if r.get("ok") else f"FAIL({r.get('error')})"
        print(f"  rpc {name}: {status} {redact_url(str(r.get('url') or ''))} rtt_ms={r.get('rtt_ms')}", flush=True)
    for r in results["wss"]:
        if not isinstance(r, dict):
            continue
        status = "OK" if r.get("ok") else f"FAIL({r.get('error')})"
        print(f"  wss: {status} {redact_url(str(r.get('url') or ''))} rtt_ms={r.get('rtt_ms')}", flush=True)
    print("Wrote", out_path, flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=".env")
    ap.add_argument("--out", default="data/provider_smoke_test.json")
    ap.add_argument("--timeout", type=float, default=6.0)
    args = ap.parse_args()
    return asyncio.run(main_async(args.env, args.out, args.timeout))


if __name__ == "__main__":
    raise SystemExit(main())
