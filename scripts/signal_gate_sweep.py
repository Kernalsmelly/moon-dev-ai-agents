#!/usr/bin/env python3
"""Sweep simple gating thresholds over labeled signal outcomes.

This is not a full backtester. It answers:
  "If we require X early-demand metrics, what does forward return look like?"

We use rows from data/signal_outcomes.jsonl (one row per mint per horizon).
The output is a ranked list of threshold combos with summary stats.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable


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


def _iter_lines(path: Path, lookback: int) -> Iterable[dict[str, Any]]:
    data = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    if lookback > 0:
        data = data[-lookback:]
    for ln in data:
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except Exception:
            continue
        if isinstance(obj, dict):
            yield obj


@dataclass(frozen=True)
class Combo:
    min_hits: int
    min_buys: int
    min_unique_buyers: int
    min_net_sol_in: float
    max_top_buyer_share: float  # 0 disables


@dataclass
class ComboStats:
    n: int
    winrate: float
    mean_ret: float
    median_ret: float
    p05: float | None
    p95: float | None
    score: float


def _passes(m: dict[str, Any], c: Combo) -> bool:
    try:
        hits = int(m.get("hits") or 0)
        buys = int(m.get("buys") or 0)
        uniq = int(m.get("unique_buyers") or 0)
        net = float(m.get("net_sol_in") or 0.0)
    except Exception:
        return False
    if c.min_hits and hits < c.min_hits:
        return False
    if c.min_buys and buys < c.min_buys:
        return False
    if c.min_unique_buyers and uniq < c.min_unique_buyers:
        return False
    if c.min_net_sol_in and net < c.min_net_sol_in:
        return False
    if c.max_top_buyer_share and "top_buyer_share" in m:
        try:
            ts = m.get("top_buyer_share")
            if ts is not None and float(ts) > c.max_top_buyer_share:
                return False
        except Exception:
            return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="data/signal_outcomes.jsonl")
    ap.add_argument("--lookback", type=int, default=5000)
    ap.add_argument("--horizon", type=int, default=120)
    ap.add_argument("--min-samples", type=int, default=30)
    ap.add_argument("--roundtrip-cost-pct", type=float, default=0.02)
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"missing file: {path}")

    # Conservative grids; keep small to avoid overfitting tiny samples.
    hits_grid = [3, 4, 5]
    buys_grid = [2, 3, 4]
    uniq_grid = [1, 2, 3, 4]
    net_grid = [0.10, 0.25, 0.50, 1.00]
    top_share_grid = [0.0, 0.85, 0.70, 0.60]

    rows: list[tuple[dict[str, Any], float]] = []
    for obj in _iter_lines(path, args.lookback):
        try:
            if int(obj.get("horizon_s")) != int(args.horizon):
                continue
            r = float(obj.get("ret"))
        except Exception:
            continue
        m = obj.get("metrics") or {}
        if not isinstance(m, dict) or not m:
            continue
        # Adjust returns by a round-trip cost (paper is optimistic).
        c = max(0.0, float(args.roundtrip_cost_pct or 0.0))
        r_adj = ((1.0 + r) * (1.0 - c)) - 1.0 if c else r
        rows.append((m, r_adj))

    if not rows:
        print("No rows with metrics at the selected horizon.")
        return 0

    combos: list[Combo] = []
    for mh in hits_grid:
        for mb in buys_grid:
            for mu in uniq_grid:
                for mn in net_grid:
                    for ts in top_share_grid:
                        combos.append(
                            Combo(
                                min_hits=mh,
                                min_buys=mb,
                                min_unique_buyers=mu,
                                min_net_sol_in=mn,
                                max_top_buyer_share=ts,
                            )
                        )

    ranked: list[tuple[Combo, ComboStats]] = []
    for c in combos:
        xs = [r for (m, r) in rows if _passes(m, c)]
        n = len(xs)
        if n < int(args.min_samples):
            continue
        w = sum(1 for x in xs if x > 0)
        wr = w / n if n else 0.0
        mu = mean(xs)
        med = median(xs)
        p05 = _pct(xs, 5)
        p95 = _pct(xs, 95)
        # Score: mean weighted by sample size (log dampens large-N dominance) plus a bit of median.
        # Penalize heavy left-tail to avoid combos that "win big but rug often".
        tail_pen = abs(float(p05)) if p05 is not None and p05 < 0 else 0.0
        score = (mu * math.log(n + 1)) + (0.25 * med * math.log(n + 1)) - (0.25 * tail_pen)
        ranked.append((c, ComboStats(n=n, winrate=wr, mean_ret=mu, median_ret=med, p05=p05, p95=p95, score=score)))

    ranked.sort(key=lambda kv: kv[1].score, reverse=True)

    print(f"file={path} lookback={args.lookback} horizon={args.horizon}s rows_with_metrics={len(rows)}")
    print(f"min_samples={args.min_samples} roundtrip_cost_pct={args.roundtrip_cost_pct}")
    print()

    topn = ranked[:15]
    if not topn:
        print("No combos met min_samples.")
        return 0

    for i, (c, s) in enumerate(topn, 1):
        ts = f"{c.max_top_buyer_share:.2f}" if c.max_top_buyer_share else "off"
        print(
            f"{i:2d}. score={s.score:+.4f} n={s.n:4d} winrate={s.winrate:5.1%} "
            f"mean={s.mean_ret:+.4f} median={s.median_ret:+.4f} p05={s.p05:+.4f} p95={s.p95:+.4f} | "
            f"hits>={c.min_hits} buys>={c.min_buys} uniq>={c.min_unique_buyers} net>={c.min_net_sol_in:.2f} "
            f"top_share<={ts}"
        )

    best, best_s = topn[0]
    ts_best = f"{best.max_top_buyer_share:.2f}" if best.max_top_buyer_share else "off"
    print()
    print("recommended_env:")
    print(f"MEME_SIGNAL_MIN_BUYS={best.min_buys}")
    print(f"MEME_SIGNAL_MIN_NET_SOL_IN={best.min_net_sol_in}")
    print(f"PUMP_SIGNAL_MIN_HITS={best.min_hits}")
    print(f"PUMP_SIGNAL_MIN_BUYS={best.min_buys}")
    if best.min_unique_buyers > 1:
        print(f"# (not yet wired as a hard gate) min_unique_buyers={best.min_unique_buyers}")
    if best.max_top_buyer_share:
        print(f"PUMP_SIGNAL_MAX_TOP_BUYER_SHARE={ts_best}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

