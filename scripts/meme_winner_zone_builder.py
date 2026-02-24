#!/usr/bin/env python3
"""Build winner-zone allowlist from signal outcomes.

This creates a compact zone map that can be enforced at entry time:
- signal score
- net SOL inflow
- top-buyer concentration
- market cap
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")


def _f(v: Any, d: float = 0.0) -> float:
    try:
        if v is None:
            return d
        return float(v)
    except Exception:
        return d


def _extract_row(obj: dict[str, Any]) -> dict[str, float] | None:
    metrics = obj.get("metrics") if isinstance(obj.get("metrics"), dict) else {}
    ts = _f(obj.get("ts"), 0.0)
    h = int(_f(obj.get("horizon_s"), 0))
    ret = _f(obj.get("ret"), 0.0)
    score = _f(obj.get("signal_score", obj.get("score0", obj.get("score", metrics.get("score", 0.0)))), 0.0)
    net = _f(metrics.get("net_sol_in", obj.get("net_sol_in0", obj.get("net_sol_in", 0.0))), 0.0)
    top = metrics.get("top_buyer_share", obj.get("top_buyer_share0", obj.get("top_buyer_share")))
    mcap = _f(
        obj.get(
            "marketcap0",
            obj.get(
                "mcap0",
                metrics.get("market_cap", metrics.get("mcap", metrics.get("fdv", obj.get("market_cap", 0.0)))),
            ),
        ),
        0.0,
    )
    if ts <= 0 or h <= 0:
        return None
    return {
        "ts": ts,
        "h": float(h),
        "ret": ret,
        "score": score,
        "net": net,
        "top": _f(top, -1.0) if top is not None else -1.0,
        "mcap": mcap,
    }


def _in_bin(v: float, lo: float, hi: float) -> bool:
    if v < lo:
        return False
    if v >= hi:
        return False
    return True


def _obj(mean_adj: float, wr: float, n: int) -> float:
    return float(mean_adj) * math.log(max(2.0, float(n) + 1.0)) + (0.25 * float(wr))


@dataclass
class Zone:
    id: str
    n: int
    win_rate: float
    mean_ret: float
    mean_ret_adj: float
    objective: float
    score_lo: float
    score_hi: float
    net_lo: float
    net_hi: float
    top_lo: float
    top_hi: float
    mcap_lo: float
    mcap_hi: float


def _build_zones(
    rows: list[dict[str, float]],
    *,
    score_bins: list[float],
    net_bins: list[float],
    top_bins: list[float],
    mcap_bins: list[float],
    min_samples: int,
    min_win_rate: float,
    min_mean_adj: float,
    max_zones: int,
) -> list[Zone]:
    zones: list[Zone] = []
    zid = 0
    for (slo, shi), (nlo, nhi), (tlo, thi), (mlo, mhi) in itertools.product(
        zip(score_bins[:-1], score_bins[1:]),
        zip(net_bins[:-1], net_bins[1:]),
        zip(top_bins[:-1], top_bins[1:]),
        zip(mcap_bins[:-1], mcap_bins[1:]),
    ):
        xs = [
            r
            for r in rows
            if _in_bin(float(r["score"]), float(slo), float(shi))
            and _in_bin(float(r["net"]), float(nlo), float(nhi))
            and _in_bin(float(r["top"]), float(tlo), float(thi))
            and _in_bin(float(r["mcap"]), float(mlo), float(mhi))
        ]
        n = len(xs)
        if n < int(min_samples):
            continue
        wr = sum(1 for r in xs if float(r["ret_adj"]) > 0) / float(n)
        mean_ret = sum(float(r["ret"]) for r in xs) / float(n)
        mean_adj = sum(float(r["ret_adj"]) for r in xs) / float(n)
        if wr < float(min_win_rate):
            continue
        if mean_adj < float(min_mean_adj):
            continue
        zid += 1
        zones.append(
            Zone(
                id=f"zone_{zid}",
                n=n,
                win_rate=wr,
                mean_ret=mean_ret,
                mean_ret_adj=mean_adj,
                objective=_obj(mean_adj, wr, n),
                score_lo=float(slo),
                score_hi=float(shi),
                net_lo=float(nlo),
                net_hi=float(nhi),
                top_lo=float(tlo),
                top_hi=float(thi),
                mcap_lo=float(mlo),
                mcap_hi=float(mhi),
            )
        )

    zones.sort(key=lambda z: float(z.objective), reverse=True)
    return zones[: max(1, int(max_zones))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(BASE / "data" / "signal_outcomes.jsonl"))
    ap.add_argument("--out", default=str(BASE / "data" / "meme_winner_zones.json"))
    ap.add_argument("--out-md", default=str(BASE / "data" / "meme_winner_zones.md"))
    ap.add_argument("--horizon", type=int, default=120)
    ap.add_argument("--lookback-hours", type=float, default=72.0)
    ap.add_argument("--roundtrip-cost-pct", type=float, default=0.03)
    ap.add_argument("--min-samples", type=int, default=20)
    ap.add_argument("--min-win-rate", type=float, default=0.50)
    ap.add_argument("--min-mean-adj", type=float, default=0.00)
    ap.add_argument("--max-zones", type=int, default=16)
    ap.add_argument("--coarse-fallback", type=int, default=1)
    ap.add_argument("--coarse-min-samples", type=int, default=8)
    ap.add_argument("--coarse-min-win-rate", type=float, default=0.48)
    ap.add_argument("--coarse-min-mean-adj", type=float, default=-0.002)
    args = ap.parse_args()

    p = Path(args.file)
    if not p.exists():
        print(f"missing {p}")
        return 2

    cutoff = time.time() - float(args.lookback_hours) * 3600.0
    rows: list[dict[str, float]] = []
    with p.open("r", encoding="utf-8") as fh:
        for ln in fh:
            try:
                obj = json.loads(ln)
            except Exception:
                continue
            r = _extract_row(obj)
            if not r:
                continue
            if int(r["h"]) != int(args.horizon):
                continue
            if float(r["ts"]) < cutoff:
                continue
            rows.append(r)

    if not rows:
        print("no rows for selected horizon/lookback")
        return 1

    cost = float(args.roundtrip_cost_pct)
    for r in rows:
        ret = float(r["ret"])
        r["ret_adj"] = ((1.0 + ret) * (1.0 - cost)) - 1.0 if cost > 0 else ret

    base_n = len(rows)
    base_wr = sum(1 for r in rows if float(r["ret_adj"]) > 0) / float(base_n)
    base_mean = sum(float(r["ret_adj"]) for r in rows) / float(base_n)

    score_bins = [0.0, 55.0, 60.0, 65.0, 70.0, 75.0, 101.0]
    net_bins = [0.0, 0.50, 1.00, 1.50, 2.50, 5.00, 999.0]
    # top=-1 means missing; keep one explicit bucket for missing top-share.
    top_bins = [-2.0, 0.0, 0.25, 0.35, 0.45, 0.55, 1.01]
    mcap_bins = [0.0, 10_000.0, 12_000.0, 15_000.0, 25_000.0, 50_000.0, 1e15]

    zones = _build_zones(
        rows,
        score_bins=score_bins,
        net_bins=net_bins,
        top_bins=top_bins,
        mcap_bins=mcap_bins,
        min_samples=int(args.min_samples),
        min_win_rate=float(args.min_win_rate),
        min_mean_adj=float(args.min_mean_adj),
        max_zones=int(args.max_zones),
    )
    selection_mode = "fine"

    # Fallback to coarser bins if strict/fine segmentation yields no viable zones.
    if (not zones) and bool(args.coarse_fallback):
        coarse_score_bins = [0.0, 60.0, 70.0, 101.0]
        coarse_net_bins = [0.0, 1.0, 2.5, 999.0]
        coarse_top_bins = [-2.0, 0.0, 0.35, 0.55, 1.01]
        coarse_mcap_bins = [0.0, 12_000.0, 25_000.0, 1e15]
        zones = _build_zones(
            rows,
            score_bins=coarse_score_bins,
            net_bins=coarse_net_bins,
            top_bins=coarse_top_bins,
            mcap_bins=coarse_mcap_bins,
            min_samples=int(args.coarse_min_samples),
            min_win_rate=float(args.coarse_min_win_rate),
            min_mean_adj=float(args.coarse_min_mean_adj),
            max_zones=int(args.max_zones),
        )
        if zones:
            selection_mode = "coarse"

    out_obj = {
        "generated_at": time.time(),
        "source_file": str(p),
        "horizon_s": int(args.horizon),
        "lookback_hours": float(args.lookback_hours),
        "roundtrip_cost_pct": float(args.roundtrip_cost_pct),
        "min_samples": int(args.min_samples),
        "base": {
            "n": base_n,
            "win_rate": base_wr,
            "mean_ret_adj": base_mean,
        },
        "selection_mode": selection_mode,
        "zones": [
            {
                "id": z.id,
                "n": z.n,
                "win_rate": z.win_rate,
                "mean_ret": z.mean_ret,
                "mean_ret_adj": z.mean_ret_adj,
                "objective": z.objective,
                "score": {"lo": z.score_lo, "hi": z.score_hi},
                "net_sol_in": {"lo": z.net_lo, "hi": z.net_hi},
                "top_buyer_share": {"lo": z.top_lo, "hi": z.top_hi},
                "mcap": {"lo": z.mcap_lo, "hi": z.mcap_hi},
            }
            for z in zones
        ],
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out_obj, indent=2, sort_keys=True), encoding="utf-8")

    lines = [
        "# Meme Winner Zones",
        "",
        f"- horizon_s: {int(args.horizon)}",
        f"- lookback_hours: {float(args.lookback_hours):g}",
        f"- base_n: {base_n}",
        f"- base_wr: {base_wr:.1%}",
        f"- base_mean_ret_adj: {base_mean:+.4f}",
        f"- selection_mode: {selection_mode}",
        f"- selected_zones: {len(zones)}",
        "",
    ]
    if zones:
        lines.append("## Zones")
        for z in zones:
            lines.append(
                f"- `{z.id}` n={z.n} wr={z.win_rate:.1%} mean_adj={z.mean_ret_adj:+.4f} "
                f"score[{z.score_lo:g},{z.score_hi:g}) net[{z.net_lo:g},{z.net_hi:g}) "
                f"top[{z.top_lo:g},{z.top_hi:g}) mcap[{z.mcap_lo:g},{z.mcap_hi:g})"
            )
    else:
        lines.append("No zones passed current constraints.")
    Path(args.out_md).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out_path}")
    print(f"wrote {args.out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
