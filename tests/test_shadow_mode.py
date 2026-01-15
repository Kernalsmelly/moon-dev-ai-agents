import json
import csv
import pytest
from unittest.mock import Mock
from unittest.mock import AsyncMock

from src.brain import MarketBrain
import src.trade_executor as te

WSOL_MINT_LITERAL = "So11111111111111111111111111111111111111112"


@pytest.mark.asyncio
async def test_shadow_mode_prevents_real_swaps(tmp_path, mock_jupiter, mock_versioned_tx, mock_async_client, monkeypatch, fast_sleep):
    """Ensure that when SHADOW_MODE is active, no on-chain send is performed but telemetry is recorded."""
    # Force live exit env var so the code would normally attempt to send
    monkeypatch.setenv('ENABLE_LIVE_EXITS', '1')
    # Ensure SHADOW_MODE is enabled in config
    monkeypatch.setattr('src.config.SHADOW_MODE', True)

    brain = MarketBrain(rpc='http://localhost')
    mint = 'ShadowMint11111111111111111111111111111111'

    # simple pricing: token price 1 SOL, large pool liquidity to avoid chunking
    async def price_side(maddr):
        if maddr == mint:
            return (1.0, 1_000_000.0)
        if maddr == WSOL_MINT_LITERAL:
            return (100.0, None)
        return (None, None)

    monkeypatch.setattr(brain, '_get_birdeye_price', AsyncMock(side_effect=price_side))
    monkeypatch.setattr(brain, '_get_token_decimals', AsyncMock(return_value=6))

    # simulate returns unitsConsumed
    sim_resp = Mock(value={'result': {'value': {'unitsConsumed': 55555}}})
    mock_async_client['sim_mock'].return_value = sim_resp

    # Redirect logging to tmp_path/data
    data_dir = tmp_path / 'data'
    data_dir.mkdir()

    def patched_log_execution_event(self, mint_arg, event_type, data):
        ev_csv = data_dir / 'execution_events.csv'
        header_needed = not ev_csv.exists()
        ts = 'ts'
        with open(ev_csv, 'a', newline='') as fh:
            writer = csv.writer(fh)
            if header_needed:
                writer.writerow(['ts', 'mint', 'event_type', 'data_json'])
            writer.writerow([ts, mint_arg, event_type, json.dumps(data)])

    monkeypatch.setattr(brain, '_log_execution_event', patched_log_execution_event)

    # Execute exit in live mode; SHADOW_MODE should prevent actual send but return True
    res = await brain._execute_exit_swap(mint, 0.1, 'TEST', live=True)
    assert res is True

    # Ensure send_raw_transaction was not called
    assert mock_async_client['send_mock'].call_count == 0

    # Ensure telemetry file exists and contains the exit_simulated event with unitsConsumed and shadow_mode
    ev_csv = data_dir / 'execution_events.csv'
    assert ev_csv.exists()
    found = False
    with open(ev_csv, 'r', newline='') as fh:
        reader = csv.DictReader(fh)
        for r in reader:
            if r.get('mint') == mint and r.get('event_type') == 'exit_simulated':
                dat = json.loads(r.get('data_json') or '{}')
                assert int(dat.get('unitsConsumed') or 0) == 55555
                assert dat.get('shadow_mode') is True
                found = True
    assert found, 'exit_simulated telemetry not found in execution_events.csv'
