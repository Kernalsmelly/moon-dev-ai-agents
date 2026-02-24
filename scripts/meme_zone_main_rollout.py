#!/usr/bin/env python3
"""Promote winner-zone policy to main lane with rollback guard."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sqlite3
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
ENV_PATH = BASE / ".env"
DB_PATH = BASE / "data" / "positions.db"
LOG_BOT = BASE / "logs" / "meme_bot_early_edge_auto.log"
ROLLOUT_DIR = BASE / "data" / "meme_reports" / "zone_rollout"
ENV_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$")


def _f(x: Any, d: float = 0.0) -> float:
    try:
        if x is None:
            return d
        return float(x)
    except Exception:
        return d


def _load_env_lines() -> list[str]:
    if not ENV_PATH.exists():
        return []
    return ENV_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()


def _write_env_lines(lines: list[str]) -> None:
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _set_env_values(changes: dict[str, str]) -> list[tuple[str, str | None, str]]:
    lines = _load_env_lines()
    idx: dict[str, int] = {}
    for i, ln in enumerate(lines):
        m = ENV_RE.match(ln)
        if m:
            idx[m.group(1)] = i
    applied: list[tuple[str, str | None, str]] = []
    for k, v in changes.items():
        nline = f"{k}={v}"
        if k in idx:
            old = lines[idx[k]]
            lines[idx[k]] = nline
            applied.append((k, old, nline))
        else:
            lines.append(nline)
            applied.append((k, None, nline))
    _write_env_lines(lines)
    return applied


def _find_main_bot_pid() -> int:
    # Main bot is the supervisor child; A/B bots are detached under pid 1.
    try:
        import subprocess

        out = subprocess.check_output(["ps", "-axo", "pid,ppid,command"], text=True)
        sup_pid = 0
        for ln in out.splitlines():
            if "scripts/meme_pipeline_supervisor.py" in ln and "rg" not in ln:
                parts = ln.strip().split(None, 2)
                if parts:
                    sup_pid = int(parts[0])
                    break
        if sup_pid <= 0:
            return 0
        for ln in out.splitlines():
            if "src/meme_bot.py" not in ln or "meme_ab_" in ln:
                continue
            parts = ln.strip().split(None, 2)
            if len(parts) < 2:
                continue
            pid = int(parts[0])
            ppid = int(parts[1])
            if ppid == sup_pid:
                return pid
    except Exception:
        return 0
    return 0


def _restart_main_bot() -> int:
    pid = _find_main_bot_pid()
    if pid > 0:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
    return pid


def _tail_run_id() -> str:
    if not LOG_BOT.exists():
        return ""
    text = LOG_BOT.read_text(encoding="utf-8", errors="ignore")
    lines = [ln for ln in text.splitlines() if "run_id=" in ln]
    if not lines:
        return ""
    last = lines[-1]
    try:
        rid = last.split("run_id=", 1)[1].replace("[/dim]", "").strip()
        return rid
    except Exception:
        return ""


def _run_pnl_for_run_id(run_id: str) -> tuple[int, float]:
    if not run_id or not DB_PATH.exists():
        return 0, 0.0
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    rows = cur.execute("SELECT pnl_usd, metadata FROM trades ORDER BY created_at ASC").fetchall()
    con.close()
    n = 0
    pnl = 0.0
    for r in rows:
        md_raw = r["metadata"] or "{}"
        try:
            md = json.loads(md_raw) if isinstance(md_raw, str) else (md_raw if isinstance(md_raw, dict) else {})
        except Exception:
            md = {}
        rid = str((md or {}).get("run_id") or "").strip()
        if rid != run_id:
            continue
        n += 1
        pnl += _f(r["pnl_usd"], 0.0)
    return n, float(pnl)


def _write_report(path_json: Path, path_md: Path, obj: dict[str, Any]) -> None:
    path_json.parent.mkdir(parents=True, exist_ok=True)
    path_md.parent.mkdir(parents=True, exist_ok=True)
    path_json.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")
    lines = [
        "# Zone Main Rollout",
        "",
        f"- started_at: {obj.get('started_at')}",
        f"- status: `{obj.get('status')}`",
        f"- rollout_run_id: `{obj.get('rollout_run_id')}`",
        f"- monitor_minutes: {obj.get('monitor_minutes')}",
        f"- rollback_max_loss_usd: {obj.get('rollback_max_loss_usd')}",
        f"- rollback_min_trades: {obj.get('rollback_min_trades')}",
        f"- trades_seen: {obj.get('trades_seen')}",
        f"- pnl_seen_usd: {obj.get('pnl_seen_usd')}",
        "",
    ]
    if obj.get("applied_changes"):
        lines.append("## Applied Changes")
        lines.append("")
        for x in obj["applied_changes"]:
            lines.append(f"- {x}")
        lines.append("")
    if obj.get("notes"):
        lines.append("## Notes")
        lines.append("")
        for n in obj["notes"]:
            lines.append(f"- {n}")
        lines.append("")
    path_md.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    load_dotenv(dotenv_path=str(BASE / ".env"), override=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--monitor-minutes", type=float, default=float(os.getenv("MEME_ZONE_ROLLOUT_MONITOR_MIN", "20") or 20))
    ap.add_argument("--rollback-max-loss-usd", type=float, default=float(os.getenv("MEME_ZONE_ROLLOUT_MAX_LOSS_USD", "3.0") or 3.0))
    ap.add_argument("--rollback-min-trades", type=int, default=int(os.getenv("MEME_ZONE_ROLLOUT_MIN_TRADES", "4") or 4))
    args = ap.parse_args()

    ts = int(time.time())
    ROLLOUT_DIR.mkdir(parents=True, exist_ok=True)
    backup_path = ROLLOUT_DIR / f"zone_rollout_{ts}.env.bak"
    report_json = ROLLOUT_DIR / "zone_rollout_latest.json"
    report_md = ROLLOUT_DIR / "zone_rollout_latest.md"
    backup_path.write_text(ENV_PATH.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")

    changes = {
        "MEME_WINNER_ZONE_ENABLED": "1",
        "MEME_WINNER_ZONE_ENFORCE": str(os.getenv("MEME_ZONE_ROLLOUT_ENFORCE", "1")),
    }
    applied = _set_env_values(changes)
    _restart_main_bot()
    time.sleep(4)
    rid = _tail_run_id()

    start = time.time()
    deadline = start + max(1.0, float(args.monitor_minutes)) * 60.0
    status = "active"
    notes: list[str] = []
    trades_seen = 0
    pnl_seen = 0.0

    while time.time() < deadline:
        n, pnl = _run_pnl_for_run_id(rid)
        trades_seen, pnl_seen = n, pnl
        if n >= int(args.rollback_min_trades) and pnl <= -abs(float(args.rollback_max_loss_usd)):
            # rollback
            _write_env_lines(backup_path.read_text(encoding="utf-8", errors="ignore").splitlines())
            _restart_main_bot()
            status = "rolled_back"
            notes.append("rollback_guard_triggered")
            break
        time.sleep(15)

    if status == "active":
        status = "completed"
        notes.append("monitor_window_completed_without_rollback")

    obj = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(start)),
        "status": status,
        "rollout_run_id": rid,
        "monitor_minutes": float(args.monitor_minutes),
        "rollback_max_loss_usd": float(args.rollback_max_loss_usd),
        "rollback_min_trades": int(args.rollback_min_trades),
        "trades_seen": int(trades_seen),
        "pnl_seen_usd": float(pnl_seen),
        "backup_env": str(backup_path),
        "applied_changes": [f"{k}: {old or '(new)'} -> {new}" for (k, old, new) in applied],
        "notes": notes,
    }
    _write_report(report_json, report_md, obj)
    print(f"wrote {report_json}")
    print(f"wrote {report_md}")
    print(f"status={status} run_id={rid} trades={trades_seen} pnl={pnl_seen:+.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
