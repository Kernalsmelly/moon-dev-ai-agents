from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "meme_pipeline_health.py"
    spec = importlib.util.spec_from_file_location("meme_pipeline_health", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


def test_parse_ws_emit_stats_extracts_numeric_fields():
    mod = _load_module()
    line = (
        "WS emit_stats emitted=2 rate_h=120.0 eval=7 rej_buys=1 rej_net=2 "
        "rej_top=0 rej_accel=0 ws_msgs=81 tx_calls=77 in_window=0"
    )
    summary, stats = mod.parse_ws_emit_stats([line])
    assert summary is not None
    assert "emitted=2" in summary
    assert int(stats["emitted"]) == 2
    assert int(stats["eval"]) == 7
    assert int(stats["ws_msgs"]) == 81
    assert int(stats["tx_calls"]) == 77


def test_signal_source_counts_recent_window_only():
    mod = _load_module()
    now = 1_700_000_000.0
    rows = [
        {"ts": now - 30, "metrics": {"source": "ws_logs"}},
        {"ts": now - 20, "metrics": {"source": "dex_mover"}},
        {"ts": now - 10, "metrics": {"source": "ws_logs"}},
        {"ts": now - 5000, "metrics": {"source": "ws_logs"}},  # old -> excluded
    ]
    lines = [json.dumps(r) for r in rows]
    counts = mod.signal_source_counts(lines, now_ts=now, window_s=120)
    assert counts["ws_logs"] == 2
    assert counts["dex_mover"] == 1
    assert "unknown" not in counts


def test_detect_positions_db_prefers_runner_meta(tmp_path, monkeypatch):
    mod = _load_module()
    base = tmp_path / "proj"
    data = base / "data"
    data.mkdir(parents=True)
    db = data / "positions_base_simple.db"
    db.write_text("", encoding="utf-8")

    meta = data / "meme_base_simple_runner.json"
    meta.write_text(json.dumps({"db": "data/positions_base_simple.db"}), encoding="utf-8")

    monkeypatch.setattr(mod, "BASE", str(base))
    monkeypatch.setattr(mod, "RUNNER_META", str(meta))
    monkeypatch.setenv("POSITION_DB", "")

    resolved = mod.detect_positions_db()
    assert Path(resolved) == db


def test_detect_positions_db_falls_back_default(tmp_path, monkeypatch):
    mod = _load_module()
    base = tmp_path / "proj"
    data = base / "data"
    data.mkdir(parents=True)
    default_db = data / "positions.db"
    default_db.write_text("", encoding="utf-8")

    monkeypatch.setattr(mod, "BASE", str(base))
    monkeypatch.setattr(mod, "RUNNER_META", str(data / "missing_meta.json"))
    monkeypatch.delenv("POSITION_DB", raising=False)

    resolved = mod.detect_positions_db()
    assert Path(resolved) == default_db
