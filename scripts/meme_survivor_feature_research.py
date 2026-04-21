#!/usr/bin/env python3
"""Research report: what separates survivor-grade winners from useful losers.

This report focuses on the subset of anchors that already became useful winners.
Within that set, it asks which feature buckets are associated with:
- survivor-grade outcomes (`persistent_runner` or `partial_persistence`)
- fake winners (`round_trip_or_spike`)
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
DATASET_CSV = BASE / "data" / "meme_reports" / "meme_anchor_dataset.csv"
OUT_JSON = BASE / "data" / "meme_reports" / "meme_survivor_feature_research.json"
OUT_MD = BASE / "data" / "meme_reports" / "meme_survivor_feature_research.md"

FOCUS_FIELDS = [
    "source_family",
    "persistence_regime0",
    "mover_pattern0",
    "feat__mcap0",
    "feat__pair_age_min0",
    "feat__mom5m0",
    "feat__hits0",
    "feat__net_sol_in0",
    "feat__buy_sell_ratio0",
]

PROFILE_FIELDS = [
    "mcap0",
    "pair_age_min0",
    "mom5m0",
    "hits0",
    "net_sol_in0",
    "buy_sell_ratio0",
]

SURVIVOR_CLASSES = {"persistent_runner", "partial_persistence"}


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


def _median(values: list[Any]) -> float | None:
    clean = [float(v) for v in values if _to_float(v) is not None]
    if not clean:
        return None
    return float(statistics.median(clean))


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100.0:.1f}%"


def _fmt_num(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def load_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for row in rows:
        row["label_useful"] = int(float(row.get("label_useful") or 0))
        row["label_persistent"] = int(float(row.get("label_persistent") or 0))
        row["survivor_grade"] = 1 if str(row.get("persistence_class") or "") in SURVIVOR_CLASSES else 0
    return rows


def _profile(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "n": len(rows),
        "source_counts": dict(Counter(str(r.get("source_family") or "unknown") for r in rows)),
        "regime_counts": dict(Counter(str(r.get("persistence_regime0") or "unknown") for r in rows)),
        "pattern_counts": dict(Counter(str(r.get("mover_pattern0") or "unknown") for r in rows)),
        **{f"median_{field}": _median([r.get(field) for r in rows]) for field in PROFILE_FIELDS},
    }


def build_report(rows: list[dict[str, Any]], *, min_bucket_n: int) -> dict[str, Any]:
    useful_rows = [row for row in rows if int(row.get("label_useful") or 0) == 1]
    survivor_rows = [row for row in useful_rows if int(row.get("survivor_grade") or 0) == 1]
    fail_rows = [row for row in useful_rows if int(row.get("survivor_grade") or 0) == 0]
    baseline = (len(survivor_rows) / len(useful_rows)) if useful_rows else 0.0

    bucket_rows: list[dict[str, Any]] = []
    for field in FOCUS_FIELDS:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in useful_rows:
            groups[str(row.get(field) or "missing")].append(row)
        for value, group in groups.items():
            n = len(group)
            if n < int(min_bucket_n):
                continue
            survivor_count = sum(int(r.get("survivor_grade") or 0) for r in group)
            survivor_rate = (survivor_count / n) if n else 0.0
            bucket_rows.append(
                {
                    "field": field,
                    "value": value,
                    "n": n,
                    "survivor_count": survivor_count,
                    "survivor_rate": survivor_rate,
                    "delta_vs_baseline": survivor_rate - baseline,
                    "lift_vs_baseline": (survivor_rate / baseline) if baseline > 0 else None,
                }
            )

    bucket_rows.sort(
        key=lambda row: (
            float(row["delta_vs_baseline"]),
            float(row["lift_vs_baseline"] or 0.0),
            int(row["n"]),
        ),
        reverse=True,
    )

    return {
        "generated_at": None,
        "summary": {
            "all_rows": len(rows),
            "useful_rows": len(useful_rows),
            "survivor_grade_rows": len(survivor_rows),
            "failed_useful_rows": len(fail_rows),
            "baseline_survivor_precision": baseline,
            "min_bucket_n": int(min_bucket_n),
        },
        "profiles": {
            "survivor_grade": _profile(survivor_rows),
            "failed_useful": _profile(fail_rows),
        },
        "top_positive_buckets": bucket_rows[:20],
        "top_negative_buckets": list(reversed(bucket_rows[-20:])),
    }


def write_md(path: Path, report: dict[str, Any]) -> None:
    s = report["summary"]
    surv = report["profiles"]["survivor_grade"]
    fail = report["profiles"]["failed_useful"]
    lines = [
        "# Survivor Feature Research",
        "",
        "Focused research pass on the useful-winner subset: what separates survivor-grade names from the ones that still fail after winning early.",
        "",
        "## Summary",
        "",
        f"- Useful rows: `{s['useful_rows']}`",
        f"- Survivor-grade rows: `{s['survivor_grade_rows']}`",
        f"- Failed useful rows: `{s['failed_useful_rows']}`",
        f"- Baseline survivor precision within useful winners: `{_fmt_pct(s['baseline_survivor_precision'])}`",
        f"- Minimum bucket size: `{s['min_bucket_n']}`",
        "",
        "## Profile Delta",
        "",
        "| Group | N | Median MCap0 | Median Age0 | Median Mom5m0 | Median Hits0 | Median NetSOL0 | Median B/S Ratio |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| Survivor-grade | {surv['n']} | {_fmt_num(surv['median_mcap0'], 0)} | {_fmt_num(surv['median_pair_age_min0'], 1)} | {_fmt_num(surv['median_mom5m0'], 1)} | {_fmt_num(surv['median_hits0'], 0)} | {_fmt_num(surv['median_net_sol_in0'], 1)} | {_fmt_num(surv['median_buy_sell_ratio0'], 2)} |",
        f"| Failed useful | {fail['n']} | {_fmt_num(fail['median_mcap0'], 0)} | {_fmt_num(fail['median_pair_age_min0'], 1)} | {_fmt_num(fail['median_mom5m0'], 1)} | {_fmt_num(fail['median_hits0'], 0)} | {_fmt_num(fail['median_net_sol_in0'], 1)} | {_fmt_num(fail['median_buy_sell_ratio0'], 2)} |",
        "",
        "## Best Positive Buckets",
        "",
        "| Field | Bucket | N | Survivor | Precision | Lift |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in report["top_positive_buckets"][:12]:
        lines.append(
            f"| `{row['field']}` | `{row['value']}` | {row['n']} | {row['survivor_count']} | {_fmt_pct(row['survivor_rate'])} | {_fmt_num(row['lift_vs_baseline'], 2)}x |"
        )

    lines.extend(
        [
            "",
            "## Worst Buckets",
            "",
            "| Field | Bucket | N | Survivor | Precision | Lift |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in report["top_negative_buckets"][:12]:
        lines.append(
            f"| `{row['field']}` | `{row['value']}` | {row['n']} | {row['survivor_count']} | {_fmt_pct(row['survivor_rate'])} | {_fmt_num(row['lift_vs_baseline'], 2)}x |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Research survivor-grade feature patterns from the anchor dataset.")
    parser.add_argument("--dataset", type=Path, default=DATASET_CSV)
    parser.add_argument("--min-bucket-n", type=int, default=4)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=OUT_MD)
    args = parser.parse_args()

    rows = load_rows(args.dataset)
    report = build_report(rows, min_bucket_n=int(args.min_bucket_n))
    report["generated_at"] = __import__("time").time()
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_md(args.out_md, report)
    print(
        "meme_survivor_feature_research: "
        f"useful={report['summary']['useful_rows']} "
        f"survivor={report['summary']['survivor_grade_rows']}"
    )


if __name__ == "__main__":
    main()
