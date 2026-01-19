#!/usr/bin/env python3
"""Exhaustion Engine: Wave-Rider exit signal detection.

Implements three complementary exhaustion signals:

1. CVD Divergence (Fuel Gauge):
   - Tracks Cumulative Volume Delta (Market Buys - Market Sells)
   - Triggers BEARISH_DIVERGENCE when price rises >2% but CVD flat/negative

2. Order Book Heatmap (The Floor):
   - Monitors bid/ask ratio within 1.5% of mid-price
   - Triggers EXIT_NOW when bid liquidity drops below 35% of localized depth

3. Velocity Snap:
   - Tracks volume standard deviation in 10-second intervals
   - Triggers BLOWOFF_TOP when volume >3σ above 1-hour mean then drops

Usage:
    from src.engine import ExhaustionEngine

    engine = ExhaustionEngine()
    await engine.start_monitoring(mint='So111...')

    # Check for signals
    signals = await engine.get_signals(mint)
    if len([s for s in signals if s.active]) >= 2:
        # Two or more exhaustion signals - execute immediate exit
        pass
"""
from __future__ import annotations

import asyncio
import math
import os
import statistics
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Optional

import httpx
try:
    import src.config as config
except Exception:
    config = None


class SignalType(Enum):
    """Types of exhaustion signals."""
    BEARISH_DIVERGENCE = 'bearish_divergence'
    EXIT_NOW = 'exit_now'
    BLOWOFF_TOP = 'blowoff_top'


@dataclass
class ExhaustionSignal:
    """An exhaustion signal event."""
    signal_type: SignalType
    active: bool
    timestamp: datetime
    mint: str
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            'signal_type': self.signal_type.value,
            'active': self.active,
            'timestamp': self.timestamp.isoformat(),
            'mint': self.mint,
            'details': self.details,
        }


@dataclass
class CVDDataPoint:
    """A single CVD data point."""
    timestamp: float
    price: float
    buy_volume: float
    sell_volume: float
    cvd: float  # Running cumulative volume delta


class CVDMonitor:
    """Cumulative Volume Delta monitor for bearish divergence detection.

    Tracks market buy vs sell volume and detects when price rises
    while buying pressure decreases (bearish divergence).
    """

    def __init__(
        self,
        birdeye_api_key: Optional[str] = None,
        divergence_price_threshold: float = 0.02,  # 2% price rise
        rolling_window_seconds: float = 60.0,  # 1-minute window
    ):
        self.api_key = birdeye_api_key or os.getenv('BIRDEYE_API_KEY', '')
        self.divergence_price_threshold = divergence_price_threshold
        self.rolling_window = rolling_window_seconds

        # Per-mint tracking
        self._data: dict[str, deque[CVDDataPoint]] = {}
        self._running_cvd: dict[str, float] = {}
        self._base_price: dict[str, float] = {}
        self._signals: dict[str, ExhaustionSignal] = {}

    def _get_mint_data(self, mint: str) -> deque[CVDDataPoint]:
        if mint not in self._data:
            try:
                maxlen = int(getattr(config, 'MAX_HISTORY_SIZE', 1000)) if config is not None else 1000
            except Exception:
                maxlen = 1000
            self._data[mint] = deque(maxlen=maxlen)
            self._running_cvd[mint] = 0.0
        return self._data[mint]

    def ingest_transaction(
        self,
        mint: str,
        price: float,
        volume: float,
        is_buy: bool,
        timestamp: Optional[float] = None,
    ):
        """Ingest a transaction and update CVD.

        Args:
            mint: Token mint address
            price: Transaction price in USD
            volume: Transaction volume in USD
            is_buy: True if market buy, False if market sell
            timestamp: Unix timestamp (defaults to now)
        """
        ts = timestamp or time.time()
        data = self._get_mint_data(mint)

        buy_vol = volume if is_buy else 0.0
        sell_vol = 0.0 if is_buy else volume

        # Update running CVD
        delta = buy_vol - sell_vol
        self._running_cvd[mint] = self._running_cvd.get(mint, 0.0) + delta

        point = CVDDataPoint(
            timestamp=ts,
            price=price,
            buy_volume=buy_vol,
            sell_volume=sell_vol,
            cvd=self._running_cvd[mint],
        )
        data.append(point)

        # Set base price if not set
        if mint not in self._base_price:
            self._base_price[mint] = price

        # Check for divergence
        self._check_divergence(mint)

    def _check_divergence(self, mint: str):
        """Check for bearish divergence (price up, CVD flat/down)."""
        data = self._get_mint_data(mint)
        if len(data) < 2:
            return

        now = time.time()
        cutoff = now - self.rolling_window

        # Get data within rolling window
        window_data = [d for d in data if d.timestamp >= cutoff]
        if len(window_data) < 2:
            return

        start_point = window_data[0]
        end_point = window_data[-1]

        # Calculate price change
        price_change = (end_point.price - start_point.price) / start_point.price if start_point.price > 0 else 0

        # Calculate CVD change
        cvd_change = end_point.cvd - start_point.cvd

        # Detect bearish divergence: price up >2%, CVD flat or negative
        is_divergence = (
            price_change >= self.divergence_price_threshold and
            cvd_change <= 0
        )

        self._signals[mint] = ExhaustionSignal(
            signal_type=SignalType.BEARISH_DIVERGENCE,
            active=is_divergence,
            timestamp=datetime.now(timezone.utc),
            mint=mint,
            details={
                'price_change_pct': price_change * 100,
                'cvd_change': cvd_change,
                'window_seconds': self.rolling_window,
                'data_points': len(window_data),
            },
        )

    def get_signal(self, mint: str) -> Optional[ExhaustionSignal]:
        """Get the current CVD divergence signal for a mint."""
        return self._signals.get(mint)

    def get_cvd(self, mint: str) -> float:
        """Get the current running CVD for a mint."""
        return self._running_cvd.get(mint, 0.0)

    def reset(self, mint: str):
        """Reset tracking for a mint."""
        self._data.pop(mint, None)
        self._running_cvd.pop(mint, None)
        self._base_price.pop(mint, None)
        self._signals.pop(mint, None)


