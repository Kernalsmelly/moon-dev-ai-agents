import asyncio
import time
import csv
import json
import pytest

import src.config as config
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
        # r1 -> 20ms, r2 -> 5ms (fastest), r3 -> 50ms
        if 'r1' in url:
            await asyncio.sleep(0.020)
        elif 'r2' in url:
            await asyncio.sleep(0.005)
        elif 'r3' in url:
            await asyncio.sleep(0.050)
        else:
            await asyncio.sleep(0.010)
        return DummyResponse()


@pytest.mark.asyncio
async def test_monitor_loop_emits_rpc_rotation(tmp_path, monkeypatch):
    # Prepare a MarketBrain and point telemetry to a temp file
    ev_file = tmp_path / 'exec_events.csv'
    monkeypatch.setattr(config, 'EXECUTION_LOG_PATH', str(ev_file))

    brain = MarketBrain(rpc='http://initial')

    # Provide a predictable pool with three endpoints
    monkeypatch.setattr(brain, '_load_rpc_pool', lambda: ['http://rpc-r1.example', 'http://rpc-r2.example', 'http://rpc-r3.example'])

    # Patch httpx.AsyncClient used inside the monitor
    import httpx
    monkeypatch.setattr(httpx, 'AsyncClient', DummyAsyncClient)

    # Run monitor for two cycles (short poll interval)
    task = asyncio.create_task(brain._rpc_health_monitor(poll_interval=0.05))
    # allow two cycles (a bit of slack)
    await asyncio.sleep(0.20)

    # cancel and wait
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        # expected when cancelling the background monitor
        pass
    except Exception:
        pass

    # Read telemetry log and assert we have rpc_rotation with new_latency_ms
    assert ev_file.exists(), "Telemetry file not created"
    found = False
    with open(ev_file, 'r', encoding='utf-8') as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            try:
                data = json.loads(row.get('data_json') or '{}')
            except Exception:
                data = {}
            if row.get('event_type') == 'rpc_rotation' and ('new_latency_ms' in data):
                found = True
                break

    assert found, "rpc_rotation event with new_latency_ms not found in telemetry"
