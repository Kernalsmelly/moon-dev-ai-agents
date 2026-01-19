#!/usr/bin/env python3
"""Benchmark script for Flash-Shadow path.

Sends a fake Helius webhook payload to the local webhook server and measures the
latency from HTTP request send to the moment the handler writes a submit marker
file (data/flash_shadow_<trace_id>.txt), which indicates Jito submit was invoked.

Usage:
  source .venv/bin/activate
  python scripts/benchmark_shadow.py --url http://localhost:8000/webhook/helius --mint <MINT> --whale <WHALE_ADDR>

If measured latency > 500ms, the script can call /webhook/prefetch to prime the
quote cache for the provided mint(s) and re-run the benchmark.
"""
from __future__ import annotations

import argparse
import time
import uuid
import json
import os
from pathlib import Path
import requests


def send_payload(url: str, trace_id: str, whale: str, mint: str, amount_usd: float = 1500.0, pct: float = 25.0, secret: str | None = None):
    ts = time.time()
    iso = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(ts)) + 'Z'
    payload = {
        'trace_id': trace_id,
        'transactions': [
            {
                'transfers': [
                    {
                        'id': str(uuid.uuid4()),
                        'from': whale,
                        'to': 'SomeOtherAddress',
                        'mint': mint,
                        'valueUsd': amount_usd,
                        'pctOfPosition': pct,
                        'timestamp': iso,
                    }
                ]
            }
        ]
    }
    headers = {'Content-Type': 'application/json'}
    if secret:
        headers['X-Webhook-Secret'] = secret
    start = time.time()
    r = requests.post(url, json=payload, headers=headers, timeout=10)
    return r.status_code, start


def wait_for_marker(trace_id: str, timeout: float = 5.0):
    repo_root = Path(__file__).resolve().parents[1]
    data_dir = repo_root.joinpath('data')
    p = data_dir.joinpath(f'flash_shadow_{trace_id}.txt')
    deadline = time.time() + timeout
    while time.time() < deadline:
        if p.exists():
            try:
                txt = p.read_text()
                ts_ms = int(txt.strip())
                return ts_ms
            except Exception:
                return None
        time.sleep(0.01)
    return None


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://localhost:8000/webhook/helius')
    parser.add_argument('--prefetch', action='store_true', help='Call prefetch endpoint before running')
    parser.add_argument('--mints', nargs='*', default=[], help='List of mints to prefetch')
    parser.add_argument('--mint', required=True, help='Target mint to include in fake tx')
    parser.add_argument('--whale', required=True, help='Whale address to simulate')
    parser.add_argument('--secret', default=os.getenv('WEBHOOK_SECRET', ''), help='Webhook secret header value')
    args = parser.parse_args()

    if args.prefetch and args.mints:
        prefetch_url = args.url.replace('/webhook/helius', '/webhook/prefetch')
        headers = {'Content-Type': 'application/json'}
        if args.secret:
            headers['X-Webhook-Secret'] = args.secret
        print('Calling prefetch for mints:', args.mints)
        try:
            r = requests.post(prefetch_url, json={'mints': args.mints}, headers=headers, timeout=5)
            print('prefetch response', r.status_code, r.text)
            time.sleep(0.1)
        except Exception as e:
            print('prefetch failed', e)

    trace_id = uuid.uuid4().hex
    print('trace_id', trace_id)
    status, send_ts = send_payload(args.url, trace_id, args.whale, args.mint, secret=args.secret)
    if status >= 400:
        print('Webhook POST failed with', status)
        raise SystemExit(1)
    marker_ts_ms = wait_for_marker(trace_id, timeout=10.0)
    if marker_ts_ms is None:
        print('No submit marker observed within timeout')
        raise SystemExit(2)
    # marker_ts_ms is ms epoch when handler wrote marker; compute delta from send_ts
    delta_ms = int(marker_ts_ms - int(send_ts * 1000))
    print(f'Internal latency (send -> jito submit marker): {delta_ms} ms')
    if delta_ms > 500:
        print('Latency > 500ms, consider prefetching quotes for top tokens to reduce quote RTT.')
    else:
        print('Latency within 500ms target')
