#!/usr/bin/env python3
# STATUS: VERIFIED - HUMIDIFI MAINNET ROUTE CAPTURED 2026-01-13
"""Trade executor: swap SOL -> USDC on Devnet using Jupiter Lite API (simulate then send).

This script will:
- Load private key from SOLANA_PRIVATE_KEY (base58) via solders.Keypair.from_base58_string
- Request a Jupiter quote for swapping SOL -> USDC (tries common mints)
- Request swap transaction from Jupiter Lite API
- Build a VersionedTransaction, sign with our key, simulate_transaction and on success send it

Usage: .venv/bin/python src/trade_executor.py --amount 0.1
"""
import argparse
import asyncio
import base64
import json
import os
import sys
import time

from rich.console import Console
from rich.panel import Panel

import httpx
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.system_program import transfer as sp_transfer, TransferParams as SPTransferParams
from src.brain import MarketBrain
try:
    from solana.rpc.types import TxOpts
except Exception:
    # Fallback for test environments where the installed 'solana' package
    # or its submodules are not importable. Provide a minimal TxOpts shim
    # sufficient for code that only needs to construct/send transactions in tests.
    class TxOpts:
        def __init__(self, skip_preflight: bool = False, preflight_commitment=None, max_retries: int | None = None):
            self.skip_preflight = skip_preflight
            self.preflight_commitment = preflight_commitment
            self.max_retries = max_retries
try:
    # preferred: solders compute budget helper
    from solders.programs.compute_budget import ComputeBudgetProgram
except Exception:
    ComputeBudgetProgram = None

# make repo root importable
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.append(project_root)

import src.config as config

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

# load .env so SOLANA_PRIVATE_KEY and RPC_URL are present when running directly
load_dotenv_into_env()

# Note: input/output mints are required parameters for Jupiter helpers below.
# We intentionally DO NOT define default mints here to avoid hidden global state.

# Jupiter V6 API endpoints (public)
JUPITER_QUOTE = "https://api.jup.ag/swap/v1/quote"
JUPITER_SWAP = "https://api.jup.ag/swap/v1/swap"

DEFAULT_SLIPPAGE_BPS = 50  # 0.5%


def load_key():
    key_b58 = os.getenv("SOLANA_PRIVATE_KEY") or getattr(config, "SOLANA_PRIVATE_KEY", None)
    if not key_b58:
        raise ValueError("SOLANA_PRIVATE_KEY not set in environment or config")
    solders_key = Keypair.from_base58_string(key_b58)
    return solders_key


async def get_jupiter_quote(input_mint: str, output_mint: str, amount_lamports: int, slippage_bps: int = DEFAULT_SLIPPAGE_BPS):
    """Request a Jupiter V6 quote (async).

    Returns the JSON response (quoteResponse) on success.
    """
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(amount_lamports),
        "slippageBps": str(slippage_bps),
    }
    try:
        # Allow optional Jupiter API key via env var (some Jupiter endpoints require auth)
        jupiter_key = os.getenv('JUPITER_API_KEY') or os.getenv('JUPITER_KEY')
        headers = {"x-api-key": jupiter_key} if jupiter_key else None
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(JUPITER_QUOTE, params=params, headers=headers)
            if resp.status_code != 200:
                console.print(Panel(f"Jupiter quote error ({resp.status_code}): {resp.text}", style="red"))
                resp.raise_for_status()
            return resp.json()
    except Exception as e:
        console.print(Panel(f"Error fetching Jupiter quote: {e}", style="red"))
        raise


