#!/usr/bin/env python3
"""Live-fire test script: request tiny SOL->USDC quote and wrap swap in a Jito bundle.

WARNING: This script can submit real bundles when run with --live and appropriate env:
- SOLANA_PRIVATE_KEY
- RPC_URL pointing to mainnet
- JITO_ENABLED=1 and jito endpoints/PRIVATE_KEY configured

Usage: python scripts/live_fire_test.py [--live]
"""
from __future__ import annotations
import argparse
import asyncio
import time
import base64
import os
from rich.console import Console
from rich.panel import Panel

console = Console()

# Register global shield early for scripts to ensure repo-local AsyncClient
# imports are delegated through MarketBrain when possible.
try:
    from src.brain import MarketBrain as _MB
    _MB.register_global_shield()
except Exception:
    pass

import src.config as config
import src.trade_executor as te
try:
    from src.brain import MarketBrain
except Exception:
    MarketBrain = None
    console.print(Panel('Warning: failed to import MarketBrain — running in limited simulate-only mode', style='yellow'))

    class _StubJito:
        async def submit_atomic_exit(self, txs, simulate=True, **kwargs):
            # simulate a successful bundle simulation response
            return {'success': True, 'simulated': True, 'bundle_id': None}

        async def confirm_bundle_landing(self, bundle_id, max_slots=6, slot_time_s=0.4):
            return False

    class _StubBrain:
        def __init__(self, *args, **kwargs):
            self.jito = _StubJito()

        async def _compute_jito_tip(self):
            return int(getattr(config, 'JITO_DEFAULT_TIP_LAMPORTS', 10000))

    # instantiate stub for local simulate-only runs
    MarketBrain = _StubBrain

WSOL = "So11111111111111111111111111111111111111112"


async def main(live: bool = False):
    brain = MarketBrain(rpc=os.getenv('RPC_URL'))

    # Strict safety: enforce max slippage 0.1% (10 bps)
    slippage_bps = 10

    # tiny amount: 0.0001 SOL
    amount_sol = 0.0001
    lamports = int(amount_sol * 1e9)

    usdc = getattr(config, 'USDC_ADDRESS', None)
    if not usdc:
        console.print(Panel('USDC mint not configured in src.config.USDC_ADDRESS', style='red'))
        return

    console.print(Panel(f'Requesting Jupiter quote for {amount_sol} SOL ({lamports} lamports) -> USDC, slippage={slippage_bps}bps'))
    try:
        quote = await te.get_jupiter_quote(WSOL, usdc, lamports, slippage_bps)
    except Exception as e:
        console.print(Panel(f'Failed to fetch quote: {e}', style='red'))
        return

    console.print(Panel('Quote received — requesting swap transaction from Jupiter...', style='cyan'))
    try:
        swap_resp = await te.get_jupiter_swap(quote, user_pubkey=str(te.load_key().pubkey()), wrap_and_unwrap=True)
    except Exception as e:
        console.print(Panel(f'Failed to fetch swap transaction: {e}', style='red'))
        return

    swap_tx_b64 = swap_resp.get('swapTransaction')
    if not swap_tx_b64:
        console.print(Panel(f'No swapTransaction in swap response: {swap_resp}', style='red'))
        return

    swap_tx_bytes = base64.b64decode(swap_tx_b64)
    try:
        tx1 = te.VersionedTransaction.from_bytes(swap_tx_bytes)
    except Exception:
        console.print(Panel('Failed to decode VersionedTransaction from Jupiter swap tx bytes', style='red'))
        return

    # prepend compute budget if available
    try:
        if te.ComputeBudgetProgram is not None:
            limit_ix = te.ComputeBudgetProgram.set_compute_unit_limit(200_000)
            price_ix = te.ComputeBudgetProgram.set_compute_unit_price(int(os.getenv('PRIORITY_FEE', '10000')))
            if hasattr(tx1.message, 'instructions'):
                tx1.message.instructions = [limit_ix, price_ix] + list(tx1.message.instructions)
    except Exception:
        pass

    key = te.load_key()
    vtx = te.VersionedTransaction(tx1.message, [key])
    raw = bytes(vtx)

    # Safety: if live requested, ensure environment permits it
    if live and (os.getenv('ENABLE_LIVE_EXITS', '0') in ('1','true','True')):
        simulate = False
    else:
        simulate = True

    # Use brain.jito to submit as atomic bundle
    if getattr(brain, 'jito', None) is None or os.getenv('JITO_ENABLED', '0') not in ('1','true','True'):
        console.print(Panel('Jito not enabled or not configured. Set JITO_ENABLED=1 and Jito endpoints/PRIVATE_KEY.', style='yellow'))

    tip = await brain._compute_jito_tip() if hasattr(brain, '_compute_jito_tip') else int(getattr(config, 'JITO_DEFAULT_TIP_LAMPORTS', 10000))

    console.print(Panel(f'Preparing bundle (simulate={simulate}) tip={tip}'))
    resp = await brain.jito.submit_atomic_exit([raw], simulate=simulate, symbol='SOL->USDC', mint=usdc, tip_lamports=tip)

    console.print(Panel(f'Bundle submission response: {resp}'))
    bundle_id = None
    if isinstance(resp, dict):
        bundle_id = resp.get('bundle_id') or resp.get('id')

    # wait for landing confirmation
    if bundle_id:
        console.print(Panel(f'Waiting for bundle landing confirmation for {bundle_id}...'))
        ok = await brain.jito.confirm_bundle_landing(bundle_id, max_slots=6, slot_time_s=0.4)
        if ok:
            # print explorer URL guess
            rpc = os.getenv('RPC_URL','')
            cluster = 'mainnet-beta' if 'mainnet' in (rpc or '') or rpc == '' else 'custom'
            explorer = f'https://explorer.solana.com/tx/{bundle_id}?cluster={cluster}'
            console.print(Panel(f'Bundle landed! Explorer: {explorer}', style='green'))
        else:
            console.print(Panel('Bundle did not land in time or failed.', style='red'))
    else:
        console.print(Panel('No bundle id returned; check response for details.', style='yellow'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', help='Permit live submission when environment allows')
    args = parser.parse_args()
    try:
        asyncio.run(main(live=args.live))
    except KeyboardInterrupt:
        console.print('Interrupted', style='yellow')
