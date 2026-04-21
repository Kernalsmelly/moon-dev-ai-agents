#!/usr/bin/env python3
"""Track live meme decisions prospectively and grade them as outcomes mature."""

from __future__ import annotations

import argparse
import csv
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
REPORTS = BASE / "data" / "meme_reports"
LIFECYCLE_JSON = REPORTS / "meme_lifecycle_monitor.json"
ATTENTION_JSON = REPORTS / "meme_attention_shortlist.json"
DATASET_CSV = REPORTS / "meme_anchor_dataset.csv"
JOURNAL_JSONL = REPORTS / "meme_decision_journal.jsonl"
OUT_JSON = REPORTS / "meme_decision_tracker.json"
OUT_MD = REPORTS / "meme_decision_tracker.md"

LIVE_STAGES = {
    "emerging_watchlist",
    "pending_promote_now",
    "pending_watch",
    "pending_cut_bias",
}
SURVIVOR_CLASSES = {"persistent_runner", "partial_persistence"}
BUCKET_ORDER = {"promote": 0, "watch": 1, "observe": 2, "cut": 3}
CALMER_REGIMES = {"late_slow_expansion", "calm_continuation"}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _load_dataset(path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            mint = str(row.get("mint") or "")
            if mint:
                out[mint] = row
    return out


def _load_journal(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100.0:.1f}%"


def _fmt_num(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def _flatten_attention(attention: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_mint: dict[str, dict[str, Any]] = {}
    tiers = attention.get("tiers") or {}
    for rows in tiers.values():
        for row in rows or []:
            mint = str(row.get("mint") or "")
            if mint and mint not in by_mint:
                by_mint[mint] = row
    return by_mint


def _decision_bucket(row: dict[str, Any], attention_row: dict[str, Any] | None) -> str:
    stage = str(row.get("stage") or "")
    status = str(row.get("status") or "")
    attention_tier = str((attention_row or {}).get("attention_tier") or "")

    if stage == "pending_cut_bias" or status == "cut_bias":
        return "cut"
    if stage == "pending_promote_now" or attention_tier == "focus_now":
        return "promote"
    if stage == "pending_watch" or attention_tier in {"watch_closely", "elevate"}:
        return "watch"
    return "observe"


def _decision_reasons(row: dict[str, Any], attention_row: dict[str, Any] | None, bucket: str) -> list[str]:
    reasons: list[str] = []
    stage = str(row.get("stage") or "")
    status = str(row.get("status") or "")
    survivor_fit = float(row.get("survivor_fit") or 0.0)
    regime = str(row.get("regime") or "unknown")
    shape_state = str(row.get("shape_state") or "unknown")
    shape_path = str(row.get("shape_path_30_to_60") or "")
    shape_path_survivor_precision = float(row.get("shape_path_historical_survivor_precision") or 0.0)

    reasons.append(f"stage:{stage}")
    if status:
        reasons.append(f"status:{status}")
    if shape_state and shape_state != "unknown":
        reasons.append(f"shape:{shape_state}")
    if shape_path and shape_path_survivor_precision >= 0.35:
        reasons.append("shape_path_confirmed")
    if attention_row and attention_row.get("attention_tier"):
        reasons.append(f"tier:{attention_row['attention_tier']}")
    if survivor_fit >= 65.0:
        reasons.append("survivor_fit_strong")
    elif survivor_fit >= 55.0:
        reasons.append("survivor_fit_ok")
    if regime in {"late_slow_expansion", "calm_continuation"}:
        reasons.append("calmer_regime")
    if bucket == "cut":
        reasons.append("stop_respecting")
    elif bucket == "promote":
        reasons.append("earned_more_time")
    return reasons[:5]


def _promote_grade_core(
    *,
    useful_score: float,
    persistent_score: float,
    survivor_fit: float,
    regime: str,
    shape_state: str,
    shape_path_survivor_precision: float,
) -> tuple[str | None, list[str]]:
    calmer_regime = regime in CALMER_REGIMES
    reasons: list[str] = []
    fit_useful_strong = survivor_fit >= 70.0 and useful_score >= 60.0
    persist_regime_strong = persistent_score >= 70.0 and calmer_regime
    shape_supportive = shape_state in {"extending_cleanly", "holding_pullback"}
    shape_fragile = shape_state in {"blowoff_risk", "losing_steam"}
    supportive_path = shape_path_survivor_precision >= 0.35

    if fit_useful_strong:
        reasons.append("fit_plus_useful")
    if persist_regime_strong:
        reasons.append("persist_plus_regime")
    if shape_supportive:
        reasons.append(f"shape:{shape_state}")
    if supportive_path:
        reasons.append("shape_path_confirmed")
    elif shape_fragile:
        reasons.append(f"shape:{shape_state}")

    if (fit_useful_strong or persist_regime_strong or supportive_path) and shape_supportive:
        return "promote_strong", reasons
    if shape_fragile:
        return "promote_probe", reasons + ["shape_not_confirmed"]
    if survivor_fit >= 55.0 or persistent_score >= 35.0 or useful_score >= 45.0:
        if survivor_fit >= 55.0:
            reasons.append("supportive_fit")
        if persistent_score >= 35.0:
            reasons.append("supportive_persist")
        if useful_score >= 45.0:
            reasons.append("supportive_useful")
        return "promote_probe", reasons
    return "promote_probe", ["earned_more_time_but_thin"]


def _decision_grade(row: dict[str, Any], bucket: str) -> tuple[str, list[str]]:
    useful_score = float(row.get("useful_score") or 0.0)
    persistent_score = float(row.get("persistent_score") or 0.0)
    survivor_fit = float(row.get("survivor_fit") or 0.0)
    regime = str(row.get("regime") or "unknown")
    shape_state = str(row.get("shape_state") or "unknown")
    shape_path_survivor_precision = float(row.get("shape_path_historical_survivor_precision") or 0.0)

    if bucket == "promote":
        grade, reasons = _promote_grade_core(
            useful_score=useful_score,
            persistent_score=persistent_score,
            survivor_fit=survivor_fit,
            regime=regime,
            shape_state=shape_state,
            shape_path_survivor_precision=shape_path_survivor_precision,
        )
        return grade or "promote_probe", reasons
    if bucket == "watch":
        return ("watch_setup", ["still_needs_60m_confirmation"])
    if bucket == "cut":
        return ("cut_hard", ["fading_checkpoint"])
    return ("observe_only", ["no_upgrade_signal"])


def _snapshot_fields(row: dict[str, Any], attention_row: dict[str, Any] | None, bucket: str) -> dict[str, Any]:
    decision_grade, grade_reasons = _decision_grade(row, bucket)
    return {
        "mint": row.get("mint") or "",
        "symbol": row.get("symbol") or "n/a",
        "decision_bucket": bucket,
        "decision_grade": decision_grade,
        "decision_grade_reasons": grade_reasons,
        "stage": row.get("stage") or "unknown",
        "status": row.get("status") or "unknown",
        "attention_tier": (attention_row or {}).get("attention_tier") or "none",
        "attention_score": round(float((attention_row or {}).get("attention_score") or 0.0), 1),
        "useful_score": round(float(row.get("useful_score") or 0.0), 1),
        "persistent_score": round(float(row.get("persistent_score") or 0.0), 1),
        "survivor_fit": round(float(row.get("survivor_fit") or 0.0), 1),
        "shape_state": row.get("shape_state") or "unknown",
        "shape_score": round(float(row.get("shape_score") or 0.0), 1),
        "shape_path_30_to_60": row.get("shape_path_30_to_60"),
        "shape_path_historical_survivor_precision": round(float(row.get("shape_path_historical_survivor_precision") or 0.0), 3),
        "regime": row.get("regime") or "unknown",
    }


def _build_live_board(lifecycle: dict[str, Any], attention: dict[str, Any]) -> list[dict[str, Any]]:
    attention_by_mint = _flatten_attention(attention)
    rows: list[dict[str, Any]] = []
    for row in lifecycle.get("board") or []:
        stage = str(row.get("stage") or "")
        if stage not in LIVE_STAGES:
            continue
        mint = str(row.get("mint") or "")
        attention_row = attention_by_mint.get(mint)
        bucket = _decision_bucket(row, attention_row)
        decision_grade, grade_reasons = _decision_grade(row, bucket)
        entry = {
            **row,
            "decision_bucket": bucket,
            "decision_grade": decision_grade,
            "decision_grade_reasons": grade_reasons,
            "decision_reasons": _decision_reasons(row, attention_row, bucket),
            "attention_tier": (attention_row or {}).get("attention_tier") or "none",
            "attention_score": float((attention_row or {}).get("attention_score") or 0.0),
        }
        rows.append(entry)

    rows.sort(
        key=lambda row: (
            BUCKET_ORDER.get(str(row.get("decision_bucket") or ""), 99),
            -float(row.get("attention_score") or 0.0),
            -float(row.get("survivor_fit") or 0.0),
            str(row.get("symbol") or ""),
        )
    )
    return rows


def _append_journal(path: Path, live_rows: list[dict[str, Any]]) -> int:
    existing = _load_journal(path)
    last_by_mint: dict[str, dict[str, Any]] = {}
    for row in existing:
        mint = str(row.get("mint") or "")
        if mint:
            last_by_mint[mint] = row

    appended = 0
    with path.open("a", encoding="utf-8") as fh:
        for row in live_rows:
            mint = str(row.get("mint") or "")
            if not mint:
                continue
            current = _snapshot_fields(row, row, str(row.get("decision_bucket") or "observe"))
            prev = last_by_mint.get(mint)
            if prev and _snapshot_fields(prev, prev, str(prev.get("decision_bucket") or "observe")) == current:
                continue
            payload = {
                "ts": time.time(),
                **current,
                "decision_reasons": row.get("decision_reasons") or [],
            }
            fh.write(json.dumps(payload) + "\n")
            appended += 1
            last_by_mint[mint] = payload
    return appended


def _latest_by_mint(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        mint = str(row.get("mint") or "")
        if not mint:
            continue
        latest[mint] = row
    return list(latest.values())


def _classify_outcome(anchor_row: dict[str, Any] | None) -> dict[str, Any]:
    if not anchor_row:
        return {"resolved": False, "class": "unresolved"}
    klass = str(anchor_row.get("persistence_class") or "")
    if not klass or klass == "pending_6h":
        return {"resolved": False, "class": "pending_6h"}
    return {
        "resolved": True,
        "class": klass,
        "label_useful": int(float(anchor_row.get("label_useful") or 0.0)),
        "label_persistent": int(float(anchor_row.get("label_persistent") or 0.0)),
        "survivor_grade": 1 if klass in SURVIVOR_CLASSES else 0,
    }


def _precision(rows: list[dict[str, Any]], field: str) -> float:
    if not rows:
        return 0.0
    return sum(int(row.get(field) or 0) for row in rows) / len(rows)


def build_report(
    *,
    lifecycle: dict[str, Any],
    attention: dict[str, Any],
    dataset: dict[str, dict[str, Any]],
    journal_rows: list[dict[str, Any]],
    min_age_hours: float,
    appended_rows: int,
) -> dict[str, Any]:
    live_rows = _build_live_board(lifecycle, attention)
    latest_journal = _latest_by_mint(journal_rows)

    current_by_mint = {str(row.get("mint") or ""): row for row in live_rows if row.get("mint")}
    open_rows = [row for row in live_rows if row.get("mint")]

    resolved_candidates: list[dict[str, Any]] = []
    for row in latest_journal:
        mint = str(row.get("mint") or "")
        if mint in current_by_mint:
            continue
        age_hours = (time.time() - float(row.get("ts") or 0.0)) / 3600.0
        outcome = _classify_outcome(dataset.get(mint))
        bucket = str(row.get("decision_bucket") or "observe")
        decision_grade = str(row.get("decision_grade") or "")
        grade_reasons = list(row.get("decision_grade_reasons") or [])
        if not decision_grade:
            decision_grade, grade_reasons = _decision_grade(row, bucket)
        resolved_candidates.append(
            {
                **row,
                "age_hours": age_hours,
                "decision_grade": decision_grade,
                "decision_grade_reasons": grade_reasons,
                **outcome,
            }
        )

    resolved = [
        row for row in resolved_candidates
        if float(row.get("age_hours") or 0.0) >= min_age_hours and bool(row.get("resolved"))
    ]

    decision_stats: list[dict[str, Any]] = []
    for bucket in ("promote", "watch", "observe", "cut"):
        rows = [row for row in resolved if str(row.get("decision_bucket") or "") == bucket]
        if not rows:
            continue
        class_counts = Counter(str(row.get("class") or "unknown") for row in rows)
        decision_stats.append(
            {
                "decision_bucket": bucket,
                "n": len(rows),
                "useful_precision": _precision(rows, "label_useful"),
                "survivor_precision": _precision(rows, "survivor_grade"),
                "persistent_precision": _precision(rows, "label_persistent"),
                "class_counts": dict(class_counts),
            }
        )

    grade_stats: list[dict[str, Any]] = []
    for grade in ("promote_strong", "promote_probe", "watch_setup", "observe_only", "cut_hard"):
        rows = [row for row in resolved if str(row.get("decision_grade") or "") == grade]
        if not rows:
            continue
        class_counts = Counter(str(row.get("class") or "unknown") for row in rows)
        grade_stats.append(
            {
                "decision_grade": grade,
                "n": len(rows),
                "useful_precision": _precision(rows, "label_useful"),
                "survivor_precision": _precision(rows, "survivor_grade"),
                "persistent_precision": _precision(rows, "label_persistent"),
                "class_counts": dict(class_counts),
            }
        )

    live_summary = Counter(str(row.get("decision_bucket") or "observe") for row in open_rows)
    grade_summary = Counter(str(row.get("decision_grade") or "observe_only") for row in open_rows)

    return {
        "generated_at": time.time(),
        "journal": {
            "path": str(JOURNAL_JSONL),
            "entries": len(journal_rows),
            "appended_this_run": appended_rows,
        },
        "summary": {
            "open_promote": live_summary.get("promote", 0),
            "open_watch": live_summary.get("watch", 0),
            "open_observe": live_summary.get("observe", 0),
            "open_cut": live_summary.get("cut", 0),
            "open_promote_strong": grade_summary.get("promote_strong", 0),
            "open_promote_probe": grade_summary.get("promote_probe", 0),
            "resolved_latest_decisions": len(resolved),
            "min_age_hours": float(min_age_hours),
        },
        "live_rows": open_rows,
        "resolved_stats": decision_stats,
        "resolved_grade_stats": grade_stats,
        "resolved_rows": resolved,
        "resolved_examples": resolved[:10],
    }


def write_md(path: Path, report: dict[str, Any]) -> None:
    s = report["summary"]
    lines = [
        "# Meme Decision Tracker",
        "",
        "Prospectively logs live `promote/watch/observe/cut` decisions so we can grade them later instead of relying on memory.",
        "",
        "## Summary",
        "",
        f"- Journal entries: `{report['journal']['entries']}`",
        f"- Appended this run: `{report['journal']['appended_this_run']}`",
        f"- Open promote: `{s['open_promote']}`",
        f"- Open watch: `{s['open_watch']}`",
        f"- Open observe: `{s['open_observe']}`",
        f"- Open cut: `{s['open_cut']}`",
        f"- Open promote-strong: `{s['open_promote_strong']}`",
        f"- Open promote-probe: `{s['open_promote_probe']}`",
        f"- Resolved latest decisions: `{s['resolved_latest_decisions']}`",
        f"- Minimum resolved age: `{_fmt_num(s['min_age_hours'], 1)}h`",
        "",
        "## Live Decision Board",
        "",
        "| Symbol | Decision | Grade | Stage | Status | Shape | Attention | Useful | Persistent | Survivor Fit | Reasons |",
        "|---|---|---|---|---|---|---:|---:|---:|---:|---|",
    ]
    for row in report["live_rows"]:
        lines.append(
            f"| {row.get('symbol') or 'n/a'} | `{row.get('decision_bucket')}` | `{row.get('decision_grade')}` | `{row.get('stage')}` | `{row.get('status')}` | "
            f"`{row.get('shape_state') or 'unknown'}` | "
            f"{_fmt_num(float(row.get('attention_score') or 0.0), 1)} | {_fmt_num(float(row.get('useful_score') or 0.0), 1)} | "
            f"{_fmt_num(float(row.get('persistent_score') or 0.0), 1)} | {_fmt_num(float(row.get('survivor_fit') or 0.0), 1)} | "
            f"{', '.join((row.get('decision_grade_reasons') or []) + (row.get('decision_reasons') or [])) or '—'} |"
        )

    lines.extend(
        [
            "",
            "## Resolved Decision Stats",
            "",
            "| Decision | N | Useful Precision | Survivor Precision | Persistent Precision |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in report["resolved_stats"]:
        lines.append(
            f"| `{row['decision_bucket']}` | {row['n']} | {_fmt_pct(row['useful_precision'])} | "
            f"{_fmt_pct(row['survivor_precision'])} | {_fmt_pct(row['persistent_precision'])} |"
        )

    if report.get("resolved_grade_stats"):
        lines.extend(
            [
                "",
                "## Resolved Grade Stats",
                "",
                "| Grade | N | Useful Precision | Survivor Precision | Persistent Precision |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for row in report["resolved_grade_stats"]:
            lines.append(
                f"| `{row['decision_grade']}` | {row['n']} | {_fmt_pct(row['useful_precision'])} | "
                f"{_fmt_pct(row['survivor_precision'])} | {_fmt_pct(row['persistent_precision'])} |"
            )

    if report["resolved_examples"]:
        lines.extend(
            [
                "",
                "## Resolved Examples",
                "",
                "| Symbol | Decision | Grade | Outcome | Useful | Survivor | Persistent |",
                "|---|---|---|---|---:|---:|---:|",
            ]
        )
        for row in report["resolved_examples"]:
            lines.append(
                f"| {row.get('symbol') or 'n/a'} | `{row.get('decision_bucket')}` | `{row.get('decision_grade')}` | `{row.get('class')}` | "
                f"{_fmt_pct(float(row.get('label_useful') or 0.0))} | {_fmt_pct(float(row.get('survivor_grade') or 0.0))} | "
                f"{_fmt_pct(float(row.get('label_persistent') or 0.0))} |"
            )

    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Track live meme decisions prospectively and grade them later.")
    parser.add_argument("--lifecycle", type=Path, default=LIFECYCLE_JSON)
    parser.add_argument("--attention", type=Path, default=ATTENTION_JSON)
    parser.add_argument("--dataset", type=Path, default=DATASET_CSV)
    parser.add_argument("--journal", type=Path, default=JOURNAL_JSONL)
    parser.add_argument("--min-age-hours", type=float, default=6.0)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=OUT_MD)
    args = parser.parse_args()

    lifecycle = _load_json(args.lifecycle)
    attention = _load_json(args.attention)
    dataset = _load_dataset(args.dataset)
    args.journal.parent.mkdir(parents=True, exist_ok=True)

    live_rows = _build_live_board(lifecycle, attention)
    appended = _append_journal(args.journal, live_rows)
    journal_rows = _load_journal(args.journal)
    report = build_report(
        lifecycle=lifecycle,
        attention=attention,
        dataset=dataset,
        journal_rows=journal_rows,
        min_age_hours=float(args.min_age_hours),
        appended_rows=appended,
    )
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_md(args.out_md, report)
    print(
        "meme_decision_tracker: "
        f"open={len(report['live_rows'])} "
        f"resolved={report['summary']['resolved_latest_decisions']} "
        f"appended={appended}"
    )


if __name__ == "__main__":
    main()
