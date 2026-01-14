#!/usr/bin/env python3
"""MarketBrain: discovers tokens, watches whales, and triggers dry-run trades.

This is intentionally conservative: by default it will only simulate trades
and never send on-chain. It integrates with the consolidated `trade_executor`
module to reuse Jupiter quote/swap helpers.

Usage: .venv/bin/python src/brain.py
"""
from __future__ import annotations

import asyncio
import time
import base64
import json
import os
import shutil
import glob
try:
    # Prefer timezone-aware UTC; Python 3.11+ exposes datetime.UTC. For older
    # versions, fall back to timezone.utc to remain compatible.
    from datetime import datetime, UTC
except Exception:
    from datetime import datetime, timezone as UTC
from typing import List

import httpx
from rich.console import Console
from rich.panel import Panel
import math
import random

from solders.pubkey import Pubkey
from solders.compute_budget import ID as COMPUTE_BUDGET_ID
from solana.rpc.async_api import AsyncClient

import src.trade_executor as te

console = Console()

# Whale DNA profiles: multipliers representing skill/conviction.
# Keys should be base58 pubkeys for tracked whales. You can override
# or extend this mapping in production as you onboard more profiles.
WHALE_PROFILES: dict[str, float] = {
    # Example mappings (match the initial_whales placeholders used above)
    '8Ldjm1eQvHx9XGvWzQpY6vVvBvXz9zZzQzPzV6zVv6': 1.5,  # Whale_Maker (high-frequency, high-win)
    '6a95f0f3R2A2v5fS2QvVvBvXz9zZzQzPzV6zVv6': 1.2,  # Whale_Sniper (fast entries)
    'Gh9ZwEmdLJ8DscKNTkTqPbNwLNNBjuSzaG9Vp2KGtKJr': 0.8,  # Whale_Chaser (follows trends)
}


