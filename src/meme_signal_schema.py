"""Shared helpers for meme signal payload normalization and run attribution."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Mapping


RUN_ID_RE = re.compile(r"run_id=([A-Za-z0-9_:-]+)")


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        return float(value)
    except Exception:
        return None


def _coalesce(source: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in source and source.get(key) is not None:
            return source.get(key)
    return None


def normalize_signal_metrics(metrics: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a canonical metrics dict while preserving original keys."""
    out: dict[str, Any] = dict(metrics or {})

    mcap = _to_float(_coalesce(out, ("market_cap_usd", "market_cap", "mcap", "fdv")))
    if mcap is not None:
        out["market_cap"] = mcap
        out["market_cap_usd"] = mcap

    liq = _to_float(_coalesce(out, ("liquidity_usd", "liquidity", "liq")))
    if liq is not None:
        out["liquidity"] = liq
        out["liquidity_usd"] = liq

    mom5 = _to_float(_coalesce(out, ("price_change_5m", "momentum_5m_pct")))
    if mom5 is not None:
        out["price_change_5m"] = mom5
        out["momentum_5m_pct"] = mom5

    mom1h = _to_float(_coalesce(out, ("price_change_1h", "momentum_1h_pct")))
    if mom1h is not None:
        out["price_change_1h"] = mom1h
        out["momentum_1h_pct"] = mom1h

    score = _to_float(out.get("score"))
    if score is not None:
        out["score"] = score

    return out


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _tail_run_id(path: Path, max_bytes: int = 200_000) -> str | None:
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return None
        data = path.read_bytes()
        if len(data) > max_bytes:
            data = data[-max_bytes:]
        text = data.decode("utf-8", errors="ignore")
        lines = text.splitlines()
        for line in reversed(lines):
            m = RUN_ID_RE.search(line)
            if m:
                rid = m.group(1).strip()
                if rid:
                    return rid
    except Exception:
        return None
    return None


def resolve_active_run_id(project_root: str | Path | None = None) -> str | None:
    """Best-effort run id resolution for sidecar processes."""
    env_run_id = str(os.getenv("MEME_RUN_ID", "") or "").strip()
    if env_run_id:
        return env_run_id

    root = Path(project_root) if project_root else Path(__file__).resolve().parents[1]
    meta_path = root / "data" / "meme_base_simple_runner.json"
    try:
        if meta_path.exists():
            obj = json.loads(meta_path.read_text(encoding="utf-8"))
            rid = str((obj or {}).get("run_id") or "").strip()
            pid = int((obj or {}).get("pid") or 0)
            if rid and (pid <= 0 or _alive(pid)):
                return rid
    except Exception:
        pass

    log_candidates = [
        root / "logs" / "meme_base_simple.log",
        root / "logs" / "meme_bot_early_edge_auto.log",
    ]
    for path in log_candidates:
        rid = _tail_run_id(path)
        if rid:
            return rid
    return None


def build_launch_signal_payload(
    *,
    mint: str,
    metrics: Mapping[str, Any] | None,
    score: float,
    ts: float | None = None,
    first_seen: float | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Build a canonical launch-signal payload used across listeners."""
    now = float(ts if ts is not None else time.time())
    first = float(first_seen if first_seen is not None else now)
    m = normalize_signal_metrics(metrics)
    rid = str(run_id or resolve_active_run_id() or "").strip()

    payload: dict[str, Any] = {
        "ts": now,
        "first_seen": first,
        "mint": mint,
        "score": round(float(score or 0.0), 2),
        "schema_version": 2,
        "metrics": m,
    }
    if rid:
        payload["run_id"] = rid
    src = m.get("source")
    if isinstance(src, str) and src:
        payload["source"] = src
    return payload
