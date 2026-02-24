import asyncio
import pytest
from datetime import datetime, timedelta, timezone

import src.config as config
from src.brain import MarketBrain


@pytest.mark.asyncio
async def test_get_session_stats_basic(monkeypatch):
    mb = MarketBrain(rpc='http://localhost')
    # clear any prior simulated trades
    mb.simulated_trades.clear()

    now = datetime.now(timezone.utc)

    # simulated winning trade: entry 1.0 USD, current price 3.0 -> +200% -> pnl_sol = 2 * amount_sol
    mb.simulated_trades.append({
        'mint': 'M1',
        'entry_price_usd': 1.0,
        'amount_sol': 1.0,
        'timestamp': (now - timedelta(minutes=10)).isoformat() + 'Z',
        'status': 'simulated',
    })

    # simulated losing trade
    mb.simulated_trades.append({
        'mint': 'M2',
        'entry_price_usd': 2.0,
        'amount_sol': 1.0,
        'timestamp': (now - timedelta(minutes=20)).isoformat() + 'Z',
        'status': 'simulated',
    })

    # skipped signal
    mb.simulated_trades.append({
        'mint': 'M3',
        'entry_price_usd': None,
        'amount_sol': 0.5,
        'timestamp': (now - timedelta(minutes=5)).isoformat() + 'Z',
        'status': 'skipped',
    })

    async def fake_birdeye_price(mint=None):
        if mint == 'M1':
            return 3.0
        if mint == 'M2':
            return 1.0
        return None

    monkeypatch.setattr(mb, '_call_birdeye_price', fake_birdeye_price)

    stats = await mb.get_session_stats('ny_open')
    assert isinstance(stats, dict)
    assert stats.get('count') == 3
    # win rate should be 50% (1 win out of 2 simulated)
    assert pytest.approx(stats.get('win_rate', 0.0), rel=1e-3) == 0.5
    # alpha_missed counts skipped entries
    assert stats.get('alpha_missed') == 1
    # top performer should be M1
    top = stats.get('top_performer')
    assert top and top.get('mint') == 'M1'


@pytest.mark.asyncio
async def test_enforce_daily_circuit_breaker(monkeypatch):
    mb = MarketBrain(rpc='http://localhost')
    # Ensure live flag is True so we can observe it being turned off
    config.LIVE_TRADING_ENABLED = True

    async def fake_stats(name):
        return {'net_sol': -2.0}

    monkeypatch.setattr(mb, 'get_session_stats', fake_stats)

    # Do not patch _send_telegram_status here; conftest provides an AsyncMock
    # for MarketBrain._send_discord_alert which `_send_telegram_status` delegates to.
    tripped = await mb.enforce_daily_circuit_breaker(threshold_sol=-1.5)
    assert tripped is True
    assert config.LIVE_TRADING_ENABLED is False

    # Ensure the autouse Discord stub was awaited
    from src.brain import MarketBrain as MBClass
    discord_mock = getattr(MBClass, '_send_discord_alert', None)
    assert discord_mock is not None
    # AsyncMock stores await_count when awaited
    assert getattr(discord_mock, 'await_count', 0) >= 1
