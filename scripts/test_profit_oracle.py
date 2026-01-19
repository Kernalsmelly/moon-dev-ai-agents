#!/usr/bin/env python3
"""Simple stress test for the profit oracle in src/brain.py.

This script runs three scenarios (A/B/C) described in the lead directive.
It mocks necessary MarketBrain methods to avoid network dependency and asserts
that the oracle returns the expected sign for net profit.

Run with: python3 scripts/test_profit_oracle.py
"""
import asyncio
import json
import os
import time
import sys

# make repo src importable
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if repo_root not in sys.path:
    # Add repo root so `import src.*` works as in normal runtime
    sys.path.append(repo_root)

from src.brain import MarketBrain

async def run_tests():
    b = MarketBrain(rpc='http://localhost', whales=[])

    # monkeypatch birdeye price to a stable value ($50 per SOL)
    async def fake_birdeye(mint):
        return (50.0, None)
    b._get_birdeye_price = fake_birdeye

    # helper to set deterministic _get_exact_priority_fee
    def make_fee_fn(return_lamports):
        def fee_fn(tx_val, units_consumed=None):
            return int(return_lamports)
        return fee_fn

    print('\nScenario A (Green): high-profit, low tip -> should be PROCEED')
    # Input 1 SOL => Output 1.02 SOL (gross gain 0.02 SOL)
    chunk_meta_A = {1: {'input_sol': 1.0, 'expected_out_sol': 1.02, 'units_consumed': 100000, 'sim_result': {}}}
    tip_A = 10000  # 10k lamports = 0.00001 SOL
    b._get_exact_priority_fee = make_fee_fn(5000)  # 5k lamports
    profit_A = await b._calculate_expected_profit([1], tip_A, chunk_meta_A)
    print('profit_A:', profit_A)
    assert profit_A['net_profit_sol'] is not None and profit_A['net_profit_sol'] > 0, 'Scenario A failed: expected positive net profit'

    print('\nScenario B (Red): tiny gross, tip > gross -> should ABORT')
    # Input 1 SOL => Output 1.000005 SOL (gross gain 0.000005 SOL)
    chunk_meta_B = {1: {'input_sol': 1.0, 'expected_out_sol': 1.000005, 'units_consumed': 50000, 'sim_result': {}}}
    tip_B = 30000  # 30k lamports = 0.00003 SOL > gross
    b._get_exact_priority_fee = make_fee_fn(1000)  # small priority fee
    profit_B = await b._calculate_expected_profit([1], tip_B, chunk_meta_B)
    print('profit_B:', profit_B)
    assert profit_B['net_profit_sol'] is not None and profit_B['net_profit_sol'] < 0, 'Scenario B failed: expected negative net profit'

    print('\nScenario C (Red): negative due to high priority fees -> should ABORT')
    # Input 1 SOL => Output 1.0001 SOL (gross gain 0.0001 SOL)
    chunk_meta_C = {1: {'input_sol': 1.0, 'expected_out_sol': 1.0001, 'units_consumed': 150000, 'sim_result': {}}}
    tip_C = 5000  # small tip
    # set priority fee very large (200k lamports = 0.0002 SOL)
    b._get_exact_priority_fee = make_fee_fn(200000)
    profit_C = await b._calculate_expected_profit([1], tip_C, chunk_meta_C)
    print('profit_C:', profit_C)
    assert profit_C['net_profit_sol'] is not None and profit_C['net_profit_sol'] < 0, 'Scenario C failed: expected negative net profit'

    print('\nAll profit-oracle scenarios passed.')

if __name__ == '__main__':
    asyncio.run(run_tests())
