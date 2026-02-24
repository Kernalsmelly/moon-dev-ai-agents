#!/usr/bin/env python3
"""Append a short session note to codex_scratchpad.md.

Goal: keep momentum by writing down what we observed and what we changed
without relying on memory across long sessions.

This is intentionally lightweight and safe:
- Does not print secrets (tries to avoid raw URLs with keys)
- Appends an entry; does not rewrite existing content
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path


BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
SCRATCH = BASE / "codex_scratchpad.md"
SMOKE = BASE / "data" / "provider_smoke_test.json"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime())


def _safe_provider_summary() -> str:
    if not SMOKE.exists():
        return "provider_smoke_test: missing"
    try:
        obj = json.loads(SMOKE.read_text(encoding="utf-8"))
        rpc = obj.get("rpc", {}) if isinstance(obj, dict) else {}
        if not isinstance(rpc, dict):
            return "provider_smoke_test: unreadable"
        ok = 0
        fail = 0
        fails: list[str] = []
        for k, v in rpc.items():
            if not isinstance(v, dict):
                continue
            if v.get("ok"):
                ok += 1
            else:
                fail += 1
                err = v.get("error")
                if err:
                    fails.append(f"{k}:{str(err)[:40]}")
        tail = ", ".join(fails[:6])
        return f"provider_smoke_test: rpc_ok={ok} fail={fail} fails=({tail})"
    except Exception:
        return "provider_smoke_test: error"


def _safe_changed_files() -> list[str]:
    # Works even in a dirty tree; avoids printing diffs.
    try:
        out = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=str(BASE),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        files: list[str] = []
        for ln in out.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            # format: "XY path"
            parts = ln.split(maxsplit=1)
            if len(parts) == 2:
                files.append(parts[1])
        return files[:20]
    except Exception:
        return []


def main() -> int:
    SCRATCH.parent.mkdir(parents=True, exist_ok=True)
    if not SCRATCH.exists():
        SCRATCH.write_text("# Codex Scratchpad\n\n", encoding="utf-8")

    changed = _safe_changed_files()
    prov = _safe_provider_summary()
    entry = []
    entry.append(f"\n- Date: {_now_iso()}")
    entry.append("- What went wrong (1-3):")
    entry.append(f"- {prov}")
    entry.append("- What we changed (1-5):")
    entry.append("- Session start (auto log).")
    entry.append("- What we learned (1-3):")
    entry.append("- (fill in)")
    entry.append("- What's next (1-5):")
    entry.append("- (fill in)")
    entry.append("- Files touched:")
    if changed:
        for f in changed:
            entry.append(f"- {f}")
    else:
        entry.append("- (none detected)")
    entry.append("- Processes running:")
    entry.append("- meme_pipeline_supervisor + children")

    with open(SCRATCH, "a", encoding="utf-8") as fh:
        fh.write("\n".join(entry) + "\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

