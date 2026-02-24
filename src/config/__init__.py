"""Lightweight test-friendly config defaults.

This module provides a few default configuration symbols relied on by
the test-suite. The real deployment may populate these from environment
or a separate config provider; keeping minimal defaults here prevents
AttributeError during tests.
"""
"""Lightweight test-friendly config defaults.

This module provides a few default configuration symbols relied on by
the test-suite. The real deployment may populate these from environment
or a separate config provider; keeping minimal defaults here prevents
AttributeError during tests.
"""
import os
from pathlib import Path

# Authoritative webhook for Paper Trade Battle Mode (tests/local overrides may still use env)
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1462684075688067178/TJfAPSd3oShJXg2Xct-_gzc6CnMIdZ3CHdNpuw6vNcoOsD6ZPrrU2HgFzYFSbjAWrzny"

# Paper trading toggle for lightweight test config
USE_PAPER_TRADING = True

# Safety defaults used by tests
SHADOW_MODE = False
MAX_HISTORY_SIZE = int(os.getenv('MAX_HISTORY_SIZE', '1000'))
WATCHLIST_MINTS = None

# Jito defaults
ENABLE_JITO = False
JITO_BLOCK_ENGINE_URL = os.getenv('JITO_BLOCK_ENGINE_URL', None)
JITO_TIP_AMOUNT_SOL = float(os.getenv('JITO_TIP_AMOUNT_SOL', '0.0'))

# Default RPC URLs for tests / local development. Real deployment may
# override this via the top-level config or environment.
RPC_URLS = [
	'https://first.mock.rpc',
	'https://second.mock.rpc',
]

# Trades JSONL path
TRADES_JSONL_PATH = str(Path(os.getenv('TRADES_JSONL_PATH', os.path.join(os.path.dirname(__file__), '..', 'data', 'trades.jsonl'))).resolve())
