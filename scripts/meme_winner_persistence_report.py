#!/usr/bin/env python3
"""Classify earliest-useful winners by persistence after the initial move.

Purpose:
- start from the earliest signal per mint that still led to +50% within 15m
- measure what happened by later horizons, especially 6h
- split winners into persistence classes that are closer to something tradable later
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
DEFAULT_IN = BASE / "data" / "signal_outcomes.jsonl"
DEFAULT_OUT_JSON = BASE / "data" / "meme_reports" / "winner_persistence_report.json"
DEFAULT_OUT_MD = BASE / "data" / "meme_reports" / "winner_persistence_report.md"


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


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100.0:.1f}%"


def _fmt_num(value: float | None, digits: int = 0, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}{suffix}"


def _median(values: list[float | None]) -> float | None:
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not clean:
        return None
    return float(statistics.median(clean))


def _classify_short_path(rets: dict[int, float]) -> str:
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


def _max_ret(rets: dict[int, float], horizon_s: int) -> float | None:
    vals = [ret for hz, ret in rets.items() if int(hz) <= int(horizon_s)]
    if not vals:
        return None
    return max(vals)


def _extract_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    return {
        "mint": str(row.get("mint") or "").strip(),
        "symbol": str((metrics.get("symbol") or row.get("symbol") or "")).strip() or "n/a",
        "signal_ts": float(row.get("signal_ts") or 0.0),
        "signal_source": str(row.get("signal_source") or metrics.get("source") or "").strip().lower() or "unknown",
        "mcap0": _to_float(row.get("mcap0") if row.get("mcap0") is not None else row.get("marketcap0")),
        "liq0": _to_float(row.get("liq0")),
        "pair_age_min0": _to_float(row.get("pair_age_min0")),
        "mom5m0": _to_float(metrics.get("price_change_5m") if metrics.get("price_change_5m") is not None else metrics.get("momentum_5m_pct")),
        "hits0": int(row.get("hits0") or 0),
        "buys0": int(row.get("buys0") or 0),
        "uniq0": int(row.get("uniq0") or 0),
        "net_sol_in0": _to_float(row.get("net_sol_in0")),
        "mover_pattern0": str(row.get("mover_pattern0") or metrics.get("mover_pattern") or "").strip().lower() or "unknown",
        "url": str(metrics.get("url") or "").strip(),
    }


def load_rows(path: Path, since_ts: float) -> dict[str, list[dict[str, Any]]]:
    by_mint: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
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
            mint = str(row.get("mint") or "").strip()
            key = _signal_key(row)
            if not mint or not key:
                continue
            grp = by_mint[mint].setdefault(
                key,
                {
                    "snapshot": _extract_snapshot(row),
                    "rets": {},
                },
            )
            hz = int(row.get("horizon_s") or 0)
            ret = _to_float(row.get("ret"))
            if hz > 0 and ret is not None:
                grp["rets"][hz] = ret

    out: dict[str, list[dict[str, Any]]] = {}
    for mint, items in by_mint.items():
        rows: list[dict[str, Any]] = []
        for key, item in items.items():
            snap = dict(item["snapshot"])
            rets = dict(item["rets"])
            snap["signal_key"] = key
            snap["ret_300s"] = _to_float(rets.get(300))
            snap["ret_900s"] = _to_float(rets.get(900))
            snap["ret_1800s"] = _to_float(rets.get(1800))
            snap["ret_3600s"] = _to_float(rets.get(3600))
            snap["ret_7200s"] = _to_float(rets.get(7200))
            snap["ret_14400s"] = _to_float(rets.get(14400))
            snap["ret_21600s"] = _to_float(rets.get(21600))
            snap["max_ret_900s"] = _max_ret(rets, 900)
            snap["max_ret_1800s"] = _max_ret(rets, 1800)
            snap["max_ret_21600s"] = _max_ret(rets, 21600)
            snap["max_ret_all"] = max(rets.values()) if rets else None
            snap["archetype_15m"] = _classify_short_path(rets)
            rows.append(snap)
        rows.sort(key=lambda r: float(r.get("signal_ts") or 0.0))
        out[mint] = rows
    return out


def first_useful(rows: list[dict[str, Any]], winner_ret: float) -> dict[str, Any] | None:
    for row in rows:
        value = _to_float(row.get("max_ret_900s"))
        if value is not None and value >= winner_ret:
            return row
    return None


def classify_persistence(row: dict[str, Any], *, persistent_ret: float, persistent_retain: float, round_trip_retain: float) -> tuple[str, float | None]:
    peak = _to_float(row.get("max_ret_all"))
    ret_6h = _to_float(row.get("ret_21600s"))
    if ret_6h is None or peak is None or peak <= 0:
        return "pending_6h", None
    retain = ret_6h / peak
    if ret_6h >= persistent_ret or retain >= persistent_retain:
        return "persistent_runner", retain
    if ret_6h <= 0.0:
        if str(row.get("archetype_15m") or "") in {"spike_then_fade", "mixed_chop", "late_breakout"}:
            return "short_lived_spike", retain
        return "round_trip_winner", retain
    if retain <= round_trip_retain:
        return "round_trip_winner", retain
    return "partial_persistence", retain


def build_report(
    rows_by_mint: dict[str, list[dict[str, Any]]],
    *,
    winner_ret: float,
    persistent_ret: float,
    persistent_retain: float,
    round_trip_retain: float,
    top: int,
) -> dict[str, Any]:
    winners: list[dict[str, Any]] = []
    class_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    first_source_counts = Counter()
    useful_source_counts = Counter()

    for mint, rows in rows_by_mint.items():
        first = rows[0]
        first_source_counts[str(first.get("signal_source") or "unknown")] += 1
        useful = first_useful(rows, winner_ret)
        if useful is None:
            continue
        useful_source_counts[str(useful.get("signal_source") or "unknown")] += 1
        klass, retain_6h = classify_persistence(
            useful,
            persistent_ret=persistent_ret,
            persistent_retain=persistent_retain,
            round_trip_retain=round_trip_retain,
        )
        record = {
            "mint": mint,
            "symbol": useful.get("symbol") or "n/a",
            "signal_source": useful.get("signal_source") or "unknown",
            "signal_ts": useful.get("signal_ts") or 0.0,
            "mcap0": _to_float(useful.get("mcap0")),
            "liq0": _to_float(useful.get("liq0")),
            "pair_age_min0": _to_float(useful.get("pair_age_min0")),
            "mom5m0": _to_float(useful.get("mom5m0")),
            "hits0": int(useful.get("hits0") or 0),
            "buys0": int(useful.get("buys0") or 0),
            "uniq0": int(useful.get("uniq0") or 0),
            "net_sol_in0": _to_float(useful.get("net_sol_in0")),
            "mover_pattern0": useful.get("mover_pattern0") or "unknown",
            "archetype_15m": useful.get("archetype_15m") or "unknown",
            "max_ret_15m": _to_float(useful.get("max_ret_900s")),
            "max_ret_30m": _to_float(useful.get("max_ret_1800s")),
            "max_ret_6h": _to_float(useful.get("max_ret_21600s")),
            "ret_6h": _to_float(useful.get("ret_21600s")),
            "retention_6h": retain_6h,
            "persistence_class": klass,
            "url": useful.get("url") or "",
        }
        winners.append(record)
        class_rows[klass].append(record)

    winners.sort(key=lambda row: float(row.get("max_ret_6h") or row.get("max_ret_30m") or -1.0), reverse=True)

    def class_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "n": len(rows),
            "median_mcap0": _median([_to_float(r.get("mcap0")) for r in rows]),
            "median_age0": _median([_to_float(r.get("pair_age_min0")) for r in rows]),
            "median_mom5m0": _median([_to_float(r.get("mom5m0")) for r in rows]),
            "median_hits0": _median([_to_float(r.get("hits0")) for r in rows]),
            "median_net_sol_in0": _median([_to_float(r.get("net_sol_in0")) for r in rows]),
            "median_max_ret_15m": _median([_to_float(r.get("max_ret_15m")) for r in rows]),
            "median_ret_6h": _median([_to_float(r.get("ret_6h")) for r in rows]),
            "median_retention_6h": _median([_to_float(r.get("retention_6h")) for r in rows]),
            "source_counts": dict(Counter(str(r.get("signal_source") or "unknown") for r in rows)),
            "pattern_counts": dict(Counter(str(r.get("mover_pattern0") or "unknown") for r in rows)),
            "archetype_counts": dict(Counter(str(r.get("archetype_15m") or "unknown") for r in rows)),
        }

    summary = {
        "unique_mints": len(rows_by_mint),
        "earliest_useful_winners": len(winners),
        "class_counts": dict(Counter(str(r.get("persistence_class") or "unknown") for r in winners)),
        "first_signal_source_counts": dict(first_source_counts),
        "earliest_useful_source_counts": dict(useful_source_counts),
    }

    return {
        "generated_at": time.time(),
        "summary": summary,
        "class_summaries": {klass: class_summary(rows) for klass, rows in sorted(class_rows.items())},
        "top_winners": winners[:top],
    }


def write_md(path: Path, report: dict[str, Any], *, since_hours: float, winner_ret: float) -> None:
    s = report["summary"]
    lines = [
        "# Winner Persistence Report",
        "",
        f"- Window: last `{since_hours:.0f}h`",
        f"- Earliest useful winner definition: `+{winner_ret * 100:.0f}% within 15m`",
        f"- Unique mints: `{int(s.get('unique_mints') or 0)}`",
        f"- Earliest useful winners: `{int(s.get('earliest_useful_winners') or 0)}`",
        "",
        "## Persistence Classes",
        "",
    ]
    for klass, count in sorted((s.get("class_counts") or {}).items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"- `{klass}`: `{count}`")
    lines.extend(["", "## Class Summaries", ""])
    for klass, stats in sorted((report.get("class_summaries") or {}).items()):
        lines.extend([
            f"### `{klass}`",
            "",
            f"- Count: `{int(stats.get('n') or 0)}`",
            f"- Median mcap0: `{_fmt_num(_to_float(stats.get('median_mcap0')), 0)}`",
            f"- Median age0: `{_fmt_num(_to_float(stats.get('median_age0')), 1, ' min')}`",
            f"- Median mom5m0: `{_fmt_num(_to_float(stats.get('median_mom5m0')), 1, '%')}`",
            f"- Median hits0: `{_fmt_num(_to_float(stats.get('median_hits0')), 0)}`",
            f"- Median net_sol_in0: `{_fmt_num(_to_float(stats.get('median_net_sol_in0')), 2)}`",
            f"- Median max 15m return: `{_fmt_pct(_to_float(stats.get('median_max_ret_15m')) )}`",
            f"- Median 6h return: `{_fmt_pct(_to_float(stats.get('median_ret_6h')) )}`",
            f"- Median 6h retention: `{_fmt_pct(_to_float(stats.get('median_retention_6h')) )}`",
            "",
        ])
    lines.extend([
        "## Top Winners",
        "",
        "| Symbol | Mint | Class | Source | MCap0 | Age0 | Mom5m0 | Max 15m | Ret 6h | Retention 6h | Pattern | Archetype |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---|---|",
    ])
    for row in report.get("top_winners") or []:
        lines.append(
            f"| {row.get('symbol') or 'n/a'} | `{row.get('mint')}` | `{row.get('persistence_class')}` | "
            f"`{row.get('signal_source') or 'unknown'}` | "
            f"{'n/a' if row.get('mcap0') is None else f'{float(row['mcap0']):.0f}'} | "
            f"{'n/a' if row.get('pair_age_min0') is None else f'{float(row['pair_age_min0']):.1f}'} | "
            f"{'n/a' if row.get('mom5m0') is None else f'{float(row['mom5m0']):.1f}'} | "
            f"{_fmt_pct(_to_float(row.get('max_ret_15m')))} | {_fmt_pct(_to_float(row.get('ret_6h')))} | "
            f"{_fmt_pct(_to_float(row.get('retention_6h')))} | `{row.get('mover_pattern0') or 'unknown'}` | "
            f"`{row.get('archetype_15m') or 'unknown'}` |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify earliest-useful winners by 6h persistence.")
    parser.add_argument("--file", type=Path, default=DEFAULT_IN)
    parser.add_argument("--since-hours", type=float, default=72.0)
    parser.add_argument("--winner-ret", type=float, default=0.50)
    parser.add_argument("--persistent-ret", type=float, default=1.00)
    parser.add_argument("--persistent-retain", type=float, default=0.50)
    parser.add_argument("--round-trip-retain", type=float, default=0.15)
    parser.add_argument("--top", type=int, default=25)
    parser.add_argument("--out-json", type=Path, default=DEFAULT_OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_OUT_MD)
    args = parser.parse_args()

    since_ts = time.time() - (float(args.since_hours) * 3600.0)
    rows_by_mint = load_rows(args.file, since_ts)
    report = build_report(
        rows_by_mint,
        winner_ret=float(args.winner_ret),
        persistent_ret=float(args.persistent_ret),
        persistent_retain=float(args.persistent_retain),
        round_trip_retain=float(args.round_trip_retain),
        top=int(args.top),
    )
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_md(args.out_md, report, since_hours=float(args.since_hours), winner_ret=float(args.winner_ret))
    print(
        f"winner_persistence_report: unique_mints={report['summary']['unique_mints']} "
        f"earliest_winners={report['summary']['earliest_useful_winners']}"
    )


if __name__ == "__main__":
    main()
