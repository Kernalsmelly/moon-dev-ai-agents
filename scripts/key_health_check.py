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
from typing import Any, Dict, List, Optional

import aiohttp

# Simple .env parser (no external deps)
ENV_RE = re.compile(r"^\s*([A-Za-z0-9_]+)\s*=\s*('?\"?)(.*)\2\s*$")

KNOWN_PROVIDER_KEYWORDS = [
    'HELIUS', 'QUICKNODE', 'SHYFT', 'ALCHEMY', 'BIRDEYE', 'RPC', 'JITO', 'JUPITER'
]


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
        if v.startswith('http://') or v.startswith('https://'):
            candidates[k] = v
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
    }

    headers = {'Content-Type': 'application/json'}

    async def _post(method: str) -> (bool, Optional[float], Optional[Any]):
        payload = JSON_RPC_TEMPLATE.copy()
        payload['method'] = method
        payload['params'] = []
        start = time.perf_counter()
        try:
            async with session.post(url, json=payload, headers=headers, timeout=timeout) as resp:
                text = await resp.text()
                elapsed = (time.perf_counter() - start) * 1000.0
                try:
                    j = json.loads(text)
                except Exception:
                    j = text
                return True, elapsed, j
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


def build_rpc_pool(probes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Rank by average successful probe RTT (health + blockhash). Lower is better."""
    scored = []
    for p in probes:
        if not p or 'url' not in p:
            continue
        latencies = []
        for k in ('rtt_getHealth_ms', 'rtt_getLatestBlockhash_ms'):
            v = p.get(k)
            if isinstance(v, (int, float)):
                latencies.append(float(v))
        avg = sum(latencies) / len(latencies) if latencies else float('inf')
        p['avg_latency_ms'] = avg
        scored.append(p)
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
    p.add_argument('--yes', action='store_true', help='Allow network probes when running the script')
    args = p.parse_args(argv)

    env = parse_dotenv(args.env)
    candidates = find_candidate_urls(env)

    if not candidates:
        print('No HTTP(S) endpoints found in .env or src.config. Populate .env with RPC URLs (RPC_URL etc.) and re-run.')
        return 2

    print(f'Found {len(candidates)} candidate endpoints:')
    for k, v in candidates.items():
        print(f'  {k} = {v}')

    if not args.yes:
        print('\nNOTE: This script will make external HTTP requests to the above endpoints.')
        print('Run again with --yes to permit network probes. (This safety prevents accidental scans.)')
        return 0

    # Run async probes
    loop = asyncio.get_event_loop()
    try:
        probes = loop.run_until_complete(run_probes(candidates, concurrency=args.concurrency, timeout=args.timeout))
    finally:
        # on some python versions loop.close() can cause issues; avoid forcibly closing
        pass

    summary = {'checked_at': time.time(), 'results': probes}

    # write human summary
    out_path = args.out
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    pool = build_rpc_pool(probes)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({'generated_at': time.time(), 'pool': pool, 'raw': probes}, f, indent=2)

    print('\nWrote RPC pool to', out_path)
    print('\nTop ranked endpoints:')
    for entry in pool[:5]:
        print(f"  {entry['label']}: {entry['url']}  avg_latency_ms={entry['avg_latency_ms']}")

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
