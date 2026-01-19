from __future__ import annotations

import asyncio
import os
import base64
from datetime import datetime, timezone
from typing import Any

import httpx
import time
from pathlib import Path

from rich.console import Console

console = Console()

# Module-level tracked task registry for handler-spawned background work
_bg_tasks: set[asyncio.Task] = set()

def _create_tracked_task(coro_or_task):
    try:
        if isinstance(coro_or_task, asyncio.Task):
            task = coro_or_task
        else:
            task = asyncio.create_task(coro_or_task)
    except Exception:
        task = asyncio.create_task(coro_or_task)
    try:
        _bg_tasks.add(task)
    except Exception:
        pass

    def _on_done(t: asyncio.Task):
        try:
            _bg_tasks.discard(t)
        except Exception:
            pass

    try:
        task.add_done_callback(_on_done)
    except Exception:
        pass
    return task

try:
    # Prefer the existing MarketBrain if available to reuse chunking logic
    from src.brain import MarketBrain
except Exception:
    MarketBrain = None

try:
    import src.trade_executor as te
except Exception:
    te = None


async def _quick_jito_submit(jito, mint: str, amount_sol: float, simulate: bool = True):
    """Quick path: build a tiny Jupiter swap tx for amount_sol -> mint and submit as a single-tx bundle.

    This is used as a fallback when MarketBrain._execute_exit_swap isn't reused. It aims to be fast and
    call JitoManager.submit_atomic_exit directly to shave processing time.
    """
    if te is None:
        console.print("trade_executor not available; cannot build swap tx", style="yellow")
        return {'success': False, 'note': 'no_trade_executor'}

    # amount_sol -> lamports
    lamports = int(amount_sol * 1e9)
    WSOL = getattr(te, 'WSOL_MINT', 'So11111111111111111111111111111111111111112') if hasattr(te, 'WSOL_MINT') else 'So11111111111111111111111111111111111111112'
    usdc = getattr(te, 'USDC_MINT', None) or getattr(__import__('src.config', fromlist=['USDC_ADDRESS']), 'USDC_ADDRESS', None)
    if not usdc:
        console.print('USDC mint not configured; cannot build quick swap', style='red')
        return {'success': False, 'note': 'no_usdc_mint'}

    try:
        quote = await te.get_jupiter_quote(WSOL, mint if mint else usdc, lamports, te.DEFAULT_SLIPPAGE_BPS)
        swap_resp = await te.get_jupiter_swap(quote, user_pubkey=str(te.load_key().pubkey()), wrap_and_unwrap=True)
        swap_tx_b64 = swap_resp.get('swapTransaction')
        if not swap_tx_b64:
            return {'success': False, 'note': 'no_swap_tx'}
        swap_tx_bytes = base64.b64decode(swap_tx_b64)
        # decode & sign
        try:
            tx1 = te.VersionedTransaction.from_bytes(swap_tx_bytes)
        except Exception:
            return {'success': False, 'note': 'decode_failed'}
        key = te.load_key()
        vtx = te.VersionedTransaction(tx1.message, [key])
        raw = bytes(vtx)
        # submit via jito
        resp = await jito.submit_atomic_exit([raw], simulate=simulate, symbol=f'SHADOW:{mint}', mint=mint, tip_lamports=int(getattr(__import__('src.config', fromlist=['JITO_DEFAULT_TIP_LAMPORTS']), 'JITO_DEFAULT_TIP_LAMPORTS', 10000)))
        return resp
    except Exception as e:
        return {'success': False, 'error': str(e)}


# Simple in-memory prefetch cache for quotes (mint -> (ts, quote))
PREFETCH_QUOTE_CACHE: dict[str, tuple[float, Any]] = {}
PREFETCH_TTL = float(os.getenv('PREFETCH_QUOTE_TTL_S', '60'))


