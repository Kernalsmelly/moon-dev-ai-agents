#!/usr/bin/env python3
"""Run a "swarm round": multiple lightweight diagnostics + tuners, then write a summary.

This does NOT trade. It's designed to keep iteration tight while the live PAPER pipeline runs.

Outputs:
- data/meme_swarm_round.json
- data/meme_swarm_round.md
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path


BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
OUT_JSON = BASE / "data" / "meme_swarm_round.json"
OUT_MD = BASE / "data" / "meme_swarm_round.md"


def _run(cmd: list[str], timeout_s: float = 120.0) -> dict:
    start = time.perf_counter()
    try:
        p = subprocess.run(
            cmd,
            cwd=str(BASE),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_s,
        )
        dur = round(time.perf_counter() - start, 3)
        return {"cmd": cmd, "ok": p.returncode == 0, "rc": p.returncode, "duration_s": dur, "out": (p.stdout or "")[-8000:]}
    except Exception as e:
        dur = round(time.perf_counter() - start, 3)
        return {"cmd": cmd, "ok": False, "rc": None, "duration_s": dur, "out": f"error: {e}"}


def main() -> int:
    BASE.joinpath("data").mkdir(parents=True, exist_ok=True)

    steps = []

    # Provider sanity (redacts secrets by design)
    steps.append(_run(["python3", "scripts/provider_smoke_test.py", "--env", ".env", "--timeout", "6.0"], timeout_s=60))

    # Recent reject reasons
    steps.append(_run(["python3", "scripts/meme_signal_debug_rollup.py", "--minutes", "30", "--top", "15"], timeout_s=60))

    # Recent trade results
    steps.append(_run(["python3", "scripts/meme_trade_feature_report.py", "--minutes", "120"], timeout_s=60))

    # Offline gate grid search (local outcomes only)
    steps.append(_run(["python3", "scripts/meme_swarm_grid_outcomes.py", "--lookback", "5000", "--min-samples-300", "40", "--top", "10"], timeout_s=180))

    obj = {"generated_at": time.time(), "steps": steps}
    OUT_JSON.write_text(json.dumps(obj, indent=2), encoding="utf-8")

    md = []
    md.append("# Meme Swarm Round\n")
    md.append(f"- generated_at: {time.strftime('%Y-%m-%d %H:%M:%S %Z', time.localtime(obj['generated_at']))}")
    md.append("")
    for s in steps:
        md.append(f"## {'OK' if s.get('ok') else 'FAIL'}: {' '.join(s.get('cmd') or [])}")
        md.append(f"- duration_s: {s.get('duration_s')}")
        md.append("")
        md.append("```text")
        md.append((s.get("out") or "").rstrip())
        md.append("```")
        md.append("")
    OUT_MD.write_text("\n".join(md), encoding="utf-8")

    print(f"wrote {OUT_JSON}")
    print(f"wrote {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