class OrderBookHeatmap:
    """Order book analysis for bid/ask ratio and liquidity floor detection.

    Monitors the bid/ask ratio within a tight band around mid-price
    and triggers EXIT_NOW when bid support collapses.
    """

    def __init__(
        self,
        jupiter_api_key: Optional[str] = None,
        band_pct: float = 0.015,  # 1.5% band around mid-price
        bid_floor_pct: float = 0.35,  # 35% bid liquidity threshold
        levels: int = 20,  # Top 20 levels
    ):
        self.api_key = jupiter_api_key or os.getenv('JUPITER_API_KEY', '')
        self.band_pct = band_pct
        self.bid_floor_pct = bid_floor_pct
        self.levels = levels

        self._signals: dict[str, ExhaustionSignal] = {}
        self._last_ratio: dict[str, float] = {}
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    async def fetch_orderbook(self, mint: str) -> Optional[dict]:
        """Fetch order book from Jupiter V6 API.

        Returns:
            dict with 'bids' and 'asks' arrays, or None on error
        """
        try:
            client = await self._get_client()

            # Jupiter V6 orderbook endpoint
            url = f"https://quote-api.jup.ag/v6/orderbook"
            params = {
                'inputMint': mint,
                'outputMint': 'So11111111111111111111111111111111111111112',  # WSOL
            }

            headers = {}
            if self.api_key:
                headers['Authorization'] = f'Bearer {self.api_key}'

            resp = await client.get(url, params=params, headers=headers)
            if resp.status_code != 200:
                return None

            data = resp.json()
            return data

        except Exception:
            return None

    def analyze_orderbook(
        self,
        mint: str,
        bids: list[dict],
        asks: list[dict],
        mid_price: Optional[float] = None,
    ) -> ExhaustionSignal:
        """Analyze order book and check for liquidity floor breach.

        Args:
            mint: Token mint address
            bids: List of bid levels [{'price': float, 'size': float}, ...]
            asks: List of ask levels [{'price': float, 'size': float}, ...]
            mid_price: Optional mid-price (calculated if not provided)

        Returns:
            ExhaustionSignal indicating if EXIT_NOW is active
        """
        # Calculate mid-price if not provided
        if mid_price is None:
            if bids and asks:
                best_bid = bids[0].get('price', 0)
                best_ask = asks[0].get('price', 0)
                if best_bid > 0 and best_ask > 0:
                    mid_price = (best_bid + best_ask) / 2
                else:
                    mid_price = 0
            else:
                mid_price = 0

        if mid_price <= 0:
            return ExhaustionSignal(
                signal_type=SignalType.EXIT_NOW,
                active=False,
                timestamp=datetime.now(timezone.utc),
                mint=mint,
                details={'error': 'invalid_mid_price'},
            )

        # Calculate band boundaries
        lower_band = mid_price * (1 - self.band_pct)
        upper_band = mid_price * (1 + self.band_pct)

        # Sum liquidity within band
        bid_liquidity = sum(
            b.get('size', 0) * b.get('price', 0)
            for b in bids[:self.levels]
            if lower_band <= b.get('price', 0) <= mid_price
        )

        ask_liquidity = sum(
            a.get('size', 0) * a.get('price', 0)
            for a in asks[:self.levels]
            if mid_price <= a.get('price', 0) <= upper_band
        )

        total_liquidity = bid_liquidity + ask_liquidity
        bid_ratio = bid_liquidity / total_liquidity if total_liquidity > 0 else 0.5

        self._last_ratio[mint] = bid_ratio

        # Check if bid support has collapsed
        is_exit_now = bid_ratio < self.bid_floor_pct

        signal = ExhaustionSignal(
            signal_type=SignalType.EXIT_NOW,
            active=is_exit_now,
            timestamp=datetime.now(timezone.utc),
            mint=mint,
            details={
                'bid_ratio': bid_ratio,
                'bid_liquidity_usd': bid_liquidity,
                'ask_liquidity_usd': ask_liquidity,
                'total_liquidity_usd': total_liquidity,
                'mid_price': mid_price,
                'band_pct': self.band_pct,
                'threshold': self.bid_floor_pct,
            },
        )

        self._signals[mint] = signal
        return signal

    async def check(self, mint: str) -> Optional[ExhaustionSignal]:
        """Fetch order book and check for EXIT_NOW signal.

        Args:
            mint: Token mint address

        Returns:
            ExhaustionSignal or None on error
        """
        orderbook = await self.fetch_orderbook(mint)
        if orderbook is None:
            return None

        bids = orderbook.get('bids', [])
        asks = orderbook.get('asks', [])

        return self.analyze_orderbook(mint, bids, asks)

    def get_signal(self, mint: str) -> Optional[ExhaustionSignal]:
        """Get the cached EXIT_NOW signal for a mint."""
        return self._signals.get(mint)

    def get_bid_ratio(self, mint: str) -> float:
        """Get the last computed bid ratio for a mint."""
        return self._last_ratio.get(mint, 0.5)

    async def close(self):
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None


