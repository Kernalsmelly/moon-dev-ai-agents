"""Helpers for source-aware signal ranking from outcome reports."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from src.meme_signal_contract import signal_field_status, signal_field_value, signal_source, signal_source_family


NUMERIC_BUCKETS: dict[str, list[float]] = {
    "score0": [35, 50, 65, 80, 90, 95],
    "mcap0": [10000, 30000, 60000, 100000, 150000, 220000, 400000, 800000],
    "liq0": [2500, 5000, 10000, 20000, 40000, 80000],
    "pair_age_min0": [5, 10, 20, 30, 45, 60, 120, 240],
    "mom5m0": [0, 2, 5, 10, 20, 40, 80, 120],
    "hits0": [3, 10, 30, 80, 150, 300, 800, 1500],
    "buys0": [2, 5, 10, 30, 80, 150, 300, 800],
    "uniq0": [2, 3, 5, 10, 20, 40, 80],
    "net_sol_in0": [0.5, 1, 2, 5, 10, 25, 50, 100, 200],
    "buy_sell_ratio0": [1.0, 1.1, 1.25, 1.5, 2.0, 3.0, 5.0],
    "top_buyer_share0": [0.2, 0.35, 0.5, 0.65, 0.8],
}


def bucket_value(value: float | None, edges: list[float]) -> str:
    if value is None or not math.isfinite(float(value)):
        return "missing"
    v = float(value)
    prev = "-inf"
    for edge in edges:
        if v < edge:
            return f"[{prev},{edge:g})"
        prev = f"{edge:g}"
    return f"[{edges[-1]:g},+inf)"


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


@dataclass
class SignalRankResult:
    family: str
    active: bool
    score: float
    positive_matches: list[str]
    negative_matches: list[str]
    recommended_matches: list[str]
    family_sample_size: int


def candidate_signal_rank_features(candidate: Any, metrics: Mapping[str, Any] | None) -> dict[str, str]:
    m = metrics if isinstance(metrics, Mapping) else {}
    src = signal_source(m)
    family = str(getattr(candidate, "signal_source_family", "") or signal_source_family(src))

    def pick_float(*values: Any) -> float | None:
        for value in values:
            out = _to_float(value)
            if out is not None:
                return out
        return None

    raw = {
        "signal_source": src or "unknown",
        "source_family": family or "unknown",
        "signal_profile0": str(getattr(candidate, "signal_profile", "") or "").strip().lower() or "missing",
        "mover_pattern0": str(
            getattr(candidate, "mover_pattern", "") or m.get("mover_pattern") or ""
        ).strip().lower() or "missing",
        "unique_buyers_status": signal_field_status(m, "unique_buyers", src),
        "top_buyer_share_status": signal_field_status(m, "top_buyer_share", src),
        "score0": pick_float(
            m.get("score"),
            getattr(candidate, "composite_score", None),
        ),
        "mcap0": pick_float(
            m.get("market_cap"),
            m.get("market_cap_usd"),
            getattr(candidate, "market_cap", None),
        ),
        "liq0": pick_float(
            m.get("liquidity"),
            m.get("liquidity_usd"),
            getattr(candidate, "liquidity", None),
        ),
        "pair_age_min0": pick_float(
            m.get("pair_age_min"),
            getattr(candidate, "pair_age_min", None),
        ),
        "mom5m0": pick_float(
            m.get("price_change_5m"),
            m.get("momentum_5m_pct"),
            getattr(candidate, "price_change_5m", None),
        ),
        "hits0": pick_float(m.get("hits")),
        "buys0": pick_float(m.get("buys")),
        "uniq0": pick_float(m.get("unique_buyers")),
        "net_sol_in0": pick_float(m.get("net_sol_in")),
        "buy_sell_ratio0": pick_float(m.get("buy_sell_ratio")),
        "top_buyer_share0": pick_float(
            signal_field_value(m, "top_buyer_share", source=src, allow_estimated=False, default=None)
        ),
    }

    out: dict[str, str] = {
        "signal_source": str(raw["signal_source"]),
        "source_family": str(raw["source_family"]),
        "signal_profile0": str(raw["signal_profile0"]),
        "mover_pattern0": str(raw["mover_pattern0"]),
        "unique_buyers_status": str(raw["unique_buyers_status"]),
        "top_buyer_share_status": str(raw["top_buyer_share_status"]),
    }
    for field, edges in NUMERIC_BUCKETS.items():
        out[field] = bucket_value(raw.get(field), edges)
    return out


def load_signal_rank_report(path: str | Path) -> dict[str, Any] | None:
    report_path = Path(path)
    if not report_path.exists():
        return None
    try:
        obj = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    return obj


def score_candidate_from_report(
    *,
    report: Mapping[str, Any] | None,
    features: Mapping[str, str],
    min_family_samples: int,
) -> SignalRankResult:
    family = str(features.get("source_family") or "unknown")
    family_summary = report.get("family_summary") if isinstance(report, Mapping) else {}
    family_stats = family_summary.get(family) if isinstance(family_summary, Mapping) else {}
    family_n = 0
    try:
        family_n = int((family_stats or {}).get("n") or 0)
    except Exception:
        family_n = 0

    active = family_n >= int(min_family_samples)
    if not active:
        return SignalRankResult(
            family=family,
            active=False,
            score=50.0,
            positive_matches=[],
            negative_matches=[],
            recommended_matches=[],
            family_sample_size=family_n,
        )

    recommended = report.get("recommended_profiles") if isinstance(report, Mapping) else {}
    family_recommended = recommended.get(family) if isinstance(recommended, Mapping) else {}
    recommended_matches: list[str] = []
    recommended_raw = 0.0
    for field, spec in (family_recommended or {}).items():
        if not isinstance(spec, Mapping):
            continue
        expected = str(spec.get("value") or "")
        if expected and str(features.get(field) or "") == expected:
            recommended_matches.append(f"{field}={expected}")
            try:
                recommended_raw += float(spec.get("edge_score") or 0.0)
            except Exception:
                pass

    positive_matches: list[str] = []
    negative_matches: list[str] = []
    positive_raw = 0.0
    negative_raw = 0.0

    for row, bucket, acc in (
        (report.get("top_positive_slices"), positive_matches, "pos"),
        (report.get("top_negative_slices"), negative_matches, "neg"),
    ):
        if not isinstance(row, list):
            continue
        for item in row:
            if not isinstance(item, Mapping):
                continue
            if str(item.get("source_family") or "") != family:
                continue
            field = str(item.get("field") or "")
            value = str(item.get("value") or "")
            if not field or not value:
                continue
            if str(features.get(field) or "") != value:
                continue
            label = f"{field}={value}"
            try:
                edge = abs(float(item.get("edge_score") or 0.0))
            except Exception:
                edge = 0.0
            if acc == "pos":
                positive_matches.append(label)
                positive_raw += edge
            else:
                negative_matches.append(label)
                negative_raw += edge

    raw = (recommended_raw * 0.70) + (positive_raw * 0.50) - (negative_raw * 0.85)
    if family_recommended and not recommended_matches:
        raw -= 12.0
    score = 50.0 + (50.0 * math.tanh(raw / 60.0))
    score = max(0.0, min(100.0, score))

    return SignalRankResult(
        family=family,
        active=True,
        score=score,
        positive_matches=positive_matches,
        negative_matches=negative_matches,
        recommended_matches=recommended_matches,
        family_sample_size=family_n,
    )
