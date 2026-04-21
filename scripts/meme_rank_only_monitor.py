#!/usr/bin/env python3
"""Train an anchor-based ranker and score fresh signals without trading.

This script uses earliest-useful winner anchors for training:
- winner mints -> earliest signal that still led to the target move
- non-winner mints -> first observed signal

It then:
- validates the ranker on a recent labeled holdout window
- scores the current live tape to surface top candidates
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
OUTCOMES = BASE / "data" / "signal_outcomes.jsonl"
TAPE = BASE / "data" / "meme_launch_signals.jsonl"
OUT_JSON = BASE / "data" / "meme_reports" / "rank_only_monitor.json"
OUT_MD = BASE / "data" / "meme_reports" / "rank_only_monitor.md"

if str(BASE) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(BASE))

from src.meme_signal_contract import signal_field_status, signal_source_family
from src.meme_signal_rank import NUMERIC_BUCKETS, bucket_value, score_candidate_from_report

SPLIT_PATTERNS = {"breakout", "retest_hold"}


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


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100.0:.1f}%"


def _fmt_num(value: float | None, digits: int = 0) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


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


def _max_ret(rets: dict[int, float], horizon_s: int) -> float | None:
    vals = [ret for hz, ret in rets.items() if int(hz) <= int(horizon_s)]
    if not vals:
        return None
    return max(vals)


def _rank_family_key(base_family: str | None, mover_pattern: str | None) -> str:
    family = str(base_family or "unknown").strip().lower() or "unknown"
    pattern = str(mover_pattern or "missing").strip().lower() or "missing"
    if family == "market_state" and pattern in SPLIT_PATTERNS:
        return f"{family}:{pattern}"
    return family


def _snapshot_from_outcome_row(row: dict[str, Any]) -> dict[str, Any]:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    signal_source = str(row.get("signal_source") or metrics.get("source") or "").strip().lower() or "unknown"
    mover_pattern = str(row.get("mover_pattern0") or metrics.get("mover_pattern") or "").strip().lower() or "missing"
    base_source_family = signal_source_family(signal_source)
    return {
        "mint": str(row.get("mint") or "").strip(),
        "symbol": str(metrics.get("symbol") or row.get("symbol") or "").strip() or "n/a",
        "signal_ts": float(row.get("signal_ts") or 0.0),
        "signal_source": signal_source,
        "base_source_family": base_source_family,
        "source_family": _rank_family_key(base_source_family, mover_pattern),
        "signal_profile0": str(row.get("signal_profile0") or "").strip().lower() or "missing",
        "mover_pattern0": mover_pattern,
        "score0": _to_float(row.get("score0") if row.get("score0") is not None else row.get("signal_score")),
        "mcap0": _to_float(row.get("mcap0") if row.get("mcap0") is not None else row.get("marketcap0")),
        "liq0": _to_float(row.get("liq0") if row.get("liq0") is not None else metrics.get("liquidity")),
        "pair_age_min0": _to_float(row.get("pair_age_min0") if row.get("pair_age_min0") is not None else metrics.get("pair_age_min")),
        "mom5m0": _to_float(metrics.get("price_change_5m") if metrics.get("price_change_5m") is not None else metrics.get("momentum_5m_pct")),
        "hits0": _to_int(row.get("hits0")),
        "buys0": _to_int(row.get("buys0")),
        "uniq0": _to_int(row.get("uniq0")),
        "net_sol_in0": _to_float(row.get("net_sol_in0")),
        "buy_sell_ratio0": _to_float(metrics.get("buy_sell_ratio")),
        "top_buyer_share0": _to_float(row.get("top_buyer_share0")),
        "unique_buyers_status": signal_field_status(metrics, "unique_buyers", signal_source),
        "top_buyer_share_status": signal_field_status(metrics, "top_buyer_share", signal_source),
        "url": str(metrics.get("url") or "").strip(),
    }


def _features_from_snapshot(snapshot: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {
        "signal_source": str(snapshot.get("signal_source") or "unknown"),
        "source_family": str(snapshot.get("source_family") or "unknown"),
        "signal_profile0": str(snapshot.get("signal_profile0") or "missing"),
        "mover_pattern0": str(snapshot.get("mover_pattern0") or "missing"),
        "unique_buyers_status": str(snapshot.get("unique_buyers_status") or "unknown"),
        "top_buyer_share_status": str(snapshot.get("top_buyer_share_status") or "unknown"),
    }
    for field, edges in NUMERIC_BUCKETS.items():
        out[field] = bucket_value(_to_float(snapshot.get(field)), edges)
    return out


def _snapshot_from_tape_row(row: dict[str, Any]) -> dict[str, Any]:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    signal_source = str(row.get("source") or metrics.get("source") or "").strip().lower() or "unknown"
    mover_pattern = str(metrics.get("mover_pattern") or "").strip().lower() or "missing"
    base_source_family = signal_source_family(signal_source)
    return {
        "mint": str(row.get("mint") or "").strip(),
        "symbol": str(metrics.get("symbol") or "").strip() or "n/a",
        "signal_ts": _to_float(row.get("ts")) or 0.0,
        "signal_source": signal_source,
        "base_source_family": base_source_family,
        "source_family": _rank_family_key(base_source_family, mover_pattern),
        "signal_profile0": "missing",
        "mover_pattern0": mover_pattern,
        "score0": _to_float(row.get("score") if row.get("score") is not None else metrics.get("score")),
        "mcap0": _to_float(metrics.get("market_cap") if metrics.get("market_cap") is not None else metrics.get("market_cap_usd")),
        "liq0": _to_float(metrics.get("liquidity") if metrics.get("liquidity") is not None else metrics.get("liquidity_usd")),
        "pair_age_min0": _to_float(metrics.get("pair_age_min")),
        "mom5m0": _to_float(metrics.get("price_change_5m") if metrics.get("price_change_5m") is not None else metrics.get("momentum_5m_pct")),
        "hits0": _to_int(metrics.get("hits")),
        "buys0": _to_int(metrics.get("buys")),
        "uniq0": _to_int(metrics.get("unique_buyers")),
        "net_sol_in0": _to_float(metrics.get("net_sol_in")),
        "buy_sell_ratio0": _to_float(metrics.get("buy_sell_ratio")),
        "top_buyer_share0": _to_float(metrics.get("top_buyer_share")),
        "unique_buyers_status": signal_field_status(metrics, "unique_buyers", signal_source),
        "top_buyer_share_status": signal_field_status(metrics, "top_buyer_share", signal_source),
        "url": str(metrics.get("url") or "").strip(),
    }


def load_outcome_rows(*, since_ts: float, winner_horizon_s: int) -> dict[str, list[dict[str, Any]]]:
    by_mint: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    with OUTCOMES.open("r", encoding="utf-8", errors="ignore") as fh:
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
                    "snapshot": _snapshot_from_outcome_row(row),
                    "rets": {},
                },
            )
            hz = _to_int(row.get("horizon_s"))
            ret = _to_float(row.get("ret"))
            if hz is not None and ret is not None:
                grp["rets"][hz] = ret
    out: dict[str, list[dict[str, Any]]] = {}
    for mint, items in by_mint.items():
        rows: list[dict[str, Any]] = []
        for key, item in items.items():
            snapshot = dict(item["snapshot"])
            rets = dict(item["rets"])
            snapshot["signal_key"] = key
            snapshot["max_ret_target"] = _max_ret(rets, winner_horizon_s)
            snapshot["max_ret_300s"] = _max_ret(rets, 300)
            snapshot["max_ret_900s"] = _max_ret(rets, 900)
            snapshot["max_ret_1800s"] = _max_ret(rets, 1800)
            snapshot["max_ret_21600s"] = _max_ret(rets, 21600)
            snapshot["features"] = _features_from_snapshot(snapshot)
            rows.append(snapshot)
        rows.sort(key=lambda r: float(r.get("signal_ts") or 0.0))
        out[mint] = rows
    return out


def _first_useful(rows: list[dict[str, Any]], winner_ret: float) -> dict[str, Any] | None:
    for row in rows:
        value = _to_float(row.get("max_ret_target"))
        if value is not None and value >= winner_ret:
            return row
    return None


def build_training_anchors(
    rows_by_mint: dict[str, list[dict[str, Any]]],
    *,
    validate_cutoff_ts: float,
    winner_ret: float,
) -> list[dict[str, Any]]:
    anchors: list[dict[str, Any]] = []
    for mint, rows in rows_by_mint.items():
        train_rows = [row for row in rows if float(row.get("signal_ts") or 0.0) < validate_cutoff_ts]
        if not train_rows:
            continue
        useful = _first_useful(train_rows, winner_ret)
        anchor = dict(useful or train_rows[0])
        anchor["label_winner"] = bool(useful is not None)
        anchor["anchor_kind"] = "earliest_useful" if useful is not None else "first_signal"
        anchor["mint"] = mint
        anchors.append(anchor)
    return anchors


def build_training_report(
    anchors: list[dict[str, Any]],
    *,
    min_slice_support: int,
) -> dict[str, Any]:
    family_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in anchors:
        family_rows[str(row.get("source_family") or "unknown")].append(row)

    family_summary: dict[str, dict[str, Any]] = {}
    for family, rows in family_rows.items():
        winners = sum(1 for row in rows if row.get("label_winner"))
        family_summary[family] = {
            "n": len(rows),
            "winner_rate": winners / max(1, len(rows)),
            "winners": winners,
        }

    slice_map: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    fields = [
        "signal_source",
        "signal_profile0",
        "mover_pattern0",
        "unique_buyers_status",
        "top_buyer_share_status",
    ] + [field for field in NUMERIC_BUCKETS]
    for row in anchors:
        family = str(row.get("source_family") or "unknown")
        features = row.get("features") if isinstance(row.get("features"), dict) else {}
        for field in fields:
            value = str(features.get(field) or "missing")
            slice_map[(family, field, value)].append(row)

    positive: list[dict[str, Any]] = []
    negative: list[dict[str, Any]] = []
    for (family, field, value), rows in slice_map.items():
        n = len(rows)
        if n < min_slice_support:
            continue
        winners = sum(1 for row in rows if row.get("label_winner"))
        precision = winners / max(1, n)
        baseline = float((family_summary.get(family) or {}).get("winner_rate") or 0.0)
        uplift = precision - baseline
        edge_score = (uplift * 100.0) + min(10.0, math.log10(n + 1.0) * 5.0)
        item = {
            "source_family": family,
            "field": field,
            "value": value,
            "n": n,
            "winners": winners,
            "precision": precision,
            "baseline_precision": baseline,
            "edge_score": edge_score,
        }
        if uplift > 0:
            positive.append(item)
        elif uplift < 0:
            negative.append(item)

    positive.sort(key=lambda row: (float(row["edge_score"]), int(row["n"])), reverse=True)
    negative.sort(key=lambda row: (abs(float(row["edge_score"])), int(row["n"])), reverse=True)

    return {
        "generated_at": time.time(),
        "family_summary": family_summary,
        "recommended_profiles": {},
        "top_positive_slices": positive[:120],
        "top_negative_slices": negative[:120],
        "training_summary": {
            "anchors": len(anchors),
            "winners": sum(1 for row in anchors if row.get("label_winner")),
            "base_precision": sum(1 for row in anchors if row.get("label_winner")) / max(1, len(anchors)),
            "anchor_kind_counts": dict(Counter(str(row.get("anchor_kind") or "unknown") for row in anchors)),
        },
    }


def score_validation_rows(
    rows_by_mint: dict[str, list[dict[str, Any]]],
    *,
    validate_cutoff_ts: float,
    report: dict[str, Any],
    min_family_samples: int,
    winner_ret: float,
) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for mint, rows in rows_by_mint.items():
        for row in rows:
            signal_ts = float(row.get("signal_ts") or 0.0)
            if signal_ts < validate_cutoff_ts:
                continue
            if _to_float(row.get("max_ret_target")) is None:
                continue
            rank = score_candidate_from_report(
                report=report,
                features=row.get("features") if isinstance(row.get("features"), dict) else {},
                min_family_samples=min_family_samples,
            )
            scored.append(
                {
                    "mint": mint,
                    "symbol": row.get("symbol") or "n/a",
                    "signal_ts": signal_ts,
                    "signal_source": row.get("signal_source") or "unknown",
                    "base_source_family": row.get("base_source_family") or "unknown",
                    "source_family": row.get("source_family") or "unknown",
                    "score": rank.score,
                    "rank_active": rank.active,
                    "rank_family_n": rank.family_sample_size,
                    "positive_matches": rank.positive_matches[:6],
                    "negative_matches": rank.negative_matches[:6],
                    "recommended_matches": rank.recommended_matches[:6],
                    "winner": bool((_to_float(row.get("max_ret_target")) or -1.0) >= winner_ret),
                    "max_ret_target": _to_float(row.get("max_ret_target")),
                    "mcap0": _to_float(row.get("mcap0")),
                    "pair_age_min0": _to_float(row.get("pair_age_min0")),
                    "mom5m0": _to_float(row.get("mom5m0")),
                    "hits0": _to_int(row.get("hits0")),
                    "net_sol_in0": _to_float(row.get("net_sol_in0")),
                    "mover_pattern0": row.get("mover_pattern0") or "missing",
                }
            )
    scored.sort(key=lambda row: (float(row["score"]), float(row["signal_ts"])), reverse=True)
    return scored


def summarize_validation(scored_rows: list[dict[str, Any]]) -> dict[str, Any]:
    base_precision = sum(1 for row in scored_rows if row.get("winner")) / max(1, len(scored_rows))
    thresholds: dict[str, dict[str, Any]] = {}
    for threshold in (55, 60, 65, 70, 75, 80):
        subset = [row for row in scored_rows if float(row.get("score") or 0.0) >= threshold]
        thresholds[str(threshold)] = {
            "n": len(subset),
            "precision": (sum(1 for row in subset if row.get("winner")) / max(1, len(subset))) if subset else 0.0,
        }
    topk: dict[str, dict[str, Any]] = {}
    for k in (10, 20, 30, 50):
        subset = scored_rows[:k]
        topk[str(k)] = {
            "n": len(subset),
            "precision": (sum(1 for row in subset if row.get("winner")) / max(1, len(subset))) if subset else 0.0,
        }
    family_breakdown: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in scored_rows:
        grouped[str(row.get("source_family") or "unknown")].append(row)
    for family, rows in grouped.items():
        family_breakdown[family] = {
            "n": len(rows),
            "precision": sum(1 for row in rows if row.get("winner")) / max(1, len(rows)),
            "mean_score": statistics.mean(float(row.get("score") or 0.0) for row in rows) if rows else 0.0,
        }
    return {
        "labeled_signals": len(scored_rows),
        "baseline_precision": base_precision,
        "thresholds": thresholds,
        "topk": topk,
        "family_breakdown": family_breakdown,
    }


def load_live_candidates(
    *,
    since_ts: float,
    report: dict[str, Any],
    min_family_samples: int,
    top: int,
) -> list[dict[str, Any]]:
    latest_by_mint: dict[str, dict[str, Any]] = {}
    with TAPE.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            ts = _to_float(row.get("ts"))
            mint = str(row.get("mint") or "").strip()
            if ts is None or ts < since_ts or not mint:
                continue
            prev = latest_by_mint.get(mint)
            if prev is None or float(prev.get("ts") or 0.0) <= ts:
                latest_by_mint[mint] = row

    scored: list[dict[str, Any]] = []
    for row in latest_by_mint.values():
        snapshot = _snapshot_from_tape_row(row)
        rank = score_candidate_from_report(
            report=report,
            features=_features_from_snapshot(snapshot),
            min_family_samples=min_family_samples,
        )
        scored.append(
            {
                "mint": snapshot["mint"],
                "symbol": snapshot["symbol"],
                "signal_ts": snapshot["signal_ts"],
                "signal_source": snapshot["signal_source"],
                "base_source_family": snapshot["base_source_family"],
                "source_family": snapshot["source_family"],
                "rank_score": rank.score,
                "rank_active": rank.active,
                "positive_matches": rank.positive_matches[:6],
                "negative_matches": rank.negative_matches[:6],
                "recommended_matches": rank.recommended_matches[:6],
                "mcap0": snapshot["mcap0"],
                "pair_age_min0": snapshot["pair_age_min0"],
                "mom5m0": snapshot["mom5m0"],
                "hits0": snapshot["hits0"],
                "net_sol_in0": snapshot["net_sol_in0"],
                "mover_pattern0": snapshot["mover_pattern0"],
                "url": snapshot["url"],
            }
        )
    scored.sort(key=lambda row: (float(row["rank_score"]), float(row["signal_ts"])), reverse=True)
    return scored[:top]


def write_md(path: Path, report: dict[str, Any]) -> None:
    cfg = report["config"]
    train = report["training"]
    validation = report["validation"]
    live = report["live_candidates"]
    lines = [
        "# Rank-Only Monitor",
        "",
        f"- Train window: `{cfg['train_hours']}h`",
        f"- Validation window: `{cfg['validate_hours']}h`",
        f"- Winner target: `+{cfg['winner_ret'] * 100:.0f}% within {int(cfg['winner_horizon_s'] / 60)}m`",
        "",
        "## Training Anchors",
        "",
        f"- Anchors: `{int(train['training_summary']['anchors'])}`",
        f"- Winners: `{int(train['training_summary']['winners'])}`",
        f"- Base precision: `{train['training_summary']['base_precision'] * 100:.1f}%`",
        f"- Anchor kinds: `{train['training_summary']['anchor_kind_counts']}`",
        "",
        "## Validation",
        "",
        f"- Labeled signals: `{int(validation['summary']['labeled_signals'])}`",
        f"- Baseline precision: `{validation['summary']['baseline_precision'] * 100:.1f}%`",
        "",
        "| Threshold | Signals | Precision |",
        "|---|---:|---:|",
    ]
    for threshold, stats in validation["summary"]["thresholds"].items():
        lines.append(f"| `>= {threshold}` | {int(stats['n'])} | {stats['precision'] * 100:.1f}% |")
    lines.extend([
        "",
        "Top-k precision:",
        "",
        "| Top-k | Signals | Precision |",
        "|---|---:|---:|",
    ])
    for k, stats in validation["summary"]["topk"].items():
        lines.append(f"| `{k}` | {int(stats['n'])} | {stats['precision'] * 100:.1f}% |")
    lines.extend([
        "",
        "Validation by rank family:",
        "",
        "| Rank Family | Signals | Precision | Mean Score |",
        "|---|---:|---:|---:|",
    ])
    for family, stats in sorted(
        validation["summary"].get("family_breakdown", {}).items(),
        key=lambda kv: (-float(kv[1].get("precision") or 0.0), -int(kv[1].get("n") or 0), kv[0]),
    ):
        lines.append(
            f"| `{family}` | {int(stats['n'])} | {float(stats['precision']) * 100:.1f}% | {float(stats['mean_score']):.1f} |"
        )
    lines.extend([
        "",
        "## Top Validation Hits",
        "",
        "| Symbol | Mint | Score | Winner | Max Target | Source | Rank Family | MCap0 | Age0 | Mom5m0 | Pattern |",
        "|---|---|---:|---:|---:|---|---|---:|---:|---:|---|",
    ])
    for row in validation["top_rows"]:
        lines.append(
            f"| {row['symbol']} | `{row['mint']}` | {float(row['score']):.1f} | "
            f"{'yes' if row['winner'] else 'no'} | {_fmt_pct(_to_float(row.get('max_ret_target')))} | "
            f"`{row['signal_source']}` | `{row.get('source_family') or 'unknown'}` | {_fmt_num(_to_float(row.get('mcap0')), 0)} | "
            f"{_fmt_num(_to_float(row.get('pair_age_min0')), 1)} | {_fmt_num(_to_float(row.get('mom5m0')), 1)} | "
            f"`{row.get('mover_pattern0') or 'missing'}` |"
        )
    lines.extend([
        "",
        "## Current Live Candidates",
        "",
        "| Symbol | Mint | Score | Source | Rank Family | MCap | Age0 | Mom5m0 | Hits | NetSOL | Pattern |",
        "|---|---|---:|---|---|---:|---:|---:|---:|---:|---|",
    ])
    for row in live:
        lines.append(
            f"| {row['symbol']} | `{row['mint']}` | {float(row['rank_score']):.1f} | "
            f"`{row['signal_source']}` | `{row.get('source_family') or 'unknown'}` | {_fmt_num(_to_float(row.get('mcap0')), 0)} | "
            f"{_fmt_num(_to_float(row.get('pair_age_min0')), 1)} | {_fmt_num(_to_float(row.get('mom5m0')), 1)} | "
            f"{int(row.get('hits0') or 0)} | {_fmt_num(_to_float(row.get('net_sol_in0')), 2)} | "
            f"`{row.get('mover_pattern0') or 'missing'}` |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Rank-only monitor using earliest-useful winner anchors.")
    parser.add_argument("--train-hours", type=float, default=72.0)
    parser.add_argument("--validate-hours", type=float, default=6.0)
    parser.add_argument("--winner-ret", type=float, default=0.50)
    parser.add_argument("--winner-horizon-s", type=int, default=900)
    parser.add_argument("--live-lookback-min", type=float, default=90.0)
    parser.add_argument("--min-family-samples", type=int, default=30)
    parser.add_argument("--min-slice-support", type=int, default=8)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=OUT_MD)
    args = parser.parse_args()

    now = time.time()
    train_since_ts = now - ((float(args.train_hours) + float(args.validate_hours)) * 3600.0)
    validate_cutoff_ts = now - (float(args.validate_hours) * 3600.0)
    live_since_ts = now - (float(args.live_lookback_min) * 60.0)

    rows_by_mint = load_outcome_rows(since_ts=train_since_ts, winner_horizon_s=int(args.winner_horizon_s))
    anchors = build_training_anchors(
        rows_by_mint,
        validate_cutoff_ts=validate_cutoff_ts,
        winner_ret=float(args.winner_ret),
    )
    train_report = build_training_report(
        anchors,
        min_slice_support=int(args.min_slice_support),
    )
    validation_rows = score_validation_rows(
        rows_by_mint,
        validate_cutoff_ts=validate_cutoff_ts,
        report=train_report,
        min_family_samples=int(args.min_family_samples),
        winner_ret=float(args.winner_ret),
    )
    validation_summary = summarize_validation(validation_rows)
    live_candidates = load_live_candidates(
        since_ts=live_since_ts,
        report=train_report,
        min_family_samples=int(args.min_family_samples),
        top=int(args.top),
    )

    report = {
        "generated_at": now,
        "config": {
            "train_hours": float(args.train_hours),
            "validate_hours": float(args.validate_hours),
            "winner_ret": float(args.winner_ret),
            "winner_horizon_s": int(args.winner_horizon_s),
            "live_lookback_min": float(args.live_lookback_min),
            "min_family_samples": int(args.min_family_samples),
            "min_slice_support": int(args.min_slice_support),
        },
        "training": train_report,
        "validation": {
            "summary": validation_summary,
            "top_rows": validation_rows[: min(int(args.top), len(validation_rows))],
        },
        "live_candidates": live_candidates,
    }

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_md(args.out_md, report)
    print(
        f"rank_only_monitor: anchors={train_report['training_summary']['anchors']} "
        f"validation={validation_summary['labeled_signals']} live={len(live_candidates)}"
    )


if __name__ == "__main__":
    main()
