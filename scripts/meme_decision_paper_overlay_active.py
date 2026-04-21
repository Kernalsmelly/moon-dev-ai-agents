#!/usr/bin/env python3
"""Active paper-trade overlay for lifecycle decisions.

This is the more realistic shadow-trader companion to the cautious v2 overlay.
It keeps the same lifecycle management engine, but opens a wider entry lane while
also tightening early risk controls so we can observe a more live-like paper flow.
"""

from __future__ import annotations

import argparse
import importlib.util
import time
from pathlib import Path
from typing import Any

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
REPORTS = BASE / "data" / "meme_reports"
V2_PATH = BASE / "scripts" / "meme_decision_paper_overlay_v2.py"

ACTIVE_STATE_JSON = REPORTS / "meme_decision_paper_overlay_active_state.json"
ACTIVE_JOURNAL_JSONL = REPORTS / "meme_decision_paper_overlay_active_journal.jsonl"
ACTIVE_OUT_JSON = REPORTS / "meme_decision_paper_overlay_active_report.json"
ACTIVE_OUT_MD = REPORTS / "meme_decision_paper_overlay_active_report.md"


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = _load_module("meme_decision_paper_overlay_v2_base", V2_PATH)

# Broaden the entry lane, but keep risk tighter than a naive chase-everything mode.
base.CALMER_REGIMES = {"late_slow_expansion", "calm_continuation", "early_hot_breakout"}
base.CONFIRM_SHAPES = {"extending_cleanly", "holding_pullback", "stalling_but_alive"}
base.STARTER_MIN_SIGNAL_RET = 0.10
base.STARTER_MAX_SIGNAL_RET = 1.60
base.STARTER_PROBE_MAX_SIGNAL_RET = 1.10
base.HIGH_CONVICTION_BREAKOUT_MAX_SIGNAL_RET = 0.75
base.HIGH_CONVICTION_PROMOTE_MAX_SIGNAL_RET = 0.90
base.HIGH_CONVICTION_MIN_LIQUIDITY_USD = 12000.0
base.HIGH_CONVICTION_MAX_IMPACT = 0.020
base.HIGH_CONVICTION_MIN_SURVIVOR_FIT = 45.0
base.ADD_MAX_SIGNAL_RET = 1.35
base.STOP_LOSS = -0.18
base.FIRST_HOUR_FAIL_RET = -0.06
base.EARLY_SURVIVAL_FAIL_RET = -0.02
base.THREE_HOUR_FLOOR_RET = 0.00
base.EARLY_SHAPE_FAIL_RET = -0.08
base.READINESS_DEGRADE_RET = -0.02
base.SIZE_PROFILES = {
    "full": {"starter_weight": 0.80, "target_capital": 1.00},
    "medium": {"starter_weight": 0.60, "target_capital": 0.90},
    "small": {"starter_weight": 0.40, "target_capital": 0.70},
    "probe": {"starter_weight": 0.25, "target_capital": 0.50},
}


def _passes_active_market_gate(
    *,
    readiness: str,
    shape_state: str,
    regime: str,
    signal_ret: float | None,
    survivor_fit: float,
    liquidity: float | None,
    impact: float | None,
    signal_ret_cap: float,
) -> bool:
    if readiness not in {"route_ready", "route_ready_meteora"}:
        return False
    if shape_state in base.BAD_SHAPES:
        return False
    if shape_state not in base.SUPPORTIVE_SHAPES | base.CONFIRM_SHAPES:
        return False
    if regime not in base.CALMER_REGIMES:
        return False
    if signal_ret is None or signal_ret > signal_ret_cap:
        return False
    if survivor_fit < base.HIGH_CONVICTION_MIN_SURVIVOR_FIT:
        return False
    if liquidity is not None and liquidity < base.HIGH_CONVICTION_MIN_LIQUIDITY_USD:
        return False
    if impact is not None and impact > base.HIGH_CONVICTION_MAX_IMPACT:
        return False
    return True


