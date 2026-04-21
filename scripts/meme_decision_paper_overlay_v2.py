#!/usr/bin/env python3
"""Paper-trade overlay v2 for lifecycle decisions.

This version treats the current lifecycle system as a *management* engine, not only
an entry engine.

Rules:
- Starter entry only on a narrow high-conviction survivor lane: route-ready,
  cleaner-shape names in calmer regimes that have not already overextended.
- Add on `promote_strong` confirmation when shape still supports the move.
- Exit on `cut_hard` / `cut_bias`, `losing_steam`, stop loss, giveback stop,
  matured lifecycle resolution, or max hold timeout.

The goal is to test whether earlier starter entries + lifecycle management behave
better than waiting to buy only at `promote_strong`.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
REPORTS = BASE / "data" / "meme_reports"
SCORECARD_SCRIPT = BASE / "scripts" / "meme_daily_scorecard.py"
STARTER_GATE_SCRIPT = BASE / "scripts" / "meme_starter_entry_gate_research.py"
DECISION_JSON = REPORTS / "meme_decision_tracker.json"
PENDING_JSON = REPORTS / "pending_maturation_report.json"
LIFECYCLE_JSON = REPORTS / "meme_lifecycle_monitor.json"
MARKET_JSON = REPORTS / "meme_market_data_adapter.json"
DATASET_CSV = REPORTS / "meme_anchor_dataset.csv"
STATE_JSON = REPORTS / "meme_decision_paper_overlay_v2_state.json"
JOURNAL_JSONL = REPORTS / "meme_decision_paper_overlay_v2_journal.jsonl"
OUT_JSON = REPORTS / "meme_decision_paper_overlay_v2_report.json"
OUT_MD = REPORTS / "meme_decision_paper_overlay_v2_report.md"

LIVE_STAGES = {"pending_promote_now", "pending_watch", "pending_cut_bias", "emerging_watchlist"}
MATURED_STAGES = {"matured_survivor", "matured_failed"}
CALMER_REGIMES = {"late_slow_expansion", "calm_continuation"}
BAD_SHAPES = {"losing_steam", "blowoff_risk"}
SUPPORTIVE_SHAPES = {"extending_cleanly", "holding_pullback", "stalling_but_alive", "too_early", "forming"}
CONFIRM_SHAPES = {"extending_cleanly", "holding_pullback"}
STARTER_MIN_SIGNAL_RET = 0.15
STARTER_MAX_SIGNAL_RET = 1.25
STARTER_PROBE_MAX_SIGNAL_RET = 0.90
HIGH_CONVICTION_BREAKOUT_MAX_SIGNAL_RET = 0.45
HIGH_CONVICTION_PROMOTE_MAX_SIGNAL_RET = 0.60
HIGH_CONVICTION_MIN_LIQUIDITY_USD = 25000.0
HIGH_CONVICTION_MAX_IMPACT = 0.0075
HIGH_CONVICTION_MIN_SURVIVOR_FIT = 52.0
ADD_MAX_SIGNAL_RET = 1.10
ADD_CONFIRM_MIN_HOURS = 0.25
ADD_CONFIRM_MIN_TRADE_RET = 0.05
STOP_LOSS = -0.25
FIRST_HOUR_FAIL_RET = -0.08
FIRST_HOUR_FAIL_HOURS = 1.00
EARLY_SURVIVAL_MIN_PEAK = 0.12
EARLY_SURVIVAL_FAIL_RET = -0.03
EARLY_SURVIVAL_FAIL_HOURS = 2.00
THREE_HOUR_PROVE_PEAK = 0.20
THREE_HOUR_FLOOR_RET = 0.02
THREE_HOUR_FAIL_HOURS = 3.00
EARLY_SHAPE_FAIL_RET = -0.12
EARLY_SHAPE_FAIL_HOURS = 0.75
READINESS_BREAK_RET = -0.08
READINESS_BREAK_HOURS = 0.25
READINESS_DEGRADE_RET = 0.02
READINESS_DEGRADE_HOURS = 0.20
ROUND_TRIP_PROTECT_RET = -0.10
ROUND_TRIP_PROTECT_HOURS = 1.5
EXTEND_RETRACE_MIN_PEAK = 0.35
EXTEND_RETRACE_FLOOR_RET = 0.05
EXTEND_RETRACE_HOURS = 0.75
STALLED_EXTEND_RET = -0.05
STALLED_EXTEND_HOURS = 0.50
BREAK_EVEN_ARM_RET = 0.25
BREAK_EVEN_FLOOR_RET = 0.00
BREAK_EVEN_HOURS = 0.50
PROFIT_LOCK_ARM_RET = 0.60
PROFIT_LOCK_FLOOR_RET = 0.20
PROFIT_LOCK_HOURS = 1.00
GIVEBACK_MIN_PEAK = 0.75
GIVEBACK_RETAIN = 0.45
MAX_CAPITAL = 1.0
RISKY_READINESS = {"thin", "high_impact", "overheated", "fragile", "no_route"}
WEAK_SHAPES = {"holding_pullback", "stalling_but_alive"}
SIZE_PROFILES = {
    "full": {"starter_weight": 0.70, "target_capital": 1.00},
    "medium": {"starter_weight": 0.55, "target_capital": 0.85},
    "small": {"starter_weight": 0.35, "target_capital": 0.65},
    "probe": {"starter_weight": 0.20, "target_capital": 0.40},
}


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STARTER_GATE_MODULE = _load_module("meme_starter_entry_gate_research", STARTER_GATE_SCRIPT)


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text())


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload) + "\n")


def _load_dataset(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            mint = str(row.get("mint") or "")
            if mint:
                out[mint] = row
    return out


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


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100.0:.1f}%"


def _fmt_num(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def _trade_return(entry_signal_ret: float | None, exit_signal_ret: float | None) -> float | None:
    if entry_signal_ret is None or exit_signal_ret is None:
        return None
    entry_mult = 1.0 + float(entry_signal_ret)
    exit_mult = 1.0 + float(exit_signal_ret)
    if entry_mult <= 0.0 or exit_mult <= 0.0:
        return None
    return (exit_mult / entry_mult) - 1.0


def _weighted_trade_return(lots: list[dict[str, Any]], signal_ret: float | None) -> float | None:
    total_weight = 0.0
    weighted = 0.0
    for lot in lots:
        weight = float(lot.get("weight") or 0.0)
        if weight <= 0.0:
            continue
        ret = _trade_return(_to_float(lot.get("entry_signal_ret")), signal_ret)
        if ret is None:
            continue
        total_weight += weight
        weighted += weight * ret
    if total_weight <= 0.0:
        return None
    return weighted / total_weight


def _median(values: list[float | None]) -> float | None:
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not clean:
        return None
    return float(statistics.median(clean))


def _run_refresh() -> bool:
    try:
        subprocess.run(
            ["python3", str(SCORECARD_SCRIPT), "--refresh"],
            cwd=BASE,
            check=True,
            capture_output=True,
            text=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def _index_by_mint(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        mint = str(row.get("mint") or "")
        if mint and mint not in out:
            out[mint] = row
    return out


def _load_live_inputs() -> dict[str, Any]:
    decision = _load_json(DECISION_JSON, {})
    pending = _load_json(PENDING_JSON, {})
    lifecycle = _load_json(LIFECYCLE_JSON, {})
    market = _load_json(MARKET_JSON, {})
    dataset = _load_dataset(DATASET_CSV)
    return {
        "decision": decision,
        "pending": pending,
        "lifecycle": lifecycle,
        "market": market,
        "dataset": dataset,
        "decision_by_mint": _index_by_mint(list(decision.get("live_rows") or [])),
        "pending_by_mint": _index_by_mint(list(pending.get("pending_rows") or [])),
        "lifecycle_by_mint": _index_by_mint(list(lifecycle.get("board") or [])),
        "market_by_mint": _index_by_mint(list(market.get("rows") or [])),
    }


def _ensure_state_defaults(state: dict[str, Any], now: float) -> dict[str, Any]:
    if "cohort_label" not in state:
        state["cohort_label"] = "legacy_v2"
    if "cohort_started_at" not in state:
        state["cohort_started_at"] = state.get("started_at") or now
    return state


def _blank_state(now: float) -> dict[str, Any]:
    return {
        "started_at": now,
        "updated_at": now,
        "cohort_label": "legacy_v2",
        "cohort_started_at": now,
        "open_positions": [],
        "closed_positions": [],
        "seen_mints": [],
    }


def _current_signal_ret(mint: str, *, pending_by_mint: dict[str, dict[str, Any]], dataset_by_mint: dict[str, dict[str, Any]]) -> float | None:
    pending_row = pending_by_mint.get(mint)
    if pending_row is not None:
        value = _to_float(pending_row.get("latest_ret"))
        if value is not None:
            return value
    ds = dataset_by_mint.get(mint)
    if ds is not None:
        for key in ("ret_21600s", "ret_3600s", "ret_1800s", "ret_900s"):
            value = _to_float(ds.get(key))
            if value is not None:
                return value
    return None


def _passes_high_conviction_market_gate(
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
    if shape_state not in CONFIRM_SHAPES:
        return False
    if regime not in CALMER_REGIMES:
        return False
    if signal_ret is None or signal_ret > signal_ret_cap:
        return False
    if survivor_fit < HIGH_CONVICTION_MIN_SURVIVOR_FIT:
        return False
    if liquidity is not None and liquidity < HIGH_CONVICTION_MIN_LIQUIDITY_USD:
        return False
    if impact is not None and impact > HIGH_CONVICTION_MAX_IMPACT:
        return False
    return True


def _starter_reason(
    decision_row: dict[str, Any],
    pending_row: dict[str, Any],
    market_row: dict[str, Any] | None,
    *,
    signal_ret: float | None,
) -> str | None:
    if signal_ret is None or signal_ret < STARTER_MIN_SIGNAL_RET or signal_ret > STARTER_MAX_SIGNAL_RET:
        return None
    bucket = str(decision_row.get("decision_bucket") or "")
    if bucket not in {"watch", "promote"}:
        return None
    stage = str(decision_row.get("stage") or "")
    if stage not in LIVE_STAGES or stage == "pending_cut_bias":
        return None
    shape_state = str(decision_row.get("shape_state") or pending_row.get("shape_state") or "unknown")
    if shape_state in BAD_SHAPES:
        return None
    useful = float(decision_row.get("useful_score") or 0.0)
    persistent = float(decision_row.get("persistent_score") or 0.0)
    survivor_fit = float(decision_row.get("survivor_fit") or 0.0)
    regime = str(decision_row.get("regime") or "unknown")
    grade = str(decision_row.get("decision_grade") or "")
    status = str(decision_row.get("status") or pending_row.get("promotion_decision") or "")
    readiness = str((market_row or {}).get("execution_readiness") or "")
    liquidity = _to_float((market_row or {}).get("dex_liquidity_usd"))
    impact = _to_float((market_row or {}).get("jupiter_price_impact_pct"))
    starter_eval = STARTER_GATE_MODULE.starter_gate_evaluate(pending_row)
    starter_grade = str(starter_eval.get("starter_grade") or "starter_neutral")

    if market_row is not None and readiness not in {"route_ready", "route_ready_meteora"}:
        return None
    if starter_grade == "starter_avoid":
        return None
    if starter_grade == "starter_probe" and signal_ret > STARTER_PROBE_MAX_SIGNAL_RET:
        return None

    high_conviction_breakout = _passes_high_conviction_market_gate(
        readiness=readiness,
        shape_state=shape_state,
        regime=regime,
        signal_ret=signal_ret,
        survivor_fit=survivor_fit,
        liquidity=liquidity,
        impact=impact,
        signal_ret_cap=HIGH_CONVICTION_BREAKOUT_MAX_SIGNAL_RET,
    )
    high_conviction_promote = _passes_high_conviction_market_gate(
        readiness=readiness,
        shape_state=shape_state,
        regime=regime,
        signal_ret=signal_ret,
        survivor_fit=survivor_fit,
        liquidity=liquidity,
        impact=impact,
        signal_ret_cap=HIGH_CONVICTION_PROMOTE_MAX_SIGNAL_RET,
    )

    if starter_grade == "starter_strong":
        if grade == "promote_strong" and high_conviction_breakout:
            return "starter_strong_breakout_confirmed"
        if high_conviction_breakout and useful >= 45.0:
            return "starter_strong_breakout"
        return None

    if starter_grade == "starter_probe":
        if high_conviction_breakout and survivor_fit >= 55.0 and useful >= 55.0:
            return "starter_probe_core"
        if high_conviction_breakout and persistent >= 60.0:
            return "starter_probe_calm_persistence"
        if (
            high_conviction_breakout
            and bucket == "watch"
            and status in {"watch_to_60m", "hold_and_recheck"}
            and useful >= 55.0
            and survivor_fit >= 52.0
        ):
            return "starter_probe_watch_setup"

    # Allow a narrow survivor-lane entry for calm, route-ready live promotes that
    # our starter research would otherwise miss. This intentionally prefers
    # cleaner names like AOW over loud, already-extended tape winners.
    if (
        starter_grade == "starter_neutral"
        and bucket == "promote"
        and grade == "promote_strong"
        and status == "promote_now"
        and high_conviction_promote
    ):
        return "starter_strong_survivor_lane"
    return None


def _confidence_profile(
    decision_row: dict[str, Any],
    pending_row: dict[str, Any],
    market_row: dict[str, Any] | None,
    *,
    starter_reason: str,
) -> dict[str, Any]:
    useful = float(decision_row.get("useful_score") or 0.0)
    persistent = float(decision_row.get("persistent_score") or 0.0)
    survivor_fit = float(decision_row.get("survivor_fit") or 0.0)
    decision_grade = str(decision_row.get("decision_grade") or "")
    shape_state = str(decision_row.get("shape_state") or pending_row.get("shape_state") or "unknown")
    readiness = str((market_row or {}).get("execution_readiness") or "")
    impact = _to_float((market_row or {}).get("jupiter_price_impact_pct"))
    liquidity = _to_float((market_row or {}).get("dex_liquidity_usd"))

    strong_path = starter_reason.startswith("starter_strong") or starter_reason == "promote_strong_confirmation"
    score = 0.15 if starter_reason.startswith("starter_probe") else 0.28

    if decision_grade == "promote_strong":
        score += 0.12
    elif decision_grade == "promote_probe":
        score += 0.06
    elif decision_grade == "watch_setup":
        score += 0.03

    if shape_state == "extending_cleanly":
        score += 0.16
    elif shape_state == "holding_pullback":
        score += 0.09
    elif shape_state in {"stalling_but_alive", "too_early", "forming"}:
        score += 0.04

    if useful >= 65.0:
        score += 0.12
    elif useful >= 50.0:
        score += 0.08
    elif useful >= 35.0:
        score += 0.04

    if survivor_fit >= 70.0:
        score += 0.14
    elif survivor_fit >= 60.0:
        score += 0.10
    elif survivor_fit >= 50.0:
        score += 0.06

    if persistent >= 70.0:
        score += 0.08
    elif persistent >= 45.0:
        score += 0.04

    if readiness == "route_ready_meteora":
        score += 0.08
    elif readiness == "route_ready":
        score += 0.05

    if impact is not None:
        if impact <= 0.0025:
            score += 0.05
        elif impact <= 0.01:
            score += 0.03
        elif impact >= 0.04:
            score -= 0.10

    if liquidity is not None:
        if liquidity >= 25000.0:
            score += 0.05
        elif liquidity >= 10000.0:
            score += 0.03
        elif liquidity < 5000.0:
            score -= 0.08

    score = max(0.0, min(1.0, score))

    if score >= 0.75:
        tier = "full"
    elif score >= 0.60:
        tier = "medium"
    elif score >= 0.45:
        tier = "small"
    else:
        tier = "probe"

    if not strong_path and tier == "full":
        tier = "medium"

    profile = dict(SIZE_PROFILES[tier])
    profile.update(
        {
            "confidence_score": score,
            "size_tier": tier,
            "starter_reason": starter_reason,
            "readiness": readiness or "unknown",
        }
    )
    return profile


def _can_add_confirmation(
    pos: dict[str, Any],
    decision_row: dict[str, Any],
    pending_row: dict[str, Any],
    market_row: dict[str, Any] | None,
    *,
    now: float,
    signal_ret: float | None,
) -> dict[str, Any] | None:
    if signal_ret is None or signal_ret > ADD_MAX_SIGNAL_RET:
        return None
    if str(pos.get("entry_decision_grade") or "") == "promote_strong":
        return None
    if (now - float(pos.get("opened_at") or now)) < (ADD_CONFIRM_MIN_HOURS * 3600.0):
        return None
    if float(pos.get("capital_used") or 0.0) >= float(pos.get("target_capital") or MAX_CAPITAL):
        return None
    if any(str(lot.get("kind") or "") == "add" for lot in pos.get("lots") or []):
        return None
    if str(decision_row.get("decision_grade") or "") != "promote_strong":
        return None
    current_trade_ret = _weighted_trade_return(list(pos.get("lots") or []), signal_ret)
    if current_trade_ret is None or current_trade_ret < ADD_CONFIRM_MIN_TRADE_RET:
        return None
    shape_state = str(decision_row.get("shape_state") or pending_row.get("shape_state") or "unknown")
    if shape_state not in CONFIRM_SHAPES:
        return None
    readiness = str((market_row or {}).get("execution_readiness") or "")
    if market_row is not None and readiness not in {"route_ready", "route_ready_meteora"}:
        return None
    profile = _confidence_profile(decision_row, pending_row, market_row, starter_reason="promote_strong_confirmation")
    if profile["target_capital"] <= float(pos.get("capital_used") or 0.0):
        return None
    return profile


def _new_position(
    now: float,
    decision_row: dict[str, Any],
    pending_row: dict[str, Any],
    *,
    signal_ret: float,
    starter_reason: str,
    profile: dict[str, Any],
) -> dict[str, Any]:
    starter_eval = STARTER_GATE_MODULE.starter_gate_evaluate(pending_row)
    starter_weight = float(profile["starter_weight"])
    lot = {
        "kind": "starter",
        "weight": starter_weight,
        "opened_at": now,
        "entry_signal_ret": signal_ret,
        "reason": starter_reason,
        "decision_grade": decision_row.get("decision_grade") or "unknown",
        "size_tier": profile["size_tier"],
        "confidence_score": profile["confidence_score"],
    }
    return {
        "mint": decision_row.get("mint") or "",
        "symbol": decision_row.get("symbol") or pending_row.get("symbol") or "n/a",
        "opened_at": now,
        "lots": [lot],
        "capital_used": starter_weight,
        "entry_decision_bucket": decision_row.get("decision_bucket") or "watch",
        "entry_decision_grade": decision_row.get("decision_grade") or "unknown",
        "entry_status": decision_row.get("status") or pending_row.get("promotion_decision") or "unknown",
        "entry_shape_state": decision_row.get("shape_state") or pending_row.get("shape_state") or "unknown",
        "entry_shape_path": decision_row.get("shape_path_30_to_60") or pending_row.get("shape_path_30_to_60"),
        "entry_starter_grade": starter_eval.get("starter_grade") or "starter_neutral",
        "entry_starter_matches": list(starter_eval.get("matches") or []),
        "entry_starter_reason": starter_reason,
        "entry_starter_weight": starter_weight,
        "entry_confidence_score": profile["confidence_score"],
        "entry_size_tier": profile["size_tier"],
        "target_capital": profile["target_capital"],
        "current_signal_ret": signal_ret,
        "current_trade_ret": 0.0,
        "max_trade_ret": 0.0,
        "max_signal_ret": signal_ret,
        "last_status": decision_row.get("status") or "unknown",
        "last_shape_state": decision_row.get("shape_state") or pending_row.get("shape_state") or "unknown",
        "last_decision_grade": decision_row.get("decision_grade") or "unknown",
        "closed": False,
    }


def _add_lot(
    pos: dict[str, Any],
    now: float,
    decision_row: dict[str, Any],
    *,
    signal_ret: float,
    profile: dict[str, Any],
) -> None:
    pos["target_capital"] = max(float(pos.get("target_capital") or 0.0), float(profile.get("target_capital") or 0.0))
    pos["entry_confidence_score"] = max(float(pos.get("entry_confidence_score") or 0.0), float(profile.get("confidence_score") or 0.0))
    pos["entry_size_tier"] = str(profile.get("size_tier") or pos.get("entry_size_tier") or "probe")
    remaining_weight = max(0.0, float(pos.get("target_capital") or MAX_CAPITAL) - float(pos.get("capital_used") or 0.0))
    if remaining_weight <= 0.0:
        return
    pos.setdefault("lots", []).append(
        {
            "kind": "add",
            "weight": remaining_weight,
            "opened_at": now,
            "entry_signal_ret": signal_ret,
            "reason": profile.get("starter_reason") or "promote_strong_confirmation",
            "decision_grade": decision_row.get("decision_grade") or "promote_strong",
            "size_tier": profile.get("size_tier") or pos.get("entry_size_tier") or "probe",
            "confidence_score": profile.get("confidence_score"),
        }
    )
    pos["capital_used"] = min(MAX_CAPITAL, float(pos.get("capital_used") or 0.0) + remaining_weight)


def _close_outcome(pos: dict[str, Any], *, now: float, reason: str, exit_signal_ret: float | None, lifecycle_row: dict[str, Any] | None, dataset_row: dict[str, Any] | None) -> dict[str, Any]:
    trade_ret = _weighted_trade_return(list(pos.get("lots") or []), exit_signal_ret)
    outcome_class = None
    if lifecycle_row is not None:
        outcome_class = lifecycle_row.get("status")
    if not outcome_class and dataset_row is not None:
        outcome_class = dataset_row.get("persistence_class")
    return {
        **pos,
        "closed": True,
        "closed_at": now,
        "exit_reason": reason,
        "exit_signal_ret": exit_signal_ret,
        "final_trade_ret": trade_ret,
        "hold_hours": (now - float(pos.get("opened_at") or now)) / 3600.0,
        "outcome_class": outcome_class or "unknown",
        "final_stage": (lifecycle_row or {}).get("stage") or "unknown",
        "final_status": (lifecycle_row or {}).get("status") or "unknown",
    }


def _step(state: dict[str, Any], live: dict[str, Any], *, now: float, max_hold_hours: float) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    open_positions = list(state.get("open_positions") or [])
    closed_positions = list(state.get("closed_positions") or [])
    seen_mints = set(str(m) for m in (state.get("seen_mints") or []))
    cohort_label = str(state.get("cohort_label") or "legacy_v2")

    decision_by_mint = live["decision_by_mint"]
    pending_by_mint = live["pending_by_mint"]
    lifecycle_by_mint = live["lifecycle_by_mint"]
    dataset_by_mint = live["dataset"]
    market_by_mint = live["market_by_mint"]

    open_by_mint = {str(pos.get("mint") or ""): pos for pos in open_positions if pos.get("mint")}

    for mint, decision_row in decision_by_mint.items():
        pending_row = pending_by_mint.get(mint)
        if pending_row is None:
            continue
        market_row = market_by_mint.get(mint)
        signal_ret = _current_signal_ret(mint, pending_by_mint=pending_by_mint, dataset_by_mint=dataset_by_mint)
        pos = open_by_mint.get(mint)

        if pos is None and mint not in seen_mints:
            starter_reason = _starter_reason(decision_row, pending_row, market_row, signal_ret=signal_ret)
            if starter_reason and signal_ret is not None:
                profile = _confidence_profile(decision_row, pending_row, market_row, starter_reason=starter_reason)
                pos = _new_position(
                    now,
                    decision_row,
                    pending_row,
                    signal_ret=signal_ret,
                    starter_reason=starter_reason,
                    profile=profile,
                )
                pos["cohort"] = cohort_label
                if market_row is not None:
                    pos["entry_execution_readiness"] = market_row.get("execution_readiness")
                    pos["entry_jupiter_price_impact_pct"] = market_row.get("jupiter_price_impact_pct")
                    pos["entry_dex_liquidity_usd"] = market_row.get("dex_liquidity_usd")
                pos["entry_confidence_score"] = profile.get("confidence_score")
                pos["entry_size_tier"] = profile.get("size_tier")
                pos["target_capital"] = profile.get("target_capital")
                open_positions.append(pos)
                open_by_mint[mint] = pos
                seen_mints.add(mint)
                events.append(
                    {
                        "ts": now,
                        "event": "open_starter",
                        "mint": mint,
                        "symbol": pos.get("symbol") or "n/a",
                        "entry_signal_ret": signal_ret,
                        "starter_reason": starter_reason,
                        "starter_grade": pos.get("entry_starter_grade"),
                        "size_tier": pos.get("entry_size_tier"),
                        "starter_weight": pos.get("entry_starter_weight"),
                        "decision_grade": decision_row.get("decision_grade"),
                        "shape_state": decision_row.get("shape_state"),
                    }
                )

        if pos is not None:
            add_profile = _can_add_confirmation(pos, decision_row, pending_row, market_row, now=now, signal_ret=signal_ret)
            if add_profile and signal_ret is not None:
                _add_lot(pos, now, decision_row, signal_ret=signal_ret, profile=add_profile)
                events.append(
                    {
                        "ts": now,
                        "event": "add_confirmation",
                        "mint": mint,
                        "symbol": pos.get("symbol") or "n/a",
                        "entry_signal_ret": signal_ret,
                        "reason": add_profile.get("starter_reason") or "promote_strong_confirmation",
                        "size_tier": add_profile.get("size_tier"),
                        "shape_state": decision_row.get("shape_state"),
                    }
                )

    still_open: list[dict[str, Any]] = []
    for pos in open_positions:
        mint = str(pos.get("mint") or "")
        decision_row = decision_by_mint.get(mint)
        pending_row = pending_by_mint.get(mint)
        lifecycle_row = lifecycle_by_mint.get(mint)
        dataset_row = dataset_by_mint.get(mint)

        current_signal_ret = _current_signal_ret(mint, pending_by_mint=pending_by_mint, dataset_by_mint=dataset_by_mint)
        current_trade_ret = _weighted_trade_return(list(pos.get("lots") or []), current_signal_ret)
        pos["current_signal_ret"] = current_signal_ret
        pos["current_trade_ret"] = current_trade_ret
        if current_signal_ret is not None:
            pos["max_signal_ret"] = max(float(pos.get("max_signal_ret") or current_signal_ret), current_signal_ret)
        if current_trade_ret is not None:
            pos["max_trade_ret"] = max(float(pos.get("max_trade_ret") or current_trade_ret), current_trade_ret)
        if decision_row is not None:
            pos["last_status"] = decision_row.get("status") or pos.get("last_status")
            pos["last_shape_state"] = decision_row.get("shape_state") or pos.get("last_shape_state")
            pos["last_decision_grade"] = decision_row.get("decision_grade") or pos.get("last_decision_grade")
        market_row = market_by_mint.get(mint)
        if market_row is not None:
            pos["last_execution_readiness"] = market_row.get("execution_readiness")

        reason = None
        exit_signal_ret = current_signal_ret
        stage = str((lifecycle_row or {}).get("stage") or "")
        status = str((decision_row or {}).get("status") or (pending_row or {}).get("promotion_decision") or "")
        decision_bucket = str((decision_row or {}).get("decision_bucket") or "")
        shape_state = str((decision_row or {}).get("shape_state") or (pending_row or {}).get("shape_state") or "")
        readiness = str((market_row or {}).get("execution_readiness") or pos.get("last_execution_readiness") or pos.get("entry_execution_readiness") or "")
        entry_readiness = str(pos.get("entry_execution_readiness") or "")
        entry_shape = str(pos.get("entry_shape_state") or "")
        hold_hours = (now - float(pos.get("opened_at") or now)) / 3600.0
        max_trade_ret = _to_float(pos.get("max_trade_ret"))

        if stage in MATURED_STAGES:
            ds_ret = _to_float((dataset_row or {}).get("ret_21600s"))
            if ds_ret is not None:
                exit_signal_ret = ds_ret
            reason = f"matured:{stage}:{(lifecycle_row or {}).get('status') or 'unknown'}"
        elif (
            current_trade_ret is not None
            and hold_hours >= FIRST_HOUR_FAIL_HOURS
            and current_trade_ret <= FIRST_HOUR_FAIL_RET
        ):
            reason = "first_hour_fail"
        elif (
            current_trade_ret is not None
            and max_trade_ret is not None
            and hold_hours >= EARLY_SURVIVAL_FAIL_HOURS
            and max_trade_ret < EARLY_SURVIVAL_MIN_PEAK
            and current_trade_ret <= EARLY_SURVIVAL_FAIL_RET
        ):
            reason = "early_survival_fail"
        elif (
            current_trade_ret is not None
            and max_trade_ret is not None
            and hold_hours >= THREE_HOUR_FAIL_HOURS
            and max_trade_ret < THREE_HOUR_PROVE_PEAK
            and current_trade_ret <= THREE_HOUR_FLOOR_RET
        ):
            reason = "three_hour_fail"
        elif decision_bucket == "cut" or status == "cut_bias" or shape_state == "losing_steam":
            reason = "decision_cut"
        elif (
            current_trade_ret is not None
            and hold_hours >= READINESS_BREAK_HOURS
            and readiness in RISKY_READINESS
            and current_trade_ret <= READINESS_BREAK_RET
        ):
            reason = "readiness_break"
        elif (
            current_trade_ret is not None
            and hold_hours >= READINESS_DEGRADE_HOURS
            and entry_readiness in {"route_ready", "route_ready_meteora"}
            and readiness in RISKY_READINESS
            and current_trade_ret <= READINESS_DEGRADE_RET
        ):
            reason = "readiness_degrade"
        elif (
            current_trade_ret is not None
            and hold_hours >= EARLY_SHAPE_FAIL_HOURS
            and shape_state in WEAK_SHAPES
            and current_trade_ret <= EARLY_SHAPE_FAIL_RET
        ):
            reason = "shape_deterioration"
        elif (
            current_trade_ret is not None
            and max_trade_ret is not None
            and hold_hours >= EXTEND_RETRACE_HOURS
            and shape_state == "extending_cleanly"
            and max_trade_ret >= EXTEND_RETRACE_MIN_PEAK
            and current_trade_ret <= EXTEND_RETRACE_FLOOR_RET
        ):
            reason = "extend_retrace"
        elif (
            current_trade_ret is not None
            and hold_hours >= STALLED_EXTEND_HOURS
            and shape_state == "extending_cleanly"
            and current_trade_ret <= STALLED_EXTEND_RET
        ):
            reason = "stalled_extend"
        elif (
            current_trade_ret is not None
            and hold_hours >= ROUND_TRIP_PROTECT_HOURS
            and entry_shape == "extending_cleanly"
            and shape_state == "extending_cleanly"
            and current_trade_ret <= ROUND_TRIP_PROTECT_RET
        ):
            reason = "round_trip_protect"
        elif (
            current_trade_ret is not None
            and max_trade_ret is not None
            and hold_hours >= BREAK_EVEN_HOURS
            and max_trade_ret >= BREAK_EVEN_ARM_RET
            and current_trade_ret <= BREAK_EVEN_FLOOR_RET
        ):
            reason = "break_even_protect"
        elif (
            current_trade_ret is not None
            and max_trade_ret is not None
            and hold_hours >= PROFIT_LOCK_HOURS
            and max_trade_ret >= PROFIT_LOCK_ARM_RET
            and current_trade_ret <= PROFIT_LOCK_FLOOR_RET
        ):
            reason = "profit_lock"
        elif current_trade_ret is not None and current_trade_ret <= STOP_LOSS:
            reason = "stop_loss"
        elif (
            current_trade_ret is not None
            and max_trade_ret is not None
            and max_trade_ret >= GIVEBACK_MIN_PEAK
            and current_trade_ret <= (max_trade_ret * GIVEBACK_RETAIN)
            and shape_state not in {"extending_cleanly"}
        ):
            reason = "giveback_stop"
        elif max_hold_hours > 0 and hold_hours >= max_hold_hours:
            reason = "max_hold"

        if reason:
            closed = _close_outcome(
                pos,
                now=now,
                reason=reason,
                exit_signal_ret=exit_signal_ret,
                lifecycle_row=lifecycle_row,
                dataset_row=dataset_row,
            )
            closed_positions.append(closed)
            events.append(
                {
                    "ts": now,
                    "event": "close",
                    "mint": mint,
                    "symbol": closed.get("symbol") or "n/a",
                    "exit_reason": reason,
                    "final_trade_ret": closed.get("final_trade_ret"),
                    "outcome_class": closed.get("outcome_class"),
                }
            )
        else:
            still_open.append(pos)

    next_state = {
        **state,
        "updated_at": now,
        "open_positions": still_open,
        "closed_positions": closed_positions,
        "seen_mints": sorted(seen_mints),
    }
    return next_state, events


def _build_report(state: dict[str, Any]) -> dict[str, Any]:
    open_positions = list(state.get("open_positions") or [])
    closed_positions = list(state.get("closed_positions") or [])
    open_trade_rets = [_to_float(row.get("current_trade_ret")) for row in open_positions]
    closed_trade_rets = [_to_float(row.get("final_trade_ret")) for row in closed_positions]
    wins = [ret for ret in closed_trade_rets if ret is not None and ret > 0.0]
    counts_by_reason: dict[str, int] = {}
    counts_by_outcome: dict[str, int] = {}
    counts_by_lot_kind: dict[str, int] = {"starter_only": 0, "starter_plus_add": 0}
    for row in closed_positions:
        counts_by_reason[str(row.get("exit_reason") or "unknown")] = counts_by_reason.get(str(row.get("exit_reason") or "unknown"), 0) + 1
        counts_by_outcome[str(row.get("outcome_class") or "unknown")] = counts_by_outcome.get(str(row.get("outcome_class") or "unknown"), 0) + 1
        lot_kinds = {str(lot.get("kind") or "") for lot in (row.get("lots") or [])}
        if "add" in lot_kinds:
            counts_by_lot_kind["starter_plus_add"] += 1
        else:
            counts_by_lot_kind["starter_only"] += 1

    report = {
        "generated_at": time.time(),
        "run_started_at": state.get("started_at"),
        "active_cohort": {
            "label": state.get("cohort_label") or "legacy_v2",
            "started_at": state.get("cohort_started_at") or state.get("started_at"),
        },
        "summary": {
            "open_positions": len(open_positions),
            "closed_positions": len(closed_positions),
            "closed_winrate": (len(wins) / len(closed_trade_rets)) if closed_trade_rets else None,
            "closed_avg_return": (sum(ret for ret in closed_trade_rets if ret is not None) / len(closed_trade_rets)) if closed_trade_rets else None,
            "closed_median_return": _median(closed_trade_rets),
            "open_avg_mark": (sum(ret for ret in open_trade_rets if ret is not None) / len(open_trade_rets)) if open_trade_rets else None,
            "open_median_mark": _median(open_trade_rets),
            "counts_by_reason": counts_by_reason,
            "counts_by_outcome": counts_by_outcome,
            "counts_by_lot_kind": counts_by_lot_kind,
        },
        "open_positions": open_positions,
        "closed_positions": closed_positions[-25:],
    }
    return report


def _write_md(path: Path, report: dict[str, Any]) -> None:
    s = report["summary"]
    lines = [
        "# Decision Paper Overlay V2",
        "",
        "Earlier starter-entry paper overlay with confirmation adds and shape-based protection.",
        "",
        "Rules:",
        f"- Starter entry only on the high-conviction survivor lane: calmer `watch` / `promote` names that stay route-ready and are roughly between `{STARTER_MIN_SIGNAL_RET*100:.0f}%` and `{HIGH_CONVICTION_PROMOTE_MAX_SIGNAL_RET*100:.0f}%` from signal.",
        "- Add on `promote_strong` confirmation when shape still supports the move.",
        "- Enforce a first-3h survival guard so weak starters get cut before they can become full decision-cut disasters.",
        "- Exit on `cut_hard` / `cut_bias`, `losing_steam`, readiness/shape failure, break-even/profit locks, stop loss, giveback stop, matured lifecycle outcome, or max hold timeout.",
        "",
        "## Summary",
        "",
        f"- Active cohort: `{((report.get('active_cohort') or {}).get('label')) or 'legacy_v2'}`",
        f"- Open positions: `{s['open_positions']}`",
        f"- Closed positions: `{s['closed_positions']}`",
        f"- Closed winrate: `{_fmt_pct(s['closed_winrate'])}`",
        f"- Closed average return: `{_fmt_pct(s['closed_avg_return'])}`",
        f"- Closed median return: `{_fmt_pct(s['closed_median_return'])}`",
        f"- Open average mark: `{_fmt_pct(s['open_avg_mark'])}`",
        f"- Open median mark: `{_fmt_pct(s['open_median_mark'])}`",
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
            f"| {row.get('symbol') or 'n/a'} | {len(row.get('lots') or [])} | `{row.get('last_decision_grade') or 'unknown'}` | `{row.get('entry_size_tier') or 'probe'}` | `{row.get('entry_starter_grade') or 'starter_neutral'}` | `{row.get('last_execution_readiness') or row.get('entry_execution_readiness') or 'n/a'}` | {_fmt_pct(_to_float(row.get('entry_starter_weight')))} | {_fmt_pct(_to_float(row.get('target_capital')))} | `{row.get('entry_shape_state')}` | `{row.get('last_shape_state')}` | "
            f"{_fmt_pct(_to_float(row.get('current_signal_ret')))} | {_fmt_pct(_to_float(row.get('current_trade_ret')))} | `{row.get('last_status') or 'unknown'}` | `{row.get('cohort') or 'legacy_v2'}` |"
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
            f"| {row.get('symbol') or 'n/a'} | {len(row.get('lots') or [])} | `{row.get('exit_reason') or 'unknown'}` | `{row.get('outcome_class') or 'unknown'}` | `{row.get('entry_size_tier') or 'probe'}` | {_fmt_pct(_to_float(row.get('target_capital')))} | "
            f"{_fmt_num(_to_float(row.get('hold_hours')), 2)} | {_fmt_pct(_to_float(row.get('exit_signal_ret')))} | {_fmt_pct(_to_float(row.get('final_trade_ret')))} | `{row.get('cohort') or 'legacy_v2'}` |"
        )
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def run_once(*, state_path: Path, journal_path: Path, out_json: Path, out_md: Path, do_refresh: bool, max_hold_hours: float) -> dict[str, Any]:
    now = time.time()
    if do_refresh:
        _run_refresh()

    state = _ensure_state_defaults(_load_json(state_path, None) or _blank_state(now), now)
    live = _load_live_inputs()
    next_state, events = _step(state, live, now=now, max_hold_hours=max_hold_hours)
    _write_json(state_path, next_state)
    for event in events:
        _append_jsonl(journal_path, event)
    report = _build_report(next_state)
    _write_json(out_json, report)
    _write_md(out_md, report)
    print(
        "meme_decision_paper_overlay_v2: "
        f"open={report['summary']['open_positions']} "
        f"closed={report['summary']['closed_positions']}"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Paper-trade overlay v2 for lifecycle decisions.")
    parser.add_argument("--loop", action="store_true", help="Run continuously.")
    parser.add_argument("--interval-sec", type=int, default=900, help="Loop interval in seconds.")
    parser.add_argument("--max-hold-hours", type=float, default=12.0, help="Close positions after this long if still open.")
    parser.add_argument("--no-refresh", action="store_true", help="Do not refresh the main scorecard before stepping.")
    parser.add_argument("--reset", action="store_true", help="Start a fresh overlay state.")
    parser.add_argument("--start-new-cohort", action="store_true", help="Mark future entries as a fresh clean cohort without wiping history.")
    parser.add_argument("--state", type=Path, default=STATE_JSON)
    parser.add_argument("--journal", type=Path, default=JOURNAL_JSONL)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=OUT_MD)
    args = parser.parse_args()

    if args.reset:
        now = time.time()
        _write_json(args.state, _blank_state(now))
        if args.journal.exists():
            args.journal.unlink()

    if args.start_new_cohort:
        now = time.time()
        state = _load_json(args.state, None) or _blank_state(now)
        state["cohort_started_at"] = now
        state["cohort_label"] = f"v2_clean_{time.strftime('%Y%m%d_%H%M%S', time.localtime(now))}"
        _write_json(args.state, state)

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
