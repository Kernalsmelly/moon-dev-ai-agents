from __future__ import annotations

import asyncio
import os
from collections import deque
import inspect
from datetime import datetime, timezone
from typing import Callable, Iterable

import httpx
from pydantic import BaseModel, Field, field_validator


class WhaleActionModel(BaseModel):
    whale_address: str
    mint: str
    action: str  # 'buy' or 'sell'
    percent_of_position: float = Field(..., ge=0.0, le=100.0)
    volume_usd: float = Field(..., ge=0.0)
    timestamp: datetime
    win_rate_30d: float = Field(..., ge=0.0, le=1.0)

    @field_validator('action')
    @classmethod
    def action_must_be_buy_or_sell(cls, v):
        if v not in ('buy', 'sell'):
            raise ValueError('action must be "buy" or "sell"')
        return v


class WhaleWatcher:
    """Background watcher that polls a market-data API for recent transactions
    for a list of smart-wallet addresses and emits WhaleActionModel events to a
    provided callback when significant sell actions are detected.

    This is intentionally simple and defensive: it expects a JSON API at
    MARKET_DATA_API_URL that exposes a `/recent_transactions?address=...` endpoint
    returning a list of tx objects with fields including `mint`, `side` ('buy'/'sell'),
    `size_usd`, and `timestamp`. If the provider uses a different shape, adapt
    the parsing logic here.
    """

    def __init__(
        self,
        api_url: str,
        whales: Iterable[str] | None = None,
        watchlist_mints: Iterable[str] | None = None,
        callback: Callable[[WhaleActionModel], None] | None = None,
        poll_interval: float = 2.0,
    ) -> None:
        self.api_url = api_url.rstrip('/')
        self.whales = list(whales or [])
        self.watchlist = set(watchlist_mints or [])
        self.callback = callback
        self.poll_interval = float(poll_interval)
        self._seen: dict[str, set[str]] = {w: set() for w in self.whales}
        self._client = httpx.AsyncClient(timeout=6.0)
        # capped sigs to avoid RAM bloat
        try:
            import src.config as config
        except Exception:
            config = None
        try:
            default_len = int(getattr(config, 'MAX_HISTORY_SIZE', 1000)) if config is not None else 1000
        except Exception:
            default_len = 1000
        try:
            sigs_max = int(os.getenv('WHALE_SIGS_MAXLEN', str(default_len)))
        except Exception:
            sigs_max = default_len
        self.whale_sigs = deque(maxlen=sigs_max)

        # background task registry and graceful stop
        self._bg_tasks: set[asyncio.Task] = set()
        self._stop = asyncio.Event()
        self._brain = None
        # backward-compatible running flag used by some tests
        self._running = False

    def _create_tracked_task(self, coro_or_task, name: str | None = None):
        """Create or register a task and track it locally. If a MarketBrain
        is attached, prefer its tracked helper. Accepts either a coroutine
        or an existing asyncio.Task/awaitable.
        """
        # prefer brain helper when attached
        if self._brain is not None and hasattr(self._brain, '_create_tracked_task'):
            try:
                return self._brain._create_tracked_task(coro_or_task, name=name)
            except Exception:
                pass

        # If a Task was provided, register it; otherwise create one
        try:
            if isinstance(coro_or_task, asyncio.Task):
                task = coro_or_task
            else:
                task = asyncio.create_task(coro_or_task)
        except Exception:
            task = asyncio.create_task(coro_or_task)

        try:
            self._bg_tasks.add(task)
        except Exception:
            pass

        def _on_done(t: asyncio.Task):
            try:
                self._bg_tasks.discard(t)
            except Exception:
                pass

        try:
            task.add_done_callback(_on_done)
        except Exception:
            pass
        return task

    async def _fetch_recent(self, address: str):
        url = f"{self.api_url}/recent_transactions"
        try:
            resp = await self._client.get(url, params={'address': address})
            if resp.status_code != 200:
                return []
            data = resp.json()
            # expect a list of tx dicts
            if isinstance(data, dict) and 'data' in data:
                return data.get('data') or []
            return data if isinstance(data, list) else []
        except Exception:
            return []

    async def run(self):
        # run until stop event is set
        try:
            # indicate running for backwards-compatible tests
            try:
                self._running = True
            except Exception:
                pass
            while not self._stop.is_set():
                for w in self.whales:
                    txs = await self._fetch_recent(w)
                    for tx in txs:
                        # expected minimal shape: id, mint, side, size_usd, pct
                        try:
                            txid = tx.get('id') or tx.get('signature') or str(tx)
                            if txid in self._seen.get(w, set()):
                                continue
                            mint = tx.get('mint') or tx.get('token') or tx.get('asset')
                            if not mint:
                                continue
                            # only consider watchlist mints if provided
                            if self.watchlist and mint not in self.watchlist:
                                continue
                            side = (tx.get('side') or tx.get('action') or '').lower()
                            size_usd = float(tx.get('size_usd') or tx.get('value_usd') or 0)
                            pct = float(tx.get('pct_of_position') or tx.get('percent') or tx.get('percent_of_position') or 0)
                            ts_raw = tx.get('timestamp') or tx.get('time')
                            if ts_raw:
                                try:
                                    ts = datetime.fromisoformat(ts_raw)
                                except Exception:
                                    # fallback: epoch seconds
                                    ts = datetime.fromtimestamp(float(ts_raw), tz=timezone.utc)
                            else:
                                ts = datetime.now(tz=timezone.utc)

                            # optional: fetch performance metric for win rate
                            win_rate = 0.0
                            perf_url = f"{self.api_url}/performance"
                            try:
                                presp = await self._client.get(perf_url, params={'address': w})
                                if presp.status_code == 200:
                                    pdat = presp.json()
                                    win_rate = float(pdat.get('win_rate_30d', pdat.get('win_rate') or 0))
                            except Exception:
                                win_rate = 0.0

                            action = WhaleActionModel(
                                whale_address=w,
                                mint=mint,
                                action=side if side in ('buy', 'sell') else 'sell',
                                percent_of_position=pct,
                                volume_usd=size_usd,
                                timestamp=ts,
                                win_rate_30d=win_rate,
                            )

                            # mark seen
                            s = self._seen.setdefault(w, set())
                            s.add(txid)

                            # record sig and dispatch via tracked task
                            try:
                                self.whale_sigs.appendleft(txid)
                            except Exception:
                                pass

                            if self.callback:
                                try:
                                    # If callback is a coroutine function, schedule it; else run in loop-safe wrapper
                                    if inspect.iscoroutinefunction(self.callback):
                                        # prefer brain helper when attached
                                        if self._brain is not None and hasattr(self._brain, '_create_tracked_task'):
                                            try:
                                                self._brain._create_tracked_task(self.callback(action))
                                            except Exception:
                                                # fallback to local tracked helper
                                                try:
                                                    self._create_tracked_task(self.callback(action))
                                                except Exception:
                                                    pass
                                        else:
                                            try:
                                                # use local tracked helper so tasks are registered
                                                self._create_tracked_task(self.callback(action))
                                            except Exception:
                                                pass
                                    else:
                                        # sync callback: run in executor to avoid blocking
                                        try:
                                            loop = asyncio.get_running_loop()
                                        except RuntimeError:
                                            loop = None

                                        if loop is not None:
                                            try:
                                                fut = loop.run_in_executor(None, self.callback, action)
                                                # ensure_future returns a Task-like future we can register
                                                try:
                                                    self._create_tracked_task(asyncio.ensure_future(fut))
                                                except Exception:
                                                    pass
                                            except Exception:
                                                pass
                                        else:
                                            # No running event loop: best-effort background thread
                                            import threading
                                            try:
                                                t = threading.Thread(target=self.callback, args=(action,), daemon=True)
                                                t.start()
                                            except Exception:
                                                pass
                                except Exception:
                                    pass
                        except Exception:
                            continue
                try:
                    await asyncio.sleep(self.poll_interval)
                except asyncio.CancelledError:
                    break
        finally:
            try:
                self._running = False
            except Exception:
                pass
            try:
                await self._client.aclose()
            except Exception:
                pass

    def attach_brain(self, brain):
        """Attach a MarketBrain instance to reuse its tracked-task helper."""
        try:
            self._brain = brain
        except Exception:
            self._brain = None

    def stop(self):
        """Synchronous-compatible stop wrapper for backwards compatibility with
        synchronous tests and callers. This sets the `_running` flag to False
        immediately and then performs the async shutdown logic either by
        running it synchronously with `asyncio.run()` (when no running loop)
        or scheduling it on the current loop.
        """
        try:
            self._running = False
        except Exception:
            pass

        async def _shutdown():
            try:
                self._stop.set()
            except Exception:
                pass

            # cancel any locally tracked tasks
            tasks = list(self._bg_tasks or [])
            for t in tasks:
                try:
                    t.cancel()
                except Exception:
                    pass

            if tasks:
                try:
                    await asyncio.gather(*tasks, return_exceptions=True)
                except Exception:
                    pass

            try:
                self._bg_tasks.clear()
            except Exception:
                pass

            try:
                await self._client.aclose()
            except Exception:
                pass

        # If there's already a running loop, schedule the shutdown task; else run it
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            try:
                # schedule via tracked helper so it is registered
                self._create_tracked_task(_shutdown())
            except Exception:
                try:
                    asyncio.create_task(_shutdown())
                except Exception:
                    pass
        else:
            try:
                asyncio.run(_shutdown())
            except Exception:
                pass
