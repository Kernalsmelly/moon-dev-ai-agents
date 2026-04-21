#!/usr/bin/env python3
"""Research winner shape and early steam-loss checkpoints.

Purpose:
- classify how earliest-useful winners are holding up at 30m and 60m
- quantify which early shapes later become survivor-grade or persistent
- expose a reusable live-shape helper for pending lifecycle tracking
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import statistics
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
OUTCOMES = BASE / "data" / "signal_outcomes.jsonl"
PERSISTENCE_REPORT_PATH = BASE / "scripts" / "meme_winner_persistence_report.py"
OUT_JSON = BASE / "data" / "meme_reports" / "meme_winner_shape_report.json"
OUT_MD = BASE / "data" / "meme_reports" / "meme_winner_shape_report.md"

SURVIVOR_CLASSES = {"persistent_runner", "partial_persistence"}
CHECKPOINTS = (
    (1800, "30m"),
    (3600, "60m"),
)
STATE_ORDER = {
    "extending_cleanly": 0,
    "holding_pullback": 1,
    "stalling_but_alive": 2,
    "too_early": 3,
    "blowoff_risk": 4,
    "losing_steam": 5,
}
STATE_SCORE = {
    "extending_cleanly": 92.0,
    "holding_pullback": 74.0,
    "stalling_but_alive": 52.0,
    "too_early": 40.0,
    "blowoff_risk": 24.0,
    "losing_steam": 8.0,
}


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


def _safe_ratio(num: float | None, den: float | None) -> float | None:
    if num is None or den in (None, 0.0):
        return None
    return float(num) / float(den)


def classify_checkpoint_shape(row: dict[str, Any], checkpoint_s: int) -> dict[str, Any]:
    peak_15m = _to_float(row.get("max_ret_900s"))
    peak_checkpoint = _to_float(row.get("max_ret_1800s")) if checkpoint_s >= 1800 else peak_15m
    if checkpoint_s >= 3600:
        peak_checkpoint = _to_float(row.get("max_ret_3600s"))
        if peak_checkpoint is None:
            peak_checkpoint = _to_float(row.get("max_ret_1800s"))
    current_ret = _to_float(row.get(f"ret_{checkpoint_s}s"))

    if current_ret is None or peak_15m in (None, 0.0):
        return {
            "checkpoint_s": checkpoint_s,
            "checkpoint_label": "60m" if checkpoint_s >= 3600 else "30m",
            "shape_state": "too_early",
            "shape_score": STATE_SCORE["too_early"],
            "steam_loss": False,
            "retention_vs_15m_peak": None,
            "retention_vs_checkpoint_peak": None,
            "extension_vs_15m_peak": None,
            "reason": "not_enough_data",
        }

    retention_15m = _safe_ratio(current_ret, peak_15m)
    retention_checkpoint = _safe_ratio(current_ret, peak_checkpoint)
    extension_vs_15m = _safe_ratio(peak_checkpoint, peak_15m)

    strong_ret_floor = 0.35 if checkpoint_s >= 3600 else 0.25
    strong_retain_floor = 0.55 if checkpoint_s >= 3600 else 0.60
    strong_peak_retain_floor = 0.55 if checkpoint_s >= 3600 else 0.60
    hold_ret_floor = 0.15 if checkpoint_s >= 3600 else 0.10
    hold_retain_floor = 0.35
    alive_retain_floor = 0.15

    if (
        extension_vs_15m is not None
        and extension_vs_15m >= 1.45
        and (retention_checkpoint or 0.0) < 0.35
        and current_ret > 0.0
    ):
        state = "blowoff_risk"
        reason = "extended_far_then_gave_back_too_much"
    elif (
        current_ret >= strong_ret_floor
        and (retention_15m or 0.0) >= strong_retain_floor
        and (retention_checkpoint or 0.0) >= strong_peak_retain_floor
    ):
        state = "extending_cleanly"
        reason = "still_pressing_or_holding_near_peak"
    elif current_ret >= hold_ret_floor and (retention_15m or 0.0) >= hold_retain_floor:
        state = "holding_pullback"
        reason = "gave_back_some_but_structure_still_intact"
    elif current_ret > 0.0 and (retention_15m or 0.0) >= alive_retain_floor:
        state = "stalling_but_alive"
        reason = "still_green_but_no_longer_expanding_cleanly"
    else:
        state = "losing_steam"
        reason = "retention_broke_down_early"

    return {
        "checkpoint_s": checkpoint_s,
        "checkpoint_label": "60m" if checkpoint_s >= 3600 else "30m",
        "shape_state": state,
        "shape_score": STATE_SCORE[state],
        "steam_loss": state in {"blowoff_risk", "losing_steam"},
        "retention_vs_15m_peak": retention_15m,
        "retention_vs_checkpoint_peak": retention_checkpoint,
        "extension_vs_15m_peak": extension_vs_15m,
        "reason": reason,
    }


def classify_live_shape(row: dict[str, Any], latest_hz: int | None = None) -> dict[str, Any]:
    latest_hz = int(latest_hz or 0)
    if latest_hz < 1800:
        return {
        "checkpoint_s": latest_hz or None,
        "checkpoint_label": "forming",
        "shape_state": "too_early",
        "shape_score": STATE_SCORE["too_early"],
        "steam_loss": False,
        "retention_vs_15m_peak": None,
        "retention_vs_checkpoint_peak": None,
        "extension_vs_15m_peak": None,
        "reason": "await_30m_shape",
    }

    checkpoint_s = 3600 if latest_hz >= 3600 else 1800
    shape = classify_checkpoint_shape(row, checkpoint_s)
    peak_15m = _to_float(row.get("max_ret_900s"))
    latest_ret = _to_float(row.get(f"ret_{latest_hz}s"))
    latest_retention = _safe_ratio(latest_ret, peak_15m)
    if latest_ret is not None and peak_15m not in (None, 0.0) and latest_hz > checkpoint_s:
        if latest_ret <= 0.0 or (latest_retention or 0.0) < 0.10:
            shape.update(
                {
                    "shape_state": "losing_steam",
                    "shape_score": STATE_SCORE["losing_steam"],
                    "steam_loss": True,
                    "retention_vs_15m_peak": latest_retention,
                    "reason": "later_checkpoint_broke_down",
                }
            )
        elif shape["shape_state"] == "extending_cleanly" and (latest_retention or 0.0) < 0.35:
            shape.update(
                {
                    "shape_state": "stalling_but_alive",
                    "shape_score": STATE_SCORE["stalling_but_alive"],
                    "steam_loss": False,
                    "retention_vs_15m_peak": latest_retention,
                    "reason": "later_checkpoint_lost_extension_but_is_still_green",
                }
            )
    return shape


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

        shape_30m = classify_checkpoint_shape(useful, 1800)
        shape_60m = classify_checkpoint_shape(useful, 3600)

        out.append(
            {
                "mint": mint,
                "symbol": useful.get("symbol") or "n/a",
                "signal_source": useful.get("signal_source") or "unknown",
                "mcap0": _to_float(useful.get("mcap0")),
                "pair_age_min0": _to_float(useful.get("pair_age_min0")),
                "mom5m0": _to_float(useful.get("mom5m0")),
                "hits0": _to_float(useful.get("hits0")),
                "net_sol_in0": _to_float(useful.get("net_sol_in0")),
                "max_ret_15m": _to_float(useful.get("max_ret_900s")),
                "ret_30m": _to_float(useful.get("ret_1800s")),
                "ret_60m": _to_float(useful.get("ret_3600s")),
                "ret_6h": _to_float(useful.get("ret_21600s")),
                "retention_6h": retention_6h,
                "persistence_class": persistence_class,
                "survivor_grade": persistence_class in SURVIVOR_CLASSES,
                "shape_30m": shape_30m["shape_state"],
                "shape_30m_score": shape_30m["shape_score"],
                "shape_30m_reason": shape_30m["reason"],
                "shape_30m_steam_loss": shape_30m["steam_loss"],
                "shape_30m_retention_vs_15m_peak": shape_30m["retention_vs_15m_peak"],
                "shape_30m_extension_vs_15m_peak": shape_30m["extension_vs_15m_peak"],
                "shape_60m": shape_60m["shape_state"],
                "shape_60m_score": shape_60m["shape_score"],
                "shape_60m_reason": shape_60m["reason"],
                "shape_60m_steam_loss": shape_60m["steam_loss"],
                "shape_60m_retention_vs_15m_peak": shape_60m["retention_vs_15m_peak"],
                "shape_60m_extension_vs_15m_peak": shape_60m["extension_vs_15m_peak"],
                "shape_path_30_to_60": f"{shape_30m['shape_state']} -> {shape_60m['shape_state']}",
            }
        )
    return out


def _checkpoint_summary(rows: list[dict[str, Any]], *, checkpoint_label: str, field: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        state = str(row.get(field) or "unknown")
        grouped[state].append(row)

    out: list[dict[str, Any]] = []
    for state, group in grouped.items():
        n = len(group)
        useful_n = len(group)
        survivor_n = sum(1 for row in group if row.get("survivor_grade"))
        persistent_n = sum(1 for row in group if str(row.get("persistence_class") or "") == "persistent_runner")
        out.append(
            {
                "checkpoint": checkpoint_label,
                "shape_state": state,
                "n": n,
                "survivor_precision": (survivor_n / useful_n) if useful_n else None,
                "persistent_precision": (persistent_n / useful_n) if useful_n else None,
                "median_retention_vs_15m_peak": _median([row.get(f"{field}_retention_vs_15m_peak") for row in group]),
                "median_extension_vs_15m_peak": _median([row.get(f"{field}_extension_vs_15m_peak") for row in group]),
                "median_ret_6h": _median([row.get("ret_6h") for row in group]),
                "examples": [
                    {
                        "symbol": row.get("symbol") or "n/a",
                        "mint": row.get("mint") or "",
                        "persistence_class": row.get("persistence_class") or "unknown",
                    }
                    for row in group[:5]
                ],
            }
        )
    out.sort(
        key=lambda row: (
            STATE_ORDER.get(str(row.get("shape_state") or ""), 99),
            -(int(row.get("n") or 0)),
        )
    )
    return out


def _path_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = str(row.get("shape_path_30_to_60") or "unknown")
        grouped[key].append(row)

    out: list[dict[str, Any]] = []
    for path_key, group in grouped.items():
        n = len(group)
        survivor_n = sum(1 for row in group if row.get("survivor_grade"))
        persistent_n = sum(1 for row in group if str(row.get("persistence_class") or "") == "persistent_runner")
        out.append(
            {
                "shape_path": path_key,
                "n": n,
                "survivor_precision": (survivor_n / n) if n else None,
                "persistent_precision": (persistent_n / n) if n else None,
            }
        )
    out.sort(key=lambda row: (-int(row.get("n") or 0), str(row.get("shape_path") or "")))
    return out


def build_report(
    rows_by_mint: dict[str, list[dict[str, Any]]],
    persistence_module: Any,
    *,
    winner_ret: float,
    persistent_ret: float,
    persistent_retain: float,
    round_trip_retain: float,
) -> dict[str, Any]:
    rows = _build_rows(
        rows_by_mint,
        persistence_module,
        winner_ret=winner_ret,
        persistent_ret=persistent_ret,
        persistent_retain=persistent_retain,
        round_trip_retain=round_trip_retain,
    )
    baseline_survivor = (
        sum(1 for row in rows if row.get("survivor_grade")) / len(rows)
        if rows
        else None
    )
    baseline_persistent = (
        sum(1 for row in rows if str(row.get("persistence_class") or "") == "persistent_runner") / len(rows)
        if rows
        else None
    )
    by_30m = _checkpoint_summary(rows, checkpoint_label="30m", field="shape_30m")
    by_60m = _checkpoint_summary(rows, checkpoint_label="60m", field="shape_60m")
    by_path = _path_summary(rows)

    def best(rows_in: list[dict[str, Any]], *, metric: str, exclude: set[str] | None = None) -> dict[str, Any] | None:
        filtered = [
            row
            for row in rows_in
            if int(row.get("n") or 0) >= 6 and str(row.get("shape_state") or "") not in (exclude or set())
        ]
        if not filtered:
            return None
        return max(filtered, key=lambda row: float(row.get(metric) or -1.0))

    summary = {
        "matured_useful_winners": len(rows),
        "baseline_survivor_precision": baseline_survivor,
        "baseline_persistent_precision": baseline_persistent,
        "best_30m_shape": best(by_30m, metric="survivor_precision", exclude={"too_early"}),
        "best_60m_shape": best(by_60m, metric="survivor_precision", exclude={"too_early"}),
        "worst_30m_shape": best(by_30m, metric="persistent_precision", exclude={"extending_cleanly", "holding_pullback", "stalling_but_alive", "too_early"}),
        "worst_60m_shape": best(by_60m, metric="persistent_precision", exclude={"extending_cleanly", "holding_pullback", "stalling_but_alive", "too_early"}),
        "best_shape_path": best(by_path, metric="survivor_precision"),
        "worst_shape_path": min(
            [row for row in by_path if int(row.get("n") or 0) >= 4],
            key=lambda row: float(row["survivor_precision"]) if row.get("survivor_precision") is not None else 1.0,
            default=None,
        ),
    }

    return {
        "generated_at": time.time(),
        "summary": summary,
        "by_checkpoint": {
            "30m": by_30m,
            "60m": by_60m,
        },
        "by_shape_path": by_path,
        "rows": rows,
    }


def write_md(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    lines = [
        "# Winner Shape Report",
        "",
        "Research on how earliest-useful winners are shaping at 30m and 60m, with a specific focus on early steam loss.",
        "",
        "## Summary",
        "",
        f"- Matured useful winners: `{summary['matured_useful_winners']}`",
        f"- Baseline survivor precision: `{_fmt_pct(summary['baseline_survivor_precision'])}`",
        f"- Baseline persistent precision: `{_fmt_pct(summary['baseline_persistent_precision'])}`",
    ]
    for key in ("best_30m_shape", "best_60m_shape"):
        row = summary.get(key)
        if row:
            lines.append(
                f"- {key.replace('_', ' ').title()}: `{row['shape_state']}` -> survivor `{_fmt_pct(row['survivor_precision'])}` on `{int(row['n'])}` rows"
            )
    lines.extend(
        [
            "",
            "## 30m Shape States",
            "",
            "| Shape | N | Survivor Precision | Persistent Precision | Median Ret/15m Peak | Median Extension | Median 6h Ret |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in report["by_checkpoint"]["30m"]:
        lines.append(
            f"| `{row['shape_state']}` | {row['n']} | {_fmt_pct(row['survivor_precision'])} | {_fmt_pct(row['persistent_precision'])} | "
            f"{_fmt_pct(row['median_retention_vs_15m_peak'])} | {_fmt_num(row['median_extension_vs_15m_peak'], 2)}x | {_fmt_pct(row['median_ret_6h'])} |"
        )
    lines.extend(
        [
            "",
            "## 30m -> 60m Shape Paths",
            "",
            "| Path | N | Survivor Precision | Persistent Precision |",
            "|---|---:|---:|---:|",
        ]
    )
    for row in report["by_shape_path"][:10]:
        lines.append(
            f"| `{row['shape_path']}` | {row['n']} | {_fmt_pct(row['survivor_precision'])} | {_fmt_pct(row['persistent_precision'])} |"
        )
    lines.extend(
        [
            "",
            "## 60m Shape States",
            "",
            "| Shape | N | Survivor Precision | Persistent Precision | Median Ret/15m Peak | Median Extension | Median 6h Ret |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in report["by_checkpoint"]["60m"]:
        lines.append(
            f"| `{row['shape_state']}` | {row['n']} | {_fmt_pct(row['survivor_precision'])} | {_fmt_pct(row['persistent_precision'])} | "
            f"{_fmt_pct(row['median_retention_vs_15m_peak'])} | {_fmt_num(row['median_extension_vs_15m_peak'], 2)}x | {_fmt_pct(row['median_ret_6h'])} |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Research winner shape and early steam loss.")
    parser.add_argument("--since-hours", type=float, default=168.0)
    parser.add_argument("--winner-ret", type=float, default=0.50)
    parser.add_argument("--persistent-ret", type=float, default=1.00)
    parser.add_argument("--persistent-retain", type=float, default=0.50)
    parser.add_argument("--round-trip-retain", type=float, default=0.15)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=OUT_MD)
    args = parser.parse_args()

    persistence_module = _load_module("meme_winner_persistence_report_module_for_shape", PERSISTENCE_REPORT_PATH)
    since_ts = time.time() - (float(args.since_hours) * 3600.0)
    rows_by_mint = persistence_module.load_rows(OUTCOMES, since_ts)
    report = build_report(
        rows_by_mint,
        persistence_module,
        winner_ret=float(args.winner_ret),
        persistent_ret=float(args.persistent_ret),
        persistent_retain=float(args.persistent_retain),
        round_trip_retain=float(args.round_trip_retain),
    )
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_md(args.out_md, report)
    print(
        "meme_winner_shape_report: "
        f"winners={report['summary']['matured_useful_winners']} "
        f"best30={((report['summary'].get('best_30m_shape') or {}).get('shape_state') or 'n/a')}"
    )


if __name__ == "__main__":
    main()
