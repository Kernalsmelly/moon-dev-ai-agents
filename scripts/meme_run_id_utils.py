#!/usr/bin/env python3
"""Helpers to resolve current meme bot run id."""

from __future__ import annotations

import json
from pathlib import Path
import os
import sys

PROJECT_ROOT = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.meme_signal_schema import resolve_active_run_id
BASE = PROJECT_ROOT
LOG_BOT = BASE / "logs" / "meme_bot_early_edge_auto.log"
RUNS_DIR = BASE / "data" / "meme_runs"
LOG_BASE_SIMPLE = BASE / "logs" / "meme_base_simple.log"


def tail_last_matching(path: Path, needle: str, max_bytes: int = 250_000) -> str | None:
    try:
        if not path.exists():
            return None
        data = path.read_bytes()
        if len(data) > max_bytes:
            data = data[-max_bytes:]
        text = data.decode("utf-8", errors="ignore")
        lines = [ln for ln in text.splitlines() if needle in ln]
        return lines[-1] if lines else None
    except Exception:
        return None


def run_id_from_log(path: Path = LOG_BOT) -> str | None:
    ln = tail_last_matching(path, "run_id=")
    if not ln:
        return None
    try:
        parts = ln.split("run_id=", 1)
        if len(parts) != 2:
            return None
        rid = parts[1].strip()
        rid = rid.replace("[/dim]", "").strip().split()[0]
        return rid or None
    except Exception:
        return None


def run_id_from_manifest(path: Path = RUNS_DIR) -> str | None:
    try:
        if not path.exists():
            return None
        files = sorted(path.glob("run_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            return None
        obj = json.loads(files[0].read_text(encoding="utf-8"))
        rid = str(obj.get("run_id") or "").strip()
        return rid or files[0].stem or None
    except Exception:
        return None


def auto_run_id(bot_log: Path = LOG_BOT) -> str | None:
    # Primary: resolve the currently active run used by sidecars/listeners.
    rid = resolve_active_run_id(BASE)
    if rid:
        return rid
    # Backward-compatible fallback for legacy runner/report flows.
    return run_id_from_log(bot_log) or run_id_from_log(LOG_BASE_SIMPLE) or run_id_from_manifest()
