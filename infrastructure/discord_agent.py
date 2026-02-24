"""Lightweight Discord agent facade.

This module provides helpers to build Embed objects and (best-effort) send
messages to a configured Discord channel. It is intentionally tolerant of
missing `discord` library so unit tests can import and verify embed
construction without requiring an actual Discord client.

Environment:
- DISCORD_TOKEN
- DISCORD_CHANNEL_ID

Functions exported:
- create_signal_embed(signal_data) -> Embed-like object
- create_session_embed(stats) -> Embed-like object
- send_signal_embed(signal_data) -> coroutine that attempts to send (best-effort)
- send_session_report(stats) -> coroutine that attempts to send (best-effort)
- run_discord_agent(brain) -> starts the bot (if discord lib present)
"""
from __future__ import annotations
import os
import asyncio
from typing import Any, Dict, List

try:
    import discord
    from discord import Embed
    HAS_DISCORD = True
except Exception:
    # Minimal Embed shim for tests and dry-run environments
    HAS_DISCORD = False

    class Embed:
        def __init__(self, title: str = None, description: str = None, color: int = 0x000000):
            self.title = title
            self.description = description
            self.color = color
            self.fields: List[Dict[str, Any]] = []
            self.url = None

        def add_field(self, name: str, value: str, inline: bool = False):
            self.fields.append({'name': name, 'value': value, 'inline': inline})

        def set_thumbnail(self, url: str):
            self.thumbnail = {'url': url}

        def set_footer(self, text: str):
            self.footer = {'text': text}


def _make_embed_base(title: str, description: str | None = None, color: int | None = None):
    if color is None:
        color = 0x00FF00
    return Embed(title=title, description=description, color=color)


def create_signal_embed(signal_data: dict) -> Embed:
    """Create an embed summarizing a signal.

    Expected keys in signal_data: mint, vhi_score, graduation_action, tx_sig (optional)
    """
    mint = signal_data.get('mint') or 'Unknown'
    vhi = signal_data.get('vhi_score') or signal_data.get('vhi') or 'n/a'
    grad = signal_data.get('graduation_action') or signal_data.get('graduation') or 'n/a'
    embed = _make_embed_base(f"Signal: {mint}", description=f"VHI: {vhi}")
    embed.add_field('Mint', str(mint), inline=True)
    embed.add_field('VHI Score', str(vhi), inline=True)
    embed.add_field('Graduation', str(grad), inline=False)
    tx = signal_data.get('tx_sig')
    if tx:
        # include solscan link in a field
        embed.add_field('Transaction', f"https://solscan.io/tx/{tx}", inline=False)
    return embed


def create_session_embed(stats: dict) -> Embed:
    net = stats.get('net_sol', 0.0)
    color = 0x00FF00 if net >= 0 else 0xFF0000
    title = "Session Report"
    desc = f"Net PnL: {net:+.4f} SOL"
    embed = _make_embed_base(title, description=desc, color=color)
    embed.add_field('Win Rate', f"{int(round((stats.get('win_rate', 0.0) * 100)))}%", inline=True)
    embed.add_field('Count', str(stats.get('count', 0)), inline=True)
    top = stats.get('top_performer') or {}
    if top and top.get('mint'):
        embed.add_field('Top Performer', str(top.get('mint')) + f" (+{int(round(top.get('pct',0.0)*100))}%)", inline=False)
        if top.get('tx_sig'):
            embed.add_field('View Transaction', f"https://solscan.io/tx/{top.get('tx_sig')}", inline=False)
    return embed


async def send_signal_embed(signal_data: dict) -> bool:
    """Best-effort send of a signal embed to configured DISCORD_CHANNEL_ID.

    If discord.py is not installed, this function simply returns True after
    constructing the embed so tests can inspect it.
    """
    embed = create_signal_embed(signal_data)
    channel_id = os.getenv('DISCORD_CHANNEL_ID')
    token = os.getenv('DISCORD_TOKEN')
    if not HAS_DISCORD or not token or not channel_id:
        # dry-run mode: return True and expose embed via attribute for tests
        # attach embed to signal_data for test inspection
        signal_data['_embed'] = embed
        return True
    # If discord is available, perform an actual send (best-effort)
    try:
        client = discord.Client(intents=discord.Intents.default())

        @client.event
        async def on_ready():
            try:
                ch = client.get_channel(int(channel_id))
                if ch is None:
                    # try fetch
                    ch = await client.fetch_channel(int(channel_id))
                await ch.send(embed=embed)
            finally:
                await client.close()

        await client.start(token)
        return True
    except Exception:
        return False


async def send_session_report(stats: dict) -> bool:
    embed = create_session_embed(stats)
    channel_id = os.getenv('DISCORD_CHANNEL_ID')
    token = os.getenv('DISCORD_TOKEN')
    if not HAS_DISCORD or not token or not channel_id:
        stats['_embed'] = embed
        return True
    try:
        client = discord.Client(intents=discord.Intents.default())

        @client.event
        async def on_ready():
            try:
                ch = client.get_channel(int(channel_id))
                if ch is None:
                    ch = await client.fetch_channel(int(channel_id))
                await ch.send(embed=embed)
            finally:
                await client.close()

        await client.start(token)
        return True
    except Exception:
        return False


async def send_text(message: str) -> bool:
    """Send a plain text message to configured Discord channel (best-effort).

    In dry-run (discord.py not installed or env vars missing) attach the
    message to a global sentinel for test inspection and return True.
    """
    channel_id = os.getenv('DISCORD_CHANNEL_ID')
    token = os.getenv('DISCORD_TOKEN')
    if not HAS_DISCORD or not token or not channel_id:
        # for tests, expose a lightweight store
        try:
            _DRYRUN_STORE = globals().setdefault('_DRYRUN_STORE', {})
            _DRYRUN_STORE.setdefault('messages', []).append(str(message))
        except Exception:
            pass
        return True
    try:
        client = discord.Client(intents=discord.Intents.default())

        @client.event
        async def on_ready():
            try:
                ch = client.get_channel(int(channel_id))
                if ch is None:
                    ch = await client.fetch_channel(int(channel_id))
                await ch.send(str(message))
            finally:
                await client.close()

        await client.start(token)
        return True
    except Exception:
        return False


async def run_discord_agent(brain):
    """Optional runner that starts a discord.py Bot with basic slash commands.

    This is safe to import when `discord` is missing; the function will be a
    no-op in that case.
    """
    if not HAS_DISCORD:
        return False
    intents = discord.Intents.all()
    bot = discord.Bot(intents=intents)

    @bot.event
    async def on_ready():
        print(f"Discord bot ready: {bot.user}")

    @bot.command(name='heartbeat')
    async def heartbeat(ctx):
        try:
            sp = brain.get_simulated_pnl() if hasattr(brain, 'get_simulated_pnl') else None
            if asyncio.iscoroutine(sp):
                sp = await sp
            await ctx.respond(str(sp))
        except Exception as e:
            await ctx.respond(f"Heartbeat error: {e}")

    @bot.command(name='KILL')
    async def kill(ctx):
        try:
            import src.config as config
            config.LIVE_TRADING_ENABLED = False
            await ctx.respond('SHUTDOWN: LIVE_TRADING_ENABLED set to False')
        except Exception as e:
            await ctx.respond(f'Failed to shutdown: {e}')

    token = os.getenv('DISCORD_TOKEN')
    if not token:
        return False
    try:
        await bot.start(token)
        return True
    except Exception:
        return False
