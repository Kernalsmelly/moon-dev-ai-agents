#!/usr/bin/env python3
"""Test Discord alerts.

Run this after setting DISCORD_WEBHOOK in your .env file.

Usage:
    python scripts/test_discord_alerts.py
"""
import os
import sys

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Load .env
def load_env():
    env_path = os.path.join(PROJECT_ROOT, '.env')
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key not in os.environ:
                        os.environ[key] = value

load_env()

from src.alerts import (
    _get_webhook_url,
    send_trade_alert,
    send_system_alert,
    send_daily_summary,
    send_startup_alert,
)

def main():
    print("=" * 50)
    print("  Discord Alert Test")
    print("=" * 50)
    print()

    webhook = _get_webhook_url()
    if not webhook:
        print("ERROR: No DISCORD_WEBHOOK configured!")
        print()
        print("To set up Discord alerts:")
        print("1. Go to your Discord server")
        print("2. Right-click the channel -> Edit Channel -> Integrations")
        print("3. Click 'Create Webhook' and copy the URL")
        print("4. Add to your .env file:")
        print("   DISCORD_WEBHOOK=https://discord.com/api/webhooks/...")
        print()
        return

    print(f"Webhook: {webhook[:50]}...")
    print()

    # Test 1: System alert
    print("[1/4] Sending startup alert...")
    if send_startup_alert('SHADOW'):
        print("      OK")
    else:
        print("      FAILED")

    # Test 2: Trade alert (win)
    print("[2/4] Sending winning trade alert...")
    if send_trade_alert(
        symbol='SOL',
        side='SELL',
        entry_price=150.0,
        exit_price=165.0,
        pnl_usd=2.50,
        pnl_pct=10.0,
        exit_reason='TP1',
        amount_usd=25.0,
        session_pnl=15.0,
        session_record='3W/1L',
    ):
        print("      OK")
    else:
        print("      FAILED")

    # Test 3: Trade alert (loss)
    print("[3/4] Sending losing trade alert...")
    if send_trade_alert(
        symbol='BONK',
        side='SELL',
        entry_price=0.00005,
        exit_price=0.000045,
        pnl_usd=-2.50,
        pnl_pct=-10.0,
        exit_reason='SL',
        amount_usd=25.0,
    ):
        print("      OK")
    else:
        print("      FAILED")

    # Test 4: Daily summary
    print("[4/4] Sending daily summary...")
    if send_daily_summary():
        print("      OK")
    else:
        print("      FAILED")

    print()
    print("Done! Check your Discord channel.")


if __name__ == '__main__':
    main()
