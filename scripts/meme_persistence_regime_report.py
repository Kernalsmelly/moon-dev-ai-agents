#!/usr/bin/env python3
"""Split earliest-useful winners into persistence regimes.

Purpose:
- work from earliest useful winner anchors, not later snapshots
- classify winners by 6h persistence outcome
- split them into interpretable persistence regimes so we can track drift
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
DEFAULT_OUT_JSON = BASE / "data" / "meme_reports" / "persistence_regime_report.json"
DEFAULT_OUT_MD = BASE / "data" / "meme_reports" / "persistence_regime_report.md"


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


def _fmt_num(value: float | None, digits: int = 0) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


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


def classify_persistence(
    row: dict[str, Any],
    *,
    persistent_ret: float,
    persistent_retain: float,
    round_trip_retain: float,
) -> tuple[str, float | None]:
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


def classify_regime(row: dict[str, Any]) -> str:
    mcap = _to_float(row.get("mcap0"))
    age = _to_float(row.get("pair_age_min0"))
    mom5m = _to_float(row.get("mom5m0"))
    hits = float(row.get("hits0") or 0.0)
    buys = float(row.get("buys0") or 0.0)
    net_sol = _to_float(row.get("net_sol_in0")) or 0.0
    pattern = str(row.get("mover_pattern0") or "unknown")

    if (
        (age is not None and age >= 45.0)
        or (mcap is not None and mcap >= 120000.0)
    ) and (mom5m is None or mom5m < 25.0):
        return "late_slow_expansion"

    if (
        (
            pattern == "retest_hold"
            and age is not None and 8.0 <= age < 45.0
            and mom5m is not None and 5.0 <= mom5m < 25.0
            and (mcap is None or 50000.0 <= mcap < 150000.0)
        )
        or (
            mcap is not None and 50000.0 <= mcap < 120000.0
            and age is not None and 8.0 <= age < 45.0
            and mom5m is not None and 8.0 <= mom5m < 25.0
            and hits < 500.0
            and buys < 350.0
            and net_sol < 25.0
        )
    ):
        return "calm_continuation"

    if (
        (mom5m is not None and mom5m >= 25.0)
        or hits >= 400.0
        or buys >= 300.0
        or net_sol >= 20.0
    ):
        return "early_hot_breakout"

    return "mixed_other"


def build_report(
    rows_by_mint: dict[str, list[dict[str, Any]]],
    *,
    winner_ret: float,
    persistent_ret: float,
    persistent_retain: float,
    round_trip_retain: float,
    top: int,
) -> dict[str, Any]:
    all_rows: list[dict[str, Any]] = []
    persistence_regime_matrix: dict[str, Counter] = defaultdict(Counter)
    regime_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for mint, rows in rows_by_mint.items():
        useful = first_useful(rows, winner_ret)
        if useful is None:
            continue
        persistence_class, retention_6h = classify_persistence(
            useful,
            persistent_ret=persistent_ret,
            persistent_retain=persistent_retain,
            round_trip_retain=round_trip_retain,
        )
        regime = classify_regime(useful)
        record = {
            "mint": mint,
            "symbol": useful.get("symbol") or "n/a",
            "signal_source": useful.get("signal_source") or "unknown",
            "signal_ts": useful.get("signal_ts") or 0.0,
            "mcap0": _to_float(useful.get("mcap0")),
            "pair_age_min0": _to_float(useful.get("pair_age_min0")),
            "mom5m0": _to_float(useful.get("mom5m0")),
            "hits0": int(useful.get("hits0") or 0),
            "buys0": int(useful.get("buys0") or 0),
            "net_sol_in0": _to_float(useful.get("net_sol_in0")),
            "mover_pattern0": useful.get("mover_pattern0") or "unknown",
            "archetype_15m": useful.get("archetype_15m") or "unknown",
            "max_ret_15m": _to_float(useful.get("max_ret_900s")),
            "max_ret_30m": _to_float(useful.get("max_ret_1800s")),
            "max_ret_6h": _to_float(useful.get("max_ret_21600s")),
            "ret_6h": _to_float(useful.get("ret_21600s")),
            "retention_6h": retention_6h,
            "persistence_class": persistence_class,
            "persistence_regime": regime,
            "url": useful.get("url") or "",
        }
        all_rows.append(record)
        persistence_regime_matrix[regime][persistence_class] += 1
        regime_rows[regime].append(record)

    all_rows.sort(key=lambda row: float(row.get("max_ret_6h") or row.get("max_ret_30m") or -1.0), reverse=True)

    regime_summary: dict[str, Any] = {}
    for regime, rows in regime_rows.items():
        matured = [r for r in rows if r.get("persistence_class") != "pending_6h"]
        persistent = [r for r in matured if r.get("persistence_class") == "persistent_runner"]
        regime_summary[regime] = {
            "n": len(rows),
            "matured_n": len(matured),
            "persistent_n": len(persistent),
            "persistent_rate_matured": (len(persistent) / len(matured)) if matured else None,
            "class_counts": dict(Counter(str(r.get("persistence_class") or "unknown") for r in rows)),
            "source_counts": dict(Counter(str(r.get("signal_source") or "unknown") for r in rows)),
            "pattern_counts": dict(Counter(str(r.get("mover_pattern0") or "unknown") for r in rows)),
            "median_mcap0": _median([_to_float(r.get("mcap0")) for r in rows]),
            "median_age0": _median([_to_float(r.get("pair_age_min0")) for r in rows]),
            "median_mom5m0": _median([_to_float(r.get("mom5m0")) for r in rows]),
            "median_hits0": _median([_to_float(r.get("hits0")) for r in rows]),
            "median_net_sol_in0": _median([_to_float(r.get("net_sol_in0")) for r in rows]),
            "examples": rows[:top],
        }

    return {
        "generated_at": time.time(),
        "summary": {
            "unique_mints": len(rows_by_mint),
            "earliest_useful_winners": len(all_rows),
            "persistence_class_counts": dict(Counter(str(r.get("persistence_class") or "unknown") for r in all_rows)),
            "regime_counts": dict(Counter(str(r.get("persistence_regime") or "unknown") for r in all_rows)),
        },
        "regime_summary": regime_summary,
        "regime_class_matrix": {regime: dict(counter) for regime, counter in persistence_regime_matrix.items()},
        "top_examples": all_rows[:top],
    }


def write_md(path: Path, report: dict[str, Any], *, top: int) -> None:
    lines = [
        "# Persistence Regime Report",
        "",
        "Split earliest-useful winners into interpretable persistence regimes.",
        "",
        "## Summary",
        "",
        f"- Unique mints: `{report['summary']['unique_mints']}`",
        f"- Earliest useful winners: `{report['summary']['earliest_useful_winners']}`",
        f"- Persistence classes: `{report['summary']['persistence_class_counts']}`",
        f"- Regime counts: `{report['summary']['regime_counts']}`",
        "",
        "## Regime x Persistence Class",
        "",
        "| Regime | Persistent | Spike | Round-trip | Partial | Pending |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for regime, counts in sorted(report.get("regime_class_matrix", {}).items()):
        lines.append(
            f"| `{regime}` | {int(counts.get('persistent_runner', 0))} | {int(counts.get('short_lived_spike', 0))} | "
            f"{int(counts.get('round_trip_winner', 0))} | {int(counts.get('partial_persistence', 0))} | {int(counts.get('pending_6h', 0))} |"
        )
    lines.extend([
        "",
        "## Regime Summaries",
        "",
    ])
    for regime, summary in sorted(
        report.get("regime_summary", {}).items(),
        key=lambda kv: (-int(kv[1].get("persistent_n") or 0), -int(kv[1].get("n") or 0), kv[0]),
    ):
        lines.extend(
            [
                f"### `{regime}`",
                "",
                f"- Rows: `{summary['n']}`",
                f"- Matured rows: `{summary['matured_n']}`",
                f"- Persistent rows: `{summary['persistent_n']}`",
                f"- Persistent rate on matured rows: `{_fmt_pct(summary.get('persistent_rate_matured'))}`",
                f"- Class counts: `{summary['class_counts']}`",
                f"- Source counts: `{summary['source_counts']}`",
                f"- Pattern counts: `{summary['pattern_counts']}`",
                f"- Median mcap0: `{_fmt_num(summary.get('median_mcap0'), 0)}`",
                f"- Median age0: `{_fmt_num(summary.get('median_age0'), 1)}` min",
                f"- Median mom5m0: `{_fmt_num(summary.get('median_mom5m0'), 1)}`%",
                f"- Median hits0: `{_fmt_num(summary.get('median_hits0'), 0)}`",
                f"- Median net_sol_in0: `{_fmt_num(summary.get('median_net_sol_in0'), 2)}`",
                "",
                "| Symbol | Mint | Class | Source | MCap0 | Age0 | Mom5m0 | Hits | NetSOL | Pattern | Max15m | Ret6h |",
                "|---|---|---|---|---:|---:|---:|---:|---:|---|---:|---:|",
            ]
        )
        for row in summary.get("examples", [])[:top]:
            lines.append(
                f"| {row['symbol']} | `{row['mint']}` | `{row['persistence_class']}` | `{row['signal_source']}` | "
                f"{_fmt_num(_to_float(row.get('mcap0')), 0)} | {_fmt_num(_to_float(row.get('pair_age_min0')), 1)} | "
                f"{_fmt_num(_to_float(row.get('mom5m0')), 1)} | {int(row.get('hits0') or 0)} | "
                f"{_fmt_num(_to_float(row.get('net_sol_in0')), 2)} | `{row.get('mover_pattern0') or 'unknown'}` | "
                f"{_fmt_pct(_to_float(row.get('max_ret_15m')))} | {_fmt_pct(_to_float(row.get('ret_6h')))} |"
            )
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Split earliest-useful winners into persistence regimes.")
    parser.add_argument("--file", type=Path, default=DEFAULT_IN)
    parser.add_argument("--since-hours", type=float, default=72.0)
    parser.add_argument("--winner-ret", type=float, default=0.50)
    parser.add_argument("--persistent-ret", type=float, default=1.00)
    parser.add_argument("--persistent-retain", type=float, default=0.50)
    parser.add_argument("--round-trip-retain", type=float, default=0.15)
    parser.add_argument("--top", type=int, default=8)
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
    write_md(args.out_md, report, top=int(args.top))
    print(
        f"persistence_regime_report: winners={report['summary']['earliest_useful_winners']} "
        f"regimes={report['summary']['regime_counts']}"
    )


if __name__ == "__main__":
    main()
