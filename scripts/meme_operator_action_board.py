#!/usr/bin/env python3
"""Build an operator-facing action board from the active paper trader rules."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import time
from collections import Counter
from pathlib import Path
from typing import Any

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
REPORTS = BASE / "data" / "meme_reports"
ACTIVE_MODULE_PATH = BASE / "scripts" / "meme_decision_paper_overlay_active.py"
ACTIVE_STATE_JSON = REPORTS / "meme_decision_paper_overlay_active_state.json"
OUT_JSON = REPORTS / "meme_operator_action_board.json"
OUT_MD = REPORTS / "meme_operator_action_board.md"


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text())


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


def _unique_reasons(reasons: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for reason in reasons:
        if not reason or reason in seen:
            continue
        seen.add(reason)
        out.append(reason)
    return out


def _reason_counts(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    counts = Counter()
    for row in rows:
        for reason in row.get(field) or []:
            counts[str(reason)] += 1
    return [{"reason": reason, "n": n} for reason, n in counts.most_common()]


def _signal_ret(active: Any, mint: str, live: dict[str, Any]) -> float | None:
    return active.base._current_signal_ret(
        mint,
        pending_by_mint=live["pending_by_mint"],
        dataset_by_mint=live["dataset"],
    )


def _entry_block_reasons(
    active: Any,
    decision_row: dict[str, Any],
    pending_row: dict[str, Any] | None,
    market_row: dict[str, Any] | None,
    *,
    signal_ret: float | None,
) -> dict[str, Any]:
    base = active.base
    bucket = str(decision_row.get("decision_bucket") or "unknown")
    stage = str(decision_row.get("stage") or "unknown")
    grade = str(decision_row.get("decision_grade") or "unknown")
    status = str(decision_row.get("status") or (pending_row or {}).get("promotion_decision") or "unknown")
    shape_state = str(decision_row.get("shape_state") or (pending_row or {}).get("shape_state") or "unknown")
    regime = str(decision_row.get("regime") or "unknown")
    useful = float(decision_row.get("useful_score") or 0.0)
    persistent = float(decision_row.get("persistent_score") or 0.0)
    survivor_fit = float(decision_row.get("survivor_fit") or 0.0)
    readiness = str((market_row or {}).get("execution_readiness") or "unknown")
    liquidity = base._to_float((market_row or {}).get("dex_liquidity_usd"))
    impact = base._to_float((market_row or {}).get("jupiter_price_impact_pct"))
    starter_eval = base.STARTER_GATE_MODULE.starter_gate_evaluate(pending_row or {})
    starter_grade = str(starter_eval.get("starter_grade") or "starter_unknown")
    reasons: list[str] = []

    if pending_row is None:
        reasons.append("missing_pending_row")

    if signal_ret is None:
        reasons.append("missing_signal_ret")
    elif signal_ret < base.STARTER_MIN_SIGNAL_RET:
        reasons.append("signal_ret_below_min")
    elif signal_ret > base.STARTER_MAX_SIGNAL_RET:
        reasons.append("signal_ret_above_max")

    if bucket not in {"watch", "promote"}:
        reasons.append(f"bucket_{bucket}")
    if stage not in base.LIVE_STAGES:
        reasons.append(f"stage_{stage}")
    elif stage == "pending_cut_bias":
        reasons.append("pending_cut_bias")
    if starter_grade == "starter_avoid":
        reasons.append("starter_gate_avoid")

    if shape_state in base.BAD_SHAPES:
        reasons.append(f"bad_shape_{shape_state}")
    elif shape_state not in (base.SUPPORTIVE_SHAPES | base.CONFIRM_SHAPES):
        reasons.append(f"unsupported_shape_{shape_state}")

    if market_row is None:
        reasons.append("missing_market_data")
    elif readiness not in {"route_ready", "route_ready_meteora"}:
        reasons.append(f"readiness_{readiness}")

    if regime not in base.CALMER_REGIMES:
        reasons.append(f"regime_{regime}")
    if survivor_fit < base.HIGH_CONVICTION_MIN_SURVIVOR_FIT:
        reasons.append("survivor_fit_low")
    if liquidity is not None and liquidity < base.HIGH_CONVICTION_MIN_LIQUIDITY_USD:
        reasons.append("liquidity_low")
    if impact is not None and impact > base.HIGH_CONVICTION_MAX_IMPACT:
        reasons.append("impact_high")

    structural_ok = not reasons
    if structural_ok:
        if starter_grade == "starter_strong":
            if grade != "promote_strong":
                reasons.append("needs_promote_strong")
            elif useful < 38.0:
                reasons.append("useful_below_breakout_min")
            else:
                reasons.append("waiting_for_strong_pattern")
        elif starter_grade == "starter_probe":
            if useful < 42.0 and persistent < 55.0:
                reasons.append("probe_needs_useful_or_persistence")
            elif bucket == "watch" and status not in {"watch_to_60m", "hold_and_recheck", "promote_now"}:
                reasons.append("watch_status_not_ready")
            else:
                reasons.append("probe_waiting_for_better_alignment")
        elif starter_grade == "starter_neutral":
            if bucket == "promote" and useful < 50.0:
                reasons.append("neutral_promote_useful_low")
            elif bucket == "watch" and useful < 55.0:
                reasons.append("neutral_watch_useful_low")
            elif survivor_fit < 50.0:
                reasons.append("neutral_survivor_fit_low")
            else:
                reasons.append("neutral_waiting_for_tighter_alignment")
        else:
            reasons.append("no_active_pattern_match")

    return {
        "starter_grade": starter_grade,
        "starter_matches": list(starter_eval.get("matches") or []),
        "reasons": _unique_reasons(reasons),
    }


def _watch_profile(active: Any, decision_row: dict[str, Any], pending_row: dict[str, Any] | None, market_row: dict[str, Any] | None) -> dict[str, Any]:
    if pending_row is None:
        return {
            "confidence_score": None,
            "size_tier": "n/a",
            "starter_weight": None,
            "target_capital": None,
        }
    profile = active.base._confidence_profile(
        decision_row,
        pending_row,
        market_row,
        starter_reason="operator_watch",
    )
    return {
        "confidence_score": _to_float(profile.get("confidence_score")),
        "size_tier": profile.get("size_tier") or "probe",
        "starter_weight": _to_float(profile.get("starter_weight")),
        "target_capital": _to_float(profile.get("target_capital")),
    }


def _evaluate_live_row(
    active: Any,
    live: dict[str, Any],
    decision_row: dict[str, Any],
    pending_row: dict[str, Any] | None,
    market_row: dict[str, Any] | None,
) -> dict[str, Any]:
    mint = str(decision_row.get("mint") or "")
    signal_ret = _signal_ret(active, mint, live)
    starter_reason = None
    if pending_row is not None:
        starter_reason = active.base._starter_reason(decision_row, pending_row, market_row, signal_ret=signal_ret)
    block = _entry_block_reasons(
        active,
        decision_row,
        pending_row,
        market_row,
        signal_ret=signal_ret,
    )
    profile = _watch_profile(active, decision_row, pending_row, market_row)
    row = {
        "mint": mint,
        "symbol": decision_row.get("symbol") or (pending_row or {}).get("symbol") or "n/a",
        "source": decision_row.get("source") or (pending_row or {}).get("signal_source") or "unknown",
        "decision_bucket": decision_row.get("decision_bucket") or "unknown",
        "decision_grade": decision_row.get("decision_grade") or "unknown",
        "stage": decision_row.get("stage") or "unknown",
        "status": decision_row.get("status") or (pending_row or {}).get("promotion_decision") or "unknown",
        "shape_state": decision_row.get("shape_state") or (pending_row or {}).get("shape_state") or "unknown",
        "regime": decision_row.get("regime") or "unknown",
        "signal_ret": signal_ret,
        "useful_score": _to_float(decision_row.get("useful_score")),
        "persistent_score": _to_float(decision_row.get("persistent_score")),
        "survivor_fit": _to_float(decision_row.get("survivor_fit")),
        "execution_readiness": (market_row or {}).get("execution_readiness") or "unknown",
        "dex_liquidity_usd": _to_float((market_row or {}).get("dex_liquidity_usd")),
        "jupiter_price_impact_pct": _to_float((market_row or {}).get("jupiter_price_impact_pct")),
        "starter_grade": block["starter_grade"],
        "starter_matches": block["starter_matches"],
        "starter_reason": starter_reason,
        "confidence_score": profile["confidence_score"],
        "size_tier": profile["size_tier"],
        "starter_weight": profile["starter_weight"],
        "target_capital": profile["target_capital"],
        "blockers": block["reasons"],
    }

    bucket = str(row["decision_bucket"])
    stage = str(row["stage"])
    starter_grade = str(row["starter_grade"])
    shape_state = str(row["shape_state"])
    if starter_reason:
        row["action"] = "enter_now"
    elif (
        bucket in {"watch", "promote"}
        and stage in active.base.LIVE_STAGES
        and stage != "pending_cut_bias"
        and starter_grade != "starter_avoid"
        and shape_state not in active.base.BAD_SHAPES
    ):
        row["action"] = "watch_candidate"
    elif bucket == "observe":
        row["action"] = "observe_only"
    else:
        row["action"] = "blocked"
    return row


def _management_row(active: Any, live: dict[str, Any], pos: dict[str, Any], *, max_hold_hours: float) -> dict[str, Any]:
    mint = str(pos.get("mint") or "")
    decision_row = live["decision_by_mint"].get(mint) or {}
    pending_row = live["pending_by_mint"].get(mint) or {}
    market_row = live["market_by_mint"].get(mint) or {}
    signal_ret = _signal_ret(active, mint, live)
    trade_ret = active.base._weighted_trade_return(list(pos.get("lots") or []), signal_ret)
    hold_hours = (time.time() - float(pos.get("opened_at") or time.time())) / 3600.0
    shape_state = str(decision_row.get("shape_state") or pending_row.get("shape_state") or pos.get("last_shape_state") or "unknown")
    status = str(decision_row.get("status") or pending_row.get("promotion_decision") or pos.get("last_status") or "unknown")
    decision_bucket = str(decision_row.get("decision_bucket") or "unknown")
    readiness = str((market_row or {}).get("execution_readiness") or pos.get("last_execution_readiness") or pos.get("entry_execution_readiness") or "unknown")
    max_trade_ret = _to_float(pos.get("max_trade_ret"))
    flags: list[str] = []
    action = "hold"

    if decision_bucket == "cut" or status == "cut_bias" or shape_state == "losing_steam":
        action = "cut_now"
        flags.append("decision_cut")
    elif trade_ret is not None and trade_ret <= active.base.STOP_LOSS:
        action = "cut_now"
        flags.append("stop_loss")
    elif trade_ret is not None and readiness in active.base.RISKY_READINESS and trade_ret <= min(active.base.READINESS_BREAK_RET, active.base.READINESS_DEGRADE_RET):
        action = "cut_now"
        flags.append(f"readiness_{readiness}")
    else:
        if readiness in active.base.RISKY_READINESS:
            flags.append(f"readiness_{readiness}")
        if shape_state in active.base.WEAK_SHAPES:
            flags.append(f"shape_{shape_state}")
        if max_trade_ret is not None and max_trade_ret >= active.base.BREAK_EVEN_ARM_RET:
            flags.append("break_even_armed")
        if max_trade_ret is not None and max_trade_ret >= active.base.PROFIT_LOCK_ARM_RET:
            flags.append("profit_lock_armed")
        if max_hold_hours > 0 and hold_hours >= (max_hold_hours * 0.75):
            flags.append("max_hold_soon")
        if flags:
            action = "protect"

    return {
        "mint": mint,
        "symbol": pos.get("symbol") or "n/a",
        "action": action,
        "flags": _unique_reasons(flags),
        "lots": len(pos.get("lots") or []),
        "hold_hours": hold_hours,
        "current_signal_ret": signal_ret,
        "current_trade_ret": trade_ret,
        "max_trade_ret": max_trade_ret,
        "decision_bucket": decision_bucket,
        "decision_grade": decision_row.get("decision_grade") or pos.get("last_decision_grade") or "unknown",
        "status": status,
        "shape_state": shape_state,
        "execution_readiness": readiness,
        "capital_used": _to_float(pos.get("capital_used")),
        "target_capital": _to_float(pos.get("target_capital")),
    }


def build_report(active: Any, state: dict[str, Any], live: dict[str, Any], *, max_hold_hours: float) -> dict[str, Any]:
    open_by_mint = {
        str(pos.get("mint") or ""): pos
        for pos in list(state.get("open_positions") or [])
        if pos.get("mint")
    }
    live_rows: list[dict[str, Any]] = []
    for decision_row in list(live["decision"].get("live_rows") or []):
        mint = str(decision_row.get("mint") or "")
        pending_row = live["pending_by_mint"].get(mint)
        market_row = live["market_by_mint"].get(mint)
        live_rows.append(_evaluate_live_row(active, live, decision_row, pending_row, market_row))

    enter_now = sorted(
        [row for row in live_rows if row.get("action") == "enter_now" and row.get("mint") not in open_by_mint],
        key=lambda row: (
            float(row.get("confidence_score") or 0.0),
            float(row.get("survivor_fit") or 0.0),
            float(row.get("useful_score") or 0.0),
        ),
        reverse=True,
    )
    watch_candidates = sorted(
        [row for row in live_rows if row.get("action") == "watch_candidate" and row.get("mint") not in open_by_mint],
        key=lambda row: (
            float(row.get("confidence_score") or 0.0),
            float(row.get("survivor_fit") or 0.0),
            float(row.get("useful_score") or 0.0),
        ),
        reverse=True,
    )
    observe_only = sorted(
        [row for row in live_rows if row.get("action") == "observe_only" and row.get("mint") not in open_by_mint],
        key=lambda row: (
            float(row.get("survivor_fit") or 0.0),
            float(row.get("useful_score") or 0.0),
        ),
        reverse=True,
    )
    blocked = sorted(
        [row for row in live_rows if row.get("action") == "blocked" and row.get("mint") not in open_by_mint],
        key=lambda row: (
            -len(row.get("blockers") or []),
            float(row.get("survivor_fit") or 0.0),
        ),
    )
    open_management = sorted(
        [_management_row(active, live, pos, max_hold_hours=max_hold_hours) for pos in list(state.get("open_positions") or [])],
        key=lambda row: (
            0 if row.get("action") == "cut_now" else 1 if row.get("action") == "protect" else 2,
            -(float(row.get("hold_hours") or 0.0)),
        ),
    )

    report = {
        "generated_at": time.time(),
        "summary": {
            "live_rows": len(live_rows),
            "open_positions": len(open_management),
            "enter_now_count": len(enter_now),
            "watch_candidate_count": len(watch_candidates),
            "observe_only_count": len(observe_only),
            "blocked_count": len(blocked),
            "cut_now_count": sum(1 for row in open_management if row.get("action") == "cut_now"),
            "protect_count": sum(1 for row in open_management if row.get("action") == "protect"),
            "hold_count": sum(1 for row in open_management if row.get("action") == "hold"),
            "portfolio_capital_used": sum(float(row.get("capital_used") or 0.0) for row in open_management),
            "portfolio_target_capital": sum(float(row.get("target_capital") or 0.0) for row in open_management),
        },
        "blocked_reason_counts": _reason_counts(live_rows, "blockers"),
        "management_flag_counts": _reason_counts(open_management, "flags"),
        "enter_now": enter_now[:12],
        "watch_candidates": watch_candidates[:12],
        "observe_only": observe_only[:12],
        "blocked_examples": blocked[:12],
        "open_management": open_management[:12],
    }
    return report


def write_md(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    lines = [
        "# Meme Operator Action Board",
        "",
        "This is the live operator layer for the active paper trader. It answers four questions: what can we enter now, what is close, what is blocked, and what open positions need attention.",
        "",
        "## Summary",
        "",
        f"- Live rows considered: `{summary['live_rows']}`",
        f"- Enter now: `{summary['enter_now_count']}`",
        f"- Watch candidates: `{summary['watch_candidate_count']}`",
        f"- Observe only: `{summary['observe_only_count']}`",
        f"- Structurally blocked: `{summary['blocked_count']}`",
        f"- Open positions: `{summary['open_positions']}`",
        f"- Open positions to cut now: `{summary['cut_now_count']}`",
        f"- Open positions to protect: `{summary['protect_count']}`",
        f"- Portfolio capital in use: `{_fmt_pct(_to_float(summary['portfolio_capital_used']))}`",
        f"- Portfolio target capital: `{_fmt_pct(_to_float(summary['portfolio_target_capital']))}`",
        "",
        "## Enter Now",
        "",
        "| Symbol | Grade | Size | Confidence | Starter | Readiness | Signal Ret | Reasons |",
        "|---|---|---|---:|---|---|---:|---|",
    ]
    if report["enter_now"]:
        for row in report["enter_now"]:
            lines.append(
                f"| {row['symbol']} | `{row['decision_grade']}` | `{row['size_tier']}` | {_fmt_num(_to_float(row.get('confidence_score')), 2)} | "
                f"`{row.get('starter_reason') or 'n/a'}` | `{row.get('execution_readiness') or 'unknown'}` | {_fmt_pct(_to_float(row.get('signal_ret')))} | "
                f"{', '.join(row.get('starter_matches') or []) or '—'} |"
            )
    else:
        lines.append("| n/a | `n/a` | `n/a` | n/a | `n/a` | `n/a` | n/a | — |")

    lines.extend(
        [
            "",
            "## Watch Candidates",
            "",
            "| Symbol | Bucket | Grade | Starter | Confidence | Readiness | Signal Ret | Blockers |",
            "|---|---|---|---|---:|---|---:|---|",
        ]
    )
    if report["watch_candidates"]:
        for row in report["watch_candidates"]:
            lines.append(
                f"| {row['symbol']} | `{row['decision_bucket']}` | `{row['decision_grade']}` | `{row['starter_grade']}` | {_fmt_num(_to_float(row.get('confidence_score')), 2)} | "
                f"`{row.get('execution_readiness') or 'unknown'}` | {_fmt_pct(_to_float(row.get('signal_ret')))} | {', '.join(row.get('blockers') or []) or '—'} |"
            )
    else:
        lines.append("| n/a | `n/a` | `n/a` | `n/a` | n/a | `n/a` | n/a | — |")

    lines.extend(
        [
            "",
            "## Open Management",
            "",
            "| Symbol | Action | Lots | Hold (h) | Trade Ret | Max Ret | Readiness | Shape | Flags |",
            "|---|---|---:|---:|---:|---:|---|---|---|",
        ]
    )
    if report["open_management"]:
        for row in report["open_management"]:
            lines.append(
                f"| {row['symbol']} | `{row['action']}` | {int(row.get('lots') or 0)} | {_fmt_num(_to_float(row.get('hold_hours')), 2)} | "
                f"{_fmt_pct(_to_float(row.get('current_trade_ret')))} | {_fmt_pct(_to_float(row.get('max_trade_ret')))} | "
                f"`{row.get('execution_readiness') or 'unknown'}` | `{row.get('shape_state') or 'unknown'}` | {', '.join(row.get('flags') or []) or '—'} |"
            )
    else:
        lines.append("| n/a | `n/a` | 0 | n/a | n/a | n/a | `n/a` | `n/a` | — |")

    lines.extend(
        [
            "",
            "## Top Blockers",
            "",
            "| Reason | Count |",
            "|---|---:|",
        ]
    )
    if report["blocked_reason_counts"]:
        for row in report["blocked_reason_counts"][:12]:
            lines.append(f"| `{row['reason']}` | {row['n']} |")
    else:
        lines.append("| `n/a` | 0 |")

    lines.extend(
        [
            "",
            "## Read",
            "",
            "- `enter_now` means the active overlay would open a starter right now if that mint is not already open.",
            "- `watch_candidate` means the lifecycle lane is tradable in principle, but the active pattern is still missing one or two conditions.",
            "- `observe_only` means the decision engine is still intentionally holding the name out of the trade lane.",
            "- `open_management` is the future operator handoff surface for tools like OpenClaw.",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the operator action board for the active meme paper trader.")
    parser.add_argument("--refresh", action="store_true", help="Refresh the upstream scorecard stack before building the board.")
    parser.add_argument("--max-hold-hours", type=float, default=12.0, help="Used only for management warnings.")
    parser.add_argument("--state", type=Path, default=ACTIVE_STATE_JSON)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=OUT_MD)
    args = parser.parse_args()

    active = _load_module("meme_decision_paper_overlay_active_operator_board", ACTIVE_MODULE_PATH)
    if args.refresh:
        active.base._run_refresh()

    now = time.time()
    state = _load_json(args.state, None) or active._blank_active_state(now)
    state = active._ensure_active_state_defaults(state, now)
    live = active.base._load_live_inputs()
    report = build_report(active, state, live, max_hold_hours=float(args.max_hold_hours))
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_md(args.out_md, report)
    print(
        "meme_operator_action_board: "
        f"enter={report['summary']['enter_now_count']} "
        f"watch={report['summary']['watch_candidate_count']} "
        f"open={report['summary']['open_positions']}"
    )


if __name__ == "__main__":
    main()
