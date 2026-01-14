#!/usr/bin/env python3
"""Fund wallet on Devnet via airdrops.

Requests 1 SOL airdrops (per iteration) to the address in `src.config.address`.
Waits for each airdrop to reach `finalized` before requesting the next.

Usage: .venv/bin/python src/fund_wallet.py [--count N]
"""
import asyncio
import os
import sys
import time
import argparse

from rich.console import Console
from rich.panel import Panel

from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey

# make repo root importable
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.append(project_root)

import src.config as config

console = Console()


async def wait_for_finalized(client: AsyncClient, signature: str, timeout: int = 120):
    """Poll signature status until confirmationStatus == 'finalized' or timeout."""
    start = time.time()
    while True:
        try:
            resp = await client.get_signature_statuses([signature])
            val = getattr(resp, "value", None) or resp
            # val is expected to be a list with one item
            status = None
            try:
                status = val[0]
            except Exception:
                status = val

            if status:
                # Many RPCs return a dict with 'confirmationStatus'
                conf = None
                if isinstance(status, dict):
                    conf = status.get("confirmationStatus") or status.get("confirmation_status")
                else:
                    conf = None

                if conf == "finalized":
                    return True

        except Exception:
            pass

        if time.time() - start > timeout:
            return False

        await asyncio.sleep(2)


async def main(count: int):
    address = getattr(config, "address", None)
    if not address:
        console.print(Panel("No `address` found in src/config.py", style="red"))
        raise SystemExit(1)

    # Determine RPC for airdrops: prefer DEVNET_RPC_URL/env, otherwise fallback to public devnet
    # Look for DEVNET_RPC_URL (we may have added it to .env earlier)
    rpc = os.getenv("DEVNET_RPC_URL") or os.getenv("DEVNET_RPC")
    if not rpc:
        # If no dedicated Devnet RPC env var, try to construct from our Alchemy key if present
        alchemy_key = os.getenv("ALCHEMY_API_KEY") or getattr(config, "ALCHEMY_API_KEY", None)
        if alchemy_key:
            rpc = f"https://solana-devnet.g.alchemy.com/v2/{alchemy_key}"
            note = f"Using Alchemy Devnet RPC: {rpc}"
        else:
            rpc = "https://api.devnet.solana.com"
            note = f"Using public Devnet RPC: {rpc}"
    else:
        note = f"Using DEVNET RPC from env: {rpc}"

    pub = Pubkey.from_string(address)

    async with AsyncClient(rpc) as client:
        # show starting balance
        bal_resp = await client.get_balance(pub)
        bal = getattr(bal_resp, "value", bal_resp)
        prev_balance = int(bal) if bal is not None else 0

        console.print(Panel(f"Starting airdrop run\nAddress: {address}\n{note}", title="Fund Wallet", expand=False))

        for i in range(count):
            console.print(f"Requesting airdrop #{i+1} of 1.0 SOL...")
            amounts = [int(1e9), int(0.5e9)]  # try 1 SOL first, then 0.5 SOL
            success = False
            last_error = None
            for amount in amounts:
                attempt = 0
                max_attempts = 5
                while attempt < max_attempts:
                    attempt += 1
                    try:
                        sig_resp = await client.request_airdrop(pub, amount)
                        sig = getattr(sig_resp, "result", None) or sig_resp
                        if isinstance(sig, dict) and "value" in sig:
                            sig = sig.get("value")

                        if not sig:
                            last_error = "No signature returned"
                            console.print(f"Attempt {attempt}/{max_attempts} for {amount/1e9} SOL failed: no signature", style="red")
                            await asyncio.sleep(5)
                            continue

                        console.print(f"Airdrop tx sig: {sig}; waiting for 'finalized'...", style="yellow")
                        ok = await wait_for_finalized(client, sig, timeout=180)
                        if not ok:
                            last_error = "Timed out waiting for finalized confirmation"
                            console.print(f"Attempt {attempt}/{max_attempts} for {amount/1e9} SOL: timed out", style="red")
                            await asyncio.sleep(5)
                            continue

                        # success
                        new_bal_resp = await client.get_balance(pub)
                        new_bal = getattr(new_bal_resp, "value", new_bal_resp)
                        new_balance = int(new_bal) if new_bal is not None else prev_balance

                        prev_sol = prev_balance / 1e9
                        new_sol = new_balance / 1e9

                        console.print(Panel(f"Previous Balance: {prev_sol:.9f} SOL\nNew Balance: {new_sol:.9f} SOL", title=f"Airdrop #{i+1} Result", expand=False))

                        prev_balance = new_balance
                        success = True
                        break

                    except Exception as e:
                        last_error = str(e)
                        # If internal error, backoff and retry
                        if "Internal" in str(e) or "internal" in str(e).lower():
                            console.print(f"Attempt {attempt}/{max_attempts} for {amount/1e9} SOL: Internal error — retrying in 5s", style="yellow")
                            await asyncio.sleep(5)
                            continue
                        else:
                            console.print(Panel(f"Airdrop attempt failed: {e}", style="red"))
                            attempt = max_attempts
                            break

                if success:
                    break
                else:
                    console.print(f"Failed to airdrop {amount/1e9} SOL after {max_attempts} attempts. Trying next smaller amount if available...", style="yellow")

            if not success:
                console.print(Panel(f"All attempts failed for airdrop #{i+1}. Last error: {last_error}", style="red"))
                break

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=1, help="Number of 1.0 SOL airdrops to request")
    args = parser.parse_args()

    try:
        asyncio.run(main(args.count))
    except KeyboardInterrupt:
        console.print("Interrupted by user", style="yellow")
