#!/usr/bin/env python3
"""Print current edge status in one line (for quick sanity checks)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv


BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
EDGE = BASE / "logs" / "meme_edge_report.log"
NEXT = BASE / "data" / "next_tuning.json"

LINE_RE = re.compile(r"edge_report ts=(?P<ts>\S+)\s+run n=(?P<n>\d+) pnl=(?P<pnl>[+-]?[0-9]*\.?[0-9]+)")


def _read_last_line(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            if size <= 0:
                return None
            fh.seek(max(0, size - 4096))
            chunk = fh.read().decode("utf-8", errors="ignore")
        lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
        return lines[-1] if lines else None
    except Exception:
        return None


def main() -> int:
    load_dotenv(dotenv_path=str(BASE / ".env"), override=True)
    last = _read_last_line(EDGE)
    if not last:
        print("edge_status: no edge report log yet")
        return 0
    m = LINE_RE.search(last)
    if not m:
        print("edge_status: could not parse last line")
        print(last)
        return 0
    ts = m.group("ts")
    n = int(m.group("n"))
    pnl = float(m.group("pnl"))
    nxt = "missing"
    why = ""
    if NEXT.exists():
        try:
            obj = json.loads(NEXT.read_text(encoding="utf-8"))
            ch = (obj.get("change") or {}) if isinstance(obj, dict) else {}
            why = str(obj.get("why") or "") if isinstance(obj, dict) else ""
            nxt = f"present change_keys={list(ch)[:5]}"
        except Exception:
            nxt = "present (unreadable)"
    thr = int(os.getenv("EDGE_DECIDER_MIN_RUN_EXITS", "30") or 30)
    print(f"edge_status ts={ts} run_n={n}/{thr} run_pnl={pnl:+.2f} next_tuning={nxt} {why[:120]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
