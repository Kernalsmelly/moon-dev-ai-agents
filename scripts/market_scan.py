#!/usr/bin/env python3
"""On-demand market scanner.

Usage:
PYTHONPATH=. .venv/bin/python3 scripts/market_scan.py

This will:
- Load watchlist from `data/watchlist.json` (fallback to a small default)
- Query CoinGecko for recent prices
- Compute a simple 5m change and VHI (Volatility Health Index)
- Send a rich embed to the configured Discord webhook using requests

"""
import os
import time
import json
from datetime import datetime, timezone
from statistics import stdev

try:
    import requests
except Exception as e:
    print('requests not available:', e)
    raise

# ensure repo root on path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in __import__('sys').path:
    __import__('sys').path.insert(0, PROJECT_ROOT)

from src import config

# helper to load watchlist (mint->symbol)
wl_path = getattr(config, 'WATCHLIST_PATH', 'watchlist.json')
# support both src/data and data root
candidates = [os.path.join('src', 'data', wl_path), os.path.join('data', wl_path), wl_path]
watch_symbols = []
for p in candidates:
    try:
        if os.path.exists(p):
            with open(p, 'r', encoding='utf-8') as fh:
                obj = json.load(fh)
                # obj is mapping mint->SYMBOL
                for sym in obj.values():
                    if sym and sym not in watch_symbols:
                        watch_symbols.append(sym)
            break
    except Exception:
        continue

if not watch_symbols:
    # fallback default
    watch_symbols = ['SOL', 'JUP', 'BTC', 'ETH', 'USDC']

# limit to 10
watch_symbols = watch_symbols[:10]

# map common symbols to CoinGecko ids
COINGECKO_MAP = {
    'SOL': 'solana',
    'JUP': 'jupiter',
    'BTC': 'bitcoin',
    'ETH': 'ethereum',
    'USDC': 'usd-coin',
}

def get_coingecko_id(sym: str) -> str:
    return COINGECKO_MAP.get(sym.upper(), sym.lower())

def fetch_market_chart(coin_id: str, vs_currency: str = 'usd', days: float = 0.01):
    """Fetch market chart from CoinGecko. days ~ 0.01 includes recent minutes."""
    url = f'https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart'
    params = {'vs_currency': vs_currency, 'days': days}
    res = requests.get(url, params=params, timeout=15)
    res.raise_for_status()
    return res.json()

now_ms = int(time.time() * 1000)
results = []
for sym in watch_symbols:
    coin_id = get_coingecko_id(sym)
    try:
        chart = fetch_market_chart(coin_id, days=0.01)
        prices = chart.get('prices', [])
        if not prices:
            raise Exception('no prices')
        # prices is list of [ts_ms, price]
        # current price = last
        ts, price = prices[-1]
        # find price ~5 minutes ago
        target_ms = now_ms - (5 * 60 * 1000)
        past_price = None
        # iterate backward to find first price <= target_ms
        for t, p in reversed(prices):
            if t <= target_ms:
                past_price = p
                break
        if past_price is None:
            # fallback to earliest point
            past_price = prices[0][1]
        change_pct = ((price - past_price) / past_price) * 100.0 if past_price else 0.0
        # compute volatility as stdev of percent returns across recent points
        vals = [p for (_, p) in prices[-12:]] if len(prices) >= 12 else [p for (_, p) in prices]
        rets = []
        for i in range(1, len(vals)):
            if vals[i-1] == 0:
                continue
            rets.append((vals[i] - vals[i-1]) / vals[i-1])
        vol = (abs(stdev(rets)) * 100.0) if len(rets) >= 2 else 0.0
        # VHI status thresholds (example): vol < 0.05% -> Healthy, <0.2% -> Watch, else Volatile
        if vol < 0.05:
            vhi = 'Healthy'
            color = 3066993
        elif vol < 0.2:
            vhi = 'Watch'
            color = 15105570
        else:
            vhi = 'Volatile'
            color = 15158332

        results.append({
            'symbol': sym,
            'coin_id': coin_id,
            'price': price,
            'change_5m': change_pct,
            'vhi': vhi,
            'vol_pct': vol,
            'color': color,
        })
    except Exception as e:
        results.append({'symbol': sym, 'error': str(e)})

# determine most volatile by vol_pct
most_volatile = None
max_vol = -1.0
for r in results:
    if 'vol_pct' in r and r['vol_pct'] > max_vol:
        max_vol = r['vol_pct']
        most_volatile = r

# Build Discord embed
embed = {
    'title': '🔍 LIVE MARKET SCAN',
    'description': f'Most Volatile: {most_volatile["symbol"] if most_volatile else "N/A"}',
    'color': most_volatile['color'] if most_volatile and 'color' in most_volatile else 3447003,
    'fields': [],
    'footer': {'text': f'Scan requested at {datetime.now(timezone.utc).isoformat()} | Moon Dev Challenger'},
    'timestamp': datetime.now(timezone.utc).isoformat(),
}

for r in results:
    if 'error' in r:
        embed['fields'].append({'name': r['symbol'], 'value': f'Error: {r["error"]}', 'inline': True})
    else:
        price_str = f'${r["price"]:,.2f}' if r['price'] else 'N/A'
        change_str = f'{r["change_5m"]:+.2f}%' if 'change_5m' in r else 'N/A'
        vhi_str = f'{r["vhi"]} ({r["vol_pct"]:.3f}%)'
        embed['fields'].append({
            'name': r['symbol'],
            'value': f'Price: {price_str}\n5m Change: {change_str}\nVHI: {vhi_str}',
            'inline': True,
        })

# send via requests using repo config
from src import alerts
webhook = getattr(config, 'DISCORD_WEBHOOK_URL', None) or os.getenv('DISCORD_WEBHOOK_URL') or os.getenv('DISCORD_WEBHOOK')
if not webhook:
    print('No webhook configured. Set DISCORD_WEBHOOK_URL in env or src.config')
    raise SystemExit(1)

payload = {
    'content': f'LIVE MARKET SCAN — Most Volatile: {most_volatile["symbol"] if most_volatile else "N/A"}',
    'embeds': [embed],
}

try:
    res = requests.post(webhook, json=payload, timeout=15)
    print('Status Code:', res.status_code)
    print('Response:', res.text)
    if res.status_code in (200, 204):
        print('✅ Sent LIVE MARKET SCAN to Discord')
    else:
        print('❌ Failed to send LIVE MARKET SCAN')
except Exception as e:
    print('💥 Error sending webhook:', e)
    raise
