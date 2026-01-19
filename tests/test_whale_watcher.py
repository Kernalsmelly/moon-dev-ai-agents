#!/usr/bin/env python3
"""Tests for WhaleWatcher Pydantic V2 model validation.

Verifies that WhaleActionModel correctly validates whale transaction data
and filters out invalid payloads.
"""
import pytest
from datetime import datetime, timezone
from pydantic import ValidationError

from src.adapters.agents.whale_watcher import WhaleActionModel, WhaleWatcher


class TestWhaleActionModelValidation:
    """Test WhaleActionModel Pydantic V2 validation."""

    def test_valid_buy_action(self):
        """Valid buy action should pass validation."""
        model = WhaleActionModel(
            whale_address="8xYkH5J2kXkLmN9pQ3rT4wC6vD7bE8fG1hI2jK3mN4oP",
            mint="So11111111111111111111111111111111111111112",
            action="buy",
            percent_of_position=50.0,
            volume_usd=10000.0,
            timestamp=datetime.now(tz=timezone.utc),
            win_rate_30d=0.65,
        )
        assert model.action == "buy"
        assert model.percent_of_position == 50.0

    def test_valid_sell_action(self):
        """Valid sell action should pass validation."""
        model = WhaleActionModel(
            whale_address="8xYkH5J2kXkLmN9pQ3rT4wC6vD7bE8fG1hI2jK3mN4oP",
            mint="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            action="sell",
            percent_of_position=25.0,
            volume_usd=5000.0,
            timestamp=datetime.now(tz=timezone.utc),
            win_rate_30d=0.72,
        )
        assert model.action == "sell"
        assert model.volume_usd == 5000.0

    def test_invalid_action_rejected(self):
        """Invalid action (not buy/sell) should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            WhaleActionModel(
                whale_address="8xYkH5J2kXkLmN9pQ3rT4wC6vD7bE8fG1hI2jK3mN4oP",
                mint="So11111111111111111111111111111111111111112",
                action="hold",  # Invalid
                percent_of_position=50.0,
                volume_usd=10000.0,
                timestamp=datetime.now(tz=timezone.utc),
                win_rate_30d=0.65,
            )
        assert "action must be" in str(exc_info.value).lower()

    def test_negative_percent_rejected(self):
        """Negative percent_of_position should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            WhaleActionModel(
                whale_address="8xYkH5J2kXkLmN9pQ3rT4wC6vD7bE8fG1hI2jK3mN4oP",
                mint="So11111111111111111111111111111111111111112",
                action="buy",
                percent_of_position=-10.0,  # Invalid
                volume_usd=10000.0,
                timestamp=datetime.now(tz=timezone.utc),
                win_rate_30d=0.65,
            )
        assert "greater than or equal to 0" in str(exc_info.value).lower()

    def test_percent_over_100_rejected(self):
        """percent_of_position > 100 should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            WhaleActionModel(
                whale_address="8xYkH5J2kXkLmN9pQ3rT4wC6vD7bE8fG1hI2jK3mN4oP",
                mint="So11111111111111111111111111111111111111112",
                action="sell",
                percent_of_position=150.0,  # Invalid
                volume_usd=10000.0,
                timestamp=datetime.now(tz=timezone.utc),
                win_rate_30d=0.65,
            )
        assert "less than or equal to 100" in str(exc_info.value).lower()

    def test_negative_volume_rejected(self):
        """Negative volume_usd should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            WhaleActionModel(
                whale_address="8xYkH5J2kXkLmN9pQ3rT4wC6vD7bE8fG1hI2jK3mN4oP",
                mint="So11111111111111111111111111111111111111112",
                action="buy",
                percent_of_position=50.0,
                volume_usd=-500.0,  # Invalid
                timestamp=datetime.now(tz=timezone.utc),
                win_rate_30d=0.65,
            )
        assert "greater than or equal to 0" in str(exc_info.value).lower()

    def test_invalid_win_rate_over_1_rejected(self):
        """win_rate_30d > 1.0 should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            WhaleActionModel(
                whale_address="8xYkH5J2kXkLmN9pQ3rT4wC6vD7bE8fG1hI2jK3mN4oP",
                mint="So11111111111111111111111111111111111111112",
                action="buy",
                percent_of_position=50.0,
                volume_usd=10000.0,
                timestamp=datetime.now(tz=timezone.utc),
                win_rate_30d=1.5,  # Invalid - must be 0.0-1.0
            )
        assert "less than or equal to 1" in str(exc_info.value).lower()

    def test_invalid_win_rate_negative_rejected(self):
        """Negative win_rate_30d should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            WhaleActionModel(
                whale_address="8xYkH5J2kXkLmN9pQ3rT4wC6vD7bE8fG1hI2jK3mN4oP",
                mint="So11111111111111111111111111111111111111112",
                action="buy",
                percent_of_position=50.0,
                volume_usd=10000.0,
                timestamp=datetime.now(tz=timezone.utc),
                win_rate_30d=-0.1,  # Invalid
            )
        assert "greater than or equal to 0" in str(exc_info.value).lower()

    def test_missing_required_field_rejected(self):
        """Missing required fields should raise ValidationError."""
        with pytest.raises(ValidationError):
            WhaleActionModel(
                whale_address="8xYkH5J2kXkLmN9pQ3rT4wC6vD7bE8fG1hI2jK3mN4oP",
                # Missing: mint, action, percent_of_position, etc.
            )

    def test_boundary_values_accepted(self):
        """Boundary values (0, 100, 1.0) should be accepted."""
        # Zero percent
        model_zero = WhaleActionModel(
            whale_address="8xYkH5J2kXkLmN9pQ3rT4wC6vD7bE8fG1hI2jK3mN4oP",
            mint="So11111111111111111111111111111111111111112",
            action="buy",
            percent_of_position=0.0,
            volume_usd=0.0,
            timestamp=datetime.now(tz=timezone.utc),
            win_rate_30d=0.0,
        )
        assert model_zero.percent_of_position == 0.0

        # Max percent
        model_max = WhaleActionModel(
            whale_address="8xYkH5J2kXkLmN9pQ3rT4wC6vD7bE8fG1hI2jK3mN4oP",
            mint="So11111111111111111111111111111111111111112",
            action="sell",
            percent_of_position=100.0,
            volume_usd=1000000.0,
            timestamp=datetime.now(tz=timezone.utc),
            win_rate_30d=1.0,
        )
        assert model_max.percent_of_position == 100.0
        assert model_max.win_rate_30d == 1.0


