#!/usr/bin/env python3
"""Smoke test for Discord webhook alerts.

Usage:
  DISCORD_WEBHOOK_URL=<your webhook url> python scripts/smoke_discord.py

This will attempt to send a single green embed via the configured webhook.
"""
from __future__ import annotations

import os
import asyncio
from src.brain import MarketBrain

async def main():
    webhook = os.getenv('DISCORD_WEBHOOK_URL')
    mb = MarketBrain(start_monitor=False)
    # Start background monitors explicitly so we control lifecycle in the smoke script
    try:
        await mb.start()
    except Exception:
        pass
    if not webhook:
        print('DISCORD_WEBHOOK_URL not set. Set it in your environment to actually send a webhook.')
        # still call the function to exercise code paths; it will return False
        ok = await mb._send_discord_alert('Smoke test (dry-run): DISCORD_WEBHOOK_URL not set', success=True)
        print('send attempted, result:', ok)
        return

    print('Sending smoke alert to Discord webhook...')
    ok = await mb._send_discord_alert('Smoke test: Green embed from moon-dev-ai-agents', success=True)
    print('green embed send result:', ok)

    # Also send a mock "Trailing Stop Triggered" message to verify formatting
    ts_msg = '🚨 Trailing Stop Triggered (mock): Trade MINTX, current $0.123456, HWM $0.130000, drawdown 5.03%'
    ok2 = await mb._send_discord_alert(ts_msg, success=False)
    print('trailing-stop mock send result:', ok2)

    # Let the background loops initialize briefly (heartbeat/trailing stop)
    print('Waiting 5s for background loops to initialize...')
    await asyncio.sleep(5)
    print('Done.')

    # Stop background monitors cleanly before exiting
    try:
        await mb.stop()
    except Exception:
        pass

if __name__ == '__main__':
    asyncio.run(main())
