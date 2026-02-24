from __future__ import annotations

import time

import pytest

import src.meme_config as meme_config
from src.meme_exit_manager import MemeExitManager, PositionState


def _new_position() -> PositionState:
    return PositionState(
        mint="TEST",
        symbol="TEST",
        entry_price=1.0,
        entry_time=time.time(),
        amount_tokens=100.0,
        amount_usd=100.0,
    )


def test_tp_fractions_as_original_preserves_moonbag(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(meme_config, "TP_FRACTIONS_AS_ORIGINAL", True, raising=False)
    monkeypatch.setattr(meme_config, "MOON_BAG_ENABLED", True, raising=False)
    monkeypatch.setattr(meme_config, "MOON_BAG_FRACTION", 0.10, raising=False)

    manager = MemeExitManager()
    pos = _new_position()
    prices = [1.35, 1.60, 2.00, 2.50, 3.50]
    expected_sell_fracs = [0.25, 1 / 3, 0.40, 1 / 3, 0.50]

    for px, expected in zip(prices, expected_sell_fracs):
        result = manager.check_exit(pos, px)
        assert result.should_exit
        assert result.sell_fraction == pytest.approx(expected, abs=1e-3)
        sold_tokens = pos.amount_tokens * result.sell_fraction
        pos.amount_tokens -= sold_tokens
        pos.amount_usd -= pos.amount_usd * result.sell_fraction

    summary = manager.get_tp_stage_summary(pos)
    assert summary["cumulative_sold"] == pytest.approx(0.90, abs=1e-6)
    assert pos.amount_tokens == pytest.approx(10.0, abs=1e-6)
    assert pos.is_moon_bag is True


def test_tp_fractions_legacy_mode_uses_remaining_fraction(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(meme_config, "TP_FRACTIONS_AS_ORIGINAL", False, raising=False)
    monkeypatch.setattr(meme_config, "MOON_BAG_ENABLED", True, raising=False)
    monkeypatch.setattr(meme_config, "MOON_BAG_FRACTION", 0.10, raising=False)

    manager = MemeExitManager()
    pos = _new_position()

    first = manager.check_exit(pos, 1.35)
    assert first.should_exit
    assert first.sell_fraction == pytest.approx(0.25, abs=1e-6)
    pos.amount_tokens -= pos.amount_tokens * first.sell_fraction
    pos.amount_usd -= pos.amount_usd * first.sell_fraction

    second = manager.check_exit(pos, 1.60)
    assert second.should_exit
    # Legacy mode keeps configured stage fraction against remaining size.
    assert second.sell_fraction == pytest.approx(0.25, abs=1e-6)


def test_get_next_tp_target_starts_at_tp0():
    manager = MemeExitManager()
    pos = _new_position()

    first_target = manager.get_next_tp_target(pos)
    assert first_target is not None
    assert first_target[0] == pytest.approx(pos.entry_price * (1 + manager.tp_tiers[0][0]), abs=1e-12)
    assert first_target[1] == pytest.approx(manager.tp_tiers[0][1], abs=1e-12)

    pos.tp0_hit = True
    second_target = manager.get_next_tp_target(pos)
    assert second_target is not None
    assert second_target[0] == pytest.approx(pos.entry_price * (1 + manager.tp_tiers[1][0]), abs=1e-12)
    assert second_target[1] == pytest.approx(manager.tp_tiers[1][1], abs=1e-12)