class TestWhaleWatcherInitialization:
    """Test WhaleWatcher class initialization."""

    def test_watcher_initialization(self):
        """WhaleWatcher should initialize with correct parameters."""
        whales = ["whale1", "whale2"]
        watchlist = ["mint1", "mint2"]

        watcher = WhaleWatcher(
            api_url="http://localhost:8000",
            whales=whales,
            watchlist_mints=watchlist,
            poll_interval=5.0,
        )

        assert watcher.api_url == "http://localhost:8000"
        assert len(watcher.whales) == 2
        assert len(watcher.watchlist) == 2
        assert watcher.poll_interval == 5.0
        assert watcher._running is False

    def test_watcher_strips_trailing_slash(self):
        """WhaleWatcher should strip trailing slash from API URL."""
        watcher = WhaleWatcher(
            api_url="http://localhost:8000/",
            whales=["whale1"],
        )
        assert watcher.api_url == "http://localhost:8000"

    def test_watcher_empty_whales(self):
        """WhaleWatcher should handle empty whale list."""
        watcher = WhaleWatcher(
            api_url="http://localhost:8000",
            whales=None,
        )
        assert watcher.whales == []

    def test_watcher_stop(self):
        """WhaleWatcher.stop() should set _running to False."""
        watcher = WhaleWatcher(
            api_url="http://localhost:8000",
            whales=["whale1"],
        )
        watcher._running = True
        watcher.stop()
        assert watcher._running is False


class TestPayloadFiltering:
    """Test that invalid API payloads are properly filtered."""

    def test_filter_invalid_action_in_raw_payload(self):
        """Simulated raw payloads with invalid actions should fail validation."""
        raw_payloads = [
            {"action": "hold", "mint": "xyz", "volume_usd": 100},
            {"action": "swap", "mint": "abc", "volume_usd": 200},
            {"action": "", "mint": "def", "volume_usd": 300},
        ]

        valid_count = 0
        for payload in raw_payloads:
            try:
                WhaleActionModel(
                    whale_address="test",
                    mint=payload.get("mint", ""),
                    action=payload.get("action", ""),
                    percent_of_position=50.0,
                    volume_usd=payload.get("volume_usd", 0),
                    timestamp=datetime.now(tz=timezone.utc),
                    win_rate_30d=0.5,
                )
                valid_count += 1
            except ValidationError:
                pass  # Expected for invalid payloads

        assert valid_count == 0, "All invalid payloads should have been rejected"

    def test_filter_out_of_range_values(self):
        """Payloads with out-of-range values should fail validation."""
        invalid_configs = [
            {"percent_of_position": -1, "volume_usd": 100, "win_rate_30d": 0.5},
            {"percent_of_position": 101, "volume_usd": 100, "win_rate_30d": 0.5},
            {"percent_of_position": 50, "volume_usd": -100, "win_rate_30d": 0.5},
            {"percent_of_position": 50, "volume_usd": 100, "win_rate_30d": 1.5},
            {"percent_of_position": 50, "volume_usd": 100, "win_rate_30d": -0.1},
        ]

        for config in invalid_configs:
            with pytest.raises(ValidationError):
                WhaleActionModel(
                    whale_address="test",
                    mint="testmint",
                    action="buy",
                    percent_of_position=config["percent_of_position"],
                    volume_usd=config["volume_usd"],
                    timestamp=datetime.now(tz=timezone.utc),
                    win_rate_30d=config["win_rate_30d"],
                )