def _starter_reason_active(
    decision_row: dict[str, Any],
    pending_row: dict[str, Any],
    market_row: dict[str, Any] | None,
    *,
    signal_ret: float | None,
) -> str | None:
    if signal_ret is None or signal_ret < base.STARTER_MIN_SIGNAL_RET or signal_ret > base.STARTER_MAX_SIGNAL_RET:
        return None
    bucket = str(decision_row.get("decision_bucket") or "")
    if bucket not in {"watch", "promote"}:
        return None
    stage = str(decision_row.get("stage") or "")
    if stage not in base.LIVE_STAGES or stage == "pending_cut_bias":
        return None
    shape_state = str(decision_row.get("shape_state") or pending_row.get("shape_state") or "unknown")
    if shape_state in base.BAD_SHAPES:
        return None

    useful = float(decision_row.get("useful_score") or 0.0)
    persistent = float(decision_row.get("persistent_score") or 0.0)
    survivor_fit = float(decision_row.get("survivor_fit") or 0.0)
    regime = str(decision_row.get("regime") or "unknown")
    grade = str(decision_row.get("decision_grade") or "")
    status = str(decision_row.get("status") or pending_row.get("promotion_decision") or "")
    readiness = str((market_row or {}).get("execution_readiness") or "")
    liquidity = base._to_float((market_row or {}).get("dex_liquidity_usd"))
    impact = base._to_float((market_row or {}).get("jupiter_price_impact_pct"))
    starter_eval = base.STARTER_GATE_MODULE.starter_gate_evaluate(pending_row)
    starter_grade = str(starter_eval.get("starter_grade") or "starter_neutral")

    if market_row is not None and readiness not in {"route_ready", "route_ready_meteora"}:
        return None
    if starter_grade == "starter_avoid":
        return None

    active_breakout = _passes_active_market_gate(
        readiness=readiness,
        shape_state=shape_state,
        regime=regime,
        signal_ret=signal_ret,
        survivor_fit=survivor_fit,
        liquidity=liquidity,
        impact=impact,
        signal_ret_cap=base.HIGH_CONVICTION_BREAKOUT_MAX_SIGNAL_RET,
    )
    active_promote = _passes_active_market_gate(
        readiness=readiness,
        shape_state=shape_state,
        regime=regime,
        signal_ret=signal_ret,
        survivor_fit=survivor_fit,
        liquidity=liquidity,
        impact=impact,
        signal_ret_cap=base.HIGH_CONVICTION_PROMOTE_MAX_SIGNAL_RET,
    )

    if starter_grade == "starter_strong":
        if grade == "promote_strong" and active_promote:
            return "active_strong_promote"
        if active_breakout and useful >= 38.0:
            return "active_strong_breakout"
        return None

    if starter_grade == "starter_probe":
        if active_breakout and useful >= 42.0 and survivor_fit >= 45.0:
            return "active_probe_core"
        if active_breakout and persistent >= 55.0:
            return "active_probe_persistence"
        if (
            active_breakout
            and bucket == "watch"
            and status in {"watch_to_60m", "hold_and_recheck", "promote_now"}
            and useful >= 45.0
        ):
            return "active_probe_watch"

    if (
        starter_grade == "starter_neutral"
        and active_promote
        and bucket == "promote"
        and grade in {"promote_probe", "promote_strong"}
        and useful >= 50.0
        and survivor_fit >= 50.0
    ):
        return "active_neutral_promote"

    if (
        starter_grade == "starter_neutral"
        and active_breakout
        and bucket == "watch"
        and status in {"watch_to_60m", "hold_and_recheck"}
        and shape_state in {"holding_pullback", "stalling_but_alive"}
        and useful >= 55.0
        and survivor_fit >= 52.0
    ):
        return "active_neutral_watch"

    return None


base._starter_reason = _starter_reason_active


def _blank_active_state(now: float) -> dict[str, Any]:
    state = base._blank_state(now)
    state["cohort_label"] = "active_legacy"
    state["cohort_started_at"] = now
    return state


def _ensure_active_state_defaults(state: dict[str, Any], now: float) -> dict[str, Any]:
    state = base._ensure_state_defaults(state, now)
    if not str(state.get("cohort_label") or "").strip():
        state["cohort_label"] = "active_legacy"
    return state


