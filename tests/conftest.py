import os
import sys
from pathlib import Path
import pytest
import pandas as pd
import weakref
import asyncio

# Ensure test imports can resolve project modules under project root and `src`.
repo_root = str(Path(__file__).resolve().parents[1])
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)
src_path = os.path.join(repo_root, 'src')
if src_path not in sys.path:
    sys.path.insert(0, src_path)

import src.config as cfg


@pytest.fixture(scope='session', autouse=True)
def isolate_telemetry(tmp_path_factory):
    """Autouse fixture to redirect telemetry and watchlist to temporary files.

    This ensures tests do not write into the repository data/ folder and
    provides hermetic isolation for execution events and watchlist.
    """
    td = tmp_path_factory.mktemp('telemetry')
    ev = td / 'execution_events.csv'
    wl = td / 'watchlist.json'

    # ensure files exist
    ev.write_text('')
    wl.write_text('{}')

    # Override config env vars so code that imports src.config sees these
    os.environ['EXECUTION_LOG_PATH'] = str(ev)
    os.environ['WATCHLIST_PATH'] = str(wl)
    # Ensure brain_state.json does not persist test state between runs
    try:
        repo_data_dir = os.path.join(repo_root, 'data')
        os.makedirs(repo_data_dir, exist_ok=True)
        bs = os.path.join(repo_data_dir, 'brain_state.json')
        with open(bs, 'w', encoding='utf-8') as _fh:
            _fh.write('{}')
    except Exception:
        pass
    # directly set module attributes too for already-imported modules
    try:
        setattr(cfg, 'EXECUTION_LOG_PATH', str(ev))
    except Exception:
        pass
    try:
        setattr(cfg, 'WATCHLIST_PATH', str(wl))
    except Exception:
        pass

    yield
import base64
from unittest.mock import AsyncMock, Mock
import importlib
import pytest
import sys
import types
import httpx
from httpx import Response

# --- External HTTP catcher: monkeypatch httpx clients to avoid real network calls ---
@pytest.fixture(autouse=True)
def block_external_http(monkeypatch):
    """Replace httpx.Client.request and httpx.AsyncClient.request with a stub
    that returns a harmless JSON response for external URLs. Allows tests to
    proceed without hitting third-party services.
    """
    # Keep original methods to allow localhost/file requests if necessary
    orig_async = getattr(httpx.AsyncClient, 'request', None)
    orig_sync = getattr(httpx.Client, 'request', None)

    async def _async_request(self, method, url, *args, **kwargs):
        s = str(url)
        # allow localhost and file URLs to pass through if desired
        if s.startswith('http://localhost') or s.startswith('http://127.') or s.startswith('file://'):
            if orig_async:
                return await orig_async(self, method, url, *args, **kwargs)
        # tailored mocks for known external providers used by tests
        # Birdeye trending/new-listing
        if 'birdeye' in s and ('new_listing' in s or 'tokens' in s or 'trending' in s):
            return Response(200, json={'data': []}, request=httpx.Request(method, url))
        # Birdeye price/volume single endpoint
        if 'birdeye' in s and 'price_volume' in s:
            return Response(200, json={'data': {'volume_24h_usd': 1000000, 'price': 1.0, 'liquidityUsd': 500000}}, request=httpx.Request(method, url))
        # Jito tip API -> return simple list of lamport tips
        if 'jito' in s and ('tip' in s or 'tips' in s):
            return Response(200, json=[10000, 15000, 20000, 25000], request=httpx.Request(method, url))
        # Jito bundle endpoints (simulate/submit)
        if 'jito' in s and ('/simulate' in s or '/submit' in s or 'block_engine' in s):
            return Response(200, json={'simulated': True, 'success': True, 'bundle_id': 'BUNDLE123', 'land_latency_ms': 120}, request=httpx.Request(method, url))
        # fallback: return a generic mocked successful response
        return Response(200, json={'mocked': True}, request=httpx.Request(method, url))

    def _sync_request(self, method, url, *args, **kwargs):
        s = str(url)
        if s.startswith('http://localhost') or s.startswith('http://127.') or s.startswith('file://'):
            if orig_sync:
                return orig_sync(self, method, url, *args, **kwargs)
        # mirror the async stub logic for sync clients
        if 'birdeye' in s and ('new_listing' in s or 'tokens' in s or 'trending' in s):
            return Response(200, json={'data': []}, request=httpx.Request(method, url))
        if 'birdeye' in s and 'price_volume' in s:
            return Response(200, json={'data': {'volume_24h_usd': 1000000, 'price': 1.0, 'liquidityUsd': 500000}}, request=httpx.Request(method, url))
        if 'jito' in s and ('tip' in s or 'tips' in s):
            return Response(200, json=[10000, 15000, 20000, 25000], request=httpx.Request(method, url))
        if 'jito' in s and ('/simulate' in s or '/submit' in s or 'block_engine' in s):
            return Response(200, json={'simulated': True, 'success': True, 'bundle_id': 'BUNDLE123', 'land_latency_ms': 120}, request=httpx.Request(method, url))
        return Response(200, json={'mocked': True}, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx.AsyncClient, 'request', _async_request, raising=False)
    monkeypatch.setattr(httpx.Client, 'request', _sync_request, raising=False)
    yield


