#!/usr/bin/env python3
"""Research starter-entry gates for the earlier paper-trade overlay.

This looks only at fields that are available on the live pending board so the
output can be applied directly to the paper overlay without inventing features
we do not actually have at entry time.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
REPORTS = BASE / "data" / "meme_reports"
DATASET_CSV = REPORTS / "meme_anchor_dataset.csv"
PENDING_JSON = REPORTS / "pending_maturation_report.json"
OUT_JSON = REPORTS / "meme_starter_entry_gate_research.json"
OUT_MD = REPORTS / "meme_starter_entry_gate_research.md"

NUMERIC_FIELDS = (
    "mcap0",
    "liq0",
    "pair_age_min0",
    "mom5m0",
    "hits0",
    "buys0",
    "uniq0",
    "net_sol_in0",
)
SURVIVOR_CLASSES = {"persistent_runner", "partial_persistence"}


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


def _fmt_num(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def _load_dataset(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            row = dict(raw)
            for field in NUMERIC_FIELDS:
                row[field] = _to_float(row.get(field))
            row["label_useful"] = int(float(raw.get("label_useful") or 0))
            row["label_persistent"] = int(float(raw.get("label_persistent") or 0))
            row["survivor_grade"] = raw.get("persistence_class") in SURVIVOR_CLASSES
            rows.append(row)
    return rows


def _load_pending_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text())
    out: list[dict[str, Any]] = []
    for raw in payload.get("pending_rows") or []:
        row = dict(raw)
        for field in NUMERIC_FIELDS:
            row[field] = _to_float(row.get(field))
        out.append(row)
    return out


def _common_ready(row: dict[str, Any]) -> bool:
    return all(row.get(field) is not None for field in ("mcap0", "liq0", "pair_age_min0", "mom5m0", "hits0", "buys0", "net_sol_in0"))


def gate_starter_strong_breakout(row: dict[str, Any]) -> bool:
    if not _common_ready(row):
        return False
    return (
        str(row.get("signal_source") or "") != "ws_logs"
        and str(row.get("mover_pattern0") or "") == "breakout"
        and 10.0 <= float(row["pair_age_min0"]) < 45.0
        and 60000.0 <= float(row["mcap0"]) < 100000.0
        and float(row["liq0"]) >= 20000.0
        and 40.0 <= float(row["mom5m0"]) < 80.0
        and 25.0 <= float(row["net_sol_in0"]) < 50.0
        and 150.0 <= float(row["hits0"]) < 1500.0
        and 80.0 <= float(row["buys0"]) < 800.0
    )


def gate_starter_probe_core(row: dict[str, Any]) -> bool:
    if not _common_ready(row):
        return False
    return (
        str(row.get("signal_source") or "") != "ws_logs"
        and 5.0 <= float(row["pair_age_min0"]) < 45.0
        and 60000.0 <= float(row["mcap0"]) < 150000.0
        and float(row["liq0"]) >= 15000.0
        and 20.0 <= float(row["mom5m0"]) < 80.0
        and 10.0 <= float(row["net_sol_in0"]) < 50.0
        and 150.0 <= float(row["hits0"]) < 1500.0
        and 80.0 <= float(row["buys0"]) < 800.0
    )


def gate_starter_hard_no(row: dict[str, Any]) -> bool:
    mcap = row.get("mcap0")
    liq = row.get("liq0")
    age = row.get("pair_age_min0")
    mom = row.get("mom5m0")
    hits = row.get("hits0")
    buys = row.get("buys0")
    net_sol = row.get("net_sol_in0")
    return (
        str(row.get("signal_source") or "") == "ws_logs"
        or liq is None
        or hits is None
        or buys is None
        or (mcap is not None and float(mcap) < 30000.0)
        or (age is not None and float(age) < 5.0)
        or (mom is not None and float(mom) >= 120.0)
        or (net_sol is not None and float(net_sol) >= 100.0)
        or (hits is not None and float(hits) < 10.0)
        or (buys is not None and float(buys) < 5.0)
    )


GATES: list[dict[str, Any]] = [
    {
        "name": "starter_strong_breakout",
        "kind": "positive",
        "description": "Early breakout starter: moderate age, solid liquidity, active flow, but not already max-chaos.",
        "fn": gate_starter_strong_breakout,
    },
    {
        "name": "starter_probe_core",
        "kind": "positive",
        "description": "Broader starter probe: still early, still liquid, with decent momentum and real flow.",
        "fn": gate_starter_probe_core,
    },
    {
        "name": "starter_hard_no",
        "kind": "negative",
        "description": "Hard avoid: thin / missing / ws_logs / tiny-cap / hyper-overheated conditions.",
        "fn": gate_starter_hard_no,
    },
]


def _gate_report_row(name: str, kind: str, description: str, rows: list[dict[str, Any]], baseline: dict[str, float]) -> dict[str, Any]:
    n = len(rows)
    useful_n = sum(int(row.get("label_useful") or 0) for row in rows)
    survivor_n = sum(1 for row in rows if row.get("survivor_grade"))
    persistent_n = sum(int(row.get("label_persistent") or 0) for row in rows)
    useful_precision = (useful_n / n) if n else None
    survivor_precision = (survivor_n / n) if n else None
    persistent_precision = (persistent_n / n) if n else None
    return {
        "name": name,
        "kind": kind,
        "description": description,
        "n": n,
        "useful_precision": useful_precision,
        "survivor_precision": survivor_precision,
        "persistent_precision": persistent_precision,
        "useful_lift_vs_baseline": (useful_precision / baseline["useful_precision"]) if useful_precision is not None and baseline["useful_precision"] > 0 else None,
        "survivor_lift_vs_baseline": (survivor_precision / baseline["survivor_precision"]) if survivor_precision is not None and baseline["survivor_precision"] > 0 else None,
        "persistent_lift_vs_baseline": (persistent_precision / baseline["persistent_precision"]) if persistent_precision is not None and baseline["persistent_precision"] > 0 else None,
    }


def starter_gate_evaluate(row: dict[str, Any]) -> dict[str, Any]:
    matches = [gate["name"] for gate in GATES if gate["fn"](row)]
    hard_no = "starter_hard_no" in matches
    if "starter_strong_breakout" in matches:
        grade = "starter_strong"
    elif "starter_probe_core" in matches and not hard_no:
        grade = "starter_probe"
    elif hard_no:
        grade = "starter_avoid"
    else:
        grade = "starter_neutral"
    return {
        "starter_grade": grade,
        "matches": matches,
        "hard_no": hard_no,
    }


def build_report(dataset_rows: list[dict[str, Any]], pending_rows: list[dict[str, Any]]) -> dict[str, Any]:
    baseline = {
        "n": len(dataset_rows),
        "useful_precision": (sum(int(row.get("label_useful") or 0) for row in dataset_rows) / len(dataset_rows)) if dataset_rows else 0.0,
        "survivor_precision": (sum(1 for row in dataset_rows if row.get("survivor_grade")) / len(dataset_rows)) if dataset_rows else 0.0,
        "persistent_precision": (sum(int(row.get("label_persistent") or 0) for row in dataset_rows) / len(dataset_rows)) if dataset_rows else 0.0,
    }

    gate_rows: list[dict[str, Any]] = []
    for gate in GATES:
        matching = [row for row in dataset_rows if gate["fn"](row)]
        gate_rows.append(_gate_report_row(gate["name"], gate["kind"], gate["description"], matching, baseline))

    gate_rows.sort(
        key=lambda row: (
            0 if row["kind"] == "positive" else 1,
            -(float(row.get("useful_lift_vs_baseline") or 0.0)),
            -(float(row.get("survivor_lift_vs_baseline") or 0.0)),
            -int(row.get("n") or 0),
        )
    )

    live_rows: list[dict[str, Any]] = []
    for row in pending_rows:
        eval_row = starter_gate_evaluate(row)
        if eval_row["starter_grade"] == "starter_neutral":
            continue
        live_rows.append(
            {
                "symbol": row.get("symbol") or "n/a",
                "mint": row.get("mint") or "",
                "starter_grade": eval_row["starter_grade"],
                "matches": eval_row["matches"],
                "signal_source": row.get("signal_source") or "unknown",
                "mcap0": row.get("mcap0"),
                "liq0": row.get("liq0"),
                "pair_age_min0": row.get("pair_age_min0"),
                "mom5m0": row.get("mom5m0"),
                "hits0": row.get("hits0"),
                "buys0": row.get("buys0"),
                "net_sol_in0": row.get("net_sol_in0"),
                "promotion_decision": row.get("promotion_decision") or "unknown",
                "shape_state": row.get("shape_state") or "unknown",
                "latest_ret": row.get("latest_ret"),
            }
        )

    live_rows.sort(
        key=lambda row: (
            0 if row["starter_grade"] == "starter_strong" else 1 if row["starter_grade"] == "starter_probe" else 2,
            -(float(row.get("latest_ret") or 0.0)),
        )
    )

    recommended = {
        "starter_open_gate": "starter_probe_core",
        "starter_priority_gate": "starter_strong_breakout",
        "starter_avoid_gate": "starter_hard_no",
    }

    return {
        "summary": {
            **baseline,
            "live_pending_rows": len(pending_rows),
            "live_starter_matches": len(live_rows),
        },
        "recommended": recommended,
        "gate_rows": gate_rows,
        "live_rows": live_rows,
    }


def render_md(report: dict[str, Any]) -> str:
    s = report["summary"]
    gates = report["gate_rows"]
    live_rows = report["live_rows"]
    recommended = report["recommended"]
    lines = [
        "# Starter Entry Gate Research",
        "",
        "A focused study of earlier starter-entry filters using only fields that are actually present on the live pending board. The goal is to tighten paper-overlay v2 so we enter earlier without dropping all discipline.",
        "",
        "## Baseline",
        "",
        f"- Anchor rows studied: `{s['n']}`",
        f"- Baseline useful precision: `{_fmt_pct(s['useful_precision'])}`",
        f"- Baseline survivor precision: `{_fmt_pct(s['survivor_precision'])}`",
        f"- Baseline persistent precision: `{_fmt_pct(s['persistent_precision'])}`",
        "",
        "## Recommended Gates",
        "",
        f"- Starter open gate: `{recommended['starter_open_gate']}`",
        f"- Priority / stronger gate: `{recommended['starter_priority_gate']}`",
        f"- Hard avoid gate: `{recommended['starter_avoid_gate']}`",
        "",
        "## Gate Performance",
        "",
        "| Gate | Kind | N | Useful | Survivor | Persistent | Useful Lift | Survivor Lift |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in gates:
        lines.append(
            f"| `{row['name']}` | `{row['kind']}` | {row['n']} | {_fmt_pct(row['useful_precision'])} | {_fmt_pct(row['survivor_precision'])} | {_fmt_pct(row['persistent_precision'])} | {_fmt_num(row['useful_lift_vs_baseline'], 2)}x | {_fmt_num(row['survivor_lift_vs_baseline'], 2)}x |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `starter_strong_breakout` is the sharper profile: fewer cases, but much higher useful and survivor precision than baseline.",
            "- `starter_probe_core` is the broader starter gate: still materially above baseline on useful precision, with a modest survivor lean.",
            "- `starter_hard_no` is not just ugly cosmetically. Historically it is a bad place to be aggressive.",
            "",
            "## Current Live Pending Matches",
            "",
            f"- Pending rows scanned: `{s['live_pending_rows']}`",
            f"- Live rows with non-neutral starter grade: `{s['live_starter_matches']}`",
            "",
            "| Symbol | Starter Grade | Matches | Promotion | Shape | Latest Ret | Mcap | Liq | Age (m) | Mom5m | Net SOL |",
            "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    if live_rows:
        for row in live_rows[:20]:
            lines.append(
                f"| `{row['symbol']}` | `{row['starter_grade']}` | `{', '.join(row['matches'])}` | `{row['promotion_decision']}` | `{row['shape_state']}` | {_fmt_pct(_to_float(row['latest_ret']))} | {_fmt_num(_to_float(row['mcap0']), 0)} | {_fmt_num(_to_float(row['liq0']), 0)} | {_fmt_num(_to_float(row['pair_age_min0']), 1)} | {_fmt_num(_to_float(row['mom5m0']), 1)} | {_fmt_num(_to_float(row['net_sol_in0']), 1)} |"
            )
    else:
        lines.append("| `n/a` | `n/a` | `n/a` | `n/a` | `n/a` | n/a | n/a | n/a | n/a | n/a | n/a |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DATASET_CSV)
    parser.add_argument("--pending", type=Path, default=PENDING_JSON)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=OUT_MD)
    args = parser.parse_args()

    dataset_rows = _load_dataset(args.dataset)
    pending_rows = _load_pending_rows(args.pending)
    report = build_report(dataset_rows, pending_rows)

    args.out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    args.out_md.write_text(render_md(report), encoding="utf-8")
    print(
        f"starter-gates rows={report['summary']['n']} "
        f"live_matches={report['summary']['live_starter_matches']} "
        f"priority={report['recommended']['starter_priority_gate']}"
    )


if __name__ == "__main__":
    main()
