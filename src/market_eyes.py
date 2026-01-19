#!/usr/bin/env python3
"""Live data checks for SOL/USDC price and on-chain supply.

- Primary price: CoinGecko (fallback to Jupiter).
- Solana RPC: reuses a single AsyncClient and measures get_latest_blockhash latency (3 trials).

Prints a small report (rich table) with price, slot, min latency and simple validation.
"""
import asyncio
import time
import sys
import os

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

import httpx
from src.brain import MarketBrain

# make repo root importable when running the script directly
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.append(project_root)

import src.config as config

JUPITER_URL = "https://api.jup.ag/price/v2?ids=So11111111111111111111111111111111111111112"
console = Console()


async def fetch_jupiter_price(http_client: httpx.AsyncClient):
    t0 = time.perf_counter()
    resp = await http_client.get(JUPITER_URL)
    t1 = time.perf_counter()
    latency_ms = (t1 - t0) * 1000
    data = None
    price = None
    try:
        data = resp.json()
    except Exception:
        data = None

    if resp.status_code != 200:
        return None, latency_ms, {"status_code": resp.status_code, "text": resp.text, "data": data}

    try:
        arr = data.get("data") if isinstance(data, dict) else None
        if arr and len(arr) > 0:
            price = arr[0].get("price")
    except Exception:
        price = None

    return price, latency_ms, data


async def fetch_coingecko_price(http_client: httpx.AsyncClient):
    url = "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"
    t0 = time.perf_counter()
    resp = await http_client.get(url)
    t1 = time.perf_counter()
    latency_ms = (t1 - t0) * 1000
    try:
        data = resp.json()
        price = data.get("solana", {}).get("usd")
    except Exception:
        price = None
        data = None
    return price, latency_ms, data


async def main():
    # Determine RPC URL: prefer RPC_ENDPOINT env var, then config keys, then default
    rpc = os.getenv("RPC_ENDPOINT") or getattr(config, "RPC_URL", None) or getattr(config, "SOLANA_RPC", None) or "https://api.mainnet-beta.solana.com"

    trials = 3
    rpc_latencies = []
    price = None
    api_latency_ms = None
    coingecko_latency = None
    source_used = None
    last_slot = None
    circulating_sol = None

    # Reuse HTTP client for price APIs and MarketBrain shield for RPCs
    async with httpx.AsyncClient(timeout=10.0) as http_client:
        # Primary price source: CoinGecko (priority flip)
        try:
            price, coingecko_latency, _ = await fetch_coingecko_price(http_client)
            if price is not None:
                source_used = "CoinGecko"
        except Exception:
            price = None
            coingecko_latency = None

        # If CoinGecko failed, try Jupiter
        if price is None:
            try:
                price, api_latency_ms, _ = await fetch_jupiter_price(http_client)
                if price is not None:
                    source_used = "Jupiter"
            except Exception:
                price = None

        # Use MarketBrain shield for RPC interactions
        brain = MarketBrain(rpc=rpc)
        # capture last_slot and circulating supply once via JSON-RPC
        try:
            slot_resp = await brain._call_rpc('getSlot', [])
            last_slot = slot_resp.get('result') if isinstance(slot_resp, dict) else slot_resp
        except Exception:
            last_slot = None

        try:
            supply_resp = await brain._call_rpc('getSupply', [])
            supply_val = supply_resp.get('result', {}).get('value') if isinstance(supply_resp, dict) else supply_resp
            circ = None
            if isinstance(supply_val, dict):
                circ = supply_val.get('circulating')
            else:
                circ = getattr(supply_val, 'circulating', None)
            if circ is not None:
                try:
                    circulating_sol = int(circ) / 1e9
                except Exception:
                    circulating_sol = None
        except Exception:
            circulating_sol = None

        # Speed test: measure getLatestBlockhash latency across trials using the brain shield
        for i in range(trials):
            try:
                t0 = time.perf_counter()
                _ = await brain._call_rpc('getLatestBlockhash', [])
                t1 = time.perf_counter()
                rpc_latencies.append((t1 - t0) * 1000)
            except Exception:
                rpc_latencies.append(float('inf'))

    # take min latency as requested
    rpc_latency_ms = min(rpc_latencies) if rpc_latencies else None

    # Build report table
    table = Table(title="Market Eyes — SOL/USDC Snapshot", show_edge=False)
    table.add_column("Metric", style="cyan", no_wrap=True)
    table.add_column("Value", style="magenta")

    table.add_row("Current SOL Price (USD)", f"{price if price is not None else 'Error/No data'}")
    table.add_row("Price Source", source_used or "None")
    table.add_row("Last RPC Slot", str(last_slot))
    if api_latency_ms is not None:
        table.add_row("Jupiter API Latency (ms)", f"{api_latency_ms:.1f} ms")
    if coingecko_latency is not None:
        table.add_row("CoinGecko Latency (ms)", f"{coingecko_latency:.1f} ms")
    table.add_row("RPC get_latest_blockhash Min Latency (ms)", f"{rpc_latency_ms:.1f} ms" if rpc_latency_ms and rpc_latency_ms != float('inf') else "Error/No data")
    if circulating_sol is not None:
        table.add_row("Circulating Supply (SOL)", f"{circulating_sol:,.3f} SOL")
        if price is not None:
            try:
                market_cap = circulating_sol * float(price)
                table.add_row("Implied Market Cap (USD)", f"${market_cap:,.0f}")
            except Exception:
                pass
    else:
        table.add_row("Circulating Supply (SOL)", "Error/No data")

    panel = Panel(table, title="Market Eyes — Pre-Flight", expand=False)
    console.print(panel)

    # Simple validation / comparison logic
    notes = []
    if source_used is None:
        notes.append("Price source missing or unparsable")
    if circulating_sol is None:
        notes.append("On-chain circulating supply missing or unparsable")
    if rpc_latency_ms is None or rpc_latency_ms == float('inf'):
        notes.append("RPC latency tests failed")
    if not notes:
        console.print("All checks returned expected fields.")
    else:
        console.print("Validation issues:")
        for n in notes:
            console.print(f" - {n}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        console = Console()
        console.print(Panel(f"Fatal error running market_eyes: {e}", style="red"))
