import asyncio
import os
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock

import pytest

import src.brain as brain_mod


@pytest.mark.asyncio
async def test_heartbeat_time_warp(tmp_path, monkeypatch):
    # ensure heartbeat interval is 4 hours
    monkeypatch.setenv('HEARTBEAT_INTERVAL_SECONDS', str(4 * 3600))
    # use a temp file for execution events so tests remain hermetic
    ev_csv = str(tmp_path / 'execution_events.csv')
    monkeypatch.setenv('EXECUTION_LOG_PATH', ev_csv)

    # create 5 mock events within the last 4 hours
    now = datetime.now(timezone.utc)
    rows = []
    header = ['ts', 'mint', 'event_type', 'data_json']
    for i in range(5):
        ts = (now - timedelta(minutes=10 * i)).isoformat()
        mint = f"Mint{i}"
        ev_type = 'chunk_executed'
        payload = {
            'unitsConsumed': 1000 + i * 10,
            'estimated_impact_pct': 1.0 + i * 0.5,
            'attempts': 1 + i,
            'quote_latency_ms': 50 + i * 5,
            'birdeye_latency_ms': 30 + i * 2,
        }
        rows.append((ts, mint, ev_type, json.dumps(payload)))

    # write CSV
    import csv
    with open(ev_csv, 'w', newline='', encoding='utf-8') as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        for r in rows:
            writer.writerow(r)

    # create MarketBrain and patch its _send_telegram_status
    mb = brain_mod.MarketBrain()
    mock_send = AsyncMock()
    mb._send_telegram_status = mock_send

    # patch the asyncio.sleep used inside brain to fast-forward
    sleep_calls = []

    async def fake_sleep(seconds):
        # record call and return immediately
        sleep_calls.append(seconds)
        await asyncio.sleep(0)  # yield to event loop
        return None

    monkeypatch.setattr(brain_mod.asyncio, 'sleep', fake_sleep)

    # run the heartbeat loop in background and wait for it to call our mock send
    task = asyncio.create_task(mb._heartbeat_loop())

    try:
        # wait up to 2 seconds for the mock_send to be awaited
        for _ in range(200):
            if mock_send.await_count > 0 or mock_send.called:
                break
            await asyncio.sleep(0.01)

        assert mock_send.await_count > 0 or mock_send.called, 'Telegram send was not called by heartbeat'

        # inspect the message passed to telegram
        call_args = mock_send.call_args
        assert call_args is not None
        message = call_args[0][0]
        assert 'Volatility Correlation:' in message

        # extract last birdeye latency line and assert non-zero
        found = None
        for line in message.splitlines():
            if 'Last Birdeye Latency' in line:
                found = line
                break
        assert found is not None, 'Heartbeat message missing birdeye latency line'
        # parse trailing value
        val = found.split(':')[-1].strip()
        assert val.lower() != 'n/a'
        # allow numeric with possible rounding
        try:
            fv = float(val)
        except Exception:
            # may contain unit text; strip non-digits
            digits = ''.join([c for c in val if c.isdigit() or c == '.' or c == '-'])
            fv = float(digits) if digits else 0.0
        assert fv > 0.0
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_heartbeat_handles_corrupt_csv(tmp_path, monkeypatch):
    # short heartbeat interval for test
    monkeypatch.setenv('HEARTBEAT_INTERVAL_SECONDS', str(4 * 3600))

    # point execution log to a tmp file and write binary garbage
    ev_csv = tmp_path / 'bad_execution_events.csv'
    with open(ev_csv, 'wb') as fh:
        fh.write(b"\x00\xff\x00\x01garbage\x02\x03")
    monkeypatch.setenv('EXECUTION_LOG_PATH', str(ev_csv))

    # capture console.print calls
    printed = []

    def fake_print(*args, **kwargs):
        try:
            printed.append(args[0])
        except Exception:
            printed.append(str(args))

    monkeypatch.setattr(brain_mod, 'console', brain_mod.console)
    monkeypatch.setattr(brain_mod.console, 'print', fake_print)

    mb = brain_mod.MarketBrain()
    # patch send to avoid external calls
    mb._send_telegram_status = AsyncMock()

    # patch sleep to fast-forward
    async def fake_sleep(seconds):
        await asyncio.sleep(0)
        return None

    monkeypatch.setattr(brain_mod.asyncio, 'sleep', fake_sleep)

    # run heartbeat loop and allow one iteration
    task = asyncio.create_task(mb._heartbeat_loop())
    try:
        # wait briefly for heartbeat to attempt reading and log warning
        for _ in range(100):
            if printed:
                break
            await asyncio.sleep(0.01)

        # ensure we captured a warning printed in yellow panel
        assert any('Heartbeat CSV read warning' in str(p) or 'warning' in str(p).lower() for p in printed), f"Expected heartbeat warning in console prints, got: {printed}"
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
