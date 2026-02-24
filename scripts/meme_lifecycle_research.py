#!/usr/bin/env python3
"""Lifecycle research report from signal_outcomes.jsonl.

Goal:
- Classify short-horizon meme price-action archetypes.
- Quantify how early demand metrics and starting market-cap relate to breakout odds.

This script intentionally uses lightweight heuristics so we can iterate quickly.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class SignalPath:
    rets: dict[int, float]
    mcap0: float | None
    metrics: dict[str, Any]


def _load(paths_file: Path) -> dict[tuple[str, float], SignalPath]:
    out: dict[tuple[str, float], SignalPath] = {}
    with paths_file.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            mint = obj.get("mint")
            signal_ts = float(obj.get("signal_ts") or 0.0)
            if not mint or signal_ts <= 0:
                continue
            key = (str(mint), signal_ts)
            row = out.get(key)
            if row is None:
                row = SignalPath(rets={}, mcap0=None, metrics={})
                out[key] = row
            try:
                h = int(obj.get("horizon_s") or 0)
                r = float(obj.get("ret") or 0.0)
                if h > 0:
                    row.rets[h] = r
            except Exception:
                pass
            if row.mcap0 is None:
                m0 = obj.get("marketcap0", obj.get("mcap0"))
                try:
                    if m0 is not None:
                        row.mcap0 = float(m0)
                except Exception:
                    pass
            if not row.metrics and isinstance(obj.get("metrics"), dict):
                row.metrics = obj.get("metrics") or {}
    return out


def _classify_path(r30: float, r120: float, r300: float, r900: float) -> str:
    m = max(r30, r120, r300, r900)
    if m < 0.08 and r900 < 0:
        return "slow_bleed_or_fail"
    if m >= 0.25 and r900 <= 0.10:
        return "spike_then_fade"
    if r30 <= 0.03 and r120 <= 0.08 and r300 >= 0.15 and r900 >= 0.20:
        return "late_breakout"
    if r30 >= 0.08 and r120 >= r30 and r300 >= r120 and r900 >= r300:
        return "one_way_expansion"
    if r900 > 0.12 and m < 0.25:
        return "grind_up"
    return "mixed_chop"


def _demand_bucket(metrics: dict[str, Any]) -> str:
    try:
        hits = int(metrics.get("hits") or 0)
    except Exception:
        hits = 0
    try:
        uniq = int(metrics.get("unique_buyers") or 0)
    except Exception:
        uniq = 0
    try:
        net = float(metrics.get("net_sol_in") or 0.0)
    except Exception:
        net = 0.0
    if hits >= 4 and uniq >= 4 and net >= 2.5:
        return "strong_demand"
    if hits >= 2 and uniq >= 2 and net >= 1.0:
        return "mid_demand"
    return "weak_demand"


def _mcap_bucket(mcap0: float | None) -> str:
    if mcap0 is None or mcap0 <= 0:
        return "unknown"
    if mcap0 <= 15_000:
        return "0-15k"
    if mcap0 <= 30_000:
        return "15-30k"
    if mcap0 <= 60_000:
        return "30-60k"
    if mcap0 <= 100_000:
        return "60-100k"
    if mcap0 <= 200_000:
        return "100-200k"
    return "200k+"


def _pct(n: int, d: int) -> str:
    if d <= 0:
        return "0.0%"
    return f"{(100.0 * float(n) / float(d)):.1f}%"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="data/signal_outcomes.jsonl")
    ap.add_argument("--out", default="data/meme_reports/lifecycle_research_latest.md")
    args = ap.parse_args()

    src = Path(args.file)
    out = Path(args.out)
    if not src.exists():
        raise SystemExit(f"missing file: {src}")

    rows = _load(src)
    required = (30, 120, 300, 900)
    paths: list[SignalPath] = []
    for row in rows.values():
        if all(h in row.rets for h in required):
            paths.append(row)

    if not paths:
        raise SystemExit("no paths with required horizons")

    # Focus on paths that actually moved at least 2% at one horizon.
    active = [p for p in paths if max(abs(p.rets[h]) for h in required) >= 0.02]

    pat = Counter()
    reach = Counter()
    by_dem = defaultdict(lambda: Counter())
    by_mcap = defaultdict(lambda: Counter())

    for p in active:
        r30, r120, r300, r900 = (p.rets[30], p.rets[120], p.rets[300], p.rets[900])
        label = _classify_path(r30, r120, r300, r900)
        pat[label] += 1
        mx = max(r30, r120, r300, r900)
        if mx >= 0.10:
            reach["10"] += 1
        if mx >= 0.25:
            reach["25"] += 1
        if mx >= 0.50:
            reach["50"] += 1
        if mx >= 1.00:
            reach["100"] += 1

        dkey = _demand_bucket(p.metrics)
        mkey = _mcap_bucket(p.mcap0)
        by_dem[dkey]["n"] += 1
        by_mcap[mkey]["n"] += 1
        if mx >= 0.50:
            by_dem[dkey]["r50"] += 1
            by_mcap[mkey]["r50"] += 1
        if mx >= 1.00:
            by_dem[dkey]["r100"] += 1
            by_mcap[mkey]["r100"] += 1
        if label == "late_breakout":
            by_dem[dkey]["late"] += 1
            by_mcap[mkey]["late"] += 1
        if label == "spike_then_fade":
            by_dem[dkey]["fade"] += 1
            by_mcap[mkey]["fade"] += 1

    lines: list[str] = []
    lines.append("# Meme Lifecycle Research")
    lines.append("")
    lines.append(f"- Source: `{src}`")
    lines.append(f"- Signals with 30/120/300/900 horizons: `{len(paths)}`")
    lines.append(f"- Active paths (>=2% move at any horizon): `{len(active)}`")
    lines.append("")
    lines.append("## Archetypes (Active Paths)")
    lines.append("")
    for k, v in pat.most_common():
        lines.append(f"- `{k}`: `{v}` ({_pct(v, len(active))})")
    lines.append("")
    lines.append("## Reach Odds (Active Paths)")
    lines.append("")
    lines.append(f"- max return >=10%: `{reach['10']}` ({_pct(reach['10'], len(active))})")
    lines.append(f"- max return >=25%: `{reach['25']}` ({_pct(reach['25'], len(active))})")
    lines.append(f"- max return >=50%: `{reach['50']}` ({_pct(reach['50'], len(active))})")
    lines.append(f"- max return >=100%: `{reach['100']}` ({_pct(reach['100'], len(active))})")
    lines.append("")
    lines.append("## By Demand Bucket")
    lines.append("")
    lines.append("| Bucket | N | >=50% | >=100% | Late Breakout | Spike->Fade |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for k in ("strong_demand", "mid_demand", "weak_demand"):
        d = by_dem.get(k, Counter())
        n = int(d.get("n", 0))
        if n <= 0:
            continue
        lines.append(
            f"| {k} | {n} | {_pct(int(d.get('r50', 0)), n)} | {_pct(int(d.get('r100', 0)), n)} | "
            f"{_pct(int(d.get('late', 0)), n)} | {_pct(int(d.get('fade', 0)), n)} |"
        )
    lines.append("")
    lines.append("## By Start Market-Cap Bucket")
    lines.append("")
    lines.append("| Bucket | N | >=50% | >=100% | Late Breakout | Spike->Fade |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for k in ("0-15k", "15-30k", "30-60k", "60-100k", "100-200k", "200k+", "unknown"):
        d = by_mcap.get(k, Counter())
        n = int(d.get("n", 0))
        if n <= 0:
            continue
        lines.append(
            f"| {k} | {n} | {_pct(int(d.get('r50', 0)), n)} | {_pct(int(d.get('r100', 0)), n)} | "
            f"{_pct(int(d.get('late', 0)), n)} | {_pct(int(d.get('fade', 0)), n)} |"
        )
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- This report only sees 30s..15m forward paths. It cannot capture multi-hour/day runners.")
    lines.append("- Use this for early-lifecycle edge only; pair with a longer-horizon recorder for full life history.")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

