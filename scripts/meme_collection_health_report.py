#!/usr/bin/env python3
"""Assess collection health and recent gaps in the meme signal tape."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
TAPE = BASE / "data" / "meme_launch_signals.jsonl"
OUT_JSON = BASE / "data" / "meme_reports" / "meme_collection_health_report.json"
OUT_MD = BASE / "data" / "meme_reports" / "meme_collection_health_report.md"


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        out = float(value)
        if math.isfinite(out):
            return out
    except Exception:
        return None
    return None


def _fmt_num(value: float | None, digits: int = 0) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def _fmt_seconds(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value < 60:
        return f"{value:.0f}s"
    if value < 3600:
        return f"{value/60.0:.1f}m"
    return f"{value/3600.0:.2f}h"


def load_timestamps(path: Path) -> list[float]:
    out: list[float] = []
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            ts = _to_float(row.get("ts"))
            if ts is not None:
                out.append(ts)
    out.sort()
    return out


def analyze_window(timestamps: list[float], *, now: float, hours: float) -> dict[str, Any]:
    since_ts = now - (hours * 3600.0)
    recent = [ts for ts in timestamps if ts >= since_ts]
    gaps = []
    if len(recent) >= 2:
        prev = recent[0]
        for ts in recent[1:]:
            gap = ts - prev
            gaps.append(gap)
            prev = ts
    large_gaps = [gap for gap in gaps if gap >= 900.0]
    hourly = Counter(int((ts - since_ts) // 3600.0) for ts in recent if ts >= since_ts)
    last_signal_age_s = (now - recent[-1]) if recent else None
    largest_gap_s = max(large_gaps) if large_gaps else 0.0
    median_gap_s = statistics.median(gaps) if gaps else None
    if not recent:
        status = "down"
    elif (last_signal_age_s or 0.0) >= 1800.0 or largest_gap_s >= 7200.0:
        status = "degraded"
    elif largest_gap_s >= 1800.0:
        status = "caution"
    else:
        status = "healthy"
    return {
        "hours": hours,
        "signals": len(recent),
        "largest_gap_s": largest_gap_s,
        "median_gap_s": median_gap_s,
        "gaps_15m_plus": len(large_gaps),
        "last_signal_age_s": last_signal_age_s,
        "status": status,
        "hourly_counts": {str(k): v for k, v in sorted(hourly.items())},
    }


def build_report(timestamps: list[float]) -> dict[str, Any]:
    now = time.time()
    windows = {
        "6h": analyze_window(timestamps, now=now, hours=6.0),
        "24h": analyze_window(timestamps, now=now, hours=24.0),
        "48h": analyze_window(timestamps, now=now, hours=48.0),
    }
    largest_gaps = []
    for label, data in windows.items():
        if float(data.get("largest_gap_s") or 0.0) > 0:
            largest_gaps.append({"window": label, "largest_gap_s": data["largest_gap_s"]})
    return {
        "generated_at": now,
        "tape_path": str(TAPE),
        "total_rows": len(timestamps),
        "first_ts": timestamps[0] if timestamps else None,
        "last_ts": timestamps[-1] if timestamps else None,
        "windows": windows,
        "largest_gaps": largest_gaps,
    }


def write_md(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Meme Collection Health Report",
        "",
        f"- Tape: `{report['tape_path']}`",
        f"- Total rows: `{report['total_rows']}`",
        f"- Last signal age: `{_fmt_seconds(time.time() - report['last_ts']) if report.get('last_ts') else 'n/a'}`",
        "",
        "| Window | Status | Signals | Largest Gap | Median Gap | Gaps >=15m | Last Signal Age |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for label, data in report["windows"].items():
        lines.append(
            f"| `{label}` | `{data['status']}` | {int(data['signals'])} | {_fmt_seconds(data['largest_gap_s'])} | "
            f"{_fmt_seconds(data['median_gap_s'])} | {int(data['gaps_15m_plus'])} | {_fmt_seconds(data['last_signal_age_s'])} |"
        )
    lines.extend(["", "## Notes", ""])
    for label, data in report["windows"].items():
        if data["status"] != "healthy":
            lines.append(
                f"- `{label}` marked `{data['status']}` because largest gap was `{_fmt_seconds(data['largest_gap_s'])}` and last signal age is `{_fmt_seconds(data['last_signal_age_s'])}`."
            )
    lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Assess recent collection health for the meme signal tape.")
    parser.add_argument("--file", type=Path, default=TAPE)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=OUT_MD)
    args = parser.parse_args()

    timestamps = load_timestamps(args.file)
    report = build_report(timestamps)
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_md(args.out_md, report)
    print(
        f"meme_collection_health_report: rows={report['total_rows']} "
        f"status24={report['windows']['24h']['status']} gap24={report['windows']['24h']['largest_gap_s']:.0f}s"
    )


if __name__ == "__main__":
    main()