class MarketBrain:
    def __init__(self, rpc: str | None = None, whales: List[str] | None = None):
        self.rpc = rpc or os.getenv('RPC_URL') or 'https://api.devnet.solana.com'
        # High-signal whale addresses (Base58). Replace with exact 44-char pubkeys.
        initial_whales = whales or [
            # The Strategist (High-frequency SOL accumulator)
            '8Ldjm1eQvHx9XGvWzQpY6vVvBvXz9zZzQzPzV6zVv6',
            # The LP Whale (HumidiFi liquidity)
            '6a95f0f3R2A2v5fS2QvVvBvXz9zZzQzPzV6zVv6',
            # The Early Adopter (ecosystem-wide smart money)
            'Gh9ZwEmdLJ8DscKNTkTqPbNwLNNBjuSzaG9Vp2KGtKJr',
        ]

        # Validate whales as Base58 Pubkeys; skip invalid entries with a warning.
        validated: list[str] = []
        for w in initial_whales:
            try:
                # attempt to parse; Pubkey.from_string will raise if invalid
                _ = Pubkey.from_string(w)
                validated.append(w)
            except Exception:
                console.print(Panel(f"[WARNING] Invalid whale pubkey skipped: {w}", style='yellow'))

        self.whales = validated
        self.birdeye_url = os.getenv('BIRDEYE_URL', 'https://public-api.birdeye.so/defi/v2/tokens/new_listing')
        self.poll_interval = int(os.getenv('BRAIN_POLL_INTERVAL', '30'))
        # minimal threshold: 300% volume spike
        self.volume_spike_threshold = float(os.getenv('VOLUME_SPIKE_THRESHOLD', '300.0'))
        # keep simple in-memory map of last seen signature for each whale
        self.last_signatures = {w: None for w in self.whales}
        # map of latest trending mints -> spike info for Alpha Filter
        self.trending_map: dict[str, dict] = {}

        # persistent state path (ensure data dir)
        data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
        os.makedirs(data_dir, exist_ok=True)
        self.state_path = os.path.join(data_dir, 'brain_state.json')

        # try to load persisted last_signatures
        self._load_state()

        # Ensure we persist initial keys (so brain_state.json contains keys even
        # before any activity). This helps auditing and ensures getSignaturesForAddress
        # will be called against stable keys on first loop.
        try:
            self._save_state()
        except Exception:
            pass

        # Address Lookup Table cache (key -> (ts, AddressLookupTableAccount))
        # Use an asyncio.Lock to protect concurrent access in async codepaths.
        self._alt_cache: dict[str, tuple[float, object]] = {}
        self._alt_cache_lock = asyncio.Lock()
        # max entries and ttl (seconds) can be tuned via env
        self._alt_cache_max = int(os.getenv('ALT_CACHE_MAX', '128'))
        self._alt_cache_ttl = int(os.getenv('ALT_CACHE_TTL', '600'))
        # short-lived flash cache for Birdeye volume lookups to de-duplicate
        # repeated requests for the same mint during bursts. Structure:
        # { mint_address: { 'volume': float, 'ts': float } }
        self._volume_cache: dict[str, dict] = {}

        # Birdeye rate limiter: allow up to N requests per WINDOW seconds
        self._birdeye_rate = int(os.getenv('BIRDEYE_RATE_PER_WINDOW', '5'))
        self._birdeye_window = float(os.getenv('BIRDEYE_WINDOW_SECONDS', '1.0'))
        self._birdeye_lock = asyncio.Lock()
        # timestamps (float seconds) of recent birdeye requests
        self._birdeye_ts: list[float] = []

        # token decimals cache: mint -> (decimals:int, ts: float)
        # TTL default 24h
        self._decimals_cache: dict[str, tuple[int, float]] = {}
        self._decimals_cache_ttl = int(os.getenv('DECIMALS_CACHE_TTL', str(24 * 60 * 60)))

        # Load whale profiles from path (env override allowed)
        default_profiles = os.path.join(data_dir, 'whale_profiles.json')
        self.whale_profiles_path = os.getenv('WHALE_PROFILES_PATH', default_profiles)
        self.whale_profiles: dict[str, float] = {}
        try:
            self._load_whale_profiles()
        except Exception:
            # fall back to module-level defaults
            self.whale_profiles = dict(WHALE_PROFILES)


    def _load_state(self):
        try:
            if os.path.exists(self.state_path):
                with open(self.state_path, 'r', encoding='utf-8') as fh:
                    obj = json.load(fh)
                    if isinstance(obj, dict):
                        # only keep keys we know (whales); fill missing with None
                        for w in self.whales:
                            if w in obj and obj[w]:
                                self.last_signatures[w] = obj[w]
        except Exception:
            # ignore load errors — start fresh
            pass

    def _save_state(self):
        try:
            tmp = self.state_path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump(self.last_signatures, fh)

            # before replacing the primary state file, rotate backups of the
            # existing file so we have redundancy in case of accidental deletion.
            try:
                self._rotate_backups(keep=5)
            except Exception:
                # non-fatal: continue to replace even if backups fail
                pass

            # atomic replace
            os.replace(tmp, self.state_path)
        except Exception:
            # best-effort persistence; ignore failures
            try:
                # cleanup tmp if present
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass

    def _rotate_backups(self, keep: int = 5):
        """Copy the current state file to data/backups/brain_state_{timestamp}.json

        Retain only the newest `keep` backups to prevent disk bloat.
        """
        try:
            state_dir = os.path.dirname(self.state_path)
            backups_dir = os.path.join(state_dir, 'backups')
            os.makedirs(backups_dir, exist_ok=True)

            # only rotate if there's an existing primary state to copy
            if not os.path.exists(self.state_path):
                return

            ts = datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
            dest = os.path.join(backups_dir, f'brain_state_{ts}.json')
            # copy metadata too
            shutil.copy2(self.state_path, dest)

            # cleanup old backups, keep the newest `keep` files
            pattern = os.path.join(backups_dir, 'brain_state_*.json')
            files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
            for old in files[keep:]:
                try:
                    os.remove(old)
                except Exception:
                    # ignore failures to delete old backups
                    pass
        except Exception:
            # non-fatal; backups are best-effort
            return

    async def fetch_trending(self):
        """Fetch trending/new-listing tokens from Birdeye V2 API."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(self.birdeye_url)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            console.print(Panel(f"Birdeye fetch error: {e}", style='yellow'))
            return None

    async def _get_birdeye_volume(self, mint_address: str) -> float | None:
        """Fetch 24h volume USD for a single token mint from Birdeye Price Volume endpoint.

        Returns the 24h volume in USD as float, or None if unavailable.
        """
        # flash cache: if we have a fresh (<60s) entry return immediately
        try:
            entry = self._volume_cache.get(mint_address)
            if entry:
                ts = entry.get('ts') or 0
                if (time.time() - ts) < 60:
                    return float(entry.get('volume'))
                else:
                    # stale -> drop
                    try:
                        del self._volume_cache[mint_address]
                    except Exception:
                        pass
        except Exception:
            # cache errors are non-fatal; proceed to normal flow
            pass

        url = os.getenv('BIRDEYE_PRICE_VOLUME_URL', 'https://public-api.birdeye.so/defi/price_volume/single')
        attempts = 3
        delay = 1.0
        data = None
        for attempt in range(1, attempts + 1):
            try:
                # Birdeye rate-limiting: ensure we don't exceed configured requests per window
                reserved_ts = None
                while True:
                    async with self._birdeye_lock:
                        now = time.time()
                        cutoff = now - self._birdeye_window
                        # prune old timestamps
                        self._birdeye_ts = [t for t in self._birdeye_ts if t >= cutoff]
                        if len(self._birdeye_ts) < self._birdeye_rate:
                            # reserve a slot
                            reserved_ts = now
                            self._birdeye_ts.append(reserved_ts)
                            break
                        else:
                            # compute sleep time until the oldest timestamp exits the window
                            oldest = self._birdeye_ts[0]
                            sleep_for = (oldest + self._birdeye_window) - now
                            if sleep_for <= 0:
                                # loop and try again
                                continue
                    # release lock while sleeping so other coroutines can proceed
                    await asyncio.sleep(sleep_for)
                try:
                    async with httpx.AsyncClient(timeout=8.0) as client:
                        resp = await client.get(url, params={'address': mint_address})
                        resp.raise_for_status()
                        data = resp.json()
                        break
                except Exception:
                    # if request failed, free the reserved timestamp so others can use the slot
                    if reserved_ts is not None:
                        try:
                            async with self._birdeye_lock:
                                # remove the reserved timestamp if still present
                                self._birdeye_ts = [t for t in self._birdeye_ts if t != reserved_ts]
                        except Exception:
                            pass
                    raise
            except Exception as e:
                if attempt < attempts:
                    console.print(Panel(f"[RETRY] Birdeye call failed for {mint_address}. Attempt {attempt}/{attempts}... {e}", style='yellow'))
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                else:
                    console.print(Panel(f"Birdeye volume fetch error for {mint_address}: {e}", style='yellow'))
                    data = None
        if data is None:
            return None

        # permissive parsing for various shapes
        vol = None
        try:
            if isinstance(data, dict):
                # common keys
                candidate = data.get('data') or data.get('result') or data
                if isinstance(candidate, dict):
                    for k in ('volume_24h_usd', 'volume24h', 'volume_24h', 'volume_usd_24h', 'volume'):
                        v = candidate.get(k)
                        if v is not None:
                            try:
                                vol = float(v)
                                break
                            except Exception:
                                continue
                # sometimes top-level has numeric
                if vol is None:
                    for k in ('volume_24h_usd', 'volume24h', 'volume_24h'):
                        v = data.get(k)
                        if v is not None:
                            try:
                                vol = float(v)
                                break
                            except Exception:
                                continue
        except Exception:
            vol = None

        # update flash cache on success
        try:
            if vol is not None:
                self._volume_cache[mint_address] = {'volume': float(vol), 'ts': time.time()}
        except Exception:
            pass

        return vol

    async def _get_birdeye_price(self, mint_address: str) -> tuple[float | None, float | None]:
        """Fetch current price and liquidity (USD) for a token mint via Birdeye.

        Returns a tuple (price, liquidity_usd). Price may be in SOL or USD
        depending on Birdeye payload; callers should interpret price consistently
        with their usage. On failure returns (None, None).
        Uses the same rate-limiting reservation as _get_birdeye_volume.
        """
        url = os.getenv('BIRDEYE_PRICE_VOLUME_URL', 'https://public-api.birdeye.so/defi/price_volume/single')
        attempts = 3
        delay = 1.0
        data = None
        for attempt in range(1, attempts + 1):
            try:
                reserved_ts = None
                while True:
                    async with self._birdeye_lock:
                        now = time.time()
                        cutoff = now - self._birdeye_window
                        self._birdeye_ts = [t for t in self._birdeye_ts if t >= cutoff]
                        if len(self._birdeye_ts) < self._birdeye_rate:
                            reserved_ts = now
                            self._birdeye_ts.append(reserved_ts)
                            break
                        else:
                            oldest = self._birdeye_ts[0]
                            sleep_for = (oldest + self._birdeye_window) - now
                            if sleep_for <= 0:
                                continue
                    await asyncio.sleep(sleep_for)
                try:
                    async with httpx.AsyncClient(timeout=8.0) as client:
                        resp = await client.get(url, params={'address': mint_address})
                        resp.raise_for_status()
                        data = resp.json()
                        break
                except Exception:
                    if reserved_ts is not None:
                        try:
                            async with self._birdeye_lock:
                                self._birdeye_ts = [t for t in self._birdeye_ts if t != reserved_ts]
                        except Exception:
                            pass
                    raise
            except Exception as e:
                if attempt < attempts:
                    console.print(Panel(f"[RETRY] Birdeye price call failed for {mint_address}. Attempt {attempt}/{attempts}... {e}", style='yellow'))
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                else:
                    console.print(Panel(f"Birdeye price fetch error for {mint_address}: {e}", style='yellow'))
                    data = None
        if data is None:
            return (None, None)

        try:
            candidate = data.get('data') or data.get('result') or data
            price_val = None
            liquidity_val = None
            if isinstance(candidate, dict):
                # extract liquidity if present (various key names)
                for lk in ('liquidityUsd', 'liquidity_usd', 'liquidity', 'poolLiquidityUsd'):
                    lv = candidate.get(lk)
                    if lv is not None:
                        try:
                            liquidity_val = float(lv)
                            break
                        except Exception:
                            continue

                # extract price from likely keys
                for k in ('price', 'priceUsd', 'price_usd', 'priceUsd24h'):
                    v = candidate.get(k)
                    if v is not None:
                        try:
                            price_val = float(v)
                            break
                        except Exception:
                            continue

            # fallback: top-level numeric
            if price_val is None:
                for k in ('price', 'priceUsd'):
                    v = data.get(k)
                    if v is not None:
                        try:
                            price_val = float(v)
                            break
                        except Exception:
                            continue

            return (price_val, liquidity_val)
        except Exception:
            pass

        return (None, None)

    async def _get_token_decimals(self, mint_address: str) -> int:
        """Return integer decimals for an SPL token mint. Cache results for 24h.

        Attempts get_token_supply RPC call and falls back to parsing account info if needed.
        Returns 0 on failure (conservative).
        """
        try:
            entry = self._decimals_cache.get(mint_address)
            if entry:
                dec, ts = entry
                if time.time() - ts < self._decimals_cache_ttl:
                    return int(dec)
                else:
                    try:
                        del self._decimals_cache[mint_address]
                    except Exception:
                        pass
        except Exception:
            pass

        # Attempt RPC call
        try:
            async with AsyncClient(self.rpc) as client:
                # prefer get_token_supply
                try:
                    res = await client.get_token_supply(mint_address)
                    val = getattr(res, 'value', None) or (res.get('result', {}).get('value') if isinstance(res, dict) else None)
                    if isinstance(val, dict) and 'decimals' in val:
                        dec = int(val.get('decimals') or 0)
                        self._decimals_cache[mint_address] = (dec, time.time())
                        return dec
                except Exception:
                    # fallback to account_info parse
                    pass

                try:
                    info = await client.get_account_info(mint_address, encoding='base64')
                    val = getattr(info, 'value', None) or (info.get('result', {}).get('value') if isinstance(info, dict) else None)
                    if val and isinstance(val, dict):
                        data_field = None
                        if isinstance(val.get('data'), list) and len(val.get('data')) >= 1:
                            data_field = val.get('data')[0]
                        elif isinstance(val.get('data'), str):
                            data_field = val.get('data')
                        if data_field:
                            raw = base64.b64decode(data_field)
                            # SPL mint layout: decimals at byte offset 44 (u8)
                            if len(raw) > 44:
                                dec = int(raw[44])
                                self._decimals_cache[mint_address] = (dec, time.time())
                                return dec
                except Exception:
                    pass
        except Exception:
            pass

        # fallback conservative default
        return 0

    async def _log_exit(self, mint: str, exit_type: str, amount_sol: float, price: float | None, success: bool = False):
        """Append an exit event to data/alpha_journal.csv with exit_type.

        This is best-effort logging for observability.
        """
        try:
            data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
            alpha_csv = os.path.join(data_dir, 'alpha_journal.csv')
            ts = datetime.now(UTC).isoformat()
            header_needed = not os.path.exists(alpha_csv)
            import csv
            with open(alpha_csv, 'a', newline='') as fh:
                writer = csv.writer(fh)
                if header_needed:
                    writer.writerow([
                        'ts', 'mint', 'name', 'volume_pct', 'expected_out_raw', 'expected_out_sol',
                        'input_amount_sol', 'unitsConsumed', 'balance_lamports', 'success', 'alpha_score', 'whale_multiplier', 'exit_type', 'fill_ratio'
                    ])
                # minimal row — we fill in fields we know; others left blank
                writer.writerow([ts, mint, '', '', '', '', amount_sol, '', '', success, '', '', exit_type, ''])
        except Exception:
            # logging failure is non-fatal
            pass

    def _log_execution_event(self, mint: str, event_type: str, data: dict):
        """Write a structured execution event to data/execution_events.csv.

        This is a lightweight telemetry sink for debugging and post-mortem analysis.
        """
        try:
            data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
            os.makedirs(data_dir, exist_ok=True)
            ev_csv = os.path.join(data_dir, 'execution_events.csv')
            import csv, json as _json
            ts = datetime.now(UTC).isoformat()
            header_needed = not os.path.exists(ev_csv)
            with open(ev_csv, 'a', newline='') as fh:
                writer = csv.writer(fh)
                if header_needed:
                    writer.writerow(['ts', 'mint', 'event_type', 'data_json'])
                writer.writerow([ts, mint, event_type, _json.dumps(data)])
        except Exception:
            pass

    async def _monitor_position_exits(self, mint: str, entry_price: float, position_sol: float | None = None, live: bool = False):
        """Monitor a position and trigger partial exits according to configured rules.

        Parameters:
        - mint: token mint to monitor
        - entry_price: price at which position was entered (in same units as Birdeye price)
        - position_sol: current position size in SOL (if None, will use ORCH_DEFAULT_INPUT_SOL)
        - live: if True, attempt live execution (requires --live and proper executor support). Default False.

        Behavior: polls current price every POLL_INTERVAL_SECONDS and checks TP1/TP2/SL.
        When a rule is hit, logs the partial exit and triggers a dry-run swap for the exit amount.
        """
        # load settings (env overrides take precedence)
        try:
            data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
            settings_path = os.getenv('SETTINGS_PATH', os.path.join(data_dir, 'settings.json'))
            if os.path.exists(settings_path):
                with open(settings_path, 'r', encoding='utf-8') as fh:
                    settings = json.load(fh)
            else:
                settings = {}
        except Exception:
            settings = {}

        tp1_pct = float(os.getenv('TP1_PCT', settings.get('TP1_PCT', 0.25)))
        tp1_size = float(os.getenv('TP1_SIZE', settings.get('TP1_SIZE', 0.5)))
        tp2_pct = float(os.getenv('TP2_PCT', settings.get('TP2_PCT', 1.0)))
        tp2_size = float(os.getenv('TP2_SIZE', settings.get('TP2_SIZE', 0.25)))
        sl_pct = float(os.getenv('SL_PCT', settings.get('SL_PCT', -0.15)))
        poll_interval = float(os.getenv('POLL_INTERVAL_SECONDS', settings.get('POLL_INTERVAL_SECONDS', 10)))

        if position_sol is None:
            try:
                position_sol = float(os.getenv('ORCH_DEFAULT_INPUT_SOL', '0.1'))
            except Exception:
                position_sol = 0.1

        # track whether each target was already executed
        tp1_done = False
        tp2_done = False
        sl_done = False

        console.print(Panel(f"Starting exit monitor for {mint}: entry_price={entry_price}, pos_sol={position_sol}", style='cyan'))

        while True:
            try:
                price, _ = await self._get_birdeye_price(mint)
            except Exception:
                price = None

            if price is None:
                console.print(Panel(f"Could not fetch price for {mint}; retrying in {poll_interval}s", style='yellow'))
                await asyncio.sleep(poll_interval)
                # go back to top of loop to retry fetching price
                continue
            # _execute_exit_swap has been refactored to a class method for testability

            # pct change from entry
            try:
                change = (price - float(entry_price)) / float(entry_price)
            except Exception:
                change = 0.0

            # check TP2 first (higher threshold)
            if not tp2_done and change >= tp2_pct:
                sell_amount = position_sol * tp2_size
                console.print(Panel(f"TP2 hit for {mint}: change={change:.3f} >= {tp2_pct}. Selling {sell_amount} SOL equivalent.", style='green'))
                # log the planned exit
                await self._log_exit(mint, 'TP2', sell_amount, price, success=False)
                # execute the exit using token->WSOL swap with decimal precision
                try:
                    await self._execute_exit_swap(mint, sell_amount, 'TP2', live=bool(os.getenv('ENABLE_LIVE_EXITS', '0') in ('1', 'true', 'True')))
                except Exception:
                    # fallback to dry-run simulation if exit execution fails
                    try:
                        await self.trigger_dry_run_swap(mint, amount_sol=sell_amount)
                    except Exception:
                        pass
                tp2_done = True

            # TP1 (lower threshold) — allow after TP2 if not done
            if not tp1_done and change >= tp1_pct:
                sell_amount = position_sol * tp1_size
                console.print(Panel(f"TP1 hit for {mint}: change={change:.3f} >= {tp1_pct}. Selling {sell_amount} SOL equivalent.", style='green'))
                await self._log_exit(mint, 'TP1', sell_amount, price, success=False)
                try:
                    await self._execute_exit_swap(mint, sell_amount, 'TP1', live=bool(os.getenv('ENABLE_LIVE_EXITS', '0') in ('1', 'true', 'True')))
                except Exception:
                    try:
                        await self.trigger_dry_run_swap(mint, amount_sol=sell_amount)
                    except Exception:
                        pass
                tp1_done = True

            # SL
            if not sl_done and change <= sl_pct:
                sell_amount = position_sol  # exit full position on hard stop
                console.print(Panel(f"SL hit for {mint}: change={change:.3f} <= {sl_pct}. Selling {sell_amount} SOL equivalent (full).", style='red'))
                await self._log_exit(mint, 'SL', sell_amount, price, success=False)
                try:
                    await self._execute_exit_swap(mint, sell_amount, 'SL', live=bool(os.getenv('ENABLE_LIVE_EXITS', '0') in ('1', 'true', 'True')))
                except Exception:
                    try:
                        await self.trigger_dry_run_swap(mint, amount_sol=sell_amount)
                    except Exception:
                        pass
                sl_done = True

            # stop monitoring when all targets done or SL executed
            if sl_done or (tp1_done and tp2_done):
                console.print(Panel(f"Exit monitoring complete for {mint} (tp1={tp1_done}, tp2={tp2_done}, sl={sl_done}).", style='cyan'))
                break

            await asyncio.sleep(poll_interval)

    async def check_volume_spikes(self):
        data = await self.fetch_trending()
        if not data:
            return []

        spikes = []
        # Support multiple shapes; try to find list of tokens
        tokens = None
        if isinstance(data, dict):
            # common key names
            tokens = data.get('data') or data.get('tokens') or data.get('results') or data.get('items')
        if tokens is None and isinstance(data, list):
            tokens = data

        if not tokens:
            return []

        for t in tokens:
            try:
                # permissive extraction
                name = t.get('name') or t.get('symbol') or t.get('token')
                mint = t.get('mint') or t.get('address') or t.get('tokenAddress')
                verified = t.get('verified') or t.get('isVerified') or False
                # volume change may appear under various keys
                vol_change = (t.get('volumeChangePct') or t.get('volume_change_pct') or t.get('volume_spike_pct') or t.get('volumeChange'))
                if vol_change is None:
                    # try nested metrics
                    metrics = t.get('metrics') or {}
                    vol_change = metrics.get('volumeChangePct') or metrics.get('volumeSpike')

                # normalize value to float percent
                vol_float = None
                if isinstance(vol_change, str):
                    try:
                        vol_float = float(vol_change)
                    except Exception:
                        vol_float = None
                elif isinstance(vol_change, (int, float)):
                    vol_float = float(vol_change)

                if mint and vol_float is not None and vol_float >= self.volume_spike_threshold and verified:
                    spike = {'name': name, 'mint': mint, 'volume_pct': vol_float, 'verified': verified}
                    spikes.append(spike)
            except Exception:
                continue

        # update trending map for alpha filter
        try:
            self.trending_map = {s['mint']: s for s in spikes}
        except Exception:
            self.trending_map = {}

        return spikes

    async def watch_whales(self):
        """Check recent signatures for known whale addresses and update last seen."""
        async with AsyncClient(self.rpc) as client:
            for w in self.whales:
                try:
                    # try multiple address formats: base58 string, 0x-hex -> bytes
                    pk = None
                    if isinstance(w, str):
                        if w.startswith('0x'):
                            # hex short or full; try full decode
                            hx = w[2:]
                            try:
                                b = bytes.fromhex(hx)
                                if len(b) == 32:
                                    pk = Pubkey.from_string(Pubkey.from_bytes(b).to_string())
                            except Exception:
                                pk = None
                        else:
                            try:
                                pk = Pubkey.from_string(w)
                            except Exception:
                                pk = None

                    if pk is None:
                        console.print(Panel(f"Skipping whale {w}: cannot parse as pubkey", style='yellow'))
                        continue

                    # Use 'until' to limit RPC work and only fetch the most recent 5 signatures
                    last_sig = self.last_signatures.get(w)
                    res = await client.get_signatures_for_address(pk, until=last_sig, limit=5)
                    val = getattr(res, 'value', None) or (res.get('result', {}).get('value') if isinstance(res, dict) else None)
                    if isinstance(val, list) and val:
                        # signatures are newest-first; iterate to find new ones
                        for entry in val:
                            sig0 = entry.get('signature') if isinstance(entry, dict) else None
                            if not sig0:
                                continue
                            # if we've already seen this signature, break
                            if sig0 == last_sig:
                                break
                            # new activity: examine transaction to find token mints involved
                            console.print(Panel(f"New whale activity for {w}: {sig0}", style='blue'))
                            self.last_signatures[w] = sig0
                            # persist immediately so restarts don't re-process
                            try:
                                self._save_state()
                            except Exception:
                                pass

                            # fetch transaction and inspect token mints
                            try:
                                # request transaction with support for address lookup table resolution
                                tx = await client.get_transaction(sig0, encoding='jsonParsed', max_supported_transaction_version=0)
                                tx_val = getattr(tx, 'value', None) or (tx.get('result', {}) if isinstance(tx, dict) else None)
                                if not tx_val:
                                    continue
                                meta = tx_val.get('meta') if isinstance(tx_val, dict) else None
                                if not meta:
                                    continue

                                # Build account keys list from the transaction message and resolved address lookups
                                account_keys = []
                                try:
                                    msg = tx_val.get('transaction', {}).get('message', {})
                                    # message.accountKeys may be present as list of pubkey strings
                                    ak = msg.get('accountKeys') or msg.get('accountKeys') or msg.get('account_keys')
                                    if isinstance(ak, list):
                                        account_keys = [k if isinstance(k, str) else k.get('pubkey') if isinstance(k, dict) else str(k) for k in ak]
                                except Exception:
                                    account_keys = []

                                # resolve any address lookup table keys and extend account_keys
                                try:
                                    resolved = await self._resolve_alt_keys(client, tx_val)
                                    if resolved:
                                        account_keys.extend(resolved)
                                except Exception:
                                    pass

                                # collect mints from pre/post token balances
                                mints = set()
                                for key in ('preTokenBalances', 'postTokenBalances'):
                                    for tb in meta.get(key, []) or []:
                                        mint = tb.get('mint') or tb.get('tokenMint')
                                        if mint:
                                            mints.add(mint)
                                        # if token balance references an accountIndex, ensure we map it to account key
                                        try:
                                            idx = tb.get('accountIndex')
                                            if idx is not None and isinstance(idx, int) and idx < len(account_keys):
                                                _ = account_keys[idx]
                                        except Exception:
                                            pass

                                # Alpha Filter: only trigger if any mint intersects trending mints
                                trending_mints = set(self.trending_map.keys())
                                intersect = mints & trending_mints
                                if intersect:
                                    for mint in intersect:
                                        spike = self.trending_map.get(mint, {})
                                        pct = spike.get('volume_pct')
                                        name = spike.get('name') or mint
                                        # Attempt to compute precise trade size in SOL for this whale
                                        try:
                                            size_sol = None
                                            # account_keys list may include the whale pubkey as a string
                                            ak = account_keys
                                            whale_str = str(pk)
                                            if whale_str in ak:
                                                idx = ak.index(whale_str)
                                                pre_balances = meta.get('preBalances') or meta.get('preBalances') or []
                                                post_balances = meta.get('postBalances') or meta.get('postBalances') or []
                                                if idx < len(pre_balances) and idx < len(post_balances):
                                                    pre = int(pre_balances[idx])
                                                    post = int(post_balances[idx])
                                                    # compute exact priority fee from compute budget instructions when possible
                                                    try:
                                                        priority_fee = self._get_exact_priority_fee(tx_val)
                                                    except Exception:
                                                        priority_fee = 0
                                                    # base fee: assume 5000 lamports per signature
                                                    sigs = tx_val.get('transaction', {}).get('signatures') or []
                                                    try:
                                                        num_sigs = len(sigs)
                                                    except Exception:
                                                        num_sigs = 1
                                                    base_fee = int(num_sigs * int(os.getenv('BASE_FEE_PER_SIGNATURE_LAMPORTS', '5000')))
                                                    fee = base_fee + int(priority_fee)
                                                    # compute absolute movement excluding fee
                                                    moved = abs((pre - post) - fee)
                                                    if moved < 0:
                                                        moved = 0
                                                    size_sol = float(moved) / 1e9
                                        except Exception:
                                            size_sol = None

                                        # attach estimated trade size to the spike for orchestrator conviction checks
                                        try:
                                            if size_sol is not None:
                                                spike['estimated_trade_sol'] = size_sol
                                        except Exception:
                                            pass

                                        console.print(Panel(f"[SIGNAL] Whale {w} interacting with {name} ({mint}) + Volume Spike {pct}% detected.", style='magenta'))
                                        if size_sol is not None:
                                            console.print(Panel(f"[WHALE] Movement detected: {size_sol:.9f} SOL", style='blue'))

                                        # Birdeye volume gate: ensure token has sufficient 24h USD volume
                                        try:
                                            vol = await self._get_birdeye_volume(mint)
                                        except Exception:
                                            vol = None

                                        min_vol = float(os.getenv('MIN_TOKEN_24H_VOLUME_USD', '50000'))
                                        if vol is not None and vol < min_vol:
                                            console.print(Panel(f"[FILTER] Skipping {mint}: Volume too low (${vol}).", style='yellow'))
                                            continue
                                        # strict gating: if volume cannot be determined after retries, skip the signal
                                        elif vol is None:
                                            console.print(Panel(f"[FILTER] Could not determine 24h volume for {mint}; skipping signal for safety.", style='yellow'))
                                            continue

                                        # Alpha scoring: prioritize the smartest money over the biggest
                                        try:
                                            trade_sol = float(size_sol) if (size_sol is not None) else float(os.getenv('ORCH_DEFAULT_INPUT_SOL', '0.1'))
                                        except Exception:
                                            trade_sol = 0.1

                                        try:
                                            # fetch dna multiplier used for logging/analysis
                                            dna = float(self.whale_profiles.get(str(pk), WHALE_PROFILES.get(str(pk), 1.0)))
                                        except Exception:
                                            dna = 1.0

                                        try:
                                            alpha_score = self._calculate_alpha_score(str(pk), trade_sol, vol)
                                        except Exception:
                                            alpha_score = 0.0

                                        try:
                                            spike['alpha_score'] = alpha_score
                                            spike['whale_multiplier'] = dna
                                        except Exception:
                                            pass

                                        min_alpha = float(os.getenv('MIN_ALPHA_SCORE_THRESHOLD', '10.0'))
                                        if alpha_score < min_alpha:
                                            console.print(Panel(f"[ALPHA FILTER] Skipping {mint}: alpha_score={alpha_score:.3f} < threshold {min_alpha}", style='yellow'))
                                            continue

                                        # pass the estimated size into the trigger (if present)
                                        await self.trigger_dry_run_swap(mint, amount_sol=trade_sol)
                                else:
                                    console.print(Panel(f"Whale {w} interacted with mints {list(mints)} but no trending match.", style='yellow'))
                            except Exception as e:
                                console.print(Panel(f"Error fetching transaction {sig0}: {e}", style='yellow'))
                except Exception as e:
                    console.print(Panel(f"Error watching whale {w}: {e}", style='yellow'))

    async def trigger_dry_run_swap(self, token_mint: str, amount_sol: float = 0.1):
        """Trigger the same simulation flow as trade_executor but for dynamic output mint.

        This function temporarily overrides `te.USDC_MINT` to the target token mint,
        requests a Jupiter quote and swap, signs with the local key, and simulates it.
        It will NOT send on-chain.
        """
        console.print(Panel(f"Triggering dry-run swap for mint: {token_mint} | amount: {amount_sol} SOL", title='Brain -> Executor', style='cyan'))

        # Temporarily swap the global constant in the executor module
        old_mint = te.USDC_MINT
        te.USDC_MINT = token_mint
        try:
            lamports = int(amount_sol * 1e9)
            # Input mint is WSOL for SOL trades
            WSOL_MINT_LITERAL = "So11111111111111111111111111111111111111112"
            quote = await te.get_jupiter_quote(WSOL_MINT_LITERAL, token_mint, lamports, te.DEFAULT_SLIPPAGE_BPS)
            if not quote:
                console.print(Panel('No quote received', style='red'))
                return

            # request swap transaction
            swap_resp = await te.get_jupiter_swap(quote, user_pubkey=str(te.load_key().pubkey()), wrap_and_unwrap=True)
            swap_tx_b64 = swap_resp.get('swapTransaction')
            if not swap_tx_b64:
                console.print(Panel(f"No swapTransaction in swap response: {swap_resp}", style='red'))
                return

            swap_tx_bytes = base64.b64decode(swap_tx_b64)
            tx1 = te.VersionedTransaction.from_bytes(swap_tx_bytes)
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
            signed_bytes = bytes(vtx)

            async with AsyncClient(self.rpc) as client:
                console.print('Simulating triggered transaction...')
                try:
                    sim = await client.simulate_transaction(vtx)
                except Exception:
                    sim = await client.simulate_transaction(signed_bytes)

                sim_val = getattr(sim, 'value', sim)
                units = None
                err = None
                if isinstance(sim_val, dict):
                    # RPC shaped response
                    units = sim_val.get('unitsConsumed') or (sim_val.get('result') or {}).get('value', {}).get('unitsConsumed')
                    err = sim_val.get('err') or (sim_val.get('result') or {}).get('value', {}).get('err')
                else:
                    units = getattr(sim_val, 'units_consumed', None) or getattr(sim_val, 'unitsConsumed', None)
                    err = getattr(sim_val, 'err', None)

                if err:
                    console.print(Panel(f"Triggered simulation failed: {err}", style='red'))
                else:
                    console.print(Panel(f"Triggered SIMULATION SUCCESS — unitsConsumed: {units}", style='green'))

        except Exception as e:
            console.print(Panel(f"Error in trigger_dry_run_swap: {e}", style='red'))
        finally:
            te.USDC_MINT = old_mint

    async def _execute_exit_swap(self, mint: str, amount_sol: float, exit_type: str, live: bool = False) -> bool:
        """Execute or simulate a token -> WSOL swap for a desired SOL-equivalent amount.

        This method converts the desired SOL amount into an approximate token amount
        using the current market price from Birdeye, requests a Jupiter quote for
        that token amount, simulates the resulting VersionedTransaction, and
        optionally sends it when `live` is True and the environment allows live exits.

        Returns True on successful simulation (or send), False otherwise.
        """
        try:
            WSOL_MINT_LITERAL = "So11111111111111111111111111111111111111112"

            # get a price for the token in SOL and pool liquidity in USD
            try:
                token_price_sol, pool_liquidity_usd = await self._get_birdeye_price(mint)
            except Exception:
                token_price_sol, pool_liquidity_usd = (None, None)

            if not token_price_sol or token_price_sol <= 0:
                console.print(Panel(f"Cannot determine price for {mint}; aborting exit {exit_type}", style='yellow'))
                # fallback to dry-run of swapping SOL for token (best-effort)
                try:
                    await self.trigger_dry_run_swap(mint, amount_sol=amount_sol)
                except Exception:
                    pass
                return False

            # compute token amount from SOL amount
            try:
                decimals = await self._get_token_decimals(mint)
            except Exception:
                decimals = 0

            token_amount = amount_sol / float(token_price_sol)
            base_amount = int(round(token_amount * (10 ** decimals)))
            if base_amount <= 0:
                console.print(Panel(f"Computed zero base amount for {mint} ({amount_sol} SOL -> {token_amount} tokens); aborting.", style='yellow'))
                return False

            # Liquidity-aware slippage gate: estimate USD size of sell and compare to pool liquidity
            try:
                # get SOL price in USD by querying WSOL price if possible
                sol_price_usd, _ = await self._get_birdeye_price("So11111111111111111111111111111111111111112")
            except Exception:
                sol_price_usd = None

            estimated_sell_usd = None
            if sol_price_usd is not None:
                try:
                    estimated_sell_usd = float(amount_sol) * float(sol_price_usd)
                except Exception:
                    estimated_sell_usd = None

            if pool_liquidity_usd is not None and estimated_sell_usd is not None:
                impact_pct = self._calculate_price_impact(estimated_sell_usd, float(pool_liquidity_usd))
                max_impact = float(os.getenv('MAX_IMPACT_PCT', '15'))
                if impact_pct > max_impact:
                    # Determine number of chunks needed to keep each chunk <= max_impact
                    n_chunks = int(math.ceil(impact_pct / max_impact))
                    console.print(Panel(f"[LIQUIDITY CHUNKING] Estimated impact {impact_pct:.1f}% > {max_impact}%. Splitting into {n_chunks} chunks for exit {exit_type} on {mint}.", style='yellow'))

                    # Chunked exit: attempt each chunk with retries; continue on per-chunk failures
                    succeeded_chunks = 0
                    # Dynamic chunking loop: re-evaluate price/liquidity before each chunk
                    succeeded_chunks = 0
                    consecutive_failures = 0
                    processed_sol = 0.0
                    remaining_sol = float(amount_sol)
                    chunk_index = 0

                    # safety cap to prevent infinite loops
                    max_total_chunks = int(os.getenv('CHUNK_MAX_TOTAL', '20'))
                    while remaining_sol > 0 and chunk_index < max_total_chunks:
                        # re-fetch price and liquidity to recalc chunks dynamically
                        try:
                            token_price_sol, pool_liquidity_usd = await self._get_birdeye_price(mint)
                        except Exception:
                            token_price_sol, pool_liquidity_usd = (token_price_sol, pool_liquidity_usd)

                        # determine number of chunks needed for remaining amount
                        max_impact = float(os.getenv('MAX_IMPACT_PCT', '15'))
                        try:
                            # get SOL USD price for computing USD sell size
                            sol_price_usd, _ = await self._get_birdeye_price(WSOL_MINT_LITERAL)
                        except Exception:
                            sol_price_usd = sol_price_usd if 'sol_price_usd' in locals() else None

                        estimated_sell_usd_rem = None
                        if sol_price_usd is not None:
                            try:
                                estimated_sell_usd_rem = float(remaining_sol) * float(sol_price_usd)
                            except Exception:
                                estimated_sell_usd_rem = None

                        if pool_liquidity_usd is not None and estimated_sell_usd_rem is not None:
                            impact_pct_rem = self._calculate_price_impact(estimated_sell_usd_rem, float(pool_liquidity_usd))
                            if impact_pct_rem <= max_impact:
                                n_chunks_now = 1
                            else:
                                n_chunks_now = int(math.ceil(impact_pct_rem / max_impact))
                        else:
                            n_chunks_now = 1

                        # compute this chunk amount as fraction of remaining
                        chunk_amount_sol = float(remaining_sol) / float(n_chunks_now)
                        chunk_index += 1
                        console.print(Panel(f"Executing dynamic chunk {chunk_index} (of approx {n_chunks_now}) for {mint}: {chunk_amount_sol} SOL", style='cyan'))

                        # compute token amount for this chunk
                        try:
                            token_amount_chunk = chunk_amount_sol / float(token_price_sol)
                            base_amount_chunk = int(round(token_amount_chunk * (10 ** decimals)))
                        except Exception:
                            base_amount_chunk = 0

                        if base_amount_chunk <= 0:
                            console.print(Panel(f"Chunk {chunk_index} computed zero base amount; skipping.", style='yellow'))
                            # treat as a failure for circuit tracking
                            consecutive_failures += 1
                            if consecutive_failures >= 3:
                                # trigger circuit breaker
                                self._log_execution_event(mint, 'CIRCUIT_BREAKER_TRIGGERED', {'reason': 'zero_base_amount', 'chunk_index': chunk_index})
                                console.print(Panel(f"Circuit breaker: aborting exit for {mint} due to repeated zero-size chunks.", style='red'))
                                break
                            continue

                        # retry loop for this chunk
                        attempts = 3
                        success = False
                        for attempt in range(1, attempts + 1):
                            try:
                                quote = await te.get_jupiter_quote(mint, WSOL_MINT_LITERAL, int(base_amount_chunk), te.DEFAULT_SLIPPAGE_BPS)
                                if not quote:
                                    raise Exception('No quote')
                                swap_resp = await te.get_jupiter_swap(quote, user_pubkey=str(te.load_key().pubkey()), wrap_and_unwrap=True)
                                swap_tx_b64 = swap_resp.get('swapTransaction')
                                if not swap_tx_b64:
                                    raise Exception(f'No swapTransaction in swap response: {swap_resp}')

                                swap_tx_bytes = base64.b64decode(swap_tx_b64)
                                tx1 = te.VersionedTransaction.from_bytes(swap_tx_bytes)

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

                                async with AsyncClient(self.rpc) as client:
                                    try:
                                        sim = await client.simulate_transaction(vtx)
                                    except Exception:
                                        sim = await client.simulate_transaction(bytes(vtx))

                                    sim_val = getattr(sim, 'value', sim)
                                    err = None
                                    units = None
                                    if isinstance(sim_val, dict):
                                        units = sim_val.get('unitsConsumed') or (sim_val.get('result') or {}).get('value', {}).get('unitsConsumed')
                                        err = sim_val.get('err') or (sim_val.get('result') or {}).get('value', {}).get('err')
                                    else:
                                        units = getattr(sim_val, 'units_consumed', None) or getattr(sim_val, 'unitsConsumed', None)
                                        err = getattr(sim_val, 'err', None)

                                    if err:
                                        raise Exception(f'Chunk simulation error: {err}')

                                    # send if live is enabled
                                    if live and (os.getenv('ENABLE_LIVE_EXITS', '0') in ('1', 'true', 'True')):
                                        try:
                                            raw = bytes(vtx)
                                            sent = await client.send_raw_transaction(raw)
                                            console.print(Panel(f"Chunk {chunk_index} tx sent: {sent}", style='green'))
                                        except Exception as e:
                                            raise

                                    # log per-chunk execution telemetry including unitsConsumed
                                    try:
                                        self._log_execution_event(mint, 'chunk_executed', {
                                            'chunk_index': chunk_index,
                                            'base_amount_chunk': base_amount_chunk,
                                            'unitsConsumed': units,
                                        })
                                    except Exception:
                                        pass

                                success = True
                                break
                                except Exception as e:
                                    console.print(Panel(f"Chunk {idx+1} attempt {attempt} failed: {e}", style='yellow'))
                                    if attempt < attempts:
                                        # exponential backoff with jitter
                                        try:
                                            initial_wait = float(os.getenv('CHUNK_RETRY_INITIAL_WAIT', '1'))
                                            max_wait = float(os.getenv('CHUNK_RETRY_MAX_WAIT', '8'))
                                        except Exception:
                                            initial_wait = 1.0
                                            max_wait = 8.0
                                        wait = min(max_wait, initial_wait * (2 ** (attempt - 1))) + random.uniform(0, 1)
                                        await asyncio.sleep(wait)
                                        continue
                                            else:
                                                console.print(Panel(f"Chunk {chunk_index} failed after {attempts} attempts; moving to next chunk.", style='red'))
                                if not success:
                                    consecutive_failures += 1
                                    # if too many consecutive chunk failures, trigger circuit breaker
                                    if consecutive_failures >= int(os.getenv('CHUNK_CIRCUIT_BREAKER_THRESHOLD', '3')):
                                        # record critical event and abort
                                        try:
                                            self._log_execution_event(mint, 'CIRCUIT_BREAKER_TRIGGERED', {
                                                'reason': 'consecutive_chunk_failures',
                                                'consecutive_failures': consecutive_failures,
                                                'chunk_index': chunk_index,
                                            })
                                        except Exception:
                                            pass
                                        console.print(Panel(f"Circuit breaker triggered after {consecutive_failures} consecutive chunk failures for {mint}.", style='red'))
                                        break
                                else:
                                    consecutive_failures = 0
                                    succeeded_chunks += 1
                                    processed_sol += chunk_amount_sol
                                    remaining_sol = float(amount_sol) - processed_sol
                        if success:
                            succeeded_chunks += 1

                        # cooldown between chunks to let liquidity rebalance (10-20s)
                        cooldown = float(os.getenv('CHUNK_COOLDOWN_SECONDS', '10'))
                        # support a random small jitter if desired
                        jitter = random.uniform(0, float(os.getenv('CHUNK_COOLDOWN_JITTER', '10')))
                        await asyncio.sleep(cooldown + jitter)
                    # after chunking, log telemetry: succeeded / total
                    try:
                        fill_ratio = f"{succeeded_chunks}/{n_chunks}"
                        self._log_execution_event(mint, 'chunking_completed', {
                            'succeeded_chunks': succeeded_chunks,
                            'total_chunks': n_chunks,
                            'fill_ratio': fill_ratio,
                            'max_impact': max_impact,
                            'estimated_usd_size': estimated_sell_usd,
                        })
                        # also append to alpha_journal for observability
                        try:
                            await self._log_exit(mint, exit_type, amount_sol, token_price_sol, success=(succeeded_chunks>0))
                        except Exception:
                            pass
                    except Exception:
                        pass

                    return succeeded_chunks > 0

            # request a Jupiter quote for token -> WSOL
            quote = await te.get_jupiter_quote(mint, WSOL_MINT_LITERAL, int(base_amount), te.DEFAULT_SLIPPAGE_BPS)
            if not quote:
                console.print(Panel('No quote received for exit swap', style='yellow'))
                try:
                    await self.trigger_dry_run_swap(mint, amount_sol=amount_sol)
                except Exception:
                    pass
                return False

            swap_resp = await te.get_jupiter_swap(quote, user_pubkey=str(te.load_key().pubkey()), wrap_and_unwrap=True)
            swap_tx_b64 = swap_resp.get('swapTransaction')
            if not swap_tx_b64:
                console.print(Panel(f"No swapTransaction in swap response: {swap_resp}", style='red'))
                return False

            swap_tx_bytes = base64.b64decode(swap_tx_b64)
            try:
                tx1 = te.VersionedTransaction.from_bytes(swap_tx_bytes)
            except Exception:
                console.print(Panel("Failed to decode swap transaction for exit.", style='red'))
                return False

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

            async with AsyncClient(self.rpc) as client:
                # simulate first
                try:
                    sim = await client.simulate_transaction(vtx)
                except Exception:
                    sim = await client.simulate_transaction(bytes(vtx))

                sim_val = getattr(sim, 'value', sim)
                err = None
                if isinstance(sim_val, dict):
                    err = sim_val.get('err') or (sim_val.get('result') or {}).get('value', {}).get('err')
                else:
                    err = getattr(sim_val, 'err', None)

                if err:
                    console.print(Panel(f"Exit simulation failed: {err}", style='red'))
                    return False

                # If live execution is enabled, send the raw transaction
                if live and (os.getenv('ENABLE_LIVE_EXITS', '0') in ('1', 'true', 'True')):
                    try:
                        raw = bytes(vtx)
                        sent = await client.send_raw_transaction(raw)
                        console.print(Panel(f"Exit tx sent: {sent}", style='green'))
                        return True
                    except Exception as e:
                        console.print(Panel(f"Failed to send exit tx: {e}", style='red'))
                        return False
                else:
                    console.print(Panel(f"Exit simulation successful (live disabled).", style='green'))
                    return True
        except Exception as e:
            console.print(Panel(f"_execute_exit_swap error: {e}", style='red'))
            return False

    def _calculate_price_impact(self, sell_amount_usd: float, pool_liquidity_usd: float) -> float:
        """Estimate price impact as a percentage given sell amount and pool liquidity in USD.

        Simple rule: impact % = (sell_amount / pool_liquidity) * 100. If pool_liquidity_usd
        is zero or None, return a very large impact (100%).
        """
        try:
            if not pool_liquidity_usd or pool_liquidity_usd <= 0:
                return 100.0
            return float(sell_amount_usd) / float(pool_liquidity_usd) * 100.0
        except Exception:
            return 100.0

    async def _resolve_alt_keys(self, client: AsyncClient, tx_val: dict) -> list[str]:
        """Resolve address lookup table keys (ALT) referenced by a VersionedTransaction.

        Returns a list of base58 pubkey strings corresponding to the resolved addresses
        (in the order they appear in the lookup tables). This is best-effort and will
        ignore any lookup tables that cannot be fetched or parsed.
        """
        try:
            lookups = None
            msg = tx_val.get('transaction', {}).get('message', {}) if isinstance(tx_val, dict) else None
            if msg:
                lookups = msg.get('addressTableLookups') or msg.get('address_table_lookups') or msg.get('addressTableLookups')

            if not lookups:
                return []

            resolved_keys: list[str] = []
            import base64
            from solders.address_lookup_table_account import AddressLookupTableAccount

            for lk in lookups:
                # permissive key extraction
                lookup_key = lk.get('accountKey') or lk.get('account_key') or lk.get('lookupTable') or lk.get('lookup_table')
                if not lookup_key:
                    continue

                addresses = None
                # check cache first
                try:
                    async with self._alt_cache_lock:
                        entry = self._alt_cache.get(lookup_key)
                        if entry:
                            ts, alt_obj = entry
                            if time.time() - ts < self._alt_cache_ttl:
                                addresses = getattr(alt_obj, 'addresses', [])
                            else:
                                # stale
                                try:
                                    del self._alt_cache[lookup_key]
                                except KeyError:
                                    pass
                except Exception:
                    addresses = None

                # fetch and cache if not present
                if addresses is None:
                    try:
                        info = await client.get_account_info(lookup_key, encoding='base64')
                        val = getattr(info, 'value', None) or (info.get('result', {}).get('value') if isinstance(info, dict) else None)
                        if not val:
                            continue
                        data_field = None
                        # RPC may return data as [base64, encoding]
                        if isinstance(val.get('data'), list) and len(val.get('data')) >= 1:
                            data_field = val.get('data')[0]
                        elif isinstance(val.get('data'), str):
                            data_field = val.get('data')
                        if not data_field:
                            continue

                        raw = base64.b64decode(data_field)
                        alt = AddressLookupTableAccount.from_bytes(raw)
                        addresses = getattr(alt, 'addresses', [])
                        if not addresses:
                            continue

                        # cache the decoded object
                        try:
                            async with self._alt_cache_lock:
                                self._alt_cache[lookup_key] = (time.time(), alt)
                                # evict oldest entries if over capacity
                                if len(self._alt_cache) > self._alt_cache_max:
                                    # sort by timestamp
                                    items = sorted(self._alt_cache.items(), key=lambda kv: kv[1][0])
                                    while len(self._alt_cache) > self._alt_cache_max:
                                        k_old = items.pop(0)[0]
                                        try:
                                            del self._alt_cache[k_old]
                                        except KeyError:
                                            pass
                        except Exception:
                            # caching failure is non-fatal
                            pass

                    except Exception:
                        # ignore failures per best-effort policy
                        continue

                # gather indexes from lookup object (writable + readonly)
                idxs = []
                for k in ('writableIndexes', 'writable_indexes', 'writable'):
                    if k in lk and isinstance(lk.get(k), list):
                        idxs.extend([int(x) for x in lk.get(k)])
                for k in ('readonlyIndexes', 'readonly_indexes', 'readonly'):
                    if k in lk and isinstance(lk.get(k), list):
                        idxs.extend([int(x) for x in lk.get(k)])

                # map indexes to addresses if in range
                for i in idxs:
                    if 0 <= i < len(addresses):
                        resolved_keys.append(str(addresses[i]))

            return resolved_keys
        except Exception:
            return []

    def _get_exact_priority_fee(self, tx_val: dict) -> int:
        """Best-effort parse of compute budget instructions to extract exact priority fee in lamports.

        Formula: priority_fee_lamports = (unit_price_microlamports * unit_limit) / 1_000_000
        Returns 0 if unable to determine.
        """
        try:
            msg = tx_val.get('transaction', {}).get('message', {}) if isinstance(tx_val, dict) else None
            if not msg:
                return 0

            # helper to map programIdIndex to program id string when needed
            account_keys = []
            ak = msg.get('accountKeys') or msg.get('account_keys') or msg.get('accountKeys')
            if isinstance(ak, list):
                account_keys = [k if isinstance(k, str) else k.get('pubkey') if isinstance(k, dict) else str(k) for k in ak]

            unit_price = None
            unit_limit = None

            instructions = msg.get('instructions') or []
            for instr in instructions:
                # determine program id string/index
                program = instr.get('programId') or instr.get('programIdIndex') or instr.get('program')
                if isinstance(program, int):
                    if 0 <= program < len(account_keys):
                        program = account_keys[program]
                    else:
                        program = None

                if not program:
                    continue

                # identify compute budget program by exact program id
                try:
                    if str(program) != str(COMPUTE_BUDGET_ID):
                        continue
                except Exception:
                    continue

                # prefer parsed instruction info when available
                parsed = instr.get('parsed') or {}
                if parsed and isinstance(parsed, dict):
                    typ = str(parsed.get('type', '')).lower()
                    info = parsed.get('info') or {}
                    # look for price
                    if 'price' in typ or 'setcomputeunitprice' in typ or 'set_compute_unit_price' in typ:
                        # common keys
                        for k in ('microLamports', 'micro_lamports', 'micro_lamport', 'micro'):
                            if k in info:
                                try:
                                    unit_price = int(info.get(k))
                                except Exception:
                                    pass

                    if 'limit' in typ or 'setcomputeunitlimit' in typ or 'set_compute_unit_limit' in typ:
                        for k in ('units', 'unitsConsumed', 'compute_units'):
                            if k in info:
                                try:
                                    unit_limit = int(info.get(k))
                                except Exception:
                                    pass

                # fallback: look at instruction data if present (binary/base64)
                if unit_price is None or unit_limit is None:
                    data = instr.get('data')
                    try:
                        if isinstance(data, str):
                            raw = base64.b64decode(data)
                        elif isinstance(data, (bytes, bytearray)):
                            raw = bytes(data)
                        else:
                            raw = None
                        if raw:
                            # instruction layout: first byte is discriminator
                            disc = raw[0]
                            # SetComputeUnitLimit = 0x02 -> next 4 bytes u32
                            if disc == 0x02 and unit_limit is None and len(raw) >= 5:
                                unit_limit = int.from_bytes(raw[1:5], 'little')
                            # SetComputeUnitPrice = 0x03 -> next 8 bytes u64 micro-lamports
                            if disc == 0x03 and unit_price is None and len(raw) >= 9:
                                unit_price = int.from_bytes(raw[1:9], 'little')
                    except Exception:
                        pass

            if unit_price is None or unit_limit is None:
                return 0

            priority_fee = int((int(unit_price) * int(unit_limit)) / 1_000_000)
            return max(0, priority_fee)
        except Exception:
            return 0

    def _load_whale_profiles(self):
        """Load whale_profiles.json from data directory into self.whale_profiles.

        If the file does not exist, write a default file using module WHALE_PROFILES.
        """
        # Safe-load: do not mutate self.whale_profiles unless load succeeds.
        try:
            if os.path.exists(self.whale_profiles_path):
                with open(self.whale_profiles_path, 'r', encoding='utf-8') as fh:
                    obj = json.load(fh)
                    if isinstance(obj, dict):
                        # coerce values to float
                        profiles = {}
                        for k, v in obj.items():
                            try:
                                profiles[str(k)] = float(v)
                            except Exception:
                                continue
                        # only set on successful parse
                        self.whale_profiles = profiles
                        return True
                    else:
                        return False

            # file missing: attempt to create default file and set profiles
            try:
                with open(self.whale_profiles_path, 'w', encoding='utf-8') as fh:
                    json.dump(WHALE_PROFILES, fh, indent=2)
                self.whale_profiles = dict(WHALE_PROFILES)
                return True
            except Exception:
                # failed to write default, but don't overwrite existing profiles
                return False
        except Exception:
            return False

    def reload_whale_profiles(self):
        """Public method to reload whale profiles at runtime.

        Returns True on success, False otherwise.
        """
        ok = False
        try:
            ok = self._load_whale_profiles()
        except Exception:
            ok = False
        return bool(ok)

    def _calculate_alpha_score(self, whale_address: str, trade_sol: float, token_volume: float) -> float:
        """Compute an alpha score for a whale signal.

        Formula: Score = (TradeSOL * DNAMultiplier) * log10(VolumeUSD / 1000)
        Returns a float (may be negative if volume < 1000). If token_volume <= 0,
        returns 0.
        """
        try:
            vol = float(token_volume) if token_volume is not None else 0.0
            if vol <= 0:
                return 0.0
            dna = self.whale_profiles.get(whale_address, WHALE_PROFILES.get(whale_address, 1.0))
            # floor the log term at 0 to avoid negative scores from tiny volumes
            raw_log = math.log10(max(vol / 1000.0, 1e-9))
            factor = max(0.0, raw_log)
            score = (float(trade_sol) * float(dna)) * factor
            return float(score)
        except Exception:
            return 0.0

    async def run(self):
        console.print(Panel('MarketBrain starting loop (dry-run only)...', style='green'))
        while True:
            try:
                # 1) check birdeye for volume spikes
                spikes = await self.check_volume_spikes()
                if spikes:
                    for s in spikes:
                        console.print(Panel(f"Volume spike found: {s['name']} ({s['mint']}) +{s['volume_pct']}%", style='magenta'))
                        # trigger dry-run swap for the first matching token
                        await self.trigger_dry_run_swap(s['mint'], amount_sol=0.1)

                # 2) check whale signatures
                await self.watch_whales()

            except Exception as e:
                console.print(Panel(f"MarketBrain loop error: {e}", style='red'))

            await asyncio.sleep(self.poll_interval)

    async def get_alpha_signal(self):
        """Return the first matching spike (if any) for orchestration.

        This method is non-destructive and only reads Birdeye data to find the
        highest-priority spike that meets the volume threshold and verification.
        Returns a dict: {'name','mint','volume_pct','verified'} or None.
        """
        spikes = await self.check_volume_spikes()
        if spikes:
            # return the top spike (highest volume_pct)
            try:
                spikes_sorted = sorted(spikes, key=lambda x: float(x.get('volume_pct', 0)), reverse=True)
                return spikes_sorted[0]
            except Exception:
                return spikes[0]
        return None


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--rpc', type=str, default=None)
    parser.add_argument('--poll', type=int, default=30)
    args = parser.parse_args()

    brain = MarketBrain(rpc=args.rpc)
    brain.poll_interval = args.poll
    try:
        asyncio.run(brain.run())
    except KeyboardInterrupt:
        console.print('MarketBrain stopped', style='yellow')
