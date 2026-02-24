#!/usr/bin/env python3
"""Offline "swarm" worker: grid-search signal gates using signal_outcomes.jsonl.

This is intentionally offline and cheap: it only reads local JSONL and emits a ranked
set of candidate threshold configs that can later be applied to `.env`.

We optimize for:
- Higher mean forward return (after a conservative roundtrip cost)
- Avoiding catastrophic tails (p05 not too negative)
- Enough samples (n threshold)

This is not a full backtest. It is a fast way to pick the next 1-2 knobs to turn.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median
from typing import Any


@dataclass
class Row:
    horizon_s: int
    ret: float
    marketcap0: float | None
    impact0: float | None
    metrics: dict[str, Any]


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


def _tail_lines(path: Path, lookback: int) -> list[str]:
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    if lookback > 0:
        lines = lines[-lookback:]
    return lines


def _load_rows(path: Path, lookback: int, *, require_metrics: bool, require_mcap0: bool) -> list[Row]:
    out: list[Row] = []
    for ln in _tail_lines(path, lookback):
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except Exception:
            continue
        try:
            h = int(obj.get("horizon_s"))
            r = float(obj.get("ret"))
        except Exception:
            continue
        m = obj.get("metrics") or {}
        if not isinstance(m, dict):
            m = {}
        if require_metrics and not m:
            continue
        mc = obj.get("marketcap0")
        if mc is not None:
            try:
                mc = float(mc)
            except Exception:
                mc = None
        if require_mcap0 and mc is None:
            continue
        i0 = obj.get("impact0")
        if i0 is not None:
            try:
                i0 = float(i0)
            except Exception:
                i0 = None
        out.append(Row(horizon_s=h, ret=r, marketcap0=mc, impact0=i0, metrics=m))
    return out


def _adjust_ret(r: float, roundtrip_cost_pct: float) -> float:
    c = max(0.0, float(roundtrip_cost_pct or 0.0))
    if c <= 0:
        return r
    return ((1.0 + r) * (1.0 - c)) - 1.0


def _passes(row: Row, cfg: dict[str, Any]) -> bool:
    m = row.metrics

    # Optional marketcap filter (applied only when present, consistent with bot).
    min_mcap = float(cfg.get("min_mcap") or 0.0)
    if min_mcap > 0.0 and row.marketcap0 is not None and float(row.marketcap0) < min_mcap:
        return False

    # Optional impact filter (applied only when present).
    max_impact = float(cfg.get("max_impact") or 0.0)
    if max_impact > 0.0 and row.impact0 is not None and float(row.impact0) > max_impact:
        return False

    # Demand metrics (applied only when present).
    def _get_int(k: str) -> int | None:
        if k not in m:
            return None
        try:
            return int(m.get(k) or 0)
        except Exception:
            return None

    def _get_float(k: str) -> float | None:
        if k not in m:
            return None
        try:
            return float(m.get(k) or 0.0)
        except Exception:
            return None

    min_hits = int(cfg.get("min_hits") or 0)
    hits = _get_int("hits")
    if min_hits and hits is not None and hits < min_hits:
        return False

    min_buys = int(cfg.get("min_buys") or 0)
    buys = _get_int("buys")
    if min_buys and buys is not None and buys < min_buys:
        return False

    min_uniq = int(cfg.get("min_unique_buyers") or 0)
    uniq = _get_int("unique_buyers")
    if min_uniq and uniq is not None and uniq < min_uniq:
        return False

    min_net = float(cfg.get("min_net_sol_in") or 0.0)
    net = _get_float("net_sol_in")
    if min_net and net is not None and net < min_net:
        return False

    max_top = float(cfg.get("max_top_buyer_share") or 0.0)
    top = _get_float("top_buyer_share")
    if max_top and top is not None and top > max_top:
        return False

    max_sells = int(cfg.get("max_sells") or 0)
    sells = _get_int("sells")
    if max_sells and sells is not None and sells > max_sells:
        return False

    min_tfs = float(cfg.get("min_t_first_sell_s") or 0.0)
    tfs = _get_float("t_first_sell_s")
    if min_tfs and tfs is not None and tfs < min_tfs:
        return False

    return True


def _score_config(stats_300: dict[str, Any], stats_120: dict[str, Any]) -> float:
    # Primary: 300s mean return
    # Secondary: 300s p05 (tail risk)
    # Secondary: winrate and sample size
    n = float(stats_300["n"])
    mu = float(stats_300["mean"])
    p05 = float(stats_300["p05"])
    wr = float(stats_300["winrate"])
    # Encourage samples, but with diminishing returns.
    size_bonus = math.log(n + 1.0) / 4.0
    # Penalize ugly tails.
    tail_pen = max(0.0, (-0.35 - p05)) * 2.0  # worse than -35% gets hit hard
    # Small bump if the 120s mean is positive (early move confirmation).
    mu120 = float(stats_120["mean"])
    early_bonus = 0.10 if mu120 > 0 else 0.0
    return (mu * 10.0) + (wr * 1.0) + size_bonus + early_bonus - tail_pen


def _summarize(rets: list[float]) -> dict[str, Any]:
    n = len(rets)
    if n == 0:
        return {"n": 0, "winrate": 0.0, "mean": 0.0, "median": 0.0, "p05": 0.0, "p95": 0.0}
    win = sum(1 for r in rets if r > 0)
    return {
        "n": n,
        "winrate": win / n,
        "mean": mean(rets),
        "median": median(rets),
        "p05": _pct(rets, 5) or 0.0,
        "p95": _pct(rets, 95) or 0.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="data/signal_outcomes.jsonl")
    ap.add_argument("--lookback", type=int, default=5000)
    ap.add_argument("--roundtrip-cost-pct", type=float, default=0.02)
    ap.add_argument("--require-metrics", action="store_true", default=True)
    ap.add_argument("--require-mcap0", action="store_true", default=True)
    ap.add_argument("--min-samples-300", type=int, default=80)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--out", default="data/meme_swarm_grid_results.json")
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"missing {path}")

    rows = _load_rows(path, args.lookback, require_metrics=bool(args.require_metrics), require_mcap0=bool(args.require_mcap0))
    by_h: dict[int, list[Row]] = {}
    for r in rows:
        by_h.setdefault(r.horizon_s, []).append(r)

    # We tune for horizons we care about.
    if 120 not in by_h or 300 not in by_h:
        raise SystemExit(f"need horizons 120 and 300 in file; got={sorted(by_h.keys())[:20]}")

    # Grid (coarse on purpose).
    grid = {
        "min_mcap": [10_000.0],  # user requirement
        "max_impact": [0.0, 0.25, 0.35],
        "min_hits": [0, 3, 4],
        "min_buys": [0, 2, 3],
        "min_unique_buyers": [0, 2, 3, 4],
        "min_net_sol_in": [0.0, 0.3, 0.5, 1.0, 1.5, 2.0],
        "max_top_buyer_share": [0.0, 0.65, 0.55, 0.45, 0.40, 0.35, 0.34],
        "max_sells": [0, 1, 2],
        "min_t_first_sell_s": [0.0, 0.5, 1.0, 2.0, 3.0],
    }

    keys = list(grid.keys())
    combos = list(itertools.product(*(grid[k] for k in keys)))
    results: list[dict[str, Any]] = []

    for vals in combos:
        cfg = dict(zip(keys, vals))
        rets_120 = []
        rets_300 = []
        for r in by_h[120]:
            if not _passes(r, cfg):
                continue
            rets_120.append(_adjust_ret(r.ret, args.roundtrip_cost_pct))
        for r in by_h[300]:
            if not _passes(r, cfg):
                continue
            rets_300.append(_adjust_ret(r.ret, args.roundtrip_cost_pct))
        if len(rets_300) < int(args.min_samples_300):
            continue

        s120 = _summarize(rets_120)
        s300 = _summarize(rets_300)
        score = _score_config(s300, s120)
        results.append(
            {
                "score": round(score, 6),
                "cfg": cfg,
                "h120": s120,
                "h300": s300,
            }
        )

    results.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    top = results[: max(1, int(args.top))]

    out = {
        "generated_at": __import__("time").time(),
        "file": str(path),
        "lookback": int(args.lookback),
        "roundtrip_cost_pct": float(args.roundtrip_cost_pct),
        "rows_loaded": len(rows),
        "results_considered": len(results),
        "top": top,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")

    # Minimal console summary (safe).
    print(f"rows_loaded={len(rows)} results={len(results)} wrote={args.out}")
    if top:
        best = top[0]
        print("best_score", best["score"])
        print("best_cfg", best["cfg"])
        print("best_h300", {k: best["h300"][k] for k in ("n", "winrate", "mean", "p05", "p95")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
