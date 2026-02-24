import os
import time
import asyncio
import base64
import logging
import hashlib
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
        self.DEDUP_WINDOW_S = float(os.getenv('JITO_BUNDLE_DEDUP_WINDOW_S', '30'))
        self.RETRY_BASE_DELAY_S = float(os.getenv('JITO_RETRY_BASE_DELAY_S', '0.35'))
        self.RETRY_MAX_DELAY_S = float(os.getenv('JITO_RETRY_MAX_DELAY_S', '2.5'))
        self._recent_bundle_keys: dict[str, float] = {}
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
        dedup_key = None
        if not simulate:
            dedup_key = self._compute_bundle_key(signed_txs)
            if self._is_duplicate_bundle(dedup_key):
                return {
                    'success': False,
                    'dedup': True,
                    'error': 'duplicate bundle suppressed',
                }
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
                    out = dict(result if isinstance(result, dict) else {'success': True, 'result': str(result)})
                    if dedup_key and (out.get('success') or out.get('bundle_id')):
                        self._remember_bundle_key(dedup_key)
                    return out
                except Exception as e:
                    logger.exception('SearcherClient send_bundle failed, falling back to HTTP')

            # Fallback to HTTP post if SDK not available
            if not self.submit_endpoint:
                logger.warning('No Jito submit endpoint configured; returning simulated success')
                return {'success': True, 'simulated': True}

            import httpx
            # build a bundle payload using helper (which will pick a tip receiver)
            # If tip_lamports not provided, compute a dynamic tip based on network load
            if tip_lamports is None:
                try:
                    tip_lamports = int(self.get_dynamic_tip())
                except Exception:
                    tip_lamports = int(os.getenv('JITO_DEFAULT_TIP_LAMPORTS', '10000'))

            bundle = self._build_jito_bundle(signed_txs, tip_lamports)
            payload = bundle
            if simulate:
                url = self.submit_endpoint.rstrip('/') + '/simulate'
            else:
                url = self.submit_endpoint.rstrip('/') + '/submit'

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json=payload)
                try:
                    out = resp.json()
                except Exception:
                    out = {'status_code': resp.status_code, 'text': resp.text}
                if dedup_key and isinstance(out, dict) and (out.get('success') or out.get('bundle_id')):
                    self._remember_bundle_key(dedup_key)
                return out
        except Exception as e:
            logger.exception('submit_atomic_exit error')
            return {'success': False, 'error': str(e)}

    async def submit_with_retry(
        self,
        signed_txs: List[bytes],
        *,
        max_retries: int = 3,
        simulate: bool = False,
        symbol: str | None = None,
        mint: str | None = None,
        tip_lamports: int | None = None,
    ) -> dict:
        """Submit a bundle with bounded retry for transient failures."""
        retries = max(1, int(max_retries))
        last_error = ""
        for attempt in range(1, retries + 1):
            resp = await self.submit_atomic_exit(
                signed_txs,
                simulate=simulate,
                symbol=symbol,
                mint=mint,
                tip_lamports=tip_lamports,
            )
            if resp.get('success') or resp.get('bundle_id'):
                if 'retries' not in resp:
                    resp['retries'] = attempt
                return resp

            last_error = str(resp.get('error') or 'unknown_error')
            if not self._is_retryable_error(last_error):
                resp['retries'] = attempt
                return resp

            if attempt >= retries:
                break

            base = max(0.0, float(self.RETRY_BASE_DELAY_S))
            cap = max(base, float(self.RETRY_MAX_DELAY_S))
            delay = min(cap, base * (2 ** (attempt - 1)))
            delay += random.uniform(0.0, 0.1)
            await asyncio.sleep(delay)

        return {
            'success': False,
            'error': f'retries exhausted after {retries} attempts: {last_error}',
            'retries': retries,
        }

    @staticmethod
    def _is_retryable_error(error: str | None) -> bool:
        txt = str(error or "").strip().lower()
        if not txt:
            return False
        non_retryable = [
            "simulation failed",
            "invalid transaction",
            "insufficient funds",
            "blockhash not found",
        ]
        if any(k in txt for k in non_retryable):
            return False
        retryable = [
            "expired blockhash",
            "blockhash expired",
            "timeout",
            "timed out",
            "http 5",
            "503",
            "429",
            "too many requests",
            "connection reset",
            "temporarily unavailable",
            "unavailable",
            "network",
        ]
        return any(k in txt for k in retryable)

    def _compute_bundle_key(self, signed_txs: List[bytes]) -> str:
        h = hashlib.sha256()
        for tx in signed_txs:
            raw = bytes(tx) if isinstance(tx, (bytes, bytearray, memoryview)) else str(tx).encode("utf-8", errors="ignore")
            h.update(len(raw).to_bytes(4, "big", signed=False))
            h.update(raw)
        return h.hexdigest()

    def _prune_dedup_cache(self) -> None:
        if not self._recent_bundle_keys:
            return
        now = time.time()
        ttl = max(0.0, float(self.DEDUP_WINDOW_S))
        stale = [k for k, ts in self._recent_bundle_keys.items() if (now - ts) > ttl]
        for k in stale:
            self._recent_bundle_keys.pop(k, None)

    def _is_duplicate_bundle(self, key: str) -> bool:
        self._prune_dedup_cache()
        seen_ts = self._recent_bundle_keys.get(key)
        if seen_ts is None:
            return False
        return (time.time() - seen_ts) <= max(0.0, float(self.DEDUP_WINDOW_S))

    def _remember_bundle_key(self, key: str) -> None:
        self._prune_dedup_cache()
        self._recent_bundle_keys[key] = time.time()

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

    def get_dynamic_tip(self) -> int:
        """Compute a dynamic tip in lamports based on network load or configured API.

        Returns an integer lamports value bounded by min/max caps (0.0001 - 0.005 SOL)
        unless overridden by config. This is best-effort and falls back to
        JITO_DEFAULT_TIP_LAMPORTS when data is unavailable.
        """
        try:
            MIN_LAMPORTS = int(0.0001 * 1e9)
            MAX_LAMPORTS = int(0.005 * 1e9)
            # If an external tip API is configured, try to fetch a list of recent tips
            api_url = os.getenv('JITO_TIP_API_URL', '')
            if api_url:
                try:
                    import httpx
                    resp = httpx.get(api_url, timeout=5.0)
                    if resp.status_code == 200:
                        data = resp.json()
                        if isinstance(data, list) and len(data) > 0:
                            nums = [float(x) for x in data if isinstance(x, (int, float)) or (isinstance(x, str) and x.replace('.', '', 1).isdigit())]
                            if nums:
                                nums_sorted = sorted(nums)
                                p = int(os.getenv('JITO_TIP_PERCENTILE', '95'))
                                idx = max(0, min(len(nums_sorted) - 1, int(round((p / 100.0) * len(nums_sorted))) - 1))
                                val = int(nums_sorted[idx])
                                # map into lamports range conservatively
                                mapped = max(MIN_LAMPORTS, min(MAX_LAMPORTS, val))
                                return int(mapped)
                except Exception:
                    pass

            # Attempt to query RPC for recent prioritization fees as a fallback
            try:
                import httpx, json
                payload = {"jsonrpc": "2.0", "id": 1, "method": "getRecentPrioritizationFees", "params": [150]}
                rpc_url = self.rpc
                resp = httpx.post(rpc_url, json=payload, timeout=5.0)
                if resp.status_code == 200:
                    j = resp.json()
                    fees = j.get('result') or j.get('value') or j.get('fees')
                    if isinstance(fees, list) and len(fees) > 0:
                        nums = []
                        for e in fees:
                            try:
                                if isinstance(e, dict):
                                    v = e.get('prioritizationFee') or e.get('fee') or list(e.values())[0]
                                else:
                                    v = e
                                nums.append(float(v))
                            except Exception:
                                continue
                        if nums:
                            nums_sorted = sorted(nums)
                            p = int(os.getenv('JITO_TIP_PERCENTILE', '95'))
                            idx = max(0, min(len(nums_sorted) - 1, int(round((p / 100.0) * len(nums_sorted))) - 1))
                            val = int(nums_sorted[idx])
                            # map to lamports bounds
                            mapped = max(MIN_LAMPORTS, min(MAX_LAMPORTS, val))
                            return int(mapped)
            except Exception:
                pass

            # Final fallback to configured default
            try:
                fallback = int(os.getenv('JITO_DEFAULT_TIP_LAMPORTS', '10000'))
                return max(MIN_LAMPORTS, min(MAX_LAMPORTS, int(fallback)))
            except Exception:
                return MIN_LAMPORTS
        except Exception:
            return int(os.getenv('JITO_DEFAULT_TIP_LAMPORTS', '10000'))

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

    async def monitor_inflight_bundles(
        self,
        bundles: list[dict],
        callback: Callable[[str, str], None] | None = None,
        poll_interval: float = 1.0,
        max_polls: int = 10,
    ) -> dict[str, str]:
        """Poll bundle statuses and invoke callback(bundle_id, status) on completion."""
        pending = {str((b or {}).get('bundle_id') or '').strip() for b in (bundles or [])}
        pending = {b for b in pending if b}
        statuses: dict[str, str] = {}
        if not pending:
            return statuses

        for _ in range(max(1, int(max_polls))):
            if not pending:
                break
            ids = list(pending)
            found: dict[str, str] = {}

            if self.searcher is not None:
                try:
                    fn = getattr(self.searcher, 'get_bundle_statuses', None)
                    if fn:
                        res = fn(ids)
                        if asyncio.iscoroutine(res):
                            res = await res
                        if isinstance(res, dict):
                            for bid in ids:
                                st = res.get(bid, {}).get('status')
                                if st:
                                    found[bid] = str(st)
                        elif isinstance(res, list):
                            for row in res:
                                if not isinstance(row, dict):
                                    continue
                                bid = str(row.get('bundle_id') or row.get('id') or '').strip()
                                st = str(row.get('status') or '').strip()
                                if bid and st:
                                    found[bid] = st
                except Exception:
                    pass

            for bid in ids:
                st = found.get(bid)
                if st in ('landed', 'failed'):
                    statuses[bid] = st
                    pending.discard(bid)
                    if callback:
                        try:
                            callback(bid, st)
                        except Exception:
                            pass

            if pending:
                await asyncio.sleep(max(0.0, float(poll_interval)))

        for bid in list(pending):
            statuses[bid] = 'unknown'
            if callback:
                try:
                    callback(bid, 'unknown')
                except Exception:
                    pass
        return statuses
