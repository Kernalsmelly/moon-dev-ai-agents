import asyncio
import random
import uuid
from typing import Any


class JitoSDK:
    """Lightweight in-repo stub that mimics Jito Block Engine simulate/send shapes.

    Methods:
      - simulateBundle(bundle): returns a JSON-RPC-like dict containing unitsConsumed
      - sendBundle(bundle): returns a simple bundle_id dict
    """

    def __init__(self, units_fixed: int | None = None, units_range: tuple[int, int] = (150_000, 300_000), malformed: bool = False, fail_count: int = 0):
        # If units_fixed provided, always return that value for determinism in tests
        self.units_fixed = units_fixed
        self.units_range = units_range
        self.malformed = bool(malformed)
        self.fail_count = fail_count  # Number of sendBundle calls that should fail before succeeding
        self._send_attempt = 0
        self.sim_calls: list[dict[str, Any]] = []
        self.sent_calls: list[dict[str, Any]] = []

    async def simulateBundle(self, bundle: list[bytes] | Any) -> dict:
        # deterministically produce a unitsConsumed number
        if self.malformed:
            # return a malformed response (missing expected keys)
            resp = {'result': {'value': {}}}
            self.sim_calls.append({'bundle': bundle, 'units': None, 'malformed': True})
            await asyncio.sleep(0)
            return resp

        if self.units_fixed is not None:
            units = int(self.units_fixed)
        else:
            units = int(random.randint(self.units_range[0], self.units_range[1]))

        # Exact 2026 JSON-RPC shape expected by the lead (single-tx or aggregated):
        # { "result": { "value": { "unitsConsumed": 150000, "err": null, "logs": [] } } }
        # For multi-tx bundles, provide per-tx `txResults` entries and also a top-level unitsConsumed sum.
        if isinstance(bundle, (list, tuple)) and len(bundle) > 1:
            per = int(units // len(bundle)) if len(bundle) > 0 else int(units)
            tx_results = [{'unitsConsumed': per} for _ in bundle]
            resp = {'result': {'value': {'unitsConsumed': int(sum([r['unitsConsumed'] for r in tx_results])), 'err': None, 'logs': [], 'txResults': tx_results}}}
        else:
            resp = {'result': {'value': {'unitsConsumed': units, 'err': None, 'logs': []}}}
        self.sim_calls.append({'bundle': bundle, 'units': units})
        # simulate small async latency
        await asyncio.sleep(0)
        return resp

    async def sendBundle(self, bundle: list[bytes] | Any) -> dict:
        self._send_attempt += 1
        if self._send_attempt <= self.fail_count:
            self.sent_calls.append({'bundle': bundle, 'bundle_id': None, 'failed': True})
            await asyncio.sleep(0)
            return {'success': False, 'error': 'expired blockhash'}
        bid = str(uuid.uuid4())
        self.sent_calls.append({'bundle': bundle, 'bundle_id': bid})
        await asyncio.sleep(0)
        return {'bundle_id': bid}


class JitoManager:
    """Compatibility wrapper to match older callsites that use submit_atomic_exit.

    submit_atomic_exit(batch, simulate=True/False, **kwargs)
      - simulate -> calls JitoSDK.simulateBundle
      - simulate=False -> calls JitoSDK.sendBundle
    """

    def __init__(self, sdk: JitoSDK | None = None):
        self.sdk = sdk or JitoSDK()

    async def submit_atomic_exit(self, batch: list[bytes], simulate: bool = True, **kwargs) -> dict:
        if simulate:
            return await self.sdk.simulateBundle(batch)
        else:
            return await self.sdk.sendBundle(batch)

    async def submit_with_retry(self, batch: list[bytes], max_retries: int = 3, **kwargs) -> dict:
        """Retry-aware submit that delegates to submit_atomic_exit."""
        for attempt in range(max_retries):
            result = await self.submit_atomic_exit(batch, simulate=False, **kwargs)
            if result.get('bundle_id') or result.get('success'):
                return result
            # Check for non-retryable errors
            error = str(result.get('error', '')).lower()
            if 'simulation failed' in error or 'invalid transaction' in error:
                return result
            await asyncio.sleep(0)
        return result
