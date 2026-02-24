#!/usr/bin/env python3
"""Lightweight health monitor for the meme pipeline.

Emits a heartbeat line every interval with last signal timestamp and last bot entry.
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE = "/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents"
load_dotenv(dotenv_path=os.path.join(BASE, ".env"), override=True)
SIGNALS = os.getenv("MEME_LAUNCH_SIGNALS_FILE", f"{BASE}/data/meme_launch_signals.jsonl")
if BASE not in sys.path:
    sys.path.insert(0, BASE)
from src.meme_signal_schema import resolve_active_run_id

PUMP_WS_LOG = f"{BASE}/logs/pump_ws_signal_listener.log"
OUTCOMES = os.getenv("SIGNAL_OUTCOMES_FILE", f"{BASE}/data/signal_outcomes.jsonl")
DEBUG = os.getenv("MEME_SIGNAL_DEBUG_FILE", f"{BASE}/data/meme_signal_debug.jsonl")
RUNNER_META = f"{BASE}/data/meme_base_simple_runner.json"
DB_PATH = os.getenv("POSITION_DB", f"{BASE}/data/positions.db")

POLL = int(os.getenv("MEME_HEALTH_POLL", "30"))
TRADE_LOOKBACK_HOURS = float(os.getenv("MEME_HEALTH_TRADE_LOOKBACK_HOURS", "2") or 2)
ALERTS_ENABLED = str(os.getenv("MEME_HEALTH_ALERTS_ENABLED", "0") or "0").strip().lower() in ("1", "true", "yes")
ALERT_COOLDOWN_S = max(60.0, float(os.getenv("MEME_HEALTH_ALERT_COOLDOWN_S", "900") or 900))
MAX_SIGNAL_AGE_S = max(0.0, float(os.getenv("MEME_HEALTH_MAX_SIGNAL_AGE_S", "600") or 600))
MAX_PASS_PREQUOTE_AGE_S = max(0.0, float(os.getenv("MEME_HEALTH_MAX_PASS_PREQUOTE_AGE_S", "900") or 900))
AUTO_RUN_ID = str(os.getenv("MEME_HEALTH_AUTO_RUN_ID", "1") or "1").strip().lower() in ("1", "true", "yes")
SIGNAL_SOURCE_WINDOW_S = max(60.0, float(os.getenv("MEME_HEALTH_SIGNAL_SOURCE_WINDOW_S", "1800") or 1800))
WS_LOGS_DROUGHT_MIN_MSGS = int(os.getenv("MEME_HEALTH_WS_LOGS_DROUGHT_MIN_MSGS", "50") or 50)

RUN_RE = re.compile(r"run_id=([A-Za-z0-9_:-]+)")


def detect_bot_log() -> str:
    env_path = str(os.getenv("MEME_BOT_LOG_FILE", "") or "").strip()
    if env_path and os.path.exists(env_path):
        return env_path

    # Prefer active base-simple runner metadata when available.
    meta = Path(BASE) / "data" / "meme_base_simple_runner.json"
    try:
        if meta.exists():
            obj = json.loads(meta.read_text(encoding="utf-8"))
            path = str((obj or {}).get("log") or "").strip()
            if path and os.path.exists(path):
                return path
    except Exception:
        pass

    candidates = [
        Path(BASE) / "logs" / "meme_base_simple.log",
        Path(BASE) / "logs" / "meme_bot_early_edge_auto.log",
    ]
    existing = [p for p in candidates if p.exists()]
    if not existing:
        return str(candidates[0])
    newest = max(existing, key=lambda p: p.stat().st_mtime)
    return str(newest)


def detect_positions_db() -> str:
    env_db = str(os.getenv("POSITION_DB", "") or "").strip()
    if env_db:
        p = Path(env_db)
        if p.exists():
            return str(p)
        p2 = Path(BASE) / env_db
        if p2.exists():
            return str(p2)
    try:
        meta = Path(RUNNER_META)
        if meta.exists():
            obj = json.loads(meta.read_text(encoding="utf-8"))
            raw = str((obj or {}).get("db") or "").strip()
            if raw:
                p = Path(raw)
                if not p.is_absolute():
                    p = Path(BASE) / raw
                if p.exists():
                    return str(p)
    except Exception:
        pass
    return str(Path(BASE) / "data" / "positions.db")


def tail_lines(path: str, n: int = 10, read_bytes: int = 4096) -> list[str]:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max(1024, int(read_bytes))))
            data = f.read().decode("utf-8", errors="ignore")
        lines = [ln for ln in data.splitlines() if ln.strip()]
        return lines[-n:]
    except Exception:
        return []


def parse_last_run_id(lines: list[str]) -> str | None:
    for line in reversed(lines):
        m = RUN_RE.search(line)
        if m:
            return m.group(1).strip()
    return None


def parse_last_signal_ts(lines: list[str]) -> float | None:
    for line in reversed(lines):
        if "\"ts\"" in line:
            try:
                obj = json.loads(line)
                return float(obj.get("ts"))
            except Exception:
                continue
    return None


def parse_last_entry(lines: list[str]) -> str | None:
    for line in reversed(lines):
        if "ENTRY" in line:
            return line.strip()
    return None


def parse_ws_emit_stats(lines: list[str]) -> tuple[str | None, dict[str, float]]:
    """Extract compact summary and numeric fields from latest 'WS emit_stats' line."""
    for line in reversed(lines):
        if "WS emit_stats" in line:
            # Example:
            # WS emit_stats emitted=1 rate_h=59.7 eval=15 rej_buys=11 rej_net=1 rej_top=2 ...
            try:
                parts = line.strip().split()
                keep = []
                stats: dict[str, float] = {}
                for p in parts:
                    if p.startswith(("emitted=", "rate_h=", "eval=", "rej_buys=", "rej_net=", "rej_top=", "rej_accel=", "ws_msgs=", "tx_calls=")):
                        keep.append(p)
                    if "=" in p:
                        k, v = p.split("=", 1)
                        try:
                            stats[k] = float(v)
                        except Exception:
                            continue
                if keep:
                    return "ws[" + " ".join(keep) + "]", stats
            except Exception:
                return "ws[emit_stats_parse_error]", {}
    return None, {}


def signal_source_counts(signal_lines: list[str], now_ts: float, window_s: float) -> dict[str, int]:
    """Count launch signals by metrics.source over a recent wall-clock window."""
    out: dict[str, int] = {}
    cutoff = now_ts - max(1.0, float(window_s))
    for ln in signal_lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except Exception:
            continue
        ts_raw = obj.get("ts")
        try:
            ts = float(ts_raw or 0.0)
        except Exception:
            ts = 0.0
        if ts <= 0 or ts < cutoff:
            continue
        metrics = obj.get("metrics") if isinstance(obj.get("metrics"), dict) else {}
        src = str(metrics.get("source") or "unknown").strip() or "unknown"
        out[src] = int(out.get(src, 0) or 0) + 1
    return out


def trade_summary(db_path: str, lookback_hours: float) -> str:
    """Return compact summary of trades/pnl over lookback window."""
    try:
        if not os.path.exists(db_path):
            return "trades[db_missing]"
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT pnl_usd FROM trades WHERE created_at >= datetime('now', ?)",
            (f"-{lookback_hours} hours",),
        ).fetchall()
        con.close()
        n = len(rows)
        pnl = sum(float(r["pnl_usd"] or 0.0) for r in rows)
        wins = sum(1 for r in rows if float(r["pnl_usd"] or 0.0) > 0)
        wr = (wins / n * 100.0) if n else 0.0
        return f"trades[{lookback_hours:.0f}h n={n} pnl={pnl:+.2f} wr={wr:.0f}%]"
    except Exception:
        return "trades[err]"


def outcomes_enrichment(outcome_lines: list[str]) -> tuple[int, int, int]:
    """Return (rows, with_metrics, with_marketcap0) for the provided JSONL lines."""
    rows = 0
    with_metrics = 0
    with_mcap = 0
    try:
        for ln in outcome_lines:
            ln = ln.strip()
            if not ln:
                continue
            try:
                obj = json.loads(ln)
            except Exception:
                continue
            rows += 1
            m = obj.get("metrics")
            if isinstance(m, dict) and m:
                with_metrics += 1
            if obj.get("marketcap0") is not None:
                with_mcap += 1
    except Exception:
        pass
    return rows, with_metrics, with_mcap


def debug_stats(debug_lines: list[str], run_id: str | None) -> tuple[float | None, float | None]:
    """Return (last_debug_ts, last_pass_prequote_ts), optionally run-scoped."""
    last_debug_ts: float | None = None
    last_pass_prequote_ts: float | None = None
    try:
        import json
        for ln in debug_lines:
            ln = ln.strip()
            if not ln:
                continue
            try:
                obj = json.loads(ln)
            except Exception:
                continue
            if run_id:
                rid = str(obj.get("run_id") or "").strip()
                if rid != run_id:
                    continue
            try:
                ts = float(obj.get("ts") or 0.0)
            except Exception:
                ts = 0.0
            kind = str(obj.get("kind") or "").strip()
            if ts > 0:
                last_debug_ts = ts
                if kind == "pass_prequote":
                    last_pass_prequote_ts = ts
    except Exception:
        pass
    return last_debug_ts, last_pass_prequote_ts


def send_alert(title: str, description: str, level: str = "warning") -> bool:
    if not ALERTS_ENABLED:
        return False
    try:
        from src.alerts import send_system_alert
        return bool(send_system_alert(title=title, description=description, level=level))
    except Exception:
        return False


def main() -> int:
    last_alert_ts: dict[str, float] = {}

    def should_alert(key: str) -> bool:
        now = time.time()
        last = float(last_alert_ts.get(key) or 0.0)
        if last > 0 and (now - last) < ALERT_COOLDOWN_S:
            return False
        last_alert_ts[key] = now
        return True

    while True:
        db_path = detect_positions_db()
        bot_log = detect_bot_log()
        sig_lines = tail_lines(SIGNALS, 20)
        sig_lines_large = tail_lines(SIGNALS, 2500, read_bytes=2_000_000)
        bot_lines = tail_lines(bot_log, 50)
        ws_lines = tail_lines(PUMP_WS_LOG, 80)
        out_lines = tail_lines(OUTCOMES, 250)
        dbg_lines = tail_lines(DEBUG, 500, read_bytes=262144)
        run_id = resolve_active_run_id(BASE) if AUTO_RUN_ID else None
        if AUTO_RUN_ID and not run_id:
            run_id = parse_last_run_id(tail_lines(bot_log, 120, read_bytes=131072))
        last_sig = parse_last_signal_ts(sig_lines)
        last_entry = parse_last_entry(bot_lines)
        last_dbg, last_pass_prequote = debug_stats(dbg_lines, run_id)
        ws_status, ws_metrics = parse_ws_emit_stats(ws_lines)
        now = time.time()
        source_counts = signal_source_counts(sig_lines_large, now, SIGNAL_SOURCE_WINDOW_S)
        o_rows, o_metrics, o_mcap = outcomes_enrichment(out_lines)
        if last_sig:
            age = now - last_sig
            sig_status = f"last_signal_age_s={int(age)}"
        else:
            sig_status = "last_signal_age_s=unknown"
            age = None
        if last_dbg:
            debug_status = f"last_debug_age_s={int(now - last_dbg)}"
        else:
            debug_status = "last_debug_age_s=unknown"
        if last_pass_prequote:
            pp_age = now - last_pass_prequote
            pass_status = f"last_pass_prequote_age_s={int(pp_age)}"
        else:
            pp_age = None
            pass_status = "last_pass_prequote_age_s=none"
        if last_entry:
            entry_status = f"last_entry_line={last_entry}"
        else:
            entry_status = "last_entry_line=none"
        enrich_status = f"outcomes_tail={o_rows} metrics={o_metrics} marketcap0={o_mcap}"
        trades = trade_summary(db_path, TRADE_LOOKBACK_HOURS)
        ws_status = ws_status or "ws[no_emit_stats]"
        source_window_m = int(SIGNAL_SOURCE_WINDOW_S // 60)
        source_status = (
            f"signal_sources[{source_window_m}m "
            + " ".join(f"{k}={v}" for k, v in sorted(source_counts.items()))
            + "]"
            if source_counts
            else f"signal_sources[{source_window_m}m none]"
        )
        rid_status = f"run_id={run_id}" if run_id else "run_id=unknown"
        bot_status = f"bot_log={os.path.basename(bot_log)}"
        print(
            f"health {rid_status} {sig_status} {debug_status} {pass_status} "
            f"{enrich_status} {ws_status} {source_status} {trades} {bot_status} {entry_status}",
            flush=True,
        )

        if ALERTS_ENABLED:
            if (
                age is not None
                and MAX_SIGNAL_AGE_S > 0
                and age > MAX_SIGNAL_AGE_S
                and should_alert("signal_stall")
            ):
                send_alert(
                    title="Meme Pipeline Signal Stall",
                    description=(
                        f"No new launch signal for {int(age)}s "
                        f"(threshold={int(MAX_SIGNAL_AGE_S)}s, run_id={run_id or 'unknown'})."
                    ),
                    level="warning",
                )

            # Funnel stall: discovery is alive, but nothing has passed prequote for too long.
            if (
                age is not None
                and age <= max(60.0, MAX_SIGNAL_AGE_S * 2.0)
                and MAX_PASS_PREQUOTE_AGE_S > 0
            ):
                if pp_age is None and should_alert("prequote_never_passed"):
                    send_alert(
                        title="Meme Funnel Stall (No Passes)",
                        description=(
                            f"Signals are flowing but no `pass_prequote` seen for run_id={run_id or 'unknown'}. "
                            f"Check prequote gates and quote availability."
                        ),
                        level="warning",
                    )
                elif pp_age is not None and pp_age > MAX_PASS_PREQUOTE_AGE_S and should_alert("prequote_stall"):
                    send_alert(
                        title="Meme Funnel Stall (Prequote)",
                        description=(
                            f"Last `pass_prequote` is {int(pp_age)}s old "
                            f"(threshold={int(MAX_PASS_PREQUOTE_AGE_S)}s, run_id={run_id or 'unknown'})."
                        ),
                        level="warning",
                    )

            # Source-health guard: if listener is seeing WS traffic but recent launch
            # stream has no ws_logs entries, treat it as a potential suppression bug.
            ws_logs_recent = int(source_counts.get("ws_logs", 0) or 0)
            ws_msgs_recent = int(ws_metrics.get("ws_msgs", 0) or 0)
            if (
                ws_msgs_recent >= WS_LOGS_DROUGHT_MIN_MSGS
                and ws_logs_recent == 0
                and should_alert("ws_logs_source_drought")
            ):
                send_alert(
                    title="Meme Source Drought (ws_logs)",
                    description=(
                        "pump_ws listener has websocket traffic but no `ws_logs` launch signals "
                        f"in last {int(SIGNAL_SOURCE_WINDOW_S)}s "
                        f"(ws_msgs={ws_msgs_recent}, run_id={run_id or 'unknown'}). "
                        "Possible source suppression or gate regression."
                    ),
                    level="warning",
                )
        time.sleep(max(5, POLL))


if __name__ == "__main__":
    raise SystemExit(main())
