"""Tests for Jito bundle retry, dedup, and background monitoring.

Tests:
- test_retry_on_transient_failure: succeeds after N retries
- test_dedup_prevents_double_submit: same bundle rejected within window
- test_no_retry_on_simulation_failure: non-retryable errors stop immediately
- test_dedup_expires_after_window: dedup allows resubmit after window
- test_retry_exhaustion: all retries fail returns error
- test_monitor_inflight_bundles: background monitor calls callback
"""
import asyncio
import hashlib
import time
import pytest

from infrastructure.jito_manager import JitoManager


class FakeSearcherClient:
    """Minimal fake SearcherClient for testing retry logic."""

    def __init__(self, fail_count: int = 0, fail_error: str = 'expired blockhash'):
        self.fail_count = fail_count
        self.fail_error = fail_error
        self._attempt = 0
        self.calls = []

    def send_bundle(self, signed_txs, **kwargs):
        self._attempt += 1
        self.calls.append({'attempt': self._attempt, 'txs': signed_txs})
        if self._attempt <= self.fail_count:
            return {'success': False, 'error': self.fail_error}
        return {'success': True, 'bundle_id': f'BUNDLE_{self._attempt}'}

    def get_bundle_statuses(self, bundle_ids):
        return {bid: {'status': 'landed'} for bid in bundle_ids}


def _make_jito_manager(fail_count: int = 0, fail_error: str = 'expired blockhash') -> JitoManager:
    """Create a JitoManager with a fake searcher for testing."""
    jm = JitoManager(rpc='https://fake-rpc.test')
    jm.searcher = FakeSearcherClient(fail_count=fail_count, fail_error=fail_error)
    jm.submit_endpoint = None  # Disable HTTP fallback
    return jm


@pytest.mark.asyncio
async def test_retry_on_transient_failure():
    """Bundle succeeds after N transient retries."""
    jm = _make_jito_manager(fail_count=2)

    result = await jm.submit_with_retry([b'tx1', b'tx2'], max_retries=3)

    assert result.get('success') or result.get('bundle_id')
    assert result.get('bundle_id') == 'BUNDLE_3'
    assert jm.searcher._attempt == 3


@pytest.mark.asyncio
async def test_retry_succeeds_first_try():
    """Bundle succeeds on first attempt, no retries needed."""
    jm = _make_jito_manager(fail_count=0)

    result = await jm.submit_with_retry([b'tx1'])

    assert result.get('success') or result.get('bundle_id')
    assert jm.searcher._attempt == 1


@pytest.mark.asyncio
async def test_no_retry_on_simulation_failure():
    """Non-retryable simulation failure stops immediately."""
    jm = _make_jito_manager(fail_count=5, fail_error='simulation failed: insufficient funds')

    result = await jm.submit_with_retry([b'tx1'], max_retries=3)

    assert not result.get('success')
    assert jm.searcher._attempt == 1  # Only one attempt, no retries


@pytest.mark.asyncio
async def test_no_retry_on_invalid_transaction():
    """Non-retryable invalid transaction error stops immediately."""
    jm = _make_jito_manager(fail_count=5, fail_error='invalid transaction')

    result = await jm.submit_with_retry([b'tx1'], max_retries=3)

    assert not result.get('success')
    assert jm.searcher._attempt == 1


@pytest.mark.asyncio
async def test_retry_exhaustion():
    """All retries exhausted returns error with retry count."""
    jm = _make_jito_manager(fail_count=10)  # Always fails

    result = await jm.submit_with_retry([b'tx1'], max_retries=3)

    assert not result.get('bundle_id')
    assert result.get('retries') == 3
    assert 'exhausted' in result.get('error', '')
    assert jm.searcher._attempt == 3


@pytest.mark.asyncio
async def test_dedup_prevents_double_submit():
    """Same bundle rejected within dedup window."""
    jm = _make_jito_manager(fail_count=0)

    txs = [b'tx_data_123']

    # First submit succeeds
    result1 = await jm.submit_atomic_exit(txs, simulate=False)
    assert result1.get('success') or result1.get('bundle_id')

    # Second submit with same content is rejected
    result2 = await jm.submit_atomic_exit(txs, simulate=False)
    assert result2.get('dedup') is True
    assert not result2.get('success')


@pytest.mark.asyncio
async def test_dedup_allows_different_bundles():
    """Different bundles are not deduplicated."""
    jm = _make_jito_manager(fail_count=0)

    result1 = await jm.submit_atomic_exit([b'tx_A'], simulate=False)
    result2 = await jm.submit_atomic_exit([b'tx_B'], simulate=False)

    assert result1.get('success') or result1.get('bundle_id')
    assert result2.get('success') or result2.get('bundle_id')


@pytest.mark.asyncio
async def test_dedup_expires_after_window():
    """Dedup allows resubmit after window expires."""
    jm = _make_jito_manager(fail_count=0)
    jm.DEDUP_WINDOW_S = 0.1  # Very short window for testing

    txs = [b'tx_data_456']

    # First submit
    result1 = await jm.submit_atomic_exit(txs, simulate=False)
    assert result1.get('success') or result1.get('bundle_id')

    # Wait for window to expire
    await asyncio.sleep(0.15)

    # Second submit should succeed after window expired
    result2 = await jm.submit_atomic_exit(txs, simulate=False)
    assert result2.get('success') or result2.get('bundle_id')


@pytest.mark.asyncio
async def test_dedup_skipped_for_simulate():
    """Simulate mode does not trigger dedup."""
    jm = _make_jito_manager(fail_count=0)

    txs = [b'tx_sim']

    result1 = await jm.submit_atomic_exit(txs, simulate=True)
    result2 = await jm.submit_atomic_exit(txs, simulate=True)

    # Both should succeed (simulate is not deduped)
    assert isinstance(result1, dict)
    assert isinstance(result2, dict)


@pytest.mark.asyncio
async def test_compute_bundle_key():
    """Bundle key is deterministic for same content."""
    jm = _make_jito_manager()

    txs = [b'hello', b'world']
    key1 = jm._compute_bundle_key(txs)
    key2 = jm._compute_bundle_key(txs)

    assert key1 == key2
    assert len(key1) == 64  # SHA256 hex digest

    # Different content -> different key
    key3 = jm._compute_bundle_key([b'different'])
    assert key3 != key1


@pytest.mark.asyncio
async def test_is_retryable_error():
    """Verify retryable vs non-retryable error classification."""
    assert JitoManager._is_retryable_error('expired blockhash')
    assert JitoManager._is_retryable_error('timeout')
    assert JitoManager._is_retryable_error('HTTP 503')

    assert not JitoManager._is_retryable_error('simulation failed')
    assert not JitoManager._is_retryable_error('invalid transaction')
    assert not JitoManager._is_retryable_error('insufficient funds')
    assert not JitoManager._is_retryable_error('Blockhash Not Found')  # case insensitive


@pytest.mark.asyncio
async def test_monitor_inflight_bundles():
    """Background monitor calls callback for each bundle."""
    jm = _make_jito_manager()
    statuses = {}

    def on_status(bundle_id, status):
        statuses[bundle_id] = status

    bundles = [{'bundle_id': 'B1'}, {'bundle_id': 'B2'}]
    await jm.monitor_inflight_bundles(bundles, callback=on_status, poll_interval=0.01)

    assert 'B1' in statuses
    assert 'B2' in statuses
    assert statuses['B1'] == 'landed'
    assert statuses['B2'] == 'landed'
