"""
Local shim of solana.rpc.async_api.AsyncClient used for lightweight tests and
local scripts. To enable the global RPC shield, a callsite can set the
GLOBAL_RPC_CALLABLE via set_global_rpc_caller(callable) where callable is an
async function of the form: async def rpc_call(method: str, params: list|dict|None)

If GLOBAL_RPC_CALLABLE is set, methods will delegate to it; otherwise the
shim returns canned responses for offline usage.
"""

from typing import Any

GLOBAL_RPC_CALLABLE = None


def set_global_rpc_caller(fn):
    global GLOBAL_RPC_CALLABLE
    GLOBAL_RPC_CALLABLE = fn


class AsyncClient:
    def __init__(self, endpoint=None):
        self.endpoint = endpoint

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def _delegate(self, method: str, params: list | dict | None = None) -> Any:
        if GLOBAL_RPC_CALLABLE is None:
            # fallback canned responses
            return None
        try:
            return await GLOBAL_RPC_CALLABLE(method, params)
        except Exception:
            raise

    async def simulate_transaction(self, tx):
        res = await self._delegate('simulateTransaction', [tx])
        return res or {'value': {'unitsConsumed': None, 'err': None}}

    async def get_account_info(self, *args, **kwargs):
        params = []
        if args:
            params.extend(args)
        if kwargs:
            params.append(kwargs)
        res = await self._delegate('getAccountInfo', params)
        return res or {'value': None}

    async def get_latest_blockhash(self):
        res = await self._delegate('getLatestBlockhash', [])
        return res or {'value': {'blockhash': 'FAKE'}}

    async def send_raw_transaction(self, raw, *args, **kwargs):
        res = await self._delegate('sendRawTransaction', [raw])
        return res or {'result': 'FAKE_SIG'}

    async def get_balance(self, pubkey):
        res = await self._delegate('getBalance', [str(pubkey)])
        return res or {'value': 1000000000}
