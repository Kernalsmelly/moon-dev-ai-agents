#!/usr/bin/env python3
"""Generate a consolidated daily scorecard for meme research."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import time
from pathlib import Path
from typing import Any

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
REPORTS = BASE / "data" / "meme_reports"
DATASET_CSV = REPORTS / "meme_anchor_dataset.csv"
BASELINE_MODEL_PATH = BASE / "scripts" / "meme_anchor_baseline_model.py"
OUT_JSON = REPORTS / "meme_daily_scorecard.json"
OUT_MD = REPORTS / "meme_daily_scorecard.md"
HISTORY_JSONL = REPORTS / "meme_research_snapshot_history.jsonl"

REFRESH_COMMANDS = [
    [
        "python3",
        "scripts/meme_signal_progress_report.py",
        "--since-hours",
        "6",
        "--out-json",
        "data/meme_reports/signal_progress_report_6h.json",
        "--out-md",
        "data/meme_reports/signal_progress_report_6h.md",
    ],
    [
        "python3",
        "scripts/meme_signal_progress_report.py",
        "--since-hours",
        "24",
        "--out-json",
        "data/meme_reports/signal_progress_report_24h.json",
        "--out-md",
        "data/meme_reports/signal_progress_report_24h.md",
    ],
    [
        "python3",
        "scripts/meme_winner_persistence_report.py",
        "--since-hours",
        "24",
        "--out-json",
        "data/meme_reports/winner_persistence_report_24h.json",
        "--out-md",
        "data/meme_reports/winner_persistence_report_24h.md",
    ],
    ["python3", "scripts/meme_anchor_dataset_export.py"],
    ["python3", "scripts/meme_anchor_baseline_model.py"],
    ["python3", "scripts/meme_useful_regime_monitor.py"],
    ["python3", "scripts/meme_research_priority_monitor.py"],
    ["python3", "scripts/meme_winner_shape_report.py"],
    ["python3", "scripts/meme_pending_maturation_report.py"],
    ["python3", "scripts/meme_late_slow_persistence_monitor.py"],
    ["python3", "scripts/meme_promotion_rule_report.py"],
    ["python3", "scripts/meme_survivor_outcome_report.py"],
    ["python3", "scripts/meme_survivor_fit_validation.py"],
    ["python3", "scripts/meme_lifecycle_monitor.py"],
    ["python3", "scripts/meme_attention_shortlist.py"],
    ["python3", "scripts/meme_attention_shortlist_validation.py"],
    ["python3", "scripts/meme_decision_tracker.py"],
    ["python3", "scripts/meme_market_data_adapter.py"],
    ["python3", "scripts/meme_starter_entry_gate_research.py"],
    ["python3", "scripts/meme_operator_action_board.py"],
    ["python3", "scripts/meme_paper_trade_expectancy_report.py"],
    ["python3", "scripts/meme_promote_cohort_report.py"],
    ["python3", "scripts/meme_collection_health_report.py"],
]

INPUT_FILES = {
    "progress6": REPORTS / "signal_progress_report_6h.json",
    "progress24": REPORTS / "signal_progress_report_24h.json",
    "persist24": REPORTS / "winner_persistence_report_24h.json",
    "baseline": REPORTS / "meme_anchor_baseline_model.json",
    "useful_regime": REPORTS / "meme_useful_regime_monitor.json",
    "research": REPORTS / "research_priority_monitor.json",
    "pending": REPORTS / "pending_maturation_report.json",
    "winner_shape": REPORTS / "meme_winner_shape_report.json",
    "late_slow": REPORTS / "late_slow_persistence_monitor.json",
    "promotion": REPORTS / "meme_promotion_rule_report.json",
    "survivor": REPORTS / "meme_survivor_outcome_report.json",
    "survivor_fit": REPORTS / "meme_survivor_fit_validation.json",
    "lifecycle": REPORTS / "meme_lifecycle_monitor.json",
    "attention": REPORTS / "meme_attention_shortlist.json",
    "attention_validation": REPORTS / "meme_attention_shortlist_validation.json",
    "decision_tracker": REPORTS / "meme_decision_tracker.json",
    "market_data": REPORTS / "meme_market_data_adapter.json",
    "starter_gates": REPORTS / "meme_starter_entry_gate_research.json",
    "operator_board": REPORTS / "meme_operator_action_board.json",
    "paper_expectancy": REPORTS / "meme_paper_trade_expectancy_report.json",
    "promote_cohort": REPORTS / "meme_promote_cohort_report.json",
    "health": REPORTS / "meme_collection_health_report.json",
}

PROCESS_CHECKS = {
    "dex_mover": "dex_mover_signal_listener.py",
    "pump_ws": "pump_ws_signal_listener.py",
    "raydium_ws": "raydium_pool_ws_listener.py",
    "wallet_outlier": "wallet_outlier_signal_listener.py",
    "outcome_recorder": "signal_outcome_recorder.py",
}


def _run_refresh() -> None:
    for cmd in REFRESH_COMMANDS:
        subprocess.run(cmd, cwd=BASE, check=True, capture_output=True, text=True)


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _load_last_jsonl(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    last_line = None
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                last_line = line
    return json.loads(last_line) if last_line else None


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100.0:.1f}%"


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _fmt_num(value: float | None, digits: int = 0) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def _fmt_seconds(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value < 60:
        return f"{value:.0f}s"
    if value < 3600:
        return f"{value/60.0:.1f}m"
    return f"{value/3600.0:.2f}h"


def _fmt_delta(value: float | int | None, *, pct: bool = False, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    if pct:
        return f"{value * 100.0:+.{digits}f}pp"
    return f"{value:+.{digits}f}"


def _confidence_level(score: float) -> str:
    if score >= 0.80:
        return "high"
    if score >= 0.55:
        return "medium"
    return "low"


def _blend_metric(current: float, reference: float, confidence_score: float) -> float:
    weight_current = max(0.0, min(1.0, confidence_score))
    weight_reference = 1.0 - weight_current
    return (weight_current * current) + (weight_reference * reference)


def _reference_benchmark(*, train_hours: float = 168.0, validate_hours: float = 72.0) -> dict[str, Any]:
    baseline = _load_module("meme_anchor_baseline_model_module_for_scorecard", BASELINE_MODEL_PATH)
    rows = baseline.load_rows(DATASET_CSV)
    now = time.time()
    train_since_ts = now - ((float(train_hours) + float(validate_hours)) * 3600.0)
    validate_cutoff_ts = now - (float(validate_hours) * 3600.0)
    train_rows = [row for row in rows if train_since_ts <= float(row["signal_ts"]) < validate_cutoff_ts]
    validation_rows = [row for row in rows if float(row["signal_ts"]) >= validate_cutoff_ts]
    useful_model = baseline.fit_model(train_rows, target_field="label_useful")
    persistent_model = baseline.fit_model(train_rows, target_field="label_persistent")
    useful_eval = baseline.evaluate_model(useful_model, validation_rows, target_field="label_useful")
    persistent_eval = baseline.evaluate_model(persistent_model, validation_rows, target_field="label_persistent")
    return {
        "train_hours": float(train_hours),
        "validate_hours": float(validate_hours),
        "train_rows": len(train_rows),
        "validation_rows": len(validation_rows),
        "useful_baseline": useful_eval["baseline_precision"],
        "useful_top10": useful_eval["topk"]["10"]["precision"],
        "useful_threshold_75": useful_eval["thresholds"]["75"]["precision"],
        "useful_threshold_75_n": useful_eval["thresholds"]["75"]["n"],
        "persistent_baseline": persistent_eval["baseline_precision"],
        "persistent_top10": persistent_eval["topk"]["10"]["precision"],
    }


def _window_confidence(label: str, health_window: dict[str, Any], signal_summary: dict[str, Any]) -> dict[str, Any]:
    hours = float(str(label).replace("h", "") or 0.0)
    score = 1.0
    reasons: list[str] = []

    status = str(health_window.get("status") or "unknown")
    largest_gap_s = float(health_window.get("largest_gap_s") or 0.0)
    last_signal_age_s = float(health_window.get("last_signal_age_s") or 0.0)
    signals = int(signal_summary.get("signals") or 0)
    unique_mints = int(signal_summary.get("unique_mints") or 0)

    if status == "degraded":
        score -= 0.30
        reasons.append("collection gaps detected")
    elif status == "caution":
        score -= 0.15
        reasons.append("minor collection gaps")
    elif status == "down":
        score -= 0.60
        reasons.append("collection appears down")

    if hours > 0:
        gap_ratio = min(1.0, largest_gap_s / (hours * 3600.0))
        if gap_ratio > 0.0:
            penalty = min(0.35, gap_ratio * 0.70)
            score -= penalty
            if largest_gap_s >= 1800:
                reasons.append(f"largest gap {_fmt_seconds(largest_gap_s)}")

    if last_signal_age_s >= 1800:
        score -= 0.15
        reasons.append("stale last signal")
    elif last_signal_age_s >= 600:
        score -= 0.05

    if hours > 0:
        signals_per_hour = signals / hours
        if signals_per_hour < 1.0:
            score -= 0.20
            reasons.append("very low signal density")
        elif signals_per_hour < 3.0:
            score -= 0.10
        if unique_mints <= max(2, int(hours // 2)):
            score -= 0.10
            reasons.append("low unique-mint coverage")

    score = max(0.0, min(1.0, score))
    return {
        "score": score,
        "level": _confidence_level(score),
        "reasons": reasons,
    }


def _operating_mode(*, report: dict[str, Any]) -> dict[str, Any]:
    confidence24 = report["confidence"]["24h"]
    useful_now = report["summary"]["useful_model"]
    effective = report["effective_model"]
    pending = report.get("pending_rows") or []
    signals24 = int(report["summary"]["signals_24h"].get("signals") or 0)
    winners24 = int(report["summary"]["signals_24h"].get("verified_winners") or 0)

    strong_pending = [row for row in pending if str(row.get("progress_hint")) in {"holding_strong", "still_alive"}]
    if confidence24["score"] < 0.55:
        if strong_pending:
            return {
                "mode": "survivor_tracking",
                "reason": "Recent window is low-confidence, but there are live survivors worth monitoring.",
            }
        return {
            "mode": "observe_only",
            "reason": "Recent window is low-confidence and there is no strong live survivor cohort.",
        }
    if winners24 >= 8 and effective["useful_top10_precision"] >= (effective["useful_baseline"] + 0.05):
        return {
            "mode": "useful_winner_active",
            "reason": "Winner flow is active and the effective useful model remains meaningfully above baseline.",
        }
    if strong_pending:
        return {
            "mode": "survivor_tracking",
            "reason": "Winner flow is mixed, but there are pending names still holding up after the first burst.",
        }
    if signals24 < 40:
        return {
            "mode": "observe_only",
            "reason": "Coverage and opportunity density are both light, so this is not a strong action day.",
        }
    return {
        "mode": "research_only",
        "reason": "Keep collecting and ranking, but there is not enough signal quality yet for a stronger stance.",
    }


def _proc_status(pattern: str) -> bool:
    res = subprocess.run(
        ["pgrep", "-fl", pattern],
        cwd=BASE,
        capture_output=True,
        text=True,
        check=False,
    )
    return bool((res.stdout or "").strip())


def _collector_status() -> dict[str, bool]:
    return {name: _proc_status(pattern) for name, pattern in PROCESS_CHECKS.items()}


def _top_winners(progress_report: dict[str, Any], top: int = 3) -> list[dict[str, Any]]:
    return list(progress_report.get("top_winners") or [])[:top]


def _top_live_research(research_report: dict[str, Any], top: int = 5) -> list[dict[str, Any]]:
    return list(research_report.get("live") or [])[:top]


def _key_takeaways(
    data: dict[str, Any],
    *,
    confidence: dict[str, Any],
    reference_benchmark: dict[str, Any],
    effective_model: dict[str, Any],
) -> list[str]:
    out: list[str] = []
    useful_base = data["baseline"]["validation"]["useful"]["baseline_precision"]
    useful_top10 = data["baseline"]["validation"]["useful"]["topk"]["10"]["precision"]
    persist_base = data["baseline"]["validation"]["persistent"]["baseline_precision"]
    persist_counts = data["persist24"]["summary"]["class_counts"]
    pending = data["pending"]["pending_rows"]
    pending_summary = data["pending"]["summary"]
    promotion = data["promotion"]["summary"]
    survivor = data["survivor"]["summary"]
    survivor_fit = data["survivor_fit"]["summary"]
    attention = data["attention"]["summary"]
    attention_validation = data["attention_validation"]["summary"]
    promote_cohort = data["promote_cohort"]
    starter_gates = data["starter_gates"]
    paper_expectancy = data["paper_expectancy"]
    winner_shape = data["winner_shape"]["summary"]
    health24 = data["health"]["windows"]["24h"]
    confidence24 = confidence["24h"]

    if confidence24.get("level") == "low":
        out.append(
            "Recent 24h model read is noisy, so use the blended view instead: "
            f"top-10 useful precision `{_fmt_pct(effective_model.get('useful_top10_precision'))}` "
            f"vs baseline `{_fmt_pct(effective_model.get('useful_baseline'))}`."
        )
    else:
        if useful_top10 > useful_base:
            out.append(
                f"Useful-winner model still has lift: top-10 useful precision is {_fmt_pct(useful_top10)} vs baseline {_fmt_pct(useful_base)}."
            )
        else:
            out.append(
                f"Useful-winner model is flat-to-weaker on the latest clean window: top-10 useful precision is {_fmt_pct(useful_top10)} vs baseline {_fmt_pct(useful_base)}."
            )
    out.append(
        f"Persistence remains scarce: current 24h window has {persist_counts.get('short_lived_spike', 0)} short-lived spikes and {persist_counts.get('pending_6h', 0)} pending names."
    )
    best_30m_shape = winner_shape.get("best_30m_shape") or {}
    if best_30m_shape:
        out.append(
            "Winner shape is starting to matter: "
            f"`{best_30m_shape.get('shape_state')}` at 30m has "
            f"`{_fmt_pct(best_30m_shape.get('survivor_precision'))}` survivor precision "
            f"on `{int(best_30m_shape.get('n') or 0)}` matured useful winners."
        )
    if str(health24.get("status")) != "healthy":
        out.append(
            f"Collection health is `{health24.get('status')}` in the last 24h, so this window may be partially contaminated by gaps."
        )
    out.append(
        f"Current 24h research confidence is `{confidence24.get('level')}` ({_fmt_pct(confidence24.get('score'))})."
    )
    if confidence24.get("level") == "low":
        out.append(
            "Use the wider reference benchmark as the steadier read: "
            f"top-10 useful precision `{_fmt_pct(reference_benchmark.get('useful_top10'))}` "
            f"vs baseline `{_fmt_pct(reference_benchmark.get('useful_baseline'))}` over the last "
            f"`{int(reference_benchmark.get('validate_hours') or 0)}h` validation window."
        )
    if pending:
        strong_pending = [
            row
            for row in pending
            if str(row.get("promotion_decision") or "") not in {"cut_bias"}
            and str(row.get("progress_hint") or "") in {"holding_strong", "still_alive", "fragile_but_green"}
        ]
        if strong_pending:
            strongest = strong_pending[0]
            out.append(
                f"Most important live survivor right now is {strongest.get('symbol') or 'n/a'} with status `{strongest.get('progress_hint')}`."
            )
        else:
            out.append("There is no strong live survivor cohort right now; current pending names are mostly cut-bias.")
    if (pending_summary.get("decision_counts") or {}).get("promote_now", 0):
        out.append(
            f"There are `{pending_summary['decision_counts']['promote_now']}` pending names currently in the promotion-ready bucket."
        )
    out.append(
        "Survivor-grade outcomes are meaningfully more common than strict runners: "
        f"`{_fmt_pct(survivor.get('baseline_survivor_precision'))}` survivor-grade vs "
        f"`{_fmt_pct(survivor.get('baseline_persistent_precision'))}` strict persistent."
    )
    best_rule = promotion.get("best_promotion_rule") or {}
    cut_rule = promotion.get("strongest_cut_signal") or {}
    best_survivor_rule = survivor.get("best_survivor_rule") or {}
    if best_rule:
        out.append(
            "Historical promotion backtest favors "
            f"`{best_rule.get('label', 'n/a')}` at `{_fmt_pct(best_rule.get('persistent_precision'))}` "
            f"persistent precision."
        )
    if best_survivor_rule:
        out.append(
            "For survivor-grade outcomes, the best checkpoint is "
            f"`{best_survivor_rule.get('label', 'n/a')}` at `{_fmt_pct(best_survivor_rule.get('survivor_precision'))}`."
        )
    fit_thresholds = list(data["survivor_fit"].get("by_threshold") or [])
    fit_65 = next((row for row in fit_thresholds if int(row.get("threshold") or 0) == 65), None)
    if fit_65 and int(fit_65.get("n") or 0) > 0:
        out.append(
            "Inside useful winners, survivor-fit is most useful as a secondary filter: "
            f"`>=65` gives `{_fmt_pct(fit_65.get('survivor_precision'))}` survivor precision "
            f"vs `{_fmt_pct(survivor_fit.get('baseline_survivor_precision'))}` baseline."
        )
    out.append(
        "Current shortlist shape: "
        f"`{attention.get('focus_now_count', 0)}` focus-now, "
        f"`{attention.get('watch_closely_count', 0)}` watch-closely, "
        f"`{attention.get('elevate_count', 0)}` elevate."
    )
    out.append(
        "Historically, the shortlist itself is "
        f"`{_fmt_pct(attention_validation.get('top10_survivor_precision'))}` survivor-precise in its top 10 "
        f"vs `{_fmt_pct(attention_validation.get('baseline_survivor_precision'))}` baseline."
    )
    promote_grades = {row.get("decision_grade"): row for row in promote_cohort.get("resolved_by_grade") or []}
    strong = promote_grades.get("promote_strong")
    probe = promote_grades.get("promote_probe")
    if strong:
        out.append(
            "Resolved promote cohort says the bucket needs grading: "
            f"`promote_strong` is `{_fmt_pct(strong.get('survivor_precision'))}` survivor-grade "
            f"on `{int(strong.get('n') or 0)}` names."
        )
    if probe:
        out.append(
            f"`promote_probe` is only `{_fmt_pct(probe.get('survivor_precision'))}` survivor-grade "
            f"on `{int(probe.get('n') or 0)}` names, so promotes should not all be treated equally."
        )
    starter_rows = {row.get("name"): row for row in starter_gates.get("gate_rows") or []}
    starter_strong = starter_rows.get("starter_strong_breakout")
    starter_probe = starter_rows.get("starter_probe_core")
    if starter_strong and starter_probe:
        out.append(
            "Starter-entry research tightened the earlier paper-trade profile: "
            f"`starter_strong_breakout` is `{_fmt_pct(starter_strong.get('useful_precision'))}` useful / "
            f"`{_fmt_pct(starter_strong.get('survivor_precision'))}` survivor on `{int(starter_strong.get('n') or 0)}` rows, "
            f"while `starter_probe_core` is `{_fmt_pct(starter_probe.get('useful_precision'))}` useful on `{int(starter_probe.get('n') or 0)}` rows."
        )
    expectancy = ((paper_expectancy.get("summary") or {}).get("combined") or {})
    if int((paper_expectancy.get("summary") or {}).get("closed_total") or 0) > 0:
        out.append(
            "Paper-trade expectancy is still negative, which means risk is the next bottleneck: "
            f"combined expectancy is `{_fmt_pct(expectancy.get('expectancy'))}` with "
            f"`{_fmt_pct(expectancy.get('winrate'))}` winrate and "
            f"`{_fmt_pct(expectancy.get('avg_loss'))}` average loss."
        )
    if cut_rule:
        out.append(
            "Historical protection backtest favors cutting on "
            f"`{cut_rule.get('label', 'n/a')}` because persistence there is only `{_fmt_pct(cut_rule.get('persistent_precision'))}`."
        )
    if persist_base <= 0.02:
        out.append("Persistence modeling is still sample-starved, so pending-maturation tracking is more trustworthy than the raw persistence score.")
    return out


def _build_changes(report: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    if not previous:
        return {"available": False, "items": []}

    items: list[str] = []
    cur6 = report["summary"]["signals_6h"]
    prev6 = previous.get("summary", {}).get("signals_6h", {})
    cur24 = report["summary"]["signals_24h"]
    prev24 = previous.get("summary", {}).get("signals_24h", {})
    cur_useful = report["summary"]["useful_model"]
    prev_useful = previous.get("summary", {}).get("useful_model", {})

    items.append(
        "6h flow: "
        f"signals {_fmt_delta((cur6.get('signals') or 0) - (prev6.get('signals') or 0), digits=0)}, "
        f"winners {_fmt_delta((cur6.get('verified_winners') or 0) - (prev6.get('verified_winners') or 0), digits=0)}, "
        f"winner rate {_fmt_delta((cur6.get('verified_winner_rate') or 0.0) - (prev6.get('verified_winner_rate') or 0.0), pct=True)}."
    )
    items.append(
        "24h flow: "
        f"signals {_fmt_delta((cur24.get('signals') or 0) - (prev24.get('signals') or 0), digits=0)}, "
        f"winners {_fmt_delta((cur24.get('verified_winners') or 0) - (prev24.get('verified_winners') or 0), digits=0)}, "
        f"winner rate {_fmt_delta((cur24.get('verified_winner_rate') or 0.0) - (prev24.get('verified_winner_rate') or 0.0), pct=True)}."
    )
    items.append(
        "Useful model: "
        f"baseline {_fmt_delta((cur_useful.get('baseline_precision') or 0.0) - (prev_useful.get('baseline_precision') or 0.0), pct=True)}, "
        f"top-10 {_fmt_delta((cur_useful.get('top10_precision') or 0.0) - (prev_useful.get('top10_precision') or 0.0), pct=True)}, "
        f">=75 {_fmt_delta((cur_useful.get('threshold_75_precision') or 0.0) - (prev_useful.get('threshold_75_precision') or 0.0), pct=True)}."
    )
    cur_conf24 = report.get("confidence", {}).get("24h", {})
    prev_conf24 = previous.get("confidence", {}).get("24h", {})
    if cur_conf24 and prev_conf24:
        items.append(
            "24h confidence: "
            f"{_fmt_delta((cur_conf24.get('score') or 0.0) - (prev_conf24.get('score') or 0.0), pct=True)} "
            f"({prev_conf24.get('level', 'n/a')} -> {cur_conf24.get('level', 'n/a')})."
        )

    cur_pending = report.get("pending_rows") or []
    prev_pending = previous.get("pending_rows") or []
    cur_pending_map = {str(row.get("mint") or ""): row for row in cur_pending}
    prev_pending_map = {str(row.get("mint") or ""): row for row in prev_pending}
    added = [row.get("symbol") or "n/a" for mint, row in cur_pending_map.items() if mint and mint not in prev_pending_map]
    removed = [row.get("symbol") or "n/a" for mint, row in prev_pending_map.items() if mint and mint not in cur_pending_map]
    if added:
        items.append(f"Pending cohort added: {', '.join(added[:4])}.")
    if removed:
        items.append(f"Pending cohort matured or dropped: {', '.join(removed[:4])}.")
    status_changes = []
    for mint, row in cur_pending_map.items():
        prev = prev_pending_map.get(mint)
        if not prev:
            continue
        if str(prev.get('progress_hint')) != str(row.get('progress_hint')):
            status_changes.append(
                f"{row.get('symbol') or 'n/a'} `{prev.get('progress_hint')}` -> `{row.get('progress_hint')}`"
            )
    if status_changes:
        items.append("Pending status changes: " + "; ".join(status_changes[:4]) + ".")

    cur_live = report.get("top_live_research") or []
    prev_live = previous.get("top_live_research") or []
    if cur_live and prev_live:
        cur_top = cur_live[0]
        prev_top = prev_live[0]
        if str(cur_top.get("mint")) != str(prev_top.get("mint")):
            items.append(
                f"Top research name rotated from {prev_top.get('symbol') or 'n/a'} to {cur_top.get('symbol') or 'n/a'}."
            )
        elif float(cur_top.get("composite_score") or 0.0) != float(prev_top.get("composite_score") or 0.0):
            delta = float(cur_top.get("composite_score") or 0.0) - float(prev_top.get("composite_score") or 0.0)
            items.append(
                f"Top research name {cur_top.get('symbol') or 'n/a'} moved {_fmt_delta(delta, digits=1)} in composite score."
            )

    return {"available": True, "items": items}


def build_report(data: dict[str, Any], *, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    confidence = {
        "6h": _window_confidence("6h", data["health"]["windows"]["6h"], data["progress6"]["summary"]),
        "24h": _window_confidence("24h", data["health"]["windows"]["24h"], data["progress24"]["summary"]),
    }
    confidence["overall"] = {
        "score": ((0.4 * confidence["6h"]["score"]) + (0.6 * confidence["24h"]["score"])),
    }
    confidence["overall"]["level"] = _confidence_level(float(confidence["overall"]["score"]))
    reference_benchmark = _reference_benchmark()
    effective_model = {
        "useful_baseline": _blend_metric(
            float(data["baseline"]["validation"]["useful"]["baseline_precision"]),
            float(reference_benchmark["useful_baseline"]),
            float(confidence["24h"]["score"]),
        ),
        "useful_top10_precision": _blend_metric(
            float(data["baseline"]["validation"]["useful"]["topk"]["10"]["precision"]),
            float(reference_benchmark["useful_top10"]),
            float(confidence["24h"]["score"]),
        ),
        "useful_threshold_75_precision": _blend_metric(
            float(data["baseline"]["validation"]["useful"]["thresholds"]["75"]["precision"]),
            float(reference_benchmark["useful_threshold_75"]),
            float(confidence["24h"]["score"]),
        ),
        "persistent_baseline": _blend_metric(
            float(data["baseline"]["validation"]["persistent"]["baseline_precision"]),
            float(reference_benchmark["persistent_baseline"]),
            float(confidence["24h"]["score"]),
        ),
    }
    report = {
        "generated_at": time.time(),
        "collector_status": _collector_status(),
        "collection_health": data["health"],
        "confidence": confidence,
        "reference_benchmark": reference_benchmark,
        "effective_model": effective_model,
        "summary": {
            "signals_6h": data["progress6"]["summary"],
            "signals_24h": data["progress24"]["summary"],
            "persistence_24h": data["persist24"]["summary"],
            "useful_model": {
                "baseline_precision": data["baseline"]["validation"]["useful"]["baseline_precision"],
                "top10_precision": data["baseline"]["validation"]["useful"]["topk"]["10"]["precision"],
                "threshold_75_precision": data["baseline"]["validation"]["useful"]["thresholds"]["75"]["precision"],
            },
            "persistence_model": {
                "baseline_precision": data["baseline"]["validation"]["persistent"]["baseline_precision"],
            },
        },
        "family_validation": data["useful_regime"]["family_validation"],
        "top_winners_6h": _top_winners(data["progress6"]),
        "top_winners_24h": _top_winners(data["progress24"]),
        "top_live_research": _top_live_research(data["research"]),
        "pending_summary": data["pending"]["summary"],
        "pending_rows": data["pending"]["pending_rows"],
        "late_slow_subset": data["late_slow"]["subset"],
        "promotion_backtest": data["promotion"],
        "survivor_backtest": data["survivor"],
        "winner_shape": data["winner_shape"],
        "survivor_fit_validation": data["survivor_fit"],
        "lifecycle_board": data["lifecycle"],
        "attention_shortlist": data["attention"],
        "attention_validation": data["attention_validation"],
        "decision_tracker": data["decision_tracker"],
        "market_data": data["market_data"],
        "starter_gates": data["starter_gates"],
        "operator_board": data["operator_board"],
        "paper_expectancy": data["paper_expectancy"],
        "promote_cohort": data["promote_cohort"],
        "takeaways": _key_takeaways(
            data,
            confidence=confidence,
            reference_benchmark=reference_benchmark,
            effective_model=effective_model,
        ),
    }
    report["operating_mode"] = _operating_mode(report=report)
    report["changes_since_last"] = _build_changes(report, previous)
    return report


def _snapshot_signature(snapshot: dict[str, Any]) -> str:
    return json.dumps(snapshot, sort_keys=True, separators=(",", ":"))


def append_history(path: Path, report: dict[str, Any]) -> bool:
    attention_top = []
    for row in list((report.get("attention_shortlist") or {}).get("top") or [])[:5]:
        attention_top.append(
            {
                "symbol": row.get("symbol") or "n/a",
                "tier": row.get("attention_tier") or "unknown",
                "score": round(float(row.get("attention_score") or 0.0), 1),
                "stage": row.get("stage") or "unknown",
            }
        )

    snapshot = {
        "ts": report["generated_at"],
        "date": time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(report["generated_at"])),
        "mode": report["operating_mode"]["mode"],
        "confidence_24h": round(float(report["confidence"]["24h"]["score"]), 4),
        "signals_24h": int(report["summary"]["signals_24h"].get("signals") or 0),
        "winners_24h": int(report["summary"]["signals_24h"].get("verified_winners") or 0),
        "pending_count": int(report["pending_summary"].get("pending_count") or 0),
        "lifecycle_summary": report["lifecycle_board"]["summary"],
        "attention_summary": (report.get("attention_shortlist") or {}).get("summary") or {},
        "decision_summary": (report.get("decision_tracker") or {}).get("summary") or {},
        "attention_top": attention_top,
    }

    signature = _snapshot_signature(snapshot)
    last = _load_last_jsonl(path)
    if last and _snapshot_signature({k: v for k, v in last.items() if k != "_signature"}) == signature:
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({**snapshot, "_signature": signature}) + "\n")
    return True


def write_md(path: Path, report: dict[str, Any]) -> None:
    s6 = report["summary"]["signals_6h"]
    s24 = report["summary"]["signals_24h"]
    p24 = report["summary"]["persistence_24h"]
    useful = report["summary"]["useful_model"]
    collectors = report["collector_status"]
    health = report["collection_health"]["windows"]
    confidence = report["confidence"]
    reference = report["reference_benchmark"]
    effective = report["effective_model"]
    mode = report["operating_mode"]
    promotion = report["promotion_backtest"]
    promotion_summary = promotion["summary"]
    survivor = report["survivor_backtest"]
    survivor_summary = survivor["summary"]
    winner_shape = report["winner_shape"]
    winner_shape_summary = winner_shape["summary"]
    survivor_fit = report["survivor_fit_validation"]
    survivor_fit_summary = survivor_fit["summary"]
    lifecycle = report["lifecycle_board"]["summary"]
    attention = report["attention_shortlist"]
    attention_summary = attention["summary"]
    attention_validation = report["attention_validation"]
    attention_validation_summary = attention_validation["summary"]
    decision_tracker = report["decision_tracker"]
    decision_summary = decision_tracker["summary"]
    market_data = report["market_data"]
    market_summary = market_data["summary"]
    starter_gates = report["starter_gates"]
    starter_summary = starter_gates["summary"]
    operator_board = report["operator_board"]
    operator_summary = operator_board["summary"]
    paper_expectancy = report["paper_expectancy"]
    paper_summary = paper_expectancy["summary"]
    promote_cohort = report["promote_cohort"]
    promote_summary = promote_cohort["summary"]
    lifecycle_rows = list(report["lifecycle_board"].get("board") or [])
    lifecycle_by_mint = {str(row.get("mint") or ""): row for row in lifecycle_rows if row.get("mint")}
    lifecycle_transitions = report["lifecycle_board"].get("transitions") or {}
    top_survivor_fit = sorted(
        [row for row in lifecycle_rows if str(row.get("stage") or "") == "emerging_watchlist"],
        key=lambda row: float(row.get("survivor_fit") or 0.0),
        reverse=True,
    )[:3]

    lines = [
        "# Meme Daily Scorecard",
        "",
        f"- Generated: `{time.strftime('%Y-%m-%d %H:%M:%S %Z', time.localtime(report['generated_at']))}`",
        "",
        "## Collector Status",
        "",
    ]
    for name, ok in collectors.items():
        lines.append(f"- `{name}`: `{'running' if ok else 'stopped'}`")

    changes = report.get("changes_since_last") or {}
    if changes.get("available"):
        lines.extend(["", "## What Changed", ""])
        for item in changes.get("items") or []:
            lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## Collection Health",
            "",
            f"- `6h`: `{health['6h']['status']}`, `{health['6h']['signals']}` signals, largest gap `{_fmt_seconds(health['6h']['largest_gap_s'])}`",
            f"- `24h`: `{health['24h']['status']}`, `{health['24h']['signals']}` signals, largest gap `{_fmt_seconds(health['24h']['largest_gap_s'])}`",
            f"- `48h`: `{health['48h']['status']}`, `{health['48h']['signals']}` signals, largest gap `{_fmt_seconds(health['48h']['largest_gap_s'])}`",
            "",
            "## Confidence",
            "",
            f"- `6h`: `{confidence['6h']['level']}` ({_fmt_pct(confidence['6h']['score'])})",
            f"- `24h`: `{confidence['24h']['level']}` ({_fmt_pct(confidence['24h']['score'])})",
            f"- `overall`: `{confidence['overall']['level']}` ({_fmt_pct(confidence['overall']['score'])})",
            "",
            "## Operating Mode",
            "",
            f"- Mode: `{mode['mode']}`",
            f"- Reason: {mode['reason']}",
            "",
            "## Signal Flow",
            "",
            f"- `6h`: `{s6['signals']}` signals, `{s6['unique_mints']}` unique mints, `{s6['verified_winners']}` verified winners, `{_fmt_pct(s6['verified_winner_rate'])}` winner rate",
            f"- `24h`: `{s24['signals']}` signals, `{s24['unique_mints']}` unique mints, `{s24['verified_winners']}` verified winners, `{_fmt_pct(s24['verified_winner_rate'])}` winner rate",
            "",
            "## Model Snapshot",
            "",
            f"- Useful baseline: `{_fmt_pct(useful['baseline_precision'])}`",
            f"- Useful top-10 precision: `{_fmt_pct(useful['top10_precision'])}`",
            f"- Useful threshold `>=75` precision: `{_fmt_pct(useful['threshold_75_precision'])}`",
            f"- Persistence baseline: `{_fmt_pct(report['summary']['persistence_model']['baseline_precision'])}`",
            "",
            "## Reference Benchmark",
            "",
            f"- Window: train `{int(reference['train_hours'])}h`, validate `{int(reference['validate_hours'])}h`",
            f"- Useful baseline: `{_fmt_pct(reference['useful_baseline'])}`",
            f"- Useful top-10 precision: `{_fmt_pct(reference['useful_top10'])}`",
            f"- Useful threshold `>=75` precision: `{_fmt_pct(reference['useful_threshold_75'])}` on `{int(reference['useful_threshold_75_n'])}` rows",
            f"- Persistence baseline: `{_fmt_pct(reference['persistent_baseline'])}`",
            "",
            "## Effective Model View",
            "",
            f"- Useful baseline: `{_fmt_pct(effective['useful_baseline'])}`",
            f"- Useful top-10 precision: `{_fmt_pct(effective['useful_top10_precision'])}`",
            f"- Useful threshold `>=75` precision: `{_fmt_pct(effective['useful_threshold_75_precision'])}`",
            f"- Persistence baseline: `{_fmt_pct(effective['persistent_baseline'])}`",
            "",
            "## Promotion Backtest",
            "",
            f"- Matured earliest-useful winners: `{promotion_summary['matured_earliest_useful_winners']}`",
            f"- Baseline persistent precision: `{_fmt_pct(promotion_summary['baseline_persistent_precision'])}`",
            f"- Best promotion rule: `{promotion_summary['best_promotion_rule']['label']}` -> `{_fmt_pct(promotion_summary['best_promotion_rule']['persistent_precision'])}`",
            f"- Strongest cut signal: `{promotion_summary['strongest_cut_signal']['label']}` -> `{_fmt_pct(promotion_summary['strongest_cut_signal']['persistent_precision'])}`",
            "",
            "## Survivor Outcome Backtest",
            "",
            f"- Baseline survivor-grade precision: `{_fmt_pct(survivor_summary['baseline_survivor_precision'])}`",
            f"- Baseline strict-persistent precision: `{_fmt_pct(survivor_summary['baseline_persistent_precision'])}`",
            f"- Partial persistence count: `{survivor_summary['partial_persistence_count']}`",
            f"- Best survivor rule: `{survivor_summary['best_survivor_rule']['label']}` -> `{_fmt_pct(survivor_summary['best_survivor_rule']['survivor_precision'])}`",
            "",
            "## Winner Shape Research",
            "",
            f"- Matured useful winners studied: `{winner_shape_summary['matured_useful_winners']}`",
            f"- Baseline survivor precision: `{_fmt_pct(winner_shape_summary['baseline_survivor_precision'])}`",
            f"- Baseline persistent precision: `{_fmt_pct(winner_shape_summary['baseline_persistent_precision'])}`",
            "## Survivor Fit Validation",
            "",
            f"- Useful rows scored: `{survivor_fit_summary['useful_rows']}`",
            f"- Baseline survivor precision: `{_fmt_pct(survivor_fit_summary['baseline_survivor_precision'])}`",
        ]
    )
    for key in ("best_30m_shape", "best_60m_shape"):
        row = winner_shape_summary.get(key) or {}
        if row:
            lines.append(
                f"- {key.replace('_', ' ').title()}: `{row.get('shape_state')}` -> survivor `{_fmt_pct(row.get('survivor_precision'))}` "
                f"and persistent `{_fmt_pct(row.get('persistent_precision'))}` on `{int(row.get('n') or 0)}` rows"
            )
    for row in survivor_fit.get("by_threshold") or []:
        if int(row.get("threshold") or 0) in {55, 65, 75}:
            lines.append(
                f"- Survivor-fit `>= {int(row['threshold'])}`: `{_fmt_pct(row['survivor_precision'])}` on `{int(row['n'])}` rows"
            )

    lines.extend(
        [
            "",
            "## Lifecycle Board",
            "",
            f"- Emerging watchlist: `{lifecycle['emerging_watchlist']}`",
            f"- Pending promote-now: `{lifecycle['pending_promote_now']}`",
            f"- Pending watch: `{lifecycle['pending_watch']}`",
            f"- Pending cut-bias: `{lifecycle['pending_cut_bias']}`",
            f"- Matured survivor: `{lifecycle['matured_survivor']}`",
            f"- Matured failed: `{lifecycle['matured_failed']}`",
            "",
            "## Lifecycle Transitions",
            "",
            f"- Promotions: `{(lifecycle_transitions.get('counts') or {}).get('promotion', 0)}`",
            f"- Deteriorations: `{(lifecycle_transitions.get('counts') or {}).get('deterioration', 0)}`",
            f"- New entries: `{(lifecycle_transitions.get('counts') or {}).get('new_entry', 0)}`",
        "",
        "## Persistence Snapshot",
            "",
            f"- Earliest useful winners in `24h`: `{p24['earliest_useful_winners']}`",
        ]
    )
    for klass, count in sorted((p24.get("class_counts") or {}).items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"- `{klass}`: `{count}`")
    transition_items = list(lifecycle_transitions.get("items") or [])
    if transition_items:
        lines.extend(["", "Recent lifecycle moves:"])
        for row in transition_items[:5]:
            lines.append(f"- `{row['symbol']}`: `{row['label']}`")

    lines.extend(
        [
            "",
            "## Family Validation",
            "",
            "| Family | Useful Baseline | Persistent Baseline | Top10 Useful |",
            "|---|---:|---:|---:|",
        ]
    )
    for family, row in report["family_validation"].items():
        lines.append(
            f"| `{family}` | {_fmt_pct(row['baseline_useful_precision'])} | {_fmt_pct(row['baseline_persistent_precision'])} | {_fmt_pct(row['top10_precision'])} |"
        )

    lines.extend(
        [
            "",
            "## Pending Maturation",
            "",
            f"- Promotion-ready now: `{report['pending_summary'].get('decision_counts', {}).get('promote_now', 0)}`",
            f"- Cut-bias now: `{report['pending_summary'].get('decision_counts', {}).get('cut_bias', 0)}`",
            (
                "- Strongest emerging survivor-fit: `"
                + "`, `".join(f"{row['symbol']} ({_fmt_num(row.get('survivor_fit'), 1)})" for row in top_survivor_fit)
                + "`"
            ) if top_survivor_fit else "- Strongest emerging survivor-fit: `n/a`",
            "",
            "| Symbol | Hint | Decision | Hist Persist | Useful | Persistent | Survivor Fit / Shape | Regime | Age Now (h) | ETA 6h (h) | Latest Ret | Retention |",
            "|---|---|---|---:|---:|---:|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in report["pending_rows"]:
        life_row = lifecycle_by_mint.get(str(row.get("mint") or ""), {})
        survivor_fit = life_row.get("survivor_fit")
        survivor_stance = life_row.get("survivor_fit_stance") or "n/a"
        lines.append(
            f"| {row['symbol']} | `{row['progress_hint']}` | `{row['promotion_decision']}` | {_fmt_pct(row.get('historical_persistence_precision'))} | "
            f"{float(row['useful_score']):.1f} | {float(row['persistent_score']):.1f} | "
            f"{_fmt_num(survivor_fit, 1) if survivor_fit is not None else '—'} `{survivor_stance}` / `{row.get('shape_state') or 'unknown'}` | "
            f"`{row['persistence_regime0']}` | {_fmt_num(row['age_hours'], 2)} | {_fmt_num(row['eta_6h_hours'], 2)} | "
            f"{_fmt_pct(row['latest_ret'])} | {_fmt_pct(row['latest_retention'])} |"
        )

    lines.extend(
        [
            "",
            "## Attention Shortlist",
            "",
            f"- Focus now: `{attention_summary['focus_now_count']}`",
            f"- Watch closely: `{attention_summary['watch_closely_count']}`",
            f"- Elevate: `{attention_summary['elevate_count']}`",
            f"- Monitor: `{attention_summary['monitor_count']}`",
            "",
            "| Symbol | Tier | Stage | Attention | Useful | Persistent | Survivor Fit | Regime | Reasons |",
            "|---|---|---|---:|---:|---:|---:|---|---|",
        ]
    )
    for row in attention.get("top") or []:
        lines.append(
            f"| {row['symbol']} | `{row['attention_tier']}` | `{row['stage']}` | {_fmt_num(row['attention_score'], 1)} | "
            f"{_fmt_num(row.get('useful_score'), 1)} | {_fmt_num(row.get('persistent_score'), 1)} | "
            f"{_fmt_num(row.get('survivor_fit'), 1)} | `{row.get('regime') or 'unknown'}` | {', '.join(row.get('attention_reasons') or []) or '—'} |"
        )

    lines.extend(
        [
            "",
            "## Attention Validation",
            "",
            f"- Baseline useful precision: `{_fmt_pct(attention_validation_summary['baseline_useful_precision'])}`",
            f"- Baseline survivor precision: `{_fmt_pct(attention_validation_summary['baseline_survivor_precision'])}`",
            f"- Top 10 useful precision: `{_fmt_pct(attention_validation_summary['top10_useful_precision'])}`",
            f"- Top 10 survivor precision: `{_fmt_pct(attention_validation_summary['top10_survivor_precision'])}`",
            f"- Top 10 persistent precision: `{_fmt_pct(attention_validation_summary['top10_persistent_precision'])}`",
            "",
            "## Decision Tracker",
            "",
            f"- Open promote: `{decision_summary['open_promote']}`",
            f"- Open watch: `{decision_summary['open_watch']}`",
            f"- Open observe: `{decision_summary['open_observe']}`",
            f"- Open cut: `{decision_summary['open_cut']}`",
            f"- Resolved latest decisions: `{decision_summary['resolved_latest_decisions']}`",
            "",
            "| Symbol | Decision | Grade | Stage | Status | Shape | Attention | Useful | Persistent | Survivor Fit |",
            "|---|---|---|---|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in list(decision_tracker.get("live_rows") or [])[:6]:
        lines.append(
            f"| {row.get('symbol') or 'n/a'} | `{row.get('decision_bucket')}` | `{row.get('decision_grade')}` | `{row.get('stage')}` | `{row.get('status')}` | "
            f"`{row.get('shape_state') or 'unknown'}` | "
            f"{_fmt_num(row.get('attention_score'), 1)} | {_fmt_num(row.get('useful_score'), 1)} | "
            f"{_fmt_num(row.get('persistent_score'), 1)} | {_fmt_num(row.get('survivor_fit'), 1)} |"
        )
    if decision_tracker.get("resolved_stats"):
        lines.extend(
            [
                "",
                "| Decision | N | Useful Precision | Survivor Precision | Persistent Precision |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for row in decision_tracker.get("resolved_stats") or []:
            lines.append(
                f"| `{row['decision_bucket']}` | {row['n']} | {_fmt_pct(row['useful_precision'])} | "
                f"{_fmt_pct(row['survivor_precision'])} | {_fmt_pct(row['persistent_precision'])} |"
            )

    lines.extend(
        [
            "",
            "## External Market Data",
            "",
            f"- Route ready: `{market_summary['route_ready']}`",
            f"- Route ready + Meteora official: `{market_summary['route_ready_meteora']}`",
            f"- Thin liquidity: `{market_summary['thin']}`",
            f"- High impact: `{market_summary['high_impact']}`",
            f"- Overheated: `{market_summary['overheated']}`",
            f"- No Jupiter route: `{market_summary['no_route']}`",
            f"- Meteora pairs seen on Dex: `{market_summary['meteora_seen']}`",
            "",
            "| Symbol | Readiness | Dex | Liquidity | MCap | Jup Impact | Label | Notes |",
            "|---|---|---|---:|---:|---:|---|---|",
        ]
    )
    for row in list(market_data.get("top") or [])[:6]:
        lines.append(
            f"| {row.get('symbol') or 'n/a'} | `{row.get('execution_readiness') or 'unknown'}` | `{row.get('dex_best_dex') or 'n/a'}` | "
            f"${_fmt_num(_to_float(row.get('dex_liquidity_usd')), 0)} | ${_fmt_num(_to_float(row.get('dex_market_cap')), 0)} | "
            f"{_fmt_pct(_to_float(row.get('jupiter_price_impact_pct')))} | `{row.get('jupiter_label') or 'n/a'}` | "
            f"{', '.join(row.get('execution_reasons') or []) or '—'} |"
        )

    lines.extend(
        [
            "",
            "## Starter Entry Gates",
            "",
            f"- Baseline useful precision: `{_fmt_pct(starter_summary['useful_precision'])}`",
            f"- Baseline survivor precision: `{_fmt_pct(starter_summary['survivor_precision'])}`",
            f"- Live pending rows scanned: `{starter_summary['live_pending_rows']}`",
            f"- Live non-neutral starter matches: `{starter_summary['live_starter_matches']}`",
            "",
            "| Gate | Kind | N | Useful | Survivor | Persistent |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in starter_gates.get("gate_rows") or []:
        lines.append(
            f"| `{row['name']}` | `{row['kind']}` | {row['n']} | {_fmt_pct(row['useful_precision'])} | {_fmt_pct(row['survivor_precision'])} | {_fmt_pct(row['persistent_precision'])} |"
        )
    lines.extend(
        [
            "",
            "| Symbol | Starter Grade | Matches | Promotion | Shape | Latest Ret |",
            "|---|---|---|---|---|---:|",
        ]
    )
    live_starter_rows = list(starter_gates.get("live_rows") or [])
    if live_starter_rows:
        for row in live_starter_rows[:6]:
            lines.append(
                f"| {row.get('symbol') or 'n/a'} | `{row.get('starter_grade') or 'starter_neutral'}` | `{', '.join(row.get('matches') or []) or '—'}` | "
                f"`{row.get('promotion_decision') or 'unknown'}` | `{row.get('shape_state') or 'unknown'}` | {_fmt_pct(_to_float(row.get('latest_ret')))} |"
            )
    else:
        lines.append("| n/a | `n/a` | `n/a` | `n/a` | `n/a` | n/a |")

    lines.extend(
        [
            "",
            "## Paper Expectancy",
            "",
            f"- Closed trades: `{paper_summary['closed_total']}`",
            f"- Open trades: `{paper_summary['open_total']}`",
            f"- Combined winrate: `{_fmt_pct(paper_summary['combined']['winrate'])}`",
            f"- Combined average return: `{_fmt_pct(paper_summary['combined']['avg_return'])}`",
            f"- Combined expectancy: `{_fmt_pct(paper_summary['combined']['expectancy'])}`",
            "",
            "| Overlay | N | Winrate | Avg Return | Avg Loss | Payoff | Expectancy |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in paper_expectancy.get("by_overlay") or []:
        lines.append(
            f"| `{row['overlay']}` | {row['n']} | {_fmt_pct(row['winrate'])} | {_fmt_pct(row['avg_return'])} | "
            f"{_fmt_pct(row['avg_loss'])} | {_fmt_num(row['payoff_ratio'])} | {_fmt_pct(row['expectancy'])} |"
        )
    lines.extend(
        [
            "",
            "## Operator Action Board",
            "",
            f"- Enter now: `{operator_summary['enter_now_count']}`",
            f"- Watch candidates: `{operator_summary['watch_candidate_count']}`",
            f"- Observe only: `{operator_summary['observe_only_count']}`",
            f"- Blocked: `{operator_summary['blocked_count']}`",
            f"- Open positions: `{operator_summary['open_positions']}`",
            f"- Manage now (`cut` / `protect`): `{operator_summary['cut_now_count'] + operator_summary['protect_count']}`",
            f"- Portfolio capital in use: `{_fmt_pct(_to_float(operator_summary['portfolio_capital_used']))}`",
            "",
            "| Lane | Symbol | Grade | Readiness | Signal Ret | Notes |",
            "|---|---|---|---|---:|---|",
        ]
    )
    board_rows = []
    board_rows.extend([("enter", row) for row in list(operator_board.get("enter_now") or [])[:3]])
    board_rows.extend([("watch", row) for row in list(operator_board.get("watch_candidates") or [])[:3]])
    board_rows.extend([("manage", row) for row in list(operator_board.get("open_management") or [])[:3]])
    if board_rows:
        for lane, row in board_rows:
            if lane == "manage":
                notes = ", ".join(row.get("flags") or []) or "hold"
                lines.append(
                    f"| `{lane}` | {row.get('symbol') or 'n/a'} | `{row.get('decision_grade') or 'unknown'}` | "
                    f"`{row.get('execution_readiness') or 'unknown'}` | {_fmt_pct(_to_float(row.get('current_trade_ret')))} | {notes} |"
                )
            else:
                notes = row.get("starter_reason") or ", ".join(row.get("blockers") or []) or "watch"
                lines.append(
                    f"| `{lane}` | {row.get('symbol') or 'n/a'} | `{row.get('decision_grade') or 'unknown'}` | "
                    f"`{row.get('execution_readiness') or 'unknown'}` | {_fmt_pct(_to_float(row.get('signal_ret')))} | {notes} |"
                )
    else:
        lines.append("| `n/a` | n/a | `n/a` | `n/a` | n/a | n/a |")
    lines.extend(
        [
            "",
            "## Promote Cohort",
            "",
            f"- Resolved promotes: `{promote_summary['resolved_promotes']}`",
            f"- Live promote-strong: `{promote_summary['live_promote_strong']}`",
            f"- Live promote-probe: `{promote_summary['live_promote_probe']}`",
            "",
            "| Grade | N | Useful Precision | Survivor Precision | Persistent Precision |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in promote_cohort.get("resolved_by_grade") or []:
        lines.append(
            f"| `{row['decision_grade']}` | {row['n']} | {_fmt_pct(row['useful_precision'])} | "
            f"{_fmt_pct(row['survivor_precision'])} | {_fmt_pct(row['persistent_precision'])} |"
        )

    lines.extend(
        [
            "",
            "## Live Research Queue",
            "",
            "| Symbol | Composite | Useful | Persistent | Regime | Tags |",
            "|---|---:|---:|---:|---|---|",
        ]
    )
    for row in report["top_live_research"]:
        lines.append(
            f"| {row['symbol']} | {float(row['composite_score']):.1f} | {float(row['useful_score']):.1f} | "
            f"{float(row['persistent_score']):.1f} | `{row['persistence_regime0']}` | {', '.join(row['tags']) or '—'} |"
        )

    lines.extend(["", "## Key Takeaways", ""])
    for item in report["takeaways"]:
        lines.append(f"- {item}")

    lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a consolidated daily meme research scorecard.")
    parser.add_argument("--refresh", action="store_true", help="Refresh upstream reports before building the scorecard.")
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=OUT_MD)
    args = parser.parse_args()

    if args.refresh:
        _run_refresh()

    previous = _load_json(args.out_json) if args.out_json.exists() else None
    data = {name: _load_json(path) for name, path in INPUT_FILES.items()}
    report = build_report(data, previous=previous)
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_md(args.out_md, report)
    append_history(HISTORY_JSONL, report)
    print(
        f"meme_daily_scorecard: winners24={report['summary']['signals_24h']['verified_winners']} "
        f"pending={len(report['pending_rows'])} useful_top10={report['summary']['useful_model']['top10_precision']:.4f}"
    )


if __name__ == "__main__":
    main()
