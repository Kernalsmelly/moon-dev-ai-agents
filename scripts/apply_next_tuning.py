#!/usr/bin/env python3
"""Apply data/next_tuning.json by updating .env, then print what changed.

This is designed to keep iteration disciplined:
- next_tuning.json contains exactly one (or zero) knob changes
- we apply only those keys
- we do not print secret values (best effort redaction)
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any


BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
NEXT = BASE / "data" / "next_tuning.json"
ENV_PATH = BASE / ".env"
EDGE_RUN_STATE = BASE / "data" / "edge_run_state.json"

SENSITIVE_KEYS = {
    "API_KEY",
    "KEY",
    "SECRET",
    "PRIVATE",
    "TOKEN",
    "WEBHOOK",
    "PASSWORD",
}


def _is_sensitive(k: str) -> bool:
    ku = k.upper()
    return any(s in ku for s in SENSITIVE_KEYS)


def _redact(v: Any) -> str:
    s = str(v)
    if len(s) <= 8:
        return "***"
    return s[:3] + "***" + s[-3:]


ENV_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$")


def _load_env_lines() -> list[str]:
    if not ENV_PATH.exists():
        return []
    return ENV_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()


def _write_env_lines(lines: list[str]) -> None:
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    if not NEXT.exists():
        print("missing data/next_tuning.json", flush=True)
        return 2
    obj = json.loads(NEXT.read_text(encoding="utf-8"))
    gen = None
    try:
        gen = float(obj.get("generated_at")) if isinstance(obj, dict) and obj.get("generated_at") is not None else None
    except Exception:
        gen = None
    change = obj.get("change") if isinstance(obj, dict) else None
    if not isinstance(change, dict):
        print("next_tuning.json has no change dict", flush=True)
        return 2
    if not change:
        print("next_tuning.json change is empty (hold settings)", flush=True)
        return 0

    lines = _load_env_lines()
    idx: dict[str, int] = {}
    for i, ln in enumerate(lines):
        m = ENV_RE.match(ln)
        if not m:
            continue
        idx[m.group(1)] = i

    applied = []
    for k, v in change.items():
        if not isinstance(k, str) or not k:
            continue
        new_line = f"{k}={v}"
        if k in idx:
            old = lines[idx[k]]
            lines[idx[k]] = new_line
            applied.append((k, old, new_line))
        else:
            lines.append(new_line)
            applied.append((k, None, new_line))

    _write_env_lines(lines)

    print("applied env changes:", flush=True)
    for k, old, new in applied:
        if _is_sensitive(k):
            o = _redact(old) if old is not None else "(new)"
            n = _redact(new)
            print(f"  {k}: {o} -> {n}", flush=True)
        else:
            print(f"  {k}: {old or '(new)'} -> {new}", flush=True)

    # Archive the proposal so the supervisor doesn't re-apply it repeatedly.
    try:
        hist = BASE / "data" / "tuning_history"
        hist.mkdir(parents=True, exist_ok=True)
        tag = f"{gen:.0f}" if isinstance(gen, (int, float)) and gen else f"{time.time():.0f}"
        dst = hist / f"next_tuning_{tag}.json"
        dst.write_text(json.dumps(obj, indent=2), encoding="utf-8")
        NEXT.unlink(missing_ok=True)
    except Exception:
        # If archiving fails, don't fail the apply.
        pass

    # Reset the "run window" used by the edge reporter/decider so the next proposal
    # is based only on trades under the new settings.
    try:
        EDGE_RUN_STATE.write_text(
            json.dumps(
                {
                    "run_started_ts": time.time(),
                    "applied_from": f"next_tuning_{tag}",
                    "change_keys": list(change.keys()),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
