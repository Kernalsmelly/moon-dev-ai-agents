#!/usr/bin/env python3
"""Monitor persistence specifically for the late_slow_expansion regime."""

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
OUT_JSON = BASE / "data" / "meme_reports" / "late_slow_persistence_monitor.json"
OUT_MD = BASE / "data" / "meme_reports" / "late_slow_persistence_monitor.md"
TARGET_REGIME = "late_slow_expansion"


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


def _subset(rows: list[dict[str, Any]], regime: str) -> list[dict[str, Any]]:
    return [row for row in rows if str(row.get("persistence_regime0") or "") == regime]


def _threshold_precision(model: dict[str, Any], baseline: Any, rows: list[dict[str, Any]], target_field: str, threshold: float) -> dict[str, Any]:
    scored = []
    for row in rows:
        score = baseline.score_row(model, row)
        scored.append({"row": row, "score": float(score["score"])})
    subset = [item for item in scored if item["score"] >= threshold]
    precision = (
        sum(int(item["row"].get(target_field) or 0) for item in subset) / len(subset)
        if subset
        else 0.0
    )
    return {"n": len(subset), "precision": precision}


def _write_md(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Late Slow Persistence Monitor",
        "",
        "Narrow persistence monitor for the `late_slow_expansion` regime only.",
        "",
        "## Dataset",
        "",
        f"- Dataset: `{report['dataset_path']}`",
        f"- Train rows: `{report['config']['train_rows']}`",
        f"- Validation rows: `{report['config']['validation_rows']}`",
        f"- Target regime: `{report['config']['target_regime']}`",
        f"- Live lookback: `{report['config']['live_lookback_min']}m`",
        "",
        "## Subset Summary",
        "",
        f"- Train subset rows: `{report['subset']['train_rows']}`",
        f"- Train useful positives: `{report['subset']['train_useful_positives']}`",
        f"- Train persistent positives: `{report['subset']['train_persistent_positives']}`",
        f"- Validation subset rows: `{report['subset']['validation_rows']}`",
        f"- Validation useful baseline: `{_fmt_pct(report['subset']['validation_useful_baseline'])}`",
        f"- Validation persistent baseline: `{_fmt_pct(report['subset']['validation_persistent_baseline'])}`",
        "",
        "## Persistence Validation",
        "",
        "| Threshold | Rows | Precision |",
        "|---|---:|---:|",
    ]
    for threshold, stats in report["validation"]["persistent"]["thresholds"].items():
        lines.append(f"| `>= {threshold}` | {int(stats['n'])} | {_fmt_pct(stats['precision'])} |")
    lines.extend(
        [
            "",
            "Top-k persistence:",
            "",
            "| Top-k | Rows | Precision |",
            "|---|---:|---:|",
        ]
    )
    for k, stats in report["validation"]["persistent"]["topk"].items():
        lines.append(f"| `{k}` | {int(stats['n'])} | {_fmt_pct(stats['precision'])} |")
    lines.extend(
        [
            "",
            "## Support Cohorts",
            "",
            "| Cohort | Rows | Useful Precision | Persistent Precision |",
            "|---|---:|---:|---:|",
        ]
    )
    for cohort in report["support_cohorts"]:
        lines.append(
            f"| `{cohort['name']}` | {int(cohort['n'])} | {_fmt_pct(cohort['useful_precision'])} | {_fmt_pct(cohort['persistent_precision'])} |"
        )
    lines.extend(
        [
            "",
            "## Top Persistence Features",
            "",
            "| Feature | Value | Weight | Pos | Neg |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for item in report["model"]["persistent_top_positive"]:
        lines.append(
            f"| `{item['field']}` | `{item['value']}` | {float(item['weight']):.2f} | {int(item['pos_count'])} | {int(item['neg_count'])} |"
        )
    lines.extend(
        [
            "",
            "## Live Queue",
            "",
            "| Symbol | Mint | Composite | Persistence | Useful | Source | MCap | Age0 | Mom5m0 | Hits | NetSOL |",
            "|---|---|---:|---:|---:|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in report["live"]:
        lines.append(
            f"| {row['symbol']} | `{row['mint']}` | {float(row['composite_score']):.1f} | {float(row['persistent_score']):.1f} | "
            f"{float(row['useful_score']):.1f} | `{row['signal_source']}` | {_fmt_num(row.get('mcap0'), 0)} | "
            f"{_fmt_num(row.get('pair_age_min0'), 1)} | {_fmt_num(row.get('mom5m0'), 1)} | "
            f"{_fmt_num(row.get('hits0'), 0)} | {_fmt_num(row.get('net_sol_in0'), 2)} |"
        )
    lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor persistence in the late_slow_expansion regime.")
    parser.add_argument("--dataset", type=Path, default=DATASET_CSV)
    parser.add_argument("--train-hours", type=float, default=72.0)
    parser.add_argument("--validate-hours", type=float, default=24.0)
    parser.add_argument("--live-lookback-min", type=float, default=120.0)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=OUT_MD)
    args = parser.parse_args()

    baseline = _load_module("meme_anchor_baseline_model_module", BASELINE_MODEL_PATH)
    rows = baseline.load_rows(args.dataset)
    now = time.time()
    train_since_ts = now - ((float(args.train_hours) + float(args.validate_hours)) * 3600.0)
    validate_cutoff_ts = now - (float(args.validate_hours) * 3600.0)

    train_rows_all = [row for row in rows if train_since_ts <= float(row["signal_ts"]) < validate_cutoff_ts]
    validation_rows_all = [row for row in rows if float(row["signal_ts"]) >= validate_cutoff_ts]

    train_rows = _subset(train_rows_all, TARGET_REGIME)
    validation_rows = _subset(validation_rows_all, TARGET_REGIME)

    useful_model = baseline.fit_model(train_rows, target_field="label_useful")
    persistent_model = baseline.fit_model(train_rows, target_field="label_persistent")
    useful_eval = baseline.evaluate_model(useful_model, validation_rows, target_field="label_useful")
    persistent_eval = baseline.evaluate_model(persistent_model, validation_rows, target_field="label_persistent")

    support_cohorts = []
    scored_validation = []
    for row in validation_rows:
        useful_score = baseline.score_row(useful_model, row)
        persistent_score = baseline.score_row(persistent_model, row)
        scored_validation.append(
            {
                "row": row,
                "useful_score": float(useful_score["score"]),
                "persistent_score": float(persistent_score["score"]),
            }
        )
    for name, predicate in (
        ("persistent>=55", lambda item: item["persistent_score"] >= 55.0),
        ("persistent>=55 & useful>=60", lambda item: item["persistent_score"] >= 55.0 and item["useful_score"] >= 60.0),
        ("composite>=60", lambda item: ((0.7 * item["persistent_score"]) + (0.3 * item["useful_score"])) >= 60.0),
    ):
        subset = [item["row"] for item in scored_validation if predicate(item)]
        support_cohorts.append(
            {
                "name": name,
                "n": len(subset),
                "useful_precision": (
                    sum(int(row.get("label_useful") or 0) for row in subset) / len(subset) if subset else 0.0
                ),
                "persistent_precision": (
                    sum(int(row.get("label_persistent") or 0) for row in subset) / len(subset) if subset else 0.0
                ),
            }
        )

    rank_module = baseline._load_rank_module()
    live_rows = baseline.load_live_rows(rank_module, since_ts=now - (float(args.live_lookback_min) * 60.0))
    live_rows = _subset(live_rows, TARGET_REGIME)
    live = []
    for row in live_rows:
        useful_score = baseline.score_row(useful_model, row)
        persistent_score = baseline.score_row(persistent_model, row)
        composite = (0.7 * float(persistent_score["score"])) + (0.3 * float(useful_score["score"]))
        live.append(
            {
                "symbol": row["symbol"],
                "mint": row["mint"],
                "signal_source": row["signal_source"],
                "mcap0": row["mcap0"],
                "pair_age_min0": row["pair_age_min0"],
                "mom5m0": row["mom5m0"],
                "hits0": row["hits0"],
                "net_sol_in0": row["net_sol_in0"],
                "useful_score": float(useful_score["score"]),
                "persistent_score": float(persistent_score["score"]),
                "composite_score": composite,
            }
        )
    live.sort(key=lambda row: float(row["composite_score"]), reverse=True)
    live = live[: int(args.top)]

    report = {
        "generated_at": now,
        "dataset_path": str(args.dataset),
        "config": {
            "train_rows": len(train_rows_all),
            "validation_rows": len(validation_rows_all),
            "target_regime": TARGET_REGIME,
            "train_hours": float(args.train_hours),
            "validate_hours": float(args.validate_hours),
            "live_lookback_min": float(args.live_lookback_min),
        },
        "subset": {
            "train_rows": len(train_rows),
            "train_useful_positives": sum(int(row.get("label_useful") or 0) for row in train_rows),
            "train_persistent_positives": sum(int(row.get("label_persistent") or 0) for row in train_rows),
            "validation_rows": len(validation_rows),
            "validation_useful_baseline": useful_eval["baseline_precision"],
            "validation_persistent_baseline": persistent_eval["baseline_precision"],
        },
        "model": {
            "persistent_top_positive": persistent_model["top_positive"][:20],
            "useful_top_positive": useful_model["top_positive"][:20],
        },
        "validation": {
            "useful": useful_eval,
            "persistent": persistent_eval,
        },
        "support_cohorts": support_cohorts,
        "live": live,
    }

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_md(args.out_md, report)
    print(
        f"late_slow_persistence_monitor: train_subset={len(train_rows)} validation_subset={len(validation_rows)} "
        f"live={len(live)} train_persistent={report['subset']['train_persistent_positives']}"
    )


if __name__ == "__main__":
    main()
