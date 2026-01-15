import asyncio
import time

import pytest

import httpx

from src.brain import MarketBrain


@pytest.mark.asyncio
async def test_flash_cache_deduplication(monkeypatch):
    """Calling _get_birdeye_volume 5x in a row should only invoke the HTTP client once
    because the 60s flash cache returns cached results for subsequent calls.
    """
    brain = MarketBrain(rpc='https://api.devnet.solana.com', whales=[])
    mint = 'DummyMintFlashCache11111111111111111111111'

    call_count = {'n': 0}

    class DummyResp:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            return None

        def json(self):
            return self._data

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, params=None):
            # simulate a single successful call
            call_count['n'] += 1
            await asyncio.sleep(0)  # yield
            return DummyResp({'data': {'volume_24h_usd': 123456.0}})

    monkeypatch.setattr('httpx.AsyncClient', DummyClient)

    # Call the method multiple times in quick succession
    results = []
    for _ in range(5):
        v = await brain._get_birdeye_volume(mint)
        results.append(v)

    # All results should match the returned volume
    assert all(r == 123456.0 for r in results)
    # The underlying HTTP client should have been triggered exactly once
    assert call_count['n'] == 1
    # And the rate-limiter should have recorded a single reservation
    assert isinstance(brain._birdeye_ts, list)
    assert len(brain._birdeye_ts) == 1


@pytest.mark.asyncio
async def test_strict_gate_skip(monkeypatch):
    """If the Birdeye endpoint fails repeatedly, the method should return None
    and nothing should be cached (strict gating behavior).
    """
    brain = MarketBrain(rpc='https://api.devnet.solana.com', whales=[])
    mint = 'DummyMintFail111111111111111111111111111111'

    class FailingClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, params=None):
            # always fail to simulate 404 / network error
            await asyncio.sleep(0)
            raise Exception('Simulated Birdeye failure')

    monkeypatch.setattr('httpx.AsyncClient', FailingClient)

    v = await brain._get_birdeye_volume(mint)
    assert v is None
    # ensure nothing bad was cached
    assert mint not in brain._volume_cache
