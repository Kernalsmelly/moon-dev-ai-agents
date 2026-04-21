#!/usr/bin/env python3
"""Validate survivor-fit against historical anchor outcomes."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import time
from collections import Counter
from pathlib import Path
from typing import Any

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
DATASET_CSV = BASE / "data" / "meme_reports" / "meme_anchor_dataset.csv"
SURVIVOR_RESEARCH_JSON = BASE / "data" / "meme_reports" / "meme_survivor_feature_research.json"
LIFECYCLE_MONITOR_PATH = BASE / "scripts" / "meme_lifecycle_monitor.py"
OUT_JSON = BASE / "data" / "meme_reports" / "meme_survivor_fit_validation.json"
OUT_MD = BASE / "data" / "meme_reports" / "meme_survivor_fit_validation.md"

SURVIVOR_CLASSES = {"persistent_runner", "partial_persistence"}


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        out = float(value)
        if math.isfinite(out):
            return out
    except Exception:
        return None
    return None


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100.0:.1f}%"


def _fmt_num(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def load_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for row in rows:
        row["label_useful"] = int(float(row.get("label_useful") or 0))
        row["label_persistent"] = int(float(row.get("label_persistent") or 0))
        row["survivor_grade"] = 1 if str(row.get("persistence_class") or "") in SURVIVOR_CLASSES else 0
    return rows


def _precision(rows: list[dict[str, Any]]) -> float:
    if not rows:
        return 0.0
    return sum(int(row.get("survivor_grade") or 0) for row in rows) / len(rows)


def _examples(rows: list[dict[str, Any]], *, count: int = 5) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows[:count]:
        out.append(
            {
                "symbol": row.get("symbol") or "n/a",
                "mint": row.get("mint") or "",
                "fit_score": row.get("fit_score"),
                "fit_stance": row.get("fit_stance"),
                "persistence_class": row.get("persistence_class") or "unknown",
                "mcap0": _to_float(row.get("mcap0")),
                "pair_age_min0": _to_float(row.get("pair_age_min0")),
                "mom5m0": _to_float(row.get("mom5m0")),
                "net_sol_in0": _to_float(row.get("net_sol_in0")),
                "mover_pattern0": row.get("mover_pattern0") or "unknown",
            }
        )
    return out


def build_report(rows: list[dict[str, Any]], survivor_research: dict[str, Any], lifecycle_module: Any) -> dict[str, Any]:
    useful_rows = [row for row in rows if int(row.get("label_useful") or 0) == 1]
    scored_rows: list[dict[str, Any]] = []
    for row in useful_rows:
        fit = lifecycle_module._survivor_fit(row, survivor_research)
        enriched = dict(row)
        enriched["fit_score"] = float(fit["score"])
        enriched["fit_stance"] = fit["stance"]
        enriched["fit_positive_tags"] = list(fit["positive_tags"])
        enriched["fit_negative_tags"] = list(fit["negative_tags"])
        scored_rows.append(enriched)

    baseline = _precision(scored_rows)
    stance_rows: list[dict[str, Any]] = []
    for stance, group_rows in sorted(Counter(str(row.get("fit_stance") or "unknown") for row in scored_rows).items()):
        group = [row for row in scored_rows if str(row.get("fit_stance") or "unknown") == stance]
        stance_rows.append(
            {
                "stance": stance,
                "n": len(group),
                "survivor_precision": _precision(group),
                "lift_vs_baseline": (_precision(group) / baseline) if baseline > 0 else None,
            }
        )

    threshold_rows: list[dict[str, Any]] = []
    for threshold in (45, 55, 65, 75):
        group = [row for row in scored_rows if float(row.get("fit_score") or 0.0) >= threshold]
        threshold_rows.append(
            {
                "threshold": threshold,
                "n": len(group),
                "survivor_precision": _precision(group),
                "lift_vs_baseline": (_precision(group) / baseline) if baseline > 0 else None,
            }
        )

    ranked = sorted(scored_rows, key=lambda row: float(row.get("fit_score") or 0.0), reverse=True)
    high_fit_failures = [row for row in ranked if int(row.get("survivor_grade") or 0) == 0]
    low_fit_survivors = sorted(
        [row for row in scored_rows if int(row.get("survivor_grade") or 0) == 1],
        key=lambda row: float(row.get("fit_score") or 0.0),
    )

    return {
        "generated_at": time.time(),
        "summary": {
            "useful_rows": len(scored_rows),
            "survivor_grade_rows": sum(int(row.get("survivor_grade") or 0) for row in scored_rows),
            "baseline_survivor_precision": baseline,
        },
        "by_stance": stance_rows,
        "by_threshold": threshold_rows,
        "top_fit_failures": _examples(high_fit_failures),
        "low_fit_survivors": _examples(low_fit_survivors),
    }


def write_md(path: Path, report: dict[str, Any]) -> None:
    s = report["summary"]
    lines = [
        "# Survivor Fit Validation",
        "",
        "Checks whether the new lifecycle `survivor_fit` score actually improves survivor selection inside the useful-winner cohort.",
        "",
        "## Summary",
        "",
        f"- Useful rows scored: `{s['useful_rows']}`",
        f"- Survivor-grade rows: `{s['survivor_grade_rows']}`",
        f"- Baseline survivor precision: `{_fmt_pct(s['baseline_survivor_precision'])}`",
        "",
        "## By Stance",
        "",
        "| Stance | N | Survivor Precision | Lift |",
        "|---|---:|---:|---:|",
    ]
    for row in report["by_stance"]:
        lines.append(
            f"| `{row['stance']}` | {row['n']} | {_fmt_pct(row['survivor_precision'])} | {_fmt_num(row['lift_vs_baseline'], 2)}x |"
        )

    lines.extend(
        [
            "",
            "## By Threshold",
            "",
            "| Threshold | N | Survivor Precision | Lift |",
            "|---|---:|---:|---:|",
        ]
    )
    for row in report["by_threshold"]:
        lines.append(
            f"| `>= {row['threshold']}` | {row['n']} | {_fmt_pct(row['survivor_precision'])} | {_fmt_num(row['lift_vs_baseline'], 2)}x |"
        )

    lines.extend(
        [
            "",
            "## High-Fit Failures",
            "",
            "| Symbol | Fit | Class | MCap0 | Age0 | Mom5m0 | NetSOL0 | Pattern |",
            "|---|---:|---|---:|---:|---:|---:|---|",
        ]
    )
    for row in report["top_fit_failures"]:
        lines.append(
            f"| {row['symbol']} | {_fmt_num(row['fit_score'], 1)} | `{row['persistence_class']}` | {_fmt_num(row['mcap0'], 0)} | {_fmt_num(row['pair_age_min0'], 1)} | {_fmt_num(row['mom5m0'], 1)} | {_fmt_num(row['net_sol_in0'], 1)} | `{row['mover_pattern0']}` |"
        )

    lines.extend(
        [
            "",
            "## Low-Fit Survivors",
            "",
            "| Symbol | Fit | Class | MCap0 | Age0 | Mom5m0 | NetSOL0 | Pattern |",
            "|---|---:|---|---:|---:|---:|---:|---|",
        ]
    )
    for row in report["low_fit_survivors"]:
        lines.append(
            f"| {row['symbol']} | {_fmt_num(row['fit_score'], 1)} | `{row['persistence_class']}` | {_fmt_num(row['mcap0'], 0)} | {_fmt_num(row['pair_age_min0'], 1)} | {_fmt_num(row['mom5m0'], 1)} | {_fmt_num(row['net_sol_in0'], 1)} | `{row['mover_pattern0']}` |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate survivor-fit against historical anchor outcomes.")
    parser.add_argument("--dataset", type=Path, default=DATASET_CSV)
    parser.add_argument("--survivor-research", type=Path, default=SURVIVOR_RESEARCH_JSON)
    parser.add_argument("--lifecycle-monitor", type=Path, default=LIFECYCLE_MONITOR_PATH)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=OUT_MD)
    args = parser.parse_args()

    rows = load_rows(args.dataset)
    survivor_research = json.loads(args.survivor_research.read_text())
    lifecycle_module = _load_module("meme_lifecycle_monitor_module_for_validation", args.lifecycle_monitor)
    report = build_report(rows, survivor_research, lifecycle_module)
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_md(args.out_md, report)
    print(
        "meme_survivor_fit_validation: "
        f"useful={report['summary']['useful_rows']} "
        f"baseline={report['summary']['baseline_survivor_precision']:.4f}"
    )


if __name__ == "__main__":
    main()
