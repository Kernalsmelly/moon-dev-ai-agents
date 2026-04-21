#!/usr/bin/env python3
"""Run the persistent rank monitor across multiple matured validation windows."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
MONITOR = BASE / "scripts" / "meme_persistent_rank_monitor.py"
DEFAULT_OUT_JSON = BASE / "data" / "meme_reports" / "persistent_validation_sweep.json"
DEFAULT_OUT_MD = BASE / "data" / "meme_reports" / "persistent_validation_sweep.md"


def _run_monitor(*, train_hours: float, validate_hours: float) -> dict[str, Any]:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp_json, tempfile.NamedTemporaryFile(suffix=".md", delete=False):
        tmp_json_path = Path(tmp_json.name)
        cmd = [
            sys.executable,
            str(MONITOR),
            "--train-hours",
            str(train_hours),
            "--validate-hours",
            str(validate_hours),
            "--out-json",
            str(tmp_json_path),
            "--out-md",
            str(Path(tmp_json.name).with_suffix(".md")),
        ]
        subprocess.run(cmd, check=True, cwd=str(BASE), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return json.loads(tmp_json_path.read_text(encoding="utf-8"))


def write_md(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Persistent Validation Sweep",
        "",
        f"- Train window: `{report['config']['train_hours']}h`",
        "",
        "| Validation Window | Anchors | Baseline | >=60 | >=70 | >=75 | Top10 | Top20 | Watchlist |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["windows"]:
        lines.append(
            f"| `{row['validate_hours']}h` | {int(row['anchors'])} | {row['baseline_precision'] * 100:.1f}% | "
            f"{row['threshold_60_precision'] * 100:.1f}% | {row['threshold_70_precision'] * 100:.1f}% | "
            f"{row['threshold_75_precision'] * 100:.1f}% | {row['top10_precision'] * 100:.1f}% | "
            f"{row['top20_precision'] * 100:.1f}% | {int(row['watchlist_count'])} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sweep persistent-rank validation windows.")
    parser.add_argument("--train-hours", type=float, default=72.0)
    parser.add_argument("--windows", type=str, default="12,24,36")
    parser.add_argument("--out-json", type=Path, default=DEFAULT_OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_OUT_MD)
    args = parser.parse_args()

    windows = [float(x.strip()) for x in str(args.windows).split(",") if x.strip()]
    rows: list[dict[str, Any]] = []
    for validate_hours in windows:
        report = _run_monitor(train_hours=float(args.train_hours), validate_hours=validate_hours)
        summary = report["validation"]["summary"]
        rows.append(
            {
                "validate_hours": validate_hours,
                "anchors": int(summary["anchors"]),
                "baseline_precision": float(summary["baseline_precision"]),
                "threshold_60_precision": float(summary["thresholds"]["60"]["precision"]),
                "threshold_70_precision": float(summary["thresholds"]["70"]["precision"]),
                "threshold_75_precision": float(summary["thresholds"]["75"]["precision"]),
                "top10_precision": float(summary["topk"]["10"]["precision"]),
                "top20_precision": float(summary["topk"]["20"]["precision"]),
                "watchlist_count": len(report.get("watchlist_candidates") or []),
            }
        )
    out = {"config": {"train_hours": float(args.train_hours), "windows": windows}, "windows": rows}
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(out, indent=2), encoding="utf-8")
    write_md(args.out_md, out)
    print(f"persistent_validation_sweep: windows={len(rows)}")


if __name__ == "__main__":
    main()
