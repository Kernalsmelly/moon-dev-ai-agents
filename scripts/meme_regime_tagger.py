#!/usr/bin/env python3
"""Tag snapshot windows as hot/cold regimes based on aggregate momentum.

Usage:
  python scripts/meme_regime_tagger.py --input data/meme_snapshots.jsonl --out data/meme_regime_tags.json
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Snapshot JSONL")
    parser.add_argument("--out", default="data/meme_regime_tags.json", help="Output JSON")
    parser.add_argument("--window", type=int, default=300, help="Seconds per regime window")
    parser.add_argument("--min-avg-5m", type=float, default=2.0, help="Min avg 5m price change for hot")
    parser.add_argument("--min-avg-1h", type=float, default=5.0, help="Min avg 1h price change for hot")
    parser.add_argument("--min-avg-txns", type=float, default=50.0, help="Min avg 1h txns for hot")
    args = parser.parse_args()

    buckets = defaultdict(list)
    with open(args.input, "r", encoding="utf-8") as fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            ts = int(float(obj.get("ts", 0)))
            bucket = ts - (ts % args.window)
            buckets[bucket].append(obj)

    tags = []
    for bucket, items in sorted(buckets.items()):
        if not items:
            continue
        avg_5m = sum(float(x.get("price_change_5m", 0) or 0) for x in items) / len(items)
        avg_1h = sum(float(x.get("price_change_1h", 0) or 0) for x in items) / len(items)
        avg_tx = sum(int(x.get("txns_1h", 0) or 0) for x in items) / len(items)
        # simple regime heuristic
        hot = (avg_5m >= args.min_avg_5m and avg_1h >= args.min_avg_1h and avg_tx >= args.min_avg_txns)
        tags.append({
            "window_start": bucket,
            "window_end": bucket + args.window,
            "avg_5m": round(avg_5m, 2),
            "avg_1h": round(avg_1h, 2),
            "avg_txns_1h": round(avg_tx, 1),
            "regime": "hot" if hot else "cold",
        })

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(tags, fh, indent=2)

    print(f"Wrote {len(tags)} regime tags to {args.out}")


if __name__ == "__main__":
    main()
