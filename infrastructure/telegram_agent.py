#!/usr/bin/env python3
"""Simple Telegram agent to listen for /KILL and trigger MarketBrain emergency halt.

This uses python-telegram-bot v22+ Application-based API.

Environment:
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID (optional, used for targeted messages)

Usage: python infrastructure/telegram_agent.py --brain-module src.brain:MarketBrain
"""
from __future__ import annotations
import os
import argparse
import asyncio
from pathlib import Path
from typing import Callable

try:
    from telegram import Update
    from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
except Exception:
    ApplicationBuilder = None


def make_kill_handler(brain) -> Callable[[Update, ContextTypes.DEFAULT_TYPE], None]:
    async def _kill(update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            # write the KILL file to data/
            repo_root = Path(__file__).resolve().parents[1]
            data_dir = repo_root / 'data'
            data_dir.mkdir(parents=True, exist_ok=True)
            kill_path = data_dir / 'KILL'
            kill_path.write_text('killed')
            # call emergency_halt if available
            try:
                if hasattr(brain, 'emergency_halt'):
                    brain.emergency_halt()
            except Exception:
                pass
            await update.message.reply_text('Emergency halt triggered. Jito disabled and inflight bundles cancelled.')
        except Exception as e:
            try:
                await update.message.reply_text(f'Failed to trigger emergency halt: {e}')
            except Exception:
                pass
    return _kill


def make_heartbeat_handler(brain) -> Callable[[Update, ContextTypes.DEFAULT_TYPE], None]:
    async def _hb(update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            # Try to obtain a simulated PnL reading from the brain if it exposes one.
            pnl_text = None
            try:
                if brain is not None:
                    # Preferred API: get_simulated_pnl() -> dict or number
                    if hasattr(brain, 'get_simulated_pnl') and callable(getattr(brain, 'get_simulated_pnl')):
                        sp = brain.get_simulated_pnl()
                        # support async or sync
                        if asyncio.iscoroutine(sp):
                            sp = await sp
                        if isinstance(sp, dict):
                            pnl_text = f"Simulated PnL: {sp.get('net', 'n/a')} | {sp.get('details', '')}"
                        else:
                            pnl_text = f"Simulated PnL: {sp!s}"
                    # Fallback: attribute 'simulated_pnl' (number or dict)
                    elif hasattr(brain, 'simulated_pnl'):
                        sp = getattr(brain, 'simulated_pnl')
                        pnl_text = f"Simulated PnL: {sp!s}"
            except Exception:
                pnl_text = None

            if pnl_text is None:
                # Generic fallback: report we have no PnL source but can still report signal counts
                sig_count = None
                try:
                    if brain is not None and hasattr(brain, 'signals'):
                        sig_count = len(getattr(brain, 'signals') or [])
                except Exception:
                    sig_count = None

                if sig_count is not None:
                    pnl_text = f"Simulated PnL: n/a (signals={sig_count})"
                else:
                    pnl_text = "Simulated PnL: n/a (no source available)"

            await update.message.reply_text(pnl_text)
        except Exception as e:
            try:
                await update.message.reply_text(f'Heartbeat failed: {e}')
            except Exception:
                pass
    return _hb


async def run_telegram_agent(brain, token: str):
    if ApplicationBuilder is None:
        print('python-telegram-bot not installed. Please pip install python-telegram-bot>=22.5')
        return
    app = ApplicationBuilder().token(token).build()
    kill_handler = CommandHandler('KILL', make_kill_handler(brain))
    hb_handler = CommandHandler('heartbeat', make_heartbeat_handler(brain))
    app.add_handler(kill_handler)
    app.add_handler(hb_handler)
    print('Telegram agent started. Listening for /KILL commands.')
    await app.initialize()
    await app.start()
    # idle forever; caller may run this in background
    await app.updater.start_polling()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--token', type=str, default=os.getenv('TELEGRAM_BOT_TOKEN'))
    args = parser.parse_args()
    if not args.token:
        print('TELEGRAM_BOT_TOKEN not provided; set env or pass --token')
    else:
        # try to import market brain lazily if present
        try:
            from src.brain import MarketBrain
            brain = MarketBrain()
        except Exception:
            brain = None
        try:
            asyncio.run(run_telegram_agent(brain, args.token))
        except KeyboardInterrupt:
            print('Telegram agent stopped')
