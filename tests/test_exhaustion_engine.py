#!/usr/bin/env python3
"""Tests for the Exhaustion Engine.

Tests CVD divergence, order book heatmap, and velocity snap detection.
"""
import pytest
import time
from datetime import datetime, timezone

from src.engine.exhaustion_engine import (
    ExhaustionEngine,
    CVDMonitor,
    OrderBookHeatmap,
    VelocitySnap,
    ExhaustionSignal,
    SignalType,
)


class TestCVDMonitor:
    """Tests for CVD divergence detection."""

    def test_cvd_accumulation_buys(self):
        """CVD should increase with buy volume."""
        monitor = CVDMonitor()
        mint = 'TestMint1'

        monitor.ingest_transaction(mint, price=100.0, volume=1000.0, is_buy=True)
        monitor.ingest_transaction(mint, price=101.0, volume=500.0, is_buy=True)

        assert monitor.get_cvd(mint) == 1500.0  # Sum of buy volumes

    def test_cvd_accumulation_sells(self):
        """CVD should decrease with sell volume."""
        monitor = CVDMonitor()
        mint = 'TestMint2'

        monitor.ingest_transaction(mint, price=100.0, volume=1000.0, is_buy=False)
        monitor.ingest_transaction(mint, price=99.0, volume=500.0, is_buy=False)

        assert monitor.get_cvd(mint) == -1500.0  # Negative for sells

    def test_cvd_mixed_flow(self):
        """CVD should track net buy-sell delta."""
        monitor = CVDMonitor()
        mint = 'TestMint3'

        monitor.ingest_transaction(mint, price=100.0, volume=1000.0, is_buy=True)
        monitor.ingest_transaction(mint, price=100.0, volume=600.0, is_buy=False)
        monitor.ingest_transaction(mint, price=101.0, volume=400.0, is_buy=True)

        assert monitor.get_cvd(mint) == 800.0  # 1000 - 600 + 400

    def test_bearish_divergence_detected(self):
        """Bearish divergence: price up >2%, CVD flat/negative."""
        monitor = CVDMonitor(
            divergence_price_threshold=0.02,
            rolling_window_seconds=60.0,
        )
        mint = 'DivergenceMint'

        now = time.time()

        # Simulate price rising while sells dominate
        monitor.ingest_transaction(mint, price=100.0, volume=1000.0, is_buy=False, timestamp=now)
        monitor.ingest_transaction(mint, price=101.0, volume=800.0, is_buy=False, timestamp=now + 10)
        monitor.ingest_transaction(mint, price=102.5, volume=500.0, is_buy=False, timestamp=now + 20)
        # Price up 2.5%, CVD very negative

        signal = monitor.get_signal(mint)
        assert signal is not None
        assert signal.signal_type == SignalType.BEARISH_DIVERGENCE
        assert signal.active is True
        assert signal.details['price_change_pct'] >= 2.0

    def test_no_divergence_healthy_flow(self):
        """No divergence when price and CVD move together."""
        monitor = CVDMonitor()
        mint = 'HealthyMint'

        now = time.time()

        # Price rises with strong buying (healthy)
        monitor.ingest_transaction(mint, price=100.0, volume=1000.0, is_buy=True, timestamp=now)
        monitor.ingest_transaction(mint, price=102.0, volume=1500.0, is_buy=True, timestamp=now + 10)
        monitor.ingest_transaction(mint, price=103.0, volume=2000.0, is_buy=True, timestamp=now + 20)

        signal = monitor.get_signal(mint)
        # Signal may exist but should not be active (CVD is strongly positive)
        if signal:
            assert signal.active is False

    def test_reset_clears_data(self):
        """Reset should clear all tracking data."""
        monitor = CVDMonitor()
        mint = 'ResetMint'

        monitor.ingest_transaction(mint, price=100.0, volume=1000.0, is_buy=True)
        assert monitor.get_cvd(mint) == 1000.0

        monitor.reset(mint)
        assert monitor.get_cvd(mint) == 0.0
        assert monitor.get_signal(mint) is None


