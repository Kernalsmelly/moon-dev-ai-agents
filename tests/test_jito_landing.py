import asyncio
import pytest

from infrastructure.jito_manager import JitoManager


@pytest.mark.asyncio
async def test_confirm_bundle_landing_landed_and_failed(monkeypatch):
    events = []
    def telemetry(ev, data):
        events.append((ev, data))

    jm = JitoManager(rpc='https://api.testnet.solana.com', telemetry_fn=telemetry)

    class FakeSearcher:
        def __init__(self, seq):
            self.seq = seq
            self.calls = 0
        def get_bundle_statuses(self, ids):
            # return different statuses based on call count
            self.calls += 1
            if self.seq == 'landed':
                return [{ 'bundle_id': ids[0], 'status': 'landed' }]
            if self.seq == 'failed':
                return [{ 'bundle_id': ids[0], 'status': 'failed' }]
            # default: pending then landed
            if self.calls < 2:
                return [{ 'bundle_id': ids[0], 'status': 'pending' }]
            return [{ 'bundle_id': ids[0], 'status': 'landed' }]

    # case 1: landed immediately
    jm.searcher = FakeSearcher('landed')
    ok = await jm.confirm_bundle_landing('B1', max_slots=2, slot_time_s=0.01)
    assert ok is True

    # case 2: failed
    jm.searcher = FakeSearcher('failed')
    ok2 = await jm.confirm_bundle_landing('B2', max_slots=2, slot_time_s=0.01)
    assert ok2 is False
    assert any(ev == 'BUNDLE_FAILED' for ev, _ in events)

    # case 3: pending then landed
    jm.searcher = FakeSearcher('pending_then_landed')
    ok3 = await jm.confirm_bundle_landing('B3', max_slots=3, slot_time_s=0.01)
    assert ok3 is True
