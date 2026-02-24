#!/usr/bin/env python3
"""Report metadata completeness/quality for meme trade records."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_FIELDS = [
    "market_cap_entry",
    "liquidity_entry",
    "signal_score",
    "signal_hits",
    "signal_unique_buyers",
    "signal_net_sol_in",
]


def _ts(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        try:
            return float(v)
        except Exception:
            return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def _is_present(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    return True


def _is_usable(field: str, v: Any) -> bool:
    if not _is_present(v):
        return False
    if field.endswith("_used"):
        try:
            return int(v) > 0
        except Exception:
            return False
    if field in {"signal_hits", "signal_unique_buyers"}:
        try:
            return int(v) > 0
        except Exception:
            return False
    if field in {"market_cap_entry", "liquidity_entry", "signal_score", "winner_score"}:
        try:
            return float(v) > 0.0
        except Exception:
            return False
    if field == "signal_net_sol_in":
        try:
            fv = float(v)
            return math.isfinite(fv)
        except Exception:
            return False
    return True


def _load_rows(db: str, since_h: int) -> list[dict[str, Any]]:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        select created_at, exit_reason, metadata
        from trades
        where metadata is not null and metadata != ''
        """,
    ).fetchall()
    con.close()

    cutoff = time.time() - max(1, int(since_h)) * 3600
    out = []
    for r in rows:
        ts = _ts(r["created_at"])
        if ts is None or ts < cutoff:
            continue
        try:
            md = json.loads(r["metadata"] or "{}")
        except Exception:
            md = {}
        if not isinstance(md, dict):
            md = {}
        out.append({"ts": ts, "exit_reason": str(r["exit_reason"] or "UNKNOWN"), "metadata": md})
    return out


def _build_report(rows: list[dict[str, Any]], fields: list[str]) -> dict[str, Any]:
    total = len(rows)
    present_counts = Counter()
    usable_counts = Counter()
    missing_combo = Counter()
    by_reason = defaultdict(lambda: {"n": 0, "complete": 0})

    complete = 0
    for r in rows:
        md = r["metadata"]
        reason = r["exit_reason"]
        by_reason[reason]["n"] += 1
        missing = []
        for f in fields:
            v = md.get(f)
            if _is_present(v):
                present_counts[f] += 1
            if _is_usable(f, v):
                usable_counts[f] += 1
            else:
                missing.append(f)
        if not missing:
            complete += 1
            by_reason[reason]["complete"] += 1
        else:
            missing_combo[",".join(missing)] += 1

    field_rows = []
    for f in fields:
        p = present_counts[f]
        u = usable_counts[f]
        field_rows.append(
            {
                "field": f,
                "present_n": p,
                "present_pct": (p / total) if total else 0.0,
                "usable_n": u,
                "usable_pct": (u / total) if total else 0.0,
            }
        )
    field_rows.sort(key=lambda x: x["usable_pct"])

    reason_rows = []
    for reason, d in by_reason.items():
        n = int(d["n"])
        c = int(d["complete"])
        reason_rows.append(
            {
                "exit_reason": reason,
                "trades": n,
                "complete_n": c,
                "complete_pct": (c / n) if n else 0.0,
            }
        )
    reason_rows.sort(key=lambda x: x["complete_pct"])

    return {
        "generated_at": time.time(),
        "total_trades": total,
        "fields": fields,
        "complete_n": complete,
        "complete_pct": (complete / total) if total else 0.0,
        "by_field": field_rows,
        "top_missing_combos": [{"missing_fields": k, "count": v} for k, v in missing_combo.most_common(10)],
        "by_exit_reason": reason_rows[:15],
    }


def _write_md(rep: dict[str, Any], out_md: Path) -> None:
    lines = []
    lines.append("# Meme Metadata Quality")
    lines.append("")
    lines.append(f"- Trades analyzed: `{rep['total_trades']}`")
    lines.append(f"- Complete rows: `{rep['complete_n']}` ({rep['complete_pct']*100:.1f}%)")
    lines.append("")
    lines.append("## Field Coverage")
    lines.append("")
    lines.append("| Field | Present | Usable |")
    lines.append("|---|---:|---:|")
    for r in rep["by_field"]:
        lines.append(
            f"| {r['field']} | {r['present_pct']*100:.1f}% ({r['present_n']}) | "
            f"{r['usable_pct']*100:.1f}% ({r['usable_n']}) |"
        )
    lines.append("")
    lines.append("## Top Missing Combos")
    lines.append("")
    lines.append("| Missing Fields | Count |")
    lines.append("|---|---:|")
    for x in rep["top_missing_combos"]:
        lines.append(f"| `{x['missing_fields']}` | {x['count']} |")
    lines.append("")
    lines.append("## Completeness by Exit Reason")
    lines.append("")
    lines.append("| Exit Reason | Trades | Complete |")
    lines.append("|---|---:|---:|")
    for x in rep["by_exit_reason"]:
        lines.append(
            f"| {x['exit_reason']} | {x['trades']} | {x['complete_pct']*100:.1f}% ({x['complete_n']}) |"
        )
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/positions.db")
    p.add_argument("--since-hours", type=int, default=72)
    p.add_argument("--fields", default=",".join(DEFAULT_FIELDS))
    p.add_argument("--out-json", default="data/meme_metadata_quality_report.json")
    p.add_argument("--out-md", default="data/meme_metadata_quality_report.md")
    args = p.parse_args()

    fields = [x.strip() for x in str(args.fields).split(",") if x.strip()]
    rows = _load_rows(args.db, args.since_hours)
    rep = _build_report(rows, fields)

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(rep, indent=2), encoding="utf-8")
    _write_md(rep, Path(args.out_md))
    print(f"wrote {out_json}")
    print(f"wrote {args.out_md}")
    print(f"complete={rep['complete_n']}/{rep['total_trades']} ({rep['complete_pct']*100:.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
