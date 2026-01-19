import base64
import pytest
from unittest.mock import AsyncMock, Mock

from src.brain import MarketBrain
import src.trade_executor as te

WSOL_MINT_LITERAL = "So11111111111111111111111111111111111111112"


@pytest.mark.asyncio
async def test_tp1_amount_and_live_gate(mock_jupiter, mock_versioned_tx, mock_async_client, monkeypatch):
    brain = MarketBrain(rpc='http://localhost')
    mint = 'FakeTokenMint11111111111111111111111111111111'

    # patch price and decimals
    monkeypatch.setattr(brain, '_get_birdeye_price', AsyncMock(return_value=1.0))
    monkeypatch.setattr(brain, '_get_token_decimals', AsyncMock(return_value=6))

    quote_mock = mock_jupiter['quote_mock']
    swap_mock = mock_jupiter['swap_mock']

    # Run with live=False: should compute base amount = 0.1 SOL / $1 * 10^6 = 100000
    res = await brain._execute_exit_swap(mint, 0.1, 'TP1', live=False)
    assert quote_mock.await_count >= 1
    called_args = quote_mock.await_args_list[-1][0]
    # args: (input_mint, output_mint, amount, slippage)
    assert called_args[0] == mint
    assert called_args[1] == WSOL_MINT_LITERAL
    assert int(called_args[2]) == 100000
    assert res is True

    # Now run with live=True: ensure send_raw_transaction is invoked
    # live True requires the ENABLE_LIVE_EXITS gate
    monkeypatch.setenv('ENABLE_LIVE_EXITS', '1')
    res2 = await brain._execute_exit_swap(mint, 0.1, 'TP1', live=True)
    assert res2 is True
    assert swap_mock.await_count >= 1


@pytest.mark.asyncio
async def test_live_flag_controls_send(mock_jupiter, mock_versioned_tx, mock_async_client, monkeypatch):
    """Test that live=False doesn't send, live=True attempts execution.

    Note: In the actual implementation, live=True may use Jito bundles instead
    of direct send_raw_transaction calls. This test verifies the simulation-only
    path for live=False.
    """
    brain = MarketBrain(rpc='http://localhost')
    mint = 'FakeTokenMint22222222222222222222222222222222'

    monkeypatch.setattr(brain, '_get_birdeye_price', AsyncMock(return_value=1.0))
    monkeypatch.setattr(brain, '_get_token_decimals', AsyncMock(return_value=6))

    quote_mock = mock_jupiter['quote_mock']
    swap_mock = mock_jupiter['swap_mock']

    send_mock = mock_async_client['send_mock']

    # live=False -> send_raw_transaction should not be called (simulation only)
    res1 = await brain._execute_exit_swap(mint, 0.1, 'TP1', live=False)
    assert send_mock.await_count == 0
    # Result should be True for successful simulation
    assert res1 is True

    # live=True with ENABLE_LIVE_EXITS=1 -> execution should be attempted
    # The actual method may use Jito bundles instead of direct send
    # We just verify the execution path was taken (swap called)
    monkeypatch.setenv('ENABLE_LIVE_EXITS', '1')
    res2 = await brain._execute_exit_swap(mint, 0.1, 'TP1', live=True)
    assert res2 is True
    # Swap should have been called for both live=False and live=True
    assert swap_mock.await_count >= 2