class VelocitySnap:
    """Volume velocity monitor for blowoff top detection.

    Tracks volume in 10-second intervals and detects when volume
    spikes >3σ above the 1-hour mean then drops for 2 consecutive intervals.
    """

    def __init__(
        self,
        interval_seconds: float = 10.0,
        lookback_seconds: float = 3600.0,  # 1 hour
        sigma_threshold: float = 3.0,  # 3 standard deviations
        drop_intervals: int = 2,  # Drop for 2 consecutive intervals
    ):
        self.interval_seconds = interval_seconds
        self.lookback_seconds = lookback_seconds
        self.sigma_threshold = sigma_threshold
        self.drop_intervals = drop_intervals

        # Per-mint tracking
        self._volumes: dict[str, deque[tuple[float, float]]] = {}  # (timestamp, volume)
        self._interval_volumes: dict[str, deque[tuple[float, float]]] = {}  # Aggregated intervals
        self._signals: dict[str, ExhaustionSignal] = {}
        self._spike_detected: dict[str, bool] = {}
        self._drop_count: dict[str, int] = {}
        self._last_interval_end: dict[str, float] = {}

    def _get_volumes(self, mint: str) -> deque:
        if mint not in self._volumes:
            try:
                maxlen = int(getattr(config, 'MAX_HISTORY_SIZE', 1000)) if config is not None else 1000
            except Exception:
                maxlen = 1000
            # use bounded deques for volume histories to avoid unbounded growth
            self._volumes[mint] = deque(maxlen=maxlen)
            self._interval_volumes[mint] = deque(maxlen=maxlen)
            self._spike_detected[mint] = False
            self._drop_count[mint] = 0
            self._last_interval_end[mint] = 0.0
        return self._volumes[mint]

    def ingest_volume(self, mint: str, volume: float, timestamp: Optional[float] = None):
        """Ingest a volume data point.

        Args:
            mint: Token mint address
            volume: Volume in USD
            timestamp: Unix timestamp (defaults to now)
        """
        ts = timestamp or time.time()
        self._get_volumes(mint).append((ts, volume))

        # Aggregate into intervals
        self._aggregate_interval(mint, ts)

    def _aggregate_interval(self, mint: str, current_ts: float):
        """Aggregate raw volume data into interval buckets."""
        last_end = self._last_interval_end.get(mint, 0.0)

        # Check if we've crossed an interval boundary
        interval_end = (current_ts // self.interval_seconds + 1) * self.interval_seconds

        if interval_end > last_end and last_end > 0:
            # Aggregate volume for the completed interval
            interval_start = last_end
            raw_volumes = self._volumes.get(mint, deque())

            interval_volume = sum(
                vol for ts, vol in raw_volumes
                if interval_start <= ts < last_end + self.interval_seconds
            )

            self._interval_volumes[mint].append((last_end, interval_volume))
            self._last_interval_end[mint] = interval_end

            # Check for blowoff top
            self._check_blowoff(mint)
        elif last_end == 0.0:
            self._last_interval_end[mint] = interval_end

    def _check_blowoff(self, mint: str):
        """Check for blowoff top pattern."""
        intervals = self._interval_volumes.get(mint, deque())
        if len(intervals) < 10:  # Need sufficient data
            return

        now = time.time()
        cutoff = now - self.lookback_seconds

        # Get intervals within lookback period
        lookback_volumes = [vol for ts, vol in intervals if ts >= cutoff]
        if len(lookback_volumes) < 5:
            return

        # Calculate mean and std dev
        mean_vol = statistics.mean(lookback_volumes)
        if len(lookback_volumes) >= 2:
            std_vol = statistics.stdev(lookback_volumes)
        else:
            std_vol = 0.0

        if std_vol == 0:
            return

        # Get latest interval volume
        latest_vol = lookback_volumes[-1] if lookback_volumes else 0

        # Calculate z-score
        z_score = (latest_vol - mean_vol) / std_vol if std_vol > 0 else 0

        # Detect spike
        if z_score >= self.sigma_threshold:
            self._spike_detected[mint] = True
            self._drop_count[mint] = 0

        # After spike, check for consecutive drops
        elif self._spike_detected.get(mint, False):
            if len(lookback_volumes) >= 2:
                prev_vol = lookback_volumes[-2]
                if latest_vol < prev_vol:
                    self._drop_count[mint] = self._drop_count.get(mint, 0) + 1
                else:
                    self._drop_count[mint] = 0
                    self._spike_detected[mint] = False

        # Check if blowoff top pattern is confirmed
        is_blowoff = (
            self._spike_detected.get(mint, False) and
            self._drop_count.get(mint, 0) >= self.drop_intervals
        )

        if is_blowoff:
            # Reset after triggering
            self._spike_detected[mint] = False
            self._drop_count[mint] = 0

        self._signals[mint] = ExhaustionSignal(
            signal_type=SignalType.BLOWOFF_TOP,
            active=is_blowoff,
            timestamp=datetime.now(timezone.utc),
            mint=mint,
            details={
                'z_score': z_score,
                'mean_volume': mean_vol,
                'std_volume': std_vol,
                'latest_volume': latest_vol,
                'spike_detected': self._spike_detected.get(mint, False),
                'drop_count': self._drop_count.get(mint, 0),
                'threshold_sigma': self.sigma_threshold,
            },
        )

    def get_signal(self, mint: str) -> Optional[ExhaustionSignal]:
        """Get the current blowoff top signal for a mint."""
        return self._signals.get(mint)

    def reset(self, mint: str):
        """Reset tracking for a mint."""
        self._volumes.pop(mint, None)
        self._interval_volumes.pop(mint, None)
        self._signals.pop(mint, None)
        self._spike_detected.pop(mint, None)
        self._drop_count.pop(mint, None)
        self._last_interval_end.pop(mint, None)


class ExhaustionEngine:
    """Main exhaustion signal engine coordinating all three detectors.

    Combines CVD divergence, order book analysis, and velocity snap
    to generate comprehensive exhaustion signals.
    """

    def __init__(
        self,
        birdeye_api_key: Optional[str] = None,
        jupiter_api_key: Optional[str] = None,
        on_signal: Optional[Callable[[ExhaustionSignal], None]] = None,
    ):
        self.cvd_monitor = CVDMonitor(birdeye_api_key=birdeye_api_key)
        self.orderbook = OrderBookHeatmap(jupiter_api_key=jupiter_api_key)
        self.velocity = VelocitySnap()

        self._on_signal = on_signal
        self._monitoring: set[str] = set()
        # per-mint task registry (mint -> Task)
        self._tasks: dict[str, asyncio.Task] = {}
        # local background task set for engine-level tasks (if any)
        self._bg_tasks: set[asyncio.Task] = set()
        # graceful stop event to allow loops to exit cleanly
        self._stop = asyncio.Event()
        # optional MarketBrain attachment for centralized tracked-task creation
        self._brain = None

    def attach_brain(self, brain):
        """Optionally attach a MarketBrain so this engine can reuse its
        tracked-task helper for centralized lifecycle management.
        """
        try:
            self._brain = brain
        except Exception:
            self._brain = None

    def _create_tracked_task(self, coro, name: str | None = None):
        """Create a tracked asyncio.Task. Prefer the attached brain's helper
        when available; otherwise track locally and ensure removal on done.
        """
        # prefer brain helper when attached
        if self._brain is not None and hasattr(self._brain, '_create_tracked_task'):
            try:
                return self._brain._create_tracked_task(coro, name=name)
            except Exception:
                pass

        try:
            task = asyncio.create_task(coro)
        except Exception:
            task = asyncio.create_task(coro)
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

    async def stop(self):
        """Gracefully stop all engine monitoring tasks and await their completion."""
        try:
            self._stop.set()
        except Exception:
            pass

        # cancel per-mint tasks
        tasks = list(self._tasks.values()) if isinstance(self._tasks, dict) else []
        for t in tasks:
            try:
                t.cancel()
            except Exception:
                pass

        # cancel any other bg tasks
        more = list(self._bg_tasks or [])
        for t in more:
            try:
                t.cancel()
            except Exception:
                pass

        all_tasks = tasks + more
        if all_tasks:
            try:
                await asyncio.gather(*all_tasks, return_exceptions=True)
            except Exception:
                pass

        try:
            self._tasks.clear()
        except Exception:
            pass
        try:
            self._bg_tasks.clear()
        except Exception:
            pass

    async def start_monitoring(
        self,
        mint: str,
        poll_interval: float = 5.0,
    ):
        """Start monitoring a mint for exhaustion signals.

        Args:
            mint: Token mint address
            poll_interval: Seconds between order book checks
        """
        if mint in self._monitoring:
            return

        self._monitoring.add(mint)
        # schedule the background monitor using tracked task helper
        try:
            task = self._create_tracked_task(self._monitor_loop(mint, poll_interval), name=f'exhaustion-monitor-{mint}')
            if task is not None:
                self._tasks[mint] = task
        except Exception:
            # fallback to untracked task
            try:
                self._tasks[mint] = asyncio.create_task(self._monitor_loop(mint, poll_interval))
            except Exception:
                self._tasks[mint] = None

    async def _monitor_loop(self, mint: str, poll_interval: float):
        """Background monitoring loop for a mint."""
        # run while this mint is still being monitored and engine not stopped
        while mint in self._monitoring and not getattr(self, '_stop', asyncio.Event()).is_set():
            try:
                # Check order book
                signal = await self.orderbook.check(mint)
                if signal and signal.active and self._on_signal:
                    self._on_signal(signal)

            except asyncio.CancelledError:
                break
            except Exception:
                pass

            try:
                await asyncio.sleep(poll_interval)
            except asyncio.CancelledError:
                break

    def stop_monitoring(self, mint: str):
        """Stop monitoring a mint."""
        self._monitoring.discard(mint)
        task = self._tasks.pop(mint, None)
        if task and not task.done():
            task.cancel()

    def stop_all(self):
        """Stop monitoring all mints."""
        for mint in list(self._monitoring):
            self.stop_monitoring(mint)

    def ingest_transaction(
        self,
        mint: str,
        price: float,
        volume: float,
        is_buy: bool,
        timestamp: Optional[float] = None,
    ):
        """Ingest a transaction into CVD and velocity monitors.

        Args:
            mint: Token mint address
            price: Transaction price in USD
            volume: Transaction volume in USD
            is_buy: True if market buy, False if market sell
            timestamp: Unix timestamp
        """
        self.cvd_monitor.ingest_transaction(mint, price, volume, is_buy, timestamp)
        self.velocity.ingest_volume(mint, volume, timestamp)

        # Check for signals after ingestion
        cvd_signal = self.cvd_monitor.get_signal(mint)
        velocity_signal = self.velocity.get_signal(mint)

        if cvd_signal and cvd_signal.active and self._on_signal:
            self._on_signal(cvd_signal)

        if velocity_signal and velocity_signal.active and self._on_signal:
            self._on_signal(velocity_signal)

    def get_signals(self, mint: str) -> list[ExhaustionSignal]:
        """Get all current signals for a mint.

        Returns:
            List of ExhaustionSignal objects (active or not)
        """
        signals = []

        cvd_signal = self.cvd_monitor.get_signal(mint)
        if cvd_signal:
            signals.append(cvd_signal)

        orderbook_signal = self.orderbook.get_signal(mint)
        if orderbook_signal:
            signals.append(orderbook_signal)

        velocity_signal = self.velocity.get_signal(mint)
        if velocity_signal:
            signals.append(velocity_signal)

        return signals

    def get_active_signals(self, mint: str) -> list[ExhaustionSignal]:
        """Get only active signals for a mint.

        Returns:
            List of active ExhaustionSignal objects
        """
        return [s for s in self.get_signals(mint) if s.active]

    def should_exit(self, mint: str, threshold: int = 2) -> tuple[bool, list[ExhaustionSignal]]:
        """Check if enough exhaustion signals are active to trigger exit.

        Args:
            mint: Token mint address
            threshold: Number of active signals required (default 2)

        Returns:
            Tuple of (should_exit: bool, active_signals: list)
        """
        active = self.get_active_signals(mint)
        return (len(active) >= threshold, active)

    def reset(self, mint: str):
        """Reset all tracking for a mint."""
        self.cvd_monitor.reset(mint)
        self.velocity.reset(mint)

    async def close(self):
        """Clean up resources."""
        try:
            await self.stop()
        except Exception:
            # fallback to best-effort stop
            try:
                self.stop_all()
            except Exception:
                pass
        try:
            await self.orderbook.close()
        except Exception:
            pass


# Convenience function for quick signal check
async def check_exhaustion(
    mint: str,
    birdeye_api_key: Optional[str] = None,
    jupiter_api_key: Optional[str] = None,
) -> tuple[bool, list[ExhaustionSignal]]:
    """Quick check for exhaustion signals on a mint.

    Args:
        mint: Token mint address
        birdeye_api_key: Optional Birdeye API key
        jupiter_api_key: Optional Jupiter API key

    Returns:
        Tuple of (should_exit: bool, active_signals: list)
    """
    engine = ExhaustionEngine(
        birdeye_api_key=birdeye_api_key,
        jupiter_api_key=jupiter_api_key,
    )

    try:
        # Check order book
        await engine.orderbook.check(mint)

        # Return signal status
        return engine.should_exit(mint)

    finally:
        await engine.close()
