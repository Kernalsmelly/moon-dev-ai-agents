#!/usr/bin/env python3
"""Build a cleaner live attention shortlist from the lifecycle board."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
REPORTS = BASE / "data" / "meme_reports"
LIFECYCLE_JSON = REPORTS / "meme_lifecycle_monitor.json"
OUT_JSON = REPORTS / "meme_attention_shortlist.json"
OUT_MD = REPORTS / "meme_attention_shortlist.md"

STAGE_BONUS = {
    "pending_promote_now": 24.0,
    "pending_watch": 12.0,
    "emerging_watchlist": 0.0,
}

STATUS_BONUS = {
    "promote_now": 16.0,
    "watch_to_60m": 10.0,
    "hold_and_recheck": 10.0,
    "fragile_watch": 2.0,
    "too_early": -2.0,
    "watchlist": 0.0,
}

CALMER_REGIMES = {"late_slow_expansion", "calm_continuation"}
HOT_REGIMES = {"early_hot_breakout"}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _fmt_num(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100.0:.1f}%"


def _score_row(row: dict[str, Any]) -> dict[str, Any]:
    stage = str(row.get("stage") or "")
    status = str(row.get("status") or "")
    regime = str(row.get("regime") or "unknown")
    useful = float(row.get("useful_score") or 0.0)
    persistent = float(row.get("persistent_score") or 0.0)
    survivor_fit = float(row.get("survivor_fit") or 0.0)
    shape_state = str(row.get("shape_state") or "unknown")
    is_pending = stage in {"pending_watch", "pending_promote_now"}
    is_emerging = stage == "emerging_watchlist"
    calmer_regime = regime in CALMER_REGIMES
    hot_regime = regime in HOT_REGIMES
    supportive_shape = shape_state in {"extending_cleanly", "holding_pullback"}
    weak_shape = shape_state in {"blowoff_risk", "losing_steam"}

    score = (
        0.28 * useful
        + 0.12 * persistent
        + 0.52 * survivor_fit
        + STAGE_BONUS.get(stage, 0.0)
        + STATUS_BONUS.get(status, 0.0)
    )
    if calmer_regime:
        score += 4.0
    elif hot_regime and survivor_fit < 65.0:
        score -= 4.0
    if supportive_shape:
        score += 6.0
    elif weak_shape:
        score -= 10.0
    # Flashy emerging names without survivor support are where the board has
    # historically over-promised, so we demote them aggressively.
    if is_emerging and useful >= 80.0 and survivor_fit < 60.0:
        score -= 10.0
    if is_emerging and persistent < 10.0 and survivor_fit < 55.0:
        score -= 8.0

    reasons: list[str] = []
    if is_pending:
        reasons.append("pending_survival")
    if status == "promote_now":
        reasons.append("promotion_ready")
    elif status in {"watch_to_60m", "hold_and_recheck"}:
        reasons.append("survival_checkpoint")
    elif status == "fragile_watch":
        reasons.append("fragile_survival")
    if useful >= 70.0:
        reasons.append("useful_high")
    if supportive_shape:
        reasons.append(f"shape_{shape_state}")
    elif weak_shape:
        reasons.append(f"shape_{shape_state}")
    if survivor_fit >= 65.0:
        reasons.append("survivor_fit_strong")
    elif survivor_fit >= 55.0:
        reasons.append("survivor_fit_ok")
    if persistent >= 25.0:
        reasons.append("persistent_lean")
    if calmer_regime:
        reasons.append("calmer_regime")
    elif hot_regime and survivor_fit >= 70.0:
        reasons.append("hot_but_holding_shape")
    if stage == "emerging_watchlist":
        reasons.append("emerging_only")

    if stage == "pending_promote_now" or (
        stage == "pending_watch"
        and status in {"promote_now", "watch_to_60m", "hold_and_recheck"}
        and survivor_fit >= 65.0
        and not weak_shape
    ):
        tier = "focus_now"
    elif stage == "pending_watch" and survivor_fit >= 55.0 and not weak_shape:
        tier = "watch_closely"
    elif (
        stage == "emerging_watchlist"
        and survivor_fit >= 60.0
        and useful >= 75.0
        and (persistent >= 20.0 or calmer_regime)
    ):
        tier = "elevate"
    elif stage == "emerging_watchlist" and (survivor_fit >= 55.0 or useful >= 65.0):
        tier = "monitor"
    else:
        tier = "exploratory"

    return {
        **row,
        "attention_score": score,
        "attention_tier": tier,
        "attention_reasons": reasons,
    }


def build_report(lifecycle: dict[str, Any], *, top: int) -> dict[str, Any]:
    board = list(lifecycle.get("board") or [])
    eligible = [
        row for row in board
        if str(row.get("stage") or "") in {"emerging_watchlist", "pending_watch", "pending_promote_now"}
    ]
    scored = [_score_row(row) for row in eligible]
    scored.sort(key=lambda row: float(row.get("attention_score") or 0.0), reverse=True)

    tiers = {
        "focus_now": [row for row in scored if str(row.get("attention_tier")) == "focus_now"],
        "watch_closely": [row for row in scored if str(row.get("attention_tier")) == "watch_closely"],
        "elevate": [row for row in scored if str(row.get("attention_tier")) == "elevate"],
        "monitor": [row for row in scored if str(row.get("attention_tier")) == "monitor"],
        "exploratory": [row for row in scored if str(row.get("attention_tier")) == "exploratory"],
    }

    return {
        "generated_at": time.time(),
        "summary": {
            "eligible_count": len(eligible),
            "focus_now_count": len(tiers["focus_now"]),
            "watch_closely_count": len(tiers["watch_closely"]),
            "elevate_count": len(tiers["elevate"]),
            "monitor_count": len(tiers["monitor"]),
            "exploratory_count": len(tiers["exploratory"]),
        },
        "top": scored[:top],
        "tiers": {name: rows[:top] for name, rows in tiers.items()},
    }


def write_md(path: Path, report: dict[str, Any]) -> None:
    s = report["summary"]
    lines = [
        "# Attention Shortlist",
        "",
        "A conservative, pending-first shortlist that combines lifecycle stage, useful strength, persistence lean, and survivor fit into one current attention board.",
        "",
        "## Summary",
        "",
        f"- Eligible live names: `{s['eligible_count']}`",
        f"- Focus now: `{s['focus_now_count']}`",
        f"- Watch closely: `{s['watch_closely_count']}`",
        f"- Elevate: `{s['elevate_count']}`",
        f"- Monitor: `{s['monitor_count']}`",
        f"- Exploratory: `{s['exploratory_count']}`",
        "",
        "## Top Shortlist",
        "",
        "| Symbol | Tier | Stage | Attention | Useful | Persistent | Survivor Fit | Regime | Reasons |",
        "|---|---|---|---:|---:|---:|---:|---|---|",
    ]
    for row in report["top"]:
        lines.append(
            f"| {row['symbol']} | `{row['attention_tier']}` | `{row['stage']}` | {_fmt_num(row['attention_score'], 1)} | "
            f"{_fmt_num(row.get('useful_score'), 1)} | {_fmt_num(row.get('persistent_score'), 1)} | "
            f"{_fmt_num(row.get('survivor_fit'), 1)} | `{row.get('regime') or 'unknown'}` | {', '.join(row.get('attention_reasons') or []) or '—'} |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a combined live attention shortlist from the lifecycle board.")
    parser.add_argument("--lifecycle", type=Path, default=LIFECYCLE_JSON)
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=OUT_MD)
    args = parser.parse_args()

    report = build_report(_load_json(args.lifecycle), top=int(args.top))
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_md(args.out_md, report)
    print(
        "meme_attention_shortlist: "
        f"eligible={report['summary']['eligible_count']} "
        f"focus={report['summary']['focus_now_count']}"
    )


if __name__ == "__main__":
    main()