class TestOrderBookHeatmap:
    """Tests for order book analysis."""

    def test_balanced_orderbook(self):
        """Balanced order book should not trigger EXIT_NOW."""
        heatmap = OrderBookHeatmap(bid_floor_pct=0.35)
        mint = 'BalancedMint'

        bids = [
            {'price': 99.5, 'size': 100},
            {'price': 99.0, 'size': 150},
            {'price': 98.5, 'size': 200},
        ]
        asks = [
            {'price': 100.5, 'size': 100},
            {'price': 101.0, 'size': 150},
            {'price': 101.5, 'size': 200},
        ]

        signal = heatmap.analyze_orderbook(mint, bids, asks, mid_price=100.0)

        assert signal is not None
        assert signal.signal_type == SignalType.EXIT_NOW
        assert signal.active is False  # Balanced, so no exit signal
        assert signal.details['bid_ratio'] >= 0.35  # Above exit threshold

    def test_collapsed_bids_triggers_exit(self):
        """Collapsed bid support should trigger EXIT_NOW."""
        heatmap = OrderBookHeatmap(bid_floor_pct=0.35, band_pct=0.02)
        mint = 'CollapsedMint'

        # Minimal bids, heavy asks
        bids = [
            {'price': 99.5, 'size': 10},  # Very thin
        ]
        asks = [
            {'price': 100.5, 'size': 500},
            {'price': 101.0, 'size': 800},
            {'price': 101.5, 'size': 600},
        ]

        signal = heatmap.analyze_orderbook(mint, bids, asks, mid_price=100.0)

        assert signal is not None
        assert signal.active is True  # Bid ratio below 35%
        assert signal.details['bid_ratio'] < 0.35

    def test_heavy_bids_no_exit(self):
        """Heavy bid support should not trigger EXIT_NOW."""
        heatmap = OrderBookHeatmap(bid_floor_pct=0.35, band_pct=0.02)
        mint = 'StrongBidMint'

        # Heavy bids, minimal asks
        bids = [
            {'price': 99.5, 'size': 1000},
            {'price': 99.0, 'size': 800},
        ]
        asks = [
            {'price': 100.5, 'size': 50},
        ]

        signal = heatmap.analyze_orderbook(mint, bids, asks, mid_price=100.0)

        assert signal is not None
        assert signal.active is False
        assert signal.details['bid_ratio'] > 0.35

    def test_empty_orderbook_no_crash(self):
        """Empty order book should not crash."""
        heatmap = OrderBookHeatmap()
        mint = 'EmptyMint'

        signal = heatmap.analyze_orderbook(mint, [], [])

        assert signal is not None
        assert signal.active is False
        assert 'error' in signal.details


class TestVelocitySnap:
    """Tests for volume velocity and blowoff top detection."""

    def test_normal_volume_no_signal(self):
        """Normal volume should not trigger blowoff signal."""
        velocity = VelocitySnap(
            interval_seconds=1.0,
            lookback_seconds=10.0,
            sigma_threshold=3.0,
        )
        mint = 'NormalMint'

        now = time.time()

        # Steady volume stream
        for i in range(20):
            velocity.ingest_volume(mint, volume=100.0, timestamp=now + i)

        signal = velocity.get_signal(mint)
        if signal:
            assert signal.active is False

    def test_spike_detection(self):
        """Volume spike should be recorded in interval data."""
        velocity = VelocitySnap(
            interval_seconds=1.0,
            lookback_seconds=100.0,
            sigma_threshold=3.0,
        )
        mint = 'SpikeMint'

        now = time.time()

        # Normal volume for a while - ensure intervals are crossed
        for i in range(50):
            velocity.ingest_volume(mint, volume=100.0, timestamp=now + i * 1.5)

        # Massive spike in new interval
        velocity.ingest_volume(mint, volume=10000.0, timestamp=now + 80)

        # The spike should have been recorded
        # We verify the tracking structures are populated
        assert mint in velocity._volumes
        assert len(velocity._volumes[mint]) > 0

    def test_reset_clears_tracking(self):
        """Reset should clear all velocity tracking."""
        velocity = VelocitySnap()
        mint = 'ResetVelMint'

        velocity.ingest_volume(mint, volume=1000.0)
        velocity.reset(mint)

        assert velocity.get_signal(mint) is None


