#!/usr/bin/env python3
"""Quick analysis for data/signal_outcomes.jsonl.

Goal: make strategy iteration cheap. This script does not attempt to be a full backtester.
It summarizes forward returns by horizon and (optionally) by impact buckets.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median
from typing import Iterable


@dataclass
class Stats:
    rets: list[float]
    impacts0: list[float]
    impacts1: list[float]


def _pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    ys = sorted(xs)
    if len(ys) == 1:
        return ys[0]
    k = (len(ys) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return ys[int(k)]
    d0 = ys[f] * (c - k)
    d1 = ys[c] * (k - f)
    return d0 + d1


def _iter_lines(path: Path, lookback: int) -> Iterable[dict]:
    # Lightweight tail-ish behavior: read full file if it's small, otherwise read last N lines.
    data = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    if lookback > 0:
        data = data[-lookback:]
    for ln in data:
        ln = ln.strip()
        if not ln:
            continue
        try:
            yield json.loads(ln)
        except Exception:
            continue


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="data/signal_outcomes.jsonl")
    ap.add_argument("--lookback", type=int, default=5000, help="Use last N lines (0 = all).")
    ap.add_argument("--min-hits", type=int, default=0)
    ap.add_argument("--min-buys", type=int, default=0)
    ap.add_argument("--min-net-sol-in", type=float, default=0.0)
    ap.add_argument("--min-unique-buyers", type=int, default=0)
    ap.add_argument("--min-buy-accel", type=float, default=0.0, help="Filter when metric is present (0 disables).")
    ap.add_argument("--max-top-buyer-share", type=float, default=0.0, help="Filter when metric is present (0 disables).")
    ap.add_argument(
        "--roundtrip-cost-pct",
        type=float,
        default=0.0,
        help="Conservative round-trip cost as a fraction (e.g. 0.02 = -2%%). Applied to all returns.",
    )
    ap.add_argument(
        "--mcap-buckets",
        default="0,25000,50000,100000,200000,500000,1000000,2000000,5000000",
        help="Comma-separated marketcap0 cutoffs (USD) for bucket summary (0 disables).",
    )
    ap.add_argument(
        "--impact-buckets",
        default="0.01,0.02,0.05,0.10,0.20,0.35",
        help="Comma-separated impact0 cutoffs for bucket summary.",
    )
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"missing file: {path}")

    buckets = []
    for x in str(args.impact_buckets).split(","):
        x = x.strip()
        if not x:
            continue
        try:
            buckets.append(float(x))
        except Exception:
            pass
    buckets = sorted(set(buckets))

    mcap_buckets = []
    for x in str(args.mcap_buckets).split(","):
        x = x.strip()
        if not x:
            continue
        try:
            mcap_buckets.append(float(x))
        except Exception:
            pass
    mcap_buckets = sorted(set(mcap_buckets))
    # If the first bucket is 0, interpret as "disabled unless explicitly provided".
    if mcap_buckets and mcap_buckets[0] == 0:
        mcap_buckets = mcap_buckets[1:]

    by_h: dict[int, Stats] = defaultdict(lambda: Stats(rets=[], impacts0=[], impacts1=[]))
    total = 0
    kept = 0
    missing_metrics = 0
    rows_with_mcap = 0
    for obj in _iter_lines(path, args.lookback):
        try:
            h = int(obj.get("horizon_s"))
            r = float(obj.get("ret"))
        except Exception:
            continue
        m = obj.get("metrics") or {}
        if not isinstance(m, dict):
            m = {}
        if not m:
            missing_metrics += 1
        try:
            hits = int(m.get("hits") or 0)
            buys = int(m.get("buys") or 0)
            net_sol_in = float(m.get("net_sol_in") or 0.0)
            uniq = int(m.get("unique_buyers") or 0)
        except Exception:
            hits = 0
            buys = 0
            net_sol_in = 0.0
            uniq = 0

        if args.min_hits and hits < args.min_hits:
            total += 1
            continue
        if args.min_buys and buys < args.min_buys:
            total += 1
            continue
        if args.min_net_sol_in and net_sol_in < args.min_net_sol_in:
            total += 1
            continue
        if args.min_unique_buyers and uniq < args.min_unique_buyers:
            total += 1
            continue
        if args.min_buy_accel:
            try:
                ba = m.get("buy_accel")
                if ba is not None and float(ba) < args.min_buy_accel:
                    total += 1
                    continue
            except Exception:
                pass
        if args.max_top_buyer_share:
            try:
                ts = m.get("top_buyer_share")
                if ts is not None and float(ts) > args.max_top_buyer_share:
                    total += 1
                    continue
            except Exception:
                pass
        i0 = float(obj.get("impact0") or 0.0)
        i1 = float(obj.get("impact1") or 0.0)
        # Adjust returns by a conservative "round-trip cost" factor to avoid paper optimism.
        if args.roundtrip_cost_pct:
            c = max(0.0, float(args.roundtrip_cost_pct))
            r = ((1.0 + r) * (1.0 - c)) - 1.0
        s = by_h[h]
        s.rets.append(r)
        s.impacts0.append(i0)
        s.impacts1.append(i1)
        total += 1
        kept += 1
        if obj.get("marketcap0") is not None:
            rows_with_mcap += 1

    print(
        f"file={path} lookback={args.lookback} rows={total} kept={kept} "
        f"missing_metrics={missing_metrics} rows_with_marketcap0={rows_with_mcap}"
    )
    print()

    for h in sorted(by_h):
        s = by_h[h]
        xs = s.rets
        if not xs:
            continue
        win = sum(1 for x in xs if x > 0)
        lose = sum(1 for x in xs if x <= 0)
        print(
            f"h={h:4d}s n={len(xs):5d} winrate={win/len(xs):6.1%} "
            f"mean={mean(xs):+.4f} median={median(xs):+.4f} "
            f"p05={_pct(xs,5):+.4f} p95={_pct(xs,95):+.4f} "
            f"impact0_med={median(s.impacts0):.4f}"
        )

        if buckets:
            # impact0 buckets: <=b
            prev = None
            for b in buckets:
                ys = [r for (r, i0) in zip(xs, s.impacts0) if i0 <= b]
                if not ys:
                    continue
                w = sum(1 for r in ys if r > 0)
                print(
                    f"  impact0<= {b:0.3f}: n={len(ys):5d} winrate={w/len(ys):6.1%} mean={mean(ys):+.4f} median={median(ys):+.4f}"
                )
                prev = b

        if mcap_buckets:
            # marketcap0 buckets, only over rows that actually have marketcap0.
            mrets = []
            mcaps = []
            for obj in _iter_lines(path, args.lookback):
                try:
                    if int(obj.get("horizon_s")) != h:
                        continue
                    r = float(obj.get("ret"))
                    mc = obj.get("marketcap0")
                    if mc is None:
                        continue
                    mc = float(mc)
                except Exception:
                    continue
                # Apply the same metric filters for apples-to-apples comparisons.
                m = obj.get("metrics") or {}
                if not isinstance(m, dict):
                    m = {}
                try:
                    hits = int(m.get("hits") or 0)
                    buys = int(m.get("buys") or 0)
                    net_sol_in = float(m.get("net_sol_in") or 0.0)
                    uniq = int(m.get("unique_buyers") or 0)
                except Exception:
                    hits = 0
                    buys = 0
                    net_sol_in = 0.0
                    uniq = 0
                if args.min_hits and hits < args.min_hits:
                    continue
                if args.min_buys and buys < args.min_buys:
                    continue
                if args.min_net_sol_in and net_sol_in < args.min_net_sol_in:
                    continue
                if args.min_unique_buyers and uniq < args.min_unique_buyers:
                    continue
                if args.min_buy_accel:
                    try:
                        ba = m.get("buy_accel")
                        if ba is not None and float(ba) < args.min_buy_accel:
                            continue
                    except Exception:
                        pass
                if args.max_top_buyer_share:
                    try:
                        ts = m.get("top_buyer_share")
                        if ts is not None and float(ts) > args.max_top_buyer_share:
                            continue
                    except Exception:
                        pass
                mrets.append(r)
                mcaps.append(mc)

            if mcaps:
                # bucket by (prev, cutoff]
                prev = 0.0
                for b in mcap_buckets:
                    ys = [r for (r, mc) in zip(mrets, mcaps) if (mc > prev and mc <= b)]
                    if ys:
                        w = sum(1 for r in ys if r > 0)
                        print(
                            f"  mcap0 in (${prev:,.0f}, ${b:,.0f}]: n={len(ys):5d} winrate={w/len(ys):6.1%} "
                            f"mean={mean(ys):+.4f} median={median(ys):+.4f}"
                        )
                    prev = b

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
