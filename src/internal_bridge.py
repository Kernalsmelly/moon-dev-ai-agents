#!/usr/bin/env python3
"""Internal bridge: send SOL from a funded key to our bot key.

Usage:
- Set FUNDER_PRIVATE_KEY in your environment (base58) or in .env as FUNDER_PRIVATE_KEY.
- The script will send 0.5 SOL to the bot address (GikBZn... by default).

This script deliberately does not log private keys. Provide keys locally only.
"""
import os
import asyncio
import base64
import sys
from rich.console import Console
from rich.panel import Panel

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.message import MessageV0
from solders.system_program import transfer as sp_transfer, TransferParams as SPTransferParams
from solders.transaction import VersionedTransaction
from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TxOpts

console = Console()


def load_dotenv_into_env(path='.env'):
    if not os.path.exists(path):
        return
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' not in line:
                continue
            k, v = line.split('=', 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k not in os.environ:
                os.environ[k] = v


async def bridge(funder_b58: str, recipient: str, amount_sol: float = 0.5, rpc: str | None = None):
    try:
        funder = Keypair.from_base58_string(funder_b58)
    except Exception as e:
        console.print(Panel(f"Invalid funder private key: {e}", style="red"))
        return 1

    rpc = rpc or os.getenv('RPC_URL') or os.getenv('DEVNET_RPC_URL') or 'https://api.devnet.solana.com'
    recipient_pub = Pubkey.from_string(recipient)
    payer_pub = funder.pubkey()
    lamports = int(amount_sol * 1e9)

    console.print(Panel(f"Bridge: {amount_sol} SOL from {payer_pub} -> {recipient}", title="Internal Bridge"))

    async with AsyncClient(rpc) as client:
        # Show funder balance
        try:
            bal = await client.get_balance(payer_pub)
            b = getattr(bal, 'value', None)
            console.print(f"Funder balance (lamports): {b}")
            if not b or int(b) < lamports + 1000:
                console.print(Panel("Funder does not have enough SOL to send. Aborting.", style="red"))
                return 2
        except Exception as e:
            console.print(Panel(f"Failed to fetch funder balance: {e}", style="red"))
            return 3

        # Get latest blockhash
        try:
            lb = await client.get_latest_blockhash()
            recent = None
            try:
                recent = lb.value.blockhash
            except Exception:
                recent = getattr(lb, 'value', None)
                if isinstance(recent, dict):
                    recent = recent.get('blockhash')
        except Exception as e:
            console.print(Panel(f"Failed to fetch latest blockhash: {e}", style="red"))
            return 4

        if not recent:
            console.print(Panel("No recent blockhash available. Aborting.", style="red"))
            return 5

        # Build instruction and MessageV0
        instr = sp_transfer(SPTransferParams(from_pubkey=payer_pub, to_pubkey=recipient_pub, lamports=lamports))
        try:
            msg = MessageV0.try_compile(payer_pub, [instr], [], recent)
        except Exception as e:
            console.print(Panel(f"Failed to compile message: {e}", style="red"))
            return 6

        try:
            vtx = VersionedTransaction(msg, [funder])
        except Exception as e:
            console.print(Panel(f"Failed to create/sign transaction: {e}", style="red"))
            return 7

        signed_bytes = bytes(vtx)

        console.print("Sending transaction...")
        try:
            send_resp = await client.send_raw_transaction(signed_bytes, opts=TxOpts(skip_preflight=False))
            sig = getattr(send_resp, 'value', send_resp)
            console.print(Panel(f"Bridge tx signature: {sig}", style="green"))
        except Exception as e:
            console.print(Panel(f"Failed to send transaction: {e}", style="red"))
            return 8

        # Optionally wait for confirmation (simple poll)
        console.print("Waiting for confirmation (short poll)...")
        for i in range(12):
            try:
                resp = await client.get_signature_statuses([sig])
                st = getattr(resp, 'value', None)
                if st and st[0] is not None:
                    console.print(Panel(f"Bridge confirmed: {st[0]}", style="green"))
                    return 0
            except Exception:
                pass
            await asyncio.sleep(1)

        console.print(Panel("Bridge sent but not yet confirmed (timed out). Check the signature on devnet explorer.", style="yellow"))
        return 0


if __name__ == '__main__':
    load_dotenv_into_env()
    FUNDER = os.getenv('FUNDER_PRIVATE_KEY')
    if not FUNDER:
        console.print(Panel('No FUNDER_PRIVATE_KEY found in environment. Set FUNDER_PRIVATE_KEY in your shell or .env and re-run.', style='red'))
        sys.exit(1)

    RECIPIENT = os.getenv('BRIDGE_RECIPIENT') or 'GikBZnDSKa1M1TN84J8dtCqkkor7wUpGoV7nZcg5Zfpi'
    AMOUNT = float(os.getenv('BRIDGE_AMOUNT_SOL') or 0.5)
    rpc = os.getenv('RPC_URL') or os.getenv('DEVNET_RPC_URL')

    code = asyncio.run(bridge(FUNDER, RECIPIENT, AMOUNT, rpc))
    sys.exit(code)
