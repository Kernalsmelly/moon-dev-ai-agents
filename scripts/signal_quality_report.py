#!/usr/bin/env python3
"""Generate a signal-quality report (JSON + Markdown) from local JSONL files.

This avoids paid APIs and focuses on which signal metrics improve mean return.
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")


def _read_last_lines(path: Path, max_lines: int) -> list[str]:
    if max_lines <= 0 or not path.exists():
        return []
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
        out[mint] = ret
    return out


def _bucket(v: float, edges: list[float]) -> str:
    for i, e in enumerate(edges):
        if v < e:
            lo = "-inf" if i == 0 else f"{edges[i-1]:g}"
            return f"[{lo},{e:g})"
    return f"[{edges[-1]:g},+inf)"


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


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


def _metric_float(s: Sig, name: str) -> float | None:
    v = (s.metrics or {}).get(name)
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


def summarize(joined: list[tuple[Sig, float]], metric: str, edges: list[float]) -> dict[str, Any]:
    groups: dict[str, list[float]] = {}
    present = 0
    for s, r in joined:
        v = _metric_float(s, metric)
        if v is None:
            continue
        present += 1
        groups.setdefault(_bucket(v, edges), []).append(r)

    out = {
        "metric": metric,
        "present": present,
        "buckets": [],
        "suggested_gate": None,
    }

    if present == 0:
        return out

    # baseline
    all_rets = [r for _, r in joined]
    base_mean = _mean(all_rets) or 0.0
    base_win = sum(1 for r in all_rets if r > 0) / max(1, len(all_rets))

    for k in sorted(groups.keys()):
        xs = groups[k]
        win = sum(1 for r in xs if r > 0) / max(1, len(xs))
        out["buckets"].append({
            "bucket": k,
            "n": len(xs),
            "mean": _mean(xs),
            "p50": _pct(xs, 0.5),
            "p90": _pct(xs, 0.9),
            "win_rate": win,
        })

    # Heuristic: choose the strongest bucket that has enough samples and
    # improves both mean and win-rate versus baseline. This avoids selecting
    # the first qualifying bucket in sort order.
    candidate = None
    candidate_score = None
    for b in out["buckets"]:
        if (b["n"] or 0) < 30:
            continue
        mean = float(b["mean"] or 0.0)
        win = float(b["win_rate"] or 0.0)
        if mean <= base_mean:
            continue
        if win <= base_win:
            continue
        # Weighted uplift score: prioritize mean improvement, then win-rate,
        # with a small sample-size bonus.
        score = (mean - base_mean) * 100.0 + (win - base_win) * 10.0 + (b["n"] ** 0.5) * 0.05
        if candidate is None or score > float(candidate_score or -1e18):
            candidate = b
            candidate_score = score

    if candidate:
        out["suggested_gate"] = {
            "bucket": candidate["bucket"],
            "n": candidate["n"],
            "mean": candidate["mean"],
            "win_rate": candidate["win_rate"],
        }
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--signals", default=os.getenv("MEME_LAUNCH_SIGNALS_FILE") or str(BASE / "data/meme_launch_signals_pump.jsonl"))
    p.add_argument("--outcomes", default=os.getenv("SIGNAL_OUTCOMES_FILE") or str(BASE / "data/signal_outcomes.jsonl"))
    p.add_argument("--horizon", type=int, default=300)
    p.add_argument("--signals-limit", type=int, default=5000)
    p.add_argument("--outcomes-limit", type=int, default=20000)
    p.add_argument("--out-json", default=str(BASE / "data" / "signal_quality_report.json"))
    p.add_argument("--out-md", default=str(BASE / "data" / "signal_quality_report.md"))
    args = p.parse_args()

    sigs = load_signals(Path(args.signals), args.signals_limit)
    outs = load_outcomes(Path(args.outcomes), args.horizon, args.outcomes_limit)

    joined: list[tuple[Sig, float]] = []
    for mint, ret in outs.items():
        s = sigs.get(mint)
        if s:
            joined.append((s, ret))

    payload: dict[str, Any] = {
        "horizon_s": args.horizon,
        "signals": len(sigs),
        "outcomes": len(outs),
        "joined": len(joined),
        "summary": {},
        "metrics": [],
    }

    if not joined:
        Path(args.out_json).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        Path(args.out_md).write_text("No joined signals/outcomes.\n", encoding="utf-8")
        print("signal_quality_report: no joined data")
        return 0

    rets = [r for _, r in joined]
    payload["summary"] = {
        "mean": _mean(rets),
        "p50": _pct(rets, 0.5),
        "p90": _pct(rets, 0.9),
        "win_rate": sum(1 for r in rets if r > 0) / max(1, len(rets)),
    }

    for metric, edges in [
        ("net_sol_in", [0.25, 0.5, 0.75, 1.0, 2.0, 4.0]),
        ("unique_buyers", [2, 3, 5, 8, 12]),
        ("top_buyer_share", [0.35, 0.5, 0.65, 0.8]),
        ("buy_accel", [0.0, 0.05, 0.1, 0.2]),
        ("t_first_sell_s", [10, 20, 40, 80]),
    ]:
        payload["metrics"].append(summarize(joined, metric, edges))

    Path(args.out_json).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # Simple markdown summary
    lines = []
    lines.append(f"# Signal Quality Report (horizon {args.horizon}s)")
    lines.append("")
    lines.append(f"Joined: {payload['joined']} | Signals: {payload['signals']} | Outcomes: {payload['outcomes']}")
    lines.append("")
    s = payload["summary"]
    lines.append(f"Mean ret: {s['mean']:+.4f} | p50: {s['p50']:+.4f} | p90: {s['p90']:+.4f} | win: {s['win_rate']*100:.1f}%")
    lines.append("")
    for m in payload["metrics"]:
        lines.append(f"## {m['metric']} (present={m['present']})")
        for b in m["buckets"]:
            lines.append(
                f"- {b['bucket']} n={b['n']} mean={b['mean']:+.4f} win={b['win_rate']*100:.1f}%"
            )
        if m.get("suggested_gate"):
            sg = m["suggested_gate"]
            lines.append(f"- Suggested gate: {sg['bucket']} (n={sg['n']}, mean={sg['mean']:+.4f}, win={sg['win_rate']*100:.1f}%)")
        lines.append("")

    Path(args.out_md).write_text("\n".join(lines), encoding="utf-8")
    print(f"signal_quality_report: wrote {args.out_json} and {args.out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
