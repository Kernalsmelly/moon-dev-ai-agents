#!/usr/bin/env python3
"""Lightweight connectivity check for Solana RPC and wallet balance.

Prints a small 'Pre-Flight Report' using rich.
"""
import asyncio
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey

import os
import sys

# make repo root importable when running the script directly
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.append(project_root)

import src.config as config

console = Console()


async def run_check():
    rpc = getattr(config, "RPC_URL", None) or getattr(config, "SOLANA_RPC", None) or "https://api.mainnet-beta.solana.com"
    address = getattr(config, "address", None)

    table = Table(title="Pre-Flight Report", show_edge=False)
    table.add_column("Check", style="cyan", no_wrap=True)
    table.add_column("Result", style="magenta")

    async with AsyncClient(rpc) as client:
        try:
            connected = await client.is_connected()
        except Exception as e:
            connected = False
            table.add_row("is_connected()", f"Error: {e}")

        if connected:
            table.add_row("is_connected()", "✅ Connected")
        else:
            table.add_row("is_connected()", "❌ Not connected")

        # Block height
        try:
            bh_resp = await client.get_block_height()
            bh = bh_resp.value if hasattr(bh_resp, 'value') else bh_resp
            table.add_row("block_height", str(bh))
        except Exception as e:
            table.add_row("block_height", f"Error: {e}")

        # Balance for configured address
        if not address:
            table.add_row("wallet_address", "⚠️ No config.address set")
        else:
            try:
                pub = Pubkey.from_string(address)
                bal_resp = await client.get_balance(pub)
                bal = bal_resp.value if hasattr(bal_resp, 'value') else bal_resp
                # balance is in lamports
                sol = int(bal) / 1e9
                table.add_row("wallet_address", address)
                table.add_row("balance_SOL", f"{sol:.9f} SOL ({bal} lamports)")
            except Exception as e:
                table.add_row("balance", f"Error: {e}")

    panel = Panel(table, title="Moon Dev — Pre-Flight Report", expand=False)
    console.print(panel)


def main():
    try:
        asyncio.run(run_check())
    except Exception as e:
        console.print(Panel(f"Fatal error running connection test: {e}", style="red"))


if __name__ == "__main__":
    main()
