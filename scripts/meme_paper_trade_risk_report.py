#!/usr/bin/env python3
"""Explain where paper-trade losses are still coming from."""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
REPORTS = BASE / "data" / "meme_reports"
V1_JSON = REPORTS / "meme_decision_paper_overlay_report.json"
V2_JSON = REPORTS / "meme_decision_paper_overlay_v2_report.json"
OUT_JSON = REPORTS / "meme_paper_trade_risk_report.json"
OUT_MD = REPORTS / "meme_paper_trade_risk_report.md"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _to_float(value: Any) -> float | None:
    try:
        if value in ("", None):
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


def _bucket_entry_signal_ret(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 0.50:
        return "<50%"
    if value < 1.00:
        return "50-100%"
    if value < 1.50:
        return "100-150%"
    if value < 2.50:
        return "150-250%"
    return "250%+"


def _collect_rows(report: dict[str, Any], overlay: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in report.get("closed_positions") or []:
        row = dict(raw)
        row["overlay"] = overlay
        lots = list(row.get("lots") or [])
        first_lot = lots[0] if lots else {}
        row["entry_signal_ret_resolved"] = _to_float(row.get("entry_signal_ret"))
        if row["entry_signal_ret_resolved"] is None:
            row["entry_signal_ret_resolved"] = _to_float(first_lot.get("entry_signal_ret"))
        row["entry_bucket"] = _bucket_entry_signal_ret(row["entry_signal_ret_resolved"])
        row["entry_readiness"] = row.get("entry_execution_readiness") or "unknown"
        row["final_trade_ret"] = _to_float(row.get("final_trade_ret"))
        row["entry_shape"] = row.get("entry_shape_state") or "unknown"
        row["last_shape"] = row.get("last_shape_state") or "unknown"
        row["size_tier"] = row.get("entry_size_tier") or ("legacy" if overlay == "v2" else "n/a")
        row["shape_transition"] = f"{row['entry_shape']} -> {row['last_shape']}"
        row["readiness_transition"] = f"{row.get('entry_execution_readiness') or 'unknown'} -> {row.get('last_execution_readiness') or row.get('entry_execution_readiness') or 'unknown'}"
        row["cohort"] = row.get("cohort") or ("legacy_v2" if overlay == "v2" else "v1")
        rows.append(row)
    return rows


def _group_return_stats(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(field) or "unknown")].append(row)
    out: list[dict[str, Any]] = []
    for key, group in grouped.items():
        rets = [row["final_trade_ret"] for row in group if row.get("final_trade_ret") is not None]
        if not rets:
            continue
        out.append(
            {
                field: key,
                "n": len(rets),
                "avg_return": sum(rets) / len(rets),
                "winrate": sum(1 for ret in rets if ret > 0.0) / len(rets),
                "big_loss_rate": sum(1 for ret in rets if ret <= -0.40) / len(rets),
            }
        )
    out.sort(key=lambda row: (-int(row["n"]), str(row[field])))
    return out


def build_report(v1: dict[str, Any], v2: dict[str, Any]) -> dict[str, Any]:
    rows = _collect_rows(v1, "v1") + _collect_rows(v2, "v2")
    big_losers = [row for row in rows if row.get("final_trade_ret") is not None and row["final_trade_ret"] <= -0.40]
    big_losers.sort(key=lambda row: float(row["final_trade_ret"]))
    return {
        "summary": {
            "closed_trades": len(rows),
            "big_losers": len(big_losers),
            "big_loser_rate": (len(big_losers) / len(rows)) if rows else None,
        },
        "by_exit_reason": _group_return_stats(rows, "exit_reason"),
        "by_entry_bucket": _group_return_stats(rows, "entry_bucket"),
        "by_size_tier": _group_return_stats(rows, "size_tier"),
        "by_readiness_transition": _group_return_stats(rows, "readiness_transition"),
        "by_shape_transition": _group_return_stats(rows, "shape_transition"),
        "by_cohort": _group_return_stats(rows, "cohort"),
        "big_loser_examples": [
            {
                "overlay": row.get("overlay"),
                "symbol": row.get("symbol") or "n/a",
                "entry_bucket": row.get("entry_bucket") or "unknown",
                "entry_readiness": row.get("entry_readiness") or "unknown",
                "readiness_transition": row.get("readiness_transition") or "unknown",
                "shape_transition": row.get("shape_transition") or "unknown",
                "exit_reason": row.get("exit_reason") or "unknown",
                "final_trade_ret": row.get("final_trade_ret"),
                "outcome_class": row.get("outcome_class") or "unknown",
            }
            for row in big_losers[:12]
        ],
    }


def render_md(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = [
        "# Paper Trade Risk Report",
        "",
        "This report is only about one thing: why our paper trades can still lose too much even when the decision engine is finding real survivors.",
        "",
        "## Summary",
        "",
        f"- Closed trades studied: `{s['closed_trades']}`",
        f"- Big losers (`<= -40%`): `{s['big_losers']}`",
        f"- Big loser rate: `{_fmt_pct(s['big_loser_rate'])}`",
        "",
    ]

    def add_table(title: str, field: str, rows: list[dict[str, Any]]) -> None:
        lines.extend(
            [
                f"## {title}",
                "",
                f"| {field} | N | Winrate | Avg Return | Big Loser Rate |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        if rows:
            for row in rows:
                lines.append(
                    f"| `{row[field]}` | {row['n']} | {_fmt_pct(row['winrate'])} | {_fmt_pct(row['avg_return'])} | {_fmt_pct(row['big_loss_rate'])} |"
                )
        else:
            lines.append(f"| `n/a` | 0 | n/a | n/a | n/a |")
        lines.append("")

    add_table("By Exit Reason", "exit_reason", report["by_exit_reason"])
    add_table("By Entry Signal Bucket", "entry_bucket", report["by_entry_bucket"])
    add_table("By Size Tier", "size_tier", report["by_size_tier"])
    add_table("By Readiness Transition", "readiness_transition", report["by_readiness_transition"])
    add_table("By Shape Transition", "shape_transition", report["by_shape_transition"])
    add_table("By Cohort", "cohort", report["by_cohort"])

    lines.extend(
        [
            "## Big Loser Examples",
            "",
            "| Overlay | Symbol | Entry Bucket | Readiness | Shape Path | Exit | PnL | Outcome |",
            "|---|---|---|---|---|---|---:|---|",
        ]
    )
    if report["big_loser_examples"]:
        for row in report["big_loser_examples"]:
            lines.append(
                f"| `{row['overlay']}` | {row['symbol']} | `{row['entry_bucket']}` | `{row['readiness_transition']}` | `{row['shape_transition']}` | `{row['exit_reason']}` | {_fmt_pct(_to_float(row['final_trade_ret']))} | `{row['outcome_class']}` |"
            )
    else:
        lines.append("| `n/a` | n/a | `n/a` | `n/a` | `n/a` | `n/a` | n/a | `n/a` |")

    lines.extend(
        [
            "",
            "## Read",
            "",
            "- If `decision_cut` is still massively negative, we are cutting too late, not too early.",
            "- If `route_ready -> thin` degrades badly, external market plumbing should become part of the exit logic.",
            "- If `extending_cleanly -> extending_cleanly` can still end in a big loser, we need a time/mark stop that does not wait for an explicit shape downgrade.",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v1-json", type=Path, default=V1_JSON)
    parser.add_argument("--v2-json", type=Path, default=V2_JSON)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=OUT_MD)
    args = parser.parse_args()

    report = build_report(_load_json(args.v1_json), _load_json(args.v2_json))
    args.out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    args.out_md.write_text(render_md(report), encoding="utf-8")
    print(
        "paper-risk "
        f"closed={report['summary']['closed_trades']} "
        f"big_losers={report['summary']['big_losers']}"
    )


if __name__ == "__main__":
    main()
