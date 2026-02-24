#!/usr/bin/env python3
"""Heuristic launch detector from Helius logs.

Reads helius_events.jsonl and emits detected launch candidates to JSONL.
This is intentionally lightweight and can be refined over time.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time


BASE58_RE = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")

# Heuristic keywords for launch-related logs
PUMP_KEYWORDS = ("pump", "initialize", "mint", "create", "launch")
RAYDIUM_KEYWORDS = ("initialize", "create", "pool", "amm", "openbook")


def extract_mints_from_logs(
    logs: list[str],
    only_if_keywords: tuple[str, ...] | None = None,
    require_mint_keyword: bool = True,
) -> list[str]:
    candidates: list[str] = []
    if only_if_keywords:
        joined = " ".join(logs).lower()
        if not any(k in joined for k in only_if_keywords):
            return []
    for line in logs:
        if require_mint_keyword and ("mint" not in line.lower()):
            continue
        for m in BASE58_RE.findall(line):
            candidates.append(m)
    # de-dupe while preserving order
    seen = set()
    out = []
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/helius_events.jsonl", help="Helius events JSONL")
    parser.add_argument("--out", default="data/meme_launch_candidates.jsonl", help="Output JSONL")
    parser.add_argument("--state", default="data/meme_launch_detector_state.json", help="State file for incremental read")
    parser.add_argument("--since", type=float, default=0.0, help="Only process events after this unix ts")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"No input file: {args.input}")
        return

    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    # incremental state: last byte offset
    offset = 0
    if os.path.exists(args.state):
        try:
            with open(args.state, "r", encoding="utf-8") as fh:
                offset = int(json.load(fh).get("offset", 0))
        except Exception:
            offset = 0

    emitted = 0
    with open(args.input, "r", encoding="utf-8") as fh, open(args.out, "a", encoding="utf-8") as out:
        if offset:
            try:
                fh.seek(offset)
            except Exception:
                offset = 0
        while True:
            line = fh.readline()
            if not line:
                break
            offset = fh.tell()
            try:
                evt = json.loads(line)
            except Exception:
                continue
            ts = float(evt.get("ts", 0) or 0)
            if ts <= args.since:
                continue
            logs = evt.get("logs") or []
            pid = evt.get("program_id")
            # Program-specific keyword gating
            if pid in ("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P", "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"):
                # Pump logs often include the mint directly; look for "pump"
                mints = extract_mints_from_logs(logs, ("pump",), require_mint_keyword=False)
            elif pid in ("675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8", "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C", "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK", "LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj"):
                mints = extract_mints_from_logs(logs, RAYDIUM_KEYWORDS, require_mint_keyword=False)
            else:
                mints = extract_mints_from_logs(logs)
            if not mints:
                continue
            signal = {
                "ts": ts,
                "program_id": pid,
                "signature": evt.get("signature"),
                "mints": mints,
                "log_sample": logs[:5],
            }
            out.write(json.dumps(signal) + "\n")
            emitted += 1

    with open(args.state, "w", encoding="utf-8") as fh:
        json.dump({"offset": offset, "updated": time.time()}, fh)

    print(f"Emitted {emitted} candidates.")


if __name__ == "__main__":
    main()
