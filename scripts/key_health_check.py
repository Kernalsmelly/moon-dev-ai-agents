#!/usr/bin/env python3
"""
Lightweight key / RPC health checker.

- Scans .env and `src.config` for HTTP(S) endpoints and provider keys.
- For each discovered URL it issues two JSON-RPC probes: `getHealth` and `getLatestBlockhash`.
- Measures RTT for each probe (ms) and records success/failure and responses.
- Produces a JSON summary and writes `config/rpc_pool.json` ranked by average latency.

Usage:
    python scripts/key_health_check.py --env .env --out config/rpc_pool.json

Important: this script will make outbound HTTP requests when you run it. Do not run it unless you
intend to probe your provider endpoints (you should have API keys / URLs configured in your .env).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from typing import Any, Dict, List, Optional

import aiohttp

# Simple .env parser (no external deps)
ENV_RE = re.compile(r"^\s*([A-Za-z0-9_]+)\s*=\s*('?\"?)(.*)\2\s*$")

KNOWN_PROVIDER_KEYWORDS = [
    'HELIUS', 'QUICKNODE', 'SHYFT', 'ALCHEMY', 'BIRDEYE', 'RPC', 'JITO', 'JUPITER'
]

SENSITIVE_QUERY_KEYS = {
    "api-key",
    "apikey",
    "key",
    "token",
    "access_token",
    "auth",
}


def redact_url(url: str) -> str:
    """Redact obvious secrets from URLs for console output.

    We still write full URLs into the output pool JSON so the bot can use them.
    """
    try:
        parts = urlsplit(url)
        q = []
        for k, v in parse_qsl(parts.query, keep_blank_values=True):
            if k.lower() in SENSITIVE_QUERY_KEYS:
                q.append((k, "***"))
            else:
                q.append((k, v))
        query = urlencode(q)
        # Redact long path segments that look like API keys.
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


def parse_dotenv(path: str) -> Dict[str, str]:
    d: Dict[str, str] = {}
    if not os.path.exists(path):
        return d
    with open(path, 'r', encoding='utf-8') as f:
        for ln in f:
            ln = ln.strip()
            if not ln or ln.startswith('#'):
                continue
            # split on first '='
            if '=' not in ln:
                continue
            k, v = ln.split('=', 1)
            k = k.strip()
            v = v.strip().strip('\"').strip("'")
            d[k] = v
    return d


def find_candidate_urls(env: Dict[str, str]) -> Dict[str, str]:
    """Return map name -> url for discovered endpoints.

    We look for any env value that looks like an http(s) url, plus explicit RPC_* keys.
    """
    candidates: Dict[str, str] = {}

    for k, v in env.items():
        if not v:
            continue
        # Support list-style env values such as RPC_URLS=url1,url2
        vals = [p.strip() for p in v.split(",")] if "," in v else [v]
        for i, vv in enumerate(vals):
            if not vv:
                continue
            if vv.startswith('http://') or vv.startswith('https://'):
                name = f"{k}[{i}]" if len(vals) > 1 else k
                candidates[name] = vv
                continue
        # some providers use only API keys (no URL). Skip those — user can provide full URL in env.

    # Also attempt to import src.config and pick up RPC_URL if present
    try:
        import importlib

        cfg = importlib.import_module('src.config')
        rpc = getattr(cfg, 'RPC_URL', None)
        if rpc and isinstance(rpc, str) and (rpc.startswith('http://') or rpc.startswith('https://')):
            candidates.setdefault('RPC_URL', rpc)
    except Exception:
        # ignore import errors — we only optionally pick up config
        pass

    return candidates


JSON_RPC_TEMPLATE = {
    'jsonrpc': '2.0',
    'id': 1,
}


async def probe_url(session: aiohttp.ClientSession, url: str, timeout: float = 3.0) -> Dict[str, Any]:
    """Probe a URL with getHealth and getLatestBlockhash JSON-RPC calls.

    Returns a dict with fields: url, success_getHealth, rtt_getHealth_ms, resp_getHealth,
    success_getLatestBlockhash, rtt_getLatestBlockhash_ms, resp_getLatestBlockhash
    """
    out: Dict[str, Any] = {
        'url': url,
        'success_getHealth': False,
        'rtt_getHealth_ms': None,
        'resp_getHealth': None,
        'success_getLatestBlockhash': False,
        'rtt_getLatestBlockhash_ms': None,
        'resp_getLatestBlockhash': None,
        'success_getTransaction': False,
        'rtt_getTransaction_ms': None,
        'resp_getTransaction': None,
    }

    headers = {'Content-Type': 'application/json'}

    async def _post(method: str, params: list[Any] | None = None) -> (bool, Optional[float], Optional[Any]):
        payload = JSON_RPC_TEMPLATE.copy()
        payload['method'] = method
        payload['params'] = params or []
        start = time.perf_counter()
        try:
            async with session.post(url, json=payload, headers=headers, timeout=timeout) as resp:
                text = await resp.text()
                elapsed = (time.perf_counter() - start) * 1000.0
                try:
                    j = json.loads(text)
                except Exception:
                    j = text
                ok = (resp.status == 200)
                if ok and isinstance(j, dict) and 'error' in j:
                    ok = False
                return ok, elapsed, j
        except Exception as e:  # network / timeout / invalid url
            elapsed = (time.perf_counter() - start) * 1000.0
            return False, elapsed, str(e)

    ok, rtt, resp = await _post('getHealth')
    out['success_getHealth'] = ok
    out['rtt_getHealth_ms'] = round(rtt, 2) if rtt is not None else None
    out['resp_getHealth'] = resp

    ok2, rtt2, resp2 = await _post('getLatestBlockhash')
    out['success_getLatestBlockhash'] = ok2
    out['rtt_getLatestBlockhash_ms'] = round(rtt2, 2) if rtt2 is not None else None
    out['resp_getLatestBlockhash'] = resp2

    # Optional deeper probe: can the endpoint serve getTransaction?
    # This matters for WS-based discovery which calls getTransaction heavily.
    #
    # We intentionally avoid getSignaturesForAddress because some providers do not
    # enable address history on free tiers. Instead:
    # - getSlot(finalized) -> slot
    # - getBlock(slot, signatures-only) -> pick one signature
    # - getTransaction(signature)
    try:
        slot_ok, _, slot_resp = await _post('getSlot', [{'commitment': 'finalized'}])
        slot = None
        if slot_ok and isinstance(slot_resp, dict):
            try:
                slot = int(slot_resp.get('result'))
            except Exception:
                slot = None
        if slot is not None and slot >= 0:
            blk_ok, _, blk_resp = await _post(
                'getBlock',
                [slot, {'encoding': 'json', 'transactionDetails': 'signatures', 'rewards': False}],
            )
            sig = None
            if blk_ok and isinstance(blk_resp, dict):
                res = blk_resp.get('result') or {}
                sigs = res.get('signatures') if isinstance(res, dict) else None
                if isinstance(sigs, list) and sigs:
                    sig = sigs[0]
            if sig and isinstance(sig, str):
                ok3, rtt3, resp3 = await _post(
                    'getTransaction',
                    [sig, {'encoding': 'json', 'maxSupportedTransactionVersion': 0}],
                )
                out['success_getTransaction'] = ok3
                out['rtt_getTransaction_ms'] = round(rtt3, 2) if rtt3 is not None else None
                out['resp_getTransaction'] = resp3
    except Exception:
        pass

    return out


async def run_probes(urls: Dict[str, str], concurrency: int = 8, timeout: float = 3.0) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    sem = asyncio.Semaphore(concurrency)

    async with aiohttp.ClientSession() as session:

        async def _run(name: str, url: str):
            async with sem:
                try:
                    return await probe_url(session, url, timeout=timeout)
                except Exception as e:
                    return {'url': url, 'error': str(e)}

        tasks = [asyncio.create_task(_run(n, u)) for n, u in urls.items()]
        for t in asyncio.as_completed(tasks):
            res = await t
            results.append(res)
    return results


def build_rpc_pool(probes: List[Dict[str, Any]], *, require_tx: bool = False) -> List[Dict[str, Any]]:
    """Rank by average successful probe RTT (health + blockhash). Lower is better.

    Important: for Solana trading we need a functional JSON-RPC endpoint. In practice,
    `getLatestBlockhash` success is the best minimal signal that the endpoint is usable.
    Some providers will respond to `getHealth` even when RPC methods are paywalled or
    quota-blocked; we filter those out.
    """
    scored = []
    for p in probes:
        if not p or 'url' not in p:
            continue
        # Require blockhash success. Otherwise downstream bots will "work" until they need
        # a blockhash/transaction and then wedge.
        if not p.get('success_getLatestBlockhash'):
            continue
        if require_tx and ('success_getTransaction' in p) and (not p.get('success_getTransaction')):
            continue
        latencies = []
        for k in ('rtt_getHealth_ms', 'rtt_getLatestBlockhash_ms'):
            v = p.get(k)
            if isinstance(v, (int, float)):
                latencies.append(float(v))
        avg = sum(latencies) / len(latencies) if latencies else float('inf')
        p['avg_latency_ms'] = avg
        scored.append(p)
    # De-dupe by URL (keep the best latency). .env often contains the same RPC under
    # multiple variable names (RPC_URL, ALCHEMY_URL, ALCHEMY_RPC_URL, etc.).
    best_by_url: Dict[str, Dict[str, Any]] = {}
    for p in scored:
        url = str(p.get('url') or '').strip()
        if not url:
            continue
        prev = best_by_url.get(url)
        if prev is None or float(p.get('avg_latency_ms') or float('inf')) < float(prev.get('avg_latency_ms') or float('inf')):
            best_by_url[url] = p
    scored = list(best_by_url.values())
    scored.sort(key=lambda x: x.get('avg_latency_ms', float('inf')))

    labeled = []
    labels = ['PRIMARY', 'BACKUP_1', 'BACKUP_2', 'BACKUP_3', 'BACKUP_4']
    for i, item in enumerate(scored):
        label = labels[i] if i < len(labels) else f'BACKUP_{i}'
        labeled.append({
            'label': label,
            'url': item['url'],
            'avg_latency_ms': item['avg_latency_ms'],
            'success_getHealth': item.get('success_getHealth', False),
            'success_getLatestBlockhash': item.get('success_getLatestBlockhash', False),
        })
    return labeled


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description='RPC / provider key health checker')
    p.add_argument('--env', default='.env', help='Path to .env file to scan')
    p.add_argument('--out', default='config/rpc_pool.json', help='Output JSON file (rpc_pool)')
    p.add_argument('--concurrency', type=int, default=6)
    p.add_argument('--timeout', type=float, default=3.0)
    p.add_argument('--rpc-only', action='store_true', help='Only probe likely Solana RPC endpoints (recommended).')
    p.add_argument('--require-tx', action='store_true', help='Require getTransaction success (recommended for WS discovery).')
    p.add_argument('--yes', action='store_true', help='Allow network probes when running the script')
    args = p.parse_args(argv)

    env = parse_dotenv(args.env)
    candidates = find_candidate_urls(env)

    if args.rpc_only and candidates:
        hints = (
            "RPC",
            "SOLANA",
            "HELIUS",
            "QUICKNODE",
            "CHAINSTACK",
            "ANKR",
            "ALCHEMY",
            "SYNDICA",
            "GETBLOCK",
            "DRPC",
        )
        deny_hosts = ("discord.com", "openweathermap.org", "pandascore.co", "api.coingecko.com")
        filtered: Dict[str, str] = {}
        for k, v in candidates.items():
            ku = k.upper()
            if not any(h in ku for h in hints):
                continue
            # Avoid mixing clusters in an RPC pool: devnet/testnet endpoints will
            # return different data and will break mainnet workflows.
            if "DEVNET" in ku or "TESTNET" in ku:
                continue
            try:
                host = urlsplit(v).netloc.lower()
                if any(d in host for d in deny_hosts):
                    continue
                if "devnet" in host or "testnet" in host:
                    continue
                if "devnet" in v.lower() or "testnet" in v.lower():
                    continue
            except Exception:
                pass
            filtered[k] = v
        candidates = filtered

    if not candidates:
        print('No HTTP(S) endpoints found in .env or src.config. Populate .env with RPC URLs (RPC_URL etc.) and re-run.')
        return 2

    print(f'Found {len(candidates)} candidate endpoints:')
    for k, v in candidates.items():
        print(f'  {k} = {redact_url(v)}')

    if not args.yes:
        print('\nNOTE: This script will make external HTTP requests to the above endpoints.')
        print('Run again with --yes to permit network probes. (This safety prevents accidental scans.)')
        return 0

    # Run async probes
    probes = asyncio.run(run_probes(candidates, concurrency=args.concurrency, timeout=args.timeout))

    summary = {'checked_at': time.time(), 'results': probes}

    # write human summary
    out_path = args.out
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    pool = build_rpc_pool(probes, require_tx=bool(args.require_tx))
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({'generated_at': time.time(), 'pool': pool, 'raw': probes}, f, indent=2)

    print('\nWrote RPC pool to', out_path)
    print('\nTop ranked endpoints:')
    for entry in pool[:5]:
        print(f"  {entry['label']}: {redact_url(entry['url'])}  avg_latency_ms={entry['avg_latency_ms']}")

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
