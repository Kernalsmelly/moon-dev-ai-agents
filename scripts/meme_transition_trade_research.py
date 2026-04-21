#!/usr/bin/env python3
"""Research entry-after-signal transition trades from historical outcome data.

This asks a more trading-relevant question than raw winner counts:
- if we entered after the first move proved itself at 30m or 60m,
- which shape states or shape paths still produced positive follow-through by 6h?
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
SHAPE_REPORT_PATH = BASE / "scripts" / "meme_winner_shape_report.py"
OUT_JSON = BASE / "data" / "meme_reports" / "meme_transition_trade_research.json"
OUT_MD = BASE / "data" / "meme_reports" / "meme_transition_trade_research.md"


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load module from {path}")
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


def _trade_return(entry_ret: float | None, exit_ret: float | None) -> float | None:
    if entry_ret is None or exit_ret is None:
        return None
    entry_mult = 1.0 + float(entry_ret)
    exit_mult = 1.0 + float(exit_ret)
    if entry_mult <= 0.0 or exit_mult <= 0.0:
        return None
    return (exit_mult / entry_mult) - 1.0


def _build_rows(
    rows_by_mint: dict[str, list[dict[str, Any]]],
    persistence_module: Any,
    shape_module: Any,
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

        ret_30m = _to_float(useful.get("ret_1800s"))
        ret_60m = _to_float(useful.get("ret_3600s"))
        ret_6h = _to_float(useful.get("ret_21600s"))
        if ret_6h is None:
            continue

        shape_30m = shape_module.classify_checkpoint_shape(useful, 1800)
        shape_60m = shape_module.classify_checkpoint_shape(useful, 3600)

        out.append(
            {
                "mint": mint,
                "symbol": useful.get("symbol") or "n/a",
                "signal_source": useful.get("signal_source") or "unknown",
                "mover_pattern0": useful.get("mover_pattern0") or "unknown",
                "persistence_class": persistence_class,
                "ret_30m": ret_30m,
                "ret_60m": ret_60m,
                "ret_6h": ret_6h,
                "shape_30m": shape_30m["shape_state"],
                "shape_60m": shape_60m["shape_state"],
                "shape_path_30_to_60": f"{shape_30m['shape_state']} -> {shape_60m['shape_state']}",
                "trade_30m_to_6h": _trade_return(ret_30m, ret_6h),
                "trade_60m_to_6h": _trade_return(ret_60m, ret_6h),
                "retention_6h": retention_6h,
            }
        )
    return out


def _summarize(rows: list[dict[str, Any]], key_field: str, trade_field: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = str(row.get(key_field) or "unknown")
        trade_ret = _to_float(row.get(trade_field))
        if trade_ret is None:
            continue
        grouped[key].append({**row, trade_field: trade_ret})

    out: list[dict[str, Any]] = []
    for key, group in grouped.items():
        trade_rets = [_to_float(row.get(trade_field)) for row in group]
        winners = [ret for ret in trade_rets if ret is not None and ret > 0.0]
        out.append(
            {
                "bucket": key,
                "n": len(group),
                "winrate": (len(winners) / len(group)) if group else None,
                "avg_return": (sum(ret for ret in trade_rets if ret is not None) / len(group)) if group else None,
                "median_return": _median(trade_rets),
                "gt_25pct": (
                    sum(1 for ret in trade_rets if ret is not None and ret >= 0.25) / len(group)
                    if group
                    else None
                ),
                "persistent_precision": (
                    sum(1 for row in group if str(row.get("persistence_class") or "") == "persistent_runner") / len(group)
                    if group
                    else None
                ),
            }
        )
    out.sort(key=lambda row: (-int(row.get("n") or 0), str(row.get("bucket") or "")))
    return out


def _best(rows: list[dict[str, Any]], *, min_n: int = 4) -> dict[str, Any] | None:
    filtered = [row for row in rows if int(row.get("n") or 0) >= min_n]
    if not filtered:
        return None
    return max(
        filtered,
        key=lambda row: (
            float(row.get("avg_return") or -999.0),
            float(row.get("winrate") or -999.0),
        ),
    )


def _worst(rows: list[dict[str, Any]], *, min_n: int = 4) -> dict[str, Any] | None:
    filtered = [row for row in rows if int(row.get("n") or 0) >= min_n]
    if not filtered:
        return None
    return min(
        filtered,
        key=lambda row: (
            float(row.get("avg_return") or 999.0),
            float(row.get("winrate") or 999.0),
        ),
    )


def build_report(
    rows_by_mint: dict[str, list[dict[str, Any]]],
    persistence_module: Any,
    shape_module: Any,
    *,
    winner_ret: float,
    persistent_ret: float,
    persistent_retain: float,
    round_trip_retain: float,
) -> dict[str, Any]:
    rows = _build_rows(
        rows_by_mint,
        persistence_module,
        shape_module,
        winner_ret=winner_ret,
        persistent_ret=persistent_ret,
        persistent_retain=persistent_retain,
        round_trip_retain=round_trip_retain,
    )
    by_30m_shape = _summarize(rows, "shape_30m", "trade_30m_to_6h")
    by_60m_shape = _summarize(rows, "shape_60m", "trade_60m_to_6h")
    by_path = _summarize(rows, "shape_path_30_to_60", "trade_60m_to_6h")

    return {
        "generated_at": time.time(),
        "summary": {
            "matured_useful_winners": len(rows),
            "best_30m_entry": _best(by_30m_shape),
            "best_60m_entry": _best(by_60m_shape),
            "best_60m_path_entry": _best(by_path),
            "worst_60m_path_entry": _worst(by_path),
        },
        "by_30m_shape": by_30m_shape,
        "by_60m_shape": by_60m_shape,
        "by_60m_path": by_path,
        "rows": rows,
    }


def write_md(path: Path, report: dict[str, Any]) -> None:
    s = report["summary"]
    lines = [
        "# Transition Trade Research",
        "",
        "Historical research on what would have happened if we entered *after* the first move proved itself at 30m or 60m.",
        "",
        "## Summary",
        "",
        f"- Matured useful winners studied: `{s['matured_useful_winners']}`",
    ]
    for label in ("best_30m_entry", "best_60m_entry", "best_60m_path_entry", "worst_60m_path_entry"):
        row = s.get(label) or {}
        if row:
            lines.append(
                f"- {label.replace('_', ' ').title()}: `{row.get('bucket')}` -> avg `{_fmt_pct(row.get('avg_return'))}`, "
                f"winrate `{_fmt_pct(row.get('winrate'))}`, persistent `{_fmt_pct(row.get('persistent_precision'))}` on `{int(row.get('n') or 0)}` rows"
            )

    def add_table(title: str, rows: list[dict[str, Any]]) -> None:
        lines.extend(
            [
                "",
                f"## {title}",
                "",
                "| Bucket | N | Winrate | Avg Return | Median Return | >25% | Persistent |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in rows:
            lines.append(
                f"| `{row['bucket']}` | {row['n']} | {_fmt_pct(row['winrate'])} | {_fmt_pct(row['avg_return'])} | "
                f"{_fmt_pct(row['median_return'])} | {_fmt_pct(row['gt_25pct'])} | {_fmt_pct(row['persistent_precision'])} |"
            )

    add_table("30m Entry Buckets", report["by_30m_shape"])
    add_table("60m Entry Buckets", report["by_60m_shape"])
    add_table("60m Path Entry Buckets", report["by_60m_path"])
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Research historical transition-trade entries.")
    parser.add_argument("--since-hours", type=float, default=336.0)
    parser.add_argument("--winner-ret", type=float, default=0.50)
    parser.add_argument("--persistent-ret", type=float, default=1.00)
    parser.add_argument("--persistent-retain", type=float, default=0.50)
    parser.add_argument("--round-trip-retain", type=float, default=0.15)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=OUT_MD)
    args = parser.parse_args()

    persistence_module = _load_module("meme_winner_persistence_for_transition_trade", PERSISTENCE_REPORT_PATH)
    shape_module = _load_module("meme_winner_shape_for_transition_trade", SHAPE_REPORT_PATH)
    since_ts = time.time() - (float(args.since_hours) * 3600.0)
    rows_by_mint = persistence_module.load_rows(OUTCOMES, since_ts)
    report = build_report(
        rows_by_mint,
        persistence_module,
        shape_module,
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
        "meme_transition_trade_research: "
        f"rows={report['summary']['matured_useful_winners']} "
        f"best60path={((report['summary'].get('best_60m_path_entry') or {}).get('bucket') or 'n/a')}"
    )


if __name__ == "__main__":
    main()
