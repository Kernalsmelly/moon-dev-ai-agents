"""
Safe balance check helper.
Reads RPC_URL and BOT_PUBLIC_ADDRESS from .env and prints the SOL balance.
Does NOT print private keys. If BOT_PUBLIC_ADDRESS is missing it instructs the user
how to provide it (or to run the existing scripts/get_pubkey.py locally).
"""
from __future__ import annotations
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:
    print('Please install python-dotenv in your environment: pip install python-dotenv')
    raise

load_dotenv()
RPC = os.getenv('RPC_URL')
ADDR = os.getenv('BOT_PUBLIC_ADDRESS')

if not RPC:
    print('RPC_URL is not set in .env — please set RPC_URL to your RPC endpoint and try again')
    raise SystemExit(2)

if not ADDR:
    print('\nBOT_PUBLIC_ADDRESS is not set in .env.\n')
    print('Add a line like: BOT_PUBLIC_ADDRESS=YourPublicAddressHere')
    print('If you only have the private key, run scripts/get_pubkey.py locally to derive the public key (that script prints only the public key).')
    raise SystemExit(3)

try:
    from solana.rpc.api import Client
    from solders.pubkey import Pubkey
except Exception:
    print('Please install solana and solders in your environment: pip install solana solders')
    raise

rpc = Client(RPC)
try:
    addr = Pubkey.from_string(ADDR)
except Exception as e:
    print('BOT_PUBLIC_ADDRESS appears invalid:', e)
    raise

bal = rpc.get_balance(addr)
lam = None
# solana-py may return different shapes depending on version
if isinstance(bal, dict):
    lam = bal.get('result', {}).get('value')
elif hasattr(bal, 'value'):
    lam = getattr(bal, 'value')
else:
    # try to be helpful
    print('Unexpected RPC response format:', bal)
    raise SystemExit(4)

print(f'\n💰 LIVE BALANCE: { (lam or 0) / 10**9 } SOL')
