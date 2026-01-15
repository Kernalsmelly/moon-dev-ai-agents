import asyncio
import base64
import time
from types import SimpleNamespace

import pytest

from src.brain import MarketBrain


@pytest.mark.asyncio
async def test_alt_cache_hit(monkeypatch):
    brain = MarketBrain.__new__(MarketBrain)
    # minimal cache setup
    brain._alt_cache = {}
    brain._alt_cache_lock = asyncio.Lock()
    brain._alt_cache_ttl = 600

    lookup_key = 'lookup_test_key'
    fake_alt = SimpleNamespace(addresses=['AddrA', 'AddrB'])
    brain._alt_cache[lookup_key] = (time.time(), fake_alt)

    # client that should NOT be called
    class DummyClient:
        async def get_account_info(self, *args, **kwargs):
            raise AssertionError('RPC should not be called on cache hit')

    tx_val = {'transaction': {'message': {'addressTableLookups': [{'accountKey': lookup_key, 'writableIndexes': [0]}]}}}

    resolved = await brain._resolve_alt_keys(DummyClient(), tx_val)
    assert resolved == ['AddrA']


@pytest.mark.asyncio
async def test_alt_cache_ttl_expiration(monkeypatch):
    brain = MarketBrain.__new__(MarketBrain)
    brain._alt_cache = {}
    brain._alt_cache_lock = asyncio.Lock()
    brain._alt_cache_ttl = 300

    lookup_key = 'lookup_ttl_key'
    stale_alt = SimpleNamespace(addresses=['StaleA'])
    # insert stale entry (older than ttl)
    brain._alt_cache[lookup_key] = (time.time() - 3600, stale_alt)

    called = {'rpc': False}

    # patch AddressLookupTableAccount.from_bytes to return a fake object
    class FakeALT:
        def __init__(self, addresses):
            self.addresses = addresses

        @staticmethod
        def from_bytes(raw):
            return FakeALT(['FreshA'])

    monkeypatch.setattr('solders.address_lookup_table_account.AddressLookupTableAccount', FakeALT)

    async def fake_get_account_info(key, encoding=None):
        called['rpc'] = True
        # return minimal structure the resolver expects
        data = base64.b64encode(b'dummy').decode()
        return {'value': {'data': [data, 'base64']}}

    class Client:
        async def get_account_info(self, *args, **kwargs):
            return await fake_get_account_info(*args, **kwargs)

    tx_val = {'transaction': {'message': {'addressTableLookups': [{'accountKey': lookup_key, 'writableIndexes': [0]}]}}}

    resolved = await brain._resolve_alt_keys(Client(), tx_val)
    assert called['rpc'] is True
    assert resolved == ['FreshA']


@pytest.mark.asyncio
async def test_watch_whales_detects_alt_account_balance(monkeypatch):
    # Prepare a brain instance with one whale and a trending mint
    brain = MarketBrain.__new__(MarketBrain)
    brain.rpc = 'https://example.test'
    brain.whales = ['Whale1']
    brain.last_signatures = {'Whale1': None}
    brain.trending_map = {'MintX': {'mint': 'MintX', 'name': 'TokenX', 'volume_pct': 500.0}}
    brain._alt_cache = {}
    brain._alt_cache_lock = asyncio.Lock()
    brain._alt_cache_ttl = 600

    # prevent disk writes
    brain._save_state = lambda: None

    # fake ALT parsing
    class FakeALT:
        def __init__(self, addresses):
            self.addresses = addresses

        @staticmethod
        def from_bytes(raw):
            return FakeALT(['AltAccount1'])

    monkeypatch.setattr('solders.address_lookup_table_account.AddressLookupTableAccount', FakeALT)

    # stub AsyncClient used in watch_whales
    class FakeAsyncClient:
        def __init__(self, rpc=None):
            self._entered = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get_signatures_for_address(self, pk, until=None, limit=5):
            return {'result': {'value': [{'signature': 'sig1'}]}}

        async def get_transaction(self, sig, encoding=None, max_supported_transaction_version=None):
            # Return a tx where the only account with the token is in the ALT (accountIndex 0)
            return {'result': {'value': {
                'meta': {
                    'preTokenBalances': [],
                    'postTokenBalances': [{'mint': 'MintX', 'accountIndex': 0}]
                },
                'transaction': {
                    'message': {
                        'accountKeys': [],
                        'addressTableLookups': [{'accountKey': 'lookup_alt', 'writableIndexes': [0]}]
                    }
                }
            }}}

        async def get_account_info(self, key, encoding='base64'):
            data = base64.b64encode(b'dummy').decode()
            return {'value': {'data': [data, 'base64']}}

    # patch AsyncClient in module to use our fake
    monkeypatch.setattr('src.brain.AsyncClient', FakeAsyncClient)

    called = {'triggered': False}

    async def fake_trigger(mint, amount_sol=0.1):
        called['triggered'] = True

    brain.trigger_dry_run_swap = fake_trigger

    # run watcher
    await brain.watch_whales()

    assert called['triggered'] is True
