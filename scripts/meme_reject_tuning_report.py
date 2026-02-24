#!/usr/bin/env python3
"""Summarize reject mix and propose one conservative next gate tweak.

Purpose:
- While waiting for more trade outcomes, optimize throughput quality by looking
  at *where* candidates are rejected in the signal-first funnel.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents/.env"), override=False)
except Exception:
    pass

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
DEBUG_FILE = BASE / "data" / "meme_signal_debug.jsonl"


def _auto_run_id() -> str:
    log_path = BASE / "logs" / "meme_bot_early_edge_auto.log"
    if not log_path.exists():
        return ""
    try:
        data = log_path.read_bytes()
        if len(data) > 250_000:
            data = data[-250_000:]
        text = data.decode("utf-8", errors="ignore")
        lines = [ln for ln in text.splitlines() if "run_id=" in ln]
        if not lines:
            return ""
        rid = lines[-1].split("run_id=", 1)[1].strip()
        rid = rid.replace("[/dim]", "").split()[0].strip()
        return rid
    except Exception:
        return ""


def _f(v, d=0.0) -> float:
    try:
        if v is None:
            return d
        return float(v)
    except Exception:
        return d


def _q(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    ys = sorted(xs)
    i = int(round((len(ys) - 1) * p))
    return ys[max(0, min(len(ys) - 1, i))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(DEBUG_FILE))
    ap.add_argument("--minutes", type=int, default=180)
    ap.add_argument("--run-id", default="")
    ap.add_argument("--auto-run-id", action="store_true")
    args = ap.parse_args()

    rid = str(args.run_id or "").strip()
    if args.auto_run_id and not rid:
        rid = str(_auto_run_id() or "").strip()

    p = Path(args.file)
    if not p.exists():
        print(f"missing {p}")
        return 2

    cutoff = time.time() - float(args.minutes) * 60.0
    kinds = Counter()
    details: dict[str, list[dict]] = defaultdict(list)
    n = 0
    with p.open("r", encoding="utf-8") as fh:
        for ln in fh:
            try:
                o = json.loads(ln)
            except Exception:
                continue
            if rid and str(o.get("run_id") or "").strip() != rid:
                continue
            ts = _f(o.get("ts"), 0.0)
            if ts < cutoff:
                continue
            n += 1
            k = str(o.get("kind") or "unknown")
            kinds[k] += 1
            if isinstance(o.get("extra"), dict):
                details[k].append(o.get("extra") or {})

    if rid:
        print(f"run_id={rid}")
    print(f"window={args.minutes}m events={n}")
    if n == 0:
        print("no data in window")
        return 0

    pass_prequote = int(kinds.get("pass_prequote", 0))
    reject_total = sum(v for k, v in kinds.items() if k.startswith("reject_"))
    prequote_total = pass_prequote + reject_total
    prequote_pass_rate = (pass_prequote / prequote_total) if prequote_total > 0 else 0.0

    print(f"pass_prequote={pass_prequote} reject_total={reject_total} prequote_pass_rate={prequote_pass_rate:.1%}")
    print("")
    print("top_rejects:")
    for k, c in [(k, v) for k, v in kinds.most_common(12) if k.startswith("reject_")]:
        share = (c / reject_total) if reject_total > 0 else 0.0
        print(f"- {k:24s} n={c:5d} share={share:.1%}")

    # Build conservative suggestions from observed near-threshold rejects.
    suggestions: list[tuple[str, float, str]] = []

    # Score gate
    if kinds.get("reject_prequote_score", 0) > 0:
        xs = details.get("reject_prequote_score", [])
        scores = [_f(d.get("score"), 0.0) for d in xs]
        mins = [_f(d.get("min_score"), 0.0) for d in xs]
        if scores and mins:
            min_score = statistics.median(mins)
            near = sum(1 for s in scores if s >= (min_score - 5.0))
            near_ratio = (near / len(scores)) if scores else 0.0
            if near_ratio >= 0.35:
                new_v = max(0.0, min_score - 2.0)
                suggestions.append(
                    (
                        0.70 * near_ratio + 0.20,
                        new_v,
                        f"MEME_SIGNAL_PREQUOTE_MIN_SIGNAL_SCORE={new_v:.0f} (near-threshold rejects {near_ratio:.1%})",
                    )
                )

    # Net SOL gate
    if kinds.get("reject_prequote_net", 0) > 0:
        xs = details.get("reject_prequote_net", [])
        nets = [_f(d.get("net_sol_in"), 0.0) for d in xs]
        mins = [_f(d.get("min_net_sol_in"), 0.0) for d in xs]
        if nets and mins:
            min_net = statistics.median(mins)
            q75 = _q(nets, 0.75)
            if q75 >= (0.80 * min_net):
                new_v = max(0.10, round(min_net - 0.10, 2))
                suggestions.append(
                    (
                        0.50 + min(0.40, q75 / max(min_net, 1e-9)),
                        new_v,
                        f"MEME_SIGNAL_PREQUOTE_MIN_NET_SOL_IN={new_v:.2f} (q75 reject net={q75:.2f}, min={min_net:.2f})",
                    )
                )

    # MCap low gate
    if kinds.get("reject_mcap_low", 0) > 0:
        xs = details.get("reject_mcap_low", [])
        mcaps = [_f(d.get("mcap"), 0.0) for d in xs if _f(d.get("mcap"), 0.0) > 0]
        mins = [_f(d.get("min_mcap"), 0.0) for d in xs if _f(d.get("min_mcap"), 0.0) > 0]
        if mcaps and mins:
            min_m = statistics.median(mins)
            q75 = _q(mcaps, 0.75)
            # Only suggest loosening if most rejects are very close; otherwise keep strict.
            if q75 >= (0.92 * min_m):
                new_v = max(0.0, float(round((min_m - 1000.0), -2)))
                suggestions.append(
                    (
                        0.35 + (q75 / max(min_m, 1e-9)),
                        new_v,
                        f"MEME_SIGNAL_MIN_MCAP_USD={new_v:.0f} (mcap rejects mostly near floor, q75={q75:.0f}, min={min_m:.0f})",
                    )
                )

    # Liquidity gate
    if kinds.get("reject_liq_low_signal", 0) > 0:
        xs = details.get("reject_liq_low_signal", [])
        liqs = [_f(d.get("liq"), 0.0) for d in xs if _f(d.get("liq"), 0.0) > 0]
        mins = [_f(d.get("min_liq"), 0.0) for d in xs if _f(d.get("min_liq"), 0.0) > 0]
        if liqs and mins:
            min_l = statistics.median(mins)
            q75 = _q(liqs, 0.75)
            if q75 >= (0.90 * min_l):
                new_v = max(0.0, float(round((min_l - 2000.0), -2)))
                suggestions.append(
                    (
                        0.30 + (q75 / max(min_l, 1e-9)),
                        new_v,
                        f"MEME_MIN_LIQUIDITY={new_v:.0f} (liq rejects near floor, q75={q75:.0f}, min={min_l:.0f})",
                    )
                )

    print("")
    if not suggestions:
        print("recommended: none (no conservative single-lever change from reject mix)")
        return 0

    suggestions.sort(key=lambda x: float(x[0]), reverse=True)
    _, _, best = suggestions[0]
    print("recommended_next_single_change:")
    print(f"- {best}")
    print("")
    print("other_candidates:")
    for _, _, text in suggestions[1:4]:
        print(f"- {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
