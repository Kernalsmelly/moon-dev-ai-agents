#!/usr/bin/env python3
"""Summarize recent signal progress and verified winner cohorts.

This report is intentionally signal-centric rather than trade-centric:
- ingest `data/signal_outcomes.jsonl`
- collapse rows by `signal_key`
- show how each signaled coin progressed through forward-return horizons
- identify "verified winners" from objective forward-return thresholds
"""

from __future__ import annotations

import argparse
import json
import math
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
DEFAULT_IN = BASE / "data" / "signal_outcomes.jsonl"
DEFAULT_OUT_JSON = BASE / "data" / "meme_reports" / "signal_progress_report.json"
DEFAULT_OUT_MD = BASE / "data" / "meme_reports" / "signal_progress_report.md"


def _to_float(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        out = float(value)
        if math.isfinite(out):
            return out
    except Exception:
        return None
    return None


def _signal_key(row: dict[str, Any]) -> str | None:
    raw = row.get("signal_key")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    mint = str(row.get("mint") or "").strip()
    signal_ts = _to_float(row.get("signal_ts"))
    signal_source = str(row.get("signal_source") or "").strip().lower()
    if mint and signal_ts is not None:
        return f"{mint}|{signal_ts:.6f}|{signal_source}"
    return None


def _max_ret(rets: dict[int, float], horizon_s: int) -> float | None:
    vals = [ret for hz, ret in rets.items() if int(hz) <= int(horizon_s)]
    if not vals:
        return None
    return max(vals)


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100.0:.1f}%"


def _pct(n: int, d: int) -> str:
    if d <= 0:
        return "0.0%"
    return f"{(100.0 * float(n) / float(d)):.1f}%"


def _classify_path(rets: dict[int, float]) -> str:
    r30 = float(rets.get(30) or 0.0)
    r120 = float(rets.get(120) or 0.0)
    r300 = float(rets.get(300) or 0.0)
    r900 = float(rets.get(900) or 0.0)
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


def _milestone(max_ret: float | None) -> str:
    if max_ret is None:
        return "none"
    if max_ret >= 2.0:
        return "200%+"
    if max_ret >= 1.0:
        return "100%+"
    if max_ret >= 0.50:
        return "50%+"
    if max_ret >= 0.25:
        return "25%+"
    if max_ret >= 0.10:
        return "10%+"
    return "<10%"


def _snapshot_from_row(row: dict[str, Any]) -> dict[str, Any]:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    return {
        "mint": str(row.get("mint") or "").strip(),
        "symbol": str((metrics.get("symbol") or row.get("symbol") or "")).strip(),
        "name": str((metrics.get("name") or row.get("name") or "")).strip(),
        "signal_ts": float(row.get("signal_ts") or 0.0),
        "run_id": str(row.get("run_id") or "").strip(),
        "signal_source": str(row.get("signal_source") or metrics.get("source") or "").strip().lower(),
        "signal_score": _to_float(row.get("signal_score") if row.get("signal_score") is not None else row.get("score0")),
        "mcap0": _to_float(row.get("mcap0") if row.get("mcap0") is not None else row.get("marketcap0")),
        "liq0": _to_float(row.get("liq0")),
        "pair_age_min0": _to_float(row.get("pair_age_min0")),
        "mom5m0": _to_float(metrics.get("price_change_5m") if metrics.get("price_change_5m") is not None else metrics.get("momentum_5m_pct")),
        "hits0": int(row.get("hits0") or 0),
        "buys0": int(row.get("buys0") or 0),
        "sells0": int(row.get("sells0") or 0),
        "uniq0": int(row.get("uniq0") or 0),
        "net_sol_in0": _to_float(row.get("net_sol_in0")),
        "mover_pattern0": str(row.get("mover_pattern0") or metrics.get("mover_pattern") or "").strip().lower() or "unknown",
        "url": str(metrics.get("url") or "").strip(),
    }


def _load_rows(path: Path, since_ts: float) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            signal_ts = _to_float(row.get("signal_ts"))
            if signal_ts is None or signal_ts < since_ts:
                continue
            key = _signal_key(row)
            if not key:
                continue
            g = grouped.setdefault(key, {"snapshot": _snapshot_from_row(row), "rets": {}, "rows": 0})
            g["rows"] += 1
            horizon = int(row.get("horizon_s") or 0)
            ret = _to_float(row.get("ret"))
            if horizon > 0 and ret is not None:
                g["rets"][horizon] = ret
    return grouped


