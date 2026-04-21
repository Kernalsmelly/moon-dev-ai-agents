#!/usr/bin/env python3
"""Train useful-winner models per source family and score the live tape by family."""

from __future__ import annotations

import argparse
import importlib.util
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
DATASET_CSV = BASE / "data" / "meme_reports" / "meme_anchor_dataset.csv"
BASELINE_MODEL_PATH = BASE / "scripts" / "meme_anchor_baseline_model.py"
OUT_JSON = BASE / "data" / "meme_reports" / "meme_useful_regime_monitor.json"
OUT_MD = BASE / "data" / "meme_reports" / "meme_useful_regime_monitor.md"


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


def _family_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    useful = (sum(int(row.get("label_useful") or 0) for row in rows) / n) if n else 0.0
    persistent = (sum(int(row.get("label_persistent") or 0) for row in rows) / n) if n else 0.0
    return {"n": n, "useful_precision": useful, "persistent_precision": persistent}


def _write_md(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Useful Regime Monitor",
        "",
        "Family-specific useful-winner models scored on validation anchors and the live tape.",
        "",
        "## Dataset",
        "",
        f"- Dataset: `{report['dataset_path']}`",
        f"- Train rows: `{report['config']['train_rows']}`",
        f"- Validation rows: `{report['config']['validation_rows']}`",
        f"- Live lookback: `{report['config']['live_lookback_min']}m`",
        "",
        "## Family Validation",
        "",
        "| Family | Model | Train Rows | Train Pos | Validation Rows | Baseline Useful | Baseline Persistent | >=70 Useful | Top10 Useful |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for family, row in report["family_validation"].items():
        lines.append(
            f"| `{family}` | `{row['model_kind']}` | {int(row['train_rows'])} | {int(row['train_positive_rows'])} | "
            f"{int(row['validation_rows'])} | {_fmt_pct(row['baseline_useful_precision'])} | "
            f"{_fmt_pct(row['baseline_persistent_precision'])} | {_fmt_pct(row['threshold_70_precision'])} | "
            f"{_fmt_pct(row['top10_precision'])} |"
        )

    lines.extend(
        [
            "",
            "## Validation Regimes",
            "",
            "| Regime | Rows | Useful Precision | Persistent Precision |",
            "|---|---:|---:|---:|",
        ]
    )
    for regime, row in report["validation_regimes"].items():
        lines.append(
            f"| `{regime}` | {int(row['n'])} | {_fmt_pct(row['useful_precision'])} | {_fmt_pct(row['persistent_precision'])} |"
        )

    for family, rows in report["live_by_family"].items():
        lines.extend(
            [
                "",
                f"## Live `{family}` Queue",
                "",
                "| Symbol | Mint | Useful Score | Regime | Source | MCap | Age0 | Mom5m0 | Hits | NetSOL |",
                "|---|---|---:|---|---|---:|---:|---:|---:|---:|",
            ]
        )
        for row in rows:
            lines.append(
                f"| {row['symbol']} | `{row['mint']}` | {float(row['score']):.1f} | "
                f"`{row['persistence_regime0']}` | `{row['signal_source']}` | "
                f"{_fmt_num(row.get('mcap0'), 0)} | {_fmt_num(row.get('pair_age_min0'), 1)} | "
                f"{_fmt_num(row.get('mom5m0'), 1)} | {_fmt_num(row.get('hits0'), 0)} | "
                f"{_fmt_num(row.get('net_sol_in0'), 2)} |"
            )
    lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Train family-specific useful-winner models.")
    parser.add_argument("--dataset", type=Path, default=DATASET_CSV)
    parser.add_argument("--train-hours", type=float, default=72.0)
    parser.add_argument("--validate-hours", type=float, default=24.0)
    parser.add_argument("--live-lookback-min", type=float, default=120.0)
    parser.add_argument("--min-train-rows", type=int, default=30)
    parser.add_argument("--min-train-pos", type=int, default=8)
    parser.add_argument("--top", type=int, default=10)
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

    global_model = baseline.fit_model(train_rows, target_field="label_useful")
    families = sorted({str(row.get("source_family") or "unknown") for row in rows})

    family_models: dict[str, dict[str, Any]] = {}
    family_validation: dict[str, dict[str, Any]] = {}
    for family in families:
        fam_train = [row for row in train_rows if str(row.get("source_family") or "unknown") == family]
        fam_val = [row for row in validation_rows if str(row.get("source_family") or "unknown") == family]
        train_pos = sum(int(row.get("label_useful") or 0) for row in fam_train)
        if len(fam_train) >= int(args.min_train_rows) and train_pos >= int(args.min_train_pos):
            model = baseline.fit_model(fam_train, target_field="label_useful")
            model_kind = "family"
        else:
            model = global_model
            model_kind = "global_fallback"
        family_models[family] = model
        evaluation = baseline.evaluate_model(model, fam_val, target_field="label_useful")
        family_validation[family] = {
            "model_kind": model_kind,
            "train_rows": len(fam_train),
            "train_positive_rows": train_pos,
            "validation_rows": len(fam_val),
            "baseline_useful_precision": (
                sum(int(row.get("label_useful") or 0) for row in fam_val) / len(fam_val) if fam_val else 0.0
            ),
            "baseline_persistent_precision": (
                sum(int(row.get("label_persistent") or 0) for row in fam_val) / len(fam_val) if fam_val else 0.0
            ),
            "threshold_70_precision": float(evaluation["thresholds"]["70"]["precision"]) if fam_val else 0.0,
            "top10_precision": float(evaluation["topk"]["10"]["precision"]) if fam_val else 0.0,
        }

    validation_regimes: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in validation_rows:
        grouped[str(row.get("persistence_regime0") or "missing")].append(row)
    for regime, items in sorted(grouped.items()):
        validation_regimes[regime] = _family_summary(items)

    live_rows = baseline.load_live_rows(
        baseline._load_rank_module(), since_ts=now - (float(args.live_lookback_min) * 60.0)
    )
    live_by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in live_rows:
        family = str(row.get("source_family") or "unknown")
        model = family_models.get(family, global_model)
        score = baseline.score_row(model, row)
        live_by_family[family].append(
            {
                "symbol": row["symbol"],
                "mint": row["mint"],
                "signal_source": row["signal_source"],
                "persistence_regime0": row["persistence_regime0"],
                "mcap0": row["mcap0"],
                "pair_age_min0": row["pair_age_min0"],
                "mom5m0": row["mom5m0"],
                "hits0": row["hits0"],
                "net_sol_in0": row["net_sol_in0"],
                "score": score["score"],
            }
        )
    for family in list(live_by_family):
        live_by_family[family].sort(key=lambda row: float(row["score"]), reverse=True)
        live_by_family[family] = live_by_family[family][: int(args.top)]

    report = {
        "generated_at": now,
        "dataset_path": str(args.dataset),
        "config": {
            "train_rows": len(train_rows),
            "validation_rows": len(validation_rows),
            "train_hours": float(args.train_hours),
            "validate_hours": float(args.validate_hours),
            "live_lookback_min": float(args.live_lookback_min),
            "min_train_rows": int(args.min_train_rows),
            "min_train_pos": int(args.min_train_pos),
        },
        "family_validation": family_validation,
        "validation_regimes": validation_regimes,
        "live_by_family": live_by_family,
    }

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_md(args.out_md, report)
    print(
        f"useful_regime_monitor: train={len(train_rows)} validation={len(validation_rows)} "
        f"families={len(family_validation)} live_rows={sum(len(v) for v in live_by_family.values())}"
    )


if __name__ == "__main__":
    main()
