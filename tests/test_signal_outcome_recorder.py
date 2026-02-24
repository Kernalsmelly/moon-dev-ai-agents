from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "signal_outcome_recorder.py"
    spec = importlib.util.spec_from_file_location("signal_outcome_recorder", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


def test_build_signal_key_stable_with_signature():
    mod = _load_module()
    sig = {
        "mint": "ExampleMintpump",
        "ts": 1700000000.1234567,
        "metrics": {"source": "ws_logs", "signature": "abc123"},
    }
    key = mod.build_signal_key(sig)
    assert key == "ExampleMintpump|1700000000.123457|ws_logs|abc123"


def test_migrate_pending_state_preserves_multiple_rows_for_same_mint():
    mod = _load_module()
    pending = {
        # Legacy mint-keyed entry
        "MintA": {"signal_ts": 1700000000.0, "metrics": {"source": "ws_logs"}, "done": []},
        # Already signal-key-like key with same mint but later timestamp
        "MintA|1700000005.000000|dex_mover": {
            "mint": "MintA",
            "signal_ts": 1700000005.0,
            "metrics": {"source": "dex_mover"},
            "done": [],
        },
    }
    out = mod.migrate_pending_state(pending)
    assert len(out) == 2
    keys = sorted(out.keys())
    assert keys[0].startswith("MintA|1700000000.000000|ws_logs")
    assert keys[1].startswith("MintA|1700000005.000000|dex_mover")
    assert out[keys[0]]["mint"] == "MintA"
    assert out[keys[1]]["mint"] == "MintA"