@pytest.mark.asyncio
async def test_monitor_lifecycle_tp_hit(mock_jupiter, mock_versioned_tx, mock_async_client, monkeypatch, fast_sleep):
    """Simulate the exit monitor lifecycle with a price stream that triggers TP1 then TP2.

    Scenario:
    - entry_price = 100
    - Iteration 1: price=105 (no hit)
    - Iteration 2: price=126 (TP1 hit, >=25%)
    - Iteration 3: price=201 (TP2 hit, >=100%)

    Expectation: _execute_exit_swap called exactly twice and monitor exits.
    """
    brain = MarketBrain(rpc='http://localhost')
    mint = 'LifecycleMint1111111111111111111111111111111'

    # Prepare price sequence; after list exhausted, return last value repeatedly
    prices = [105.0, 126.0, 201.0]
    async def price_side_effect():
        if prices:
            return prices.pop(0)
        return 201.0

    monkeypatch.setattr(brain, '_get_birdeye_price', AsyncMock(side_effect=price_side_effect))

    # Ensure token decimals and other Jupiter plumbing use defaults from fixtures
    monkeypatch.setattr(brain, '_get_token_decimals', AsyncMock(return_value=6))

    # Patch _execute_exit_swap to count invocations and avoid network
    exec_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(brain, '_execute_exit_swap', exec_mock)

    # Run monitor with entry_price=100, small poll interval to iterate quickly
    # We set position_sol to 0.1 (default) and live=False
    await brain._monitor_position_exits(mint, entry_price=100.0, position_sol=0.1, live=False)

    # Expect exactly two calls (TP1 and TP2)
    assert exec_mock.await_count == 2


@pytest.mark.asyncio
async def test_monitor_sequence_tp1_then_sl(mock_jupiter, mock_versioned_tx, mock_async_client, monkeypatch, fast_sleep):
    """Sequence: TP1 then crash to SL — ensure partial then full exit occurs."""
    brain = MarketBrain(rpc='http://localhost')
    mint = 'SeqMint11111111111111111111111111111111'

    # Price sequence: first TP1 (>=25%), then crash to SL (<= -15%)
    prices = [126.0, 85.0]
    async def price_side_effect():
        if prices:
            return prices.pop(0)
        return 85.0

    monkeypatch.setattr(brain, '_get_birdeye_price', AsyncMock(side_effect=price_side_effect))
    monkeypatch.setattr(brain, '_get_token_decimals', AsyncMock(return_value=6))

    exec_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(brain, '_execute_exit_swap', exec_mock)

    await brain._monitor_position_exits(mint, entry_price=100.0, position_sol=0.1, live=False)

    # TP1 then SL => two calls
    assert exec_mock.await_count == 2
    # first call sold 50% (0.05), second sold full position (0.1) per current logic
    first_args = exec_mock.await_args_list[0][0]
    second_args = exec_mock.await_args_list[1][0]
    assert abs(first_args[1] - 0.05) < 1e-9
    assert abs(second_args[1] - 0.1) < 1e-9


@pytest.mark.asyncio
async def test_zero_decimals_aborts_and_does_not_quote(mock_jupiter, mock_versioned_tx, mock_async_client, monkeypatch):
    brain = MarketBrain(rpc='http://localhost')
    mint = 'ZeroDecimalsMint111111111111111111111111111111'

    monkeypatch.setattr(brain, '_get_birdeye_price', AsyncMock(return_value=1.0))
    monkeypatch.setattr(brain, '_get_token_decimals', AsyncMock(return_value=0))

    quote_mock = mock_jupiter['quote_mock']

    res = await brain._execute_exit_swap(mint, 0.1, 'TP1', live=False)
    # base_amount becomes 0 and method should abort without calling Jupiter quote
    assert res is False
    # quote should not have been called because base_amount <= 0 leads to abort before quoting
    # but implementation may still call quote; ensure it's not called
    assert quote_mock.await_count == 0


@pytest.mark.asyncio
async def test_rounding_of_odd_base_units(mock_jupiter, mock_versioned_tx, mock_async_client, monkeypatch):
    brain = MarketBrain(rpc='http://localhost')
    mint = 'OddBaseMint11111111111111111111111111111111'

    # choose price so that base_amount for amount_sol=0.1 becomes 100001 (odd)
    monkeypatch.setattr(brain, '_get_birdeye_price', AsyncMock(return_value=0.99999))
    monkeypatch.setattr(brain, '_get_token_decimals', AsyncMock(return_value=6))

    quote_mock = mock_jupiter['quote_mock']

    res = await brain._execute_exit_swap(mint, 0.1, 'TP1', live=False)
    assert res is True
    called_args = quote_mock.await_args_list[-1][0]
    assert int(called_args[2]) == 100001


