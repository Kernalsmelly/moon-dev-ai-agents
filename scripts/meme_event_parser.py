#!/usr/bin/env python3
"""Parse Helius log events into simple launch signals.

This is a heuristic parser that looks for program log hits and emits a
lightweight JSONL signal stream you can iterate on.

Usage:
  python scripts/meme_event_parser.py --input data/helius_events.jsonl --out data/meme_launch_events.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/helius_events.jsonl", help="Helius events JSONL")
    parser.add_argument("--out", default="data/meme_launch_events.jsonl", help="Output JSONL")
    parser.add_argument("--since", type=float, default=0.0, help="Only process events after this unix ts")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"No input file: {args.input}")
        return

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    seen = 0
    emitted = 0

    with open(args.input, "r", encoding="utf-8") as fh, open(args.out, "a", encoding="utf-8") as out:
        for line in fh:
            try:
                evt = json.loads(line)
            except Exception:
                continue
            seen += 1
            ts = float(evt.get("ts", 0) or 0)
            if ts <= args.since:
                continue

            pid = evt.get("program_id")
            logs = evt.get("logs") or []
            sig = evt.get("signature")
            err = evt.get("err")

            # Basic heuristic: any log notify from target program is a potential launch event
            signal = {
                "ts": ts,
                "program_id": pid,
                "signature": sig,
                "err": err,
                "log_lines": logs[:5],
            }
            out.write(json.dumps(signal) + "\n")
            emitted += 1

    print(f"Processed {seen} events, emitted {emitted} signals.")


if __name__ == "__main__":
    main()
