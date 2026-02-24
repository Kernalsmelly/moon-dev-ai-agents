#!/usr/bin/env python3
"""Normalize meme signal/debug JSONL files and optionally rewrite in place."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from src.meme_signal_schema import normalize_signal_metrics, resolve_active_run_id


def _iter_jsonl(path: Path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                yield i, json.loads(raw), None
            except Exception as exc:  # pragma: no cover - best-effort fixer
                yield i, None, str(exc)


def _fix_signal_row(row: dict[str, Any], default_run_id: str | None) -> dict[str, Any]:
    out = dict(row)
    ts = out.get("ts")
    if not isinstance(ts, (int, float)):
        ts = time.time()
    out["ts"] = float(ts)
    if not isinstance(out.get("first_seen"), (int, float)):
        out["first_seen"] = float(out["ts"])
    if not out.get("run_id") and default_run_id:
        out["run_id"] = default_run_id
    metrics = out.get("metrics")
    out["metrics"] = normalize_signal_metrics(metrics if isinstance(metrics, dict) else {})
    src = out["metrics"].get("source")
    if src and not out.get("source"):
        out["source"] = src
    out["schema_version"] = 2
    return out


def _fix_debug_row(row: dict[str, Any], default_run_id: str | None) -> dict[str, Any]:
    out = dict(row)
    ts = out.get("ts")
    if not isinstance(ts, (int, float)):
        ts = time.time()
    out["ts"] = float(ts)
    if not out.get("run_id") and default_run_id:
        out["run_id"] = default_run_id
    out["schema_version"] = 2
    return out


def _rewrite(
    path: Path,
    mode: str,
    default_run_id: str | None,
    in_place: bool,
) -> tuple[Path, int, int]:
    bad = 0
    ok = 0
    fixed_rows: list[dict[str, Any]] = []
    for _, row, err in _iter_jsonl(path):
        if err or not isinstance(row, dict):
            bad += 1
            continue
        if mode == "signals":
            fixed_rows.append(_fix_signal_row(row, default_run_id))
        else:
            fixed_rows.append(_fix_debug_row(row, default_run_id))
        ok += 1

    if in_place:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        backup = path.with_suffix(path.suffix + f".bak.{stamp}")
        shutil.copy2(path, backup)
        out_path = path
    else:
        out_path = path.with_suffix(path.suffix + ".normalized")

    with open(out_path, "w", encoding="utf-8") as out:
        for row in fixed_rows:
            out.write(json.dumps(row, separators=(",", ":")) + "\n")
    return out_path, ok, bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", default=str(BASE / "data" / "meme_launch_signals.jsonl"))
    ap.add_argument("--debug", default=str(BASE / "data" / "meme_signal_debug.jsonl"))
    ap.add_argument("--in-place", action="store_true")
    ap.add_argument("--fill-run-id", action="store_true")
    args = ap.parse_args()

    default_run_id = resolve_active_run_id(BASE) if args.fill_run_id else None

    sig_path = Path(args.signals)
    dbg_path = Path(args.debug)
    if not sig_path.exists():
        raise SystemExit(f"signals file missing: {sig_path}")
    if not dbg_path.exists():
        raise SystemExit(f"debug file missing: {dbg_path}")

    sig_out, sig_ok, sig_bad = _rewrite(sig_path, "signals", default_run_id, args.in_place)
    dbg_out, dbg_ok, dbg_bad = _rewrite(dbg_path, "debug", default_run_id, args.in_place)

    print(
        f"signals: out={sig_out} rows={sig_ok} dropped_bad={sig_bad}\n"
        f"debug:   out={dbg_out} rows={dbg_ok} dropped_bad={dbg_bad}\n"
        f"fill_run_id={bool(default_run_id)} run_id={default_run_id or 'n/a'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
