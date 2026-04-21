#!/usr/bin/env python3
"""Compare persistent runners vs short-lived spikes on earliest-useful winner anchors."""

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
DEFAULT_OUT_JSON = BASE / "data" / "meme_reports" / "persistence_delta_report.json"
DEFAULT_OUT_MD = BASE / "data" / "meme_reports" / "persistence_delta_report.md"

if str(BASE) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(BASE))

from src.meme_signal_rank import NUMERIC_BUCKETS, bucket_value


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


def _to_int(value: Any) -> int | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return int(value)
    except Exception:
        return None


def _median(values: list[float | None]) -> float | None:
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not clean:
        return None
    return float(statistics.median(clean))


def _fmt_num(value: float | None, digits: int = 0, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}{suffix}"


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
        "hits0": _to_int(row.get("hits0")),
        "buys0": _to_int(row.get("buys0")),
        "uniq0": _to_int(row.get("uniq0")),
        "net_sol_in0": _to_float(row.get("net_sol_in0")),
        "buy_sell_ratio0": _to_float(metrics.get("buy_sell_ratio")),
        "mover_pattern0": str(row.get("mover_pattern0") or metrics.get("mover_pattern") or "").strip().lower() or "unknown",
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
            grp = by_mint[mint].setdefault(key, {"snapshot": _extract_snapshot(row), "rets": {}})
            hz = _to_int(row.get("horizon_s"))
            ret = _to_float(row.get("ret"))
            if hz is not None and ret is not None:
                grp["rets"][hz] = ret
    out: dict[str, list[dict[str, Any]]] = {}
    for mint, items in by_mint.items():
        rows: list[dict[str, Any]] = []
        for key, item in items.items():
            snap = dict(item["snapshot"])
            rets = dict(item["rets"])
            snap["signal_key"] = key
            snap["max_ret_900s"] = _max_ret(rets, 900)
            snap["max_ret_all"] = max(rets.values()) if rets else None
            snap["ret_21600s"] = _to_float(rets.get(21600))
            snap["archetype_15m"] = _classify_short_path(rets)
            rows.append(snap)
        rows.sort(key=lambda r: float(r.get("signal_ts") or 0.0))
        out[mint] = rows
    return out


def _first_useful(rows: list[dict[str, Any]], winner_ret: float) -> dict[str, Any] | None:
    for row in rows:
        if (_to_float(row.get("max_ret_900s")) or -1.0) >= winner_ret:
            return row
    return None


def _classify_persistence(row: dict[str, Any], *, persistent_ret: float, persistent_retain: float, round_trip_retain: float) -> tuple[str, float | None]:
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


