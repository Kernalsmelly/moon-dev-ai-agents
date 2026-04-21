#!/usr/bin/env python3
"""Retrospective dry-run report for the regime-aware persistence watchlists."""

from __future__ import annotations

import argparse
import importlib.util
import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
MODULE_PATH = BASE / "scripts" / "meme_persistent_rank_monitor.py"
OUT_JSON = BASE / "data" / "meme_reports" / "dry_run_selection_report.json"
OUT_MD = BASE / "data" / "meme_reports" / "dry_run_selection_report.md"


def _load_rank_module():
    spec = importlib.util.spec_from_file_location("meme_persistent_rank_monitor_module", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load module from {MODULE_PATH}")
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


def _is_useful_winner(row: dict[str, Any]) -> bool:
    klass = str(row.get("persistence_class") or "unknown")
    return klass in {"persistent_runner", "round_trip_or_spike", "partial_persistence"}


def _summarize_selection(rows: list[dict[str, Any]]) -> dict[str, Any]:
    persistent_n = sum(1 for row in rows if bool(row.get("persistent")))
    useful_n = sum(1 for row in rows if _is_useful_winner(row))
    return {
        "n": len(rows),
        "persistent_n": persistent_n,
        "persistent_precision": (persistent_n / len(rows)) if rows else 0.0,
        "useful_winner_n": useful_n,
        "useful_winner_precision": (useful_n / len(rows)) if rows else 0.0,
        "class_counts": dict(Counter(str(row.get("persistence_class") or "unknown") for row in rows)),
        "regime_counts": dict(Counter(str(row.get("persistence_regime0") or "unknown") for row in rows)),
        "family_counts": dict(Counter(str(row.get("source_family") or "unknown") for row in rows)),
    }


def _decorate_watchlist_rows(module: Any, rows: list[dict[str, Any]], tier: str) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for row in rows:
        gate = module._watchlist_gate_result(row, tier=tier)
        if not gate.get("passed"):
            continue
        decorated = dict(row)
        decorated[f"{tier}_profile"] = gate.get("profile")
        decorated[f"{tier}_misses"] = gate.get("misses") or []
        selected.append(decorated)
    return selected


def _write_md(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Dry-Run Selection Report",
        "",
        "Retrospective evaluation of the regime-aware persistent watchlists.",
        "",
        "## Config",
        "",
        f"- Train window: `{report['config']['train_hours']}h`",
        f"- Validation window: `{report['config']['validate_hours']}h`",
        f"- Live lookback: `{report['config']['live_lookback_min']}m`",
        "",
        "## Validation Baseline",
        "",
        f"- Anchors: `{report['validation']['anchors']}`",
        f"- Persistent baseline: `{_fmt_pct(report['validation']['baseline_persistent_precision'])}`",
        f"- Useful-winner baseline: `{_fmt_pct(report['validation']['baseline_useful_precision'])}`",
        "",
    ]

    for tier in ("strict", "near"):
        section = report["validation"][tier]
        lines.extend(
            [
                f"## Validation {tier.title()} Watchlist",
                "",
                f"- Selected: `{section['summary']['n']}`",
                f"- Persistent precision: `{_fmt_pct(section['summary']['persistent_precision'])}`",
                f"- Useful-winner precision: `{_fmt_pct(section['summary']['useful_winner_precision'])}`",
                f"- Class counts: `{section['summary']['class_counts']}`",
                f"- Regime counts: `{section['summary']['regime_counts']}`",
                "",
                "| Symbol | Mint | Score | Regime | Profile | Class | Persistent | MCap0 | Age0 | Mom5m0 | Hits | Buys | NetSOL |",
                "|---|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in section["top_rows"]:
            lines.append(
                f"| {row.get('symbol') or 'n/a'} | `{row.get('mint')}` | {float(row.get('score') or 0.0):.1f} | "
                f"`{row.get('persistence_regime0') or 'unknown'}` | `{row.get(f'{tier}_profile') or 'unknown'}` | "
                f"`{row.get('persistence_class') or 'unknown'}` | {'yes' if row.get('persistent') else 'no'} | "
                f"{_fmt_num(row.get('mcap0'), 0)} | {_fmt_num(row.get('pair_age_min0'), 1)} | "
                f"{_fmt_num(row.get('mom5m0'), 1)} | {int(row.get('hits0') or 0)} | {int(row.get('buys0') or 0)} | "
                f"{_fmt_num(row.get('net_sol_in0'), 2)} |"
            )
        lines.append("")

    lines.extend(
        [
            "## Live Watchlists",
            "",
            f"- Strict live count: `{report['live']['strict']['summary']['n']}`",
            f"- Near live count: `{report['live']['near']['summary']['n']}`",
            "",
            "| Tier | Symbol | Mint | Score | Regime | Profile | MCap | Age0 | Mom5m0 | Hits | Buys | NetSOL |",
            "|---|---|---|---:|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for tier in ("strict", "near"):
        for row in report["live"][tier]["top_rows"]:
            lines.append(
                f"| `{tier}` | {row.get('symbol') or 'n/a'} | `{row.get('mint')}` | {float(row.get('persistent_score') or 0.0):.1f} | "
                f"`{row.get('persistence_regime0') or 'unknown'}` | `{row.get(f'{tier}_profile') or 'unknown'}` | "
                f"{_fmt_num(row.get('mcap0'), 0)} | {_fmt_num(row.get('pair_age_min0'), 1)} | "
                f"{_fmt_num(row.get('mom5m0'), 1)} | {int(row.get('hits0') or 0)} | {int(row.get('buys0') or 0)} | "
                f"{_fmt_num(row.get('net_sol_in0'), 2)} |"
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Retrospective dry-run report for persistent watchlists.")
    parser.add_argument("--train-hours", type=float, default=72.0)
    parser.add_argument("--validate-hours", type=float, default=24.0)
    parser.add_argument("--winner-ret", type=float, default=0.50)
    parser.add_argument("--persistent-ret", type=float, default=1.00)
    parser.add_argument("--persistent-retain", type=float, default=0.50)
    parser.add_argument("--round-trip-retain", type=float, default=0.15)
    parser.add_argument("--live-lookback-min", type=float, default=120.0)
    parser.add_argument("--min-family-samples", type=int, default=20)
    parser.add_argument("--min-slice-support", type=int, default=6)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=OUT_MD)
    args = parser.parse_args()

    module = _load_rank_module()
    now = time.time()
    train_since_ts = now - ((float(args.train_hours) + float(args.validate_hours)) * 3600.0)
    validate_cutoff_ts = now - (float(args.validate_hours) * 3600.0)
    live_since_ts = now - (float(args.live_lookback_min) * 60.0)

    rows_by_mint = module.load_outcome_rows(since_ts=train_since_ts)
    train_anchors = module.build_anchor_set(
        rows_by_mint,
        min_ts=None,
        max_ts=validate_cutoff_ts,
        winner_ret=float(args.winner_ret),
        persistent_ret=float(args.persistent_ret),
        persistent_retain=float(args.persistent_retain),
        round_trip_retain=float(args.round_trip_retain),
    )
    train_report = module.build_training_report(train_anchors, min_slice_support=int(args.min_slice_support))
    validation_anchors = module.build_anchor_set(
        rows_by_mint,
        min_ts=validate_cutoff_ts,
        max_ts=None,
        winner_ret=float(args.winner_ret),
        persistent_ret=float(args.persistent_ret),
        persistent_retain=float(args.persistent_retain),
        round_trip_retain=float(args.round_trip_retain),
    )
    validation_rows = module.score_anchor_set(
        validation_anchors,
        report=train_report,
        min_family_samples=int(args.min_family_samples),
    )

    strict_validation = _decorate_watchlist_rows(module, validation_rows, "strict")
    near_validation = _decorate_watchlist_rows(module, validation_rows, "near")

    live_candidates = module.load_live_candidates(
        since_ts=live_since_ts,
        report=train_report,
        min_family_samples=int(args.min_family_samples),
        top=int(args.top),
    )
    strict_live = _decorate_watchlist_rows(module, live_candidates, "strict")
    near_live = _decorate_watchlist_rows(module, live_candidates, "near")

    validation_baseline = _summarize_selection(validation_rows)
    report = {
        "generated_at": now,
        "config": {
            "train_hours": float(args.train_hours),
            "validate_hours": float(args.validate_hours),
            "live_lookback_min": float(args.live_lookback_min),
        },
        "validation": {
            "anchors": len(validation_rows),
            "baseline_persistent_precision": validation_baseline["persistent_precision"],
            "baseline_useful_precision": validation_baseline["useful_winner_precision"],
            "strict": {
                "summary": _summarize_selection(strict_validation),
                "top_rows": strict_validation[: int(args.top)],
            },
            "near": {
                "summary": _summarize_selection(near_validation),
                "top_rows": near_validation[: int(args.top)],
            },
        },
        "live": {
            "strict": {
                "summary": _summarize_selection(strict_live),
                "top_rows": strict_live[: int(args.top)],
            },
            "near": {
                "summary": _summarize_selection(near_live),
                "top_rows": near_live[: int(args.top)],
            },
        },
    }

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_md(args.out_md, report)
    print(
        f"dry_run_selection_report: validation_anchors={len(validation_rows)} "
        f"strict={len(strict_validation)} near={len(near_validation)}"
    )


if __name__ == "__main__":
    main()
