import asyncio
import base64
import json

import pytest

from src.brain import MarketBrain


@pytest.mark.asyncio
async def test_check_volume_spikes_and_alpha_filter(monkeypatch):
    """Mock Birdeye and Solana RPC to validate spike detection and alpha filter flow.

    - Birdeye returns a token with a 450% volume spike and verified True.
    - Solana RPC returns one signature and a transaction that touches that token mint.
    - MarketBrain should detect the spike and trigger the dry-run swap path.
    """

    # Prepare a fake trending response from Birdeye
    target_mint = "TARGETMINT111111111111111111111111111111111"
    trending_payload = {
        "data": [
            {
                "name": "PUMP",
                "mint": target_mint,
                "verified": True,
                # intentionally provided as string to test normalization
                "volumeChangePct": "450",
            }
        ]
    }

    # monkeypatch MarketBrain.fetch_trending to return our payload
    async def fake_fetch_trending(self):
        return trending_payload

    monkeypatch.setattr(MarketBrain, "fetch_trending", fake_fetch_trending)

    # Create a brain instance with a real-looking whale pubkey (the code validates via Pubkey.from_string)
    whale_pub = "Gh9ZwEmdLJ8DscKNTkTqPbNwLNNBjuSzaG9Vp2KGtKJr"
    brain = MarketBrain(rpc="https://api.mainnet-beta.solana.com", whales=[whale_pub])

    # Validate that check_volume_spikes finds the spike and populates trending_map
    spikes = await brain.check_volume_spikes()
    assert isinstance(spikes, list) and len(spikes) == 1
    assert spikes[0]["mint"] == target_mint
    assert float(spikes[0]["volume_pct"]) >= 300.0

    # Now mock AsyncClient used in watch_whales
    class FakeAsyncClient:
        def __init__(self, rpc):
            self.rpc = rpc

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get_signatures_for_address(self, pk, until=None, limit=5):
            # return newest-first list of 1 fake signature
            return {"value": [{"signature": "FAKESIG1"}]}

        async def get_transaction(self, signature, encoding="jsonParsed"):
            # return parsed transaction where meta contains the target mint in preTokenBalances
            tx = {
                "value": {
                    "meta": {
                        "preTokenBalances": [{"mint": target_mint}],
                        "postTokenBalances": [],
                    }
                }
            }
            return tx

    # Patch the AsyncClient in the brain module to our fake
    monkeypatch.setattr("src.brain.AsyncClient", FakeAsyncClient)

    # Capture calls to trigger_dry_run_swap rather than performing real HTTP calls
    calls = []

    async def fake_trigger(token_mint, amount_sol=0.1):
        calls.append((token_mint, amount_sol))

    monkeypatch.setattr(MarketBrain, "trigger_dry_run_swap", fake_trigger)

    # Run the whale watcher which should call our fake_trigger once
    await brain.watch_whales()

    assert len(calls) == 1
    assert calls[0][0] == target_mint


@pytest.mark.asyncio
async def test_zero_spike_no_trigger(monkeypatch):
    # Birdeye returns a tiny spike 0.1% -> should be ignored
    tiny_payload = {"data": [{"name": "NOISE", "mint": "NOISEMINT111111111111111111111111111111111", "verified": True, "volumeChangePct": "0.1"}]}

    async def fake_fetch_trending(self):
        return tiny_payload

    monkeypatch.setattr(MarketBrain, "fetch_trending", fake_fetch_trending)
    brain = MarketBrain(rpc="https://api.mainnet-beta.solana.com", whales=["Gh9ZwEmdLJ8DscKNTkTqPbNwLNNBjuSzaG9Vp2KGtKJr"])
    spikes = await brain.check_volume_spikes()
    assert spikes == []


@pytest.mark.asyncio
async def test_ghost_transaction_handles_missing_balances(monkeypatch):
    # Birdeye returns a valid spike
    target_mint = "GHOSTMINT1111111111111111111111111111111111"
    payload = {"data": [{"name": "GHOST", "mint": target_mint, "verified": True, "volumeChangePct": "500"}]}

    async def fake_fetch_trending(self):
        return payload

    monkeypatch.setattr(MarketBrain, "fetch_trending", fake_fetch_trending)
    brain = MarketBrain(rpc="https://api.mainnet-beta.solana.com", whales=["Gh9ZwEmdLJ8DscKNTkTqPbNwLNNBjuSzaG9Vp2KGtKJr"])

    class GhostClient:
        def __init__(self, rpc):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get_signatures_for_address(self, pk, until=None, limit=5):
            return {"value": [{"signature": "GHOSTSIG"}]}

        async def get_transaction(self, signature, encoding="jsonParsed"):
            # meta exists but pre/post balances missing
            return {"value": {"meta": {}}}

    monkeypatch.setattr("src.brain.AsyncClient", GhostClient)

    called = []

    async def fake_trigger(token_mint, amount_sol=0.1):
        called.append(token_mint)

    monkeypatch.setattr(MarketBrain, "trigger_dry_run_swap", fake_trigger)

    # Should not raise and should not call trigger
    await brain.watch_whales()
    assert called == []


@pytest.mark.asyncio
async def test_multi_mint_identifies_correct(monkeypatch):
    # Birdeye trending contains one of multiple mints in a transaction
    target_mint = "MATCHMINT1111111111111111111111111111111111"
    payload = {"data": [{"name": "MULTI", "mint": target_mint, "verified": True, "volumeChangePct": "400"}]}

    async def fake_fetch_trending(self):
        return payload

    monkeypatch.setattr(MarketBrain, "fetch_trending", fake_fetch_trending)
    brain = MarketBrain(rpc="https://api.mainnet-beta.solana.com", whales=["Gh9ZwEmdLJ8DscKNTkTqPbNwLNNBjuSzaG9Vp2KGtKJr"])

    class MultiClient:
        def __init__(self, rpc):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get_signatures_for_address(self, pk, until=None, limit=5):
            return {"value": [{"signature": "MULTISIG"}]}

        async def get_transaction(self, signature, encoding="jsonParsed"):
            tx = {"value": {"meta": {"preTokenBalances": [{"mint": "A111"}, {"mint": "B222"}], "postTokenBalances": [{"mint": target_mint}, {"mint": "C333"}]}}}
            return tx

    monkeypatch.setattr("src.brain.AsyncClient", MultiClient)

    calls = []

    async def fake_trigger(token_mint, amount_sol=0.1):
        calls.append(token_mint)

    monkeypatch.setattr(MarketBrain, "trigger_dry_run_swap", fake_trigger)

    await brain.watch_whales()
    # Trigger should be called once for the matching mint
    assert len(calls) == 1
    assert calls[0] == target_mint
