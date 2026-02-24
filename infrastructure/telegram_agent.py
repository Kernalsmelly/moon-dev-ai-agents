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
    from datetime import time as dtime, timezone
    import asyncio as _asyncio
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
            # Prefer sending an operator alert via MarketBrain's Discord webhook sender if available
            try:
                if brain is not None and hasattr(brain, '_send_discord_alert'):
                    try:
                        await brain._send_discord_alert('Emergency halt triggered. Jito disabled and inflight bundles cancelled.', success=False)
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                await update.message.reply_text('Emergency halt triggered. Jito disabled and inflight bundles cancelled.')
            except Exception:
                pass
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

            # Compose richer heartbeat including active signals and top strategy
            try:
                if isinstance(sp, dict):
                    net = sp.get('net_sol') or sp.get('net') or sp.get('net_sol') or 0.0
                    count = sp.get('count') or 0
                    pnl_line = f"Simulated PnL: {float(net):+.4f} SOL"
                else:
                    pnl_line = pnl_text
                    count = 0
            except Exception:
                pnl_line = pnl_text
                count = 0

            # Active signals: inspect brain.simulated_trades if available
            active_signals = 0
            top_strategy = 'None'
            try:
                if brain is not None and hasattr(brain, 'simulated_trades'):
                    trades = getattr(brain, 'simulated_trades') or []
                    active_signals = len([t for t in trades if t.get('status') == 'simulated'])
                    # compute most common strategy
                    from collections import Counter
                    stats = Counter([t.get('strategy') or 'Unknown' for t in trades if t.get('strategy')])
                    if stats:
                        top_strategy = stats.most_common(1)[0][0]
            except Exception:
                active_signals = active_signals

            msg = f"{pnl_line}\nActive Signals: {active_signals}\nTop Strategy: [{top_strategy}]"
            # Send heartbeat via MarketBrain Discord sender if configured; otherwise fallback to Telegram reply
            try:
                if brain is not None and hasattr(brain, '_send_discord_alert'):
                    try:
                        await brain._send_discord_alert(msg, success=True)
                        return
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                await update.message.reply_text(msg)
            except Exception:
                pass
        except Exception as e:
            try:
                await update.message.reply_text(f'Heartbeat failed: {e}')
            except Exception:
                pass
    return _hb


def _format_session_message(session_name: str, stats: dict) -> str:
    try:
        # This formatter targets MarkdownV2 output — caller must pass parse_mode accordingly.
        net = stats.get('net_sol', 0.0)
        win_rate = int(round((stats.get('win_rate', 0.0) * 100)))
        avg_gain = stats.get('avg_gain_pct', 0.0) * 100.0
        missed = stats.get('alpha_missed', 0)
        top = stats.get('top_performer') or {}

        def _fmt_top(top):
            if not top or not top.get('mint'):
                return 'None'
            ticker = str(top.get('mint'))
            try:
                pct = int(round(top.get('pct', 0.0) * 100))
                return f"{ticker} (+{pct}%)"
            except Exception:
                return f"{ticker} ({top.get('pct')})"

        top_str = _fmt_top(top)

        msg = f"📊 {session_name} Session Report\n\nNet PnL: {net:+.4f} SOL\n\nWin Rate: {win_rate}%\n\nNoise Filtered: {missed} 'Ghost' Signals ignored.\n\nTop Performer: {top_str}"
        return msg
    except Exception:
        return f"📊 {session_name} Session Report\n\nNo data available."


def markdown_escape(text: str) -> str:
    """Escape text for Telegram MarkdownV2."""
    if text is None:
        return ''
    try:
        # Characters to escape in MarkdownV2
        esc_chars = r"_\*\[\]\(\)~`>#\+\-\=\|\{\}\\.!"
        # Simple iterative replace — keep it deterministic
        out = str(text)
        for ch in ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!','\\']:
            out = out.replace(ch, f"\\{ch}")
        return out
    except Exception:
        return str(text)


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
    # schedule automated session reports using the JobQueue
    try:
        chat_id = os.getenv('TELEGRAM_CHAT_ID')
        if chat_id:
            # helper to schedule a non-blocking job that delegates heavy work
            def _make_job(session_name: str):
                async def _job(context: ContextTypes.DEFAULT_TYPE):
                    # run the heavy report generation in background so JobQueue
                    # handler returns quickly and doesn't block other tasks.
                    async def _do():
                        try:
                            stats = None
                            if hasattr(brain, 'get_session_stats'):
                                try:
                                    stats = brain.get_session_stats(session_name)
                                    if _asyncio.iscoroutine(stats):
                                        stats = await stats
                                except Exception:
                                    stats = None
                            # If we have a top performer tx signature, build a Solscan link
                            solscan = None
                            try:
                                top = (stats or {}).get('top_performer') or {}
                                tx = top.get('tx_sig') if isinstance(top, dict) else None
                                if tx and hasattr(brain, 'get_solscan_url'):
                                    try:
                                        solscan = brain.get_solscan_url(tx)
                                    except Exception:
                                        solscan = None
                            except Exception:
                                solscan = None

                            # Format message and escape for MarkdownV2
                            raw_msg = _format_session_message(session_name.upper(), stats or {})
                            try:
                                # Build message body; prefer sending via MarketBrain Discord webhook
                                body = markdown_escape(raw_msg)
                                if solscan:
                                    # Discord-friendly raw URL appended
                                    body = body + "\n\n" + f"🔗 View Transaction: {solscan}"
                                # If brain exposes the discord sender, use it
                                if brain is not None and hasattr(brain, '_send_discord_alert'):
                                    try:
                                        await brain._send_discord_alert(body, tx_sig=tx, success=True)
                                        return
                                    except Exception:
                                        pass
                                # Fallback to Telegram send
                                try:
                                    await context.bot.send_message(chat_id=chat_id, text=body, parse_mode='MarkdownV2')
                                except Exception:
                                    pass
                            except Exception:
                                pass
                        except Exception:
                            pass
                    # dispatch background task and return immediately
                    try:
                        _asyncio.create_task(_do())
                    except Exception:
                        # fallback: run synchronously (best-effort)
                        await _do()
                return _job

            # Asia Wrap-up: 08:00 UTC
            try:
                app.job_queue.run_daily(_make_job('asia'), time=dtime(hour=8, minute=0, tzinfo=timezone.utc))
            except Exception:
                pass

            # NY Open Impact: 16:00 UTC
            try:
                app.job_queue.run_daily(_make_job('ny_open'), time=dtime(hour=16, minute=0, tzinfo=timezone.utc))
            except Exception:
                pass

            # Daily PnL Summary: 00:00 UTC
            try:
                app.job_queue.run_daily(_make_job('daily'), time=dtime(hour=0, minute=0, tzinfo=timezone.utc))
            except Exception:
                pass
    except Exception:
        pass
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
