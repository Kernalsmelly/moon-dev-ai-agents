#!/usr/bin/env python3
"""Print the bot's public Solana address safely.

This script looks for common environment variables that may contain your
private key (PRIVATE_KEY, SOLANA_PRIVATE_KEY, FUNDER_PRIVATE_KEY). It will
load `.env` if present and print only the derived public address (no private
key material is printed).

Usage:
  source .venv/bin/activate  # optional
  python scripts/get_pubkey.py

If you use a different env var, set it before running:
  PRIVATE_KEY=... python scripts/get_pubkey.py
"""
from __future__ import annotations
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

try:
    from solders.keypair import Keypair
except Exception:
    print("solders library is required. Install with: pip install solders")
    raise SystemExit(1)

candidates = ("PRIVATE_KEY", "SOLANA_PRIVATE_KEY", "FUNDER_PRIVATE_KEY")
found = False
for name in candidates:
    pk = os.getenv(name)
    if pk:
        found = True
        try:
            kp = Keypair.from_base58_string(pk)
            print("\n🚀 BOT PUBLIC ADDRESS:", kp.pubkey())
        except Exception as e:
            print(f"Found env {name} but failed to parse key: {e}")
        break

if not found:
    print("No private-key env var found. Check your .env or set PRIVATE_KEY or SOLANA_PRIVATE_KEY.")