def _write_md_active(path: Path, report: dict[str, Any]) -> None:
    s = report["summary"]
    lines = [
        "# Decision Paper Overlay Active",
        "",
        "Broader shadow-trading paper overlay built to act more like a real operator-managed bot.",
        "",
        "Rules:",
        f"- Broader starter entry lane: route-ready `watch` / `promote` names between `{base.STARTER_MIN_SIGNAL_RET*100:.0f}%` and `{base.HIGH_CONVICTION_PROMOTE_MAX_SIGNAL_RET*100:.0f}%` from signal, including stronger breakout regimes.",
        "- Keep add-on confirmation for `promote_strong`, but allow more names to qualify for the first starter lot.",
        "- Tighten early risk controls so wider entry coverage does not turn into pure chase mode.",
        "- Exit on the same lifecycle / readiness / protection framework as v2.",
        "",
        "## Summary",
        "",
        f"- Active cohort: `{((report.get('active_cohort') or {}).get('label')) or 'active_legacy'}`",
        f"- Open positions: `{s['open_positions']}`",
        f"- Closed positions: `{s['closed_positions']}`",
        f"- Closed winrate: `{base._fmt_pct(s['closed_winrate'])}`",
        f"- Closed average return: `{base._fmt_pct(s['closed_avg_return'])}`",
        f"- Closed median return: `{base._fmt_pct(s['closed_median_return'])}`",
        f"- Open average mark: `{base._fmt_pct(s['open_avg_mark'])}`",
        f"- Open median mark: `{base._fmt_pct(s['open_median_mark'])}`",
        f"- Closed starter-only: `{s['counts_by_lot_kind'].get('starter_only', 0)}`",
        f"- Closed starter-plus-add: `{s['counts_by_lot_kind'].get('starter_plus_add', 0)}`",
        "",
        "## Open Positions",
        "",
        "| Symbol | Lots | Grade | Size Tier | Starter Gate | Readiness | Entry Wt | Target Cap | Entry Shape | Current Shape | Mark Ret | Paper PnL | Status | Cohort |",
        "|---|---:|---|---|---|---|---:|---:|---|---|---:|---:|---|---|",
    ]
    for row in report["open_positions"]:
        lines.append(
            f"| {row.get('symbol') or 'n/a'} | {len(row.get('lots') or [])} | `{row.get('last_decision_grade') or 'unknown'}` | `{row.get('entry_size_tier') or 'probe'}` | `{row.get('entry_starter_grade') or 'starter_neutral'}` | `{row.get('last_execution_readiness') or row.get('entry_execution_readiness') or 'n/a'}` | {base._fmt_pct(base._to_float(row.get('entry_starter_weight')))} | {base._fmt_pct(base._to_float(row.get('target_capital')))} | `{row.get('entry_shape_state')}` | `{row.get('last_shape_state')}` | "
            f"{base._fmt_pct(base._to_float(row.get('current_signal_ret')))} | {base._fmt_pct(base._to_float(row.get('current_trade_ret')))} | `{row.get('last_status') or 'unknown'}` | `{row.get('cohort') or 'active_legacy'}` |"
        )
    lines.extend(
        [
            "",
            "## Recently Closed",
            "",
            "| Symbol | Lots | Exit Reason | Outcome | Size Tier | Target Cap | Hold (h) | Exit Ret | Paper PnL | Cohort |",
            "|---|---:|---|---|---|---:|---:|---:|---:|---|",
        ]
    )
    for row in list(report["closed_positions"])[-20:]:
        lines.append(
            f"| {row.get('symbol') or 'n/a'} | {len(row.get('lots') or [])} | `{row.get('exit_reason') or 'unknown'}` | `{row.get('outcome_class') or 'unknown'}` | `{row.get('entry_size_tier') or 'probe'}` | {base._fmt_pct(base._to_float(row.get('target_capital')))} | "
            f"{base._fmt_num(base._to_float(row.get('hold_hours')), 2)} | {base._fmt_pct(base._to_float(row.get('exit_signal_ret')))} | {base._fmt_pct(base._to_float(row.get('final_trade_ret')))} | `{row.get('cohort') or 'active_legacy'}` |"
        )
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def run_once(*, state_path: Path, journal_path: Path, out_json: Path, out_md: Path, do_refresh: bool, max_hold_hours: float) -> dict[str, Any]:
    now = time.time()
    if do_refresh:
        base._run_refresh()

    state = _ensure_active_state_defaults(base._load_json(state_path, None) or _blank_active_state(now), now)
    live = base._load_live_inputs()
    next_state, events = base._step(state, live, now=now, max_hold_hours=max_hold_hours)
    base._write_json(state_path, next_state)
    for event in events:
        base._append_jsonl(journal_path, event)
    report = base._build_report(next_state)
    base._write_json(out_json, report)
    _write_md_active(out_md, report)
    print(
        "meme_decision_paper_overlay_active: "
        f"open={report['summary']['open_positions']} "
        f"closed={report['summary']['closed_positions']}"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Active paper-trade overlay for lifecycle decisions.")
    parser.add_argument("--loop", action="store_true", help="Run continuously.")
    parser.add_argument("--interval-sec", type=int, default=900, help="Loop interval in seconds.")
    parser.add_argument("--max-hold-hours", type=float, default=12.0, help="Close positions after this long if still open.")
    parser.add_argument("--no-refresh", action="store_true", help="Do not refresh the main scorecard before stepping.")
    parser.add_argument("--reset", action="store_true", help="Start a fresh overlay state.")
    parser.add_argument("--start-new-cohort", action="store_true", help="Mark future entries as a fresh clean cohort without wiping history.")
    parser.add_argument("--state", type=Path, default=ACTIVE_STATE_JSON)
    parser.add_argument("--journal", type=Path, default=ACTIVE_JOURNAL_JSONL)
    parser.add_argument("--out-json", type=Path, default=ACTIVE_OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=ACTIVE_OUT_MD)
    args = parser.parse_args()

    if args.reset:
        now = time.time()
        base._write_json(args.state, _blank_active_state(now))
        if args.journal.exists():
            args.journal.unlink()

    if args.start_new_cohort:
        now = time.time()
        state = base._load_json(args.state, None) or _blank_active_state(now)
        state["cohort_started_at"] = now
        state["cohort_label"] = f"active_clean_{time.strftime('%Y%m%d_%H%M%S', time.localtime(now))}"
        base._write_json(args.state, state)

    if not args.loop:
        run_once(
            state_path=args.state,
            journal_path=args.journal,
            out_json=args.out_json,
            out_md=args.out_md,
            do_refresh=not args.no_refresh,
            max_hold_hours=float(args.max_hold_hours),
        )
        return

    while True:
        run_once(
            state_path=args.state,
            journal_path=args.journal,
            out_json=args.out_json,
            out_md=args.out_md,
            do_refresh=not args.no_refresh,
            max_hold_hours=float(args.max_hold_hours),
        )
        time.sleep(max(60, int(args.interval_sec)))


if __name__ == "__main__":
    main()
