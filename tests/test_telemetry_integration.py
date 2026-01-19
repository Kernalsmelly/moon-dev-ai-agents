"""Tests for telemetry integration.

These tests verify that telemetry events are properly logged
without testing the full execution flow which requires extensive mocking.
"""
import json
import csv
import os
import pytest
from pathlib import Path
from unittest.mock import Mock, patch

from src.brain import MarketBrain
import src.config as config


class TestTelemetryLogging:
    """Test the _log_execution_event method directly."""

    def test_log_execution_event_creates_file(self, tmp_path, monkeypatch):
        """_log_execution_event should create the CSV file if it doesn't exist."""
        ev_path = tmp_path / 'execution_events.csv'
        monkeypatch.setattr(config, 'EXECUTION_LOG_PATH', str(ev_path))

        # Create a minimal brain instance
        brain = MarketBrain.__new__(MarketBrain)

        # Call the logging method
        brain._log_execution_event('TestMint', 'test_event', {'key': 'value'})

        # Verify file exists
        assert ev_path.exists()

        # Verify content
        with open(ev_path, 'r', newline='') as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)

        assert len(rows) == 1
        assert rows[0]['mint'] == 'TestMint'
        assert rows[0]['event_type'] == 'test_event'
        data = json.loads(rows[0]['data_json'])
        assert data['key'] == 'value'

    def test_log_execution_event_appends(self, tmp_path, monkeypatch):
        """_log_execution_event should append to existing CSV."""
        ev_path = tmp_path / 'execution_events.csv'
        monkeypatch.setattr(config, 'EXECUTION_LOG_PATH', str(ev_path))

        brain = MarketBrain.__new__(MarketBrain)

        # Log two events
        brain._log_execution_event('Mint1', 'event1', {'n': 1})
        brain._log_execution_event('Mint2', 'event2', {'n': 2})

        # Verify both are present
        with open(ev_path, 'r', newline='') as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)

        assert len(rows) == 2
        assert rows[0]['mint'] == 'Mint1'
        assert rows[1]['mint'] == 'Mint2'

    def test_log_execution_event_handles_none_mint(self, tmp_path, monkeypatch):
        """_log_execution_event should handle None mint gracefully."""
        ev_path = tmp_path / 'execution_events.csv'
        monkeypatch.setattr(config, 'EXECUTION_LOG_PATH', str(ev_path))

        brain = MarketBrain.__new__(MarketBrain)

        # Log with None mint (used for system events)
        brain._log_execution_event(None, 'system_event', {'status': 'ok'})

        with open(ev_path, 'r', newline='') as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)

        assert len(rows) == 1
        # None should be stored as empty string or 'None'
        assert rows[0]['mint'] in ('', 'None')

    def test_log_execution_event_complex_data(self, tmp_path, monkeypatch):
        """_log_execution_event should handle complex nested data."""
        ev_path = tmp_path / 'execution_events.csv'
        monkeypatch.setattr(config, 'EXECUTION_LOG_PATH', str(ev_path))

        brain = MarketBrain.__new__(MarketBrain)

        complex_data = {
            'unitsConsumed': 85000,
            'estimated_impact_pct': 12.5,
            'shadow_mode': True,
            'symbol': 'TEST',
            'nested': {'a': 1, 'b': [2, 3, 4]},
        }
        brain._log_execution_event('ComplexMint', 'chunk_executed', complex_data)

        with open(ev_path, 'r', newline='') as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)

        assert len(rows) == 1
        data = json.loads(rows[0]['data_json'])
        assert data['unitsConsumed'] == 85000
        assert data['shadow_mode'] is True
        assert data['nested']['a'] == 1


class TestTelemetryEventTypes:
    """Test that various telemetry event types can be logged."""

    @pytest.fixture
    def brain_with_logging(self, tmp_path, monkeypatch):
        """Create a brain instance configured to log to tmp_path."""
        ev_path = tmp_path / 'execution_events.csv'
        monkeypatch.setattr(config, 'EXECUTION_LOG_PATH', str(ev_path))

        brain = MarketBrain.__new__(MarketBrain)
        brain._ev_path = ev_path
        return brain

    def test_chunk_executed_event(self, brain_with_logging):
        """chunk_executed event should include unitsConsumed."""
        brain_with_logging._log_execution_event(
            'SomeMint',
            'chunk_executed',
            {
                'chunk_index': 1,
                'base_amount_chunk': 1000000,
                'unitsConsumed': 92000,
                'quote_latency_ms': 150,
            }
        )

        with open(brain_with_logging._ev_path, 'r', newline='') as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)

        assert len(rows) == 1
        data = json.loads(rows[0]['data_json'])
        assert data['unitsConsumed'] == 92000
        assert data['chunk_index'] == 1

    def test_exit_simulated_event(self, brain_with_logging):
        """exit_simulated event should include shadow_mode flag."""
        brain_with_logging._log_execution_event(
            'ExitMint',
            'exit_simulated',
            {
                'unitsConsumed': 55555,
                'estimated_impact_pct': 5.2,
                'shadow_mode': True,
                'symbol': 'SOL',
            }
        )

        with open(brain_with_logging._ev_path, 'r', newline='') as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)

        data = json.loads(rows[0]['data_json'])
        assert data['shadow_mode'] is True
        assert data['symbol'] == 'SOL'

    def test_rpc_rotation_event(self, brain_with_logging):
        """rpc_rotation event should include old/new RPC URLs."""
        brain_with_logging._log_execution_event(
            None,
            'rpc_rotation',
            {
                'old': 'https://old-rpc.com',
                'new': 'https://new-rpc.com',
                'new_latency_ms': 50,
            }
        )

        with open(brain_with_logging._ev_path, 'r', newline='') as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)

        data = json.loads(rows[0]['data_json'])
        assert 'old-rpc' in data['old']
        assert 'new-rpc' in data['new']

    def test_circuit_breaker_event(self, brain_with_logging):
        """CIRCUIT_BREAKER_TRIGGERED event should be loggable."""
        brain_with_logging._log_execution_event(
            'FailedMint',
            'CIRCUIT_BREAKER_TRIGGERED',
            {
                'reason': 'consecutive_chunk_failures',
                'consecutive_failures': 3,
            }
        )

        with open(brain_with_logging._ev_path, 'r', newline='') as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)

        assert rows[0]['event_type'] == 'CIRCUIT_BREAKER_TRIGGERED'
        data = json.loads(rows[0]['data_json'])
        assert data['consecutive_failures'] == 3
