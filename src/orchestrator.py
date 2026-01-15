#!/usr/bin/env python3
"""Orchestrator: polls MarketBrain and routes alpha signals to TradeExecutor for dry-run simulations.

Usage: .venv/bin/python src/orchestrator.py
"""
from __future__ import annotations

import asyncio
import csv
import os
import time
from datetime import datetime, timedelta

from rich.console import Console
from rich.panel import Panel

import src.trade_executor as te
from src.brain import MarketBrain

from solana.rpc.async_api import AsyncClient

console = Console()

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
ALPHA_CSV = os.path.join(DATA_DIR, 'alpha_journal.csv')

# ensure data dir exists
os.makedirs(DATA_DIR, exist_ok=True)


async def check_balance_ok(rpc: str, min_remaining_lamports: int = int(0.05 * 1e9), spend_lamports: int = 0):
    """Check wallet balance and ensure min_remaining after spend."""
    key = te.load_key()
    async with AsyncClient(rpc) as client:
        try:
            bal = await client.get_balance(key.pubkey())
            bal_val = getattr(bal, 'value', None) or (bal.get('result', {}).get('value') if isinstance(bal, dict) else None)
            bal_lamports = int(bal_val) if bal_val is not None else None
        except Exception:
            bal_lamports = None

    if bal_lamports is None:
        console.print(Panel('Could not determine wallet balance; aborting simulation for safety.', style='red'))
        return False, None

    ESTIMATED_FEE = 50_000
    if bal_lamports - spend_lamports - ESTIMATED_FEE < min_remaining_lamports:
        console.print(Panel(f"Balance {bal_lamports} lamports would drop below safety floor after spend {spend_lamports}. Required min remaining: {min_remaining_lamports}", style='red'))
        return False, bal_lamports

    return True, bal_lamports


