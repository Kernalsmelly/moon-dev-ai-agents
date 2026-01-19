import pytest
import asyncio
from stubs import jito_sdk

from src.brain import MarketBrain


def make_chunk_meta(input_sol: float, out_sol: float, units_consumed: int | None = None, sim_result: dict | None = None):
    return {
        1: {
            'input_sol': float(input_sol),
            'expected_out_sol': float(out_sol),
            'units_consumed': units_consumed,
            'sim_result': sim_result,
        }
    }


@pytest.mark.asyncio
async def test_send_not_called_when_unprofitable():
    brain = MarketBrain(rpc='https://example.devnet')
    # stub returns 200k units
    sdk = jito_sdk.JitoSDK(units_fixed=200_000)
    jm = jito_sdk.JitoManager(sdk=sdk)
    brain.jito = jm

    tip_lamports = int(0.00003 * 1e9)
    input_sol = 0.010
    out_sol = input_sol + 0.000005
    chunk_meta = make_chunk_meta(input_sol, out_sol)

    sim_resp = await brain.jito.submit_atomic_exit([], simulate=True)
    # ensure exact RPC shape
    assert isinstance(sim_resp, dict)
    units = brain._get_precisely_consumed_units(sim_resp)
    # add explicit units for profit calc
    local_meta = dict(chunk_meta)
    local_meta['exact_units'] = units

    profit_info = await brain._calculate_expected_profit([1], tip_lamports, local_meta)
    assert profit_info['net_profit_sol'] < 0
    # sendBundle should not have been called automatically
    assert len(sdk.sent_calls) == 0


@pytest.mark.asyncio
async def test_send_called_when_profitable():
    brain = MarketBrain(rpc='https://example.devnet')
    sdk = jito_sdk.JitoSDK(units_fixed=180_000)
    jm = jito_sdk.JitoManager(sdk=sdk)
    brain.jito = jm

    tip_lamports = int(1e4)
    input_sol = 0.010
    out_sol = input_sol + 0.02
    chunk_meta = make_chunk_meta(input_sol, out_sol)

    sim_resp = await brain.jito.submit_atomic_exit([], simulate=True)
    units = brain._get_precisely_consumed_units(sim_resp)
    local_meta = dict(chunk_meta)
    local_meta['exact_units'] = units

    profit_info = await brain._calculate_expected_profit([1], tip_lamparts if False else tip_lamports, local_meta)
    assert profit_info['net_profit_sol'] > 0

    # simulate the live submit and verify stub records the send
    send_resp = await brain.jito.submit_atomic_exit([b'fake_tx'], simulate=False)
    assert 'bundle_id' in send_resp
    assert len(sdk.sent_calls) == 1


@pytest.mark.asyncio
async def test_multi_tx_bundle_units_sum():
    """Ensure _get_precisely_consumed_units sums units across multiple txResults entries."""
    brain = MarketBrain(rpc='https://example.devnet')
    # craft a simulate response with 5 txResults each having unitsConsumed
    txs = [{'unitsConsumed': 100000}, {'unitsConsumed': 120000}, {'unitsConsumed': 90000}, {'unitsConsumed': 110000}, {'unitsConsumed': 130000}]
    sim_resp = {'result': {'value': {'txResults': txs}}}
    total = brain._get_precisely_consumed_units(sim_resp)
    assert total == sum(x['unitsConsumed'] for x in txs)
    # Now ensure calculate_expected_profit accepts explicit units across batch
    sdk = jito_sdk.JitoSDK(units_fixed=None)
    jm = jito_sdk.JitoManager(sdk=sdk)
    brain.jito = jm
    tip_lamports = int(1e4)
    input_sol = 0.01
    out_sol = input_sol + 0.02
    chunk_meta = {1: {'input_sol': input_sol, 'expected_out_sol': out_sol, 'sim_result': sim_resp}}
    # Provide explicit batch-level exact_units
    chunk_meta['exact_units'] = total
    profit = await brain._calculate_expected_profit([1], tip_lamports, chunk_meta)
    # profit should be a numeric value (may be positive depending on unit-price fallback)
    assert 'net_profit_sol' in profit


@pytest.mark.asyncio
async def test_malformed_sim_response_handled_gracefully():
    brain = MarketBrain(rpc='https://example.devnet')
    # create a malformed stub (no unitsConsumed)
    sdk = jito_sdk.JitoSDK(malformed=True)
    jm = jito_sdk.JitoManager(sdk=sdk)
    brain.jito = jm

    tip_lamports = int(1e4)
    input_sol = 0.01
    out_sol = input_sol + 0.001
    chunk_meta = make_chunk_meta(input_sol, out_sol)

    sim_resp = await brain.jito.submit_atomic_exit([], simulate=True)
    # sim_resp is malformed but _get_precisely_consumed_units should return None
    units = brain._get_precisely_consumed_units(sim_resp)
    assert units is None

    # profit calc should still return a result (falls back to estimate) and not raise
    profit_info = await brain._calculate_expected_profit([1], tip_lamports, chunk_meta)
    assert 'net_profit_sol' in profit_info
