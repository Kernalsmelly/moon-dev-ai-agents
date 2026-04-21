#!/usr/bin/env python3
"""Validate the live attention-shortlist scoring on historical anchors."""

from __future__ import annotations

import argparse
import importlib.util
import json
import time
from pathlib import Path
from typing import Any

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
DATASET_CSV = BASE / "data" / "meme_reports" / "meme_anchor_dataset.csv"
BASELINE_MODEL_PATH = BASE / "scripts" / "meme_anchor_baseline_model.py"
LIFECYCLE_MONITOR_PATH = BASE / "scripts" / "meme_lifecycle_monitor.py"
ATTENTION_SHORTLIST_PATH = BASE / "scripts" / "meme_attention_shortlist.py"
SURVIVOR_RESEARCH_JSON = BASE / "data" / "meme_reports" / "meme_survivor_feature_research.json"
OUT_JSON = BASE / "data" / "meme_reports" / "meme_attention_shortlist_validation.json"
OUT_MD = BASE / "data" / "meme_reports" / "meme_attention_shortlist_validation.md"

SURVIVOR_CLASSES = {"persistent_runner", "partial_persistence"}


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100.0:.1f}%"


def _fmt_num(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def _precision(rows: list[dict[str, Any]], field: str) -> float:
    if not rows:
        return 0.0
    return sum(int(row.get(field) or 0) for row in rows) / len(rows)


def _examples(rows: list[dict[str, Any]], *, count: int = 5) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows[:count]:
        out.append(
            {
                "symbol": row.get("symbol") or "n/a",
                "mint": row.get("mint") or "",
                "tier": row.get("attention_tier") or "unknown",
                "attention_score": row.get("attention_score"),
                "label_useful": int(row.get("label_useful") or 0),
                "label_persistent": int(row.get("label_persistent") or 0),
                "survivor_grade": int(row.get("survivor_grade") or 0),
                "persistence_class": row.get("persistence_class") or "unknown",
                "useful_score": row.get("useful_score"),
                "persistent_score": row.get("persistent_score"),
                "survivor_fit": row.get("survivor_fit"),
            }
        )
    return out


def build_report(
    rows: list[dict[str, Any]],
    *,
    baseline: Any,
    lifecycle_module: Any,
    attention_module: Any,
    survivor_research: dict[str, Any],
    train_hours: float,
    validate_hours: float,
) -> dict[str, Any]:
    now = time.time()
    train_since_ts = now - ((float(train_hours) + float(validate_hours)) * 3600.0)
    validate_cutoff_ts = now - (float(validate_hours) * 3600.0)
    train_rows = [row for row in rows if train_since_ts <= float(row["signal_ts"]) < validate_cutoff_ts]
    validation_rows = [row for row in rows if float(row["signal_ts"]) >= validate_cutoff_ts]

    useful_model = baseline.fit_model(train_rows, target_field="label_useful")
    persistent_model = baseline.fit_model(train_rows, target_field="label_persistent")

    scored_validation: list[dict[str, Any]] = []
    for row in validation_rows:
        useful = baseline.score_row(useful_model, row)
        persistent = baseline.score_row(persistent_model, row)
        fit = lifecycle_module._survivor_fit(row, survivor_research)
        shortlist_row = attention_module._score_row(
            {
                **row,
                "stage": "emerging_watchlist",
                "status": "watchlist",
                "regime": row.get("persistence_regime0") or "unknown",
                "useful_score": float(useful["score"]),
                "persistent_score": float(persistent["score"]),
                "survivor_fit": float(fit["score"]),
            }
        )
        scored_validation.append(
            {
                **row,
                **shortlist_row,
                "useful_score": float(useful["score"]),
                "persistent_score": float(persistent["score"]),
                "survivor_fit": float(fit["score"]),
                "survivor_grade": 1 if str(row.get("persistence_class") or "") in SURVIVOR_CLASSES else 0,
            }
        )

    baseline_useful = _precision(scored_validation, "label_useful")
    baseline_survivor = _precision(scored_validation, "survivor_grade")
    baseline_persistent = _precision(scored_validation, "label_persistent")

    tier_reports = []
    for tier in ("focus_now", "watch_closely", "elevate", "monitor", "exploratory"):
        tier_rows = [row for row in scored_validation if str(row.get("attention_tier") or "") == tier]
        if not tier_rows:
            continue
        tier_reports.append(
            {
                "tier": tier,
                "n": len(tier_rows),
                "useful_precision": _precision(tier_rows, "label_useful"),
                "survivor_precision": _precision(tier_rows, "survivor_grade"),
                "persistent_precision": _precision(tier_rows, "label_persistent"),
            }
        )

    ranked = sorted(scored_validation, key=lambda row: float(row.get("attention_score") or 0.0), reverse=True)
    top10 = ranked[:10]
    top20 = ranked[:20]

    return {
        "generated_at": time.time(),
        "config": {
            "train_hours": float(train_hours),
            "validate_hours": float(validate_hours),
            "train_rows": len(train_rows),
            "validation_rows": len(validation_rows),
        },
        "summary": {
            "baseline_useful_precision": baseline_useful,
            "baseline_survivor_precision": baseline_survivor,
            "baseline_persistent_precision": baseline_persistent,
            "top10_useful_precision": _precision(top10, "label_useful"),
            "top10_survivor_precision": _precision(top10, "survivor_grade"),
            "top10_persistent_precision": _precision(top10, "label_persistent"),
            "top20_useful_precision": _precision(top20, "label_useful"),
            "top20_survivor_precision": _precision(top20, "survivor_grade"),
            "top20_persistent_precision": _precision(top20, "label_persistent"),
        },
        "tiers": tier_reports,
        "top_examples": _examples(top10),
    }


def write_md(path: Path, report: dict[str, Any]) -> None:
    s = report["summary"]
    lines = [
        "# Attention Shortlist Validation",
        "",
        "Replays the current attention-shortlist logic on historical validation anchors to see whether the tiering has real signal.",
        "",
        "Note: this is an emerging-stage proxy only. The live shortlist is now intentionally pending-first, so the strongest `focus_now` and `watch_closely` behavior will only show up prospectively as names move through the lifecycle board.",
        "",
        "## Summary",
        "",
        f"- Train rows: `{report['config']['train_rows']}`",
        f"- Validation rows: `{report['config']['validation_rows']}`",
        f"- Baseline useful precision: `{_fmt_pct(s['baseline_useful_precision'])}`",
        f"- Baseline survivor precision: `{_fmt_pct(s['baseline_survivor_precision'])}`",
        f"- Baseline persistent precision: `{_fmt_pct(s['baseline_persistent_precision'])}`",
        f"- Top 10 useful precision: `{_fmt_pct(s['top10_useful_precision'])}`",
        f"- Top 10 survivor precision: `{_fmt_pct(s['top10_survivor_precision'])}`",
        f"- Top 10 persistent precision: `{_fmt_pct(s['top10_persistent_precision'])}`",
        "",
        "## Tier Performance",
        "",
        "| Tier | N | Useful Precision | Survivor Precision | Persistent Precision |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in report["tiers"]:
        lines.append(
            f"| `{row['tier']}` | {row['n']} | {_fmt_pct(row['useful_precision'])} | {_fmt_pct(row['survivor_precision'])} | {_fmt_pct(row['persistent_precision'])} |"
        )
    lines.extend(
        [
            "",
            "## Top Examples",
            "",
            "| Symbol | Tier | Attention | Useful | Survivor | Persistent | Class |",
            "|---|---|---:|---:|---:|---:|---|",
        ]
    )
    for row in report["top_examples"]:
        lines.append(
            f"| {row['symbol']} | `{row['tier']}` | {_fmt_num(row['attention_score'], 1)} | "
            f"{_fmt_pct(row['label_useful'])} | {_fmt_pct(row['survivor_grade'])} | {_fmt_pct(row['label_persistent'])} | `{row['persistence_class']}` |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the attention-shortlist logic on historical anchors.")
    parser.add_argument("--dataset", type=Path, default=DATASET_CSV)
    parser.add_argument("--train-hours", type=float, default=72.0)
    parser.add_argument("--validate-hours", type=float, default=24.0)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=OUT_MD)
    args = parser.parse_args()

    baseline = _load_module("meme_anchor_baseline_model_for_attention_validation", BASELINE_MODEL_PATH)
    lifecycle = _load_module("meme_lifecycle_monitor_for_attention_validation", LIFECYCLE_MONITOR_PATH)
    attention = _load_module("meme_attention_shortlist_for_attention_validation", ATTENTION_SHORTLIST_PATH)
    survivor_research = json.loads(SURVIVOR_RESEARCH_JSON.read_text())
    rows = baseline.load_rows(args.dataset)
    report = build_report(
        rows,
        baseline=baseline,
        lifecycle_module=lifecycle,
        attention_module=attention,
        survivor_research=survivor_research,
        train_hours=float(args.train_hours),
        validate_hours=float(args.validate_hours),
    )
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_md(args.out_md, report)
    print(
        "meme_attention_shortlist_validation: "
        f"validation={report['config']['validation_rows']} "
        f"top10_survivor={report['summary']['top10_survivor_precision']:.4f}"
    )


if __name__ == "__main__":
    main()
