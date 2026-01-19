import asyncio
import time
import pytest

from src.brain import MarketBrain


class DummyResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {"result": {"value": {"blockhash": "BH"}}}

    def json(self):
        return self._body


class DummyAsyncClient:
    def __init__(self, timeout=None):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, json=None):
        # Simulate different latencies based on hostname fragment
        # r1 -> 200ms, r2 -> 50ms, r3 -> 500ms
        if 'r1' in url:
            await asyncio.sleep(0.200)
        elif 'r2' in url:
            await asyncio.sleep(0.050)
        elif 'r3' in url:
            await asyncio.sleep(0.500)
        else:
            await asyncio.sleep(0.100)
        return DummyResponse()


@pytest.mark.asyncio
async def test_rpc_rotation_picks_lowest_rtt(monkeypatch):
    """Given three RPCs with latencies [200ms,50ms,500ms], the probe should pick the 50ms node."""
    brain = MarketBrain(rpc='http://initial-rpc')

    # Provide a predictable pool with three endpoints
    monkeypatch.setattr(brain, '_load_rpc_pool', lambda: ['http://rpc-r1.example', 'http://rpc-r2.example', 'http://rpc-r3.example'])

    # Patch httpx.AsyncClient used inside _rpc_health_probe_once/_rpc_health_monitor
    import httpx
    monkeypatch.setattr(httpx, 'AsyncClient', DummyAsyncClient)

    called = {}

    async def fake_replace(url, timeout_s=None):
        # record call
        called['url'] = url

    monkeypatch.setattr(brain, '_replace_active_client', fake_replace)

    # Ensure no rate-limited blacklist to interfere
    brain._rate_limited_blacklist = {}

    # Run the single-shot probe
    await brain._rpc_health_probe_once()

    # The chosen best should be rpc-r2 (contains 'r2')
    assert 'url' in called, "_replace_active_client was not called"
    assert 'r2' in called['url'] or 'rpc-r2' in called['url']