async def simulate_swap_and_log(rpc: str, spike: dict, amount_sol: float = 0.1, input_amount_sol: float | None = None):
    """Use trade_executor helpers to get a Jupiter quote, request a swap tx, simulate it, and log outcome."""
    # spike contains 'mint','name','volume_pct'
    mint = spike.get('mint')
    name = spike.get('name') or mint
    pct = spike.get('volume_pct')

    # request quote explicitly for the target mint
    try:
        # use explicit input amount when provided, else fall back to amount_sol
        if input_amount_sol is None:
            input_amount_sol = amount_sol
        lamports = int(input_amount_sol * 1e9)
        # check balance safety first using expected spend lamports
        ok, bal = await check_balance_ok(rpc, spend_lamports=lamports)
        if not ok:
            return False

    # Input mint is WSOL for SOL trades
    WSOL_MINT_LITERAL = "So11111111111111111111111111111111111111112"
    quote = await te.get_jupiter_quote(WSOL_MINT_LITERAL, mint, lamports, te.DEFAULT_SLIPPAGE_BPS)
        if not quote:
            console.print(Panel('No quote received from Jupiter; skipping.', style='yellow'))
            return False

        # extract expected out amount from quote (robust to shapes)
        out_amount_raw = quote.get('outAmount') or quote.get('out_amount') or quote.get('out') or quote.get('outAmountRaw')
        expected_out_raw = None
        try:
            if out_amount_raw is not None:
                expected_out_raw = int(out_amount_raw)
        except Exception:
            expected_out_raw = None

        # Convert expected_out to SOL when possible. Prefer precise conversion using
        # price information from the quote when output mint is not SOL.
        expected_out_sol = None
        try:
            WSOL_MINT_LITERAL = "So11111111111111111111111111111111111111112"
            if expected_out_raw is not None:
                if mint == WSOL_MINT_LITERAL:
                    expected_out_sol = float(expected_out_raw) / 1e9
                else:
                    # try to get a price from the quote (SOL per token)
                    price = None
                    for k in ('price', 'priceUsd', 'price_in', 'tokenPrice'):
                        try:
                            v = quote.get(k)
                            if v is not None:
                                price = float(v)
                                break
                        except Exception:
                            continue

                    if price is not None and price > 0:
                        # heuristically infer token decimals from the raw amount
                        decimals = 0
                        try:
                            if expected_out_raw % 1_000_000_000 == 0:
                                decimals = 9
                            elif expected_out_raw % 1_000_000 == 0:
                                decimals = 6
                            else:
                                # fallback guess
                                decimals = 6
                        except Exception:
                            decimals = 6

                        token_amount = float(expected_out_raw) / (10 ** decimals)
                        # price is assumed to be SOL per token unit
                        expected_out_sol = token_amount * float(price)
                    else:
                        # no price info — leave as unknown (conservative)
                        expected_out_sol = 0.0

        except Exception:
            expected_out_sol = None

        # request swap transaction (simulatable blob)
        swap_resp = await te.get_jupiter_swap(quote, user_pubkey=str(te.load_key().pubkey()), wrap_and_unwrap=True)
        swap_tx_b64 = swap_resp.get('swapTransaction')
        if not swap_tx_b64:
            console.print(Panel(f"No swapTransaction in swap response: {swap_resp}", style='red'))
            return False

        import base64
        from solders.transaction import VersionedTransaction

        swap_tx_bytes = base64.b64decode(swap_tx_b64)
        try:
            tx1 = VersionedTransaction.from_bytes(swap_tx_bytes)
        except Exception as e:
            console.print(Panel(f"Failed to decode VersionedTransaction: {e}", style='red'))
            return False

        # Prepend compute budget if available
        try:
            if te.ComputeBudgetProgram is not None:
                limit_ix = te.ComputeBudgetProgram.set_compute_unit_limit(200_000)
                price_ix = te.ComputeBudgetProgram.set_compute_unit_price(int(os.getenv('PRIORITY_FEE', '10000')))
                if hasattr(tx1.message, 'instructions'):
                    tx1.message.instructions = [limit_ix, price_ix] + list(tx1.message.instructions)
        except Exception:
            pass

        key = te.load_key()
        vtx = VersionedTransaction(tx1.message, [key])

        # simulate
        async with AsyncClient(rpc) as client:
            try:
                sim = await client.simulate_transaction(vtx)
            except Exception:
                # fallback to raw bytes
                try:
                    sim = await client.simulate_transaction(bytes(vtx))
                except Exception as e:
                    console.print(Panel(f"Simulation RPC failed: {e}", style='red'))
                    return False

        sim_val = getattr(sim, 'value', sim)
        units = None
        err = None
        if isinstance(sim_val, dict):
            units = sim_val.get('unitsConsumed') or (sim_val.get('result') or {}).get('value', {}).get('unitsConsumed')
            err = sim_val.get('err') or (sim_val.get('result') or {}).get('value', {}).get('err')
        else:
            units = getattr(sim_val, 'units_consumed', None) or getattr(sim_val, 'unitsConsumed', None)
            err = getattr(sim_val, 'err', None)

        if err:
            console.print(Panel(f"SIMULATION failed: {err}", style='red'))
            success = False
        else:
            console.print(Panel(f"SIMULATION SUCCESS — unitsConsumed: {units}", style='green'))
            success = True

        # Log to CSV: timestamp, mint, name, volume_pct, expected_out_raw, expected_out_sol,
        # input_amount_sol, units, balance, success, alpha_score, whale_multiplier
        ts = datetime.utcnow().isoformat() + 'Z'
        header_needed = not os.path.exists(ALPHA_CSV)
        with open(ALPHA_CSV, 'a', newline='') as fh:
            writer = csv.writer(fh)
            if header_needed:
                writer.writerow([
                    'ts', 'mint', 'name', 'volume_pct', 'expected_out_raw', 'expected_out_sol',
                    'input_amount_sol', 'unitsConsumed', 'balance_lamports', 'success', 'alpha_score', 'whale_multiplier', 'exit_type'
                ])
            alpha_score = spike.get('alpha_score') if spike and isinstance(spike, dict) else None
            whale_mult = spike.get('whale_multiplier') if spike and isinstance(spike, dict) else None
            exit_type = spike.get('exit_type') if spike and isinstance(spike, dict) else None
            writer.writerow([ts, mint, name, pct, expected_out_raw, expected_out_sol, input_amount_sol, units, bal, success, alpha_score, whale_mult, exit_type])

        return success
    finally:
        # no global mutation performed
        pass


