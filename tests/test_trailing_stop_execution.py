import asyncio
import pytest

from src.brain import MarketBrain


@pytest.mark.asyncio
async def test_trailing_stop_invokes_auto_exit(monkeypatch):
    # create brain without starting monitors automatically
    mb = MarketBrain(start_monitor=False)

    calls = []

    async def fake_auto_exit(self, tr):
        calls.append(tr)
        return True

    # Patch at the class level so the running loop picks up the replacement
    monkeypatch.setattr(type(mb), 'auto_exit_trade', fake_auto_exit, raising=False)

    # update_trailing_stops should return the marked trade only once
    state = {'called': False}

    async def fake_update_trailing_stops(self):
        if not state['called']:
            state['called'] = True
            return [{'mint': 'MINTX', 'amount_sol': 1.0, 'entry_price_usd': 1.0, 'tx_sig': None, 'marked_for_exit': True}]
        return []

    # Patch update_trailing_stops at the class level as well
    monkeypatch.setattr(type(mb), 'update_trailing_stops', fake_update_trailing_stops, raising=False)

    # use a short interval so the loop runs quickly
    monkeypatch.setenv('TRAILING_STOP_INTERVAL', '0.05')

    task = asyncio.create_task(mb._trailing_stop_loop())
    try:
        # let the loop iterate a few times
        await asyncio.sleep(0.2)
    finally:
        task.cancel()
        try:
            await task
        except Exception:
            pass

    assert len(calls) == 1
