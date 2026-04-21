#!/usr/bin/env python3
"""Backtest survivor-promotion rules on earliest-useful winners.

Purpose:
- start from earliest-useful winners (coins that reached +50% within 15m)
- inspect what they looked like at 30m and 60m
- quantify when a coin has earned "more time" versus when it should be treated as fading
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
OUTCOMES = BASE / "data" / "signal_outcomes.jsonl"
PERSISTENCE_REPORT_PATH = BASE / "scripts" / "meme_winner_persistence_report.py"
OUT_JSON = BASE / "data" / "meme_reports" / "meme_promotion_rule_report.json"
OUT_MD = BASE / "data" / "meme_reports" / "meme_promotion_rule_report.md"

CHECKPOINTS = (
    (1800, "30m"),
    (3600, "60m"),
)


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100.0:.1f}%"


def _fmt_num(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def _median(values: list[float | None]) -> float | None:
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not clean:
        return None
    return float(statistics.median(clean))


def _checkpoint_state(row: dict[str, Any], checkpoint_s: int) -> dict[str, Any] | None:
    ret = _to_float(row.get(f"ret_{checkpoint_s}s"))
    max_ret_15m = _to_float(row.get("max_ret_900s"))
    if ret is None:
        return None
    retention_vs_15m = None
    if max_ret_15m not in (None, 0.0):
        retention_vs_15m = ret / float(max_ret_15m)

    if checkpoint_s >= 3600 and ret >= 0.25 and (retention_vs_15m or 0.0) >= 0.50:
        state = "holding_strong"
    elif ret >= 0.10 and (retention_vs_15m or 0.0) >= 0.30:
        state = "still_alive"
    elif ret > 0.0 and (max_ret_15m or 0.0) >= 0.50:
        state = "fragile_but_green"
    elif ret <= 0.0:
        state = "fading"
    else:
        state = "unclear"

    return {
        "state": state,
        "checkpoint_ret": ret,
        "retention_vs_15m": retention_vs_15m,
    }


def _state_stats(rows: list[dict[str, Any]], baseline_precision: float) -> dict[str, Any]:
    counts = Counter(str(row.get("persistence_class") or "unknown") for row in rows)
    persistent_count = counts.get("persistent_runner", 0)
    precision = (persistent_count / len(rows)) if rows else 0.0
    return {
        "n": len(rows),
        "persistent_count": persistent_count,
        "persistent_precision": precision,
        "lift_vs_baseline": (precision / baseline_precision) if baseline_precision > 0 else None,
        "median_checkpoint_ret": _median([_to_float(row.get("checkpoint_ret")) for row in rows]),
        "median_checkpoint_retention": _median([_to_float(row.get("checkpoint_retention")) for row in rows]),
        "median_ret_6h": _median([_to_float(row.get("ret_6h")) for row in rows]),
        "class_counts": dict(counts),
    }


def _rule_stats(
    rows: list[dict[str, Any]],
    *,
    baseline_precision: float,
    label: str,
    rule_type: str,
    description: str,
) -> dict[str, Any]:
    stats = _state_stats(rows, baseline_precision)
    return {
        "label": label,
        "type": rule_type,
        "description": description,
        **stats,
    }


def _write_md(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    lines = [
        "# Promotion Rule Report",
        "",
        "Historical backtest for when an earliest-useful winner has earned more time versus when it should be treated as fading.",
        "",
        "## Summary",
        "",
        f"- Window: `{_fmt_num(report['window_hours'], 0)}h`",
        f"- Matured earliest-useful winners: `{summary['matured_earliest_useful_winners']}`",
        f"- Baseline persistent precision: `{_fmt_pct(summary['baseline_persistent_precision'])}`",
        f"- Best promotion rule: `{summary['best_promotion_rule']['label']}` -> `{_fmt_pct(summary['best_promotion_rule']['persistent_precision'])}` persistence (`{_fmt_num(summary['best_promotion_rule']['lift_vs_baseline'], 2)}x` lift)",
        f"- Strongest cut signal: `{summary['strongest_cut_signal']['label']}` -> `{_fmt_pct(summary['strongest_cut_signal']['persistent_precision'])}` persistence",
        "",
    ]

    for checkpoint_label in ("30m", "60m"):
        checkpoint = report["checkpoints"][checkpoint_label]
        lines.extend(
            [
                f"## Checkpoint {checkpoint_label}",
                "",
                f"- Rows with checkpoint data: `{checkpoint['rows_with_checkpoint']}`",
                "",
                "| State | N | Persistent | Precision | Lift | Median Ret | Median Retention vs 15m | Median 6h Ret |",
                "|---|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for state in ("holding_strong", "still_alive", "fragile_but_green", "unclear", "fading"):
            row = checkpoint["states"].get(state)
            if not row:
                continue
            lines.append(
                f"| `{state}` | {row['n']} | {row['persistent_count']} | {_fmt_pct(row['persistent_precision'])} | "
                f"{_fmt_num(row['lift_vs_baseline'], 2)}x | {_fmt_pct(row['median_checkpoint_ret'])} | "
                f"{_fmt_pct(row['median_checkpoint_retention'])} | {_fmt_pct(row['median_ret_6h'])} |"
            )
        lines.append("")

    lines.extend(
        [
            "## Candidate Rules",
            "",
            "| Rule | Type | Selected | Persistent | Precision | Lift | Median 6h Ret |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in report["rules"]:
        lines.append(
            f"| {row['label']} | `{row['type']}` | {row['n']} | {row['persistent_count']} | "
            f"{_fmt_pct(row['persistent_precision'])} | {_fmt_num(row['lift_vs_baseline'], 2)}x | {_fmt_pct(row['median_ret_6h'])} |"
        )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            f"- `{summary['best_promotion_rule']['label']}` is the best current promotion checkpoint in this backtest.",
            f"- `{summary['strongest_cut_signal']['label']}` is the clearest protection signal; these names almost never become persistent.",
            "- This report is about survivor promotion after the first move, not first-entry selection.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def build_report(
    rows_by_mint: dict[str, list[dict[str, Any]]],
    persistence_module: Any,
    *,
    winner_ret: float,
    persistent_ret: float,
    persistent_retain: float,
    round_trip_retain: float,
    window_hours: float,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for mint, mint_rows in rows_by_mint.items():
        useful = persistence_module.first_useful(mint_rows, winner_ret)
        if useful is None:
            continue
        persistence_class, retention_6h = persistence_module.classify_persistence(
            useful,
            persistent_ret=persistent_ret,
            persistent_retain=persistent_retain,
            round_trip_retain=round_trip_retain,
        )
        if persistence_class == "pending_6h":
            continue
        record = {
            "mint": mint,
            "symbol": useful.get("symbol") or "n/a",
            "signal_source": useful.get("signal_source") or "unknown",
            "persistence_class": persistence_class,
            "ret_6h": _to_float(useful.get("ret_21600s")),
            "retention_6h": retention_6h,
            "max_ret_15m": _to_float(useful.get("max_ret_900s")),
            "max_ret_6h": _to_float(useful.get("max_ret_21600s")),
        }
        for checkpoint_s, checkpoint_label in CHECKPOINTS:
            cp = _checkpoint_state(useful, checkpoint_s)
            if cp is None:
                record[f"{checkpoint_label}_state"] = None
                record[f"{checkpoint_label}_checkpoint_ret"] = None
                record[f"{checkpoint_label}_checkpoint_retention"] = None
                continue
            record[f"{checkpoint_label}_state"] = cp["state"]
            record[f"{checkpoint_label}_checkpoint_ret"] = cp["checkpoint_ret"]
            record[f"{checkpoint_label}_checkpoint_retention"] = cp["retention_vs_15m"]
        rows.append(record)

    baseline_precision = (
        sum(1 for row in rows if row["persistence_class"] == "persistent_runner") / len(rows)
        if rows
        else 0.0
    )

    checkpoint_report: dict[str, Any] = {}
    for _checkpoint_s, checkpoint_label in CHECKPOINTS:
        state_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        with_checkpoint = 0
        for row in rows:
            state = row.get(f"{checkpoint_label}_state")
            if not state:
                continue
            with_checkpoint += 1
            state_rows[str(state)].append(
                {
                    "persistence_class": row["persistence_class"],
                    "checkpoint_ret": row.get(f"{checkpoint_label}_checkpoint_ret"),
                    "checkpoint_retention": row.get(f"{checkpoint_label}_checkpoint_retention"),
                    "ret_6h": row.get("ret_6h"),
                }
            )
        checkpoint_report[checkpoint_label] = {
            "rows_with_checkpoint": with_checkpoint,
            "states": {
                state: _state_stats(state_group, baseline_precision)
                for state, state_group in sorted(state_rows.items())
            },
        }

    rules: list[dict[str, Any]] = []
    rule_defs: list[tuple[str, str, str, Callable[[dict[str, Any]], bool]]] = [
        (
            "promote_30m_still_alive_plus",
            "30m still_alive+",
            "promotion",
            lambda row: row.get("30m_state") in {"still_alive"},
        ),
        (
            "promote_60m_holding_strong",
            "60m holding_strong",
            "promotion",
            lambda row: row.get("60m_state") == "holding_strong",
        ),
        (
            "promote_60m_not_fading",
            "60m not_fading",
            "promotion",
            lambda row: row.get("60m_state") in {"holding_strong", "still_alive", "fragile_but_green", "unclear"},
        ),
        (
            "promote_30m_and_60m",
            "30m alive + 60m strong",
            "promotion",
            lambda row: row.get("30m_state") in {"still_alive"} and row.get("60m_state") == "holding_strong",
        ),
        (
            "cut_30m_fading",
            "30m fading",
            "protection",
            lambda row: row.get("30m_state") == "fading",
        ),
        (
            "cut_60m_fading",
            "60m fading",
            "protection",
            lambda row: row.get("60m_state") == "fading",
        ),
    ]

    for key, label, rule_type, predicate in rule_defs:
        selected = [row for row in rows if predicate(row)]
        if not selected:
            continue
        rule_row = _rule_stats(
            selected,
            baseline_precision=baseline_precision,
            label=label,
            rule_type=rule_type,
            description=key,
        )
        rule_row["key"] = key
        rules.append(rule_row)

    best_promotion_rule = max(
        (row for row in rules if row["type"] == "promotion"),
        key=lambda row: (float(row["persistent_precision"]), float(row["n"])),
        default={
            "label": "n/a",
            "persistent_precision": 0.0,
            "lift_vs_baseline": None,
        },
    )
    strongest_cut_signal = min(
        (row for row in rules if row["type"] == "protection"),
        key=lambda row: (float(row["persistent_precision"]), -float(row["n"])),
        default={
            "label": "n/a",
            "persistent_precision": None,
            "lift_vs_baseline": None,
        },
    )

    return {
        "generated_at": time.time(),
        "window_hours": window_hours,
        "summary": {
            "matured_earliest_useful_winners": len(rows),
            "persistent_runners": sum(1 for row in rows if row["persistence_class"] == "persistent_runner"),
            "baseline_persistent_precision": baseline_precision,
            "best_promotion_rule": best_promotion_rule,
            "strongest_cut_signal": strongest_cut_signal,
        },
        "checkpoints": checkpoint_report,
        "rules": sorted(rules, key=lambda row: (-float(row["persistent_precision"]), -int(row["n"]), row["label"])),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest survivor-promotion rules for meme winners.")
    parser.add_argument("--since-hours", type=float, default=168.0)
    parser.add_argument("--winner-ret", type=float, default=0.50)
    parser.add_argument("--persistent-ret", type=float, default=1.00)
    parser.add_argument("--persistent-retain", type=float, default=0.50)
    parser.add_argument("--round-trip-retain", type=float, default=0.15)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=OUT_MD)
    args = parser.parse_args()

    persistence_module = _load_module("meme_winner_persistence_report_module", PERSISTENCE_REPORT_PATH)
    since_ts = time.time() - (float(args.since_hours) * 3600.0)
    rows_by_mint = persistence_module.load_rows(OUTCOMES, since_ts)
    report = build_report(
        rows_by_mint,
        persistence_module,
        winner_ret=float(args.winner_ret),
        persistent_ret=float(args.persistent_ret),
        persistent_retain=float(args.persistent_retain),
        round_trip_retain=float(args.round_trip_retain),
        window_hours=float(args.since_hours),
    )

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_md(args.out_md, report)
    print(
        "meme_promotion_rule_report: "
        f"matured={report['summary']['matured_earliest_useful_winners']} "
        f"baseline={report['summary']['baseline_persistent_precision']:.4f}"
    )


if __name__ == "__main__":
    main()
