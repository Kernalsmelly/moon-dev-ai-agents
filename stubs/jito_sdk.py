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

    def __init__(self, units_fixed: int | None = None, units_range: tuple[int, int] = (150_000, 300_000), malformed: bool = False):
        # If units_fixed provided, always return that value for determinism in tests
        self.units_fixed = units_fixed
        self.units_range = units_range
        self.malformed = bool(malformed)
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
