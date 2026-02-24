#!/usr/bin/env python3
"""
Roll up signal-first debug events to quickly see why we're rejecting candidates.

Reads: data/meme_signal_debug.jsonl
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def _auto_run_id() -> str:
    """Best-effort run_id detection from bot log tail."""
    log_path = Path("logs/meme_bot_early_edge_auto.log")
    if not log_path.exists():
        return ""
    try:
        data = log_path.read_bytes()
        if len(data) > 250_000:
            data = data[-250_000:]
        text = data.decode("utf-8", errors="ignore")
        lines = [ln for ln in text.splitlines() if "run_id=" in ln]
        if not lines:
            return ""
        rid = lines[-1].split("run_id=", 1)[1].strip()
        rid = rid.replace("[/dim]", "").split()[0].strip()
        return rid
    except Exception:
        return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="data/meme_signal_debug.jsonl")
    ap.add_argument("--minutes", type=int, default=30)
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--run-id", default="", help="optional: filter to a specific run_id")
    ap.add_argument("--auto-run-id", action="store_true", help="auto-detect run_id from bot log/manifest")
    args = ap.parse_args()

    run_id = str(args.run_id or "").strip()
    if args.auto_run_id and not run_id:
        run_id = _auto_run_id()

    cutoff = time.time() - args.minutes * 60

    kinds = Counter()
    kinds_by_detail: dict[str, Counter] = defaultdict(Counter)
    n = 0
    n_cut = 0

    try:
        with open(args.file, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                n += 1
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if run_id and str(obj.get("run_id") or "").strip() != run_id:
                    continue
                ts = obj.get("ts")
                try:
                    ts_f = float(ts)
                except Exception:
                    continue
                if ts_f < cutoff:
                    continue
                n_cut += 1
                kind = str(obj.get("kind") or "unknown")
                kinds[kind] += 1
                extra = obj.get("extra") or {}
                if isinstance(extra, dict):
                    # Capture a small amount of structured context for the top reasons.
                    if kind in ("reject_sig_score", "reject_net_sol", "reject_mcap_low", "reject_impact"):
                        for k in ("sig_score", "min_sig_score", "net_sol_in", "min_net_sol_in", "mcap", "min_mcap", "impact", "max_impact"):
                            if k in extra and extra.get(k) is not None:
                                kinds_by_detail[kind][f"{k}={extra.get(k)}"] += 1
                                break

    except FileNotFoundError:
        print(f"missing {args.file}")
        return 0

    if run_id:
        print(f"run_id={run_id}")
    print(f"events_total={n} events_window={n_cut} window={args.minutes}m")
    print("\nTop kinds:")
    for k, c in kinds.most_common(args.top):
        print(f"{k:28s} {c:6d}")

    # Print a small amount of detail for a few key reject causes.
    for k in ("reject_sig_score", "reject_net_sol", "reject_mcap_low", "reject_impact"):
        if k not in kinds:
            continue
        det = kinds_by_detail.get(k)
        if not det:
            continue
        print(f"\n{k} details:")
        for d, c in det.most_common(10):
            print(f"  {d:24s} {c:6d}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
