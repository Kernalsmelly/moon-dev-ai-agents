#!/usr/bin/env python3
"""Replay-based regression guard for meme strategy variants.

Runs `scripts/meme_replay.py` on one baseline + N variants, then compares:
- net pnl
- max drawdown
- cluster tail-loss concentration
- dominant cluster leg concentration

This is meant to catch parameter changes that improve a headline metric while
quietly worsening tail/concentration risk.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
REPLAY = BASE / "scripts" / "meme_replay.py"


@dataclass
class Metrics:
    name: str
    trades: int
    wins: int
    pnl: float
    max_dd: float
    cluster_count: int
    cluster_tail_loss_share: float
    dominant_cluster_leg_share: float


def _safe_float(v: str | None, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _load_metrics(csv_path: Path, *, name: str) -> Metrics:
    rows: list[dict] = []
    if not csv_path.exists():
        return Metrics(name=name, trades=0, wins=0, pnl=0.0, max_dd=0.0, cluster_count=0, cluster_tail_loss_share=0.0, dominant_cluster_leg_share=0.0)
    with csv_path.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for r in reader:
            rows.append(r)
    try:
        rows.sort(key=lambda r: _safe_float(r.get("exit_ts"), 0.0))
    except Exception:
        pass

    pnls = [_safe_float(r.get("pnl_usd"), 0.0) for r in rows]
    wins = sum(1 for p in pnls if p > 0)
    pnl = sum(pnls)

    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        eq += p
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > max_dd:
            max_dd = dd

    clusters: dict[tuple[str, str], dict[str, float]] = {}
    for r in rows:
        k = (str(r.get("mint") or ""), str(r.get("entry_ts") or ""))
        d = clusters.setdefault(k, {"legs": 0.0, "pnl": 0.0})
        d["legs"] += 1.0
        d["pnl"] += _safe_float(r.get("pnl_usd"), 0.0)
    c_vals = list(clusters.values())
    c_n = len(c_vals)
    c_losses = [abs(float(c["pnl"])) for c in c_vals if float(c["pnl"]) < 0]
    c_total_loss = sum(c_losses)
    c_largest_loss = max(c_losses) if c_losses else 0.0
    c_tail = (c_largest_loss / c_total_loss) if c_total_loss > 0 else 0.0

    total_legs = sum(int(c["legs"]) for c in c_vals)
    dominant_legs = max((int(c["legs"]) for c in c_vals), default=0)
    dominant_leg_share = (dominant_legs / total_legs) if total_legs > 0 else 0.0

    return Metrics(
        name=name,
        trades=len(rows),
        wins=wins,
        pnl=pnl,
        max_dd=max_dd,
        cluster_count=c_n,
        cluster_tail_loss_share=c_tail,
        dominant_cluster_leg_share=dominant_leg_share,
    )


def _variant_csv(out_csv: Path, variant_name: str) -> Path:
    if variant_name == "baseline":
        return out_csv
    root = out_csv.with_suffix("")
    return Path(f"{root}.{variant_name}{out_csv.suffix or '.csv'}")


def _default_variants() -> list[dict]:
    return [
        {
            "name": "strict_quality",
            "MIN_MARKET_CAP_USD": 15000,
            "MIN_LIQUIDITY_USD": 15000,
            "MIN_BUYS_5M": 2,
            "MIN_TXNS_5M": 5,
            "MAX_5M_PUMP": 25.0,
        },
        {
            "name": "momentum_lean",
            "MIN_MARKET_CAP_USD": 12000,
            "MIN_PRICE_CHANGE_5M": 1.0,
            "MIN_BUY_SELL_RATIO_5M": 1.2,
            "MIN_VOLUME_5M": 500.0,
        },
        {
            "name": "conservative_mcap",
            "MIN_MARKET_CAP_USD": 25000,
            "MIN_LIQUIDITY_USD": 20000,
            "MIN_BUYS_5M": 3,
        },
    ]


def _score(m: Metrics) -> float:
    # Simple risk-adjusted score for ranking pass candidates.
    wr = (m.wins / m.trades) if m.trades > 0 else 0.0
    return float(m.pnl) - float(m.max_dd) - (2.0 * float(m.cluster_tail_loss_share)) - (0.5 * float(m.dominant_cluster_leg_share)) + (2.0 * wr)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=str(BASE / "data" / "meme_snapshots.jsonl"))
    ap.add_argument("--out", default=str(BASE / "data" / "meme_replay_regression.csv"))
    ap.add_argument("--config-file", default="", help="Optional base replay config JSON")
    ap.add_argument("--variants-file", default="", help="JSON list of variant overrides")
    ap.add_argument("--fee-usd", type=float, default=0.15)
    ap.add_argument("--slippage-mult", type=float, default=1.0)
    ap.add_argument("--scan-interval", type=int, default=60)
    ap.add_argument("--min-trades-ratio", type=float, default=0.35)
    ap.add_argument("--max-dd-delta", type=float, default=0.50)
    ap.add_argument("--min-pnl-delta", type=float, default=-0.25)
    ap.add_argument("--max-cluster-tail-delta", type=float, default=0.05)
    ap.add_argument("--max-dominant-leg-delta", type=float, default=0.10)
    ap.add_argument("--strict", action="store_true", help="exit non-zero when no variant passes")
    args = ap.parse_args()

    input_path = Path(args.input)
    out_path = Path(args.out)
    if not input_path.exists():
        print(f"missing input: {input_path}")
        return 2

    out_path.parent.mkdir(parents=True, exist_ok=True)
    variants_path: Path
    if args.variants_file:
        variants_path = Path(args.variants_file)
        if not variants_path.exists():
            print(f"missing variants-file: {variants_path}")
            return 2
        variants = json.loads(variants_path.read_text(encoding="utf-8"))
    else:
        variants = _default_variants()
        variants_path = out_path.parent / "meme_replay_regression_variants.json"
        variants_path.write_text(json.dumps(variants, indent=2), encoding="utf-8")

    baseline_cmd = [
        "python3",
        str(REPLAY),
        "--input",
        str(input_path),
        "--out",
        str(out_path),
        "--fee-usd",
        str(float(args.fee_usd)),
        "--slippage-mult",
        str(float(args.slippage_mult)),
        "--scan-interval",
        str(int(args.scan_interval)),
    ]
    if args.config_file:
        baseline_cmd.extend(["--config-file", str(args.config_file)])

    # Always generate a true baseline first.
    subprocess.run(baseline_cmd, check=True)

    # Then generate variant outputs (if any).
    if variants:
        var_cmd = list(baseline_cmd) + ["--variants-file", str(variants_path)]
        subprocess.run(var_cmd, check=True)

    all_names = ["baseline"] + [str((v or {}).get("name") or f"variant_{i+1}") for i, v in enumerate(variants)]
    metrics = []
    for nm in all_names:
        p = _variant_csv(out_path, nm)
        metrics.append(_load_metrics(p, name=nm))

    base = metrics[0]
    print("Replay Regression Check")
    print(
        f"baseline: trades={base.trades} pnl={base.pnl:+.2f} max_dd={base.max_dd:.2f} "
        f"clusters={base.cluster_count} tail={base.cluster_tail_loss_share:.1%} dom_legs={base.dominant_cluster_leg_share:.1%}"
    )
    print("")
    print("variants:")

    passing: list[Metrics] = []
    base_trades = max(1, int(base.trades))
    for m in metrics[1:]:
        checks = {
            "trades": m.trades >= int(math.ceil(base_trades * float(args.min_trades_ratio))),
            "pnl": m.pnl >= (base.pnl + float(args.min_pnl_delta)),
            "dd": m.max_dd <= (base.max_dd + float(args.max_dd_delta)),
            "tail": m.cluster_tail_loss_share <= (base.cluster_tail_loss_share + float(args.max_cluster_tail_delta)),
            "dom_leg": m.dominant_cluster_leg_share <= (base.dominant_cluster_leg_share + float(args.max_dominant_leg_delta)),
        }
        ok = all(checks.values())
        flag = "PASS" if ok else "FAIL"
        print(
            f"- {m.name:16s} {flag} "
            f"trades={m.trades:4d} pnl={m.pnl:+7.2f} max_dd={m.max_dd:6.2f} "
            f"tail={m.cluster_tail_loss_share:6.1%} dom_legs={m.dominant_cluster_leg_share:6.1%} "
            f"checks={checks}"
        )
        if ok:
            passing.append(m)

    if passing:
        best = sorted(passing, key=_score, reverse=True)[0]
        print("")
        print(
            "recommended:"
            f" {best.name} (pnl={best.pnl:+.2f}, max_dd={best.max_dd:.2f}, "
            f"tail={best.cluster_tail_loss_share:.1%}, dom_legs={best.dominant_cluster_leg_share:.1%})"
        )
        return 0

    print("")
    print("recommended: none (no variant passed regression gates)")
    return 1 if args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
