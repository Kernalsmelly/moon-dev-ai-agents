#!/usr/bin/env python3
"""Blend useful-winner and persistence models into a research priority monitor."""

from __future__ import annotations

import argparse
import importlib.util
import json
import time
from pathlib import Path
from typing import Any, Callable

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
DATASET_CSV = BASE / "data" / "meme_reports" / "meme_anchor_dataset.csv"
BASELINE_MODEL_PATH = BASE / "scripts" / "meme_anchor_baseline_model.py"
OUT_JSON = BASE / "data" / "meme_reports" / "research_priority_monitor.json"
OUT_MD = BASE / "data" / "meme_reports" / "research_priority_monitor.md"

PERSIST_FRIENDLY_REGIMES = {"late_slow_expansion", "calm_continuation"}


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


def _fmt_num(value: float | None, digits: int = 0) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def _evaluate_subset(rows: list[dict[str, Any]]) -> dict[str, Any]:
    useful_precision = (
        sum(int(row["label_useful"]) for row in rows) / len(rows) if rows else 0.0
    )
    persistent_precision = (
        sum(int(row["label_persistent"]) for row in rows) / len(rows) if rows else 0.0
    )
    return {
        "n": len(rows),
        "useful_precision": useful_precision,
        "persistent_precision": persistent_precision,
    }


