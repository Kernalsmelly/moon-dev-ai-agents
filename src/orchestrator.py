#!/usr/bin/env python3
"""Orchestrator: polls MarketBrain and routes alpha signals to TradeExecutor for dry-run simulations.

Usage: .venv/bin/python src/orchestrator.py
"""
from __future__ import annotations

import asyncio
import csv
import os
import time
from datetime import datetime, timedelta, timezone

from rich.console import Console
from rich.panel import Panel
import json
from pathlib import Path

import src.trade_executor as te
from src.brain import MarketBrain
from src.strategies.volume_heat import VolumeHeatIndex
from src.strategies.graduation_sniper import GraduationSniper

from solana.rpc.async_api import AsyncClient

console = Console()

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
ALPHA_CSV = os.path.join(DATA_DIR, 'alpha_journal.csv')

# ensure data dir exists
os.makedirs(DATA_DIR, exist_ok=True)


async def check_balance_ok(rpc: str, min_remaining_lamports: int = int(0.05 * 1e9), spend_lamports: int = 0):
    """Check wallet balance and ensure min_remaining after spend."""
    key = te.load_key()
    brain = MarketBrain(rpc=rpc)
    try:
        bal = await brain._call_rpc('getBalance', [str(key.pubkey())])
        bal_val = None
        if isinstance(bal, dict):
            bal_val = bal.get('result', {}).get('value') or bal.get('value')
        else:
            bal_val = bal
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
    except Exception as e:
        console.print(Panel(f'Failed to prepare quote/safety checks: {e}', style='red'))
        return False

    # Input mint is WSOL for SOL trades
    WSOL_MINT_LITERAL = "So11111111111111111111111111111111111111112"
    # instantiate a MarketBrain to compute dynamic slippage and optionally
    # route RPC calls through its RPC rotating wrapper
    brain = MarketBrain(rpc=rpc)
    try:
        sl_bps = await brain.get_dynamic_slippage(int(te.DEFAULT_SLIPPAGE_BPS))
    except Exception:
        sl_bps = int(te.DEFAULT_SLIPPAGE_BPS)

    quote = await te.get_jupiter_quote(WSOL_MINT_LITERAL, mint, lamports, int(sl_bps))
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

    # simulate via MarketBrain shield
    brain = MarketBrain(rpc=rpc)
    try:
        import base64
        b64 = base64.b64encode(bytes(vtx)).decode()
        # Prefer using MarketBrain._call_rpc when available (tests sometimes
        # inject simple FakeBrain instances that lack _call_rpc). If absent,
        # fall back to the orchestrator AsyncClient (tests patch this object).
        if hasattr(brain, '_call_rpc') and callable(getattr(brain, '_call_rpc')):
            try:
                sim = await brain._call_rpc('simulateTransaction', [b64, {"encoding": "base64"}])
            except Exception:
                sim = await brain._call_rpc('simulateTransaction', [bytes(vtx)])
        else:
            # use local AsyncClient (test harness may patch orch.AsyncClient)
            async with AsyncClient(rpc) as client:
                try:
                    sim = await client.simulate_transaction(bytes(vtx))
                except Exception:
                    try:
                        sim = await client.simulate_transaction(b64)
                    except Exception as e:
                        console.print(Panel(f"Simulation RPC failed: {e}", style='red'))
                        return False
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
    ts = datetime.now(timezone.utc).isoformat()
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
    today = datetime.now(timezone.utc).date()

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
                now = datetime.now(timezone.utc)
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
    # send immediate status report on startup (best-effort)
    try:
        await brain.send_immediate_status()
    except Exception:
        pass
    # instantiate strategies
    vhi = VolumeHeatIndex()
    sniper = GraduationSniper()
    console.print(Panel(f"Orchestrator starting. Poll interval: {poll_interval}s | RPC: {rpc}", style='green'))
    # start heartbeat background task (4-hour summary to Discord)
    async def _heartbeat_loop(brain: MarketBrain, interval_s: int = 4 * 3600):
        path = getattr(brain, 'TRADES_JSONL_PATH', None)
        # fallback to config path used by MarketBrain.append_trade_log
        if not path:
            base = os.path.dirname(os.path.dirname(__file__))
            path = os.path.join(base, 'data', 'trades.jsonl')

        while True:
            try:
                # Prefer runtime-provided async stats helpers when available
                net_pnl = 0.0
                win_rate = 0.0
                jito_rate = 0.0
                latest_vhi = None

                try:
                    stats = await brain.get_session_stats(None)
                except Exception:
                    stats = None

                try:
                    pnl_stats = await brain.get_simulated_pnl()
                except Exception:
                    pnl_stats = None

                if isinstance(pnl_stats, dict) and 'net_sol' in pnl_stats:
                    net_pnl = float(pnl_stats.get('net_sol') or 0.0)

                if isinstance(stats, dict) and 'win_rate' in stats:
                    win_rate = float(stats.get('win_rate') or 0.0) * 100.0 if 0.0 <= float(stats.get('win_rate') or 0.0) <= 1.0 else float(stats.get('win_rate') or 0.0)

                # fallback: try quick file-based scan for jito & vhi if needed
                p = Path(path)
                if p.exists():
                    try:
                        with open(p, 'r', encoding='utf-8') as fh:
                            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
                        # compute jito rate and latest vhi from tail
                        jito_attempts = 0
                        jito_success = 0
                        for ln in lines[-200:]:
                            try:
                                obj = json.loads(ln)
                            except Exception:
                                continue
                            if latest_vhi is None and ('vhi' in obj or 'vhi_display' in obj):
                                latest_vhi = obj.get('vhi') or obj.get('vhi_display')
                            if obj.get('bundle_id') or obj.get('jito_bundle_id'):
                                jito_attempts += 1
                                st = obj.get('bundle_status') or obj.get('jito_status') or (obj.get('bundle_result') or {}).get('status') if isinstance(obj.get('bundle_result'), dict) else None
                                if st in ('ok', 'success', 'submitted') or obj.get('bundle_success') is True:
                                    jito_success += 1
                        if jito_attempts:
                            jito_rate = (jito_success / jito_attempts) * 100.0
                    except Exception:
                        pass

                # best rpc info
                best_rpc = None
                best_latency = None
                try:
                    if getattr(brain, 'rpc_manager', None) is not None:
                        best_rpc = brain.rpc_manager.get_best_rpc()
                        best_latency = brain.rpc_manager.latencies.get(best_rpc)
                except Exception:
                    best_rpc = brain.rpc

                # format alert
                pnl_disp = f"{net_pnl:+.3f} SOL"
                win_disp = f"{win_rate:.0f}%"
                jito_disp = f"{jito_rate:.0f}%"
                rpc_disp = f"{best_rpc or 'unknown'} ({int(best_latency)}ms)" if best_latency is not None else (best_rpc or 'unknown')

                body = (
                    f"📊 4-Hour Summary:\n\n"
                    f"💰 PnL: {pnl_disp}\n\n"
                    f"🎯 Win Rate: {win_disp}\n\n"
                    f"🛡️ Jito Efficiency: {jito_disp}\n\n"
                    f"🌐 Best RPC: {rpc_disp}\n"
                )

                try:
                    await brain._send_discord_alert(body, success=True)
                except Exception:
                    # non-fatal: swallow and continue
                    pass

            except asyncio.CancelledError:
                return
            except Exception:
                # guard: don't crash the whole orchestrator loop
                pass

            await asyncio.sleep(interval_s)

    # spawn but keep running within main loop lifetime
    hb_task = asyncio.create_task(_heartbeat_loop(brain, 4 * 3600))
    # hourly Comparison Summary for Paper Trade Battle Mode
    async def _paper_comparison_loop(brain: MarketBrain, interval_s: int = 3600):
        path = getattr(brain, 'TRADES_JSONL_PATH', None)
        if not path:
            base = os.path.dirname(os.path.dirname(__file__))
            path = os.path.join(base, 'data', 'trades.jsonl')

        while True:
            try:
                total_pnl = 0.0
                jito_attempts = 0
                jito_success = 0
                avg_latency = None

                # compute total paper PnL today from simulated trades
                p = Path(path)
                if p.exists():
                    try:
                        today = datetime.now(timezone.utc).date()
                        with open(p, 'r', encoding='utf-8') as fh:
                            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
                        for ln in lines:
                            try:
                                obj = json.loads(ln)
                            except Exception:
                                continue
                            # prefer engine == 'simulated' or status == 'simulated'
                            if obj.get('engine') == 'simulated' or obj.get('status') == 'simulated' or obj.get('type') == 'buy' and obj.get('engine') == 'simulated':
                                # consider timestamp in ISO format
                                try:
                                    ts = obj.get('timestamp')
                                    if ts:
                                        tdate = datetime.fromisoformat(ts).date()
                                        if tdate != today:
                                            continue
                                except Exception:
                                    pass
                                # pnl field if present (net_sol or pnl_sol)
                                try:
                                    pnl = float(obj.get('pnl_sol') or obj.get('net_sol') or 0.0)
                                except Exception:
                                    pnl = 0.0
                                total_pnl += pnl
                            # jito metrics
                            if obj.get('bundle_id') or obj.get('jito_bundle_id'):
                                jito_attempts += 1
                                st = obj.get('bundle_status') or obj.get('jito_status') or (obj.get('bundle_result') or {}).get('status') if isinstance(obj.get('bundle_result'), dict) else None
                                if st in ('ok', 'success', 'submitted') or obj.get('bundle_success') is True:
                                    jito_success += 1
                    except Exception:
                        pass

                # rpc avg latency
                try:
                    if getattr(brain, 'rpc_manager', None) is not None:
                        latencies = [v for v in brain.rpc_manager.latencies.values() if isinstance(v, (int, float))]
                        if latencies:
                            avg_latency = sum(latencies) / len(latencies)
                except Exception:
                    avg_latency = None

                jito_rate = (jito_success / jito_attempts * 100.0) if jito_attempts else 0.0

                body = (
                    f"⚔️ Comparison Summary — Paper Trade Battle Mode:\n\n"
                    f"Total Paper PnL Today: {total_pnl:+.4f} SOL\n\n"
                    f"Jito Success Rate: {jito_rate:.1f}%\n\n"
                    f"Latency (Avg): {int(avg_latency) if avg_latency is not None else 'n/a'}ms\n"
                )

                try:
                    # best-effort: only send if paper trading enabled
                    if getattr(config, 'USE_PAPER_TRADING', False):
                        await brain._send_discord_alert(body, success=True)
                except Exception:
                    pass

            except asyncio.CancelledError:
                return
            except Exception:
                pass

            await asyncio.sleep(interval_s)

    comp_task = asyncio.create_task(_paper_comparison_loop(brain, 3600))

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
            # Enforce daily circuit breaker from MarketBrain (best-effort)
            try:
                try:
                    tripped = await brain.enforce_daily_circuit_breaker()
                    if tripped:
                        console.print(Panel('Daily circuit breaker engaged; LIVE_TRADING_ENABLED set to False.', style='red'))
                except Exception:
                    pass
            except Exception:
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
                    # Strategy filtering: compute Volume Heat and Graduation Sniper signals
                    try:
                        mint = signal.get('mint')
                        vol_1m = signal.get('volume_1m') or signal.get('vol_1m') or signal.get('volume_pct')
                        vol_5m = signal.get('volume_5m') or signal.get('vol_5m') or (vol_1m * 5 if vol_1m is not None else None)

                        # If upstream payloads don't include explicit volume fields,
                        # synthesize them from recent ticks recorded in MarketBrain.
                        try:
                            if (vol_1m is None or vol_1m == 0) or (vol_5m is None or vol_5m == 0):
                                try:
                                    virt_1m, virt_5m = brain.get_virtual_volumes()
                                    if vol_1m is None or vol_1m == 0:
                                        vol_1m = virt_1m
                                    if vol_5m is None or vol_5m == 0:
                                        vol_5m = virt_5m
                                except Exception:
                                    # if virtual volume calc fails, continue with None (vhi.score will handle)
                                    pass
                        except Exception:
                            pass
                        try:
                            strength = vhi.score(vol_1m, vol_5m)
                        except Exception:
                            strength = 0

                        # build a migration-like event for GraduationSniper from available fields
                        migration_event = {
                            'mint': mint,
                            'is_lp_burned': signal.get('is_lp_burned', False),
                            'top_holder_concentration': signal.get('top_holder_concentration', 1.0),
                        }
                        try:
                            action, confidence, details = await sniper.handle_migration_event(migration_event)
                        except Exception:
                            action, confidence, details = False, 0.0, {}

                        top_strategy = 'Volume Heat' if strength >= (confidence or 0) else 'Graduation'

                        if strength < 50 or (not action and (confidence or 0) < 50):
                            console.print(Panel(f"SKIPPED: Low Confidence (strength={strength}, confidence={confidence}) for {mint}", style='yellow'))
                            # record skipped signal for telemetry
                            try:
                                entry_price = None
                                try:
                                    bp = await brain._call_birdeye_price(mint)
                                    entry_price = bp[0] if isinstance(bp, (list, tuple)) else bp
                                except Exception:
                                    entry_price = None
                                brain.simulated_trades.append({
                                    'mint': mint,
                                    'entry_price_usd': entry_price,
                                    'amount_sol': trade_value,
                                    'timestamp': datetime.now(timezone.utc).isoformat(),
                                    'status': 'skipped',
                                    'strategy': top_strategy,
                                })
                            except Exception:
                                pass
                        else:
                            console.print(Panel(f"TRADING: strength={strength} confidence={confidence} -> executing simulated swap for {mint}", style='green'))
                            # corroboration: require either positive social sentiment
                            # or net whale buy activity to proceed.
                            try:
                                social = await brain.get_social_sentiment(mint)
                            except Exception:
                                social = 0.0
                            try:
                                whale_score = await brain.get_whale_activity_score(mint)
                            except Exception:
                                whale_score = 0.0

                            # require at least one corroborating signal
                            if not (social > 0.1 or whale_score > 0.0):
                                console.print(Panel(f"SKIPPED: No corroborating social/whale activity for {mint} (social={social}, whale_score={whale_score})", style='yellow'))
                                # record skipped signal for telemetry
                                try:
                                    bp = await brain._call_birdeye_price(mint)
                                    entry_price = bp[0] if isinstance(bp, (list, tuple)) else bp
                                except Exception:
                                    entry_price = None
                                try:
                                    brain.simulated_trades.append({
                                        'mint': mint,
                                        'entry_price_usd': entry_price,
                                        'amount_sol': trade_value,
                                        'timestamp': datetime.now(timezone.utc).isoformat(),
                                        'status': 'skipped_no_corroboration',
                                        'strategy': top_strategy,
                                    })
                                except Exception:
                                    pass
                                continue

                            # Rug-check: ensure LP burned or top-holder concentration below threshold
                            try:
                                rug = await brain._rug_check(mint)
                                lp_burned = rug.get('lp_burned')
                                top_holder = rug.get('top_holder_pct')
                            except Exception:
                                lp_burned = False
                                top_holder = None

                            safe = False
                            try:
                                if lp_burned:
                                    safe = True
                                elif top_holder is not None and float(top_holder) < 0.20:
                                    safe = True
                                else:
                                    safe = False
                            except Exception:
                                safe = False

                            if not safe:
                                console.print(Panel(f"SKIPPED: Rug-check failed for {mint} (lp_burned={lp_burned}, top_holder_pct={top_holder})", style='red'))
                                try:
                                    bp = await brain._call_birdeye_price(mint)
                                    entry_price = bp[0] if isinstance(bp, (list, tuple)) else bp
                                except Exception:
                                    entry_price = None
                                try:
                                    brain.simulated_trades.append({
                                        'mint': mint,
                                        'entry_price_usd': entry_price,
                                        'amount_sol': trade_value,
                                        'timestamp': datetime.now(timezone.utc).isoformat(),
                                        'status': 'skipped_rug_check',
                                        'strategy': top_strategy,
                                    })
                                except Exception:
                                    pass
                                continue

                            # record entry price before sim and then run simulation
                            try:
                                bp = await brain._call_birdeye_price(mint)
                                entry_price = bp[0] if isinstance(bp, (list, tuple)) else bp
                            except Exception:
                                entry_price = None
                            # compute volatility-aware position sizing
                            try:
                                try:
                                    virt_1m, virt_5m = brain.get_virtual_volumes()
                                except Exception:
                                    virt_1m, virt_5m = (None, None)
                                try:
                                    raw_vhi = vhi.score(virt_1m, virt_5m)
                                except Exception:
                                    raw_vhi = 0.0
                                # normalize score to 0..1 if VolumeHeatIndex uses 0..100
                                try:
                                    vhi_norm = float(raw_vhi) / 100.0 if float(raw_vhi) > 1.0 else float(raw_vhi)
                                except Exception:
                                    vhi_norm = float(raw_vhi or 0.0)
                                size_sol = float(brain.get_smart_position_size(vhi_norm))
                            except Exception:
                                size_sol = float(trade_value)

                            try:
                                brain.simulated_trades.append({
                                    'mint': mint,
                                    'entry_price_usd': entry_price,
                                    'amount_sol': size_sol,
                                    'timestamp': datetime.now(timezone.utc).isoformat(),
                                    'status': 'simulated',
                                    'strategy': top_strategy,
                                    'rug_check': {'lp_burned': lp_burned, 'top_holder_pct': top_holder},
                                })
                                # persist structured trade log to JSONL (source of truth)
                                try:
                                    trade_entry = {
                                        'timestamp': datetime.now(timezone.utc).isoformat(),
                                        'pair': mint,
                                        'size': float(size_sol),
                                        'vhi': float(vhi_norm),
                                        'type': 'buy',
                                        'engine': 'simulated'
                                    }
                                    try:
                                        await brain.append_trade_log(trade_entry)
                                    except Exception:
                                        # best-effort logging; non-fatal
                                        pass
                                except Exception:
                                    pass
                            except Exception:
                                pass

                            # Report the new buy to Discord including rug status
                            try:
                                # include slippage & priority fee telemetry in the buy alert
                                try:
                                    sl_bps = await brain.get_dynamic_slippage(int(te.DEFAULT_SLIPPAGE_BPS))
                                except Exception:
                                    sl_bps = int(te.DEFAULT_SLIPPAGE_BPS)
                                try:
                                    pf = await brain.get_recent_priority_fee(percentile=80, slots=150)
                                except Exception:
                                    pf = int(os.getenv('PRIORITY_FEE', '10000'))

                                rug_msg = '✅ LP Burned' if lp_burned else (f'⚠️ Top Holder: {top_holder:.2%}' if top_holder is not None else '⚠️ Rug Check: Unknown')
                                try:
                                    vhi_display = round(vhi_norm, 3)
                                except Exception:
                                    vhi_display = 0.0
                                meta = {'vhi': vhi_display, 'size': float(size_sol), 'symbol': mint, 'paper': True}
                                await brain._send_discord_alert(f"🟢 New BUY (simulated) for {mint} | Strategy={top_strategy} | Rug: {rug_msg} | Slippage: {sl_bps}bps | PriorityFee: {pf} (lamports) | 📏 Position Sizing: {size_sol} SOL (VHI: {vhi_display})", success=True, meta=meta)
                            except Exception:
                                pass

                            await simulate_swap_and_log(rpc, signal, amount_sol=size_sol, input_amount_sol=size_sol)
                    except Exception:
                        # If any error in strategy filtering or recording occurs,
                        # skip this signal gracefully and continue main loop.
                        console.print(Panel(f"Strategy filtering error for signal {signal.get('mint')}: continuing", style='yellow'))
                        pass
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
