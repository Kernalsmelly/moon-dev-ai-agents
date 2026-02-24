import pytest
import asyncio

from types import SimpleNamespace

import src.config as config
from src.brain import MarketBrain


@pytest.mark.asyncio
async def test_dynamic_slippage_high_vhi(monkeypatch):
    mb = MarketBrain(start_monitor=False)

    class FakeVHI:
        def score(self, a, b):
            return 90  # 0.9

    # patch VolumeHeatIndex to return our fake
    monkeypatch.setattr('src.strategies.volume_heat.VolumeHeatIndex', lambda: FakeVHI())
    # ensure get_virtual_volumes exists and returns zeros
    monkeypatch.setattr(MarketBrain, 'get_virtual_volumes', lambda self: (0, 0))

    base = 100
    sl = await mb.get_dynamic_slippage(base)
    assert sl == min(int(getattr(config, 'MAX_SLIPPAGE_BPS', 500)), base * 2)


@pytest.mark.asyncio
async def test_moonbag_transitions_on_price_double(monkeypatch):
    mb = MarketBrain(start_monitor=False)
    mint = 'FAKE_MINT'
    trade = {
        'mint': mint,
        'entry_price_usd': 1.0,
        'amount_sol': 1.0,
        'status': 'open',
    }

    # mock price doubling
    async def fake_price(m):
        return 2.0

    monkeypatch.setattr(MarketBrain, '_call_birdeye_price', lambda self, m: asyncio.sleep(0, result=2.0))

    # mock _execute_exit_swap to return True
    monkeypatch.setattr(MarketBrain, '_execute_exit_swap', lambda self, mint, amount, exit_type, live=False, entry_price_usd=None: asyncio.sleep(0, result=True))

    ok = await mb._maybe_execute_moonbag(trade)
    assert ok is True
    assert trade.get('is_moon_bag') is True
    # amount_sol should have been halved (MOON_BAG_PERCENT default 0.5)
    assert abs(trade.get('amount_sol') - 0.5) < 1e-9


@pytest.mark.asyncio
async def test_stop_loss_not_moved_below_entry(monkeypatch):
    mb = MarketBrain(start_monitor=False)
    mint = 'FAKE2'
    trade = {
        'mint': mint,
        'entry_price_usd': 1.0,
        'amount_sol': 1.0,
        'status': 'open',
        'is_moon_bag': True,
        'stop_loss_price': 1.0,
    }

    # Simulate a trailing stop update that would attempt to reduce the stop
    # below entry by forcing a high-water-mark and then a low current price.
    # Our update_trailing_stops should not lower 'stop_loss_price' below entry_price.

    # mock birdeye price: current price low
    async def low_price(m):
        return 0.5

    monkeypatch.setattr(MarketBrain, '_call_birdeye_price', lambda self, m: asyncio.sleep(0, result=0.5))

    # ensure trade is present
    mb.simulated_trades = [trade]

    marked = await mb.update_trailing_stops()
    # After running trailing stops, stop_loss_price should remain >= entry_price
    assert trade.get('stop_loss_price') >= trade.get('entry_price_usd')


def test_volatility_sizing():
    """Verify get_smart_position_size responds to VHI levels.

    - VHI 0.9 -> reduced size (BASE * SIZE_REDUCTION_FACTOR -> 0.5 by default)
    - VHI 0.2 -> boosted size (BASE * SIZE_BOOST_FACTOR -> 1.25 by default)
    """
    mb = MarketBrain(start_monitor=False)
    # high volatility
    size_high = mb.get_smart_position_size(0.9)
    assert abs(size_high - getattr(config, 'BASE_POSITION_SIZE_SOL', 1.0) * getattr(config, 'SIZE_REDUCTION_FACTOR', 0.5)) < 1e-9

    # low volatility
    size_low = mb.get_smart_position_size(0.2)
    assert abs(size_low - getattr(config, 'BASE_POSITION_SIZE_SOL', 1.0) * getattr(config, 'SIZE_BOOST_FACTOR', 1.25)) < 1e-9
