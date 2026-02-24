import asyncio
import pytest

from src.strategies.volume_heat import VolumeHeatIndex
from src.strategies.graduation_sniper import GraduationSniper
from src.brain import MarketBrain
from datetime import datetime, timedelta, timezone


def test_perfect_vhi_score():
    v = VolumeHeatIndex(max_ratio=5.0)
    # vol_1m is very large compared to 5m average
    vol_1m = 500.0
    vol_5m = 100.0
    score = v.score(vol_1m, vol_5m)
    assert isinstance(score, int)
    assert score == 100


@pytest.mark.asyncio
async def test_successful_graduation_snipe():
    s = GraduationSniper(max_top_holder_concentration=0.25)
    event = {'mint': 'FAKE', 'is_lp_burned': True, 'top_holder_concentration': 0.1}
    action, confidence, details = await s.handle_migration_event(event)
    assert action is True
    assert confidence > 50.0
    assert details.get('mint') == 'FAKE'


@pytest.mark.asyncio
async def test_get_simulated_pnl_double_gain(monkeypatch):
    mb = MarketBrain(rpc='http://localhost')

    # single simulated trade: entry_price_usd=1.0, amount_sol=1.0
    mb.simulated_trades.append({'mint': 'MINTX', 'entry_price_usd': 1.0, 'amount_sol': 1.0, 'status': 'simulated'})

    async def fake_birdeye_price(mint=None):
        # return current price USD == 3.0 (2x increase over 1.0 entry)
        return 3.0

    monkeypatch.setattr(mb, '_call_birdeye_price', fake_birdeye_price)

    res = await mb.get_simulated_pnl()
    # net_sol should be approx 2.0 (pct = (3-1)/1 = 2.0 -> pnl_sol = 2.0 * amount_sol)
    assert pytest.approx(res.get('net_sol', 0.0), rel=1e-3) == 2.0
    assert res.get('count') == 1


def test_virtual_bucket_aggregation():
    """Populate tick history across a 5-minute window and verify
    get_virtual_volumes returns correct 1m and 5m sums.
    """
    mb = MarketBrain(rpc='http://localhost')
    # clear any prior ticks
    mb.tick_history.clear()

    now = datetime.now(timezone.utc)
    # ticks: one within 30s, one at 90s, one at 200s, one at 400s (outside 5m)
    mb.add_tick(10.0, ts=now - timedelta(seconds=30))   # in 1m and 5m
    mb.add_tick(20.0, ts=now - timedelta(seconds=90))   # in 5m only
    mb.add_tick(30.0, ts=now - timedelta(seconds=200))  # in 5m only
    mb.add_tick(40.0, ts=now - timedelta(seconds=400))  # outside 5m

    vol_1m, vol_5m = mb.get_virtual_volumes()
    assert vol_1m == pytest.approx(10.0)
    assert vol_5m == pytest.approx(10.0 + 20.0 + 30.0)