async def prefetch_quotes(mints: list[str], lamports: int = 100000):
    """Prefetch Jupiter quotes for given mints and store them in memory cache.

    This helps avoid the quote round-trip when handling an incoming webhook.
    """
    if te is None:
        return {}
    results = {}
    for m in mints:
        try:
            q = await te.get_jupiter_quote(getattr(te, 'WSOL_MINT', 'So11111111111111111111111111111111111111112'), m, lamports, te.DEFAULT_SLIPPAGE_BPS)
            PREFETCH_QUOTE_CACHE[m] = (time.time(), q)
            results[m] = True
        except Exception:
            results[m] = False
    return results


def get_cached_quote_for_mint(mint: str):
    ent = PREFETCH_QUOTE_CACHE.get(mint)
    if not ent:
        return None
    ts, q = ent
    if time.time() - ts > PREFETCH_TTL:
        try:
            del PREFETCH_QUOTE_CACHE[mint]
        except Exception:
            pass
        return None
    return q


def _write_submit_marker(trace_id: str):
    """Write a timestamp file in data/ to signal jito submit was invoked for trace_id."""
    try:
        repo_root = Path(__file__).resolve().parents[3]
        data_dir = repo_root.joinpath('data')
        data_dir.mkdir(parents=True, exist_ok=True)
        p = data_dir.joinpath(f'flash_shadow_{trace_id}.txt')
        with p.open('w', encoding='utf-8') as fh:
            fh.write(str(int(time.time() * 1000)))
    except Exception:
        pass


