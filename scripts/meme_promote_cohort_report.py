#!/usr/bin/env python3
"""Summarize how promoted names have behaved and split them into strong vs probe cohorts."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
REPORTS = BASE / "data" / "meme_reports"
DECISION_TRACKER_JSON = REPORTS / "meme_decision_tracker.json"
OUT_JSON = REPORTS / "meme_promote_cohort_report.json"
OUT_MD = REPORTS / "meme_promote_cohort_report.md"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100.0:.1f}%"


def _fmt_num(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def _precision(rows: list[dict[str, Any]], field: str) -> float:
    if not rows:
        return 0.0
    return sum(int(row.get(field) or 0) for row in rows) / len(rows)


def build_report(tracker: dict[str, Any]) -> dict[str, Any]:
    resolved = [row for row in tracker.get("resolved_rows") or [] if str(row.get("decision_bucket") or "") == "promote"]
    live = [row for row in tracker.get("live_rows") or [] if str(row.get("decision_bucket") or "") == "promote"]

    by_grade: list[dict[str, Any]] = []
    for grade in ("promote_strong", "promote_probe"):
        rows = [row for row in resolved if str(row.get("decision_grade") or "") == grade]
        if not rows:
            continue
        by_grade.append(
            {
                "decision_grade": grade,
                "n": len(rows),
                "useful_precision": _precision(rows, "label_useful"),
                "survivor_precision": _precision(rows, "survivor_grade"),
                "persistent_precision": _precision(rows, "label_persistent"),
                "examples": [
                    {
                        "symbol": row.get("symbol") or "n/a",
                        "outcome": row.get("class") or "unknown",
                        "useful_score": row.get("useful_score"),
                        "persistent_score": row.get("persistent_score"),
                        "survivor_fit": row.get("survivor_fit"),
                        "regime": row.get("regime") or "unknown",
                    }
                    for row in rows[:5]
                ],
            }
        )

    return {
        "generated_at": time.time(),
        "summary": {
            "resolved_promotes": len(resolved),
            "live_promotes": len(live),
            "live_promote_strong": sum(1 for row in live if str(row.get("decision_grade") or "") == "promote_strong"),
            "live_promote_probe": sum(1 for row in live if str(row.get("decision_grade") or "") == "promote_probe"),
            "rule_notes": [
                "promote_strong if survivor_fit >= 70 and useful_score >= 60",
                "or if persistent_score >= 70 in a calmer regime",
                "otherwise pending promotes stay in promote_probe",
            ],
        },
        "resolved_by_grade": by_grade,
        "live_promotes": live,
    }


def write_md(path: Path, report: dict[str, Any]) -> None:
    s = report["summary"]
    lines = [
        "# Promote Cohort Report",
        "",
        "A focused look at promoted names, split into `promote_strong` and `promote_probe` so we can tighten the one decision bucket that still has the most upside.",
        "",
        "## Summary",
        "",
        f"- Resolved promotes: `{s['resolved_promotes']}`",
        f"- Live promotes: `{s['live_promotes']}`",
        f"- Live promote-strong: `{s['live_promote_strong']}`",
        f"- Live promote-probe: `{s['live_promote_probe']}`",
    ]
    for note in s["rule_notes"]:
        lines.append(f"- {note}")

    lines.extend(
        [
            "",
            "## Resolved Promote Grades",
            "",
            "| Grade | N | Useful Precision | Survivor Precision | Persistent Precision |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in report["resolved_by_grade"]:
        lines.append(
            f"| `{row['decision_grade']}` | {row['n']} | {_fmt_pct(row['useful_precision'])} | {_fmt_pct(row['survivor_precision'])} | {_fmt_pct(row['persistent_precision'])} |"
        )

    if report["live_promotes"]:
        lines.extend(
            [
                "",
                "## Live Promote Cohort",
                "",
                "| Symbol | Grade | Status | Attention | Useful | Persistent | Survivor Fit | Regime |",
                "|---|---|---|---:|---:|---:|---:|---|",
            ]
        )
        for row in report["live_promotes"]:
            lines.append(
                f"| {row.get('symbol') or 'n/a'} | `{row.get('decision_grade')}` | `{row.get('status')}` | "
                f"{_fmt_num(float(row.get('attention_score') or 0.0), 1)} | {_fmt_num(float(row.get('useful_score') or 0.0), 1)} | "
                f"{_fmt_num(float(row.get('persistent_score') or 0.0), 1)} | {_fmt_num(float(row.get('survivor_fit') or 0.0), 1)} | `{row.get('regime') or 'unknown'}` |"
            )

    for row in report["resolved_by_grade"]:
        lines.extend(["", f"### {row['decision_grade']}", ""])
        for ex in row["examples"]:
            lines.append(
                f"- `{ex['symbol']}` -> `{ex['outcome']}` | useful `{_fmt_num(ex['useful_score'], 1)}` | "
                f"persistent `{_fmt_num(ex['persistent_score'], 1)}` | survivor_fit `{_fmt_num(ex['survivor_fit'], 1)}` | regime `{ex['regime']}`"
            )

    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize promote cohort behavior from the decision tracker.")
    parser.add_argument("--tracker", type=Path, default=DECISION_TRACKER_JSON)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=OUT_MD)
    args = parser.parse_args()

    report = build_report(_load_json(args.tracker))
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_md(args.out_md, report)
    print(
        "meme_promote_cohort_report: "
        f"resolved={report['summary']['resolved_promotes']} "
        f"live={report['summary']['live_promotes']}"
    )


if __name__ == "__main__":
    main()
