#!/usr/bin/env python3
"""Periodic signal monitor: counts + rates + latency summary."""
from __future__ import annotations

import argparse
import json
import os
import time
from statistics import mean


def load_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--signals", default="data/meme_launch_signals.jsonl")
    parser.add_argument("--mints", default="data/meme_launch_mints.jsonl")
    parser.add_argument("--out", default="data/meme_signal_stats.jsonl")
    parser.add_argument("--window-hours", type=int, default=1)
    args = parser.parse_args()

    now = time.time()
    cutoff = now - (args.window_hours * 3600)
    signals = [s for s in load_jsonl(args.signals) if float(s.get("ts", 0) or 0) >= cutoff]
    mints = [m for m in load_jsonl(args.mints) if float(m.get("ts", 0) or 0) >= cutoff]

    # latency: signal ts - first_seen
    latencies = []
    for s in signals:
        ts = float(s.get("ts", 0) or 0)
        first_seen = float(s.get("first_seen", 0) or 0)
        if ts and first_seen and ts >= first_seen:
            latencies.append(ts - first_seen)

    payload = {
        "ts": now,
        "window_hours": args.window_hours,
        "mints": len(mints),
        "signals": len(signals),
        "signal_rate_per_hour": round(len(signals) / max(1, args.window_hours), 2),
        "latency_avg_sec": round(mean(latencies), 2) if latencies else None,
        "latency_p50_sec": round(sorted(latencies)[len(latencies)//2], 2) if latencies else None,
    }

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload) + "\n")

    print(json.dumps(payload))


if __name__ == "__main__":
    main()
