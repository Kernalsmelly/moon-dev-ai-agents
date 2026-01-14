import json
import csv
from unittest.mock import Mock
import base64
import pytest
from unittest.mock import AsyncMock

from src.brain import MarketBrain
import src.trade_executor as te

WSOL_MINT_LITERAL = "So11111111111111111111111111111111111111112"


@pytest.mark.asyncio
async def test_telemetry_integration_two_chunks(tmp_path, mock_jupiter, mock_versioned_tx, mock_async_client, monkeypatch, fast_sleep):
    """End-to-end telemetry smoke test: ensure execution_events.csv and alpha_journal.csv
    are populated with chunk telemetry including unitsConsumed per chunk and final fill ratio.
    """
    brain = MarketBrain(rpc='http://localhost')
    mint = 'TelemetryMint11111111111111111111111111111111'

    token_price_sol = 1.0
    # choose pool liquidity so 2 chunks expected
    pool_liquidity_usd = 33.3333333
    sol_price_usd = 100.0

    async def price_side(maddr):
        if maddr == mint:
            return (token_price_sol, pool_liquidity_usd)
        if maddr == WSOL_MINT_LITERAL:
            return (sol_price_usd, None)
        return (None, None)

    monkeypatch.setattr(brain, '_get_birdeye_price', AsyncMock(side_effect=price_side))
    monkeypatch.setattr(brain, '_get_token_decimals', AsyncMock(return_value=6))

    # Prepare simulate responses with unitsConsumed values we want to record
    sim1 = Mock(value={'result': {'value': {'unitsConsumed': 85000}}})
    sim2 = Mock(value={'result': {'value': {'unitsConsumed': 92000}}})
    mock_async_client['sim_mock'].side_effect = [sim1, sim2]

    # Redirect logging to tmp_path/data by monkeypatching methods to write there
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

    async def patched_log_exit(self, mint_arg, exit_type, amount_sol, price, success=False):
        # read last chunking_completed to pick up fill_ratio
        ev_csv = data_dir / 'execution_events.csv'
        fill_ratio = ''
        if ev_csv.exists():
            with open(ev_csv, 'r', newline='') as fh:
                reader = csv.DictReader(fh)
                rows = list(reader)
                for r in reversed(rows):
                    if r.get('event_type') == 'chunking_completed' and r.get('mint') == mint_arg:
                        dat = json.loads(r.get('data_json') or '{}')
                        fr = dat.get('fill_ratio')
                        if isinstance(fr, str) and '/' in fr:
                            # convert "2/2" -> "1.0"
                            try:
                                num, den = fr.split('/')
                                fill_ratio = f"{float(num)/float(den):.1f}"
                            except Exception:
                                fill_ratio = fr
                        else:
                            fill_ratio = fr
                        break

        alpha_csv = data_dir / 'alpha_journal.csv'
        header_needed = not alpha_csv.exists()
        with open(alpha_csv, 'a', newline='') as fh:
            writer = csv.writer(fh)
            if header_needed:
                writer.writerow([
                    'ts', 'mint', 'name', 'volume_pct', 'expected_out_raw', 'expected_out_sol',
                    'input_amount_sol', 'unitsConsumed', 'balance_lamports', 'success', 'alpha_score', 'whale_multiplier', 'exit_type', 'fill_ratio'
                ])
            writer.writerow(['ts', mint_arg, '', '', '', price, amount_sol, '', '', success, '', '', exit_type, fill_ratio])

    monkeypatch.setattr(brain, '_log_execution_event', patched_log_execution_event)
    monkeypatch.setattr(brain, '_log_exit', patched_log_exit)

    # run the exit; should execute two chunks and log unitsConsumed values
    res = await brain._execute_exit_swap(mint, 0.1, 'TP2', live=False)
    assert res is True

    # Read execution_events.csv and assert chunk_executed entries contain unitsConsumed
    ev_csv = data_dir / 'execution_events.csv'
    assert ev_csv.exists()
    units = []
    chunking_fill_ratio = None
    with open(ev_csv, 'r', newline='') as fh:
        reader = csv.DictReader(fh)
        for r in reader:
            dat = json.loads(r.get('data_json') or '{}')
            if r.get('event_type') == 'chunk_executed':
                if 'unitsConsumed' in dat:
                    units.append(int(dat['unitsConsumed']))
            if r.get('event_type') == 'chunking_completed':
                fr = dat.get('fill_ratio')
                # compute numeric fill ratio
                if isinstance(fr, str) and '/' in fr:
                    num, den = fr.split('/')
                    chunking_fill_ratio = float(num) / float(den)
                else:
                    try:
                        chunking_fill_ratio = float(fr)
                    except Exception:
                        chunking_fill_ratio = None

    assert units == [85000, 92000]
    assert chunking_fill_ratio == 1.0

    # Check alpha_journal.csv contains a row with expected_out_sol matching price and fill_ratio
    alpha_csv = data_dir / 'alpha_journal.csv'
    assert alpha_csv.exists()
    rows = []
    with open(alpha_csv, 'r', newline='') as fh:
        reader = csv.DictReader(fh)
        for r in reader:
            rows.append(r)
    assert len(rows) >= 1
    last = rows[-1]
    # expected_out_sol column should equal the token_price_sol we provided
    assert float(last.get('expected_out_sol') or 0) == pytest.approx(token_price_sol)
    # fill_ratio column should match numeric 1.0 or '1.0'
    fr_val = last.get('fill_ratio')
    assert fr_val in ('1.0', '1') or (fr_val and float(fr_val) == pytest.approx(1.0))
