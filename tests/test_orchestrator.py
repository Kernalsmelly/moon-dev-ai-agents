import asyncio
import base64
import csv
import os

import pytest

import src.orchestrator as orch
import src.trade_executor as te


@pytest.mark.asyncio
async def test_orchestrator_simulate_logs(monkeypatch, tmp_path):
    # Setup a fake alpha signal
    spike = {"mint": "OUTMINT111111111111111111111111111111111", "name": "PUMP", "volume_pct": 450}

    # Monkeypatch brain.get_alpha_signal to return our spike once
    class FakeBrain:
        def __init__(self):
            pass

        async def get_alpha_signal(self):
            return spike

    monkeypatch.setattr(orch, "MarketBrain", lambda rpc=None: FakeBrain())

    # Monkeypatch check_balance_ok to always allow
    async def fake_check_balance_ok(rpc, min_remaining_lamports=None, spend_lamports=0):
        return True, 10_000_000_000

    monkeypatch.setattr(orch, "check_balance_ok", fake_check_balance_ok)

    # Monkeypatch te.get_jupiter_quote to return a simple quote dict
    async def fake_quote(input_mint, output_mint, amount, slippage):
        return {"outAmount": "1234500"}

    monkeypatch.setattr(te, "get_jupiter_quote", fake_quote)

    # Monkeypatch te.get_jupiter_swap to return a base64 payload; we'll also
    # replace solders.transaction.VersionedTransaction to a fake so simulation proceeds
    fake_b64 = base64.b64encode(b"fake-tx-bytes").decode()

    async def fake_swap(quote, user_pubkey=None, wrap_and_unwrap=True):
        return {"swapTransaction": fake_b64}

    monkeypatch.setattr(te, "get_jupiter_swap", fake_swap)

    # Fake VersionedTransaction to bypass binary parsing
    import solders.transaction as sold_tx

    class FakeVT:
        def __init__(self, message, keys=None):
            self.message = message

        @classmethod
        def from_bytes(cls, b):
            class Msg:
                instructions = []

            inst = Msg()
            return cls(inst, None)

        def __bytes__(self):
            return b"fake"

    monkeypatch.setattr(sold_tx, "VersionedTransaction", FakeVT)

    # Monkeypatch AsyncClient used in orchestrator to simulate a success
    class FakeClient:
        def __init__(self, rpc):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def simulate_transaction(self, tx):
            return {"value": {"unitsConsumed": 555}}

    monkeypatch.setattr(orch, "AsyncClient", FakeClient)

    # Redirect ALPHA_CSV to tmp file
    tmp_csv = tmp_path / "alpha_journal.csv"
    monkeypatch.setenv("RPC_URL", "https://api.mainnet-beta.solana.com")
    monkeypatch.setattr(orch, "ALPHA_CSV", str(tmp_csv))

    # Run a single cycle of main_loop by calling simulate_swap_and_log directly
    result = await orch.simulate_swap_and_log("https://api.mainnet-beta.solana.com", spike, amount_sol=0.1)
    assert result is True

    # Check that CSV was written and contains our mint
    assert tmp_csv.exists()
    with open(tmp_csv, 'r') as fh:
        content = fh.read()
    assert "OUTMINT1111111111" in content
