#!/usr/bin/env python3
"""Build a source-aware ranking report from labeled signal outcomes.

The output is intentionally offline and deterministic:
- read local `data/signal_outcomes.jsonl`
- collapse rows by signal key
- compute forward-return labels per signal
- rank bucketed feature slices by source family

This is the bridge from "raw tape" to "rankable edge".
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")

if str(BASE) not in os.sys.path:
    os.sys.path.insert(0, str(BASE))

from src.meme_signal_contract import signal_field_status, signal_source_family
from src.meme_signal_rank import NUMERIC_BUCKETS, bucket_value


OUTCOMES = BASE / "data" / "signal_outcomes.jsonl"
OUT_JSON = BASE / "data" / "meme_reports" / "signal_rank_report.json"
OUT_MD = BASE / "data" / "meme_reports" / "signal_rank_report.md"


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


def _signal_key(row: dict[str, Any]) -> str | None:
    raw = row.get("signal_key")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    mint = str(row.get("mint") or "").strip()
    signal_ts = _to_float(row.get("signal_ts"))
    signal_source = str(row.get("signal_source") or row.get("source") or "").strip().lower()
    if mint and signal_ts is not None:
        return f"{mint}|{signal_ts:.6f}|{signal_source}"
    return None


def _extract_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    signal_source = str(
        row.get("signal_source")
        or metrics.get("source")
        or ""
    ).strip().lower()
    top0 = row.get("top_buyer_share0")
    buy_sell_ratio0 = _to_float(metrics.get("buy_sell_ratio"))
    snapshot = {
        "mint": str(row.get("mint") or "").strip(),
        "signal_ts": _to_float(row.get("signal_ts")) or 0.0,
        "run_id": str(row.get("run_id") or "").strip(),
        "signal_source": signal_source,
        "source_family": signal_source_family(signal_source),
        "signal_profile0": str(row.get("signal_profile0") or "").strip().lower() or None,
        "mover_pattern0": str(row.get("mover_pattern0") or metrics.get("mover_pattern") or "").strip().lower() or None,
        "score0": _to_float(row.get("score0") if row.get("score0") is not None else row.get("signal_score")),
        "mcap0": _to_float(row.get("mcap0") if row.get("mcap0") is not None else row.get("marketcap0")),
        "liq0": _to_float(row.get("liq0") if row.get("liq0") is not None else metrics.get("liquidity")),
        "pair_age_min0": _to_float(row.get("pair_age_min0") if row.get("pair_age_min0") is not None else metrics.get("pair_age_min")),
        "mom5m0": _to_float(metrics.get("price_change_5m") if metrics.get("price_change_5m") is not None else metrics.get("momentum_5m_pct")),
        "hits0": _to_int(row.get("hits0")),
        "buys0": _to_int(row.get("buys0")),
        "sells0": _to_int(row.get("sells0")),
        "uniq0": _to_int(row.get("uniq0")),
        "net_sol_in0": _to_float(row.get("net_sol_in0")),
        "top_buyer_share0": _to_float(top0),
        "buy_sell_ratio0": buy_sell_ratio0,
        "unique_buyers_status": signal_field_status(metrics, "unique_buyers", signal_source),
        "top_buyer_share_status": signal_field_status(metrics, "top_buyer_share", signal_source),
    }
    return snapshot


def _mean(xs: list[float]) -> float:
    return sum(xs) / max(1, len(xs))


def _rate(flags: list[bool]) -> float:
    return sum(1 for f in flags if f) / max(1, len(flags))


def _load_signals(path: Path, since_ts: float) -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            sig_ts = _to_float(row.get("signal_ts"))
            if sig_ts is None or sig_ts < since_ts:
                continue
            key = _signal_key(row)
            if not key:
                continue
            g = groups.setdefault(
                key,
                {
                    "snapshot": _extract_snapshot(row),
                    "rets": {},
                    "rows": 0,
                },
            )
            g["rows"] += 1
            hz = _to_int(row.get("horizon_s"))
            ret = _to_float(row.get("ret"))
            if hz is not None and ret is not None:
                g["rets"][hz] = ret
    return groups


def _max_ret(rets: dict[int, float], horizon_s: int) -> float | None:
    vals = [ret for hz, ret in rets.items() if int(hz) <= int(horizon_s)]
    if not vals:
        return None
    return max(vals)


def _build_rows(groups: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, item in groups.items():
        snap = dict(item["snapshot"])
        rets = item["rets"]
        row = {
            "signal_key": key,
            **snap,
            "ret_30s": _to_float(rets.get(30)),
            "ret_120s": _to_float(rets.get(120)),
            "ret_300s": _to_float(rets.get(300)),
            "ret_900s": _to_float(rets.get(900)),
            "ret_1800s": _to_float(rets.get(1800)),
            "max_ret_300s": _max_ret(rets, 300),
            "max_ret_900s": _max_ret(rets, 900),
            "max_ret_1800s": _max_ret(rets, 1800),
            "runner20_5m": (_max_ret(rets, 300) or -1.0) >= 0.20,
            "runner50_15m": (_max_ret(rets, 900) or -1.0) >= 0.50,
            "runner100_30m": (_max_ret(rets, 1800) or -1.0) >= 1.00,
        }
        for field, edges in NUMERIC_BUCKETS.items():
            row[f"{field}_bucket"] = bucket_value(_to_float(row.get(field)), edges)
        rows.append(row)
    return rows


def _family_baselines(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("source_family") or "unknown")].append(row)
    out: dict[str, dict[str, float]] = {}
    for family, items in grouped.items():
        max_ret_1800 = [float(r.get("max_ret_1800s") or 0.0) for r in items if r.get("max_ret_1800s") is not None]
        ret_300 = [float(r.get("ret_300s") or 0.0) for r in items if r.get("ret_300s") is not None]
        out[family] = {
            "n": len(items),
            "mean_ret_300s": _mean(ret_300) if ret_300 else 0.0,
            "mean_max_ret_1800s": _mean(max_ret_1800) if max_ret_1800 else 0.0,
            "runner20_5m_rate": _rate([bool(r.get("runner20_5m")) for r in items]),
            "runner50_15m_rate": _rate([bool(r.get("runner50_15m")) for r in items]),
            "runner100_30m_rate": _rate([bool(r.get("runner100_30m")) for r in items]),
        }
    return out


def _slice_stats(rows: list[dict[str, Any]], min_samples: int) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    fields = [
        "signal_source",
        "signal_profile0",
        "mover_pattern0",
        "unique_buyers_status",
        "top_buyer_share_status",
    ] + [f"{field}_bucket" for field in NUMERIC_BUCKETS]
    for row in rows:
        family = str(row.get("source_family") or "unknown")
        for field in fields:
            value = str(row.get(field) or "missing")
            buckets[(family, field, value)].append(row)

    baselines = _family_baselines(rows)
    out: list[dict[str, Any]] = []
    for (family, field, value), items in buckets.items():
        if len(items) < min_samples:
            continue
        baseline = baselines.get(family, {})
        ret_300s = [float(r.get("ret_300s") or 0.0) for r in items if r.get("ret_300s") is not None]
        max_ret_1800s = [float(r.get("max_ret_1800s") or 0.0) for r in items if r.get("max_ret_1800s") is not None]
        runner20 = _rate([bool(r.get("runner20_5m")) for r in items])
        runner50 = _rate([bool(r.get("runner50_15m")) for r in items])
        runner100 = _rate([bool(r.get("runner100_30m")) for r in items])
        mean_300 = _mean(ret_300s) if ret_300s else 0.0
        mean_1800 = _mean(max_ret_1800s) if max_ret_1800s else 0.0
        score = (
            (runner50 - float(baseline.get("runner50_15m_rate") or 0.0)) * 120.0
            + (runner100 - float(baseline.get("runner100_30m_rate") or 0.0)) * 90.0
            + (mean_1800 - float(baseline.get("mean_max_ret_1800s") or 0.0)) * 45.0
            + min(12.0, math.log10(len(items) + 1.0) * 6.0)
        )
        out.append(
            {
                "source_family": family,
                "field": field,
                "value": value,
                "n": len(items),
                "mean_ret_300s": mean_300,
                "mean_max_ret_1800s": mean_1800,
                "runner20_5m_rate": runner20,
                "runner50_15m_rate": runner50,
                "runner100_30m_rate": runner100,
                "baseline_runner50_15m_rate": float(baseline.get("runner50_15m_rate") or 0.0),
                "baseline_runner100_30m_rate": float(baseline.get("runner100_30m_rate") or 0.0),
                "baseline_mean_max_ret_1800s": float(baseline.get("mean_max_ret_1800s") or 0.0),
                "edge_score": score,
            }
        )
    out.sort(key=lambda row: (float(row["edge_score"]), int(row["n"])), reverse=True)
    return out


def _recommend_profiles(slices: list[dict[str, Any]], min_edge_score: float) -> dict[str, dict[str, dict[str, Any]]]:
    out: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in slices:
        family = str(row["source_family"])
        field = str(row["field"])
        value = str(row["value"])
        if field == "signal_source":
            continue
        if value in {"missing", "unknown", "none", ""}:
            continue
        if float(row["edge_score"]) < min_edge_score:
            continue
        if field in out[family]:
            continue
        out[family][field] = {
            "value": value,
            "n": int(row["n"]),
            "edge_score": round(float(row["edge_score"]), 2),
            "runner50_15m_rate": round(float(row["runner50_15m_rate"]), 4),
            "runner100_30m_rate": round(float(row["runner100_30m_rate"]), 4),
            "mean_max_ret_1800s": round(float(row["mean_max_ret_1800s"]), 4),
        }
    return dict(out)


def _actionable_slices(rows: list[dict[str, Any]], *, positive: bool, limit: int) -> list[dict[str, Any]]:
    filtered = [
        row
        for row in rows
        if str(row.get("value") or "") not in {"missing", "unknown", "none", ""}
    ]
    ordered = sorted(filtered, key=lambda row: float(row["edge_score"]), reverse=positive)
    return ordered[:limit]


def _write_md(
    out_path: Path,
    *,
    payload: dict[str, Any],
) -> None:
    lines: list[str] = []
    lines.append("# Signal Rank Report")
    lines.append("")
    lines.append(
        f"Window: last {payload['since_hours']}h | Signals: {payload['signals']} | Source families: {', '.join(sorted(payload['family_summary'].keys()))}"
    )
    lines.append("")
    lines.append("## Baselines")
    for family, stats in sorted(payload["family_summary"].items()):
        lines.append(
            f"- `{family}`: n={stats['n']} mean_max_ret_1800s={stats['mean_max_ret_1800s']:+.4f} "
            f"runner20_5m={stats['runner20_5m_rate']*100:.1f}% "
            f"runner50_15m={stats['runner50_15m_rate']*100:.1f}% "
            f"runner100_30m={stats['runner100_30m_rate']*100:.1f}%"
        )
    lines.append("")
    lines.append("## Recommended Profiles")
    for family, fields in sorted(payload["recommended_profiles"].items()):
        lines.append(f"### `{family}`")
        for field, row in sorted(fields.items()):
            lines.append(
                f"- `{field}` -> `{row['value']}` "
                f"(n={row['n']}, edge={row['edge_score']:+.2f}, "
                f"runner50_15m={row['runner50_15m_rate']*100:.1f}%, "
                f"runner100_30m={row['runner100_30m_rate']*100:.1f}%, "
                f"mean_max_ret_1800s={row['mean_max_ret_1800s']:+.4f})"
            )
        lines.append("")
    lines.append("## Top Positive Slices")
    for row in payload["top_positive_slices"]:
        lines.append(
            f"- `{row['source_family']}` `{row['field']}`=`{row['value']}` "
            f"n={row['n']} edge={row['edge_score']:+.2f} "
            f"runner50_15m={row['runner50_15m_rate']*100:.1f}% "
            f"runner100_30m={row['runner100_30m_rate']*100:.1f}% "
            f"mean_max_ret_1800s={row['mean_max_ret_1800s']:+.4f}"
        )
    lines.append("")
    lines.append("## Top Negative Slices")
    for row in payload["top_negative_slices"]:
        lines.append(
            f"- `{row['source_family']}` `{row['field']}`=`{row['value']}` "
            f"n={row['n']} edge={row['edge_score']:+.2f} "
            f"runner50_15m={row['runner50_15m_rate']*100:.1f}% "
            f"runner100_30m={row['runner100_30m_rate']*100:.1f}% "
            f"mean_max_ret_1800s={row['mean_max_ret_1800s']:+.4f}"
        )
    out_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Build a source-aware rank report from signal outcomes.")
    ap.add_argument("--file", default=str(OUTCOMES))
    ap.add_argument("--out-json", default=str(OUT_JSON))
    ap.add_argument("--out-md", default=str(OUT_MD))
    ap.add_argument("--since-hours", type=float, default=168.0)
    ap.add_argument("--min-samples", type=int, default=30)
    ap.add_argument("--min-edge-score", type=float, default=8.0)
    args = ap.parse_args()

    since_ts = time.time() - (float(args.since_hours) * 3600.0)
    groups = _load_signals(Path(args.file), since_ts)
    rows = _build_rows(groups)
    family_summary = _family_baselines(rows)
    slices = _slice_stats(rows, min_samples=int(args.min_samples))

    positive = _actionable_slices(
        [row for row in slices if float(row["edge_score"]) > 0],
        positive=True,
        limit=20,
    )
    negative = _actionable_slices(slices, positive=False, limit=20)
    recommended = _recommend_profiles(slices, min_edge_score=float(args.min_edge_score))

    payload = {
        "generated_at": time.time(),
        "since_hours": float(args.since_hours),
        "signals": len(rows),
        "family_summary": family_summary,
        "recommended_profiles": recommended,
        "top_positive_slices": positive,
        "top_negative_slices": negative,
    }

    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _write_md(out_md, payload=payload)
    print(
        f"signal_rank_report: signals={len(rows)} families={len(family_summary)} "
        f"positive={len(positive)} negative={len(negative)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