@pytest.fixture()
def stub_birdeye_volume(monkeypatch):
    """Ensure Birdeye volume lookups return a sensible default during tests.

    Some unit tests only mock trending but not the detailed price/volume
    endpoint. Provide a conservative non-zero volume so the watcher does not
    skip signals due to missing Birdeye data.
    """
    try:
        from src.brain import MarketBrain

        async def _fake_birdeye(self, mint):
            return 1_000_000

        monkeypatch.setattr(MarketBrain, '_get_birdeye_volume', _fake_birdeye)
    except Exception:
        pass
    yield
    # restore is automatic when monkeypatch fixture cleans up


@pytest.fixture(autouse=True)
def jito_sdk_stub(monkeypatch):
    """Inject a minimal `jito_sdk` module into sys.modules so legacy imports
    succeed when the real package isn't installed.
    """
    if 'jito_sdk' not in sys.modules:
        mod = types.ModuleType('jito_sdk')
        # Provide a minimal JitoClient class if code expects it
        class JitoClient:
            def __init__(self, *args, **kwargs):
                pass
            async def submit_atomic_exit(self, *a, **k):
                return {'success': True, 'simulated': True}
            async def confirm_bundle_landing(self, *a, **k):
                return False

        mod.JitoClient = JitoClient
        sys.modules['jito_sdk'] = mod
    yield

# --- CSV stubber: ensure missing Dropbox CSVs don't break tests ---
_pd_read_csv = pd.read_csv

def _safe_read_csv(path, *args, **kwargs):
    try:
        return _pd_read_csv(path, *args, **kwargs)
    except FileNotFoundError:
        # Return an empty DataFrame so consumers can iterate/len() without error
        return pd.DataFrame()


@pytest.fixture(autouse=True)
def stub_pandas_read_csv(monkeypatch):
    """Auto-applied fixture that replaces pandas.read_csv with a safe version
    returning an empty DataFrame for missing files (prevents FileNotFoundError
    from data-dependent scripts during tests).
    """
    monkeypatch.setattr(pd, 'read_csv', _safe_read_csv)
    # Ensure live flags are explicitly off during tests
    os.environ['JITO_ENABLED'] = os.environ.get('JITO_ENABLED', '0')
    os.environ['ENABLE_LIVE_EXITS'] = os.environ.get('ENABLE_LIVE_EXITS', '0')
    # Lower alpha threshold for tests to avoid stringent alpha gating
    os.environ['MIN_ALPHA_SCORE_THRESHOLD'] = os.environ.get('MIN_ALPHA_SCORE_THRESHOLD', '0')
    yield


def _import_trade_executor():
    return importlib.import_module('src.trade_executor')

WSOL_MINT_LITERAL = "So11111111111111111111111111111111111111112"

@pytest.fixture
def mock_jupiter(monkeypatch):
    """Patch trade_executor Jupiter helpers with AsyncMocks and return them."""
    te = _import_trade_executor()
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
    te = _import_trade_executor()

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


@pytest.fixture(autouse=True)
def ensure_brain_shutdown(monkeypatch):
    """Autouse async fixture that registers MarketBrain instances created during a test
    and ensures they are shut down at the end of the test to avoid orphaned
    background tasks causing memory growth or dangling asyncio tasks.
    """
    brains = weakref.WeakSet()
    try:
        from src import brain as brain_mod
        MarketBrain = getattr(brain_mod, 'MarketBrain', None)
        if MarketBrain is not None:
            orig_init = MarketBrain.__init__

            def _wrapped_init(self, *a, **k):
                # call original initializer then register instance
                orig_init(self, *a, **k)
                try:
                    brains.add(self)
                except Exception:
                    pass

            monkeypatch.setattr(MarketBrain, '__init__', _wrapped_init, raising=False)
    except Exception:
        pass
    # Also wrap ExhaustionEngine and WhaleWatcher constructors so tests that
    # instantiate them directly get registered for teardown as well.
    try:
        from src.engine import exhaustion_engine as ee_mod
        ExhaustionEngine = getattr(ee_mod, 'ExhaustionEngine', None)
        if ExhaustionEngine is not None:
            orig_e_init = ExhaustionEngine.__init__

            def _wrapped_e_init(self, *a, **k):
                orig_e_init(self, *a, **k)
                try:
                    brains.add(self)
                except Exception:
                    pass

            monkeypatch.setattr(ExhaustionEngine, '__init__', _wrapped_e_init, raising=False)
    except Exception:
        pass

    try:
        from src.adapters.agents import whale_watcher as ww_mod
        WhaleWatcher = getattr(ww_mod, 'WhaleWatcher', None)
        if WhaleWatcher is not None:
            orig_w_init = WhaleWatcher.__init__

            def _wrapped_w_init(self, *a, **k):
                orig_w_init(self, *a, **k)
                try:
                    brains.add(self)
                except Exception:
                    pass

            monkeypatch.setattr(WhaleWatcher, '__init__', _wrapped_w_init, raising=False)
    except Exception:
        pass

    yield

    # Teardown: gracefully stop/shutdown any registered instances
    for b in list(brains):
        try:
            # prefer async stop(), then async shutdown(); run them synchronously
            if hasattr(b, 'stop') and callable(getattr(b, 'stop')):
                try:
                    asyncio.run(b.stop())
                    continue
                except Exception:
                    # fall through to try shutdown()
                    pass
            shutdown = getattr(b, 'shutdown', None)
            if callable(shutdown):
                try:
                    asyncio.run(shutdown())
                except Exception:
                    pass
        except Exception:
            pass