def build_report(
    rows: dict[str, dict[str, Any]],
    *,
    winner_horizon_s: int,
    winner_ret: float,
    top: int,
) -> dict[str, Any]:
    signals: list[dict[str, Any]] = []
    source_counts: Counter[str] = Counter()
    pattern_counts: Counter[str] = Counter()
    milestone_counts: Counter[str] = Counter()
    winners: list[dict[str, Any]] = []
    mint_rollups: dict[str, dict[str, Any]] = {}

    for key, item in rows.items():
        snap = dict(item["snapshot"])
        rets = dict(item["rets"])
        max_5m = _max_ret(rets, 300)
        max_15m = _max_ret(rets, 900)
        max_30m = _max_ret(rets, 1800)
        max_60m = _max_ret(rets, 3600)
        max_all = max(rets.values()) if rets else None
        progress = {
            "signal_key": key,
            **snap,
            "ret_30s": _to_float(rets.get(30)),
            "ret_120s": _to_float(rets.get(120)),
            "ret_300s": _to_float(rets.get(300)),
            "ret_900s": _to_float(rets.get(900)),
            "ret_1800s": _to_float(rets.get(1800)),
            "ret_3600s": _to_float(rets.get(3600)),
            "max_ret_5m": max_5m,
            "max_ret_15m": max_15m,
            "max_ret_30m": max_30m,
            "max_ret_60m": max_60m,
            "max_ret_all": max_all,
            "milestone": _milestone(max_all),
            "archetype_15m": _classify_path(rets),
            "verified_winner": bool((_max_ret(rets, winner_horizon_s) or -1.0) >= winner_ret),
        }
        signals.append(progress)
        source_counts[str(progress.get("signal_source") or "unknown")] += 1
        pattern_counts[str(progress.get("mover_pattern0") or "unknown")] += 1
        milestone_counts[str(progress.get("milestone") or "none")] += 1
        if progress["verified_winner"]:
            winners.append(progress)
        mint = str(progress.get("mint") or "").strip()
        if mint:
            roll = mint_rollups.get(mint)
            if roll is None:
                roll = {
                    "mint": mint,
                    "symbol": progress.get("symbol") or "n/a",
                    "signal_source": progress.get("signal_source") or "unknown",
                    "first_signal_ts": progress.get("signal_ts") or 0.0,
                    "signal_count": 0,
                    "best_max_ret_all": float(progress.get("max_ret_all") or -1.0),
                    "best_max_ret_15m": float(progress.get("max_ret_15m") or -1.0),
                    "best_max_ret_30m": float(progress.get("max_ret_30m") or -1.0),
                    "best_milestone": progress.get("milestone") or "none",
                    "best_archetype_15m": progress.get("archetype_15m") or "unknown",
                    "mcap0_min": float(progress.get("mcap0") or 0.0),
                    "mcap0_max": float(progress.get("mcap0") or 0.0),
                    "age0_min": float(progress.get("pair_age_min0") or 0.0),
                    "age0_max": float(progress.get("pair_age_min0") or 0.0),
                }
                mint_rollups[mint] = roll
            roll["signal_count"] = int(roll.get("signal_count") or 0) + 1
            sig_ts = float(progress.get("signal_ts") or 0.0)
            if sig_ts > 0 and (float(roll.get("first_signal_ts") or 0.0) <= 0 or sig_ts < float(roll.get("first_signal_ts") or 0.0)):
                roll["first_signal_ts"] = sig_ts
            current_best = float(roll.get("best_max_ret_all") or -1.0)
            if float(progress.get("max_ret_all") or -1.0) > current_best:
                roll["best_max_ret_all"] = float(progress.get("max_ret_all") or -1.0)
                roll["best_max_ret_15m"] = float(progress.get("max_ret_15m") or -1.0)
                roll["best_max_ret_30m"] = float(progress.get("max_ret_30m") or -1.0)
                roll["best_milestone"] = progress.get("milestone") or "none"
                roll["best_archetype_15m"] = progress.get("archetype_15m") or "unknown"
                if progress.get("symbol"):
                    roll["symbol"] = progress.get("symbol")
                if progress.get("signal_source"):
                    roll["signal_source"] = progress.get("signal_source")
            mcap0 = float(progress.get("mcap0") or 0.0)
            age0 = float(progress.get("pair_age_min0") or 0.0)
            if mcap0 > 0:
                roll["mcap0_min"] = mcap0 if float(roll.get("mcap0_min") or 0.0) <= 0 else min(float(roll.get("mcap0_min") or 0.0), mcap0)
                roll["mcap0_max"] = max(float(roll.get("mcap0_max") or 0.0), mcap0)
            if age0 > 0:
                roll["age0_min"] = age0 if float(roll.get("age0_min") or 0.0) <= 0 else min(float(roll.get("age0_min") or 0.0), age0)
                roll["age0_max"] = max(float(roll.get("age0_max") or 0.0), age0)

    signals.sort(key=lambda row: float(row.get("signal_ts") or 0.0), reverse=True)
    winners.sort(key=lambda row: float(row.get("max_ret_all") or -1.0), reverse=True)
    unique_mints = sorted(
        mint_rollups.values(),
        key=lambda row: (float(row.get("best_max_ret_all") or -1.0), int(row.get("signal_count") or 0)),
        reverse=True,
    )
    horizon_label = f"{int(winner_horizon_s // 60)}m" if winner_horizon_s >= 60 else f"{winner_horizon_s}s"
    summary = {
        "signals": len(signals),
        "unique_mints": len(unique_mints),
        "verified_winners": len(winners),
        "verified_winner_rate": (float(len(winners)) / float(len(signals))) if signals else 0.0,
        "winner_definition": {
            "horizon_s": int(winner_horizon_s),
            "horizon_label": horizon_label,
            "ret_threshold": float(winner_ret),
        },
        "source_counts": dict(source_counts),
        "pattern_counts": dict(pattern_counts),
        "milestone_counts": dict(milestone_counts),
    }
    return {
        "generated_at": time.time(),
        "summary": summary,
        "top_winners": winners[:top],
        "top_unique_mints": unique_mints[:top],
        "recent_signals": signals[:top],
        "recent_verified_winners": [row for row in signals if row["verified_winner"]][:top],
    }


