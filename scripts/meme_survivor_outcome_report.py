#!/usr/bin/env python3
"""Measure survivor-grade outcomes beyond strict persistent runners.

Survivor-grade means:
- persistent_runner
- partial_persistence

This lets us quantify whether a name was still worth respecting later,
even if it did not become a full persistent runner.
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
from typing import Any

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
OUTCOMES = BASE / "data" / "signal_outcomes.jsonl"
PERSISTENCE_REPORT_PATH = BASE / "scripts" / "meme_winner_persistence_report.py"
OUT_JSON = BASE / "data" / "meme_reports" / "meme_survivor_outcome_report.json"
OUT_MD = BASE / "data" / "meme_reports" / "meme_survivor_outcome_report.md"

CHECKPOINTS = (
    (1800, "30m"),
    (3600, "60m"),
)
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
        if value is None or isinstance(value, bool):
            return None
        out = float(value)
        if math.isfinite(out):
            return out
    except Exception:
        return None
    return None


def _median(values: list[float | None]) -> float | None:
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not clean:
        return None
    return float(statistics.median(clean))


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100.0:.1f}%"


def _fmt_num(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def _checkpoint_state(row: dict[str, Any], checkpoint_s: int) -> str | None:
    ret = _to_float(row.get(f"ret_{checkpoint_s}s"))
    max_ret_15m = _to_float(row.get("max_ret_900s"))
    if ret is None:
        return None
    retention_vs_15m = None
    if max_ret_15m not in (None, 0.0):
        retention_vs_15m = ret / float(max_ret_15m)
    if checkpoint_s >= 3600 and ret >= 0.25 and (retention_vs_15m or 0.0) >= 0.50:
        return "holding_strong"
    if ret >= 0.10 and (retention_vs_15m or 0.0) >= 0.30:
        return "still_alive"
    if ret > 0.0 and (max_ret_15m or 0.0) >= 0.50:
        return "fragile_but_green"
    if ret <= 0.0:
        return "fading"
    return "unclear"


def _build_rows(
    rows_by_mint: dict[str, list[dict[str, Any]]],
    persistence_module: Any,
    *,
    winner_ret: float,
    persistent_ret: float,
    persistent_retain: float,
    round_trip_retain: float,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
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
        row = {
            "mint": mint,
            "symbol": useful.get("symbol") or "n/a",
            "signal_source": useful.get("signal_source") or "unknown",
            "mcap0": _to_float(useful.get("mcap0")),
            "pair_age_min0": _to_float(useful.get("pair_age_min0")),
            "mom5m0": _to_float(useful.get("mom5m0")),
            "hits0": _to_float(useful.get("hits0")),
            "net_sol_in0": _to_float(useful.get("net_sol_in0")),
            "mover_pattern0": useful.get("mover_pattern0") or "unknown",
            "archetype_15m": useful.get("archetype_15m") or "unknown",
            "max_ret_15m": _to_float(useful.get("max_ret_900s")),
            "max_ret_all": _to_float(useful.get("max_ret_all")),
            "ret_6h": _to_float(useful.get("ret_21600s")),
            "retention_6h": retention_6h,
            "persistence_class": persistence_class,
            "survivor_grade": persistence_class in SURVIVOR_CLASSES,
        }
        for checkpoint_s, checkpoint_label in CHECKPOINTS:
            row[f"{checkpoint_label}_state"] = _checkpoint_state(useful, checkpoint_s)
        out.append(row)
    return out


def _profile(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "n": len(rows),
        "median_mcap0": _median([_to_float(r.get("mcap0")) for r in rows]),
        "median_age0": _median([_to_float(r.get("pair_age_min0")) for r in rows]),
        "median_mom5m0": _median([_to_float(r.get("mom5m0")) for r in rows]),
        "median_hits0": _median([_to_float(r.get("hits0")) for r in rows]),
        "median_net_sol_in0": _median([_to_float(r.get("net_sol_in0")) for r in rows]),
        "median_max_ret_15m": _median([_to_float(r.get("max_ret_15m")) for r in rows]),
        "median_ret_6h": _median([_to_float(r.get("ret_6h")) for r in rows]),
        "median_retention_6h": _median([_to_float(r.get("retention_6h")) for r in rows]),
        "source_counts": dict(Counter(str(r.get("signal_source") or "unknown") for r in rows)),
        "pattern_counts": dict(Counter(str(r.get("mover_pattern0") or "unknown") for r in rows)),
    }


def _rule_summary(label: str, rows: list[dict[str, Any]], *, baseline_survivor: float, baseline_persistent: float) -> dict[str, Any]:
    survivor_count = sum(1 for row in rows if row["survivor_grade"])
    persistent_count = sum(1 for row in rows if row["persistence_class"] == "persistent_runner")
    class_counts = Counter(str(row["persistence_class"]) for row in rows)
    survivor_precision = (survivor_count / len(rows)) if rows else 0.0
    persistent_precision = (persistent_count / len(rows)) if rows else 0.0
    return {
        "label": label,
        "n": len(rows),
        "survivor_count": survivor_count,
        "persistent_count": persistent_count,
        "survivor_precision": survivor_precision,
        "persistent_precision": persistent_precision,
        "survivor_lift": (survivor_precision / baseline_survivor) if baseline_survivor > 0 else None,
        "persistent_lift": (persistent_precision / baseline_persistent) if baseline_persistent > 0 else None,
        "median_ret_6h": _median([_to_float(r.get("ret_6h")) for r in rows]),
        "class_counts": dict(class_counts),
    }


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
    rows = _build_rows(
        rows_by_mint,
        persistence_module,
        winner_ret=winner_ret,
        persistent_ret=persistent_ret,
        persistent_retain=persistent_retain,
        round_trip_retain=round_trip_retain,
    )
    baseline_survivor = (sum(1 for row in rows if row["survivor_grade"]) / len(rows)) if rows else 0.0
    baseline_persistent = (
        sum(1 for row in rows if row["persistence_class"] == "persistent_runner") / len(rows)
        if rows
        else 0.0
    )

    class_profiles = {
        klass: _profile([row for row in rows if row["persistence_class"] == klass])
        for klass in sorted(Counter(row["persistence_class"] for row in rows))
    }
    survivor_profile = _profile([row for row in rows if row["survivor_grade"]])

    rules: list[dict[str, Any]] = []
    rule_defs = [
        ("30m still_alive+", lambda row: row.get("30m_state") in {"still_alive"}),
        ("60m holding_strong", lambda row: row.get("60m_state") == "holding_strong"),
        ("60m not_fading", lambda row: row.get("60m_state") in {"holding_strong", "still_alive", "fragile_but_green", "unclear"}),
        ("30m alive + 60m strong", lambda row: row.get("30m_state") in {"still_alive"} and row.get("60m_state") == "holding_strong"),
        ("60m fading", lambda row: row.get("60m_state") == "fading"),
        ("30m fading", lambda row: row.get("30m_state") == "fading"),
    ]
    for label, predicate in rule_defs:
        selected = [row for row in rows if predicate(row)]
        if selected:
            rules.append(
                _rule_summary(
                    label,
                    selected,
                    baseline_survivor=baseline_survivor,
                    baseline_persistent=baseline_persistent,
                )
            )

    best_survivor_rule = max(
        (row for row in rules if "fading" not in row["label"]),
        key=lambda row: (float(row["survivor_precision"]), float(row["n"])),
        default={"label": "n/a", "survivor_precision": 0.0, "survivor_lift": None},
    )

    return {
        "generated_at": time.time(),
        "window_hours": window_hours,
        "summary": {
            "matured_earliest_useful_winners": len(rows),
            "survivor_grade_count": sum(1 for row in rows if row["survivor_grade"]),
            "persistent_runner_count": sum(1 for row in rows if row["persistence_class"] == "persistent_runner"),
            "partial_persistence_count": sum(1 for row in rows if row["persistence_class"] == "partial_persistence"),
            "baseline_survivor_precision": baseline_survivor,
            "baseline_persistent_precision": baseline_persistent,
            "best_survivor_rule": best_survivor_rule,
        },
        "class_profiles": class_profiles,
        "survivor_profile": survivor_profile,
        "rules": sorted(rules, key=lambda row: (-float(row["survivor_precision"]), -int(row["n"]), row["label"])),
    }


def write_md(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    lines = [
        "# Survivor Outcome Report",
        "",
        "Quantifies whether `partial_persistence` behaves like a meaningful survivor class rather than just leftover noise.",
        "",
        "## Summary",
        "",
        f"- Window: `{_fmt_num(report['window_hours'], 0)}h`",
        f"- Matured earliest-useful winners: `{summary['matured_earliest_useful_winners']}`",
        f"- Survivor-grade count (`persistent_runner + partial_persistence`): `{summary['survivor_grade_count']}`",
        f"- Persistent runners: `{summary['persistent_runner_count']}`",
        f"- Partial persistence: `{summary['partial_persistence_count']}`",
        f"- Baseline survivor precision: `{_fmt_pct(summary['baseline_survivor_precision'])}`",
        f"- Baseline persistent precision: `{_fmt_pct(summary['baseline_persistent_precision'])}`",
        f"- Best survivor rule: `{summary['best_survivor_rule']['label']}` -> `{_fmt_pct(summary['best_survivor_rule']['survivor_precision'])}` survivor precision (`{_fmt_num(summary['best_survivor_rule']['survivor_lift'], 2)}x` lift)",
        "",
        "## Class Profiles",
        "",
    ]

    survivor_profile = report["survivor_profile"]
    lines.extend(
        [
            "### `survivor_grade`",
            "",
            f"- Count: `{survivor_profile['n']}`",
            f"- Median mcap0: `{_fmt_num(survivor_profile['median_mcap0'], 0)}`",
            f"- Median age0: `{_fmt_num(survivor_profile['median_age0'], 1)} min`",
            f"- Median mom5m0: `{_fmt_num(survivor_profile['median_mom5m0'], 1)}%`",
            f"- Median max 15m return: `{_fmt_pct(survivor_profile['median_max_ret_15m'])}`",
            f"- Median 6h return: `{_fmt_pct(survivor_profile['median_ret_6h'])}`",
            f"- Median 6h retention: `{_fmt_pct(survivor_profile['median_retention_6h'])}`",
            "",
        ]
    )

    for klass, profile in report["class_profiles"].items():
        lines.extend(
            [
                f"### `{klass}`",
                "",
                f"- Count: `{profile['n']}`",
                f"- Median mcap0: `{_fmt_num(profile['median_mcap0'], 0)}`",
                f"- Median age0: `{_fmt_num(profile['median_age0'], 1)} min`",
                f"- Median mom5m0: `{_fmt_num(profile['median_mom5m0'], 1)}%`",
                f"- Median max 15m return: `{_fmt_pct(profile['median_max_ret_15m'])}`",
                f"- Median 6h return: `{_fmt_pct(profile['median_ret_6h'])}`",
                f"- Median 6h retention: `{_fmt_pct(profile['median_retention_6h'])}`",
                "",
            ]
        )

    lines.extend(
        [
            "## Candidate Rules",
            "",
            "| Rule | Selected | Survivor | Survivor Precision | Survivor Lift | Persistent Precision | Median 6h Ret |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in report["rules"]:
        lines.append(
            f"| {row['label']} | {row['n']} | {row['survivor_count']} | {_fmt_pct(row['survivor_precision'])} | "
            f"{_fmt_num(row['survivor_lift'], 2)}x | {_fmt_pct(row['persistent_precision'])} | {_fmt_pct(row['median_ret_6h'])} |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Quantify survivor-grade outcomes for meme signals.")
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
    write_md(args.out_md, report)
    print(
        "meme_survivor_outcome_report: "
        f"matured={report['summary']['matured_earliest_useful_winners']} "
        f"survivor={report['summary']['survivor_grade_count']}"
    )


if __name__ == "__main__":
    main()