@pytest.mark.asyncio
async def test_flash_crash_oracle_defers(mock_jupiter, mock_versioned_tx, mock_async_client, monkeypatch):
    brain = MarketBrain(rpc='http://localhost')
    mint = 'FlashCrashMint111111111111111111111111111111'

    # Price returns 0 -> should defer and call trigger_dry_run_swap
    monkeypatch.setattr(brain, '_get_birdeye_price', AsyncMock(return_value=0.0))
    td_mock = AsyncMock()
    monkeypatch.setattr(brain, 'trigger_dry_run_swap', td_mock)
    res = await brain._execute_exit_swap(mint, 0.1, 'TP1', live=False)
    assert res is False
    assert td_mock.await_count >= 1

    # Price returns None -> same behavior
    monkeypatch.setattr(brain, '_get_birdeye_price', AsyncMock(return_value=None))
    td_mock2 = AsyncMock()
    monkeypatch.setattr(brain, 'trigger_dry_run_swap', td_mock2)
    res2 = await brain._execute_exit_swap(mint, 0.1, 'TP1', live=False)
    assert res2 is False
    assert td_mock2.await_count >= 1


@pytest.mark.asyncio
async def test_exit_aborts_on_low_liquidity(mock_jupiter, mock_versioned_tx, mock_async_client, monkeypatch):
    """If estimated impact > 15% (sell size >> pool liquidity), the exit should be deferred."""
    brain = MarketBrain(rpc='http://localhost')
    mint = 'ThinLiquidityMint11111111111111111111111111111'

    # token price in SOL and pool liquidity USD
    token_price_sol = 1.0
    pool_liquidity_usd = 20.0
    # SOL price in USD used to compute sell USD
    sol_price_usd = 100.0

    async def price_side(maddr):
        # Return (price, liquidity) tuples for token and WSOL
        if maddr == mint:
            return (token_price_sol, pool_liquidity_usd)
        if maddr == WSOL_MINT_LITERAL:
            return (sol_price_usd, None)
        return (None, None)

    monkeypatch.setattr(brain, '_get_birdeye_price', AsyncMock(side_effect=price_side))
    monkeypatch.setattr(brain, '_get_token_decimals', AsyncMock(return_value=6))

    quote_mock = mock_jupiter['quote_mock']
    swap_mock = mock_jupiter['swap_mock']

    # amount_sol=0.1 -> sell USD = 0.1 * 100 = $10; liquidity=20 -> impact 50% -> abort
    res = await brain._execute_exit_swap(mint, 0.1, 'TP2', live=False)
    assert res is False
    # Should not have attempted to get a quote or swap
    assert quote_mock.await_count == 0
    assert swap_mock.await_count == 0



@pytest.mark.asyncio
async def test_circuit_breaker_halts_on_repeat_failure(mock_jupiter, mock_versioned_tx, mock_async_client, monkeypatch, fast_sleep):
    """If multiple chunks each fail all retries consecutively, the circuit breaker should abort the mission.

    Scenario:
    - Configure prices so initial impact -> 3 chunks required
    - Make the first 2 chunks fail all 3 retries each (6 exceptions)
    - Ensure the circuit breaker log event is emitted and the 3rd chunk is NOT attempted
    """
    brain = MarketBrain(rpc='http://localhost')
    mint = 'CircuitBreakerMint11111111111111111111111111111'

    token_price_sol = 1.0
    # sell USD = 0.1 * 100 = $10 -> want impact ~45% -> 3 chunks at 15% threshold
    pool_liquidity_usd = 22.2222222
    sol_price_usd = 100.0

    async def price_side(maddr):
        if maddr == mint:
            return (token_price_sol, pool_liquidity_usd)
        if maddr == WSOL_MINT_LITERAL:
            return (sol_price_usd, None)
        return (None, None)

    monkeypatch.setattr(brain, '_get_birdeye_price', AsyncMock(side_effect=price_side))
    monkeypatch.setattr(brain, '_get_token_decimals', AsyncMock(return_value=6))

    quote_mock = mock_jupiter['quote_mock']
    swap_mock = mock_jupiter['swap_mock']

    # First 2 chunks should each exhaust 3 retries -> 6 exceptions
    swap_mock.side_effect = [Exception('boom')] * 6 + [{'swapTransaction': base64.b64encode(b'fake-tx').decode()}]

    # Spy on execution events
    log_spy = Mock()
    monkeypatch.setattr(brain, '_log_execution_event', log_spy)

    res = await brain._execute_exit_swap(mint, 0.1, 'TP2', live=False)

    # Circuit should have aborted -> overall result should be False
    assert res is False

    # Ensure exactly 6 swap attempts happened (2 chunks * 3 retries)
    assert swap_mock.await_count == 6

    # Ensure circuit breaker event was logged
    assert log_spy.call_count >= 1
    found = False
    for call in log_spy.call_args_list:
        if call[0][1] == 'CIRCUIT_BREAKER_TRIGGERED':
            found = True
            break
    assert found, 'Expected CIRCUIT_BREAKER_TRIGGERED event to be logged'

