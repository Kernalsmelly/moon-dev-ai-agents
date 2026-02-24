#!/usr/bin/env python3
"""Quick, single-command status for the meme bot pipeline.

Goal: avoid tailing multiple logs and poking SQLite manually.

This script is intentionally lightweight and read-only.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
LOG_BOT = BASE / "logs" / "meme_bot_early_edge_auto.log"
DB = BASE / "data" / "positions.db"
SIGNAL_DEBUG = BASE / "data" / "meme_signal_debug.jsonl"
RUNNER_META = BASE / "data" / "meme_base_simple_runner.json"


def _load_runner_meta() -> dict:
    try:
        if RUNNER_META.exists():
            import json
            obj = json.loads(RUNNER_META.read_text(encoding="utf-8"))
            return obj if isinstance(obj, dict) else {}
    except Exception:
        pass
    return {}


def _resolve_runtime_paths() -> None:
    global LOG_BOT, DB
    meta = _load_runner_meta()
    log_raw = str(meta.get("log") or "").strip()
    db_raw = str(meta.get("db") or "").strip()
    if log_raw:
        lp = Path(log_raw)
        if lp.exists():
            LOG_BOT = lp
    if db_raw:
        dp = Path(db_raw)
        if not dp.is_absolute():
            dp = BASE / dp
        if dp.exists():
            DB = dp


_resolve_runtime_paths()


def _ago(ts: float) -> str:
    if not ts:
        return "n/a"
    dt = max(0.0, time.time() - ts)
    if dt < 90:
        return f"{dt:.0f}s"
    if dt < 3600:
        return f"{dt/60:.1f}m"
    return f"{dt/3600:.1f}h"


def _tail_last_matching(path: Path, needle: str, max_bytes: int = 256_000) -> str | None:
    if not path.exists():
        return None
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            start = max(0, size - max_bytes)
            fh.seek(start, 0)
            chunk = fh.read()
        text = chunk.decode("utf-8", errors="ignore").splitlines()
        for ln in reversed(text):
            if needle in ln:
                return ln.strip()
    except Exception:
        return None
    return None


def _db_last_write_mtime(db_path: Path) -> float | None:
    """Use the freshest mtime across DB and WAL so status reflects live writes."""
    mtimes: list[float] = []
    try:
        if db_path.exists():
            mtimes.append(float(db_path.stat().st_mtime))
    except Exception:
        pass
    try:
        wal = db_path.with_name(db_path.name + "-wal")
        if wal.exists():
            mtimes.append(float(wal.stat().st_mtime))
    except Exception:
        pass
    return max(mtimes) if mtimes else None


def _last_run_id() -> str | None:
    try:
        meta = _load_runner_meta()
        rid = str(meta.get("run_id") or "").strip()
        if rid:
            return rid
    except Exception:
        pass
    ln = _tail_last_matching(LOG_BOT, "run_id=")
    if not ln:
        return None
    # Example: "config ... run_id=run_123"
    try:
        parts = ln.split("run_id=", 1)
        if len(parts) != 2:
            return None
        rid = parts[1].strip().split()[0].strip()
        return rid or None
    except Exception:
        return None

def _print_proc_status() -> None:
    patterns = [
        "scripts/meme_pipeline_supervisor.py",
        "/src/meme_bot.py",
        "scripts/signal_outcome_recorder.py",
        "scripts/meme_pipeline_health.py",
        "scripts/meme_edge_reporter.py",
        "scripts/meme_edge_decider.py",
        "scripts/meme_hourly_discord_summary.py",
        "scripts/pump_ws_signal_listener.py",
        "scripts/raydium_pool_ws_listener.py",
    ]
    try:
        out = subprocess.check_output(["ps", "-axo", "pid=,etime=,command="], text=True)
    except Exception as e:
        print(f"processes: unavailable ({e})")
        return
    rows: list[str] = []
    for ln in out.splitlines():
        s = ln.strip()
        if not s:
            continue
        if any(p in s for p in patterns):
            rows.append(s)
    print("processes:")
    if not rows:
        print("  (none)")
        return
    for r in rows[:40]:
        print(f"  {r}")


def _proc_cwd(pid: int) -> str:
    try:
        out = subprocess.check_output(
            ["lsof", "-a", "-p", str(int(pid)), "-d", "cwd", "-Fn"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return ""
    for ln in out.splitlines():
        if ln.startswith("n"):
            return ln[1:].strip()
    return ""


def _script_name_from_cmd(cmd: str) -> str:
    parts = (cmd or "").split()
    for tok in reversed(parts):
        if tok.endswith(".py"):
            return Path(tok).name
    return ""


def _print_pipeline_topology_audit() -> None:
    """Compare expected supervisor children vs actual repo-local python processes."""
    try:
        out = subprocess.check_output(["ps", "-Ao", "pid=,ppid=,command="], text=True)
    except Exception as e:
        print(f"pipeline_topology: unavailable ({e})")
        return

    repo = str(BASE)
    py_rows: list[dict] = []
    for ln in out.splitlines():
        s = ln.strip()
        if not s:
            continue
        parts = s.split(maxsplit=2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except Exception:
            continue
        cmd = parts[2]
        if ("python" not in cmd.lower()) and ("Python" not in cmd):
            continue
        cwd = _proc_cwd(pid)
        if cwd != repo:
            continue
        py_rows.append(
            {
                "pid": pid,
                "ppid": ppid,
                "cmd": cmd,
                "script": _script_name_from_cmd(cmd),
            }
        )

    supervisor_pids = {
        int(r["pid"])
        for r in py_rows
        if "meme_pipeline_supervisor.py" in str(r.get("cmd") or "")
    }

    expected_scripts: set[str] = set()
    expected_err = ""
    try:
        if repo not in sys.path:
            sys.path.insert(0, repo)
        import scripts.meme_pipeline_supervisor as _sup

        env = _sup.build_env()
        specs = _sup.build_specs(env)
        expected_scripts = {Path(s.cmd[-1]).name for s in specs}
    except Exception as e:
        expected_err = str(e)

    runner_meta = _load_runner_meta()
    runner_pid = int(runner_meta.get("pid") or 0)

    running_expected_by_supervisor: set[str] = set()
    expected_child_pids: set[int] = set()
    orphan_expected: list[dict] = []
    extra_repo_procs: list[dict] = []
    manual_allowed: list[dict] = []
    worker_allowed: list[dict] = []

    for row in py_rows:
        script = str(row.get("script") or "")
        pid = int(row.get("pid") or 0)
        ppid = int(row.get("ppid") or 0)
        cmd = str(row.get("cmd") or "")

        if "meme_pipeline_supervisor.py" in cmd:
            continue
        if script in expected_scripts:
            if ppid in supervisor_pids:
                running_expected_by_supervisor.add(script)
                expected_child_pids.add(pid)
            else:
                orphan_expected.append(row)
            continue

        # Base-simple bot is intentionally outside supervisor when
        # MEME_SUPERVISOR_ENABLE_BOT=0 and tracked in runner metadata.
        if script == "meme_bot.py" and pid == runner_pid:
            manual_allowed.append(row)
            continue

        # Some managed children run one-off helper scripts as subprocesses.
        if ppid in expected_child_pids:
            worker_allowed.append(row)
            continue

        # Ignore this script itself when invoked as `python - ...`.
        if script in ("", "meme_live_status.py"):
            continue
        extra_repo_procs.append(row)

    missing_expected = sorted(expected_scripts - running_expected_by_supervisor)
    orphan_expected.sort(key=lambda x: int(x.get("pid") or 0))
    extra_repo_procs.sort(key=lambda x: int(x.get("pid") or 0))

    print("pipeline_topology:")
    print(
        "  "
        f"supervisors={len(supervisor_pids)} "
        f"expected_children={len(expected_scripts)} "
        f"managed_running={len(running_expected_by_supervisor)} "
        f"missing={len(missing_expected)} "
        f"orphaned={len(orphan_expected)} "
        f"extra={len(extra_repo_procs)} "
        f"workers={len(worker_allowed)}"
    )
    if expected_err:
        print(f"  expected_build_error={expected_err}")
    if manual_allowed:
        print(
            "  manual_allowed="
            + ", ".join(
                f"{str(r.get('script') or 'unknown')}@pid={int(r.get('pid') or 0)}"
                for r in manual_allowed
            )
        )
    if missing_expected:
        print("  missing_children=" + ",".join(missing_expected))
    if orphan_expected:
        for r in orphan_expected[:8]:
            print(
                "  orphan_child="
                f"{str(r.get('script') or 'unknown')} "
                f"pid={int(r.get('pid') or 0)} "
                f"ppid={int(r.get('ppid') or 0)}"
            )
    if extra_repo_procs:
        for r in extra_repo_procs[:8]:
            print(
                "  extra_proc="
                f"{str(r.get('script') or 'unknown')} "
                f"pid={int(r.get('pid') or 0)} "
                f"ppid={int(r.get('ppid') or 0)}"
            )


@dataclass
class DbStats:
    open_positions: int = 0
    stale_positions: int = 0
    closed_positions: int = 0
    trades_total: int = 0


def _db_stats() -> DbStats | None:
    if not DB.exists():
        return None
    try:
        con = sqlite3.connect(str(DB))
        cur = con.cursor()
        s = DbStats()
        for status in ("open", "stale", "closed"):
            cur.execute("SELECT COUNT(*) FROM positions WHERE status=?", (status,))
            n = int(cur.fetchone()[0] or 0)
            if status == "open":
                s.open_positions = n
            elif status == "stale":
                s.stale_positions = n
            else:
                s.closed_positions = n
        cur.execute("SELECT COUNT(*) FROM trades")
        s.trades_total = int(cur.fetchone()[0] or 0)
        con.close()
        return s
    except Exception:
        return None


def _recent_trades(limit: int) -> list[dict]:
    if not DB.exists():
        return []
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    try:
        cur.execute(
            "SELECT trade_id, symbol, side, pnl_usd, pnl_pct, exit_reason, entry_timestamp, exit_timestamp, amount_usd "
            "FROM trades ORDER BY created_at DESC LIMIT ?",
            (int(limit),),
        )
        rows = [dict(r) for r in cur.fetchall()]
        return rows
    finally:
        con.close()

def _parse_created_at(s: str) -> float | None:
    # Stored as "YYYY-MM-DD HH:MM:SS" (local time). Treat as local.
    try:
        dt = _dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        return dt.timestamp()
    except Exception:
        return None


def _parse_iso8601(s: str) -> float | None:
    if not s:
        return None
    try:
        return _dt.datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def _window_trades(hours: float) -> list[dict]:
    if not DB.exists():
        return []
    cutoff = time.time() - (float(hours) * 3600.0)
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    try:
        cur.execute(
            "SELECT created_at, symbol, pnl_usd, pnl_pct, exit_reason, amount_usd "
            "FROM trades ORDER BY created_at DESC LIMIT 5000"
        )
        out: list[dict] = []
        for r in cur.fetchall():
            d = dict(r)
            ts = _parse_created_at(str(d.get("created_at") or ""))
            if ts is None:
                continue
            if ts < cutoff:
                break
            out.append(d)
        return out
    finally:
        con.close()

def _window_mcap_sanity(hours: float) -> dict:
    """Basic sanity stats for entry market cap pulled from trade metadata in a time window."""
    if not DB.exists():
        return {}
    cutoff = time.time() - (float(hours) * 3600.0)
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    try:
        cur.execute(
            "SELECT created_at, metadata FROM trades ORDER BY created_at DESC LIMIT 8000"
        )
        total = 0
        unknown = 0
        below_10k = 0
        below_25k = 0
        mcaps: list[float] = []
        for r in cur.fetchall():
            d = dict(r)
            ts = _parse_created_at(str(d.get("created_at") or ""))
            if ts is None:
                continue
            if ts < cutoff:
                break
            md = d.get("metadata") or ""
            try:
                import json as _json
                obj = _json.loads(md) if isinstance(md, str) and md else {}
            except Exception:
                obj = {}
            if not isinstance(obj, dict):
                continue
            if "market_cap_entry" not in obj:
                continue
            total += 1
            try:
                mv = float(obj.get("market_cap_entry") or 0.0)
            except Exception:
                mv = 0.0
            if mv <= 0:
                unknown += 1
                continue
            mcaps.append(mv)
            if mv < 10_000:
                below_10k += 1
            if mv < 25_000:
                below_25k += 1
        out = {
            "trades_with_mcap": total,
            "unknown_mcap": unknown,
            "below_10k": below_10k,
            "below_25k": below_25k,
        }
        if mcaps:
            s = sorted(mcaps)
            out["mcap_min"] = s[0]
            out["mcap_med"] = s[len(s) // 2]
            out["mcap_max"] = s[-1]
        return out
    finally:
        con.close()

def _window_trades_for_run(hours: float, run_id: str) -> list[dict]:
    if not DB.exists():
        return []
    if not run_id:
        return []
    cutoff = time.time() - (float(hours) * 3600.0)
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    try:
        cur.execute(
            "SELECT created_at, exit_timestamp, mint, symbol, pnl_usd, pnl_pct, exit_reason, amount_usd, metadata "
            "FROM trades ORDER BY created_at DESC LIMIT 8000"
        )
        out: list[dict] = []
        for r in cur.fetchall():
            d = dict(r)
            ts = _parse_created_at(str(d.get("created_at") or ""))
            if ts is None:
                continue
            if ts < cutoff:
                break
            md = d.get("metadata") or ""
            try:
                import json as _json
                obj = _json.loads(md) if isinstance(md, str) and md else {}
            except Exception:
                obj = {}
            if not isinstance(obj, dict):
                continue
            if str(obj.get("run_id") or "").strip() != run_id:
                continue
            d["_metadata_obj"] = obj
            out.append(d)
        return out
    finally:
        con.close()


def _cluster_run_rows(rows: list[dict], entry_tol_s: float = 180.0) -> list[dict]:
    by_mint: dict[str, list[dict]] = {}
    xs = sorted(rows, key=lambda r: _parse_iso8601(str(r.get("exit_timestamp") or "")) or 0.0)
    for r in xs:
        mint = str(r.get("mint") or "UNKNOWN_MINT")
        md = r.get("_metadata_obj") if isinstance(r.get("_metadata_obj"), dict) else {}
        exit_ts = _parse_iso8601(str(r.get("exit_timestamp") or "")) or _parse_created_at(str(r.get("created_at") or "")) or 0.0
        hold_s = None
        try:
            hv = md.get("hold_time_sec")
            hold_s = float(hv) if hv is not None else None
        except Exception:
            hold_s = None
        anchor = (exit_ts - hold_s) if (hold_s is not None and hold_s >= 0) else None

        bucket = by_mint.setdefault(mint, [])
        chosen = None
        if anchor is not None:
            best_dist = None
            for c in bucket:
                ca = c.get("anchor")
                if ca is None:
                    continue
                dist = abs(float(ca) - float(anchor))
                if dist <= float(entry_tol_s) and (best_dist is None or dist < best_dist):
                    best_dist = dist
                    chosen = c
        if chosen is None:
            chosen = {
                "mint": mint,
                "symbol": str(r.get("symbol") or mint[:8]),
                "anchor": anchor,
                "legs": 0,
                "pnl": 0.0,
                "reasons": {},
            }
            bucket.append(chosen)
        chosen["legs"] = int(chosen.get("legs") or 0) + 1
        chosen["pnl"] = float(chosen.get("pnl") or 0.0) + float(r.get("pnl_usd") or 0.0)
        rr = str(r.get("exit_reason") or "UNKNOWN")
        reasons = chosen.get("reasons") if isinstance(chosen.get("reasons"), dict) else {}
        reasons[rr] = int(reasons.get(rr) or 0) + 1
        chosen["reasons"] = reasons
    out: list[dict] = []
    for cs in by_mint.values():
        out.extend(cs)
    return out


def _print_run_window_summary(hours: float, run_id: str) -> None:
    xs = _window_trades_for_run(hours, run_id)
    if not xs:
        print(f"run_window_{hours:.2f}h({run_id}): no trades")
        return
    n = len(xs)
    pnl = sum(float(x.get("pnl_usd") or 0.0) for x in xs)
    wins = sum(1 for x in xs if float(x.get("pnl_usd") or 0.0) > 0)
    wr = (wins / n) * 100.0 if n else 0.0
    print(f"run_window_{hours:.2f}h({run_id}): trades={n} winrate={wr:.1f}% pnl=${pnl:+.2f}")

    by_reason: dict[str, dict[str, float]] = {}
    for x in xs:
        r = (x.get("exit_reason") or "UNKNOWN").strip() or "UNKNOWN"
        d = by_reason.setdefault(r, {"cnt": 0.0, "pnl": 0.0})
        d["cnt"] += 1.0
        d["pnl"] += float(x.get("pnl_usd") or 0.0)
    worst = sorted(by_reason.items(), key=lambda kv: kv[1]["pnl"])[:4]
    best = sorted(by_reason.items(), key=lambda kv: kv[1]["pnl"], reverse=True)[:4]
    print("run_worst_reasons:")
    for r, d in worst:
        print(f"  {r:22s} cnt={int(d['cnt']):4d} pnl=${d['pnl']:+.2f}")
    print("run_best_reasons:")
    for r, d in best:
        print(f"  {r:22s} cnt={int(d['cnt']):4d} pnl=${d['pnl']:+.2f}")

    clusters = _cluster_run_rows(xs, entry_tol_s=180.0)
    if clusters:
        cn = len(clusters)
        cw = sum(1 for c in clusters if float(c.get("pnl") or 0.0) > 0)
        cwr = (cw / cn) * 100.0 if cn else 0.0
        avg_legs = (sum(int(c.get("legs") or 0) for c in clusters) / cn) if cn else 0.0
        dom_leg_share = 0.0
        dom_abs_pnl_share = 0.0
        try:
            dom = max(clusters, key=lambda c: abs(float(c.get("pnl") or 0.0)))
            total_legs = sum(int(c.get("legs") or 0) for c in clusters)
            total_abs_pnl = sum(abs(float(c.get("pnl") or 0.0)) for c in clusters)
            if total_legs > 0:
                dom_leg_share = float(dom.get("legs") or 0) / float(total_legs)
            if total_abs_pnl > 0:
                dom_abs_pnl_share = abs(float(dom.get("pnl") or 0.0)) / float(total_abs_pnl)
        except Exception:
            pass
        print(
            "run_clusters:"
            f" n={cn} wr={cwr:.1f}% avg_legs={avg_legs:.2f}"
            f" dom_leg_share={dom_leg_share:.1%} dom_abs_pnl_share={dom_abs_pnl_share:.1%}"
        )
        cworst = sorted(clusters, key=lambda c: float(c.get("pnl") or 0.0))[:2]
        cbest = sorted(clusters, key=lambda c: float(c.get("pnl") or 0.0), reverse=True)[:2]
        print("run_cluster_worst:")
        for c in cworst:
            reasons = ",".join(sorted((c.get("reasons") or {}).keys()))
            print(
                f"  {str(c.get('symbol') or ''):16s} legs={int(c.get('legs') or 0):3d} "
                f"pnl=${float(c.get('pnl') or 0.0):+6.2f} reasons={reasons}"
            )
        print("run_cluster_best:")
        for c in cbest:
            reasons = ",".join(sorted((c.get("reasons") or {}).keys()))
            print(
                f"  {str(c.get('symbol') or ''):16s} legs={int(c.get('legs') or 0):3d} "
                f"pnl=${float(c.get('pnl') or 0.0):+6.2f} reasons={reasons}"
            )


def _read_tail_text(path: Path, max_bytes: int = 1_200_000) -> str:
    if not path.exists():
        return ""
    try:
        data = path.read_bytes()
        if len(data) > max_bytes:
            data = data[-max_bytes:]
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _run_reject_counts(hours: float, run_id: str, top_k: int = 8) -> list[tuple[str, int]]:
    if not run_id:
        return []
    text = _read_tail_text(SIGNAL_DEBUG)
    if not text:
        return []
    cutoff = time.time() - (float(hours) * 3600.0)
    counts: dict[str, int] = {}
    for ln in text.splitlines():
        s = ln.strip()
        if not s or '"kind"' not in s:
            continue
        try:
            import json as _json
            row = _json.loads(s)
        except Exception:
            continue
        try:
            ts = float(row.get("ts") or 0.0)
        except Exception:
            ts = 0.0
        if ts < cutoff:
            continue
        if str(row.get("run_id") or "").strip() != run_id:
            continue
        kind = str(row.get("kind") or "").strip()
        if not kind.startswith("reject_"):
            continue
        counts[kind] = int(counts.get(kind, 0) or 0) + 1
    if not counts:
        return []
    return sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[: max(1, int(top_k))]

def _print_window_summary(hours: float) -> None:
    xs = _window_trades(hours)
    if not xs:
        print(f"window_{hours:.2f}h: no trades")
        return
    n = len(xs)
    pnl = sum(float(x.get("pnl_usd") or 0.0) for x in xs)
    wins = sum(1 for x in xs if float(x.get("pnl_usd") or 0.0) > 0)
    wr = (wins / n) * 100.0 if n else 0.0
    print(f"window_{hours:.2f}h: trades={n} winrate={wr:.1f}% pnl=${pnl:+.2f}")

    by_reason: dict[str, dict[str, float]] = {}
    for x in xs:
        r = (x.get("exit_reason") or "UNKNOWN").strip() or "UNKNOWN"
        d = by_reason.setdefault(r, {"cnt": 0.0, "pnl": 0.0})
        d["cnt"] += 1.0
        d["pnl"] += float(x.get("pnl_usd") or 0.0)

    worst = sorted(by_reason.items(), key=lambda kv: kv[1]["pnl"])[:6]
    best = sorted(by_reason.items(), key=lambda kv: kv[1]["pnl"], reverse=True)[:6]
    print("window_worst_reasons:")
    for r, d in worst:
        print(f"  {r:22s} cnt={int(d['cnt']):4d} pnl=${d['pnl']:+.2f}")
    print("window_best_reasons:")
    for r, d in best:
        print(f"  {r:22s} cnt={int(d['cnt']):4d} pnl=${d['pnl']:+.2f}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", type=int, default=8, help="how many recent trades to show")
    ap.add_argument("--hours", type=float, default=3.0, help="summary window size in hours")
    args = ap.parse_args()

    print(f"now: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    # File freshness
    if LOG_BOT.exists():
        print(f"bot_log: {LOG_BOT.name} updated {_ago(LOG_BOT.stat().st_mtime)} ago")
    else:
        print(f"bot_log: missing ({LOG_BOT})")
    db_mtime = _db_last_write_mtime(DB)
    if db_mtime is not None:
        print(f"positions_db: {DB.name} updated {_ago(db_mtime)} ago")
    else:
        print(f"positions_db: missing ({DB})")

    # Bot summary line
    sess = _tail_last_matching(LOG_BOT, "Session:")
    if sess:
        print(f"bot_session: {sess}")

    _print_window_summary(float(args.hours))
    sanity = _window_mcap_sanity(float(args.hours))
    if sanity and sanity.get("trades_with_mcap"):
        mm = ""
        if all(k in sanity for k in ("mcap_min", "mcap_med", "mcap_max")):
            mm = f" mcap_min/med/max=${sanity['mcap_min']:.0f}/${sanity['mcap_med']:.0f}/${sanity['mcap_max']:.0f}"
        print(
            "window_mcap_sanity:"
            f" trades_with_mcap={sanity['trades_with_mcap']}"
            f" unknown={sanity['unknown_mcap']}"
            f" below10k={sanity['below_10k']}"
            f" below25k={sanity['below_25k']}"
            f"{mm}"
        )
    rid = _last_run_id()
    if rid:
        _print_run_window_summary(float(args.hours), rid)
        rj = _run_reject_counts(float(args.hours), rid, top_k=8)
        if rj:
            print("run_rejects:")
            for kind, n in rj:
                print(f"  {kind:22s} n={n:4d}")

    # DB stats + trades
    s = _db_stats()
    if s:
        print(
            f"db: positions open={s.open_positions} stale={s.stale_positions} closed={s.closed_positions} trades={s.trades_total}"
        )
    trades = _recent_trades(args.trades)
    if trades:
        print("recent_trades:")
        for t in trades:
            sym = (t.get("symbol") or "").strip() or (t.get("trade_id") or "")
            pnl = float(t.get("pnl_usd") or 0.0)
            pnlp = float(t.get("pnl_pct") or 0.0)
            reason = (t.get("exit_reason") or "").strip()
            amt = float(t.get("amount_usd") or 0.0)
            print(f"  {sym:14s} pnl=${pnl:+.2f} ({pnlp:+.2f}%) size=${amt:.2f} reason={reason}")

    _print_proc_status()
    _print_pipeline_topology_audit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
