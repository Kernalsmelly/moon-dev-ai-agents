#!/usr/bin/env python3
"""Quick offline edge analysis for WS launch signals.

This script joins:
- MEME_LAUNCH_SIGNALS_FILE (signals + early-demand metrics + score)
- data/signal_outcomes.jsonl (forward returns from Jupiter quotes)

and prints simple summaries to help tune entry gates without guessing.

It does not require paid APIs; it reads local JSONL only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")


def _read_last_lines(path: Path, max_lines: int) -> list[str]:
    if max_lines <= 0:
        return []
    if not path.exists():
        return []
    # Read from the end, enough bytes to cover roughly max_lines.
    # 512 bytes/line is generous for these JSONLs.
    want_bytes = max(4096, int(max_lines) * 512)
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - want_bytes))
            chunk = fh.read().decode("utf-8", errors="ignore")
    except Exception:
        return []
    lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
    return lines[-max_lines:] if len(lines) > max_lines else lines


@dataclass
class Sig:
    ts: float
    score: float
    metrics: dict[str, Any]


def load_signals(path: Path, limit: int) -> dict[str, Sig]:
    out: dict[str, Sig] = {}
    for ln in _read_last_lines(path, limit):
        try:
            obj = json.loads(ln)
        except Exception:
            continue
        mint = obj.get("mint")
        if not isinstance(mint, str) or not mint:
            continue
        try:
            ts = float(obj.get("ts", 0.0) or 0.0)
        except Exception:
            ts = 0.0
        try:
            score = float(obj.get("score", 0.0) or 0.0)
        except Exception:
            score = 0.0
        metrics = obj.get("metrics") if isinstance(obj.get("metrics"), dict) else {}
        out[mint] = Sig(ts=ts, score=score, metrics=metrics or {})
    return out


def load_outcomes(path: Path, horizon_s: int, limit: int) -> dict[str, float]:
    out: dict[str, float] = {}
    for ln in _read_last_lines(path, limit):
        try:
            obj = json.loads(ln)
        except Exception:
            continue
        if int(obj.get("horizon_s", -1) or -1) != int(horizon_s):
            continue
        mint = obj.get("mint")
        if not isinstance(mint, str) or not mint:
            continue
        try:
            ret = float(obj.get("ret", 0.0) or 0.0)
        except Exception:
            continue
        # Keep the latest computed ret for that mint/horizon.
        out[mint] = ret
    return out


def _bucket(v: float, edges: list[float]) -> str:
    # edges are ascending cutpoints; produce a label.
    for i, e in enumerate(edges):
        if v < e:
            lo = "-inf" if i == 0 else f"{edges[i-1]:g}"
            return f"[{lo},{e:g})"
    return f"[{edges[-1]:g},+inf)"


def _mean(xs: list[float]) -> float | None:
    if not xs:
        return None
    return sum(xs) / len(xs)


def _pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    ys = sorted(xs)
    if len(ys) == 1:
        return ys[0]
    k = (len(ys) - 1) * q
    f = int(k)
    c = min(len(ys) - 1, f + 1)
    if f == c:
        return ys[f]
    return ys[f] * (c - k) + ys[c] * (k - f)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--signals", default=os.getenv("MEME_LAUNCH_SIGNALS_FILE") or str(BASE / "data/meme_launch_signals_pump.jsonl"))
    p.add_argument("--outcomes", default=os.getenv("SIGNAL_OUTCOMES_FILE") or str(BASE / "data/signal_outcomes.jsonl"))
    p.add_argument("--horizon", type=int, default=300)
    p.add_argument("--signals-limit", type=int, default=5000)
    p.add_argument("--outcomes-limit", type=int, default=20000)
    args = p.parse_args(argv)

    sig_path = Path(args.signals)
    out_path = Path(args.outcomes)

    sigs = load_signals(sig_path, args.signals_limit)
    outs = load_outcomes(out_path, args.horizon, args.outcomes_limit)

    joined: list[tuple[Sig, float]] = []
    for mint, ret in outs.items():
        s = sigs.get(mint)
        if not s:
            continue
        joined.append((s, ret))

    print(f"analyze_signal_edge horizon_s={args.horizon} joined={len(joined)} signals={len(sigs)} outcomes={len(outs)}", flush=True)
    if not joined:
        return 0

    rets = [r for _, r in joined]
    win = sum(1 for r in rets if r > 0)
    print(f"ret mean={_mean(rets):+.4f} p50={_pct(rets,0.5):+.4f} p90={_pct(rets,0.9):+.4f} win_rate={100.0*win/len(rets):.1f}%", flush=True)

    # Score buckets
    buckets: dict[str, list[float]] = {}
    for s, r in joined:
        b = _bucket(float(s.score or 0.0), [5, 10, 20, 35, 50, 65, 80])
        buckets.setdefault(b, []).append(r)
    print("by_score_bucket:", flush=True)
    for k in sorted(buckets.keys()):
        xs = buckets[k]
        w = sum(1 for r in xs if r > 0)
        print(f"  {k} n={len(xs)} mean={_mean(xs):+.4f} p50={_pct(xs,0.5):+.4f} win={100.0*w/len(xs):.1f}%", flush=True)

    # A few key metrics buckets (only if present).
    def metric_float(s: Sig, name: str) -> float | None:
        v = (s.metrics or {}).get(name)
        if v is None:
            return None
        try:
            return float(v)
        except Exception:
            return None

    for name, edges in [
        ("net_sol_in", [0.25, 0.5, 1.0, 2.0, 4.0]),
        ("unique_buyers", [2, 3, 5, 8, 12]),
        ("top_buyer_share", [0.35, 0.5, 0.65, 0.8]),
        ("buy_accel", [0.0, 0.05, 0.1, 0.2]),
        ("t_first_sell_s", [10, 20, 40, 80]),
    ]:
        groups: dict[str, list[float]] = {}
        present = 0
        for s, r in joined:
            v = metric_float(s, name)
            if v is None:
                continue
            present += 1
            groups.setdefault(_bucket(v, edges), []).append(r)
        if present < 30:
            continue
        print(f"by_metric {name} (present={present}):", flush=True)
        for k in sorted(groups.keys()):
            xs = groups[k]
            w = sum(1 for r in xs if r > 0)
            print(f"  {k} n={len(xs)} mean={_mean(xs):+.4f} p50={_pct(xs,0.5):+.4f} win={100.0*w/len(xs):.1f}%", flush=True)

    return 0


if __name__ == "__main__":
    load_dotenv(dotenv_path=str(BASE / ".env"), override=True)
    raise SystemExit(main())

