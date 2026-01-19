import asyncio
import os
import pytest
import base64

from src.brain import MarketBrain


class DummyResp:
    def __init__(self, success=True, bundle_id=None, failed_index=None):
        self._d = {'success': success}
        if bundle_id is not None:
            self._d['bundle_id'] = bundle_id
        if failed_index is not None:
            self._d['failed_index'] = failed_index

    def get(self, k, default=None):
        return self._d.get(k, default)


@pytest.mark.asyncio
async def test_atomic_revert(tmp_path, monkeypatch):
    # Prepare a MarketBrain with a short RPC (we won't actually hit network)
    mb = MarketBrain(rpc='https://api.testnet.solana.com')

    # Force JITO_ENABLED and disable shadow mode to simulate a live attempt
    monkeypatch.setenv('JITO_ENABLED', '1')
    monkeypatch.setenv('ENABLE_LIVE_EXITS', '1')
    monkeypatch.setenv('SHADOW_MODE', '0')

    # Make _compute_jito_tip deterministic
    async def fake_tip():
        return 12345
    monkeypatch.setattr(mb, '_compute_jito_tip', fake_tip)

    # We'll collect whether submit_atomic_exit was called and simulate a failure
    called = {'count': 0}
    logged = []

    # Patch _log_execution_event to capture telemetry events for assertions
    def fake_log_execution_event(mint, event_type, data):
        logged.append((mint, event_type, data))
    monkeypatch.setattr(mb, '_log_execution_event', fake_log_execution_event)

    async def fake_submit(batch, simulate=False, symbol=None, mint=None, tip_lamports=None):
        called['count'] += 1
        # For the test: simulate that bundle fails with failed_index = 2
        return {'success': False, 'failed_index': 2}

    # Attach fake submit to the JitoManager instance
    if getattr(mb, 'jito', None) is None:
        # create a simple fake jito manager
        class FakeJito:
            async def submit_atomic_exit(self, *args, **kwargs):
                return await fake_submit(*args, **kwargs)
        mb.jito = FakeJito()
    else:
        monkeypatch.setattr(mb.jito, 'submit_atomic_exit', fake_submit)

    # Patch Jupiter helpers to return a fake swap response which decodes to a minimal
    # VersionedTransaction-like bytes blob. We'll craft a small signed-bytes sentinel.
    async def fake_get_jupiter_quote(mint, wsol, base_amount, slippage):
        return {'dummy': 'quote'}

    async def fake_get_jupiter_swap(quote, user_pubkey=None, wrap_and_unwrap=False):
        # Return base64 of a short bytes marker to emulate a tx; the code will call
        # te.VersionedTransaction.from_bytes but we will monkeypatch that to accept
        # raw bytes and return an object with message/instructions attributes used.
        return {'swapTransaction': base64.b64encode(b'FAKE_TX_BYTES').decode('utf-8')}

    monkeypatch.setattr(__import__('src.trade_executor', fromlist=['*']), 'get_jupiter_quote', fake_get_jupiter_quote)
    monkeypatch.setattr(__import__('src.trade_executor', fromlist=['*']), 'get_jupiter_swap', fake_get_jupiter_swap)

    # Monkeypatch VersionedTransaction.from_bytes to accept our fake bytes and produce
    # a minimal object that the code can wrap and sign via te.VersionedTransaction(...)
    import src.trade_executor as te

    class FakeVTX:
        def __init__(self, message, signers=None):
            self.message = message
            self.signers = signers or []
        def __bytes__(self):
            return b'SIGNED_FAKE_TX'
        @classmethod
        def from_bytes(cls, bts):
            class Msg:
                instructions = []
            return cls(Msg())

    # Replace the module's VersionedTransaction with our fake class
    monkeypatch.setattr(te, 'VersionedTransaction', FakeVTX)

    # Also patch load_key to return an object with pubkey()
    class KeyObj:
        def pubkey(self):
            return 'DUMMY_PUBKEY'
    monkeypatch.setattr(te, 'load_key', lambda: KeyObj())

    # Finally, run the chunked path with an amount that triggers chunking
    # We'll choose amount_sol such that estimated impact > MAX_IMPACT_PCT and n_chunks>1
    # To avoid calling Birdeye, monkeypatch _get_birdeye_price to return controlled values
    async def fake_birdeye(mint):
        # token_price_sol, pool_liquidity_usd
        return (0.001, 1_000_000.0)
    monkeypatch.setattr(mb, '_get_birdeye_price', fake_birdeye)

    # Run: choose amount_sol large to force multiple chunks via impact calc
    result = await mb._execute_exit_swap('FAKE_MINT', amount_sol=1000.0, exit_type='test_exit', live=True)

    # Because fake_submit returns success=False, the atomic exit should abort and return False
    assert result is False
    # And JitoManager.submit_atomic_exit should have been called at least once
    assert called['count'] >= 1

    # Assert that a jito_bundle_failed telemetry event was recorded with the failing index
    found = False
    for mint, ev, data in logged:
        if ev == 'jito_bundle_failed' and data.get('failed_chunk_index') == 2:
            found = True
            break
    assert found, f"Expected jito_bundle_failed with failed_chunk_index=2 in logged events, got: {logged}"

