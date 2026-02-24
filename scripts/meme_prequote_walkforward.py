#!/usr/bin/env python3
"""Walk-forward tuner for winner-first prequote gates.

Goal:
- Tune prequote demand thresholds on older data (train).
- Validate on newest data slice (validation).
- Recommend at most one knob change to reduce overfitting risk.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any

from dotenv import load_dotenv

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")


@dataclass(frozen=True)
class Gate:
    min_hits: int
    min_buys: int
    min_unique_buyers: int
    min_net_sol_in: float
    min_signal_score: float
    min_buy_sell_ratio: float
    max_top_buyer_share: float


@dataclass
class Stats:
    n: int
    win_rate: float
    mean_ret: float
    median_ret: float
    p05: float
    p95: float
    score: float


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    ys = sorted(xs)
    if len(ys) == 1:
        return ys[0]
    k = (len(ys) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return ys[int(k)]
    return ys[f] * (c - k) + ys[c] * (k - f)


def _adjust_ret(r: float, roundtrip_cost_pct: float) -> float:
    c = max(0.0, float(roundtrip_cost_pct or 0.0))
    if c <= 0:
        return r
    return ((1.0 + r) * (1.0 - c)) - 1.0


def _load_rows(path: Path, lookback: int, horizon_s: int, roundtrip_cost_pct: float) -> list[tuple[float, dict[str, Any], float]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    if lookback > 0:
        lines = lines[-lookback:]
    out: list[tuple[float, dict[str, Any], float]] = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except Exception:
            continue
        try:
            if int(obj.get("horizon_s") or -1) != int(horizon_s):
                continue
            ts = float(obj.get("ts") or 0.0)
            ret = float(obj.get("ret") or 0.0)
        except Exception:
            continue
        metrics = obj.get("metrics")
        if not isinstance(metrics, dict) or not metrics:
            # Backward compatibility for legacy rows that stored flat features only.
            metrics = {}
            if obj.get("score0") is not None:
                metrics["score"] = obj.get("score0")
            elif obj.get("signal_score") is not None:
                metrics["score"] = obj.get("signal_score")
            if obj.get("hits0") is not None:
                metrics["hits"] = obj.get("hits0")
            if obj.get("buys0") is not None:
                metrics["buys"] = obj.get("buys0")
            if obj.get("sells0") is not None:
                metrics["sells"] = obj.get("sells0")
            if obj.get("uniq0") is not None:
                metrics["unique_buyers"] = obj.get("uniq0")
            if obj.get("net_sol_in0") is not None:
                metrics["net_sol_in"] = obj.get("net_sol_in0")
            if obj.get("top_buyer_share0") is not None:
                metrics["top_buyer_share"] = obj.get("top_buyer_share0")
        if not metrics:
            continue
        # Backfill score from top-level field when older rows omitted it in metrics.
        if "score" not in metrics and obj.get("signal_score") is not None:
            try:
                metrics = dict(metrics)
                metrics["score"] = float(obj.get("signal_score") or 0.0)
            except Exception:
                pass
        out.append((ts, metrics, _adjust_ret(ret, roundtrip_cost_pct)))
    out.sort(key=lambda x: x[0])
    return out


def _passes(metrics: dict[str, Any], g: Gate) -> bool:
    try:
        hits = int(metrics.get("hits") or 0)
    except Exception:
        hits = 0
    try:
        buys = int(metrics.get("buys") or 0)
    except Exception:
        buys = 0
    try:
        uniq = int(metrics.get("unique_buyers") or 0)
    except Exception:
        uniq = 0
    try:
        net = float(metrics.get("net_sol_in") or 0.0)
    except Exception:
        net = 0.0
    try:
        score = float(metrics.get("score") or 0.0)
    except Exception:
        score = 0.0
    try:
        sells = int(metrics.get("sells") or 0)
    except Exception:
        sells = 0
    try:
        top_share_raw = metrics.get("top_buyer_share")
        top_share = float(top_share_raw) if top_share_raw is not None else None
    except Exception:
        top_share = None

    if hits < int(g.min_hits):
        return False
    if buys < int(g.min_buys):
        return False
    if uniq < int(g.min_unique_buyers):
        return False
    if net < float(g.min_net_sol_in):
        return False
    if float(g.min_signal_score) > 0 and score < float(g.min_signal_score):
        return False
    if float(g.min_buy_sell_ratio) > 0 and sells > 0:
        ratio = float(buys) / float(sells)
        if ratio < float(g.min_buy_sell_ratio):
            return False
    if float(g.max_top_buyer_share) > 0 and top_share is not None:
        if float(top_share) > float(g.max_top_buyer_share):
            return False
    return True


def _summarize(rets: list[float]) -> Stats:
    n = len(rets)
    if n <= 0:
        return Stats(n=0, win_rate=0.0, mean_ret=0.0, median_ret=0.0, p05=0.0, p95=0.0, score=-9999.0)
    wr = sum(1 for r in rets if r > 0) / n
    mu = mean(rets)
    med = median(rets)
    p05 = _pct(rets, 5)
    p95 = _pct(rets, 95)
    # Score favors positive expectancy on validation while penalizing left-tail blowups.
    tail_pen = max(0.0, (-0.35 - p05)) * 2.0
    score = (mu * 12.0) + (wr * 0.75) + (math.log(n + 1.0) / 4.0) - tail_pen
    return Stats(n=n, win_rate=wr, mean_ret=mu, median_ret=med, p05=p05, p95=p95, score=score)


def _stats_for(rows: list[tuple[float, dict[str, Any], float]], g: Gate) -> Stats:
    rets = [ret for _, m, ret in rows if _passes(m, g)]
    return _summarize(rets)


def _gate_to_env(g: Gate) -> dict[str, str]:
    return {
        "MEME_SIGNAL_PREQUOTE_MIN_HITS": str(int(g.min_hits)),
        "MEME_SIGNAL_PREQUOTE_MIN_BUYS": str(int(g.min_buys)),
        "MEME_SIGNAL_PREQUOTE_MIN_UNIQUE_BUYERS": str(int(g.min_unique_buyers)),
        "MEME_SIGNAL_PREQUOTE_MIN_NET_SOL_IN": f"{float(g.min_net_sol_in):.2f}",
        "MEME_SIGNAL_PREQUOTE_MIN_SIGNAL_SCORE": f"{float(g.min_signal_score):.1f}",
        "MEME_SIGNAL_PREQUOTE_MIN_BUY_SELL_RATIO": f"{float(g.min_buy_sell_ratio):.2f}",
        "MEME_SIGNAL_PREQUOTE_MAX_TOP_BUYER_SHARE": f"{float(g.max_top_buyer_share):.2f}",
    }


def _suggest_one_change(current: Gate, target: Gate, baseline_val: Stats, best_val: Stats) -> dict[str, str]:
    # Require a minimum validation uplift before recommending a change.
    uplift = float(best_val.score) - float(baseline_val.score)
    if uplift < 0.03:
        return {}

    pairs = [
        ("MEME_SIGNAL_PREQUOTE_MIN_NET_SOL_IN", float(current.min_net_sol_in), float(target.min_net_sol_in)),
        ("MEME_SIGNAL_PREQUOTE_MIN_BUYS", float(current.min_buys), float(target.min_buys)),
        ("MEME_SIGNAL_PREQUOTE_MIN_HITS", float(current.min_hits), float(target.min_hits)),
        ("MEME_SIGNAL_PREQUOTE_MIN_UNIQUE_BUYERS", float(current.min_unique_buyers), float(target.min_unique_buyers)),
        ("MEME_SIGNAL_PREQUOTE_MIN_SIGNAL_SCORE", float(current.min_signal_score), float(target.min_signal_score)),
        ("MEME_SIGNAL_PREQUOTE_MIN_BUY_SELL_RATIO", float(current.min_buy_sell_ratio), float(target.min_buy_sell_ratio)),
        ("MEME_SIGNAL_PREQUOTE_MAX_TOP_BUYER_SHARE", float(current.max_top_buyer_share), float(target.max_top_buyer_share)),
    ]
    for name, cur, nxt in pairs:
        if abs(cur - nxt) > 1e-12:
            if name.endswith("NET_SOL_IN"):
                return {name: f"{nxt:.2f}"}
            if name.endswith("SIGNAL_SCORE"):
                return {name: f"{nxt:.1f}"}
            if name.endswith("BUY_SELL_RATIO"):
                return {name: f"{nxt:.2f}"}
            if name.endswith("TOP_BUYER_SHARE"):
                return {name: f"{nxt:.2f}"}
            return {name: str(int(round(nxt)))}
    return {}


def _write_md(path: Path, report: dict[str, Any]) -> None:
    cur = report.get("current", {})
    best = report.get("best", {})
    rec = report.get("recommended_change", {})
    lines = [
        "# Meme Prequote Walk-Forward",
        "",
        f"- Generated: `{report.get('generated_at')}`",
        f"- File: `{report.get('file')}`",
        f"- Horizon: `{report.get('horizon_s')}s`",
        f"- Rows: `{report.get('rows_total')}` (train `{report.get('rows_train')}`, val `{report.get('rows_val')}`)",
        "",
        "## Current Gate",
        f"- Gate: `{cur.get('gate')}`",
        f"- Train: n={cur.get('train', {}).get('n', 0)} mean={cur.get('train', {}).get('mean_ret', 0.0):+.4f} win={cur.get('train', {}).get('win_rate', 0.0)*100:.1f}%",
        f"- Val: n={cur.get('val', {}).get('n', 0)} mean={cur.get('val', {}).get('mean_ret', 0.0):+.4f} win={cur.get('val', {}).get('win_rate', 0.0)*100:.1f}% score={cur.get('val', {}).get('score', 0.0):+.4f}",
        "",
        "## Best Gate",
        f"- Gate: `{best.get('gate')}`",
        f"- Train: n={best.get('train', {}).get('n', 0)} mean={best.get('train', {}).get('mean_ret', 0.0):+.4f} win={best.get('train', {}).get('win_rate', 0.0)*100:.1f}%",
        f"- Val: n={best.get('val', {}).get('n', 0)} mean={best.get('val', {}).get('mean_ret', 0.0):+.4f} win={best.get('val', {}).get('win_rate', 0.0)*100:.1f}% score={best.get('val', {}).get('score', 0.0):+.4f}",
        "",
        "## Recommendation",
    ]
    if rec:
        for k, v in rec.items():
            lines.append(f"- `{k}={v}`")
    else:
        lines.append("- No change (insufficient validation uplift).")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    load_dotenv(dotenv_path=str(BASE / ".env"), override=True)

    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="data/signal_outcomes.jsonl")
    ap.add_argument("--lookback", type=int, default=12000)
    ap.add_argument("--horizon", type=int, default=300)
    ap.add_argument("--train-frac", type=float, default=0.7)
    ap.add_argument("--min-train", type=int, default=120)
    ap.add_argument("--min-val", type=int, default=60)
    ap.add_argument("--roundtrip-cost-pct", type=float, default=0.03)
    ap.add_argument("--out-json", default="data/meme_prequote_walkforward.json")
    ap.add_argument("--out-md", default="data/meme_prequote_walkforward.md")
    args = ap.parse_args()

    path = Path(args.file)
    rows = _load_rows(path, int(args.lookback), int(args.horizon), float(args.roundtrip_cost_pct))
    if not rows:
        print("no rows")
        return 1

    split = int(len(rows) * float(args.train_frac))
    split = max(1, min(len(rows) - 1, split))
    train = rows[:split]
    val = rows[split:]

    cur_gate = Gate(
        min_hits=int(os.getenv("MEME_SIGNAL_PREQUOTE_MIN_HITS", "3") or 3),
        min_buys=int(os.getenv("MEME_SIGNAL_PREQUOTE_MIN_BUYS", "4") or 4),
        min_unique_buyers=int(os.getenv("MEME_SIGNAL_PREQUOTE_MIN_UNIQUE_BUYERS", "3") or 3),
        min_net_sol_in=float(os.getenv("MEME_SIGNAL_PREQUOTE_MIN_NET_SOL_IN", "1.0") or 1.0),
        min_signal_score=float(os.getenv("MEME_SIGNAL_PREQUOTE_MIN_SIGNAL_SCORE", "65") or 65),
        min_buy_sell_ratio=float(os.getenv("MEME_SIGNAL_PREQUOTE_MIN_BUY_SELL_RATIO", "0") or 0),
        max_top_buyer_share=float(os.getenv("MEME_SIGNAL_PREQUOTE_MAX_TOP_BUYER_SHARE", "0") or 0),
    )

    hits_grid = sorted(set([2, 3, 4, 5, int(cur_gate.min_hits)]))
    buys_grid = sorted(set([2, 3, 4, 5, int(cur_gate.min_buys)]))
    uniq_grid = sorted(set([2, 3, 4, int(cur_gate.min_unique_buyers)]))
    net_grid = sorted(set([0.50, 0.75, 1.00, 1.25, 1.50, 2.00, float(cur_gate.min_net_sol_in)]))
    score_grid = sorted(set([55.0, 60.0, 65.0, 70.0, float(cur_gate.min_signal_score)]))
    bs_grid = sorted(set([0.0, 1.0, 1.2, 1.5, float(cur_gate.min_buy_sell_ratio)]))
    top_grid = sorted(set([0.0, 0.45, 0.40, 0.35, float(cur_gate.max_top_buyer_share)]))

    best_gate = cur_gate
    best_train = _stats_for(train, cur_gate)
    best_val = _stats_for(val, cur_gate)
    ranked: list[dict[str, Any]] = []

    for h in hits_grid:
        for b in buys_grid:
            for u in uniq_grid:
                for n in net_grid:
                    for smin in score_grid:
                        for bs in bs_grid:
                            for top in top_grid:
                                g = Gate(
                                    min_hits=h,
                                    min_buys=b,
                                    min_unique_buyers=u,
                                    min_net_sol_in=float(n),
                                    min_signal_score=float(smin),
                                    min_buy_sell_ratio=float(bs),
                                    max_top_buyer_share=float(top),
                                )
                                s_tr = _stats_for(train, g)
                                s_va = _stats_for(val, g)
                                if s_tr.n < int(args.min_train) or s_va.n < int(args.min_val):
                                    continue
                                row = {
                                    "gate": _gate_to_env(g),
                                    "train": s_tr.__dict__,
                                    "val": s_va.__dict__,
                                }
                                ranked.append(row)
                                if s_va.score > best_val.score:
                                    best_gate = g
                                    best_train = s_tr
                                    best_val = s_va

    baseline_train = _stats_for(train, cur_gate)
    baseline_val = _stats_for(val, cur_gate)
    recommended = _suggest_one_change(cur_gate, best_gate, baseline_val, best_val)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "file": str(path),
        "horizon_s": int(args.horizon),
        "rows_total": int(len(rows)),
        "rows_train": int(len(train)),
        "rows_val": int(len(val)),
        "current": {
            "gate": _gate_to_env(cur_gate),
            "train": baseline_train.__dict__,
            "val": baseline_val.__dict__,
        },
        "best": {
            "gate": _gate_to_env(best_gate),
            "train": best_train.__dict__,
            "val": best_val.__dict__,
        },
        "recommended_change": recommended,
        "top": sorted(ranked, key=lambda r: float((r.get("val") or {}).get("score") or -9999.0), reverse=True)[:12],
    }

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_md(Path(args.out_md), report)

    print(f"wrote {out_json}")
    print(f"wrote {args.out_md}")
    print(
        "val_current="
        f"{baseline_val.mean_ret:+.4f}/{baseline_val.win_rate*100:.1f}% n={baseline_val.n} "
        "val_best="
        f"{best_val.mean_ret:+.4f}/{best_val.win_rate*100:.1f}% n={best_val.n}"
    )
    if recommended:
        print(f"recommended_change={recommended}")
    else:
        print("recommended_change=none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
