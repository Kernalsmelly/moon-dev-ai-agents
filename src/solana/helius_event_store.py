"""Simple JSONL event store for Helius websocket logs."""
from __future__ import annotations

import json
import os
import time
from typing import Any


def append_event(event: dict[str, Any], path: str | None = None) -> None:
    out_path = path or os.getenv("HELIUS_EVENTS_FILE", "data/helius_events.jsonl")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    payload = dict(event)
    payload.setdefault("ts", time.time())
    try:
        with open(out_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload) + "\n")
    except Exception:
        pass
