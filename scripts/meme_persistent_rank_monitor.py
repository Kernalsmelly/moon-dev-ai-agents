#!/usr/bin/env python3
"""Train a rank-only monitor for persistent runners and score live signals.

This script differs from the generic rank monitor:
- positives are earliest-useful signals for mints that later classify as persistent runners
- negatives include earliest-useful signals that later round-trip or spike/fade,
  plus first signals for mints that never became useful winners
- pending 6h cases are excluded from training/validation
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
OUTCOMES = BASE / "data" / "signal_outcomes.jsonl"
TAPE = BASE / "data" / "meme_launch_signals.jsonl"
OUT_JSON = BASE / "data" / "meme_reports" / "persistent_rank_monitor.json"
OUT_MD = BASE / "data" / "meme_reports" / "persistent_rank_monitor.md"

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


def _classify_regime(snapshot: dict[str, Any]) -> str:
    mcap = _to_float(snapshot.get("mcap0"))
    age = _to_float(snapshot.get("pair_age_min0"))
    mom5m = _to_float(snapshot.get("mom5m0"))
    hits = float(snapshot.get("hits0") or 0.0)
    buys = float(snapshot.get("buys0") or 0.0)
    net_sol = _to_float(snapshot.get("net_sol_in0")) or 0.0
    pattern = str(snapshot.get("mover_pattern0") or "unknown")

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


def _regime_prior_adjustment(*, report: Mapping[str, Any], regime: str) -> tuple[float, dict[str, Any] | None]:
    training = report.get("training_summary") if isinstance(report, Mapping) else {}
    base = _to_float((training or {}).get("base_precision"))
    regime_summary = report.get("regime_summary") if isinstance(report, Mapping) else {}
    stats = regime_summary.get(regime) if isinstance(regime_summary, Mapping) else None
    if base is None or not isinstance(stats, Mapping):
        return 0.0, None
    rate = _to_float(stats.get("persistent_rate"))
    n = int(stats.get("n") or 0)
    if rate is None or n <= 0:
        return 0.0, None
    # Small, explicit prior: enough to differentiate regimes, not enough to dominate slice matches.
    adj = (rate - base) * 30.0
    return adj, {"persistent_rate": rate, "n": n}


def _snapshot_from_outcome_row(row: dict[str, Any]) -> dict[str, Any]:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    signal_source = str(row.get("signal_source") or metrics.get("source") or "").strip().lower() or "unknown"
    mover_pattern = str(row.get("mover_pattern0") or metrics.get("mover_pattern") or "").strip().lower() or "missing"
    base_source_family = signal_source_family(signal_source)
    snapshot = {
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
    snapshot["persistence_regime0"] = _classify_regime(snapshot)
    return snapshot


def _features_from_snapshot(snapshot: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {
        "signal_source": str(snapshot.get("signal_source") or "unknown"),
        "source_family": str(snapshot.get("source_family") or "unknown"),
        "signal_profile0": str(snapshot.get("signal_profile0") or "missing"),
        "mover_pattern0": str(snapshot.get("mover_pattern0") or "missing"),
        "persistence_regime0": str(snapshot.get("persistence_regime0") or "missing"),
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
    snapshot = {
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
    snapshot["persistence_regime0"] = _classify_regime(snapshot)
    return snapshot


def load_outcome_rows(*, since_ts: float) -> dict[str, list[dict[str, Any]]]:
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
            snapshot["ret_300s"] = _to_float(rets.get(300))
            snapshot["ret_900s"] = _to_float(rets.get(900))
            snapshot["ret_1800s"] = _to_float(rets.get(1800))
            snapshot["ret_21600s"] = _to_float(rets.get(21600))
            snapshot["max_ret_900s"] = _max_ret(rets, 900)
            snapshot["max_ret_1800s"] = _max_ret(rets, 1800)
            snapshot["max_ret_21600s"] = _max_ret(rets, 21600)
            snapshot["max_ret_all"] = max(rets.values()) if rets else None
            snapshot["features"] = _features_from_snapshot(snapshot)
            rows.append(snapshot)
        rows.sort(key=lambda r: float(r.get("signal_ts") or 0.0))
        out[mint] = rows
    return out


def _first_useful(rows: list[dict[str, Any]], winner_ret: float) -> dict[str, Any] | None:
    for row in rows:
        value = _to_float(row.get("max_ret_900s"))
        if value is not None and value >= winner_ret:
            return row
    return None


def _classify_persistence(
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
        return "round_trip_or_spike", retain if retain is not None else None
    if retain <= round_trip_retain:
        return "round_trip_or_spike", retain
    return "partial_persistence", retain


def _anchor_from_rows(
    rows: list[dict[str, Any]],
    *,
    winner_ret: float,
    persistent_ret: float,
    persistent_retain: float,
    round_trip_retain: float,
) -> dict[str, Any] | None:
    if not rows:
        return None
    useful = _first_useful(rows, winner_ret)
    if useful is None:
        anchor = dict(rows[0])
        anchor["label_persistent"] = False
        anchor["anchor_kind"] = "first_signal"
        anchor["persistence_class"] = "non_winner"
        return anchor
    klass, retain = _classify_persistence(
        useful,
        persistent_ret=persistent_ret,
        persistent_retain=persistent_retain,
        round_trip_retain=round_trip_retain,
    )
    if klass == "pending_6h":
        return None
    anchor = dict(useful)
    anchor["label_persistent"] = klass == "persistent_runner"
    anchor["anchor_kind"] = f"earliest_useful:{klass}"
    anchor["persistence_class"] = klass
    anchor["retention_6h"] = retain
    return anchor


def build_anchor_set(
    rows_by_mint: dict[str, list[dict[str, Any]]],
    *,
    min_ts: float | None,
    max_ts: float | None,
    winner_ret: float,
    persistent_ret: float,
    persistent_retain: float,
    round_trip_retain: float,
) -> list[dict[str, Any]]:
    anchors: list[dict[str, Any]] = []
    for mint, rows in rows_by_mint.items():
        subset = [
            row for row in rows
            if (min_ts is None or float(row.get("signal_ts") or 0.0) >= min_ts)
            and (max_ts is None or float(row.get("signal_ts") or 0.0) < max_ts)
        ]
        anchor = _anchor_from_rows(
            subset,
            winner_ret=winner_ret,
            persistent_ret=persistent_ret,
            persistent_retain=persistent_retain,
            round_trip_retain=round_trip_retain,
        )
        if anchor is None:
            continue
        anchor["mint"] = mint
        anchors.append(anchor)
    return anchors


def build_training_report(anchors: list[dict[str, Any]], *, min_slice_support: int) -> dict[str, Any]:
    family_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in anchors:
        family_rows[str(row.get("source_family") or "unknown")].append(row)

    family_summary: dict[str, dict[str, Any]] = {}
    for family, rows in family_rows.items():
        positives = sum(1 for row in rows if row.get("label_persistent"))
        useful_rows = [
            row
            for row in rows
            if str(row.get("persistence_class") or "") in {"persistent_runner", "round_trip_or_spike"}
        ]
        family_summary[family] = {
            "n": len(rows),
            "persistent_rate": positives / max(1, len(rows)),
            "persistent": positives,
            "useful_n": len(useful_rows),
            "persistent_given_useful": (
                sum(1 for row in useful_rows if row.get("label_persistent")) / max(1, len(useful_rows))
            ),
        }

    regime_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in anchors:
        features = row.get("features") if isinstance(row.get("features"), dict) else {}
        regime_rows[str(features.get("persistence_regime0") or "missing")].append(row)
    regime_summary: dict[str, dict[str, Any]] = {}
    for regime, rows in regime_rows.items():
        positives = sum(1 for row in rows if row.get("label_persistent"))
        useful_rows = [
            row
            for row in rows
            if str(row.get("persistence_class") or "") in {"persistent_runner", "round_trip_or_spike"}
        ]
        regime_summary[regime] = {
            "n": len(rows),
            "persistent_rate": positives / max(1, len(rows)),
            "persistent": positives,
            "useful_n": len(useful_rows),
            "persistent_given_useful": (
                sum(1 for row in useful_rows if row.get("label_persistent")) / max(1, len(useful_rows))
            ),
        }

    slice_map: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    contrast_map: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    fields = [
        "signal_source",
        "signal_profile0",
        "mover_pattern0",
        "persistence_regime0",
        "unique_buyers_status",
        "top_buyer_share_status",
    ] + [field for field in NUMERIC_BUCKETS]
    for row in anchors:
        family = str(row.get("source_family") or "unknown")
        features = row.get("features") if isinstance(row.get("features"), dict) else {}
        for field in fields:
            value = str(features.get(field) or "missing")
            slice_map[(family, field, value)].append(row)
            if str(row.get("persistence_class") or "") in {"persistent_runner", "round_trip_or_spike"}:
                contrast_map[(family, field, value)].append(row)

    positive: list[dict[str, Any]] = []
    negative: list[dict[str, Any]] = []
    for (family, field, value), rows in slice_map.items():
        n = len(rows)
        if n < min_slice_support:
            continue
        positives = sum(1 for row in rows if row.get("label_persistent"))
        precision = positives / max(1, n)
        baseline = float((family_summary.get(family) or {}).get("persistent_rate") or 0.0)
        uplift = precision - baseline
        contrast_rows = contrast_map.get((family, field, value), [])
        contrast_n = len(contrast_rows)
        contrast_precision = (
            sum(1 for row in contrast_rows if row.get("label_persistent")) / max(1, contrast_n)
            if contrast_n
            else None
        )
        contrast_baseline = float((family_summary.get(family) or {}).get("persistent_given_useful") or 0.0)
        contrast_uplift = (float(contrast_precision) - contrast_baseline) if contrast_precision is not None else 0.0
        edge_score = (
            (uplift * 100.0)
            + (contrast_uplift * 80.0)
            + min(10.0, math.log10(n + 1.0) * 5.0)
            + (min(6.0, math.log10(contrast_n + 1.0) * 3.0) if contrast_n else 0.0)
        )
        item = {
            "source_family": family,
            "field": field,
            "value": value,
            "n": n,
            "persistent": positives,
            "precision": precision,
            "baseline_precision": baseline,
            "contrast_n": contrast_n,
            "contrast_precision": contrast_precision,
            "contrast_baseline_precision": contrast_baseline if contrast_n else None,
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
        "regime_summary": regime_summary,
        "recommended_profiles": {},
        "top_positive_slices": positive[:120],
        "top_negative_slices": negative[:120],
        "training_summary": {
            "anchors": len(anchors),
            "persistent": sum(1 for row in anchors if row.get("label_persistent")),
            "base_precision": sum(1 for row in anchors if row.get("label_persistent")) / max(1, len(anchors)),
            "anchor_kind_counts": dict(Counter(str(row.get("anchor_kind") or "unknown") for row in anchors)),
        },
    }


def score_anchor_set(
    anchors: list[dict[str, Any]],
    *,
    report: dict[str, Any],
    min_family_samples: int,
) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for row in anchors:
        rank = score_candidate_from_report(
            report=report,
            features=row.get("features") if isinstance(row.get("features"), dict) else {},
            min_family_samples=min_family_samples,
        )
        regime = str((row.get("features") or {}).get("persistence_regime0") or "missing")
        regime_adj, regime_stats = _regime_prior_adjustment(report=report, regime=regime)
        adjusted_score = max(0.0, min(100.0, float(rank.score) + float(regime_adj)))
        scored.append(
            {
                "mint": row.get("mint"),
                "symbol": row.get("symbol") or "n/a",
                "signal_ts": float(row.get("signal_ts") or 0.0),
                "signal_source": row.get("signal_source") or "unknown",
                "base_source_family": row.get("base_source_family") or "unknown",
                "source_family": row.get("source_family") or "unknown",
                "score": adjusted_score,
                "base_score": rank.score,
                "rank_active": rank.active,
                "rank_family_n": rank.family_sample_size,
                "persistence_regime0": regime,
                "regime_prior_adjustment": regime_adj,
                "regime_prior_rate": _to_float((regime_stats or {}).get("persistent_rate")),
                "positive_matches": rank.positive_matches[:6],
                "negative_matches": rank.negative_matches[:6],
                "recommended_matches": rank.recommended_matches[:6],
                "persistent": bool(row.get("label_persistent")),
                "anchor_kind": row.get("anchor_kind") or "unknown",
                "persistence_class": row.get("persistence_class") or "unknown",
                "max_ret_900s": _to_float(row.get("max_ret_900s")),
                "ret_21600s": _to_float(row.get("ret_21600s")),
                "retention_6h": _to_float(row.get("retention_6h")),
                "mcap0": _to_float(row.get("mcap0")),
                "pair_age_min0": _to_float(row.get("pair_age_min0")),
                "mom5m0": _to_float(row.get("mom5m0")),
                "hits0": _to_int(row.get("hits0")),
                "buys0": _to_int(row.get("buys0")),
                "net_sol_in0": _to_float(row.get("net_sol_in0")),
                "mover_pattern0": row.get("mover_pattern0") or "missing",
            }
        )
    scored.sort(key=lambda row: (float(row["score"]), float(row["signal_ts"])), reverse=True)
    return scored


def summarize_validation(scored_rows: list[dict[str, Any]]) -> dict[str, Any]:
    base_precision = sum(1 for row in scored_rows if row.get("persistent")) / max(1, len(scored_rows))
    thresholds: dict[str, dict[str, Any]] = {}
    for threshold in (55, 60, 65, 70, 75, 80):
        subset = [row for row in scored_rows if float(row.get("score") or 0.0) >= threshold]
        thresholds[str(threshold)] = {
            "n": len(subset),
            "precision": (sum(1 for row in subset if row.get("persistent")) / max(1, len(subset))) if subset else 0.0,
        }
    topk: dict[str, dict[str, Any]] = {}
    for k in (10, 20, 30, 50):
        subset = scored_rows[:k]
        topk[str(k)] = {
            "n": len(subset),
            "precision": (sum(1 for row in subset if row.get("persistent")) / max(1, len(subset))) if subset else 0.0,
        }
    family_breakdown: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in scored_rows:
        grouped[str(row.get("source_family") or "unknown")].append(row)
    for family, rows in grouped.items():
        family_breakdown[family] = {
            "n": len(rows),
            "precision": sum(1 for row in rows if row.get("persistent")) / max(1, len(rows)),
            "mean_score": statistics.mean(float(row.get("score") or 0.0) for row in rows) if rows else 0.0,
        }
    return {
        "anchors": len(scored_rows),
        "baseline_precision": base_precision,
        "thresholds": thresholds,
        "topk": topk,
        "family_breakdown": family_breakdown,
    }


def _watchlist_gate_result(row: dict[str, Any], *, tier: str) -> dict[str, Any]:
    if tier == "strict":
        cfgs = [
            {
                "profile": "calm_continuation",
                "regimes": {"calm_continuation"},
                "score_min": 55.0,
                "mcap": (50000.0, 150000.0),
                "age": (8.0, 45.0),
                "mom5m": (5.0, 25.0),
                "hits": (80, 500),
                "buys": (60, 350),
                "net_sol": (4.0, 25.0),
                "max_misses": 0,
            },
            {
                "profile": "late_slow_expansion",
                "regimes": {"late_slow_expansion"},
                "score_min": 55.0,
                "mcap": (80000.0, 350000.0),
                "age": (45.0, 140.0),
                "mom5m": (5.0, 20.0),
                "hits": (100, 700),
                "buys": (80, 500),
                "net_sol": (5.0, 40.0),
                "max_misses": 0,
            },
        ]
    elif tier == "near":
        cfgs = [
            {
                "profile": "calm_continuation",
                "regimes": {"calm_continuation"},
                "score_min": 52.0,
                "mcap": (45000.0, 180000.0),
                "age": (5.0, 60.0),
                "mom5m": (4.0, 30.0),
                "hits": (60, 700),
                "buys": (40, 500),
                "net_sol": (3.0, 35.0),
                "max_misses": 1,
            },
            {
                "profile": "late_slow_expansion",
                "regimes": {"late_slow_expansion"},
                "score_min": 52.0,
                "mcap": (70000.0, 450000.0),
                "age": (35.0, 180.0),
                "mom5m": (4.0, 25.0),
                "hits": (80, 900),
                "buys": (60, 700),
                "net_sol": (4.0, 60.0),
                "max_misses": 1,
            },
            {
                "profile": "early_hot_breakout",
                "regimes": {"early_hot_breakout"},
                "score_min": 60.0,
                "mcap": (60000.0, 140000.0),
                "age": (5.0, 35.0),
                "mom5m": (15.0, 70.0),
                "hits": (200, 1500),
                "buys": (100, 1000),
                "net_sol": (8.0, 90.0),
                "max_misses": 1,
            },
        ]
    else:
        raise ValueError(f"unknown watchlist tier: {tier}")

    regime = str(row.get("persistence_regime0") or "missing")
    score = float(row.get("persistent_score") or 0.0)
    mcap = _to_float(row.get("mcap0"))
    age = _to_float(row.get("pair_age_min0"))
    mom5m = _to_float(row.get("mom5m0"))
    hits = _to_int(row.get("hits0"))
    buys = _to_int(row.get("buys0"))
    net_sol = _to_float(row.get("net_sol_in0"))

    best: dict[str, Any] | None = None
    for cfg in cfgs:
        misses: list[str] = []
        if regime not in cfg["regimes"]:
            misses.append("regime")
        if score < float(cfg["score_min"]):
            misses.append("score")
        lo, hi = cfg["mcap"]
        if mcap is None or not (lo <= mcap < hi):
            misses.append("mcap")
        lo, hi = cfg["age"]
        if age is None or not (lo <= age < hi):
            misses.append("age")
        lo, hi = cfg["mom5m"]
        if mom5m is None or not (lo <= mom5m < hi):
            misses.append("mom5m")
        lo, hi = cfg["hits"]
        if hits is None or not (lo <= hits < hi):
            misses.append("hits")
        lo, hi = cfg["buys"]
        if buys is not None and not (lo <= buys < hi):
            misses.append("buys")
        lo, hi = cfg["net_sol"]
        if net_sol is None or not (lo <= net_sol < hi):
            misses.append("net_sol")
        passed = len(misses) <= int(cfg["max_misses"])
        candidate = {
            "tier": tier,
            "profile": cfg["profile"],
            "passed": passed,
            "misses": misses,
        }
        if passed:
            return candidate
        if best is None or len(misses) < len(best["misses"]):
            best = candidate
    return best or {"tier": tier, "profile": "none", "passed": False, "misses": ["profile"]}


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
        regime = str(snapshot.get("persistence_regime0") or "missing")
        regime_adj, regime_stats = _regime_prior_adjustment(report=report, regime=regime)
        adjusted_score = max(0.0, min(100.0, float(rank.score) + float(regime_adj)))
        scored.append(
            {
                "mint": snapshot["mint"],
                "symbol": snapshot["symbol"],
                "signal_ts": snapshot["signal_ts"],
                "signal_source": snapshot["signal_source"],
                "base_source_family": snapshot["base_source_family"],
                "source_family": snapshot["source_family"],
                "persistent_score": adjusted_score,
                "base_score": rank.score,
                "rank_active": rank.active,
                "persistence_regime0": regime,
                "regime_prior_adjustment": regime_adj,
                "regime_prior_rate": _to_float((regime_stats or {}).get("persistent_rate")),
                "positive_matches": rank.positive_matches[:6],
                "negative_matches": rank.negative_matches[:6],
                "recommended_matches": rank.recommended_matches[:6],
                "mcap0": snapshot["mcap0"],
                "pair_age_min0": snapshot["pair_age_min0"],
                "mom5m0": snapshot["mom5m0"],
                "hits0": snapshot["hits0"],
                "buys0": snapshot["buys0"],
                "net_sol_in0": snapshot["net_sol_in0"],
                "buy_sell_ratio0": snapshot["buy_sell_ratio0"],
                "mover_pattern0": snapshot["mover_pattern0"],
                "url": snapshot["url"],
            }
        )
    scored.sort(key=lambda row: (float(row["persistent_score"]), float(row["signal_ts"])), reverse=True)
    return scored[:top]


def write_md(path: Path, report: dict[str, Any]) -> None:
    cfg = report["config"]
    train = report["training"]
    validation = report["validation"]
    live = report["live_candidates"]
    watchlist = report.get("watchlist_candidates") or []
    near_watchlist = report.get("near_watchlist_candidates") or []
    lines = [
        "# Persistent Rank Monitor",
        "",
        f"- Train window: `{cfg['train_hours']}h`",
        f"- Validation window: `{cfg['validate_hours']}h`",
        f"- Earliest useful winner threshold: `+{cfg['winner_ret'] * 100:.0f}% within 15m`",
        f"- Persistence target: `ret_6h >= {cfg['persistent_ret'] * 100:.0f}%` or `retain >= {cfg['persistent_retain'] * 100:.0f}% of peak`",
        "",
        "## Training Anchors",
        "",
        f"- Anchors: `{int(train['training_summary']['anchors'])}`",
        f"- Persistent runners: `{int(train['training_summary']['persistent'])}`",
        f"- Base precision: `{train['training_summary']['base_precision'] * 100:.1f}%`",
        f"- Anchor kinds: `{train['training_summary']['anchor_kind_counts']}`",
        "",
        "Training by persistence regime:",
        "",
        "| Regime | Anchors | Persistent Rate | Persistent |",
        "|---|---:|---:|---:|",
    ]
    for regime, stats in sorted(
        train.get("regime_summary", {}).items(),
        key=lambda kv: (-float(kv[1].get("persistent_rate") or 0.0), -int(kv[1].get("n") or 0), kv[0]),
    ):
        lines.append(
            f"| `{regime}` | {int(stats.get('n') or 0)} | {float(stats.get('persistent_rate') or 0.0) * 100:.1f}% | {int(stats.get('persistent') or 0)} |"
        )
    lines.extend([
        "",
        "## Validation",
        "",
        f"- Anchors: `{int(validation['summary']['anchors'])}`",
        f"- Baseline precision: `{validation['summary']['baseline_precision'] * 100:.1f}%`",
        "",
        "| Threshold | Anchors | Persistent Precision |",
        "|---|---:|---:|",
    ])
    for threshold, stats in validation["summary"]["thresholds"].items():
        lines.append(f"| `>= {threshold}` | {int(stats['n'])} | {stats['precision'] * 100:.1f}% |")
    lines.extend([
        "",
        "Top-k persistent precision:",
        "",
        "| Top-k | Anchors | Persistent Precision |",
        "|---|---:|---:|",
    ])
    for k, stats in validation["summary"]["topk"].items():
        lines.append(f"| `{k}` | {int(stats['n'])} | {stats['precision'] * 100:.1f}% |")
    lines.extend([
        "",
        "Validation by rank family:",
        "",
        "| Rank Family | Anchors | Persistent Precision | Mean Score |",
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
        "## Top Validation Anchors",
        "",
        "| Symbol | Mint | Score | Base | Regime | Persistent | Class | Source | Rank Family | MCap0 | Age0 | Mom5m0 | Pattern |",
        "|---|---|---:|---:|---|---:|---|---|---|---:|---:|---:|---|",
    ])
    for row in validation["top_rows"]:
        lines.append(
            f"| {row['symbol']} | `{row['mint']}` | {float(row['score']):.1f} | "
            f"{float(row.get('base_score') or 0.0):.1f} | `{row.get('persistence_regime0') or 'missing'}` | "
            f"{'yes' if row['persistent'] else 'no'} | `{row.get('persistence_class') or 'unknown'}` | "
            f"`{row['signal_source']}` | `{row.get('source_family') or 'unknown'}` | "
            f"{_fmt_num(_to_float(row.get('mcap0')), 0)} | {_fmt_num(_to_float(row.get('pair_age_min0')), 1)} | "
            f"{_fmt_num(_to_float(row.get('mom5m0')), 1)} | `{row.get('mover_pattern0') or 'missing'}` |"
        )
    lines.extend([
        "",
        "## Live Persistent Leaderboard",
        "",
        "| Symbol | Mint | Persistent Score | Base | Regime | Source | Rank Family | MCap | Age0 | Mom5m0 | Hits | NetSOL | Pattern |",
        "|---|---|---:|---:|---|---|---|---:|---:|---:|---:|---:|---|",
    ])
    for row in live:
        lines.append(
            f"| {row['symbol']} | `{row['mint']}` | {float(row['persistent_score']):.1f} | "
            f"{float(row.get('base_score') or 0.0):.1f} | `{row.get('persistence_regime0') or 'missing'}` | "
            f"`{row['signal_source']}` | `{row.get('source_family') or 'unknown'}` | "
            f"{_fmt_num(_to_float(row.get('mcap0')), 0)} | {_fmt_num(_to_float(row.get('pair_age_min0')), 1)} | "
            f"{_fmt_num(_to_float(row.get('mom5m0')), 1)} | {int(row.get('hits0') or 0)} | "
            f"{_fmt_num(_to_float(row.get('net_sol_in0')), 2)} | `{row.get('mover_pattern0') or 'missing'}` |"
        )
    lines.extend([
        "",
        "## Actionable Watchlist",
        "",
        "- Regime-aware strict gate: `calm_continuation` or `late_slow_expansion` profiles only",
        "",
        "| Symbol | Mint | Persistent Score | Regime | Profile | Rank Family | MCap | Age0 | Mom5m0 | Hits | Buys | NetSOL |",
        "|---|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|",
    ])
    for row in watchlist:
        lines.append(
            f"| {row['symbol']} | `{row['mint']}` | {float(row['persistent_score']):.1f} | "
            f"`{row.get('persistence_regime0') or 'missing'}` | `{row.get('watchlist_profile') or 'unknown'}` | "
            f"`{row.get('source_family') or 'unknown'}` | {_fmt_num(_to_float(row.get('mcap0')), 0)} | "
            f"{_fmt_num(_to_float(row.get('pair_age_min0')), 1)} | {_fmt_num(_to_float(row.get('mom5m0')), 1)} | "
            f"{int(row.get('hits0') or 0)} | {int(row.get('buys0') or 0)} | {_fmt_num(_to_float(row.get('net_sol_in0')), 2)} |"
        )
    lines.extend([
        "",
        "## Near Watchlist",
        "",
        "- Regime-aware near gate: relaxed `calm_continuation` / `late_slow_expansion`, plus an `early_hot_breakout` research profile",
        "",
        "| Symbol | Mint | Persistent Score | Regime | Profile | Rank Family | MCap | Age0 | Mom5m0 | Hits | Buys | NetSOL | Strict Misses |",
        "|---|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---|",
    ])
    for row in near_watchlist:
        misses = ",".join(row.get("watchlist_misses") or []) or "-"
        lines.append(
            f"| {row['symbol']} | `{row['mint']}` | {float(row['persistent_score']):.1f} | "
            f"`{row.get('persistence_regime0') or 'missing'}` | `{row.get('near_watchlist_profile') or 'unknown'}` | "
            f"`{row.get('source_family') or 'unknown'}` | {_fmt_num(_to_float(row.get('mcap0')), 0)} | "
            f"{_fmt_num(_to_float(row.get('pair_age_min0')), 1)} | {_fmt_num(_to_float(row.get('mom5m0')), 1)} | "
            f"{int(row.get('hits0') or 0)} | {int(row.get('buys0') or 0)} | {_fmt_num(_to_float(row.get('net_sol_in0')), 2)} | "
            f"`{misses}` |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Rank-only monitor for persistent runners.")
    parser.add_argument("--train-hours", type=float, default=72.0)
    parser.add_argument("--validate-hours", type=float, default=12.0)
    parser.add_argument("--winner-ret", type=float, default=0.50)
    parser.add_argument("--persistent-ret", type=float, default=1.00)
    parser.add_argument("--persistent-retain", type=float, default=0.50)
    parser.add_argument("--round-trip-retain", type=float, default=0.15)
    parser.add_argument("--live-lookback-min", type=float, default=120.0)
    parser.add_argument("--min-family-samples", type=int, default=20)
    parser.add_argument("--min-slice-support", type=int, default=6)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=OUT_MD)
    args = parser.parse_args()

    now = time.time()
    train_since_ts = now - ((float(args.train_hours) + float(args.validate_hours)) * 3600.0)
    validate_cutoff_ts = now - (float(args.validate_hours) * 3600.0)
    live_since_ts = now - (float(args.live_lookback_min) * 60.0)

    rows_by_mint = load_outcome_rows(since_ts=train_since_ts)
    train_anchors = build_anchor_set(
        rows_by_mint,
        min_ts=None,
        max_ts=validate_cutoff_ts,
        winner_ret=float(args.winner_ret),
        persistent_ret=float(args.persistent_ret),
        persistent_retain=float(args.persistent_retain),
        round_trip_retain=float(args.round_trip_retain),
    )
    train_report = build_training_report(train_anchors, min_slice_support=int(args.min_slice_support))
    validation_anchors = build_anchor_set(
        rows_by_mint,
        min_ts=validate_cutoff_ts,
        max_ts=None,
        winner_ret=float(args.winner_ret),
        persistent_ret=float(args.persistent_ret),
        persistent_retain=float(args.persistent_retain),
        round_trip_retain=float(args.round_trip_retain),
    )
    validation_rows = score_anchor_set(
        validation_anchors,
        report=train_report,
        min_family_samples=int(args.min_family_samples),
    )
    validation_summary = summarize_validation(validation_rows)
    live_candidates = load_live_candidates(
        since_ts=live_since_ts,
        report=train_report,
        min_family_samples=int(args.min_family_samples),
        top=int(args.top),
    )
    watchlist_candidates: list[dict[str, Any]] = []
    near_watchlist_candidates: list[dict[str, Any]] = []
    for row in live_candidates:
        strict = _watchlist_gate_result(row, tier="strict")
        near = _watchlist_gate_result(row, tier="near")
        if strict["passed"]:
            row["watchlist_misses"] = []
            row["watchlist_profile"] = strict.get("profile")
            watchlist_candidates.append(row)
            continue
        row["watchlist_misses"] = strict["misses"]
        if near["passed"]:
            row["near_watchlist_misses"] = near["misses"]
            row["near_watchlist_profile"] = near.get("profile")
            near_watchlist_candidates.append(row)

    report = {
        "generated_at": now,
        "config": {
            "train_hours": float(args.train_hours),
            "validate_hours": float(args.validate_hours),
            "winner_ret": float(args.winner_ret),
            "persistent_ret": float(args.persistent_ret),
            "persistent_retain": float(args.persistent_retain),
            "round_trip_retain": float(args.round_trip_retain),
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
        "watchlist_candidates": watchlist_candidates,
        "near_watchlist_candidates": near_watchlist_candidates,
    }

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_md(args.out_md, report)
    print(
        f"persistent_rank_monitor: train_anchors={train_report['training_summary']['anchors']} "
        f"validation_anchors={validation_summary['anchors']} live={len(live_candidates)}"
    )


if __name__ == "__main__":
    main()
