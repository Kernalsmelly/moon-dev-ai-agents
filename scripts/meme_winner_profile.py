#!/usr/bin/env python3
"""Build winner-profile thresholds from historical trade metadata.

The output file is consumed by `src/meme_bot.py` when MEME_WINNER_PROFILE_ENABLED=true.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any


FEATURE_DIRECTIONS: dict[str, str] = {
    "market_cap": "high",
    "liquidity": "high",
    "price_change_5m": "high",
    "txns_5m": "high",
    "volume_5m": "high",
    "signal_score": "high",
    "signal_hits": "high",
    "signal_unique_buyers": "high",
    "signal_net_sol_in": "high",
    "signal_buy_accel": "high",
    "signal_t_first_sell_s": "high",
    "signal_buy_sell_ratio": "high",
    "signal_top_buyer_share": "low",
    "signal_sell_share": "low",
}


def _ts_epoch(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        try:
            return float(v)
        except Exception:
            return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    data = sorted(float(v) for v in values)
    q = max(0.0, min(1.0, float(q)))
    pos = (len(data) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(data[lo])
    frac = pos - lo
    return float(data[lo] * (1.0 - frac) + data[hi] * frac)


def _f(md: dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(md.get(key, default) or default)
    except Exception:
        return float(default)


def _extract_features(md: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    out["market_cap"] = _f(md, "market_cap_entry")
    out["liquidity"] = _f(md, "liquidity_entry")
    out["price_change_5m"] = _f(md, "price_change_5m_entry")
    out["txns_5m"] = _f(md, "txns_5m_entry")
    out["volume_5m"] = _f(md, "volume_5m_entry")
    out["signal_score"] = _f(md, "signal_score")
    out["signal_hits"] = _f(md, "signal_hits")
    out["signal_unique_buyers"] = _f(md, "signal_unique_buyers")
    out["signal_net_sol_in"] = _f(md, "signal_net_sol_in")
    out["signal_buy_accel"] = _f(md, "signal_buy_accel")
    out["signal_t_first_sell_s"] = _f(md, "signal_t_first_sell_s")
    out["signal_top_buyer_share"] = _f(md, "signal_top_buyer_share")

    sig_buys = _f(md, "signal_buys")
    sig_sells = _f(md, "signal_sells")
    if sig_buys > 0 or sig_sells > 0:
        out["signal_buy_sell_ratio"] = (sig_buys + 1.0) / (sig_sells + 1.0)
        out["signal_sell_share"] = sig_sells / max(1.0, sig_buys + sig_sells)
    return out


def _is_present(v: float) -> bool:
    if not math.isfinite(v):
        return False
    return abs(v) > 1e-12


def _score_from_profile(features: dict[str, dict[str, Any]], md: dict[str, Any]) -> tuple[float, int]:
    vals = _extract_features(md)
    weighted = 0.0
    total_w = 0.0
    used = 0
    for key, spec in features.items():
        if key not in vals:
            continue
        try:
            val = float(vals[key])
            lo = float(spec.get("p10", spec.get("min", 0.0)))
            hi = float(spec.get("p90", spec.get("max", 1.0)))
            direction = str(spec.get("direction", "high") or "high").strip().lower()
            w = float(spec.get("weight", 1.0) or 1.0)
        except Exception:
            continue
        if not math.isfinite(val) or not math.isfinite(lo) or not math.isfinite(hi) or w <= 0:
            continue
        if hi <= lo:
            hi = lo + 1e-9
        if direction in ("low", "lower", "min", "minimize"):
            s01 = (hi - val) / (hi - lo)
        else:
            s01 = (val - lo) / (hi - lo)
        s01 = max(0.0, min(1.0, float(s01)))
        weighted += s01 * w
        total_w += w
        used += 1
    if total_w <= 0:
        return 0.0, 0
    return (weighted / total_w) * 100.0, used


def _load_trades(db_path: str, lookback_hours: int) -> list[dict[str, Any]]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        select created_at, pnl_usd, pnl_pct, metadata
        from trades
        where metadata is not null and metadata != ''
        """,
    ).fetchall()
    con.close()

    cutoff = time.time() - (max(1, int(lookback_hours)) * 3600)
    out: list[dict[str, Any]] = []
    for r in rows:
        ts = _ts_epoch(r["created_at"])
        if ts is None or ts < cutoff:
            continue
        try:
            md = json.loads(r["metadata"] or "{}")
        except Exception:
            md = {}
        if not isinstance(md, dict):
            md = {}
        try:
            pnl_usd = float(r["pnl_usd"] or 0.0)
            pnl_pct = float(r["pnl_pct"] or 0.0)
        except Exception:
            continue
        out.append({"pnl_usd": pnl_usd, "pnl_pct": pnl_pct, "metadata": md})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/positions.db")
    ap.add_argument("--out", default="data/meme_winner_profile.json")
    ap.add_argument("--lookback-hours", type=int, default=72)
    ap.add_argument("--winner-pnl-pct", type=float, default=12.0)
    ap.add_argument("--loser-pnl-pct", type=float, default=-6.0)
    ap.add_argument("--min-group", type=int, default=20)
    ap.add_argument("--min-feature-samples", type=int, default=10)
    args = ap.parse_args()

    trades = _load_trades(args.db, args.lookback_hours)
    if not trades:
        print("No recent trades found.")
        return 1

    winners = [t for t in trades if t["pnl_pct"] >= float(args.winner_pnl_pct) and t["pnl_usd"] > 0]
    losers = [t for t in trades if t["pnl_pct"] <= float(args.loser_pnl_pct) and t["pnl_usd"] < 0]

    # Fallback to sign split when strict pnl buckets are too small.
    if len(winners) < int(args.min_group) or len(losers) < int(args.min_group):
        winners = [t for t in trades if t["pnl_usd"] > 0]
        losers = [t for t in trades if t["pnl_usd"] < 0]

    if len(winners) < int(args.min_group) or len(losers) < int(args.min_group):
        print(
            f"Insufficient samples for profile (winners={len(winners)}, losers={len(losers)}, min_group={args.min_group})."
        )
        return 1

    feature_keys = sorted(FEATURE_DIRECTIONS.keys())
    features: dict[str, dict[str, Any]] = {}
    raw_weights: dict[str, float] = {}
    for key in feature_keys:
        wvals = []
        lvals = []
        for t in winners:
            v = _extract_features(t["metadata"]).get(key, 0.0)
            if _is_present(v):
                wvals.append(float(v))
        for t in losers:
            v = _extract_features(t["metadata"]).get(key, 0.0)
            if _is_present(v):
                lvals.append(float(v))
        if len(wvals) < int(args.min_feature_samples) or len(lvals) < int(args.min_feature_samples):
            continue

        w10, w50, w90 = _pct(wvals, 0.10), _pct(wvals, 0.50), _pct(wvals, 0.90)
        l10, l50, l90 = _pct(lvals, 0.10), _pct(lvals, 0.50), _pct(lvals, 0.90)
        direction = FEATURE_DIRECTIONS.get(key, "high")

        # Separation strength for weighting.
        if direction == "high":
            sep = float(w50 - l50)
        else:
            sep = float(l50 - w50)
        spread = abs(float(w90 - w10)) + abs(float(l90 - l10)) + 1e-9
        strength = max(0.0, sep / spread)
        raw_weights[key] = strength

        features[key] = {
            "direction": direction,
            "p10": float(w10),
            "p50": float(w50),
            "p90": float(w90),
            "loser_p10": float(l10),
            "loser_p50": float(l50),
            "loser_p90": float(l90),
            "n_winners": len(wvals),
            "n_losers": len(lvals),
        }

    if not features:
        print("No features met sample requirements.")
        return 1

    # Normalize feature weights with a floor so weaker but useful features still contribute.
    max_w = max(raw_weights.values()) if raw_weights else 1.0
    for key, f in features.items():
        rw = float(raw_weights.get(key, 0.0))
        norm = (rw / max_w) if max_w > 0 else 0.0
        f["weight"] = round(0.35 + (1.65 * max(0.0, min(1.0, norm))), 4)

    out_obj = {
        "generated_at": time.time(),
        "db": str(args.db),
        "lookback_hours": int(args.lookback_hours),
        "winners": len(winners),
        "losers": len(losers),
        "winner_pnl_pct_threshold": float(args.winner_pnl_pct),
        "loser_pnl_pct_threshold": float(args.loser_pnl_pct),
        "features": features,
    }

    # Recommend a minimum winner score based on historical pnl_pct expectancy.
    # Objective: improve average return while keeping enough sample size.
    score_rows: list[tuple[float, float]] = []  # (score, pnl_pct)
    for t in trades:
        s, used = _score_from_profile(features, t["metadata"])
        if used < 2:
            continue
        score_rows.append((float(s), float(t["pnl_pct"])))
    if score_rows:
        total_n = len(score_rows)
        best_obj = -1e18
        best = None
        for th in range(20, 86, 2):
            seg = [p for s, p in score_rows if s >= float(th)]
            if len(seg) < int(args.min_group):
                continue
            avg_pct = sum(seg) / len(seg)
            wr = sum(1 for p in seg if p > 0) / len(seg)
            coverage = len(seg) / max(1, total_n)
            # Bias for positive expectancy with non-trivial coverage.
            obj = (avg_pct * 0.7) + (wr * 100.0 * 0.3) + (coverage * 8.0)
            if obj > best_obj:
                best_obj = obj
                best = {
                    "min_score": float(th),
                    "n": len(seg),
                    "coverage": round(coverage, 4),
                    "avg_pnl_pct": round(avg_pct, 4),
                    "win_rate": round(wr, 4),
                }
        if best:
            out_obj["recommended_min_score"] = float(best["min_score"])
            out_obj["recommended_min_score_stats"] = best

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out_obj, indent=2), encoding="utf-8")

    ranked = sorted(
        ((k, float(v.get("weight", 0.0))) for k, v in features.items()),
        key=lambda kv: kv[1],
        reverse=True,
    )
    print(
        f"Wrote {out_path} with {len(features)} features "
        f"(winners={len(winners)}, losers={len(losers)})."
    )
    print("Top weighted features:")
    for k, w in ranked[:8]:
        print(f"  {k:24s} weight={w:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