def _with_scores(
    rows: list[dict[str, Any]],
    *,
    baseline: Any,
    useful_model: dict[str, Any],
    persistent_model: dict[str, Any],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        useful = baseline.score_row(useful_model, row)
        persistent = baseline.score_row(persistent_model, row)
        composite = (0.75 * float(useful["score"])) + (0.25 * float(persistent["score"]))
        tags: list[str] = []
        if float(useful["score"]) >= 75.0:
            tags.append("useful_high")
        if float(persistent["score"]) >= 50.0:
            tags.append("persistent_high")
        if str(row.get("persistence_regime0") or "") in PERSIST_FRIENDLY_REGIMES:
            tags.append("persist_friendly_regime")
        out.append(
            {
                **row,
                "useful_score": float(useful["score"]),
                "persistent_score": float(persistent["score"]),
                "composite_score": composite,
                "useful_prob": float(useful["prob"]),
                "persistent_prob": float(persistent["prob"]),
                "tags": tags,
            }
        )
    out.sort(key=lambda row: float(row["composite_score"]), reverse=True)
    return out


def _subset_report(
    scored_rows: list[dict[str, Any]],
    *,
    name: str,
    predicate: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    subset = [row for row in scored_rows if predicate(row)]
    report = _evaluate_subset(subset)
    report["name"] = name
    report["examples"] = subset[:10]
    return report


def _write_md(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Research Priority Monitor",
        "",
        "Blends the useful-winner baseline with the persistence baseline into a single research queue.",
        "",
        "## Dataset",
        "",
        f"- Dataset: `{report['dataset_path']}`",
        f"- Train rows: `{report['config']['train_rows']}`",
        f"- Validation rows: `{report['config']['validation_rows']}`",
        f"- Live lookback: `{report['config']['live_lookback_min']}m`",
        "",
        "## Validation Baselines",
        "",
        f"- Useful baseline: `{_fmt_pct(report['baselines']['useful_precision'])}`",
        f"- Persistent baseline: `{_fmt_pct(report['baselines']['persistent_precision'])}`",
        "",
        "## Validation Cohorts",
        "",
        "| Cohort | Rows | Useful Precision | Persistent Precision |",
        "|---|---:|---:|---:|",
    ]
    for cohort in report["validation_cohorts"]:
        lines.append(
            f"| `{cohort['name']}` | {int(cohort['n'])} | {_fmt_pct(cohort['useful_precision'])} | {_fmt_pct(cohort['persistent_precision'])} |"
        )
    lines.extend(
        [
            "",
            "## Live Research Queue",
            "",
            "| Symbol | Mint | Composite | Useful | Persistent | Regime | Source | MCap | Age0 | Mom5m0 | Hits | NetSOL | Tags |",
            "|---|---|---:|---:|---:|---|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in report["live"]:
        lines.append(
            f"| {row['symbol']} | `{row['mint']}` | {float(row['composite_score']):.1f} | "
            f"{float(row['useful_score']):.1f} | {float(row['persistent_score']):.1f} | "
            f"`{row['persistence_regime0']}` | `{row['signal_source']}` | "
            f"{_fmt_num(row.get('mcap0'), 0)} | {_fmt_num(row.get('pair_age_min0'), 1)} | "
            f"{_fmt_num(row.get('mom5m0'), 1)} | {_fmt_num(row.get('hits0'), 0)} | "
            f"{_fmt_num(row.get('net_sol_in0'), 2)} | {', '.join(row['tags']) or '—'} |"
        )
    lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a blended useful/persistent research monitor.")
    parser.add_argument("--dataset", type=Path, default=DATASET_CSV)
    parser.add_argument("--train-hours", type=float, default=72.0)
    parser.add_argument("--validate-hours", type=float, default=24.0)
    parser.add_argument("--live-lookback-min", type=float, default=120.0)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=OUT_MD)
    args = parser.parse_args()

    baseline = _load_module("meme_anchor_baseline_model_module", BASELINE_MODEL_PATH)
    rows = baseline.load_rows(args.dataset)
    now = time.time()
    train_since_ts = now - ((float(args.train_hours) + float(args.validate_hours)) * 3600.0)
    validate_cutoff_ts = now - (float(args.validate_hours) * 3600.0)
    train_rows = [row for row in rows if train_since_ts <= float(row["signal_ts"]) < validate_cutoff_ts]
    validation_rows = [row for row in rows if float(row["signal_ts"]) >= validate_cutoff_ts]

    useful_model = baseline.fit_model(train_rows, target_field="label_useful")
    persistent_model = baseline.fit_model(train_rows, target_field="label_persistent")

    scored_validation = _with_scores(
        validation_rows,
        baseline=baseline,
        useful_model=useful_model,
        persistent_model=persistent_model,
    )
    validation_baselines = _evaluate_subset(scored_validation)
    validation_cohorts = [
        _subset_report(
            scored_validation,
            name="useful>=75",
            predicate=lambda row: float(row["useful_score"]) >= 75.0,
        ),
        _subset_report(
            scored_validation,
            name="useful>=75 & persistent>=40",
            predicate=lambda row: float(row["useful_score"]) >= 75.0
            and float(row["persistent_score"]) >= 40.0,
        ),
        _subset_report(
            scored_validation,
            name="composite>=70",
            predicate=lambda row: float(row["composite_score"]) >= 70.0,
        ),
        _subset_report(
            scored_validation,
            name="useful>=70 & persist-friendly regime",
            predicate=lambda row: float(row["useful_score"]) >= 70.0
            and str(row.get("persistence_regime0") or "") in PERSIST_FRIENDLY_REGIMES,
        ),
    ]

    live_rows = baseline.load_live_rows(
        baseline._load_rank_module(), since_ts=now - (float(args.live_lookback_min) * 60.0)
    )
    scored_live = _with_scores(
        live_rows,
        baseline=baseline,
        useful_model=useful_model,
        persistent_model=persistent_model,
    )[: int(args.top)]

    report = {
        "generated_at": now,
        "dataset_path": str(args.dataset),
        "config": {
            "train_rows": len(train_rows),
            "validation_rows": len(validation_rows),
            "train_hours": float(args.train_hours),
            "validate_hours": float(args.validate_hours),
            "live_lookback_min": float(args.live_lookback_min),
        },
        "baselines": {
            "useful_precision": validation_baselines["useful_precision"],
            "persistent_precision": validation_baselines["persistent_precision"],
        },
        "validation_cohorts": validation_cohorts,
        "live": scored_live,
    }

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_md(args.out_md, report)
    print(
        f"research_priority_monitor: validation={len(validation_rows)} "
        f"live={len(scored_live)} useful_baseline={validation_baselines['useful_precision']:.4f} "
        f"persistent_baseline={validation_baselines['persistent_precision']:.4f}"
    )


if __name__ == "__main__":
    main()
