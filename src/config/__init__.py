"""Lightweight test-friendly config defaults.

This module provides a few default configuration symbols relied on by
the test-suite. The real deployment may populate these from environment
or a separate config provider; keeping minimal defaults here prevents
AttributeError during tests.
"""
import os

# Safety defaults used by tests
SHADOW_MODE = False
MAX_HISTORY_SIZE = int(os.getenv('MAX_HISTORY_SIZE', '1000'))
WATCHLIST_MINTS = None
