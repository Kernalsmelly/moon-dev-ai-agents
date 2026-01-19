"""Tests for brain persistence and state management."""
import json
import os
import time
from pathlib import Path

import pytest

from src.brain import MarketBrain


class TestRotateBackups:
    """Test the _rotate_backups method."""

    def test_rotate_backups_creates_backup(self, tmp_path, monkeypatch):
        """_rotate_backups should create a backup of the current state file."""
        data_dir = tmp_path / "data"
        backups_dir = data_dir / "backups"
        data_dir.mkdir(parents=True)

        # create a state file to be backed up
        state_file = data_dir / "brain_state.json"
        state_file.write_text(json.dumps({"test": "data"}))

        # Prevent __init__ side-effects
        monkeypatch.setenv('RPC_URL', 'http://localhost')

        brain = MarketBrain.__new__(MarketBrain)
        brain.state_path = str(state_file)

        # call rotate - should create backup
        brain._rotate_backups(keep=5)

        # backups dir should now exist with at least one backup
        assert backups_dir.exists()
        backups = list(backups_dir.glob("brain_state_*.json"))
        assert len(backups) >= 1

        # newest backup should have same content as original
        newest = sorted(backups)[-1]
        backed = json.loads(newest.read_text())
        assert backed == {"test": "data"}

    def test_rotate_backups_no_state_file(self, tmp_path, monkeypatch):
        """_rotate_backups should do nothing if no state file exists."""
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)

        state_file = data_dir / "brain_state.json"
        # Don't create the state file

        monkeypatch.setenv('RPC_URL', 'http://localhost')

        brain = MarketBrain.__new__(MarketBrain)
        brain.state_path = str(state_file)

        # Should not raise, just return silently
        brain._rotate_backups(keep=5)

        # No backups should exist
        backups_dir = data_dir / "backups"
        if backups_dir.exists():
            assert len(list(backups_dir.glob("*"))) == 0


class TestSaveState:
    """Test the _save_state method."""

    def test_save_state_writes_signatures(self, tmp_path, monkeypatch):
        """_save_state should write last_signatures to the state file."""
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)

        state_file = data_dir / "brain_state.json"

        monkeypatch.setenv('RPC_URL', 'http://localhost')

        brain = MarketBrain.__new__(MarketBrain)
        brain.state_path = str(state_file)
        brain.last_signatures = {"whale1": "sig1", "whale2": "sig2"}

        # Save state
        brain._save_state()

        # Verify state file was written
        assert state_file.exists()
        content = json.loads(state_file.read_text())
        assert content == {"whale1": "sig1", "whale2": "sig2"}

    def test_save_state_atomic_replace(self, tmp_path, monkeypatch):
        """_save_state should atomically replace existing state."""
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)

        state_file = data_dir / "brain_state.json"
        # Write initial state
        state_file.write_text(json.dumps({"old": "data"}))

        monkeypatch.setenv('RPC_URL', 'http://localhost')

        brain = MarketBrain.__new__(MarketBrain)
        brain.state_path = str(state_file)
        brain.last_signatures = {"new": "signatures"}

        # Save new state
        brain._save_state()

        # Verify content was replaced
        content = json.loads(state_file.read_text())
        assert content == {"new": "signatures"}
        assert "old" not in content

    def test_save_state_handles_empty_signatures(self, tmp_path, monkeypatch):
        """_save_state should handle empty signatures dict."""
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)

        state_file = data_dir / "brain_state.json"

        monkeypatch.setenv('RPC_URL', 'http://localhost')

        brain = MarketBrain.__new__(MarketBrain)
        brain.state_path = str(state_file)
        brain.last_signatures = {}

        # Save empty state
        brain._save_state()

        # Verify empty dict was written
        content = json.loads(state_file.read_text())
        assert content == {}