def build_rows(
    rows_by_mint: dict[str, list[dict[str, Any]]],
    *,
    winner_ret: float,
    persistent_ret: float,
    persistent_retain: float,
    round_trip_retain: float,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for mint, rows in rows_by_mint.items():
        useful = _first_useful(rows, winner_ret)
        if useful is None:
            continue
        klass, retain = _classify_persistence(
            useful,
            persistent_ret=persistent_ret,
            persistent_retain=persistent_retain,
            round_trip_retain=round_trip_retain,
        )
        if klass in {"pending_6h", "partial_persistence"}:
            continue
        row = dict(useful)
        row["mint"] = mint
        row["persistence_class"] = klass
        row["retention_6h"] = retain
        row["label"] = "persistent" if klass == "persistent_runner" else "spike"
        out.append(row)
    return out


def summarize(rows: list[dict[str, Any]], label: str) -> dict[str, Any]:
    subset = [row for row in rows if row.get("label") == label]
    return {
        "n": len(subset),
        "median_mcap0": _median([_to_float(r.get("mcap0")) for r in subset]),
        "median_age0": _median([_to_float(r.get("pair_age_min0")) for r in subset]),
        "median_mom5m0": _median([_to_float(r.get("mom5m0")) for r in subset]),
        "median_hits0": _median([_to_float(r.get("hits0")) for r in subset]),
        "median_net_sol_in0": _median([_to_float(r.get("net_sol_in0")) for r in subset]),
        "source_counts": dict(Counter(str(r.get("signal_source") or "unknown") for r in subset)),
        "pattern_counts": dict(Counter(str(r.get("mover_pattern0") or "unknown") for r in subset)),
        "archetype_counts": dict(Counter(str(r.get("archetype_15m") or "unknown") for r in subset)),
    }


def slice_deltas(rows: list[dict[str, Any]], *, min_support: int) -> list[dict[str, Any]]:
    fields = ["signal_source", "mover_pattern0", "archetype_15m"] + list(NUMERIC_BUCKETS)
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        for field in fields:
            if field in NUMERIC_BUCKETS:
                value = bucket_value(_to_float(row.get(field)), NUMERIC_BUCKETS[field])
            else:
                value = str(row.get(field) or "missing")
            buckets[(field, value)].append(row)

    total_persistent = sum(1 for row in rows if row.get("label") == "persistent")
    total_spike = sum(1 for row in rows if row.get("label") == "spike")
    persistent_base = total_persistent / max(1, total_persistent + total_spike)

    out: list[dict[str, Any]] = []
    for (field, value), items in buckets.items():
        if len(items) < min_support:
            continue
        persistent = sum(1 for row in items if row.get("label") == "persistent")
        spike = sum(1 for row in items if row.get("label") == "spike")
        precision = persistent / max(1, persistent + spike)
        uplift = precision - persistent_base
        score = uplift * 100.0 + min(10.0, math.log10(len(items) + 1.0) * 5.0)
        out.append(
            {
                "field": field,
                "value": value,
                "n": len(items),
                "persistent": persistent,
                "spike": spike,
                "precision": precision,
                "baseline_precision": persistent_base,
                "edge_score": score,
            }
        )
    out.sort(key=lambda row: (float(row["edge_score"]), int(row["n"])), reverse=True)
    return out


def write_md(path: Path, report: dict[str, Any]) -> None:
    p = report["persistent_summary"]
    s = report["spike_summary"]
    lines = [
        "# Persistence Delta Report",
        "",
        f"- Window: `{report['config']['since_hours']:.0f}h`",
        f"- Earliest useful winners analyzed: `{report['summary']['rows']}`",
        f"- Persistent runners: `{report['summary']['persistent']}`",
        f"- Short-lived spikes: `{report['summary']['spike']}`",
        "",
        "## Median Comparison",
        "",
        "| Class | Count | MCap0 | Age0 | Mom5m0 | Hits0 | NetSOL0 |",
        "|---|---:|---:|---:|---:|---:|---:|",
        f"| Persistent | {p['n']} | {_fmt_num(_to_float(p['median_mcap0']),0)} | {_fmt_num(_to_float(p['median_age0']),1)} | {_fmt_num(_to_float(p['median_mom5m0']),1)} | {_fmt_num(_to_float(p['median_hits0']),0)} | {_fmt_num(_to_float(p['median_net_sol_in0']),2)} |",
        f"| Spike | {s['n']} | {_fmt_num(_to_float(s['median_mcap0']),0)} | {_fmt_num(_to_float(s['median_age0']),1)} | {_fmt_num(_to_float(s['median_mom5m0']),1)} | {_fmt_num(_to_float(s['median_hits0']),0)} | {_fmt_num(_to_float(s['median_net_sol_in0']),2)} |",
        "",
        "## Positive Deltas",
        "",
        "| Field | Value | N | Persistent | Spike | Precision | Edge |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in report["top_positive_slices"][:20]:
        lines.append(
            f"| `{row['field']}` | `{row['value']}` | {int(row['n'])} | {int(row['persistent'])} | "
            f"{int(row['spike'])} | {float(row['precision']) * 100:.1f}% | {float(row['edge_score']):.1f} |"
        )
    lines.extend([
        "",
        "## Negative Deltas",
        "",
        "| Field | Value | N | Persistent | Spike | Precision | Edge |",
        "|---|---|---:|---:|---:|---:|---:|",
    ])
    for row in report["top_negative_slices"][:20]:
        lines.append(
            f"| `{row['field']}` | `{row['value']}` | {int(row['n'])} | {int(row['persistent'])} | "
            f"{int(row['spike'])} | {float(row['precision']) * 100:.1f}% | {float(row['edge_score']):.1f} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare persistent runners vs short-lived spikes.")
    parser.add_argument("--file", type=Path, default=DEFAULT_IN)
    parser.add_argument("--since-hours", type=float, default=72.0)
    parser.add_argument("--winner-ret", type=float, default=0.50)
    parser.add_argument("--persistent-ret", type=float, default=1.00)
    parser.add_argument("--persistent-retain", type=float, default=0.50)
    parser.add_argument("--round-trip-retain", type=float, default=0.15)
    parser.add_argument("--min-support", type=int, default=4)
    parser.add_argument("--out-json", type=Path, default=DEFAULT_OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_OUT_MD)
    args = parser.parse_args()

    since_ts = time.time() - (float(args.since_hours) * 3600.0)
    rows_by_mint = load_rows(args.file, since_ts)
    rows = build_rows(
        rows_by_mint,
        winner_ret=float(args.winner_ret),
        persistent_ret=float(args.persistent_ret),
        persistent_retain=float(args.persistent_retain),
        round_trip_retain=float(args.round_trip_retain),
    )
    deltas = slice_deltas(rows, min_support=int(args.min_support))
    report = {
        "generated_at": time.time(),
        "config": {
            "since_hours": float(args.since_hours),
            "winner_ret": float(args.winner_ret),
            "persistent_ret": float(args.persistent_ret),
            "persistent_retain": float(args.persistent_retain),
            "round_trip_retain": float(args.round_trip_retain),
        },
        "summary": {
            "rows": len(rows),
            "persistent": sum(1 for row in rows if row.get("label") == "persistent"),
            "spike": sum(1 for row in rows if row.get("label") == "spike"),
        },
        "persistent_summary": summarize(rows, "persistent"),
        "spike_summary": summarize(rows, "spike"),
        "top_positive_slices": [row for row in deltas if float(row["edge_score"]) > 0][:40],
        "top_negative_slices": [row for row in deltas if float(row["edge_score"]) < 0][:40],
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_md(args.out_md, report)
    print(
        f"persistence_delta_report: rows={report['summary']['rows']} "
        f"persistent={report['summary']['persistent']} spike={report['summary']['spike']}"
    )


if __name__ == "__main__":
    main()
