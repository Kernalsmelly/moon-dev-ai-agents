#!/usr/bin/env python3
"""Run a paper-trade overlay on top of the lifecycle decision engine.

This is intentionally simple:
- enter on live `promote_strong`
- exit on `cut_hard` / `cut_bias`, matured lifecycle outcome, or max hold timeout
- mark open positions to market using the latest signal-relative return

The goal is not perfect execution realism. The goal is to answer:
"If we had followed our current decision engine, would the overnight book look better?"
"""

from __future__ import annotations

import argparse
import csv
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
DECISION_JSON = REPORTS / "meme_decision_tracker.json"
PENDING_JSON = REPORTS / "pending_maturation_report.json"
LIFECYCLE_JSON = REPORTS / "meme_lifecycle_monitor.json"
DATASET_CSV = REPORTS / "meme_anchor_dataset.csv"
STATE_JSON = REPORTS / "meme_decision_paper_overlay_state.json"
JOURNAL_JSONL = REPORTS / "meme_decision_paper_overlay_journal.jsonl"
OUT_JSON = REPORTS / "meme_decision_paper_overlay_report.json"
OUT_MD = REPORTS / "meme_decision_paper_overlay_report.md"

OPEN_ENTRY_GRADES = {"promote_strong"}
LIVE_STAGES = {"pending_promote_now", "pending_watch", "pending_cut_bias", "emerging_watchlist"}
MATURED_STAGES = {"matured_survivor", "matured_failed"}
STOP_LOSS = -0.25
EARLY_SHAPE_FAIL_RET = -0.12
EARLY_SHAPE_FAIL_HOURS = 0.75
ROUND_TRIP_PROTECT_RET = -0.10
ROUND_TRIP_PROTECT_HOURS = 1.50
BREAK_EVEN_ARM_RET = 0.25
BREAK_EVEN_FLOOR_RET = 0.00
BREAK_EVEN_HOURS = 0.50
PROFIT_LOCK_ARM_RET = 0.60
PROFIT_LOCK_FLOOR_RET = 0.20
PROFIT_LOCK_HOURS = 1.00
EXTEND_RETRACE_MIN_PEAK = 0.35
EXTEND_RETRACE_FLOOR_RET = 0.05
EXTEND_RETRACE_HOURS = 0.75
WEAK_SHAPES = {"holding_pullback", "stalling_but_alive"}


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
    dataset = _load_dataset(DATASET_CSV)
    return {
        "decision": decision,
        "pending": pending,
        "lifecycle": lifecycle,
        "dataset": dataset,
        "decision_by_mint": _index_by_mint(list(decision.get("live_rows") or [])),
        "pending_by_mint": _index_by_mint(list(pending.get("pending_rows") or [])),
        "lifecycle_by_mint": _index_by_mint(list(lifecycle.get("board") or [])),
    }


def _blank_state(now: float) -> dict[str, Any]:
    return {
        "started_at": now,
        "updated_at": now,
        "entry_grades": sorted(OPEN_ENTRY_GRADES),
        "open_positions": [],
        "closed_positions": [],
        "seen_mints": [],
    }


def _open_position(now: float, decision_row: dict[str, Any], pending_row: dict[str, Any]) -> dict[str, Any]:
    entry_signal_ret = _to_float(pending_row.get("latest_ret"))
    return {
        "mint": decision_row.get("mint") or "",
        "symbol": decision_row.get("symbol") or pending_row.get("symbol") or "n/a",
        "opened_at": now,
        "entry_signal_ret": entry_signal_ret,
        "entry_decision_bucket": decision_row.get("decision_bucket") or "promote",
        "entry_decision_grade": decision_row.get("decision_grade") or "promote_strong",
        "entry_status": decision_row.get("status") or pending_row.get("promotion_decision") or "unknown",
        "entry_shape_state": decision_row.get("shape_state") or pending_row.get("shape_state") or "unknown",
        "entry_shape_path": decision_row.get("shape_path_30_to_60") or pending_row.get("shape_path_30_to_60"),
        "entry_attention_score": _to_float(decision_row.get("attention_score")),
        "entry_useful_score": _to_float(decision_row.get("useful_score")),
        "entry_persistent_score": _to_float(decision_row.get("persistent_score")),
        "entry_survivor_fit": _to_float(decision_row.get("survivor_fit")),
        "current_signal_ret": entry_signal_ret,
        "current_trade_ret": 0.0,
        "max_signal_ret": entry_signal_ret,
        "max_trade_ret": 0.0,
        "last_status": decision_row.get("status") or "unknown",
        "last_shape_state": decision_row.get("shape_state") or "unknown",
        "closed": False,
    }