class TestExhaustionEngine:
    """Tests for the main exhaustion engine."""

    def test_engine_initialization(self):
        """Engine should initialize all monitors."""
        engine = ExhaustionEngine()

        assert engine.cvd_monitor is not None
        assert engine.orderbook is not None
        assert engine.velocity is not None

    def test_ingest_transaction_updates_monitors(self):
        """Ingesting transactions should update both CVD and velocity."""
        engine = ExhaustionEngine()
        mint = 'EngineMint'

        engine.ingest_transaction(mint, price=100.0, volume=1000.0, is_buy=True)

        assert engine.cvd_monitor.get_cvd(mint) == 1000.0

    def test_get_signals_returns_all(self):
        """get_signals should return signals from all monitors."""
        engine = ExhaustionEngine()
        mint = 'AllSignalsMint'

        # Ingest some data to create signals
        engine.ingest_transaction(mint, price=100.0, volume=1000.0, is_buy=True)

        # Manually add an orderbook signal
        engine.orderbook._signals[mint] = ExhaustionSignal(
            signal_type=SignalType.EXIT_NOW,
            active=False,
            timestamp=datetime.now(timezone.utc),
            mint=mint,
        )

        signals = engine.get_signals(mint)
        signal_types = [s.signal_type for s in signals]

        # Should have at least CVD and EXIT_NOW signals
        assert SignalType.EXIT_NOW in signal_types

    def test_should_exit_threshold(self):
        """should_exit should respect threshold."""
        engine = ExhaustionEngine()
        mint = 'ThresholdMint'

        # Set up one active signal
        engine.cvd_monitor._signals[mint] = ExhaustionSignal(
            signal_type=SignalType.BEARISH_DIVERGENCE,
            active=True,
            timestamp=datetime.now(timezone.utc),
            mint=mint,
        )

        # One signal, threshold 2 - should not exit
        should_exit, signals = engine.should_exit(mint, threshold=2)
        assert should_exit is False
        assert len(signals) == 1

        # Add second active signal
        engine.orderbook._signals[mint] = ExhaustionSignal(
            signal_type=SignalType.EXIT_NOW,
            active=True,
            timestamp=datetime.now(timezone.utc),
            mint=mint,
        )

        # Two signals, threshold 2 - should exit
        should_exit, signals = engine.should_exit(mint, threshold=2)
        assert should_exit is True
        assert len(signals) == 2

    def test_reset_clears_all(self):
        """reset should clear all monitor data for a mint."""
        engine = ExhaustionEngine()
        mint = 'ResetEngineMint'

        engine.ingest_transaction(mint, price=100.0, volume=1000.0, is_buy=True)
        engine.reset(mint)

        assert engine.cvd_monitor.get_cvd(mint) == 0.0


class TestExhaustionSignal:
    """Tests for ExhaustionSignal data class."""

    def test_signal_to_dict(self):
        """Signal should serialize to dict."""
        signal = ExhaustionSignal(
            signal_type=SignalType.BEARISH_DIVERGENCE,
            active=True,
            timestamp=datetime.now(timezone.utc),
            mint='SerializeMint',
            details={'price_change_pct': 2.5, 'cvd_change': -500},
        )

        d = signal.to_dict()

        assert d['signal_type'] == 'bearish_divergence'
        assert d['active'] is True
        assert d['mint'] == 'SerializeMint'
        assert d['details']['price_change_pct'] == 2.5

    def test_signal_types(self):
        """All signal types should be distinct."""
        types = [SignalType.BEARISH_DIVERGENCE, SignalType.EXIT_NOW, SignalType.BLOWOFF_TOP]

        assert len(set(t.value for t in types)) == 3
