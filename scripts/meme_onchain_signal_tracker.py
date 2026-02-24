#!/usr/bin/env python3
"""Generate launch signals from on-chain WS events only.

Consumes meme_launch_mints.jsonl and emits a signal per new mint.
"""
from __future__ import annotations

import argparse
import json
import os
import time
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/meme_launch_mints.jsonl", help="Filtered mints JSONL")
    parser.add_argument("--out", default="data/meme_launch_signals.jsonl", help="Output signals JSONL")
    parser.add_argument("--state", default="data/meme_onchain_signal_state.json", help="State file for incremental read")
    parser.add_argument("--poll", type=int, default=5, help="Polling interval")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"No input file: {args.input}")
        return

    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    # incremental state
    offset = 0
    emitted = set()
    if os.path.exists(args.state):
        try:
            with open(args.state, "r", encoding="utf-8") as fh:
                st = json.load(fh)
                offset = int(st.get("offset", 0))
                for m in st.get("emitted", []):
                    emitted.add(m)
        except Exception:
            offset = 0
            emitted = set()

    last_status = 0.0
    with open(args.input, "r", encoding="utf-8") as fh:
        if offset:
            try:
                if os.path.getsize(args.input) < offset:
                    # file was truncated; reset offset to read new lines
                    offset = 0
                fh.seek(offset)
            except Exception:
                offset = 0

        while True:
            # handle truncation mid-run
            try:
                if os.path.getsize(args.input) < offset:
                    offset = 0
                    fh.seek(0)
            except Exception:
                pass
            line = fh.readline()
            if line:
                offset = fh.tell()
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                mint = obj.get("mint")
                if not mint or mint in emitted:
                    continue
                if not mint.endswith("pump"):
                    continue
                now = time.time()
                pid = obj.get("program_id")
                score = 1.0
                if isinstance(mint, str) and mint.endswith("pump"):
                    score += 1.0
                if pid:
                    score += 0.5
                with open(args.out, "a", encoding="utf-8") as out:
                    out.write(json.dumps({
                        "ts": now,
                        "mint": mint,
                        "first_seen": obj.get("ts") or now,
                        "metrics": {
                            "program_id": pid,
                            "source": "onchain_mints",
                        },
                        "score": round(score, 3),
                    }) + "\n")
                emitted.add(mint)

            if time.time() - last_status > 30:
                last_status = time.time()
                print(f"Status: emitted={len(emitted)} offset={offset}", flush=True)

            # persist state
            try:
                with open(args.state, "w", encoding="utf-8") as sf:
                    json.dump({"offset": offset, "emitted": list(emitted)[-5000:]}, sf)
            except Exception:
                pass

            time.sleep(max(1, args.poll))


if __name__ == "__main__":
    main()