@pytest.mark.asyncio
async def test_chunked_exit_on_high_impact(mock_jupiter, mock_versioned_tx, mock_async_client, monkeypatch, fast_sleep):
    """When impact > MAX_IMPACT_PCT, _execute_exit_swap should split into chunks and call Jupiter per chunk."""
    brain = MarketBrain(rpc='http://localhost')
    mint = 'ChunkMint11111111111111111111111111111111'

    # Setup prices such that impact is 30% and MAX_IMPACT_PCT default is 15% -> 2 chunks
    token_price_sol = 1.0
    pool_liquidity_usd = 33.3333333  # sell USD 10 -> impact ~30%
    sol_price_usd = 100.0

    async def price_side(maddr):
        if maddr == mint:
            return (token_price_sol, pool_liquidity_usd)
        if maddr == WSOL_MINT_LITERAL:
            return (sol_price_usd, None)
        return (None, None)

    monkeypatch.setattr(brain, '_get_birdeye_price', AsyncMock(side_effect=price_side))
    monkeypatch.setattr(brain, '_get_token_decimals', AsyncMock(return_value=6))

    quote_mock = mock_jupiter['quote_mock']
    swap_mock = mock_jupiter['swap_mock']

    # run chunked exit
    res = await brain._execute_exit_swap(mint, 0.1, 'TP2', live=False)
    assert res is True
    # Expect two chunks -> two quote calls (no retries since mocks succeed)
    assert quote_mock.await_count == 2
    assert swap_mock.await_count == 2


    @pytest.mark.asyncio
    async def test_chunked_exit_with_retry_recovery(mock_jupiter, mock_versioned_tx, mock_async_client, monkeypatch, fast_sleep):
        """Simulate chunk1 failing first attempt then succeeding, chunk2 succeeds immediately."""
        brain = MarketBrain(rpc='http://localhost')
        mint = 'RetryChunkMint111111111111111111111111111111'

        token_price_sol = 1.0
        pool_liquidity_usd = 33.3333333  # impact ~30% -> 2 chunks
        sol_price_usd = 100.0

        async def price_side(maddr):
            if maddr == mint:
                return (token_price_sol, pool_liquidity_usd)
            if maddr == WSOL_MINT_LITERAL:
                return (sol_price_usd, None)
            return (None, None)

        monkeypatch.setattr(brain, '_get_birdeye_price', AsyncMock(side_effect=price_side))
        monkeypatch.setattr(brain, '_get_token_decimals', AsyncMock(return_value=6))

        quote_mock = mock_jupiter['quote_mock']
        swap_mock = mock_jupiter['swap_mock']

        # Configure swap_mock so first call raises, then next two succeed
        swap_res = {'swapTransaction': base64.b64encode(b'fake-tx').decode()}
        swap_mock.side_effect = [Exception('boom'), swap_res, swap_res]

        res = await brain._execute_exit_swap(mint, 0.1, 'TP2', live=False)
        assert res is True
        # total Jupiter swap calls should be 3 (1 failure + 2 successes)
        assert swap_mock.await_count == 3
