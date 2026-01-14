#!/usr/bin/env python3
import asyncio
import os
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey

async def scan():
    addr = 'GikBZnDSKa1M1TN84J8dtCqkkor7wUpGoV7nZcg5Zfpi'
    clusters = {
        'Alchemy': os.getenv('RPC_URL') or 'https://solana-devnet.g.alchemy.com/v2/SvtEhMMoeXyNIy0WARnZn',
        'Public': 'https://api.devnet.solana.com'
    }
    for name, url in clusters.items():
        try:
            async with AsyncClient(url) as client:
                res = await client.get_balance(Pubkey.from_string(addr))
                bal = getattr(res, 'value', None)
                if bal is None:
                    print(f'{name} Balance: <no result>')
                else:
                    print(f'{name} Balance: {bal / 10**9} SOL')
        except Exception as e:
            print(f'{name} Balance: ERROR - {e}')

if __name__ == '__main__':
    asyncio.run(scan())
