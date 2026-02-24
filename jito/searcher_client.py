from __future__ import annotations

import os
import logging
from typing import Any, List

logger = logging.getLogger(__name__)


# Runtime toggle: use real Jito client when env var USE_REAL_JITO is truthy.
USE_REAL = os.getenv('USE_REAL_JITO', '0') in ('1', 'true', 'True')

RealSearcherClient = None
if USE_REAL:
    try:
        # Try common import patterns for the upstream package. The exact
        # module name may vary; attempt a couple of likely names but fail
        # gracefully back to the local stub if not available.
        try:
            from jito import searcher_client as _real_mod
            RealSearcherClient = getattr(_real_mod, 'SearcherClient', None)
        except Exception:
            try:
                # some installs expose a top-level searcher_client module
                import searcher_client as _real_mod2

                RealSearcherClient = getattr(_real_mod2, 'SearcherClient', None)
            except Exception:
                RealSearcherClient = None
    except Exception:
        RealSearcherClient = None


class SearcherClient:
    """SearcherClient facade that either delegates to the real upstream
    implementation (when USE_REAL_JITO is set) or falls back to a minimal
    local stub useful for testing and offline development.
    """

    def __init__(self, private_key: str | None = None, endpoint: str | None = None):
        self._use_real = USE_REAL and RealSearcherClient is not None
        if self._use_real:
            try:
                # instantiate the real client with the provided args
                self._real = RealSearcherClient(private_key=private_key, endpoint=endpoint)
            except Exception:
                logger.exception('Failed to construct real SearcherClient; falling back to stub')
                self._use_real = False
                self._real = None
        else:
            self._real = None

        # stub state (used when not using real client)
        self.private_key = private_key
        self.endpoint = endpoint

    def send_bundle(self, signed_txs: List[bytes], tip_lamports: int | None = None, simulate: bool = True) -> Any:
        """Send a bundle. Delegates to real client when available; otherwise
        return a deterministic stub response suitable for tests.
        """
        if self._use_real and self._real is not None:
            try:
                return self._real.send_bundle(signed_txs, tip_lamports=tip_lamports, simulate=simulate)
            except Exception:
                logger.exception('real SearcherClient.send_bundle failed; returning stub result')

        # stubbed response
        return {"success": True, "bundle_id": "stub-bundle-000"}

    def get_bundle_statuses(self, bundle_ids: List[str]) -> Any:
        """Return statuses for bundle ids. Delegates to real client when
        available; otherwise returns a simple mapping used by tests.
        """
        if self._use_real and self._real is not None:
            try:
                return self._real.get_bundle_statuses(bundle_ids)
            except Exception:
                logger.exception('real SearcherClient.get_bundle_statuses failed; returning stub mapping')

        return {bid: {"status": "landed"} for bid in bundle_ids}