def _current_signal_ret(
    mint: str,
    *,
    pending_by_mint: dict[str, dict[str, Any]],
    dataset_by_mint: dict[str, dict[str, Any]],
) -> float | None:
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


def _close_outcome(
    pos: dict[str, Any],
    *,
    now: float,
    reason: str,
    exit_signal_ret: float | None,
    lifecycle_row: dict[str, Any] | None,
    dataset_row: dict[str, Any] | None,
) -> dict[str, Any]:
    if exit_signal_ret is None:
        exit_signal_ret = _to_float(pos.get("current_signal_ret"))
    trade_ret = _trade_return(_to_float(pos.get("entry_signal_ret")), exit_signal_ret)
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

    decision_by_mint = live["decision_by_mint"]
    pending_by_mint = live["pending_by_mint"]
    lifecycle_by_mint = live["lifecycle_by_mint"]
    dataset_by_mint = live["dataset"]

    open_by_mint = {str(pos.get("mint") or ""): pos for pos in open_positions if pos.get("mint")}

    for mint, decision_row in decision_by_mint.items():
        if mint in seen_mints or mint in open_by_mint:
            continue
        if str(decision_row.get("decision_grade") or "") not in OPEN_ENTRY_GRADES:
            continue
        pending_row = pending_by_mint.get(mint)
        if pending_row is None:
            continue
        pos = _open_position(now, decision_row, pending_row)
        open_positions.append(pos)
        open_by_mint[mint] = pos
        seen_mints.add(mint)
        events.append(
            {
                "ts": now,
                "event": "open",
                "mint": mint,
                "symbol": pos.get("symbol") or "n/a",
                "decision_grade": pos.get("entry_decision_grade"),
                "entry_signal_ret": pos.get("entry_signal_ret"),
                "shape_state": pos.get("entry_shape_state"),
                "shape_path": pos.get("entry_shape_path"),
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
        pos["current_signal_ret"] = current_signal_ret
        pos["current_trade_ret"] = _trade_return(_to_float(pos.get("entry_signal_ret")), current_signal_ret)
        if current_signal_ret is not None:
            pos["max_signal_ret"] = max(float(pos.get("max_signal_ret") or current_signal_ret), current_signal_ret)
        current_trade_ret = _to_float(pos.get("current_trade_ret"))
        if current_trade_ret is not None:
            pos["max_trade_ret"] = max(float(pos.get("max_trade_ret") or current_trade_ret), current_trade_ret)
        if decision_row is not None:
            pos["last_status"] = decision_row.get("status") or pos.get("last_status")
            pos["last_shape_state"] = decision_row.get("shape_state") or pos.get("last_shape_state")

        reason = None
        exit_signal_ret = current_signal_ret
        stage = str((lifecycle_row or {}).get("stage") or "")
        status = str((decision_row or {}).get("status") or (pending_row or {}).get("promotion_decision") or "")
        decision_bucket = str((decision_row or {}).get("decision_bucket") or "")
        shape_state = str((decision_row or {}).get("shape_state") or (pending_row or {}).get("shape_state") or "")
        hold_hours = (now - float(pos.get("opened_at") or now)) / 3600.0
        current_trade_ret = _to_float(pos.get("current_trade_ret"))
        max_trade_ret = _to_float(pos.get("max_trade_ret"))
        entry_shape = str(pos.get("entry_shape_state") or "")

        if stage in MATURED_STAGES:
            ds_ret = _to_float((dataset_row or {}).get("ret_21600s"))
            if ds_ret is not None:
                exit_signal_ret = ds_ret
            reason = f"matured:{stage}:{(lifecycle_row or {}).get('status') or 'unknown'}"
        elif decision_bucket == "cut" or status == "cut_bias" or shape_state == "losing_steam":
            reason = "decision_cut"
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
    for row in closed_positions:
        counts_by_reason[str(row.get("exit_reason") or "unknown")] = counts_by_reason.get(str(row.get("exit_reason") or "unknown"), 0) + 1
        counts_by_outcome[str(row.get("outcome_class") or "unknown")] = counts_by_outcome.get(str(row.get("outcome_class") or "unknown"), 0) + 1

    report = {
        "generated_at": time.time(),
        "run_started_at": state.get("started_at"),
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
        },
        "open_positions": open_positions,
        "closed_positions": closed_positions[-25:],
    }
    return report


def _write_md(path: Path, report: dict[str, Any]) -> None:
    s = report["summary"]
    lines = [
        "# Decision Paper Overlay",
        "",
        "Paper-trade overlay on top of the lifecycle decision engine.",
        "",
        "Rules:",
        "- Enter on `promote_strong`.",
        "- Exit on `cut_hard` / `cut_bias`, early shape deterioration, break-even / profit-lock protection, stop loss, matured lifecycle resolution, or max hold timeout.",
        "- Returns are measured relative to the signal baseline and converted to entry-relative paper PnL.",
        "",
        "## Summary",
        "",
        f"- Open positions: `{s['open_positions']}`",
        f"- Closed positions: `{s['closed_positions']}`",
        f"- Closed winrate: `{_fmt_pct(s['closed_winrate'])}`",
        f"- Closed average return: `{_fmt_pct(s['closed_avg_return'])}`",
        f"- Closed median return: `{_fmt_pct(s['closed_median_return'])}`",
        f"- Open average mark: `{_fmt_pct(s['open_avg_mark'])}`",
        f"- Open median mark: `{_fmt_pct(s['open_median_mark'])}`",
        "",
        "## Open Positions",
        "",
        "| Symbol | Grade | Entry Shape | Current Shape | Entry Ret | Mark Ret | Paper PnL | Status |",
        "|---|---|---|---|---:|---:|---:|---|",
    ]
    for row in report["open_positions"]:
        lines.append(
            f"| {row.get('symbol') or 'n/a'} | `{row.get('entry_decision_grade')}` | `{row.get('entry_shape_state')}` | `{row.get('last_shape_state')}` | "
            f"{_fmt_pct(_to_float(row.get('entry_signal_ret')))} | {_fmt_pct(_to_float(row.get('current_signal_ret')))} | {_fmt_pct(_to_float(row.get('current_trade_ret')))} | `{row.get('last_status') or 'unknown'}` |"
        )
    lines.extend(
        [
            "",
            "## Recently Closed",
            "",
            "| Symbol | Exit Reason | Outcome | Hold (h) | Entry Ret | Exit Ret | Paper PnL |",
            "|---|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in list(report["closed_positions"])[-20:]:
        lines.append(
            f"| {row.get('symbol') or 'n/a'} | `{row.get('exit_reason') or 'unknown'}` | `{row.get('outcome_class') or 'unknown'}` | "
            f"{_fmt_num(_to_float(row.get('hold_hours')), 2)} | {_fmt_pct(_to_float(row.get('entry_signal_ret')))} | "
            f"{_fmt_pct(_to_float(row.get('exit_signal_ret')))} | {_fmt_pct(_to_float(row.get('final_trade_ret')))} |"
        )
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def run_once(*, state_path: Path, journal_path: Path, out_json: Path, out_md: Path, do_refresh: bool, max_hold_hours: float) -> dict[str, Any]:
    now = time.time()
    if do_refresh:
        _run_refresh()

    state = _load_json(state_path, None) or _blank_state(now)
    live = _load_live_inputs()
    next_state, events = _step(state, live, now=now, max_hold_hours=max_hold_hours)
    _write_json(state_path, next_state)
    for event in events:
        _append_jsonl(journal_path, event)
    report = _build_report(next_state)
    _write_json(out_json, report)
    _write_md(out_md, report)
    print(
        "meme_decision_paper_overlay: "
        f"open={report['summary']['open_positions']} "
        f"closed={report['summary']['closed_positions']}"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Paper-trade overlay for lifecycle decisions.")
    parser.add_argument("--loop", action="store_true", help="Run continuously.")
    parser.add_argument("--interval-sec", type=int, default=900, help="Loop interval in seconds.")
    parser.add_argument("--max-hold-hours", type=float, default=12.0, help="Close positions after this long if still open.")
    parser.add_argument("--no-refresh", action="store_true", help="Do not refresh the main scorecard before stepping.")
    parser.add_argument("--reset", action="store_true", help="Start a fresh overlay state.")
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