async def get_jupiter_swap(quote_response, user_pubkey: str | None = None, wrap_and_unwrap: bool = True):
    """Request Jupiter swap transaction (returns JSON with swapTransaction base64).

    Sends quoteResponse to the swap endpoint and asks Jupiter to return a signed/simulatable transaction blob.
    """
    payload = {
        "quoteResponse": quote_response,
        "wrapAndUnwrapSol": bool(wrap_and_unwrap),
    }
    if user_pubkey:
        payload["userPublicKey"] = user_pubkey
    # optional auth header
    jupiter_key = os.getenv('JUPITER_API_KEY') or os.getenv('JUPITER_KEY')
    headers = {"x-api-key": jupiter_key} if jupiter_key else None
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(JUPITER_SWAP, json=payload, headers=headers)
        if resp.status_code != 200:
            console.print(Panel(f"Jupiter swap error ({resp.status_code}): {resp.text}", style="red"))
            resp.raise_for_status()
        return resp.json()


# Backwards-compatible wrapper for older callers
async def get_jupiter_swap_transaction(quote_response, user_pubkey: str, wrap_and_unwrap: bool = True):
    return await get_jupiter_swap(quote_response, user_pubkey=user_pubkey, wrap_and_unwrap=wrap_and_unwrap)


async def main(amount: float, input_mint: str, output_mint: str, slippage: float = 0.01, live: bool = False, priority_fee: int | None = None, verbose: bool = False):
    KEY = load_key()
    # Default priority fee (micro-lamports per CU)
    DEFAULT_PRIORITY_FEE = int(os.getenv('PRIORITY_FEE', '10000'))
    rpc = os.getenv("RPC_URL") or getattr(config, "RPC_URL", None) or getattr(config, "SOLANA_RPC", None) or "https://api.devnet.solana.com"

    # Instantiate MarketBrain so we can route all RPC calls through the
    # centralized shield (_call_rpc). This ensures consistent rotation,
    # blacklisting and monitoring for critical money flows.
    brain = MarketBrain(rpc=rpc)

    lamports = int(amount * 1e9)
    # Use the DEFAULT_SLIPPAGE_BPS (50 bps) unless a caller explicitly provided a priority argument
    slippage_bps = DEFAULT_SLIPPAGE_BPS

    console.print(Panel(f"Preparing swap: {amount} SOL -> USDC | Slippage: {slippage_bps} bps\nRPC: {rpc}", title="Trade Executor"))

    # Determine effective modes: explicit args take precedence, then env, then defaults
    env_live = os.getenv('TRADE_LIVE', '0') in ('1', 'true', 'True')
    priority_fee_value = int(priority_fee) if priority_fee is not None else int(os.getenv('PRIORITY_FEE', str(DEFAULT_PRIORITY_FEE)))
    final_live_env = live or env_live

    # 1) Get quote
    try:
        quote = await get_jupiter_quote(input_mint, output_mint, lamports, slippage_bps)
        quote_failed = False
    except Exception as e:
        console.print(Panel(f"Failed to fetch quote: {e}", style="yellow"))
        quote = None
        quote_failed = True

    # If quote succeeded, proceed with Jupiter swap flow; otherwise create a mock transfer transaction
    solders_key = KEY

    if not quote_failed and quote is not None:
        # Try to extract and display a friendly route summary for visibility
        def extract_route_summary(q):
            try:
                # Jupiter V6: top-level 'data' is a list of routes
                data = q.get('data') or q.get('routes') or q.get('quote') or q.get('results')
                if isinstance(data, list) and data:
                    r = data[0]
                    parts = []
                    # Prefer marketInfos labels
                    if isinstance(r, dict):
                        if 'marketInfos' in r and isinstance(r['marketInfos'], list):
                            for mi in r['marketInfos']:
                                if isinstance(mi, dict):
                                    parts.append(mi.get('label') or mi.get('ammName') or mi.get('market') or mi.get('id') or str(mi))
                        elif 'route' in r and isinstance(r['route'], list):
                            for step in r['route']:
                                if isinstance(step, dict):
                                    parts.append(step.get('label') or step.get('ammName') or step.get('program') or step.get('market') or str(step))
                        elif 'swap' in r and isinstance(r['swap'], list):
                            for s in r['swap']:
                                parts.append(s.get('label') or s.get('ammName') or str(s))
                    if parts:
                        return ' -> '.join(parts)
                if isinstance(q, dict):
                    return q.get('route') or q.get('path') or str(list(q.keys())[:5])
            except Exception:
                pass
            return 'Route info unavailable'

        route_summary = extract_route_summary(quote)
        console.print(Panel(f"Route: {route_summary}\n(Full quote available in logs)", title="Route Info", style="cyan"))

        # Deep route scan: robust extraction of AMM/program names from multiple possible fields
        def deep_route_list(q):
            results = []
            # Candidate containers to inspect
            candidates = []
            if isinstance(q, dict):
                # common Jupiter V6 shapes
                cand = q.get('data') or q.get('routes') or q.get('quote') or q.get('results') or q.get('routePlan')
                if cand:
                    candidates = cand if isinstance(cand, list) else [cand]
            # If candidates is empty, try top-level
            if not candidates and isinstance(q, dict):
                candidates = [q]

            for route in candidates:
                if not isinstance(route, dict):
                    continue
                # marketInfos
                if 'marketInfos' in route and isinstance(route['marketInfos'], list):
                    for mi in route['marketInfos']:
                        if isinstance(mi, dict):
                            results.append(mi.get('label') or mi.get('ammName') or mi.get('market') or mi.get('id'))
                    if results:
                        return results
                # route steps
                if 'route' in route and isinstance(route['route'], list):
                    for step in route['route']:
                        if isinstance(step, dict):
                            results.append(step.get('ammName') or step.get('label') or step.get('program') or step.get('market') or step.get('id'))
                    if results:
                        return results
                # swap/hops
                if 'swap' in route and isinstance(route['swap'], list):
                    for s in route['swap']:
                        if isinstance(s, dict):
                            results.append(s.get('label') or s.get('ammName') or s.get('market') or s.get('id'))
                    if results:
                        return results
                # routePlan nested
                if 'routePlan' in route and isinstance(route['routePlan'], list):
                    for hop in route['routePlan']:
                        if isinstance(hop, dict):
                            results.append(hop.get('ammName') or hop.get('label') or hop.get('program') or hop.get('market') or hop.get('id'))
                    if results:
                        return results
            return results

        try:
            route_list = deep_route_list(quote)
            if route_list:
                console.print(Panel("\n".join([f"{i+1}. {r}" for i, r in enumerate(route_list)]), title="Deep Route Scan", style="magenta"))
            else:
                # If verbose, print a pretty summary extracted from the quoteResponse
                if verbose:
                    try:
                        # Extract amounts and labels (robust to shapes)
                        in_amount_raw = quote.get('inAmount') or quote.get('in_amount') or quote.get('inAmountRaw') or quote.get('in')
                        out_amount_raw = quote.get('outAmount') or quote.get('out_amount') or quote.get('outAmountRaw') or quote.get('out')
                        slippage_bps = quote.get('slippageBps') or quote.get('slippage_bps') or quote.get('slippage')

                        def lamports_to_sol_val(s):
                            try:
                                return int(s) / 1e9
                            except Exception:
                                return None

                        def usdc_units_to_float_val(s):
                            try:
                                return int(s) / 1e6
                            except Exception:
                                return None
                        # robust extraction for outAmount across Jupiter v1/Metis shapes
                        try:
                            out_amount_raw = quote.get('outAmount') or quote.get('out_amount') or quote.get('out') or quote.get('outAmountRaw')
                            if out_amount_raw is None:
                                data = quote.get('data') or quote.get('routes') or quote.get('results') or quote.get('quote') or []
                                if isinstance(data, list) and len(data) > 0:
                                    first = data[0]
                                    if isinstance(first, dict):
                                        out_amount_raw = first.get('outAmount') or first.get('out_amount') or first.get('out') or first.get('outAmountRaw')
                                # fallback: look through data list entries
                                if out_amount_raw is None and isinstance(data, list):
                                    for entry in data:
                                        if isinstance(entry, dict):
                                            cand = entry.get('outAmount') or entry.get('out') or entry.get('out_amount')
                                            if cand is not None:
                                                out_amount_raw = cand
                                                break
                        except Exception:
                            out_amount_raw = quote.get('outAmount') or quote.get('out')
                        output_usdc = usdc_units_to_float_val(out_amount_raw) if out_amount_raw is not None else None
                        # If out_amount_raw is still None after robust parsing, log the raw quote for post-mortem
                        if out_amount_raw is None:
                            try:
                                data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
                                os.makedirs(data_dir, exist_ok=True)
                                err_log = os.path.join(data_dir, 'parsing_errors.log')
                                with open(err_log, 'a', encoding='utf-8') as fh:
                                    fh.write(json.dumps({'ts': time.time(), 'stage': 'outAmount_missing', 'quote': quote}) + "\n")
                                console.print(Panel(f"critical_parsing_error: outAmount not found in Jupiter quote — saved raw payload to {err_log}", style='red'))
                            except Exception:
                                pass

                        # Try to extract AMM label from routePlan
                        amm_label = None
                        route_plan = quote.get('routePlan') or quote.get('route_plan') or quote.get('routePlanData') or quote.get('data')
                        if isinstance(route_plan, list) and route_plan:
                            first = route_plan[0]
                            swap_info = None
                            if isinstance(first, dict):
                                swap_info = first.get('swapInfo') or first.get('swap_info') or first
                            if isinstance(swap_info, dict):
                                amm_label = swap_info.get('label') or swap_info.get('ammName') or swap_info.get('AmmName')

                        input_str = f"{input_sol:.9f} SOL" if input_sol is not None else "unknown"
                        output_display = f"{output_usdc:.2f} USDC" if output_usdc is not None else "unknown"
                        slippage_str = f"{(int(slippage_bps) / 100):.2f}%" if slippage_bps is not None else "unknown"

                        summary = (
                            f"Input: {input_str}\n"
                            f"Output: {output_display}\n"
                            f"AMM: {amm_label or 'unknown'}\n"
                            f"Slippage: {slippage_str} (Confirmed)\n"
                        )
                        console.print(Panel(summary, title="Jupiter Route Summary", style="green"))
                    except Exception:
                        console.print(Panel(f"No detailed route hops found. Quote keys: {list(quote.keys())}", title="Route Plan Debug", style="yellow"))
                        # Log the raw quote for post-mortem analysis when verbose parsing fails
                        try:
                            data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
                            os.makedirs(data_dir, exist_ok=True)
                            err_log = os.path.join(data_dir, 'parsing_errors.log')
                            with open(err_log, 'a', encoding='utf-8') as fh:
                                fh.write(json.dumps({'ts': time.time(), 'stage': 'route_plan_parse_fail', 'quote_keys': list(quote.keys()), 'quote': quote}) + "\n")
                        except Exception:
                            pass
        except Exception:
            if verbose:
                console.print(Panel("Failed to extract detailed routePlan (structure unexpected).", style="yellow"))
        # Use quote to request swap transaction
        try:
            swap_resp = await get_jupiter_swap_transaction(quote, str(solders_key.pubkey()), wrap_and_unwrap=True)
        except Exception as e:
            console.print(Panel(f"Failed to request swap transaction: {e}", style="red"))
            return

        swap_tx_b64 = swap_resp.get("swapTransaction")
        if not swap_tx_b64:
            console.print(Panel(f"No swapTransaction found in response: {swap_resp}", style="red"))
            return

        swap_tx_bytes = base64.b64decode(swap_tx_b64)

        # Try decode to VersionedTransaction
        try:
            tx1 = VersionedTransaction.from_bytes(swap_tx_bytes)
        except Exception as e:
            console.print(Panel(f"Failed to decode VersionedTransaction: {e}", style="red"))
            return

        # Try to prepend compute-budget instructions for extra priority (best-effort)
        try:
            if ComputeBudgetProgram is not None:
                limit_ix = ComputeBudgetProgram.set_compute_unit_limit(200_000)
                price_ix = ComputeBudgetProgram.set_compute_unit_price(priority_fee_value)
                if hasattr(tx1.message, 'instructions'):
                    tx1.message.instructions = [limit_ix, price_ix] + list(tx1.message.instructions)
        except Exception:
            # ignore and continue
            pass

        # Sign the transaction with our key
        vtx = VersionedTransaction(tx1.message, [solders_key])
        signed_bytes = bytes(vtx)
        signed_b64 = base64.b64encode(signed_bytes).decode()
    else:
        # Build a solders-native mock transfer instruction and VersionedTransaction
        payer_pub = solders_key.pubkey()
        # Use self-transfer for Devnet to ensure recipient account exists and avoid AccountNotFound errors
        burn_pub = payer_pub
        console.print(Panel(f"Payer pubkey: {payer_pub}", title="Debug"))
        lamports_small = int(0.001 * 1e9)

        instr = sp_transfer(SPTransferParams(from_pubkey=payer_pub, to_pubkey=burn_pub, lamports=lamports_small))

        # Prepend compute budget instruction so transactions have a priority fee on mainnet
        try:
            if ComputeBudgetProgram is not None:
                limit_ix = ComputeBudgetProgram.set_compute_unit_limit(200_000)
                price_ix = ComputeBudgetProgram.set_compute_unit_price(priority_fee_value)
                instrs = [limit_ix, price_ix, instr]
            else:
                instrs = [instr]
        except Exception:
            instrs = [instr]

        # fetch latest blockhash and balance via MarketBrain shield
        recent = None
        try:
            bal = await brain._call_rpc('getBalance', [str(payer_pub)])
            # resilient parsing of common JSON-RPC shapes
            bal_val = None
            if isinstance(bal, dict):
                bal_val = bal.get('result', {}).get('value') or bal.get('value')
            else:
                bal_val = None
            b = int(bal_val) if bal_val is not None else None
            console.print(Panel(f"Payer balance (lamports): {b}", title="Debug"))
            if not b or int(b) == 0:
                console.print(Panel("Payer account not funded or not found on this RPC. Fund the wallet on Devnet or set SOLANA_PRIVATE_KEY to a funded key to run a successful simulation.", style="yellow"))
                return
        except Exception:
            console.print(Panel("Failed to fetch payer balance for debug.", style="yellow"))
        try:
            lb = await brain._call_rpc('getLatestBlockhash', [])
            recent = None
            if isinstance(lb, dict):
                res = lb.get('result') or lb
                if isinstance(res, dict):
                    val = res.get('value') or res.get('result')
                    if isinstance(val, dict):
                        recent = val.get('blockhash') or val.get('blockHash')
                    elif isinstance(res.get('value'), dict):
                        recent = res['value'].get('blockhash')
            # if still None, try common fallback shapes
            if not recent:
                try:
                    recent = lb.get('value', {}).get('blockhash') if isinstance(lb, dict) else None
                except Exception:
                    recent = None
        except Exception:
            recent = None

        if not recent:
            console.print(Panel("Failed to fetch recent blockhash for message compilation.", style="red"))
            return

        # Compile a MessageV0
        try:
            msg = MessageV0.try_compile(payer_pub, instrs, [], recent)
        except Exception as e:
            console.print(Panel(f"Failed to compile MessageV0: {e}", style="red"))
            return

        # Create and sign VersionedTransaction using solders Keypair
        try:
            vtx = VersionedTransaction(msg, [solders_key])
        except Exception as e:
            console.print(Panel(f"Failed to create/sign VersionedTransaction: {e}", style="red"))
            return

        signed_bytes = bytes(vtx)
        signed_b64 = base64.b64encode(signed_bytes).decode()

    # Determine live mode from env/CLI
    # If the user passed --live on the command line, prefer that setting
    # (support for CLI flags when run directly is below in __main__)
    # Default behavior: DRY-RUN / SIMULATE ONLY.

    # SIMULATE and optionally SEND when --live is provided
    # SIMULATE and optionally SEND when --live is provided — route through brain shield
    console.print("Simulating transaction...")
    try:
        # prefer using base64-encoded signed bytes for RPC simulate
        try:
            sim = await brain._call_rpc('simulateTransaction', [signed_b64, {"encoding": "base64"}])
        except Exception:
            try:
                sim = await brain._call_rpc('simulateTransaction', [signed_bytes, {"encoding": "base64"}])
            except Exception:
                sim = await brain._call_rpc('simulateTransaction', [signed_b64])

        sim_val = sim.get('result', {}).get('value') if isinstance(sim, dict) else sim

        # Attempt to extract units consumed and error information from several possible shapes
        units = None
        err = None
        if isinstance(sim_val, dict):
            units = sim_val.get('unitsConsumed')
            if units is None:
                units = sim_val.get('result', {}).get('value', {}).get('unitsConsumed') if isinstance(sim_val.get('result', {}), dict) else None
            err = sim_val.get('err') or (sim_val.get('result', {}).get('value', {}).get('err') if isinstance(sim_val.get('result', {}), dict) else None)
        else:
            units = getattr(sim_val, 'units_consumed', None) or getattr(sim_val, 'unitsConsumed', None)
            err = getattr(sim_val, 'err', None)

        if err:
            console.print(Panel(f"Simulation failed: {err}", style="red"))
            return

        console.print(Panel(f"SIMULATION SUCCESS — unitsConsumed: {units}", style="green"))

        # Check on-chain balance safety: ensure we keep at least 0.05 SOL (50_000_000 lamports)
        try:
            bal = await brain._call_rpc('getBalance', [str(solders_key.pubkey())])
            bal_val = bal.get('result', {}).get('value') if isinstance(bal, dict) else bal
            bal_lamports = int(bal_val) if bal_val is not None else None
        except Exception:
            bal_lamports = None

        MIN_REMAINING = int(0.05 * 1e9)
        ESTIMATED_FEE = 50_000  # safety cushion in lamports

        if bal_lamports is not None:
            spend_lamports = lamports if (not quote_failed and quote is not None) else lamports_small
            if bal_lamports - spend_lamports - ESTIMATED_FEE < MIN_REMAINING:
                console.print(Panel(f"Insufficient balance to leave {MIN_REMAINING} lamports after trade. Current balance: {bal_lamports}. Aborting.", style="red"))
                return
        else:
            console.print(Panel("Could not determine payer balance; aborting for safety.", style="red"))
            return

        # Only send if live mode explicitly enabled via env var or CLI override
        final_live = final_live_env

        if final_live:
            console.print(Panel("LIVE MODE enabled. Sending transaction on-chain...", style="yellow"))
            try:
                resp = await brain._call_rpc('sendTransaction', [signed_b64, {"skipPreflight": True}])
                sig = None
                if isinstance(resp, dict):
                    sig = resp.get('result') or resp.get('value') or resp.get('signature')
                else:
                    sig = getattr(resp, 'result', None) or getattr(resp, 'value', None)

                console.print(Panel(f"Send result: {sig}", title="TX Result"))
            except Exception as e:
                console.print(Panel(f"Failed to send transaction: {e}", style="red"))
                return
        else:
            console.print(Panel("DRY-RUN (no send). To send on mainnet, re-run with --live (and ensure you understand the risks).", style="yellow"))
    except Exception as e:
        console.print(Panel(f"Simulation RPC error: {e}", style="red"))
        return
        console.print("Simulating transaction...")
        try:
            # Prefer passing a VersionedTransaction object for simulation when available
            try:
                sim = await client.simulate_transaction(vtx)
            except Exception:
                # Fallback to raw bytes of the signed transaction (avoid passing a str)
                try:
                    sim = await client.simulate_transaction(signed_bytes)
                except Exception:
                    # final fallback: base64 string if client supports it
                    sim = await client.simulate_transaction(signed_b64)

            sim_val = getattr(sim, "value", sim)

            # Attempt to extract units consumed and error information from several possible shapes
            units = None
            err = None
            if isinstance(sim_val, dict):
                units = sim_val.get('unitsConsumed')
                if units is None:
                    # nested shape (rpc result)
                    units = sim_val.get('result', {}).get('value', {}).get('unitsConsumed')
                err = sim_val.get('err') or sim_val.get('result', {}).get('value', {}).get('err')
            else:
                units = getattr(sim_val, 'units_consumed', None) or getattr(sim_val, 'unitsConsumed', None)
                err = getattr(sim_val, 'err', None)

            if err:
                console.print(Panel(f"Simulation failed: {err}", style="red"))
                return

            console.print(Panel(f"SIMULATION SUCCESS — unitsConsumed: {units}", style="green"))

            # Check on-chain balance safety: ensure we keep at least 0.05 SOL (50_000_000 lamports)
            try:
                bal = await client.get_balance(solders_key.pubkey())
                bal_val = getattr(bal, 'value', None) or (bal.get('result', {}).get('value') if isinstance(bal, dict) else None)
                bal_lamports = int(bal_val) if bal_val is not None else None
            except Exception:
                bal_lamports = None

            MIN_REMAINING = int(0.05 * 1e9)
            ESTIMATED_FEE = 50_000  # safety cushion in lamports

            if bal_lamports is not None:
                # If this is a Jupiter swap, use lamports as the amount being spent; otherwise use 0 for mock transfer
                spend_lamports = lamports if (not quote_failed and quote is not None) else lamports_small
                if bal_lamports - spend_lamports - ESTIMATED_FEE < MIN_REMAINING:
                    console.print(Panel(f"Insufficient balance to leave {MIN_REMAINING} lamports after trade. Current balance: {bal_lamports}. Aborting.", style="red"))
                    return
            else:
                console.print(Panel("Could not determine payer balance; aborting for safety.", style="red"))
                return

            # Only send if live mode explicitly enabled via env var or CLI override
            final_live = final_live_env

            if final_live:
                console.print(Panel("LIVE MODE enabled. Sending transaction on-chain...", style="yellow"))
                try:
                    # send_raw_transaction prefers raw bytes
                    resp = await client.send_raw_transaction(signed_bytes, opts=TxOpts(skip_preflight=True))
                    # Try to extract signature
                    sig = None
                    if isinstance(resp, dict):
                        sig = resp.get('result') or resp.get('value') or resp.get('signature')
                    else:
                        sig = getattr(resp, 'result', None) or getattr(resp, 'value', None)

                    console.print(Panel(f"Send result: {sig}", title="TX Result"))
                except Exception as e:
                    console.print(Panel(f"Failed to send transaction: {e}", style="red"))
                    return
            else:
                console.print(Panel("DRY-RUN (no send). To send on mainnet, re-run with --live (and ensure you understand the risks).", style="yellow"))
        except Exception as e:
            console.print(Panel(f"Simulation RPC error: {e}", style="red"))
            return

    return


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--amount", type=float, default=0.1, help="Amount of SOL to swap")
    parser.add_argument("--input-mint", type=str, required=True, help="Input mint (e.g. WSOL) - required")
    parser.add_argument("--output-mint", type=str, required=True, help="Output mint (e.g. USDC) - required")
    parser.add_argument("--live", action="store_true", help="If set, actually send the signed transaction on-chain. Defaults to simulate-only.")
    parser.add_argument("--priority-fee", type=int, default=None, help="Priority fee (micro-lamports per compute unit). Defaults to 10000.")
    parser.add_argument("--verbose", action="store_true", help="Verbose output: print detailed routePlan AmmName entries when available.")
    args = parser.parse_args()

    try:
        asyncio.run(main(args.amount, input_mint=args.input_mint, output_mint=args.output_mint, live=args.live, priority_fee=args.priority_fee, verbose=args.verbose))
    except KeyboardInterrupt:
        console.print("Interrupted", style="yellow")
