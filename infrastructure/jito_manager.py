import os
import time
import asyncio
import base64
import logging
from typing import Callable, List
import random

logger = logging.getLogger(__name__)


class JitoManager:
    """Encapsulate Jito/block-engine interactions.

    Responsibilities:
    - submit_atomic_exit(signed_txs): submit or simulate bundles
    - confirm_bundle_landing(bundle_id): poll for bundle landing and emit telemetry
    """

    def __init__(self, rpc: str, telemetry_fn: Callable[[str, dict], None] | None = None):
        self.rpc = rpc
        self.telemetry_fn = telemetry_fn
        # configured endpoints (used as fallback)
        self.submit_endpoint = os.getenv('JITO_BUNDLE_SUBMIT_URL') or os.getenv('JITO_BLOCK_ENGINE_URL')
        self.status_endpoint = os.getenv('JITO_BUNDLE_STATUS_URL') or (self.submit_endpoint and self.submit_endpoint.rstrip('/') + '/statuses')

        # Try to initialize SearcherClient from jito-sdk
        self.searcher = None
        try:
            from jito.searcher_client import SearcherClient
            # PRIVATE_KEY expected to be base58 or similar; pass-through to SDK
            pk = os.getenv('PRIVATE_KEY')
            if pk:
                try:
                    self.searcher = SearcherClient(private_key=pk, endpoint=self.submit_endpoint)
                except Exception:
                    # try without private key
                    self.searcher = SearcherClient(endpoint=self.submit_endpoint)
            else:
                self.searcher = SearcherClient(endpoint=self.submit_endpoint)
        except Exception:
            # jito-sdk not available or failed to init — remain None and fall back to HTTP
            self.searcher = None

        # Tip receivers: prefer an explicit env var with comma-separated tip addresses
        # Otherwise fall back to a curated default list (from a recent handshake probe).
        try:
            env_accounts = os.getenv('JITO_TIP_ACCOUNTS', '')
            if env_accounts:
                self.tip_accounts = [x.strip() for x in env_accounts.split(',') if x.strip()]
            else:
                # default list discovered during verification (can be overridden via env)
                self.tip_accounts = [
                    'HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe',
                    'DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh',
                    'Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY',
                    '96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5',
                    'ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49',
                    'DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL',
                    '3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT',
                    'ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt',
                ]
        except Exception:
            self.tip_accounts = []

    async def submit_atomic_exit(self, signed_txs: List[bytes], simulate: bool = True, symbol: str | None = None, mint: str | None = None, tip_lamports: int | None = None) -> dict:
        """Submit or simulate an atomic bundle. Returns a dict with at least 'success'.

        If SDK is available, use it. Otherwise fall back to POST to configured endpoint.
        """
        try:
            # Prefer SDK SearcherClient if available
            if self.searcher is not None:
                try:
                    # send_bundle/send_bundle may be synchronous or async depending on SDK; handle both
                    send_fn = getattr(self.searcher, 'send_bundle', None)
                    if send_fn is None:
                        return {'success': False, 'error': 'searcher has no send_bundle'}

                    # prepare base64 txs list or raw bytes depending on SDK expectation
                    # We'll pass raw signed bytes and let the SDK handle encoding
                    result = send_fn(signed_txs, tip_lamports=tip_lamports, simulate=simulate)
                    if asyncio.iscoroutine(result):
                        result = await result
                    # expected to return a dict with success and bundle_id
                    return dict(result if isinstance(result, dict) else {'success': True, 'result': str(result)})
                except Exception as e:
                    logger.exception('SearcherClient send_bundle failed, falling back to HTTP')

            # Fallback to HTTP post if SDK not available
            if not self.submit_endpoint:
                logger.warning('No Jito submit endpoint configured; returning simulated success')
                return {'success': True, 'simulated': True}

            import httpx
            # build a bundle payload using helper (which will pick a tip receiver)
            bundle = self._build_jito_bundle(signed_txs, tip_lamports)
            payload = bundle
            if simulate:
                url = self.submit_endpoint.rstrip('/') + '/simulate'
            else:
                url = self.submit_endpoint.rstrip('/') + '/submit'

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json=payload)
                try:
                    return resp.json()
                except Exception:
                    return {'status_code': resp.status_code, 'text': resp.text}
        except Exception as e:
            logger.exception('submit_atomic_exit error')
            return {'success': False, 'error': str(e)}

    def _build_jito_bundle(self, signed_txs: List[bytes], tip_lamports: int | None) -> dict:
        """Construct a minimal bundle payload (transactions base64 + tip receiver/lamports)."""
        try:
            txs_b64 = [base64.b64encode(t).decode('utf-8') for t in signed_txs]
        except Exception:
            txs_b64 = []

        tip_receiver = None
        try:
            if hasattr(self, 'tip_accounts') and isinstance(self.tip_accounts, list) and len(self.tip_accounts) > 0:
                tip_receiver = random.choice(self.tip_accounts)
        except Exception:
            tip_receiver = None

        if not tip_receiver:
            tip_receiver = os.getenv('JITO_TIP_RECEIVER', '')

        bundle = {
            'transactions': txs_b64,
            'tip': {
                'receiver': tip_receiver,
                'lamports': int(tip_lamports) if tip_lamports is not None else None,
            }
        }
        return bundle

    async def confirm_bundle_landing(self, bundle_id: str, max_slots: int = 3, slot_time_s: float = 0.4) -> bool:
        """Poll for bundle landing. If not landed within max_slots, emit telemetry event.

        Returns True if landed, False otherwise.
        """
        try:
            for attempt in range(max_slots):
                # Prefer SDK status check if available
                if self.searcher is not None:
                    try:
                        get_fn = getattr(self.searcher, 'get_bundle_statuses', None)
                        if get_fn:
                            res = get_fn([bundle_id])
                            if asyncio.iscoroutine(res):
                                res = await res
                            # Expect list/dict describing status
                            status = None
                            if isinstance(res, dict):
                                status = res.get(bundle_id, {}).get('status')
                            elif isinstance(res, list) and len(res) > 0 and isinstance(res[0], dict):
                                status = res[0].get('status')
                            if status == 'landed':
                                return True
                            if status == 'failed':
                                if self.telemetry_fn:
                                    try:
                                        self.telemetry_fn('BUNDLE_FAILED', {'bundle_id': bundle_id, 'status': status})
                                    except Exception:
                                        pass
                                return False
                    except Exception:
                        # non-fatal; fall back to HTTP polling below
                        pass

                # check status via HTTP if available
                if self.status_endpoint:
                    try:
                        import httpx
                        async with httpx.AsyncClient(timeout=5.0) as client:
                            resp = await client.get(self.status_endpoint, params={'bundle_id': bundle_id})
                            data = resp.json()
                            status = data.get('status')
                            if status == 'landed':
                                return True
                            if status == 'failed':
                                if self.telemetry_fn:
                                    try:
                                        self.telemetry_fn('BUNDLE_FAILED', {'bundle_id': bundle_id, 'status': status})
                                    except Exception:
                                        pass
                                return False
                    except Exception:
                        pass

                # fallback: sleep for a slot approximation
                await asyncio.sleep(slot_time_s)

            # if we reach here without landing, emit telemetry about expiry
            if self.telemetry_fn:
                try:
                    self.telemetry_fn('BUNDLE_EXPIRED_RETRY_NEEDED', {'bundle_id': bundle_id, 'max_slots': max_slots})
                except Exception:
                    pass
            return False
        except Exception:
            return False
