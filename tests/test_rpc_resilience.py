import asyncio
import time
import pytest

import httpx

from src.brain import MarketBrain


class DummyClient:
    def __init__(self, base_url: str, status_code: int = 429, headers: dict | None = None):
        self.base_url = httpx.URL(base_url)
        self._status = status_code
        self._headers = headers or {}

    async def post(self, path: str, json: dict | None = None):
        # construct a Request so raise_for_status produces HTTPStatusError
        req = httpx.Request('POST', str(self.base_url))
        return httpx.Response(self._status, headers=self._headers, request=req)


@pytest.mark.asyncio
async def test_rate_limit_blacklist_and_rotation(monkeypatch):
    brain = MarketBrain(rpc='https://rpc.test')
    # replace active client with dummy that returns 429 and Retry-After=2
    brain.active_client = DummyClient('https://rpc.test', status_code=429, headers={'Retry-After': '2'})

    called = {'probe': False}

    async def fake_probe():
        called['probe'] = True

    monkeypatch.setattr(brain, '_rpc_health_probe_once', fake_probe)

    with pytest.raises(httpx.HTTPStatusError):
        await brain._call_rpc('getHealth', [])

    await asyncio.sleep(0)

    bl = brain._rate_limited_blacklist
    assert 'https://rpc.test' in bl
    assert bl['https://rpc.test'] > time.time()
    assert called['probe'] is True


@pytest.mark.asyncio
async def test_server_error_triggers_probe(monkeypatch):
    brain = MarketBrain(rpc='https://rpc.server')
    brain.active_client = DummyClient('https://rpc.server', status_code=500)

    called = {'probe': False}

    async def fake_probe():
        called['probe'] = True

    monkeypatch.setattr(brain, '_rpc_health_probe_once', fake_probe)

    with pytest.raises(httpx.HTTPStatusError):
        await brain._call_rpc('getLatestBlockhash', [])

    await asyncio.sleep(0)
    assert called['probe'] is True
