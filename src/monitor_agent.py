#!/usr/bin/env python3
"""Moon Dev Market Monitor - simple live dashboard

Runs an async loop every 60s (configurable) that:
- Fetches SOL price from CoinGecko
- Fetches wallet balance from RPC
- Keeps last 5 prices and detects simple bullish trend

Usage: .venv/bin/python src/monitor_agent.py [--interval SECONDS] [--iterations N]
"""
import asyncio
import os
import sys
import time
from collections import deque
import argparse

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.live import Live

import httpx
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey

# make repo root importable
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.append(project_root)

import src.config as config

console = Console()


async def fetch_price(http_client: httpx.AsyncClient):
    url = "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"
    try:
        t0 = time.perf_counter()
        resp = await http_client.get(url, timeout=10.0)
        t1 = time.perf_counter()
        latency_ms = (t1 - t0) * 1000
        data = resp.json()
        price = data.get("solana", {}).get("usd")
        return float(price) if price is not None else None, latency_ms
    except Exception:
        return None, None


async def get_balance(client: AsyncClient, pub: Pubkey):
    try:
        resp = await client.get_balance(pub)
        bal = getattr(resp, "value", resp)
        return int(bal) / 1e9
    except Exception:
        return None


def build_panel(price, price_latency, balance, last_prices, interval):
    table = Table.grid(expand=True)
    table.add_column(justify="left")
    table.add_column(justify="right")

    table.add_row("Current SOL Price (USD)", f"{price:.2f}" if price is not None else "N/A")
    table.add_row("Price Latency (ms)", f"{price_latency:.1f} ms" if price_latency is not None else "N/A")
    table.add_row("Wallet Balance", f"{balance:.6f} SOL" if balance is not None else "N/A")
    if last_prices:
        avg = sum(last_prices) / len(last_prices)
        table.add_row("Avg (last %d)" % len(last_prices), f"{avg:.2f}")

    # trend detection
    trend = ""
    if last_prices and price is not None and len(last_prices) >= 1:
        avg = sum(last_prices) / len(last_prices)
        if price > avg:
            trend = "📈 BULLISH TREND DETECTED"

    panel = Panel(table, title="LIVE DASHBOARD", subtitle=f"Update every {interval}s")
    return panel, trend


async def monitor_loop(interval: int = 60, iterations: int = 0):
    # Determine RPC URL
    rpc = os.getenv("RPC_URL") or getattr(config, "RPC_URL", None) or getattr(config, "SOLANA_RPC", None) or "https://api.mainnet-beta.solana.com"
    address = getattr(config, "address", None)
    if not address:
        console.print(Panel("No address found in src/config.py", style="red"))
        return
    pub = Pubkey.from_string(address)

    last_prices = deque(maxlen=5)

    async with httpx.AsyncClient() as http_client, AsyncClient(rpc) as rpc_client:
        count = 0
        with Live(refresh_per_second=4) as live:
            while True:
                count += 1
                price, price_latency = await fetch_price(http_client)
                balance = await get_balance(rpc_client, pub)

                if price is not None:
                    last_prices.append(price)

                panel, trend = build_panel(price, price_latency, balance, list(last_prices), interval)
                if trend:
                    live.update(Panel(panel.renderable, title="LIVE DASHBOARD — " + trend))
                else:
                    live.update(panel)

                if iterations and count >= iterations:
                    break

                await asyncio.sleep(interval)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=60, help="Seconds between checks")
    parser.add_argument("--iterations", type=int, default=0, help="Number of iterations to run (0 = infinite)")
    args = parser.parse_args()

    try:
        asyncio.run(monitor_loop(interval=args.interval, iterations=args.iterations))
    except KeyboardInterrupt:
        console.print("Monitor stopped", style="yellow")


if __name__ == "__main__":
    main()
