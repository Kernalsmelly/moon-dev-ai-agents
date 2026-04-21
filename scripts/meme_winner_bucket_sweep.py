#!/usr/bin/env python3
"""Sweep winner buckets using earliest-useful signal anchors.

Goal:
- use one anchor row per mint
- winners use the earliest signal that still led to the target return
- non-winners use the first signal
- measure which buckets improve winner precision and recall
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import time
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
if str(BASE) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(BASE))

from src.meme_signal_rank import NUMERIC_BUCKETS, bucket_value

DEFAULT_IN = BASE / "data" / "signal_outcomes.jsonl"
DEFAULT_OUT_JSON = BASE / "data" / "meme_reports" / "winner_bucket_sweep.json"
DEFAULT_OUT_MD = BASE / "data" / "meme_reports" / "winner_bucket_sweep.md"


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


def _f1(precision: float, recall: float) -> float:
    if precision <= 0 or recall <= 0:
        return 0.0
    return (2.0 * precision * recall) / (precision + recall)


def _safe_median(values: list[float]) -> float | None:
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not vals:
        return None
    return float(median(vals))


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
        "hits0": _to_float(row.get("hits0")),
        "buys0": _to_float(row.get("buys0")),
        "uniq0": _to_float(row.get("uniq0")),
        "net_sol_in0": _to_float(row.get("net_sol_in0")),
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
            snap["max_ret_900s"] = _max_ret(rets, 900)
            snap["max_ret_1800s"] = _max_ret(rets, 1800)
            snap["max_ret_all"] = max(rets.values()) if rets else None
            rows.append(snap)
        rows.sort(key=lambda r: float(r.get("signal_ts") or 0.0))
        out[mint] = rows
    return out


def anchor_rows(
    rows_by_mint: dict[str, list[dict[str, Any]]],
    *,
    winner_horizon_s: int,
    winner_ret: float,
) -> list[dict[str, Any]]:
    anchors: list[dict[str, Any]] = []
    target_field = "max_ret_900s" if int(winner_horizon_s) <= 900 else "max_ret_1800s"
    for mint, rows in rows_by_mint.items():
        useful = None
        for row in rows:
            value = _to_float(row.get(target_field))
            if value is not None and value >= float(winner_ret):
                useful = row
                break
        anchor = dict(useful or rows[0])
        anchor["label_winner"] = bool(useful is not None)
        anchor["anchor_kind"] = "earliest_useful" if useful is not None else "first_signal"
        anchor["best_mint_ret_all"] = max(float(r.get("max_ret_all") or -1.0) for r in rows)
        anchors.append(anchor)
    return anchors


def feature_map(row: dict[str, Any]) -> dict[str, str]:
    out = {
        "signal_source": str(row.get("signal_source") or "unknown"),
        "mover_pattern0": str(row.get("mover_pattern0") or "unknown"),
    }
    for field in ("mcap0", "pair_age_min0", "mom5m0", "hits0", "buys0", "uniq0", "net_sol_in0", "liq0"):
        out[f"{field}_bucket"] = bucket_value(_to_float(row.get(field)), NUMERIC_BUCKETS[field] if field in NUMERIC_BUCKETS else [])
    return out


def score_bucket(rows: list[dict[str, Any]], total_winners: int, base_precision: float) -> dict[str, Any]:
    winners = [r for r in rows if r.get("label_winner")]
    n = len(rows)
    win_n = len(winners)
    precision = (float(win_n) / float(n)) if n else 0.0
    recall = (float(win_n) / float(total_winners)) if total_winners else 0.0
    lift = (precision / base_precision) if base_precision > 0 else 0.0
    return {
        "n": n,
        "winners": win_n,
        "precision": precision,
        "recall": recall,
        "lift": lift,
        "f1": _f1(precision, recall),
        "winner_median_mcap0": _safe_median([_to_float(r.get("mcap0")) for r in winners]),
        "winner_median_age0": _safe_median([_to_float(r.get("pair_age_min0")) for r in winners]),
        "winner_median_mom5m0": _safe_median([_to_float(r.get("mom5m0")) for r in winners]),
        "winner_median_hits0": _safe_median([_to_float(r.get("hits0")) for r in winners]),
        "winner_median_net_sol_in0": _safe_median([_to_float(r.get("net_sol_in0")) for r in winners]),
    }


def build_report(
    anchors: list[dict[str, Any]],
    *,
    min_single_support: int,
    min_pair_support: int,
    min_pair_winners: int,
) -> dict[str, Any]:
    total = len(anchors)
    winners = [r for r in anchors if r.get("label_winner")]
    total_winners = len(winners)
    base_precision = (float(total_winners) / float(total)) if total else 0.0

    single_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    pair_groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    feature_fields = [
        "signal_source",
        "mover_pattern0",
        "mcap0_bucket",
        "pair_age_min0_bucket",
        "mom5m0_bucket",
        "hits0_bucket",
        "net_sol_in0_bucket",
    ]

    for row in anchors:
        fmap = feature_map(row)
        for field in feature_fields:
            single_groups[(field, fmap[field])].append(row)
        for f1, f2 in itertools.combinations(feature_fields, 2):
            pair_groups[(f1, fmap[f1], f2, fmap[f2])].append(row)

    singles: list[dict[str, Any]] = []
    for (field, value), rows in single_groups.items():
        if len(rows) < int(min_single_support):
            continue
        stats = score_bucket(rows, total_winners, base_precision)
        singles.append({"field": field, "value": value, **stats})

    pairs: list[dict[str, Any]] = []
    for (f1, v1, f2, v2), rows in pair_groups.items():
        if len(rows) < int(min_pair_support):
            continue
        stats = score_bucket(rows, total_winners, base_precision)
        if int(stats["winners"]) < int(min_pair_winners):
            continue
        pairs.append({"field1": f1, "value1": v1, "field2": f2, "value2": v2, **stats})

    singles.sort(key=lambda r: (r["f1"], r["lift"], r["precision"], r["n"]), reverse=True)
    pairs.sort(key=lambda r: (r["f1"], r["lift"], r["precision"], r["n"]), reverse=True)

    return {
        "summary": {
            "anchors": total,
            "winners": total_winners,
            "base_precision": base_precision,
            "min_single_support": int(min_single_support),
            "min_pair_support": int(min_pair_support),
            "min_pair_winners": int(min_pair_winners),
        },
        "top_single_buckets": singles[:30],
        "top_pair_buckets": pairs[:30],
    }


def write_md(path: Path, report: dict[str, Any], *, since_hours: float) -> None:
    s = report["summary"]
    lines = [
        "# Winner Bucket Sweep",
        "",
        f"- Window: last `{since_hours:.0f}h`",
        f"- Anchor rows: `{int(s.get('anchors') or 0)}`",
        f"- Winners: `{int(s.get('winners') or 0)}`",
        f"- Base precision: `{float(s.get('base_precision') or 0.0) * 100.0:.1f}%`",
        "",
        "## Top Single Buckets",
        "",
        "| Field | Value | N | Winners | Precision | Recall | Lift | F1 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report.get("top_single_buckets") or []:
        lines.append(
            f"| `{row['field']}` | `{row['value']}` | {int(row['n'])} | {int(row['winners'])} | "
            f"{row['precision']*100.0:.1f}% | {row['recall']*100.0:.1f}% | {row['lift']:.2f}x | {row['f1']:.3f} |"
        )
    lines.extend([
        "",
        "## Top Pair Buckets",
        "",
        "| Field 1 | Value 1 | Field 2 | Value 2 | N | Winners | Precision | Recall | Lift | F1 |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|",
    ])
    for row in report.get("top_pair_buckets") or []:
        lines.append(
            f"| `{row['field1']}` | `{row['value1']}` | `{row['field2']}` | `{row['value2']}` | "
            f"{int(row['n'])} | {int(row['winners'])} | {row['precision']*100.0:.1f}% | "
            f"{row['recall']*100.0:.1f}% | {row['lift']:.2f}x | {row['f1']:.3f} |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(DEFAULT_IN))
    ap.add_argument("--since-hours", type=float, default=72.0)
    ap.add_argument("--winner-horizon-s", type=int, default=900)
    ap.add_argument("--winner-ret", type=float, default=0.50)
    ap.add_argument("--min-single-support", type=int, default=12)
    ap.add_argument("--min-pair-support", type=int, default=8)
    ap.add_argument("--min-pair-winners", type=int, default=3)
    ap.add_argument("--out-json", default=str(DEFAULT_OUT_JSON))
    ap.add_argument("--out-md", default=str(DEFAULT_OUT_MD))
    args = ap.parse_args()

    rows_by_mint = load_rows(Path(args.file), time.time() - float(args.since_hours) * 3600.0)
    anchors = anchor_rows(
        rows_by_mint,
        winner_horizon_s=int(args.winner_horizon_s),
        winner_ret=float(args.winner_ret),
    )
    report = build_report(
        anchors,
        min_single_support=int(args.min_single_support),
        min_pair_support=int(args.min_pair_support),
        min_pair_winners=int(args.min_pair_winners),
    )
    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_md(out_md, report, since_hours=float(args.since_hours))
    print(f"wrote {out_json}")
    print(f"wrote {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
