#!/usr/bin/env python3
"""Assemble a lifecycle board from existing meme research reports."""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
REPORTS = BASE / "data" / "meme_reports"
PENDING_JSON = REPORTS / "pending_maturation_report.json"
RESEARCH_JSON = REPORTS / "research_priority_monitor.json"
PERSIST24_JSON = REPORTS / "winner_persistence_report_24h.json"
SURVIVOR_RESEARCH_JSON = REPORTS / "meme_survivor_feature_research.json"
OUT_JSON = REPORTS / "meme_lifecycle_monitor.json"
OUT_MD = REPORTS / "meme_lifecycle_monitor.md"

SURVIVOR_CLASSES = {"persistent_runner", "partial_persistence"}
FAILED_CLASSES = {"short_lived_spike", "round_trip_winner"}
STAGE_ORDER = {
    "emerging_watchlist": 0,
    "pending_promote_now": 1,
    "pending_watch": 2,
    "pending_cut_bias": 3,
    "matured_survivor": 4,
    "matured_failed": 5,
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100.0:.1f}%"


def _fmt_num(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def _coerce_float(value: Any) -> float | None:
    if value in (None, "", "missing"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pending_stage(row: dict[str, Any]) -> str:
    decision = str(row.get("promotion_decision") or "")
    if decision == "promote_now":
        return "pending_promote_now"
    if decision in {"watch_to_60m", "hold_and_recheck", "fragile_watch", "too_early"}:
        return "pending_watch"
    return "pending_cut_bias"


def _feature_value(row: dict[str, Any], field: str) -> Any:
    field = field.replace("feat__", "")
    if field == "source_family":
        source_family = row.get("source_family")
        if source_family:
            return source_family
        signal_source = str(row.get("signal_source") or row.get("source") or "")
        mover_pattern = str(row.get("mover_pattern0") or row.get("regime") or "")
        if signal_source == "dex_mover":
            if mover_pattern in {"breakout", "retest_hold"}:
                return f"market_state:{mover_pattern}"
            return "market_state"
        if signal_source == "ws_logs":
            return "flow_discovery"
        return signal_source or None
    return row.get(field)


def _bucket_match(value: Any, bucket: str) -> bool:
    if value in (None, "", "missing"):
        return False
    if bucket.startswith("[") and bucket.endswith(")"):
        numeric_value = _coerce_float(value)
        if numeric_value is None:
            return False
        left, right = bucket[1:-1].split(",", 1)
        lower = None if left == "-inf" else float(left)
        upper = None if right == "+inf" else float(right)
        if lower is not None and numeric_value < lower:
            return False
        if upper is not None and numeric_value >= upper:
            return False
        return True
    return str(value) == bucket


def _bucket_label(field: str, bucket: str) -> str:
    field = field.replace("feat__", "")
    aliases = {
        "pair_age_min0": "age",
        "mcap0": "mcap",
        "net_sol_in0": "net_sol",
        "buy_sell_ratio0": "bsr",
        "mom5m0": "mom5m",
        "hits0": "hits",
        "source_family": "family",
        "mover_pattern0": "pattern",
    }
    label = aliases.get(field, field)
    return f"{label} {bucket}"


def _survivor_fit(row: dict[str, Any], survivor_research: dict[str, Any]) -> dict[str, Any]:
    positives: list[str] = []
    negatives: list[str] = []
    pos_delta = 0.0
    neg_delta = 0.0

    for bucket in list(survivor_research.get("top_positive_buckets") or [])[:10]:
        field = str(bucket.get("field") or "")
        value = bucket.get("value")
        if not field or value is None:
            continue
        if _bucket_match(_feature_value(row, field), str(value)):
            pos_delta += float(bucket.get("delta_vs_baseline") or 0.0)
            positives.append(_bucket_label(field, str(value)))

    for bucket in list(survivor_research.get("top_negative_buckets") or [])[:10]:
        field = str(bucket.get("field") or "")
        value = bucket.get("value")
        if not field or value is None:
            continue
        if _bucket_match(_feature_value(row, field), str(value)):
            neg_delta += abs(float(bucket.get("delta_vs_baseline") or 0.0))
            negatives.append(_bucket_label(field, str(value)))

    fit = max(0.0, min(100.0, 50.0 + (35.0 * (pos_delta - neg_delta))))
    if fit >= 75.0:
        stance = "survivor_friendly"
    elif fit >= 55.0:
        stance = "balanced"
    elif fit >= 35.0:
        stance = "fragile"
    else:
        stance = "overheated_or_weak"
    return {
        "score": fit,
        "stance": stance,
        "positive_tags": positives[:3],
        "negative_tags": negatives[:3],
    }


def _transition_kind(prev_stage: str | None, new_stage: str | None, prev_status: str | None, new_status: str | None) -> str:
    if not prev_stage and new_stage:
        return "new_entry"
    if prev_stage and not new_stage:
        return "exited"
    if prev_stage != new_stage:
        if new_stage in {"pending_promote_now", "matured_survivor"}:
            return "promotion"
        if new_stage in {"pending_cut_bias", "matured_failed"}:
            return "deterioration"
        return "stage_shift"
    if prev_status != new_status:
        return "status_shift"
    return "stable"


def _transition_label(prev_stage: str | None, new_stage: str | None, prev_status: str | None, new_status: str | None) -> str:
    if not prev_stage and new_stage:
        return f"new -> {new_stage}"
    if prev_stage and not new_stage:
        return f"{prev_stage} -> exited"
    if prev_stage != new_stage:
        return f"{prev_stage} -> {new_stage}"
    if prev_status != new_status:
        return f"{prev_status or 'n/a'} -> {new_status or 'n/a'}"
    return "stable"


def _build_transitions(board: list[dict[str, Any]], previous_report: dict[str, Any] | None) -> dict[str, Any]:
    prev_board = list((previous_report or {}).get("board") or [])
    prev_by_mint = {str(row.get("mint") or ""): row for row in prev_board if row.get("mint")}
    cur_by_mint = {str(row.get("mint") or ""): row for row in board if row.get("mint")}

    transitions: list[dict[str, Any]] = []

    for mint, row in cur_by_mint.items():
        prev = prev_by_mint.get(mint)
        prev_stage = str(prev.get("stage") or "") if prev else None
        prev_status = str(prev.get("status") or "") if prev else None
        new_stage = str(row.get("stage") or "") or None
        new_status = str(row.get("status") or "") or None
        kind = _transition_kind(prev_stage, new_stage, prev_status, new_status)
        if kind == "stable":
            continue
        transitions.append(
            {
                "symbol": row.get("symbol") or (prev.get("symbol") if prev else "n/a"),
                "mint": mint,
                "kind": kind,
                "label": _transition_label(prev_stage, new_stage, prev_status, new_status),
                "from_stage": prev_stage,
                "to_stage": new_stage,
                "from_status": prev_status,
                "to_status": new_status,
            }
        )

    for mint, row in prev_by_mint.items():
        if mint in cur_by_mint:
            continue
        prev_stage = str(row.get("stage") or "") or None
        prev_status = str(row.get("status") or "") or None
        transitions.append(
            {
                "symbol": row.get("symbol") or "n/a",
                "mint": mint,
                "kind": "exited",
                "label": _transition_label(prev_stage, None, prev_status, None),
                "from_stage": prev_stage,
                "to_stage": None,
                "from_status": prev_status,
                "to_status": None,
            }
        )

    priority = {
        "promotion": 0,
        "deterioration": 1,
        "stage_shift": 2,
        "status_shift": 3,
        "new_entry": 4,
        "exited": 5,
    }
    transitions.sort(key=lambda row: (priority.get(str(row.get("kind") or ""), 99), str(row.get("symbol") or "")))
    counts = Counter(str(row.get("kind") or "unknown") for row in transitions)
    return {
        "counts": dict(counts),
        "items": transitions,
    }


def build_report(
    pending_report: dict[str, Any],
    research: dict[str, Any],
    persistence24: dict[str, Any],
    survivor_research: dict[str, Any],
    previous_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pending_rows = list(pending_report.get("pending_rows") or [])
    pending_mints = {str(row.get("mint") or "") for row in pending_rows}

    persistence_rows = list(persistence24.get("top_winners") or [])
    matured_mints = {str(row.get("mint") or "") for row in persistence_rows}

    board: list[dict[str, Any]] = []

    for row in research.get("live") or []:
        mint = str(row.get("mint") or "")
        if not mint or mint in pending_mints or mint in matured_mints:
            continue
        fit = _survivor_fit(row, survivor_research)
        board.append(
            {
                "symbol": row.get("symbol") or "n/a",
                "mint": mint,
                "stage": "emerging_watchlist",
                "source": row.get("signal_source") or "unknown",
                "regime": row.get("persistence_regime0") or "unknown",
                "useful_score": row.get("useful_score"),
                "persistent_score": row.get("persistent_score"),
                "composite_score": row.get("composite_score"),
                "status": "watchlist",
                "detail_metric": row.get("mom5m0"),
                "detail_label": "mom5m0",
                "shape_state": "forming",
                "shape_score": None,
                "shape_steam_loss": False,
                "shape_path_30_to_60": None,
                "shape_path_historical_survivor_precision": None,
                "survivor_fit": fit["score"],
                "survivor_fit_stance": fit["stance"],
                "survivor_fit_positive_tags": fit["positive_tags"],
                "survivor_fit_negative_tags": fit["negative_tags"],
            }
        )

    for row in pending_rows:
        fit = _survivor_fit(row, survivor_research)
        board.append(
            {
                "symbol": row.get("symbol") or "n/a",
                "mint": row.get("mint") or "",
                "stage": _pending_stage(row),
                "source": row.get("signal_source") or "unknown",
                "regime": row.get("persistence_regime0") or "unknown",
                "useful_score": row.get("useful_score"),
                "persistent_score": row.get("persistent_score"),
                "composite_score": None,
                "status": row.get("promotion_decision") or row.get("progress_hint") or "pending",
                "detail_metric": row.get("latest_ret"),
                "detail_label": "latest_ret",
                "shape_state": row.get("shape_state") or "unknown",
                "shape_score": row.get("shape_score"),
                "shape_steam_loss": bool(row.get("shape_steam_loss")),
                "shape_path_30_to_60": row.get("shape_path_30_to_60"),
                "shape_path_historical_survivor_precision": row.get("shape_path_historical_survivor_precision"),
                "survivor_fit": fit["score"],
                "survivor_fit_stance": fit["stance"],
                "survivor_fit_positive_tags": fit["positive_tags"],
                "survivor_fit_negative_tags": fit["negative_tags"],
            }
        )

    for row in persistence_rows:
        klass = str(row.get("persistence_class") or "unknown")
        if klass in SURVIVOR_CLASSES:
            stage = "matured_survivor"
        elif klass in FAILED_CLASSES:
            stage = "matured_failed"
        else:
            continue
        fit = _survivor_fit(row, survivor_research)
        board.append(
            {
                "symbol": row.get("symbol") or "n/a",
                "mint": row.get("mint") or "",
                "stage": stage,
                "source": row.get("signal_source") or "unknown",
                "regime": row.get("mover_pattern0") or "unknown",
                "useful_score": None,
                "persistent_score": None,
                "composite_score": None,
                "status": klass,
                "detail_metric": row.get("ret_6h"),
                "detail_label": "ret_6h",
                "shape_state": row.get("shape_60m") or "unknown",
                "shape_score": row.get("shape_60m_score"),
                "shape_steam_loss": bool(row.get("shape_60m_steam_loss")),
                "shape_path_30_to_60": row.get("shape_path_30_to_60"),
                "shape_path_historical_survivor_precision": row.get("shape_path_historical_survivor_precision"),
                "survivor_fit": fit["score"],
                "survivor_fit_stance": fit["stance"],
                "survivor_fit_positive_tags": fit["positive_tags"],
                "survivor_fit_negative_tags": fit["negative_tags"],
            }
        )

    board.sort(
        key=lambda row: (
            STAGE_ORDER.get(str(row.get("stage") or ""), 999),
            float(row.get("composite_score") or row.get("useful_score") or -999.0),
            float(row.get("detail_metric") or -999.0),
        ),
        reverse=False,
    )

    stage_counts = Counter(str(row.get("stage") or "unknown") for row in board)
    transitions = _build_transitions(board, previous_report)
    return {
        "generated_at": time.time(),
        "summary": {
            "emerging_watchlist": stage_counts.get("emerging_watchlist", 0),
            "pending_promote_now": stage_counts.get("pending_promote_now", 0),
            "pending_watch": stage_counts.get("pending_watch", 0),
            "pending_cut_bias": stage_counts.get("pending_cut_bias", 0),
            "matured_survivor": stage_counts.get("matured_survivor", 0),
            "matured_failed": stage_counts.get("matured_failed", 0),
            "board_size": len(board),
        },
        "transitions": transitions,
        "board": board,
    }


def write_md(path: Path, report: dict[str, Any]) -> None:
    s = report["summary"]
    lines = [
        "# Lifecycle Monitor",
        "",
        "One board for the coin life cycle: emerging, pending, promoted, and failed.",
        "",
        "## Summary",
        "",
        f"- Emerging watchlist: `{s['emerging_watchlist']}`",
        f"- Pending promote-now: `{s['pending_promote_now']}`",
        f"- Pending watch: `{s['pending_watch']}`",
        f"- Pending cut-bias: `{s['pending_cut_bias']}`",
        f"- Matured survivor: `{s['matured_survivor']}`",
        f"- Matured failed: `{s['matured_failed']}`",
        "",
        "## Transitions",
        "",
    ]
    transitions = report.get("transitions") or {}
    counts = transitions.get("counts") or {}
    items = list(transitions.get("items") or [])
    if counts:
        lines.extend(
            [
                f"- Promotions: `{counts.get('promotion', 0)}`",
                f"- Deteriorations: `{counts.get('deterioration', 0)}`",
                f"- Stage shifts: `{counts.get('stage_shift', 0)}`",
                f"- Status shifts: `{counts.get('status_shift', 0)}`",
                f"- New entries: `{counts.get('new_entry', 0)}`",
                f"- Exits: `{counts.get('exited', 0)}`",
                "",
            ]
        )
        for row in items[:8]:
            lines.append(f"- `{row['symbol']}`: `{row['label']}`")
    else:
        lines.extend(["- No meaningful lifecycle transitions since the last refresh.", ""])
    lines.extend(
        [
        "| Symbol | Stage | Status | Source | Regime | Shape | Useful | Persistent | Survivor Fit | Detail |",
        "|---|---|---|---|---|---|---:|---:|---|---|",
        ]
    )
    for row in report["board"]:
        detail = f"{row['detail_label']}={_fmt_pct(row['detail_metric']) if row['detail_label'].endswith('ret') else _fmt_num(row['detail_metric'], 1)}"
        useful = row.get("useful_score")
        persistent = row.get("persistent_score")
        survivor_fit = _fmt_num(row.get("survivor_fit"), 1)
        stance = row.get("survivor_fit_stance") or "unknown"
        positives = list(row.get("survivor_fit_positive_tags") or [])
        negatives = list(row.get("survivor_fit_negative_tags") or [])
        tag_bits: list[str] = []
        if positives:
            tag_bits.append("+" + ", ".join(positives[:2]))
        if negatives:
            tag_bits.append("-" + ", ".join(negatives[:2]))
        fit_summary = f"{survivor_fit} `{stance}`"
        if tag_bits:
            fit_summary += " " + " / ".join(tag_bits)
        shape_state = str(row.get("shape_state") or "unknown")
        shape_score = row.get("shape_score")
        shape_summary = f"`{shape_state}`"
        if shape_score is not None:
            shape_summary += f" {_fmt_num(shape_score, 0)}"
        if bool(row.get("shape_steam_loss")):
            shape_summary += " steam_loss"
        lines.append(
            f"| {row['symbol']} | `{row['stage']}` | `{row['status']}` | `{row['source']}` | `{row['regime']}` | "
            f"{shape_summary} | {_fmt_num(useful, 1) if useful is not None else '—'} | {_fmt_num(persistent, 1) if persistent is not None else '—'} | {fit_summary} | {detail} |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a lifecycle board from meme research reports.")
    parser.add_argument("--pending", type=Path, default=PENDING_JSON)
    parser.add_argument("--research", type=Path, default=RESEARCH_JSON)
    parser.add_argument("--persistence24", type=Path, default=PERSIST24_JSON)
    parser.add_argument("--survivor-research", type=Path, default=SURVIVOR_RESEARCH_JSON)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=OUT_MD)
    args = parser.parse_args()

    previous_report = _load_json(args.out_json) if args.out_json.exists() else None
    report = build_report(
        _load_json(args.pending),
        _load_json(args.research),
        _load_json(args.persistence24),
        _load_json(args.survivor_research),
        previous_report=previous_report,
    )
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_md(args.out_md, report)
    print(
        "meme_lifecycle_monitor: "
        f"board={report['summary']['board_size']} "
        f"pending_promote={report['summary']['pending_promote_now']}"
    )


if __name__ == "__main__":
    main()
