#!/usr/bin/env python3
"""Filter launch candidates to likely meme mints.

Heuristics:
- Drop known base mints (SOL/USDC/USDT)
- Prefer mints ending with 'pump' or seen with pump.fun program
"""
from __future__ import annotations

import argparse
import json
import os

SOL_MINT = "So11111111111111111111111111111111111111112"
WSOL_MINT = "So11111111111111111111111111111111111111111"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"

PUMP_PROGRAMS = {
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/meme_launch_candidates.jsonl", help="Candidates JSONL")
    parser.add_argument("--out", default="data/meme_launch_mints.jsonl", help="Filtered mints JSONL")
    parser.add_argument("--state", default="data/meme_launch_filter_state.json", help="State file for incremental read")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"No input file: {args.input}")
        return

    # incremental state: last byte offset + seen mints
    offset = 0
    seen = set()
    if os.path.exists(args.state):
        try:
            with open(args.state, "r", encoding="utf-8") as fh:
                st = json.load(fh)
                offset = int(st.get("offset", 0))
                for m in st.get("seen", []):
                    seen.add(m)
        except Exception:
            offset = 0
            seen = set()

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
                obj = json.loads(line)
            except Exception:
                continue
            mints = obj.get("mints") or []
            pid = obj.get("program_id")
            for mint in mints:
                if mint in (SOL_MINT, WSOL_MINT, USDC_MINT, USDT_MINT):
                    continue
                if mint in seen:
                    continue
                # prefer pump.fun tags or pump suffix
                if (pid in PUMP_PROGRAMS) or mint.endswith("pump"):
                    out.write(json.dumps({
                        "ts": obj.get("ts"),
                        "mint": mint,
                        "program_id": pid,
                        "signature": obj.get("signature"),
                    }) + "\n")
                    seen.add(mint)
                    emitted += 1

    try:
        with open(args.state, "w", encoding="utf-8") as fh:
            json.dump({"offset": offset, "seen": list(seen)[-5000:]}, fh)
    except Exception:
        pass

    print(f"Emitted {emitted} filtered mints.")


if __name__ == "__main__":
    main()
