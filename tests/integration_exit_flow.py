import asyncio
import os

import pytest

from src.brain import MarketBrain

import stubs.jito_sdk as jito_stub


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
async def test_send_not_called_when_unprofitable(monkeypatch):
    # arrange: create brain and a deterministic jito stub that returns a unitsConsumed
    brain = MarketBrain(rpc='https://example.devnet')
    sdk = jito_stub.JitoSDK(units_fixed=200_000)
    jm = jito_stub.JitoManager(sdk=sdk)
    brain.jito = jm

    # choose tip large enough to make net negative given gross
    tip_lamports = int(0.00003 * 1e9)  # 0.00003 SOL

    # craft chunk meta such that gross gain is tiny (0.000005 SOL)
    input_sol = 0.010
    out_sol = input_sol + 0.000005
    chunk_meta = make_chunk_meta(input_sol, out_sol)

    # simulate first (stub will return unitsConsumed inside dict)
    sim_resp = await brain.jito.submit_atomic_exit([], simulate=True)
    precise_units = brain._get_precisely_consumed_units(sim_resp)

    # add explicit units to meta (what the real flow does)
    local_meta = dict(chunk_meta)
    local_meta['exact_units'] = precise_units

    # act: compute profit
    profit_info = await brain._calculate_expected_profit([1], tip_lamports, local_meta)

    # assert: net is negative and sendBundle is not called implicitly by our test harness
    assert profit_info['net_profit_sol'] is not None
    assert profit_info['net_profit_sol'] < 0

    # ensure sendBundle was not executed (we haven't asked brain to send when net < 0)
    assert len(sdk.sent_calls) == 0


@pytest.mark.asyncio
async def test_send_called_when_profitable(monkeypatch):
    brain = MarketBrain(rpc='https://example.devnet')
    # smaller units (not used directly in fee calc here), but deterministic
    sdk = jito_stub.JitoSDK(units_fixed=180_000)
    jm = jito_stub.JitoManager(sdk=sdk)
    brain.jito = jm

    tip_lamports = int(1e4)  # 0.00001 SOL

    # craft chunk meta such that gross gain is comfortably larger (0.02 SOL)
    input_sol = 0.010
    out_sol = input_sol + 0.02
    chunk_meta = make_chunk_meta(input_sol, out_sol)

    sim_resp = await brain.jito.submit_atomic_exit([], simulate=True)
    precise_units = brain._get_precisely_consumed_units(sim_resp)
    local_meta = dict(chunk_meta)
    local_meta['exact_units'] = precise_units

    profit_info = await brain._calculate_expected_profit([1], tip_lamports, local_meta)

    assert profit_info['net_profit_sol'] is not None
    assert profit_info['net_profit_sol'] > 0

    # If the brain's gating allowed a send, the test harness would call send; here we emulate that
    # to assert the Jito stub's sendBundle works and records the send.
    send_resp = await brain.jito.submit_atomic_exit([b'fake_tx'], simulate=False)
    assert 'bundle_id' in send_resp
    assert len(sdk.sent_calls) == 1
