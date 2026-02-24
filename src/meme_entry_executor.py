#!/usr/bin/env python3
"""Meme Entry Executor: Jito-bundled entry execution for MEV protection.

Provides execute_entry_jito() which builds a Jupiter SOL->Token swap and
submits it via Jito bundle with retry. Falls back to direct swap if Jito
is unavailable.

Usage:
    from src.meme_entry_executor import execute_entry_jito

    result = await execute_entry_jito(brain, mint, amount_sol, symbol)
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Try to import trade executor for Jupiter swap building
try:
    import src.trade_executor as te
    HAS_TRADE_EXECUTOR = True
except Exception:
    te = None
    HAS_TRADE_EXECUTOR = False

# WSOL mint for SOL swaps
WSOL_MINT = 'So11111111111111111111111111111111111111112'


async def execute_entry_jito(
    brain,
    mint: str,
    amount_sol: float,
    symbol: str = '',
    tip_lamports: int | None = None,
) -> dict:
    """Execute entry swap via Jito bundle for MEV protection.

    Builds a Jupiter SOL->Token swap transaction, then submits it as a
    Jito bundle with automatic retry on transient failures.

    Falls back to direct swap if:
    - Jito manager is not available
    - Transaction building fails
    - All Jito retries are exhausted

    Args:
        brain: MarketBrain instance with .jito JitoManager
        mint: Token mint address to buy
        amount_sol: Amount of SOL to spend
        symbol: Token symbol for logging
        tip_lamports: Jito tip in lamports (auto-computed if None)

    Returns:
        dict with 'success', 'method' ('jito'|'direct'|'failed'), and details
    """
    jito = getattr(brain, 'jito', None)

    if jito is None:
        logger.info('No Jito manager available, falling back to direct swap')
        return await _execute_direct_swap(mint, amount_sol, symbol)

    # Step 1: Build the swap transaction
    try:
        swap_tx_bytes = await _build_swap_transaction(mint, amount_sol)
        if swap_tx_bytes is None:
            logger.warning('Failed to build swap transaction, falling back to direct')
            return await _execute_direct_swap(mint, amount_sol, symbol)
    except Exception as e:
        logger.warning('Swap transaction build error: %s, falling back to direct', e)
        return await _execute_direct_swap(mint, amount_sol, symbol)

    # Step 2: Compute tip if not provided
    if tip_lamports is None:
        try:
            tip_lamports = jito.get_dynamic_tip()
        except Exception:
            tip_lamports = int(os.getenv('JITO_DEFAULT_TIP_LAMPORTS', '10000'))

    # Step 3: Submit via Jito bundle with retry
    try:
        result = await jito.submit_with_retry(
            [swap_tx_bytes],
            mint=mint,
            symbol=symbol,
            tip_lamports=tip_lamports,
        )

        if result.get('success') or result.get('bundle_id'):
            bundle_id = result.get('bundle_id', '')
            logger.info('Jito entry bundle submitted: %s for %s (%s SOL)',
                       bundle_id, symbol, amount_sol)

            # Optionally confirm landing
            if bundle_id:
                try:
                    landed = await jito.confirm_bundle_landing(bundle_id)
                    if landed:
                        return {
                            'success': True,
                            'method': 'jito',
                            'bundle_id': bundle_id,
                            'landed': True,
                            'amount_sol': amount_sol,
                            'mint': mint,
                            'symbol': symbol,
                        }
                    else:
                        logger.warning('Jito entry bundle did not land: %s', bundle_id)
                except Exception as e:
                    logger.warning('Bundle landing check failed: %s', e)

            return {
                'success': True,
                'method': 'jito',
                'bundle_id': bundle_id,
                'landed': None,  # Unknown
                'amount_sol': amount_sol,
                'mint': mint,
                'symbol': symbol,
            }

        # Jito submission failed, fall back to direct
        logger.warning('Jito entry submission failed: %s', result.get('error', 'unknown'))
    except Exception as e:
        logger.warning('Jito entry error: %s, falling back to direct', e)

    # Fallback to direct swap
    return await _execute_direct_swap(mint, amount_sol, symbol)


async def _build_swap_transaction(mint: str, amount_sol: float) -> bytes | None:
    """Build a Jupiter SOL->Token swap transaction.

    Returns raw transaction bytes or None if building fails.
    """
    if not HAS_TRADE_EXECUTOR or te is None:
        return None

    try:
        # Convert SOL to lamports for Jupiter
        amount_lamports = int(amount_sol * 1e9)

        # Get Jupiter quote
        get_quote = getattr(te, 'get_jupiter_quote', None)
        get_swap = getattr(te, 'get_jupiter_swap', None)

        if not get_quote or not get_swap:
            return None

        quote = await get_quote(
            input_mint=WSOL_MINT,
            output_mint=mint,
            amount=amount_lamports,
        )
        if not quote:
            return None

        # Get swap transaction
        swap_resp = await get_swap(quote)
        if not swap_resp:
            return None

        swap_tx_b64 = swap_resp.get('swapTransaction')
        if not swap_tx_b64:
            return None

        return base64.b64decode(swap_tx_b64)

    except Exception as e:
        logger.warning('Failed to build swap transaction: %s', e)
        return None


async def _execute_direct_swap(mint: str, amount_sol: float, symbol: str) -> dict:
    """Execute a direct Jupiter swap without Jito bundling.

    This is the fallback path when Jito is unavailable.
    """
    if not HAS_TRADE_EXECUTOR or te is None:
        return {
            'success': False,
            'method': 'failed',
            'error': 'trade executor not available',
            'mint': mint,
            'symbol': symbol,
        }

    try:
        execute_swap = getattr(te, 'main', None)
        if execute_swap:
            await execute_swap(
                amount=amount_sol,
                input_mint=WSOL_MINT,
                output_mint=mint,
                live=True,
            )
            return {
                'success': True,
                'method': 'direct',
                'amount_sol': amount_sol,
                'mint': mint,
                'symbol': symbol,
            }
    except Exception as e:
        logger.warning('Direct swap failed: %s', e)

    return {
        'success': False,
        'method': 'failed',
        'error': 'swap execution failed',
        'mint': mint,
        'symbol': symbol,
    }