def write_markdown(path: Path, report: dict[str, Any], *, since_hours: float) -> None:
    summary = report.get("summary") or {}
    lines: list[str] = []
    lines.append("# Meme Signal Progress Report")
    lines.append("")
    lines.append(f"- Window: last `{since_hours:.0f}h`")
    lines.append(f"- Signals: `{int(summary.get('signals') or 0)}`")
    lines.append(f"- Unique mints: `{int(summary.get('unique_mints') or 0)}`")
    lines.append(
        f"- Verified winners: `{int(summary.get('verified_winners') or 0)}` "
        f"(`{_pct(int(summary.get('verified_winners') or 0), int(summary.get('signals') or 0))}`)"
    )
    winner_def = summary.get("winner_definition") or {}
    lines.append(
        f"- Winner definition: `max return >= {_fmt_pct(float(winner_def.get('ret_threshold') or 0.0))}` "
        f"within `{winner_def.get('horizon_label') or 'n/a'}`"
    )
    lines.append("")
    lines.append("## Source Mix")
    lines.append("")
    for source, count in sorted((summary.get("source_counts") or {}).items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"- `{source}`: `{count}`")
    lines.append("")
    lines.append("## Milestones")
    lines.append("")
    for name, count in sorted((summary.get("milestone_counts") or {}).items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"- `{name}`: `{count}`")
    lines.append("")
    lines.append("## Top Verified Winners")
    lines.append("")
    lines.append("| Symbol | Mint | Source | MCap0 | Age0 | Mom5m0 | Hits0 | NetSOL0 | Max 5m | Max 15m | Max 30m | Max All | Archetype |")
    lines.append("|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for row in report.get("top_winners") or []:
        lines.append(
            f"| {row.get('symbol') or 'n/a'} | `{row.get('mint')}` | `{row.get('signal_source') or 'n/a'}` | "
            f"{float(row.get('mcap0') or 0.0):.0f} | {float(row.get('pair_age_min0') or 0.0):.1f} | "
            f"{float(row.get('mom5m0') or 0.0):.2f} | {int(row.get('hits0') or 0)} | "
            f"{float(row.get('net_sol_in0') or 0.0):.2f} | {_fmt_pct(_to_float(row.get('max_ret_5m')))} | "
            f"{_fmt_pct(_to_float(row.get('max_ret_15m')))} | {_fmt_pct(_to_float(row.get('max_ret_30m')))} | "
            f"{_fmt_pct(_to_float(row.get('max_ret_all')))} | `{row.get('archetype_15m') or 'n/a'}` |"
        )
    lines.append("")
    lines.append("## Top Unique Mints")
    lines.append("")
    lines.append("| Symbol | Mint | Source | Signals | MCap Range | Age Range | Best 15m | Best 30m | Best All | Best Milestone | Best Archetype |")
    lines.append("|---|---|---|---:|---|---|---:|---:|---:|---|---|")
    for row in report.get("top_unique_mints") or []:
        mcap_lo = float(row.get("mcap0_min") or 0.0)
        mcap_hi = float(row.get("mcap0_max") or 0.0)
        age_lo = float(row.get("age0_min") or 0.0)
        age_hi = float(row.get("age0_max") or 0.0)
        lines.append(
            f"| {row.get('symbol') or 'n/a'} | `{row.get('mint')}` | `{row.get('signal_source') or 'n/a'}` | "
            f"{int(row.get('signal_count') or 0)} | `{mcap_lo:.0f}-{mcap_hi:.0f}` | `{age_lo:.1f}-{age_hi:.1f}` | "
            f"{_fmt_pct(_to_float(row.get('best_max_ret_15m')))} | {_fmt_pct(_to_float(row.get('best_max_ret_30m')))} | "
            f"{_fmt_pct(_to_float(row.get('best_max_ret_all')))} | `{row.get('best_milestone') or 'n/a'}` | "
            f"`{row.get('best_archetype_15m') or 'n/a'}` |"
        )
    lines.append("")
    lines.append("## Most Recent Signals")
    lines.append("")
    lines.append("| Symbol | Mint | Source | MCap0 | Age0 | Mom5m0 | Hits0 | NetSOL0 | Max 5m | Max 15m | Max 30m | Milestone |")
    lines.append("|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for row in report.get("recent_signals") or []:
        lines.append(
            f"| {row.get('symbol') or 'n/a'} | `{row.get('mint')}` | `{row.get('signal_source') or 'n/a'}` | "
            f"{float(row.get('mcap0') or 0.0):.0f} | {float(row.get('pair_age_min0') or 0.0):.1f} | "
            f"{float(row.get('mom5m0') or 0.0):.2f} | {int(row.get('hits0') or 0)} | "
            f"{float(row.get('net_sol_in0') or 0.0):.2f} | {_fmt_pct(_to_float(row.get('max_ret_5m')))} | "
            f"{_fmt_pct(_to_float(row.get('max_ret_15m')))} | {_fmt_pct(_to_float(row.get('max_ret_30m')))} | "
            f"`{row.get('milestone') or 'n/a'}` |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(DEFAULT_IN))
    ap.add_argument("--since-hours", type=float, default=24.0)
    ap.add_argument("--winner-horizon-s", type=int, default=900)
    ap.add_argument("--winner-ret", type=float, default=0.50)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--out-json", default=str(DEFAULT_OUT_JSON))
    ap.add_argument("--out-md", default=str(DEFAULT_OUT_MD))
    args = ap.parse_args()

    src = Path(args.file)
    if not src.exists():
        raise SystemExit(f"missing file: {src}")

    since_ts = time.time() - (float(args.since_hours) * 3600.0)
    grouped = _load_rows(src, since_ts)
    report = build_report(
        grouped,
        winner_horizon_s=int(args.winner_horizon_s),
        winner_ret=float(args.winner_ret),
        top=max(1, int(args.top)),
    )

    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_markdown(out_md, report, since_hours=float(args.since_hours))
    print(f"wrote {out_json}")
    print(f"wrote {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