async def check_daily_pnl():
    """Check today's PnL and trip a daily circuit breaker if losses exceed threshold.

    Heuristic behavior:
    - Reads `data/alpha_journal.csv` and selects rows with a timestamp dated today (UTC).
    - Uses ORCH_DEFAULT_INPUT_SOL (env) or 0.1 SOL as the assumed input per logged signal when
      an explicit input amount is not present in the CSV.
    - Tries to parse `expected_out_raw` as a numeric SOL amount when plausible (small numbers).
      When `expected_out_raw` looks like a token base-unit (very large), the function is conservative
      and treats expected_out as 0 SOL (worst-case) for the purpose of circuit-breaking.
    - If total_daily_loss (inputs - expected_out) > MAX_DAILY_LOSS (env, default 0.5 SOL),
      it logs a CRITICAL alert and sleeps until UTC midnight to pause the orchestrator for the day.
    """
    max_loss = float(os.getenv('MAX_DAILY_LOSS', '0.5'))
    default_input = float(os.getenv('ORCH_DEFAULT_INPUT_SOL', '0.1'))

    if not os.path.exists(ALPHA_CSV):
        return False

    total_input = 0.0
    total_expected_out = 0.0
    today = datetime.utcnow().date()

    try:
        with open(ALPHA_CSV, 'r', newline='') as fh:
            reader = csv.DictReader(fh)
            rows_today = []
            for r in reader:
                ts = r.get('ts')
                if not ts:
                    continue
                try:
                    # support ISO timestamps with trailing Z
                    dt = datetime.fromisoformat(ts.replace('Z', ''))
                except Exception:
                    # best-effort parse
                    try:
                        dt = datetime.strptime(ts.split('T')[0], '%Y-%m-%d')
                    except Exception:
                        continue

                if dt.date() != today:
                    continue

                rows_today.append(r)

            if not rows_today:
                return False

            # Use explicit logged input_amount_sol and expected_out_sol fields for precise PnL
            total_input = 0.0
            total_expected_out = 0.0
            for r in rows_today:
                # input_amount_sol is expected in CSV when available
                in_raw = r.get('input_amount_sol')
                try:
                    if in_raw is not None and in_raw != '':
                        total_input += float(in_raw)
                    else:
                        total_input += default_input
                except Exception:
                    total_input += default_input

                out_sol_raw = r.get('expected_out_sol')
                try:
                    if out_sol_raw is not None and out_sol_raw != '':
                        total_expected_out += float(out_sol_raw)
                except Exception:
                    # missing or unparsable expected_out_sol -> treat as 0 for conservative PnL
                    continue

            total_daily_loss = max(0.0, total_input - total_expected_out)

            if total_daily_loss > max_loss:
                console.print(Panel(f"[CRITICAL] Daily loss {total_daily_loss:.4f} SOL exceeds limit {max_loss} SOL — circuit breaker engaged for today.", style='red'))
                # sleep until next UTC midnight
                now = datetime.utcnow()
                next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                sleep_seconds = (next_midnight - now).total_seconds()
                console.print(Panel(f"Sleeping for {int(sleep_seconds)} seconds until next UTC day reset.", style='yellow'))
                await asyncio.sleep(sleep_seconds)
                return True

    except Exception as e:
        console.print(Panel(f"Error computing daily pnl: {e}", style='yellow'))

    return False


async def main_loop(poll_interval: int = 60, rpc: str | None = None):
    rpc = rpc or os.getenv('RPC_URL') or 'https://api.mainnet-beta.solana.com'
    brain = MarketBrain(rpc=rpc)
    console.print(Panel(f"Orchestrator starting. Poll interval: {poll_interval}s | RPC: {rpc}", style='green'))

    while True:
        try:
            # daily circuit-breaker check: may sleep the loop for the remainder of the day
            try:
                cb = await check_daily_pnl()
                if cb:
                    # after sleeping until next day, continue to next cycle
                    continue
            except Exception:
                # non-fatal: if the breaker check fails, proceed normally
                pass

            signal = await brain.get_alpha_signal()
            if signal:
                console.print(Panel(f"Alpha signal found: {signal}", style='magenta'))
                # Signal conviction filter: skip low-value whale trades
                try:
                    threshold = float(os.getenv('MIN_WHALE_TRADE_THRESHOLD', '1.0'))
                except Exception:
                    threshold = 1.0

                # Determine the trade value in SOL: prefer explicit field on the signal
                default_input = float(os.getenv('ORCH_DEFAULT_INPUT_SOL', '0.1'))
                trade_value = None
                try:
                    trade_value = float(signal.get('estimated_trade_sol')) if signal.get('estimated_trade_sol') is not None else None
                except Exception:
                    trade_value = None

                if trade_value is None:
                    trade_value = default_input

                if trade_value < threshold:
                    console.print(Panel(f"[INFO] Whale trade below threshold ({trade_value} SOL). Skipping analysis.", style='cyan'))
                    # skip this low conviction signal
                else:
                    # run the safety guard and simulate using the explicit input amount
                    await simulate_swap_and_log(rpc, signal, amount_sol=trade_value, input_amount_sol=trade_value)
            else:
                console.print(Panel('No alpha signal this cycle.', style='yellow'))
        except Exception as e:
            console.print(Panel(f"Orchestrator error: {e}", style='red'))

        await asyncio.sleep(poll_interval)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--rpc', type=str, default=None)
    parser.add_argument('--poll', type=int, default=60)
    args = parser.parse_args()

    try:
        asyncio.run(main_loop(poll_interval=args.poll, rpc=args.rpc))
    except KeyboardInterrupt:
        console.print('Orchestrator stopped', style='yellow')
