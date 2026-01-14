import base64
from unittest.mock import AsyncMock, Mock
import pytest

import src.trade_executor as te

WSOL_MINT_LITERAL = "So11111111111111111111111111111111111111112"

@pytest.fixture
def mock_jupiter(monkeypatch):
    """Patch trade_executor Jupiter helpers with AsyncMocks and return them."""
    quote_mock = AsyncMock(return_value={'id': 'q1'})
    swap_tx_bytes = b'fake-tx-bytes'
    swap_b64 = base64.b64encode(swap_tx_bytes).decode()
    swap_mock = AsyncMock(return_value={'swapTransaction': swap_b64})
    monkeypatch.setattr(te, 'get_jupiter_quote', quote_mock)
    monkeypatch.setattr(te, 'get_jupiter_swap', swap_mock)
    # sensible defaults
    monkeypatch.setattr(te, 'DEFAULT_SLIPPAGE_BPS', 50, raising=False)
    monkeypatch.setattr(te, 'ComputeBudgetProgram', Mock(), raising=False)
    fake_key = Mock()
    fake_key.pubkey = Mock(return_value='FakePubkey')
    monkeypatch.setattr(te, 'load_key', Mock(return_value=fake_key))
    def set_swap_failure(mode: str = 'exception'):
        """Allow tests to toggle swap behavior for failure modes.

        mode: 'exception' -> swap_mock raises Exception
              'no_tx'     -> swap_mock returns a dict without 'swapTransaction'
        """
        if mode == 'exception':
            swap_mock.side_effect = Exception('Simulated Jupiter swap failure')
        elif mode == 'no_tx':
            swap_mock.side_effect = None
            swap_mock.return_value = {'unexpected': True}

    return {'quote_mock': quote_mock, 'swap_mock': swap_mock, 'set_swap_failure': set_swap_failure}

@pytest.fixture
def mock_versioned_tx(monkeypatch):
    """Provide a dummy VersionedTransaction class that decodes from bytes and provides __bytes__."""
    class DummyVersionedTransaction:
        def __init__(self, message, signers=None):
            self.message = message
        @classmethod
        def from_bytes(cls, bts):
            class Msg:
                instructions = []
            return cls(Msg(), [])
        def __bytes__(self):
            return b'rawtx'
    monkeypatch.setattr(te, 'VersionedTransaction', DummyVersionedTransaction)
    return DummyVersionedTransaction

@pytest.fixture
def mock_async_client(monkeypatch):
    """Patch src.brain.AsyncClient to a dummy context manager exposing simulate/send mocks."""
    send_mock = AsyncMock(return_value={'result': 'ok'})
    sim_mock = AsyncMock(return_value=Mock(value={'result': {'value': {'unitsConsumed': 100}}}))
    class DummyClient:
        def __init__(self, rpc):
            self.rpc = rpc
            self.simulate_transaction = sim_mock
            self.send_raw_transaction = send_mock
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
    monkeypatch.setattr('src.brain.AsyncClient', DummyClient)
    return {'send_mock': send_mock, 'sim_mock': sim_mock}


@pytest.fixture
def fast_sleep(monkeypatch):
    """Patch asyncio.sleep to run instantly (no real delay) for fast tests.

    This prevents tests that exercise loops with polling intervals from waiting
    during the test run. It simply replaces asyncio.sleep with a no-op async
    function.
    """
    async def _fast_sleep(duration):
        # simulate tiny dilation if desired (e.g., duration * 0.001)
        return None

    monkeypatch.setattr('asyncio.sleep', _fast_sleep)
    yield
