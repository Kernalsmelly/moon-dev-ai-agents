#!/usr/bin/env python3
import asyncio
import os
from solders.pubkey import Pubkey
from src.brain import MarketBrain

async def scan():
    addr = 'GikBZnDSKa1M1TN84J8dtCqkkor7wUpGoV7nZcg5Zfpi'
    clusters = {
        'Alchemy': os.getenv('RPC_URL') or 'https://solana-devnet.g.alchemy.com/v2/SvtEhMMoeXyNIy0WARnZn',
        'Public': 'https://api.devnet.solana.com'
    }
    for name, url in clusters.items():
        try:
            brain = MarketBrain(rpc=url)
            resp = await brain._call_rpc('getBalance', [addr])
            bal = None
            if isinstance(resp, dict):
                bal = resp.get('result', {}).get('value') or resp.get('value')
            else:
                bal = resp

            if bal is None:
                print(f'{name} Balance: <no result>')
            else:
                print(f'{name} Balance: {int(bal) / 10**9} SOL')
        except Exception as e:
            print(f'{name} Balance: ERROR - {e}')

if __name__ == '__main__':
    asyncio.run(scan())
