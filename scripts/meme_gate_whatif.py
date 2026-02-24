#!/usr/bin/env python3
"""Offline what-if analysis for signal-entry gate tuning.

Uses `data/signal_outcomes.jsonl` to estimate how a single gate change would
affect sample size and forward-return statistics.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")

try:
    from dotenv import load_dotenv

    load_dotenv(BASE / ".env", override=False)
except Exception:
    pass


@dataclass
class Gate:
    min_score: float
    min_hits: int
    min_buys: int
    min_unique_buyers: int
    min_net_sol_in: float
    min_mcap_usd: float
    min_buy_sell_ratio: float
    max_top_buyer_share: float
    # score-bypass controls
    bypass_enabled: bool
    bypass_min_hits: int
    bypass_min_buys: int
    bypass_min_unique_buyers: int
    bypass_min_net_sol_in: float
    bypass_max_top_buyer_share: float


def _env_f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except Exception:
        return float(default)


def _env_i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)) or default)
    except Exception:
        return int(default)


def _env_b(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "true" if default else "false") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _load_gate() -> Gate:
    return Gate(
        min_score=_env_f("MEME_SIGNAL_PREQUOTE_MIN_SIGNAL_SCORE", 65.0),
        min_hits=_env_i("MEME_SIGNAL_PREQUOTE_MIN_HITS", 3),
        min_buys=_env_i("MEME_SIGNAL_PREQUOTE_MIN_BUYS", 2),
        min_unique_buyers=_env_i("MEME_SIGNAL_PREQUOTE_MIN_UNIQUE_BUYERS", 3),
        min_net_sol_in=_env_f("MEME_SIGNAL_PREQUOTE_MIN_NET_SOL_IN", 1.0),
        min_mcap_usd=_env_f("MEME_SIGNAL_PREQUOTE_MIN_MCAP_USD", 0.0),
        min_buy_sell_ratio=_env_f("MEME_SIGNAL_PREQUOTE_MIN_BUY_SELL_RATIO", 0.0),
        max_top_buyer_share=_env_f("MEME_SIGNAL_PREQUOTE_MAX_TOP_BUYER_SHARE", 0.0),
        bypass_enabled=_env_b("MEME_SIGNAL_PREQUOTE_SCORE_BYPASS_ENABLED", True),
        bypass_min_hits=_env_i("MEME_SIGNAL_PREQUOTE_SCORE_BYPASS_MIN_HITS", 4),
        bypass_min_buys=_env_i("MEME_SIGNAL_PREQUOTE_SCORE_BYPASS_MIN_BUYS", 3),
        bypass_min_unique_buyers=_env_i("MEME_SIGNAL_PREQUOTE_SCORE_BYPASS_MIN_UNIQUE_BUYERS", 4),
        bypass_min_net_sol_in=_env_f("MEME_SIGNAL_PREQUOTE_SCORE_BYPASS_MIN_NET_SOL_IN", 1.0),
        bypass_max_top_buyer_share=_env_f("MEME_SIGNAL_PREQUOTE_SCORE_BYPASS_MAX_TOP_BUYER_SHARE", 0.45),
    )


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _to_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(v)
    except Exception:
        return default


def _iter_rows(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8") as fh:
        for ln in fh:
            try:
                obj = json.loads(ln)
            except Exception:
                continue
            m = obj.get("metrics") if isinstance(obj.get("metrics"), dict) else {}
            score = _to_float(
                obj.get(
                    "score",
                    obj.get(
                        "signal_score",
                        obj.get("score0", m.get("score", 0.0)),
                    ),
                ),
                0.0,
            )
            row = {
                "h": _to_int(obj.get("horizon_s"), 0),
                "ret": _to_float(obj.get("ret"), 0.0),
                "score": score,
                "hits": _to_int(m.get("hits", obj.get("hits0", obj.get("hits", 0))), 0),
                "buys": _to_int(m.get("buys", obj.get("buys0", obj.get("buys", 0))), 0),
                "sells": _to_int(m.get("sells", obj.get("sells0", obj.get("sells", 0))), 0),
                "unique_buyers": _to_int(
                    m.get("unique_buyers", obj.get("uniq0", obj.get("unique_buyers", 0))),
                    0,
                ),
                "net_sol_in": _to_float(
                    m.get("net_sol_in", obj.get("net_sol_in0", obj.get("net_sol_in", 0.0))),
                    0.0,
                ),
                "mcap": _to_float(
                    m.get(
                        "market_cap",
                        m.get(
                            "mcap",
                            m.get(
                                "fdv",
                                obj.get(
                                    "market_cap",
                                    obj.get("marketcap0", obj.get("mcap0", obj.get("fdv0", 0.0))),
                                ),
                            ),
                        ),
                    ),
                    0.0,
                ),
            }
            ts = m.get("top_buyer_share", obj.get("top_buyer_share0", obj.get("top_buyer_share")))
            row["top_buyer_share"] = None if ts is None else _to_float(ts, 0.0)
            out.append(row)
    return out


def _passes(g: Gate, r: dict[str, Any]) -> bool:
    hits = int(r["hits"])
    buys = int(r["buys"])
    sells = int(r["sells"])
    uniq = int(r["unique_buyers"])
    net = float(r["net_sol_in"])
    mcap = float(r["mcap"])
    score = float(r["score"])
    top = r.get("top_buyer_share")

    if g.min_hits > 0 and hits < g.min_hits:
        return False
    if g.min_buys > 0 and buys < g.min_buys:
        return False
    if g.min_unique_buyers > 0 and uniq < g.min_unique_buyers:
        return False
    if g.min_net_sol_in > 0 and net < g.min_net_sol_in:
        return False
    if g.min_mcap_usd > 0 and mcap > 0 and mcap < g.min_mcap_usd:
        return False
    if g.min_buy_sell_ratio > 0 and sells > 0 and (float(buys) / float(max(1, sells))) < g.min_buy_sell_ratio:
        return False
    if g.max_top_buyer_share > 0 and top is not None and float(top) > g.max_top_buyer_share:
        return False

    if score >= g.min_score:
        return True
    if not g.bypass_enabled:
        return False

    top_ok = (
        top is None
        or g.bypass_max_top_buyer_share <= 0
        or float(top) <= g.bypass_max_top_buyer_share
    )
    return (
        hits >= g.bypass_min_hits
        and buys >= g.bypass_min_buys
        and uniq >= g.bypass_min_unique_buyers
        and net >= g.bypass_min_net_sol_in
        and top_ok
    )


def _summarize(rows: list[dict[str, Any]], gate: Gate, horizon: int, cost_pct: float) -> dict[str, float]:
    xs: list[float] = []
    for r in rows:
        if int(r["h"]) != int(horizon):
            continue
        if not _passes(gate, r):
            continue
        ret = float(r["ret"])
        adj = ((1.0 + ret) * (1.0 - cost_pct)) - 1.0 if cost_pct > 0 else ret
        xs.append(adj)
    if not xs:
        return {"n": 0, "wr": 0.0, "mean": 0.0, "median": 0.0, "score": -1e18}
    n = len(xs)
    wr = sum(1 for x in xs if x > 0) / n
    mean = sum(xs) / n
    med = sorted(xs)[n // 2]
    # rank objective: mean with sample-size support + win rate bonus.
    score = (mean * math.log(n + 1.0)) + (0.20 * wr)
    return {"n": float(n), "wr": wr, "mean": mean, "median": med, "score": score}


def _clone(g: Gate, **kwargs: Any) -> Gate:
    d = g.__dict__.copy()
    d.update(kwargs)
    return Gate(**d)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(BASE / "data" / "signal_outcomes.jsonl"))
    ap.add_argument("--horizon", type=int, default=300)
    ap.add_argument("--roundtrip-cost-pct", type=float, default=0.03)
    ap.add_argument("--min-samples", type=int, default=80)
    args = ap.parse_args()

    rows = _iter_rows(Path(args.file))
    if not rows:
        print("No rows found.")
        return 0

    gate = _load_gate()
    base = _summarize(rows, gate, args.horizon, args.roundtrip_cost_pct)
    print(f"baseline h={args.horizon}s n={int(base['n'])} wr={base['wr']:.1%} mean={base['mean']:+.4f} med={base['median']:+.4f}")

    candidates: list[tuple[str, Gate]] = []
    candidates.append(("min_hits-1", _clone(gate, min_hits=max(1, gate.min_hits - 1))))
    candidates.append(("min_hits+1", _clone(gate, min_hits=gate.min_hits + 1)))
    candidates.append(("min_net-0.25", _clone(gate, min_net_sol_in=max(0.0, round(gate.min_net_sol_in - 0.25, 2)))))
    candidates.append(("min_net+0.25", _clone(gate, min_net_sol_in=round(gate.min_net_sol_in + 0.25, 2))))
    candidates.append(("min_uniq-1", _clone(gate, min_unique_buyers=max(1, gate.min_unique_buyers - 1))))
    candidates.append(("min_uniq+1", _clone(gate, min_unique_buyers=gate.min_unique_buyers + 1)))
    candidates.append(("max_top+0.05", _clone(gate, max_top_buyer_share=min(0.95, round(gate.max_top_buyer_share + 0.05, 2)))))
    candidates.append(("max_top-0.05", _clone(gate, max_top_buyer_share=max(0.0, round(gate.max_top_buyer_share - 0.05, 2)))))
    candidates.append(("min_score-5", _clone(gate, min_score=max(0.0, gate.min_score - 5.0))))
    candidates.append(("min_score+5", _clone(gate, min_score=gate.min_score + 5.0)))
    # Market-cap floor sensitivity. Small-launch noise often dominates drawdowns.
    candidates.append(("min_mcap-2000", _clone(gate, min_mcap_usd=max(0.0, gate.min_mcap_usd - 2000.0))))
    candidates.append(("min_mcap+2000", _clone(gate, min_mcap_usd=gate.min_mcap_usd + 2000.0)))
    candidates.append(("min_mcap+5000", _clone(gate, min_mcap_usd=gate.min_mcap_usd + 5000.0)))
    candidates.append(("min_mcap+8000", _clone(gate, min_mcap_usd=gate.min_mcap_usd + 8000.0)))

    ranked: list[tuple[str, dict[str, float], Gate]] = []
    for name, g2 in candidates:
        s = _summarize(rows, g2, args.horizon, args.roundtrip_cost_pct)
        if int(s["n"]) < int(args.min_samples):
            continue
        ranked.append((name, s, g2))

    ranked.sort(key=lambda x: x[1]["score"], reverse=True)
    if not ranked:
        print(f"No candidate met min_samples={args.min_samples}.")
        return 0

    base_n = int(base["n"])
    base_mean = float(base["mean"])
    meaningful: list[tuple[str, dict[str, float], Gate]] = []
    min_n_shift = max(5, int(round(base_n * 0.02)))
    for name, s, g2 in ranked:
        n_shift = abs(int(s["n"]) - base_n)
        mean_shift = abs(float(s["mean"]) - base_mean)
        if n_shift >= min_n_shift or mean_shift >= 0.002:
            meaningful.append((name, s, g2))

    print("\nTop what-if candidates:")
    for name, s, _ in ranked[:8]:
        print(
            f"- {name:12s} n={int(s['n']):4d} wr={s['wr']:.1%} mean={s['mean']:+.4f} "
            f"med={s['median']:+.4f} score={s['score']:+.4f}"
        )

    better = [x for x in meaningful if float(x[1]["score"]) > float(base["score"]) + 1e-6]

    if not better:
        print("\nRecommended single change:")
        print("- none (no tested one-step gate improved objective vs baseline)")
        return 0

    best_name, best_s, best_g = better[0]
    print("\nRecommended single change:")
    print(
        f"- {best_name} "
        f"(n {int(base['n'])}->{int(best_s['n'])}, "
        f"mean {base['mean']:+.4f}->{best_s['mean']:+.4f}, "
        f"wr {base['wr']:.1%}->{best_s['wr']:.1%})"
    )
    # Emit exact env delta for automation/manual apply.
    fields = [k for k in gate.__dict__.keys() if gate.__dict__[k] != best_g.__dict__[k]]
    for k in fields:
        v = best_g.__dict__[k]
        env = {
            "min_score": "MEME_SIGNAL_PREQUOTE_MIN_SIGNAL_SCORE",
            "min_hits": "MEME_SIGNAL_PREQUOTE_MIN_HITS",
            "min_buys": "MEME_SIGNAL_PREQUOTE_MIN_BUYS",
            "min_unique_buyers": "MEME_SIGNAL_PREQUOTE_MIN_UNIQUE_BUYERS",
            "min_net_sol_in": "MEME_SIGNAL_PREQUOTE_MIN_NET_SOL_IN",
            "min_mcap_usd": "MEME_SIGNAL_PREQUOTE_MIN_MCAP_USD",
            "min_buy_sell_ratio": "MEME_SIGNAL_PREQUOTE_MIN_BUY_SELL_RATIO",
            "max_top_buyer_share": "MEME_SIGNAL_PREQUOTE_MAX_TOP_BUYER_SHARE",
        }.get(k, k)
        if isinstance(v, bool):
            print(f"{env}={'true' if v else 'false'}")
        else:
            print(f"{env}={v}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
