#!/usr/bin/env python3
"""Train simple baseline models on the anchor dataset and score live candidates."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
DATASET_CSV = BASE / "data" / "meme_reports" / "meme_anchor_dataset.csv"
MODULE_PATH = BASE / "scripts" / "meme_persistent_rank_monitor.py"
TAPE = BASE / "data" / "meme_launch_signals.jsonl"
OUT_JSON = BASE / "data" / "meme_reports" / "meme_anchor_baseline_model.json"
OUT_MD = BASE / "data" / "meme_reports" / "meme_anchor_baseline_model.md"


def _load_rank_module():
    spec = importlib.util.spec_from_file_location("meme_persistent_rank_monitor_module", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load module from {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        out = float(value)
        if math.isfinite(out):
            return out
    except Exception:
        return None
    return None


def _to_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except Exception:
        return None


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100.0:.1f}%"


def _fmt_num(value: float | None, digits: int = 0) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def load_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = [dict(row) for row in reader]
    for row in rows:
        row["signal_ts"] = _to_float(row.get("signal_ts")) or 0.0
        row["label_useful"] = int(float(row.get("label_useful") or 0))
        row["label_persistent"] = int(float(row.get("label_persistent") or 0))
    rows.sort(key=lambda row: float(row.get("signal_ts") or 0.0))
    return rows


def feature_fields(rows: list[dict[str, Any]]) -> list[str]:
    return sorted(key for key in rows[0].keys() if key.startswith("feat__")) if rows else []


def fit_model(rows: list[dict[str, Any]], *, target_field: str, alpha: float = 1.0) -> dict[str, Any]:
    fields = feature_fields(rows)
    pos_rows = [row for row in rows if int(row.get(target_field) or 0) == 1]
    neg_rows = [row for row in rows if int(row.get(target_field) or 0) == 0]
    pos_n = len(pos_rows)
    neg_n = len(neg_rows)

    field_vocab: dict[str, set[str]] = defaultdict(set)
    pos_counts: dict[tuple[str, str], int] = Counter()
    neg_counts: dict[tuple[str, str], int] = Counter()

    for row in rows:
        for field in fields:
            field_vocab[field].add(str(row.get(field) or "missing"))

    for row in pos_rows:
        for field in fields:
            pos_counts[(field, str(row.get(field) or "missing"))] += 1
    for row in neg_rows:
        for field in fields:
            neg_counts[(field, str(row.get(field) or "missing"))] += 1

    prior = math.log((pos_n + alpha) / (neg_n + alpha))
    weights: dict[tuple[str, str], float] = {}
    top_positive: list[dict[str, Any]] = []
    top_negative: list[dict[str, Any]] = []
    for field in fields:
        vocab = sorted(field_vocab[field])
        k = max(1, len(vocab))
        pos_den = pos_n + (alpha * k)
        neg_den = neg_n + (alpha * k)
        for value in vocab:
            pos_prob = (pos_counts[(field, value)] + alpha) / pos_den
            neg_prob = (neg_counts[(field, value)] + alpha) / neg_den
            weight = math.log(pos_prob / neg_prob)
            weights[(field, value)] = weight
            item = {
                "field": field,
                "value": value,
                "weight": weight,
                "pos_count": pos_counts[(field, value)],
                "neg_count": neg_counts[(field, value)],
            }
            if weight > 0:
                top_positive.append(item)
            elif weight < 0:
                top_negative.append(item)

    top_positive.sort(key=lambda item: item["weight"], reverse=True)
    top_negative.sort(key=lambda item: item["weight"])
    return {
        "target": target_field,
        "train_rows": len(rows),
        "positive_rows": pos_n,
        "positive_rate": (pos_n / len(rows)) if rows else 0.0,
        "fields": fields,
        "prior": prior,
        "weights": weights,
        "top_positive": top_positive[:25],
        "top_negative": top_negative[:25],
    }


def score_row(model: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    raw = float(model["prior"])
    matched: list[tuple[str, str, float]] = []
    for field in model["fields"]:
        value = str(row.get(field) or "missing")
        weight = float(model["weights"].get((field, value), 0.0))
        raw += weight
        matched.append((field, value, weight))
    prob = _sigmoid(raw)
    matched.sort(key=lambda item: abs(item[2]), reverse=True)
    return {
        "raw_score": raw,
        "prob": prob,
        "score": prob * 100.0,
        "top_matches": matched[:8],
    }


def evaluate_model(model: dict[str, Any], rows: list[dict[str, Any]], *, target_field: str) -> dict[str, Any]:
    scored = []
    for row in rows:
        result = score_row(model, row)
        scored.append({"row": row, **result})
    scored.sort(key=lambda item: item["score"], reverse=True)
    base = (sum(int(item["row"].get(target_field) or 0) for item in scored) / len(scored)) if scored else 0.0
    thresholds: dict[str, dict[str, Any]] = {}
    for threshold in (55, 60, 65, 70, 75, 80):
        subset = [item for item in scored if float(item["score"]) >= threshold]
        precision = (sum(int(item["row"].get(target_field) or 0) for item in subset) / len(subset)) if subset else 0.0
        thresholds[str(threshold)] = {"n": len(subset), "precision": precision}
    topk: dict[str, dict[str, Any]] = {}
    for k in (10, 20, 30, 50):
        subset = scored[:k]
        precision = (sum(int(item["row"].get(target_field) or 0) for item in subset) / len(subset)) if subset else 0.0
        topk[str(k)] = {"n": len(subset), "precision": precision}
    return {
        "n": len(scored),
        "baseline_precision": base,
        "thresholds": thresholds,
        "topk": topk,
        "top_rows": scored[:25],
    }


def load_live_rows(module: Any, *, since_ts: float) -> list[dict[str, Any]]:
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

    out: list[dict[str, Any]] = []
    for row in latest_by_mint.values():
        snapshot = module._snapshot_from_tape_row(row)
        features = module._features_from_snapshot(snapshot)
        feature_cols = {f"feat__{field}": value for field, value in features.items()}
        out.append(
            {
                "mint": snapshot["mint"],
                "symbol": snapshot["symbol"],
                "signal_ts": snapshot["signal_ts"],
                "signal_source": snapshot["signal_source"],
                "source_family": snapshot["source_family"],
                "persistence_regime0": snapshot["persistence_regime0"],
                "mcap0": snapshot["mcap0"],
                "pair_age_min0": snapshot["pair_age_min0"],
                "mom5m0": snapshot["mom5m0"],
                "hits0": snapshot["hits0"],
                "buys0": snapshot["buys0"],
                "net_sol_in0": snapshot["net_sol_in0"],
                "mover_pattern0": snapshot["mover_pattern0"],
                **feature_cols,
            }
        )
    out.sort(key=lambda row: float(row.get("signal_ts") or 0.0), reverse=True)
    return out


def _write_md(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Meme Anchor Baseline Model",
        "",
        "Simple additive likelihood baselines built from the anchor dataset.",
        "",
        "## Dataset",
        "",
        f"- CSV: `{report['dataset_path']}`",
        f"- Train rows: `{report['config']['train_rows']}`",
        f"- Validation rows: `{report['config']['validation_rows']}`",
        f"- Live lookback: `{report['config']['live_lookback_min']}m`",
        "",
    ]
    for target in ("useful", "persistent"):
        model = report["models"][target]
        validation = report["validation"][target]
        lines.extend(
            [
                f"## {target.title()} Model",
                "",
                f"- Train positives: `{model['positive_rows']}` / `{model['train_rows']}` (`{_fmt_pct(model['positive_rate'])}`)",
                f"- Validation baseline: `{_fmt_pct(validation['baseline_precision'])}`",
                "",
                "| Threshold | Rows | Precision |",
                "|---|---:|---:|",
            ]
        )
        for threshold, stats in validation["thresholds"].items():
            lines.append(f"| `>= {threshold}` | {int(stats['n'])} | {_fmt_pct(stats['precision'])} |")
        lines.extend(
            [
                "",
                "Top-k precision:",
                "",
                "| Top-k | Rows | Precision |",
                "|---|---:|---:|",
            ]
        )
        for k, stats in validation["topk"].items():
            lines.append(f"| `{k}` | {int(stats['n'])} | {_fmt_pct(stats['precision'])} |")
        lines.extend(
            [
                "",
                "Top positive feature weights:",
                "",
                "| Feature | Value | Weight | Pos | Neg |",
                "|---|---|---:|---:|---:|",
            ]
        )
        for item in model["top_positive"]:
            lines.append(
                f"| `{item['field']}` | `{item['value']}` | {item['weight']:.2f} | {int(item['pos_count'])} | {int(item['neg_count'])} |"
            )
        lines.extend(
            [
                "",
                f"## Live {target.title()} Leaderboard",
                "",
                "| Symbol | Mint | Score | Regime | Source | MCap | Age0 | Mom5m0 | Hits | NetSOL |",
                "|---|---|---:|---|---|---:|---:|---:|---:|---:|",
            ]
        )
        for row in report["live"][target]:
            lines.append(
                f"| {row['symbol']} | `{row['mint']}` | {float(row['score']):.1f} | `{row['persistence_regime0']}` | "
                f"`{row['signal_source']}` | {_fmt_num(_to_float(row.get('mcap0')), 0)} | "
                f"{_fmt_num(_to_float(row.get('pair_age_min0')), 1)} | {_fmt_num(_to_float(row.get('mom5m0')), 1)} | "
                f"{int(_to_int(row.get('hits0')) or 0)} | {_fmt_num(_to_float(row.get('net_sol_in0')), 2)} |"
            )
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Train simple baseline models on the meme anchor dataset.")
    parser.add_argument("--dataset", type=Path, default=DATASET_CSV)
    parser.add_argument("--train-hours", type=float, default=72.0)
    parser.add_argument("--validate-hours", type=float, default=24.0)
    parser.add_argument("--live-lookback-min", type=float, default=120.0)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=OUT_MD)
    args = parser.parse_args()

    module = _load_rank_module()
    rows = load_rows(args.dataset)
    now = time.time()
    train_since_ts = now - ((float(args.train_hours) + float(args.validate_hours)) * 3600.0)
    validate_cutoff_ts = now - (float(args.validate_hours) * 3600.0)
    train_rows = [row for row in rows if train_since_ts <= float(row["signal_ts"]) < validate_cutoff_ts]
    validation_rows = [row for row in rows if float(row["signal_ts"]) >= validate_cutoff_ts]

    models: dict[str, Any] = {}
    validation: dict[str, Any] = {}
    live_rows = load_live_rows(module, since_ts=now - (float(args.live_lookback_min) * 60.0))
    live_scores: dict[str, list[dict[str, Any]]] = {}

    for target_name, target_field in (("useful", "label_useful"), ("persistent", "label_persistent")):
        model = fit_model(train_rows, target_field=target_field)
        models[target_name] = {
            k: v for k, v in model.items() if k != "weights"
        }
        validation[target_name] = evaluate_model(model, validation_rows, target_field=target_field)
        scored_live: list[dict[str, Any]] = []
        for row in live_rows:
            score = score_row(model, row)
            scored_live.append(
                {
                    "mint": row["mint"],
                    "symbol": row["symbol"],
                    "signal_source": row["signal_source"],
                    "source_family": row["source_family"],
                    "persistence_regime0": row["persistence_regime0"],
                    "mcap0": row["mcap0"],
                    "pair_age_min0": row["pair_age_min0"],
                    "mom5m0": row["mom5m0"],
                    "hits0": row["hits0"],
                    "buys0": row["buys0"],
                    "net_sol_in0": row["net_sol_in0"],
                    "score": score["score"],
                    "prob": score["prob"],
                    "top_matches": score["top_matches"],
                }
            )
        scored_live.sort(key=lambda row: float(row["score"]), reverse=True)
        live_scores[target_name] = scored_live[: int(args.top)]

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
        "models": models,
        "validation": validation,
        "live": live_scores,
    }

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_md(args.out_md, report)
    print(
        f"meme_anchor_baseline_model: train={len(train_rows)} validation={len(validation_rows)} "
        f"useful_train_pos={models['useful']['positive_rows']} persistent_train_pos={models['persistent']['positive_rows']}"
    )


if __name__ == "__main__":
    main()
