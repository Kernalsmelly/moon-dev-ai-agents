#!/usr/bin/env python3
"""Simple diagnostic script to test the configured Discord webhook.

Run with:
PYTHONPATH=. .venv/bin/python3 scripts/debug_webhook.py
"""
import os
import json

try:
    import requests
except Exception as e:
    print("requests not installed:", e)
    raise

# ensure project root is on path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in __import__('sys').path:
    __import__('sys').path.insert(0, PROJECT_ROOT)

from src import config

url = getattr(config, 'DISCORD_WEBHOOK_URL', None) or os.getenv('DISCORD_WEBHOOK_URL') or os.getenv('DISCORD_WEBHOOK')
if not url:
    print('No DISCORD_WEBHOOK_URL configured (env or src.config). Set it first and re-run.')
    raise SystemExit(1)

# Mask the URL for logs (show prefix only)
masked = url[:20] + '...' if len(url) > 20 else url
print(f"Checking URL: {masked}")

payload = {"content": "🚨 DIAGNOSTIC: If you see this, the plumbing is working."}

try:
    res = requests.post(url, json=payload, timeout=10)
    print(f"Status Code: {res.status_code}")
    try:
        text = res.text
    except Exception:
        text = '<no-text>'
    print(f"Response: {text}")
    if res.status_code in (200, 204):
        print("✅ SUCCESS: Check Discord now.")
    elif res.status_code == 400:
        print("❌ 400 Bad Request: payload likely malformed for this webhook (check embed/content format).")
    elif res.status_code in (401, 404):
        print("❌ 401/404 Unauthorized or Not Found: webhook URL invalid or expired.")
    else:
        print("❌ Unexpected status code")
except Exception as e:
    print(f"💥 CRASH: {e}")
    raise
