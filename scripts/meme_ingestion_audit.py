#!/usr/bin/env python3
"""Unified source-by-source ingestion audit for the meme pipeline.

This report is meant to answer a simple operator question:
"Are the sources really healthy, or are they only technically running?"

It combines:
- process presence
- log freshness
- recent signal tape freshness by source
- source-specific heartbeat clues from recent logs
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
REPORTS = BASE / "data" / "meme_reports"
LOGS = BASE / "logs"

load_dotenv(dotenv_path=BASE / ".env", override=True)

SIGNALS_FILE = Path(
    (os.getenv("MEME_LAUNCH_SIGNALS_FILE") or "").strip() or str(BASE / "data" / "meme_launch_signals.jsonl")
)
OUTCOMES_FILE = Path(
    (os.getenv("SIGNAL_OUTCOMES_FILE") or "").strip() or str(BASE / "data" / "signal_outcomes.jsonl")
)
OUT_JSON = REPORTS / "meme_ingestion_audit.json"
OUT_MD = REPORTS / "meme_ingestion_audit.md"

NOW = time.time()


def _bool_env(name: str, default: str) -> bool:
    return str(os.getenv(name, default) or default).strip().lower() in ("1", "true", "yes", "on")


def _fmt_age(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60.0:.1f}m"
    return f"{seconds / 3600.0:.2f}h"


def _fmt_count(value: int | None) -> str:
    if value is None:
        return "n/a"
    return str(int(value))


def _path_age_s(path: Path) -> float | None:
    try:
        return max(0.0, NOW - path.stat().st_mtime)
    except Exception:
        return None


def _tail_lines(path: Path, n: int = 40, read_bytes: int = 16384) -> list[str]:
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - max(1024, int(read_bytes))))
            data = fh.read().decode("utf-8", errors="ignore")
        lines = [line for line in data.splitlines() if line.strip()]
        return lines[-n:]
    except Exception:
        return []


def _pgrep_matches(pattern: str) -> list[str]:
    try:
        proc = subprocess.run(
            ["pgrep", "-af", pattern],
            capture_output=True,
            text=True,
            check=False,
        )
        lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        return lines
    except Exception:
        return []


def _parse_keyvals(line: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, raw in re.findall(r"([A-Za-z0-9_]+)=([A-Za-z0-9_.:+-]+)", line):
        if raw == "none":
            out[key] = None
            continue
        try:
            if "." in raw:
                out[key] = float(raw)
            else:
                out[key] = int(raw)
            continue
        except Exception:
            out[key] = raw
    return out


def _source_summary_from_tape(path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return out
    cut_30m = NOW - 1800.0
    cut_24h = NOW - 86400.0
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            try:
                ts = float(row.get("ts") or 0.0)
            except Exception:
                ts = 0.0
            if ts <= 0:
                continue
            metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
            source = str(metrics.get("source") or row.get("source") or "unknown").strip() or "unknown"
            bucket = out.setdefault(
                source,
                {"last_ts": None, "count_total": 0, "count_30m": 0, "count_24h": 0},
            )
            bucket["count_total"] = int(bucket.get("count_total") or 0) + 1
            last_ts = bucket.get("last_ts")
            if last_ts is None or ts > float(last_ts):
                bucket["last_ts"] = ts
            if ts >= cut_24h:
                bucket["count_24h"] = int(bucket.get("count_24h") or 0) + 1
            if ts >= cut_30m:
                bucket["count_30m"] = int(bucket.get("count_30m") or 0) + 1
    return out


@dataclass(frozen=True)
class SourceSpec:
    key: str
    label: str
    enabled: bool
    process_pattern: str
    log_path: Path
    tape_source: str | None = None


SOURCES = [
    SourceSpec(
        key="dex_mover",
        label="Dex Mover",
        enabled=_bool_env("MEME_ENABLE_DEX_MOVER", "1"),
        process_pattern="dex_mover_signal_listener.py",
        log_path=LOGS / "dex_mover_signal_listener.log",
        tape_source="dex_mover",
    ),
    SourceSpec(
        key="pump_ws",
        label="Pump WS",
        enabled=_bool_env("MEME_ENABLE_PUMP_WS", "0"),
        process_pattern="pump_ws_signal_listener.py",
        log_path=LOGS / "pump_ws_signal_listener.log",
        tape_source="ws_logs",
    ),
    SourceSpec(
        key="raydium_ws",
        label="Raydium WS",
        enabled=_bool_env("MEME_ENABLE_RAYDIUM_WS", "1"),
        process_pattern="raydium_pool_ws_listener.py",
        log_path=LOGS / "raydium_pool_ws_listener.log",
        tape_source="raydium_pool",
    ),
    SourceSpec(
        key="wallet_outlier",
        label="Wallet Outlier",
        enabled=_bool_env("MEME_ENABLE_WALLET_OUTLIER", "0"),
        process_pattern="wallet_outlier_signal_listener.py",
        log_path=LOGS / "wallet_outlier_signal_listener.log",
        tape_source="wallet_outlier",
    ),
    SourceSpec(
        key="outcome_recorder",
        label="Outcome Recorder",
        enabled=True,
        process_pattern="signal_outcome_recorder.py",
        log_path=LOGS / "signal_outcome_recorder.log",
    ),
]


def _classify(spec: SourceSpec, row: dict[str, Any]) -> tuple[str, str]:
    if not spec.enabled:
        return "disabled", "disabled in env"
    if not row["process_running"]:
        return "down", "process not running"
    log_age_s = row.get("log_age_s")
    if log_age_s is None or log_age_s > 900:
        return "degraded", "log heartbeat is stale"

    if spec.key == "dex_mover":
        age = row.get("tape_last_age_s")
        if age is not None and age <= 300:
            return "healthy", "fresh tape events are arriving"
        if age is not None and age <= 1800:
            return "caution", "process is alive but recent tape flow slowed"
        return "degraded", "process alive but tape output is stale"

    if spec.key == "pump_ws":
        age = row.get("tape_last_age_s")
        ws_msgs = row.get("log_fields", {}).get("ws_msgs")
        emitted = row.get("log_fields", {}).get("emitted")
        if age is not None and age <= 3600:
            return "healthy", "ws-derived signals reached the tape recently"
        if ws_msgs and float(ws_msgs) > 0 and emitted == 0 and (age is None or age > 14400):
            return "degraded", "websocket is alive but ws_logs source is stale"
        if ws_msgs and float(ws_msgs) > 0:
            return "idle", "websocket is alive but not producing accepted signals"
        return "degraded", "no recent websocket activity visible"

    if spec.key == "raydium_ws":
        age = row.get("tape_last_age_s")
        total = row.get("tape_count_total")
        notif = row.get("log_fields", {}).get("notif")
        seen_sigs = row.get("log_fields", {}).get("seen_sigs")
        if not total:
            return "degraded", "heartbeat is healthy but raydium_pool has never reached the tape"
        if age is not None and age <= 14400:
            return "healthy", "raydium pool source reached the tape recently"
        if (notif and float(notif) > 0) or (seen_sigs and float(seen_sigs) > 0):
            return "idle", "heartbeat is healthy but no recent raydium_pool emits"
        return "degraded", "raydium listener is up but not showing activity"

    if spec.key == "wallet_outlier":
        age = row.get("tape_last_age_s")
        scanned = row.get("log_fields", {}).get("scanned")
        emitted = row.get("log_fields", {}).get("emitted")
        if age is not None and age <= 21600:
            return "healthy", "wallet_outlier produced a recent boosted signal"
        if scanned and int(scanned) > 0:
            return "caution", "wallet_outlier is scanning but has not emitted recently"
        if emitted == 0:
            return "idle", "wallet_outlier is running but dormant"
        return "degraded", "wallet_outlier is not showing useful work"

    if spec.key == "outcome_recorder":
        outcome_age = row.get("outcomes_age_s")
        if outcome_age is not None and outcome_age <= 600:
            return "healthy", "outcomes file is updating"
        pending = row.get("log_fields", {}).get("pending")
        if pending is not None:
            return "caution", "recorder is alive but outcomes file freshness slipped"
        return "degraded", "recorder heartbeat is weak"

    return "caution", "unclassified"


def _latest_log_fields(spec: SourceSpec, lines: list[str]) -> tuple[str | None, dict[str, Any]]:
    if spec.key == "pump_ws":
        for line in reversed(lines):
            if "WS emit_stats" in line:
                return line, _parse_keyvals(line)
        for line in reversed(lines):
            if "WS status" in line:
                return line, _parse_keyvals(line)
        return None, {}

    if spec.key == "raydium_ws":
        for line in reversed(lines):
            if "raydium_ws heartbeat" in line:
                return line, _parse_keyvals(line)
        return None, {}

    if spec.key == "dex_mover":
        for line in reversed(lines):
            if line.startswith("dex_mover "):
                return line, _parse_keyvals(line)
        return None, {}

    if spec.key == "wallet_outlier":
        for line in reversed(lines):
            if line.startswith("wallet_outlier "):
                return line, _parse_keyvals(line)
        return None, {}

    if spec.key == "outcome_recorder":
        for line in reversed(lines):
            if line.startswith("outcomes:"):
                return line, _parse_keyvals(line)
        return None, {}

    return None, {}


def build_report() -> dict[str, Any]:
    tape_summary = _source_summary_from_tape(SIGNALS_FILE)
    rows: list[dict[str, Any]] = []

    for spec in SOURCES:
        proc_matches = _pgrep_matches(spec.process_pattern)
        log_age_s = _path_age_s(spec.log_path)
        log_lines = _tail_lines(spec.log_path)
        latest_log_line, latest_log_fields = _latest_log_fields(spec, log_lines)

        tape_bucket = tape_summary.get(spec.tape_source or "", {})
        tape_last_ts = tape_bucket.get("last_ts")
        tape_last_age_s = (NOW - float(tape_last_ts)) if tape_last_ts else None

        row = {
            "key": spec.key,
            "label": spec.label,
            "enabled": spec.enabled,
            "process_running": bool(proc_matches),
            "process_matches": proc_matches,
            "log_path": str(spec.log_path),
            "log_age_s": log_age_s,
            "latest_log_line": latest_log_line,
            "log_fields": latest_log_fields,
            "tape_source": spec.tape_source,
            "tape_count_total": int(tape_bucket.get("count_total") or 0),
            "tape_count_30m": int(tape_bucket.get("count_30m") or 0),
            "tape_count_24h": int(tape_bucket.get("count_24h") or 0),
            "tape_last_ts": tape_last_ts,
            "tape_last_age_s": tape_last_age_s,
        }
        if spec.key == "outcome_recorder":
            row["outcomes_age_s"] = _path_age_s(OUTCOMES_FILE)
            row["outcomes_path"] = str(OUTCOMES_FILE)
        status, reason = _classify(spec, row)
        row["status"] = status
        row["reason"] = reason
        rows.append(row)

    status_counts: dict[str, int] = {}
    for row in rows:
        status_counts[row["status"]] = int(status_counts.get(row["status"], 0) or 0) + 1

    overall = "healthy"
    if status_counts.get("down") or status_counts.get("degraded"):
        overall = "degraded"
    elif status_counts.get("caution") or status_counts.get("idle"):
        overall = "caution"

    recommendations: list[str] = []
    for row in rows:
        if row["status"] in {"down", "degraded"}:
            recommendations.append(f"{row['label']}: {row['reason']}.")
        elif row["status"] == "idle":
            recommendations.append(f"{row['label']}: running, but currently idle; confirm whether this is expected.")

    if not recommendations:
        recommendations.append("All configured sources look healthy right now.")

    return {
        "generated_at": NOW,
        "generated_at_iso": datetime.fromtimestamp(NOW).astimezone().isoformat(),
        "signals_file": str(SIGNALS_FILE),
        "outcomes_file": str(OUTCOMES_FILE),
        "overall_status": overall,
        "status_counts": status_counts,
        "sources": rows,
        "recommendations": recommendations,
    }


def write_md(report: dict[str, Any]) -> None:
    lines = [
        "# Meme Ingestion Audit",
        "",
        f"- Generated: `{report['generated_at_iso']}`",
        f"- Overall status: `{report['overall_status']}`",
        f"- Signals tape: `{report['signals_file']}`",
        f"- Outcomes file: `{report['outcomes_file']}`",
        "",
        "## Source Status",
        "",
        "| Source | Enabled | Process | Log Age | Tape Total | Tape 30m | Tape 24h | Last Tape Event | Status | Why |",
        "|---|---|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in report["sources"]:
        lines.append(
            f"| {row['label']} | `{str(row['enabled']).lower()}` | `{str(row['process_running']).lower()}` | "
            f"{_fmt_age(row.get('log_age_s'))} | {_fmt_count(row.get('tape_count_total'))} | {_fmt_count(row.get('tape_count_30m'))} | {_fmt_count(row.get('tape_count_24h'))} | "
            f"{_fmt_age(row.get('tape_last_age_s'))} | `{row['status']}` | {row['reason']} |"
        )

    lines.extend(["", "## Notes", ""])
    for row in report["sources"]:
        if row.get("latest_log_line"):
            lines.append(f"- `{row['label']}` latest log: `{row['latest_log_line']}`")

    lines.extend(["", "## Recommendations", ""])
    for rec in report["recommendations"]:
        lines.append(f"- {rec}")
    lines.append("")
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    report = build_report()
    OUT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_md(report)
    print(
        f"meme_ingestion_audit: overall={report['overall_status']} "
        f"healthy={report['status_counts'].get('healthy', 0)} "
        f"idle={report['status_counts'].get('idle', 0)} "
        f"degraded={report['status_counts'].get('degraded', 0)} "
        f"down={report['status_counts'].get('down', 0)}"
    )


if __name__ == "__main__":
    main()
