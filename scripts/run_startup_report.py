#!/usr/bin/env python3
"""One-shot: read last trades and send a Startup Status embed via repo alerts.

Run with: PYTHONPATH=. .venv/bin/python3 -u scripts/run_startup_report.py
"""
import os
import json
from datetime import datetime, timezone

# Add project root to path (repo root is two levels up from this script)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in __import__('sys').path:
    __import__('sys').path.insert(0, PROJECT_ROOT)

try:
    from src import config
    from src import alerts
except Exception as e:
    print("Failed to import repo modules:", e)
    raise

path = getattr(config, 'TRADES_JSONL_PATH', None)
if not path:
    base = os.path.dirname(os.path.dirname(__file__))
    path = os.path.join(base, 'data', 'trades.jsonl')

if not os.path.exists(path):
    print("No trades.jsonl found at:", path)
    # still send a minimal startup embed
    embed = {
        'title': '🚀 Startup Status',
        'description': 'No trades data available yet.',
        'color': 3447003,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'footer': {'text': 'Moon Dev Challenger | Startup Report'},
    }
    ok = alerts._send_discord(embed=embed)
    print('Sent (no-data):', ok)
    raise SystemExit(0)

# read last 10 non-empty lines
lines = []
with open(path, 'r', encoding='utf-8') as fh:
    for ln in fh.read().splitlines():
        if ln.strip():
            lines.append(ln)

tail = lines[-10:]

net_pnl = 0.0
wins = 0
total = 0
jito_attempts = 0
jito_success = 0
latest_vhi = None

for ln in tail:
    try:
        obj = json.loads(ln)
    except Exception:
        continue
    pnl = None
    if 'pnl_sol' in obj:
        try:
            pnl = float(obj.get('pnl_sol') or 0.0)
        except Exception:
            pnl = None
    else:
        try:
            out = float(obj.get('expected_out_sol') or 0.0)
            inp = float(obj.get('input_amount_sol') or obj.get('amount_sol') or 0.0)
            pnl = out - inp
        except Exception:
            pnl = None

    if pnl is not None:
        net_pnl += pnl
        total += 1
        if pnl > 0:
            wins += 1
    if obj.get('jito_attempted'):
        jito_attempts += 1
        if obj.get('jito_succeeded'):
            jito_success += 1
    if latest_vhi is None and ('vhi' in obj or 'VHI' in obj):
        latest_vhi = obj.get('vhi') or obj.get('VHI')

win_rate = (wins / total * 100.0) if total else 0.0
jito_rate = (jito_success / jito_attempts * 100.0) if jito_attempts else 0.0

embed = {
    'title': '🚀 Startup Status',
    'description': (
        f'Net PnL: {net_pnl:.6f} SOL\n'
        f'Win Rate: {win_rate:.1f}%\n'
        f'Jito Land Rate: {jito_rate:.1f}%\n'
        f'Current VHI: {latest_vhi if latest_vhi is not None else "N/A"}\n'
        f'Sample Count: {total}'
    ),
    'color': 3066993,
    'timestamp': datetime.now(timezone.utc).isoformat(),
    'footer': {'text': 'Moon Dev Challenger | Startup Report'},
}

ok = alerts._send_discord(embed=embed)
print('Sent:', ok)