async def handle_helius_enhanced(tx_payload: dict[str, Any], *, jito=None, market_api_url: str | None = None) -> dict:
    """Entry point for webhook server: parse Helius enhanced transaction payload and, if a tracked whale
    performed a qualifying sell, call JitoManager.submit_atomic_exit directly to shadow.

    Expected minimal payload shapes are tolerated; this function is defensive.
    """
    try:
        # load whales list from config/env
        try:
            cfg = __import__('src.config', fromlist=['WHALE_ADDRESSES'])
            whales = getattr(cfg, 'WHALE_ADDRESSES', None)
        except Exception:
            whales = None
        if not whales:
            # fallback to env comma separated
            w = os.getenv('WHALE_ADDRESSES', '')
            whales = [x.strip() for x in w.split(',') if x.strip()]

        whales_set = set(whales or [])

        # robustly parse transactions list
        txs = tx_payload.get('transactions') or tx_payload.get('enhancedTransactions') or tx_payload.get('data') or []
        if isinstance(txs, dict):
            txs = [txs]

        triggered = []
        for tx in txs:
            try:
                # look for transfers or tokenTransfers array
                transfers = tx.get('transfers') or tx.get('tokenTransfers') or tx.get('tokenTransfersByOwner') or []
                if isinstance(transfers, dict):
                    # try common shapes
                    transfers = transfers.get('items') or list(transfers.values())
                for t in transfers:
                    frm = t.get('from') or t.get('sender') or t.get('source')
                    to = t.get('to') or t.get('receiver') or t.get('dest')
                    mint = t.get('mint') or t.get('token') or t.get('asset')
                    amount_usd = float(t.get('valueUsd') or t.get('sizeUsd') or t.get('amountUsd') or 0)
                    pct = float(t.get('pctOfPosition') or t.get('pct_of_position') or t.get('percent') or 0)
                    # if a known whale is the sender, treat as a sell
                    if frm in whales_set and amount_usd > 0:
                        # confidence filters
                        min_win = float(os.getenv('SHADOW_MIN_WIN_RATE', '0.7'))
                        min_pct = float(os.getenv('SHADOW_MIN_PCT', '20.0'))
                        min_vol = float(os.getenv('SHADOW_MIN_VOLUME_USD', '1000.0'))
                        # try to get win rate from perf endpoint if available
                        win_rate = 0.0
                        try:
                            if market_api_url:
                                async with httpx.AsyncClient(timeout=3.0) as client:
                                    presp = await client.get(f"{market_api_url.rstrip('/')}/performance", params={'address': frm})
                                    if presp.status_code == 200:
                                        pdata = presp.json()
                                        win_rate = float(pdata.get('win_rate_30d', pdata.get('win_rate') or 0))
                        except Exception:
                            win_rate = 0.0

                        if win_rate < min_win or pct < min_pct or amount_usd < min_vol:
                            continue

                        # compute amount_sol from USD
                        try:
                            brain_mod = __import__('src.brain', fromlist=['MarketBrain'])
                            mb = MarketBrain(rpc=os.getenv('RPC_URL')) if MarketBrain is not None else None
                        except Exception:
                            mb = None

                        if mb is not None:
                            # call brain's exit directly (this will simulate unless ENABLE_LIVE_EXITS=1)
                            try:
                                sol_price = (await mb._get_birdeye_price('So11111111111111111111111111111111111111112'))[0]
                            except Exception:
                                sol_price = None
                            try:
                                amount_sol = float(amount_usd) / float(sol_price) if sol_price else float(os.getenv('ORCH_DEFAULT_INPUT_SOL', '0.05'))
                            except Exception:
                                amount_sol = float(os.getenv('ORCH_DEFAULT_INPUT_SOL', '0.05'))
                            amount_sol = min(amount_sol, float(os.getenv('SHADOW_MAX_SOL', '1.0')))
                            # write marker for benchmark tracing if provided
                            trace_id = tx_payload.get('trace_id') if isinstance(tx_payload, dict) else None
                            if trace_id:
                                _write_submit_marker(trace_id)
                            # submit via brain path (spawned task). Prefer brain's tracked helper when available.
                            try:
                                if hasattr(mb, '_create_tracked_task'):
                                    mb._create_tracked_task(mb._execute_exit_swap(mint, amount_sol, exit_type='flash_shadow', live=False))
                                elif hasattr(mb, '_create_task'):
                                    mb._create_task(mb._execute_exit_swap(mint, amount_sol, exit_type='flash_shadow', live=False))
                                else:
                                    _create_tracked_task(mb._execute_exit_swap(mint, amount_sol, exit_type='flash_shadow', live=False))
                            except Exception:
                                try:
                                    _create_tracked_task(mb._execute_exit_swap(mint, amount_sol, exit_type='flash_shadow', live=False))
                                except Exception:
                                    pass
                            triggered.append({'mint': mint, 'amount_sol': amount_sol, 'method': 'marketbrain'})
                        else:
                            # fallback: use jito directly with quick swap
                            if jito is None:
                                try:
                                    jm = __import__('infrastructure.jito_manager', fromlist=['JitoManager']).JitoManager(os.getenv('RPC_URL'))
                                except Exception:
                                    jm = None
                            else:
                                jm = jito
                            amount_sol = float(amount_usd) / 1.0 if amount_usd else float(os.getenv('ORCH_DEFAULT_INPUT_SOL', '0.05'))
                            amount_sol = min(amount_sol, float(os.getenv('SHADOW_MAX_SOL', '1.0')))
                            # write marker for benchmark tracing if provided
                            trace_id = tx_payload.get('trace_id') if isinstance(tx_payload, dict) else None
                            if trace_id:
                                _write_submit_marker(trace_id)
                            if jm is not None:
                                # prefer brain tracked task if available
                                try:
                                    if mb is not None and hasattr(mb, '_create_tracked_task'):
                                        mb._create_tracked_task(_quick_jito_submit(jm, mint or '', amount_sol, simulate=True))
                                    elif mb is not None and hasattr(mb, '_create_task'):
                                        mb._create_task(_quick_jito_submit(jm, mint or '', amount_sol, simulate=True))
                                    else:
                                        _create_tracked_task(_quick_jito_submit(jm, mint or '', amount_sol, simulate=True))
                                except Exception:
                                    try:
                                        _create_tracked_task(_quick_jito_submit(jm, mint or '', amount_sol, simulate=True))
                                    except Exception:
                                        pass
                                triggered.append({'mint': mint, 'amount_sol': amount_sol, 'method': 'quick_jito'})
            except Exception:
                continue

        return {'triggered': triggered}
    except Exception as e:
        return {'error': str(e)}
