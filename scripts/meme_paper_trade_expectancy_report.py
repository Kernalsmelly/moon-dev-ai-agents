#!/usr/bin/env python3
"""Summarize paper-trade expectancy across the current overlay layers."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
REPORTS = BASE / "data" / "meme_reports"
V1_JSON = REPORTS / "meme_decision_paper_overlay_report.json"
V2_JSON = REPORTS / "meme_decision_paper_overlay_v2_report.json"
ACTIVE_JSON = REPORTS / "meme_decision_paper_overlay_active_report.json"
OUT_JSON = REPORTS / "meme_paper_trade_expectancy_report.json"
OUT_MD = REPORTS / "meme_paper_trade_expectancy_report.md"


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


def _fmt_num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def _stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    returns = [_to_float(row.get("final_trade_ret")) for row in rows]
    clean = [r for r in returns if r is not None]
    wins = [r for r in clean if r > 0.0]
    losses = [r for r in clean if r <= 0.0]
    n = len(clean)
    winrate = (len(wins) / n) if n else None
    avg_return = (sum(clean) / n) if n else None
    avg_win = (sum(wins) / len(wins)) if wins else None
    avg_loss = (sum(losses) / len(losses)) if losses else None
    payoff_ratio = None
    if avg_win is not None and avg_loss not in (None, 0.0):
        payoff_ratio = avg_win / abs(avg_loss)
    expectancy = None
    if winrate is not None and avg_win is not None and avg_loss is not None:
        expectancy = (winrate * avg_win) + ((1.0 - winrate) * avg_loss)
    return {
        "n": n,
        "winrate": winrate,
        "avg_return": avg_return,
        "median_return": _median(clean),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": payoff_ratio,
        "expectancy": expectancy,
    }


def _overlay_closed_rows(report: dict[str, Any], overlay_name: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    managed_overlay = overlay_name in {"v2", "active"}
    for raw in report.get("closed_positions") or []:
        row = dict(raw)
        row["overlay"] = overlay_name
        row["entry_readiness"] = row.get("entry_execution_readiness") or "unknown"
        row["decision_grade"] = row.get("entry_decision_grade") or row.get("last_decision_grade") or "unknown"
        row["starter_grade"] = row.get("entry_starter_grade") or ("legacy" if managed_overlay else "n/a")
        row["size_tier"] = row.get("entry_size_tier") or ("legacy" if managed_overlay else "n/a")
        default_cohort = f"legacy_{overlay_name}" if managed_overlay else overlay_name
        row["cohort"] = row.get("cohort") or default_cohort
        out.append(row)
    return out


def _overlay_open_rows(report: dict[str, Any], overlay_name: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    managed_overlay = overlay_name in {"v2", "active"}
    for raw in report.get("open_positions") or []:
        row = dict(raw)
        row["overlay"] = overlay_name
        row["entry_readiness"] = row.get("entry_execution_readiness") or "unknown"
        row["decision_grade"] = row.get("entry_decision_grade") or row.get("last_decision_grade") or "unknown"
        row["starter_grade"] = row.get("entry_starter_grade") or ("legacy" if managed_overlay else "n/a")
        row["size_tier"] = row.get("entry_size_tier") or ("legacy" if managed_overlay else "n/a")
        default_cohort = f"legacy_{overlay_name}" if managed_overlay else overlay_name
        row["cohort"] = row.get("cohort") or default_cohort
        out.append(row)
    return out


def _group_stats(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(field) or "unknown")].append(row)
    out: list[dict[str, Any]] = []
    for key, group in grouped.items():
        stats = _stats(group)
        out.append(
            {
                field: key,
                **stats,
            }
        )
    out.sort(key=lambda row: (-int(row.get("n") or 0), str(row.get(field) or "")))
    return out


def build_report(overlays: dict[str, dict[str, Any]]) -> dict[str, Any]:
    closed: list[dict[str, Any]] = []
    open_rows: list[dict[str, Any]] = []
    for overlay_name, report in overlays.items():
        closed.extend(_overlay_closed_rows(report, overlay_name))
        open_rows.extend(_overlay_open_rows(report, overlay_name))

    managed_closed = [row for row in closed if row.get("overlay") in {"v2", "active"}]

    report = {
        "summary": {
            "closed_total": len(closed),
            "open_total": len(open_rows),
            "combined": _stats(closed),
        },
        "by_overlay": _group_stats(closed, "overlay"),
        "by_exit_reason": _group_stats(closed, "exit_reason"),
        "by_readiness": _group_stats(closed, "entry_readiness"),
        "by_decision_grade": _group_stats(closed, "decision_grade"),
        "by_starter_grade": _group_stats(managed_closed, "starter_grade"),
        "by_size_tier": _group_stats(managed_closed, "size_tier"),
        "by_cohort": _group_stats(managed_closed, "cohort"),
        "open_positions": open_rows,
        "recent_closed": closed[-12:],
    }
    return report


def render_md(report: dict[str, Any]) -> str:
    s = report["summary"]
    combined = s["combined"]
    lines = [
        "# Paper Trade Expectancy",
        "",
        "A plain-English scoreboard for whether the current paper-trade engine is moving toward something tradable. This focuses on expectancy, payoff, and whether the new v2 starter-gate cohort has enough data yet to judge.",
        "",
        "## Combined Summary",
        "",
        f"- Closed trades: `{s['closed_total']}`",
        f"- Open trades: `{s['open_total']}`",
        f"- Combined winrate: `{_fmt_pct(combined['winrate'])}`",
        f"- Combined average return: `{_fmt_pct(combined['avg_return'])}`",
        f"- Combined median return: `{_fmt_pct(combined['median_return'])}`",
        f"- Average win: `{_fmt_pct(combined['avg_win'])}`",
        f"- Average loss: `{_fmt_pct(combined['avg_loss'])}`",
        f"- Payoff ratio: `{_fmt_num(combined['payoff_ratio'])}`",
        f"- Expectancy per trade: `{_fmt_pct(combined['expectancy'])}`",
        "",
    ]

    def add_table(title: str, field: str, rows: list[dict[str, Any]]) -> None:
        lines.extend(
            [
                f"## {title}",
                "",
                f"| {field} | N | Winrate | Avg Return | Avg Win | Avg Loss | Payoff | Expectancy |",
                "|---|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        if rows:
            for row in rows:
                lines.append(
                    f"| `{row[field]}` | {row['n']} | {_fmt_pct(row['winrate'])} | {_fmt_pct(row['avg_return'])} | {_fmt_pct(row['avg_win'])} | {_fmt_pct(row['avg_loss'])} | {_fmt_num(row['payoff_ratio'])} | {_fmt_pct(row['expectancy'])} |"
                )
        else:
            lines.append(f"| `n/a` | 0 | n/a | n/a | n/a | n/a | n/a | n/a |")
        lines.append("")

    add_table("By Overlay", "overlay", report["by_overlay"])
    add_table("By Exit Reason", "exit_reason", report["by_exit_reason"])
    add_table("By Entry Readiness", "entry_readiness", report["by_readiness"])
    add_table("By Decision Grade", "decision_grade", report["by_decision_grade"])
    add_table("Managed Overlays By Starter Grade", "starter_grade", report["by_starter_grade"])
    add_table("Managed Overlays By Size Tier", "size_tier", report["by_size_tier"])
    add_table("Managed Overlays By Cohort", "cohort", report["by_cohort"])

    lines.extend(
        [
            "## Open Positions",
            "",
            "| Overlay | Symbol | Grade | Starter | Size Tier | Readiness | Mark Ret | Status | Cohort |",
            "|---|---|---|---|---|---|---:|---|---|",
        ]
    )
    if report["open_positions"]:
        for row in report["open_positions"][:12]:
            lines.append(
                f"| `{row.get('overlay')}` | {row.get('symbol') or 'n/a'} | `{row.get('decision_grade') or 'unknown'}` | `{row.get('starter_grade') or 'n/a'}` | `{row.get('size_tier') or 'n/a'}` | `{row.get('entry_readiness') or 'unknown'}` | {_fmt_pct(_to_float(row.get('current_trade_ret')))} | `{row.get('last_status') or 'unknown'}` | `{row.get('cohort') or 'unknown'}` |"
            )
    else:
        lines.append("| `n/a` | n/a | `n/a` | `n/a` | `n/a` | n/a | `n/a` |")

    lines.extend(
        [
            "",
            "## Read",
            "",
            "- If winrate is fine but expectancy is negative, losers are still too large.",
            "- `legacy_v2` is the cautious benchmark sample; `active_clean_*` rows belong to the broader shadow-trader overlay.",
            "- `v2_clean_*` rows are still the stricter benchmark, while `active_*` rows help us learn how a more realistic paper bot would behave.",
            "- A blank or tiny clean-cohort section is not failure; it just means the new rules still need time to mature.",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v1-json", type=Path, default=V1_JSON)
    parser.add_argument("--v2-json", type=Path, default=V2_JSON)
    parser.add_argument("--active-json", type=Path, default=ACTIVE_JSON)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=OUT_MD)
    args = parser.parse_args()

    overlays = {
        "v1": _load_json(args.v1_json),
        "v2": _load_json(args.v2_json),
    }
    if args.active_json.exists():
        overlays["active"] = _load_json(args.active_json)

    report = build_report(overlays)
    args.out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    args.out_md.write_text(render_md(report), encoding="utf-8")
    print(
        "paper-expectancy "
        f"closed={report['summary']['closed_total']} "
        f"open={report['summary']['open_total']} "
        f"expectancy={_fmt_pct(report['summary']['combined']['expectancy'])}"
    )


if __name__ == "__main__":
    main()
