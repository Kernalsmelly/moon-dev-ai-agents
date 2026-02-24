#!/usr/bin/env python3
"""Scan snapshots for liquidity/volume spikes.

Usage:
  python scripts/meme_liquidity_spike_scan.py --input data/meme_snapshots.jsonl
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Snapshot JSONL")
    parser.add_argument("--top", type=int, default=25, help="Top spikes to report")
    parser.add_argument("--out", default="data/meme_liq_spikes.json", help="Output JSON file")
    args = parser.parse_args()

    prev = {}
    spikes = []

    with open(args.input, "r", encoding="utf-8") as fh:
        for line in fh:
            try:
                s = json.loads(line)
            except Exception:
                continue
            mint = s.get("mint")
            if not mint:
                continue
            liq = float(s.get("liquidity", 0) or 0)
            vol5 = float(s.get("volume_5m", 0) or 0)
            ts = float(s.get("ts", 0) or 0)
            sym = s.get("symbol", "")

            p = prev.get(mint)
            if p:
                dliq = liq - p["liq"]
                dvol = vol5 - p["vol5"]
                if dliq > 5000 or dvol > 1000:
                    spikes.append({
                        "ts": ts,
                        "mint": mint,
                        "symbol": sym,
                        "liq": liq,
                        "vol5": vol5,
                        "dliq": dliq,
                        "dvol5": dvol,
                    })
            prev[mint] = {"liq": liq, "vol5": vol5}

    # Rank by liquidity spike then volume spike
    spikes.sort(key=lambda x: (x["dliq"], x["dvol5"]), reverse=True)
    top = spikes[: args.top]

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(top, fh, indent=2)

    print(f"Wrote {len(top)} spikes to {args.out}")


if __name__ == "__main__":
    main()
