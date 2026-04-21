#!/usr/bin/env python3
"""Run three continuation-paper lanes in parallel with isolated DBs/logs."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import subprocess
import time
from pathlib import Path

from dotenv import load_dotenv

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
PYTHON = "/opt/homebrew/bin/python3"
META = BASE / "data" / "meme_continuation_tri_runner.json"
DEFAULT_PROFILE = BASE / "config" / "meme_continuation_tri_profile.json"


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _load_meta() -> dict:
    if not META.exists():
        return {}
    try:
        return json.loads(META.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_meta(obj: dict) -> None:
    META.parent.mkdir(parents=True, exist_ok=True)
    META.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def _resolve_profile(base_env: dict[str, str]) -> dict:
    raw = str(base_env.get("MEME_CONTINUATION_TRI_PROFILE_FILE") or "").strip()
    path = Path(raw) if raw else DEFAULT_PROFILE
    if not path.is_absolute():
        path = BASE / path
    if not path.exists():
        raise FileNotFoundError(f"missing profile: {path}")
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError(f"invalid profile: {path}")
    obj["_path"] = str(path)
    return obj


def _apply_profile(env: dict[str, str], profile: dict, lane: str) -> None:
    common = profile.get("common")
    if isinstance(common, dict):
        for k, v in common.items():
            env[str(k)] = str(v)
    lane_cfg = ((profile.get("lanes") or {}) if isinstance(profile.get("lanes"), dict) else {}).get(lane)
    if isinstance(lane_cfg, dict):
        for k, v in lane_cfg.items():
            env[str(k)] = str(v)


def _lane_names(profile: dict) -> list[str]:
    lanes = profile.get("lanes") if isinstance(profile.get("lanes"), dict) else {}
    names = [str(name).strip() for name in lanes.keys() if str(name).strip()]
    return names or ["early", "core", "late"]


def _spawn_lane(name: str, run_id: str, db_rel: str, debug_rel: str, base_env: dict[str, str], profile: dict) -> int:
    env = dict(base_env)
    _apply_profile(env, profile, name)
    env["MEME_PAPER_MODE"] = "true"
    env["MEME_DISCORD_ALERTS"] = "false"
    env["MEME_RUN_ID"] = run_id
    env["MEME_ENV_FILE"] = ""
    env["MEME_POSITIONS_DB"] = db_rel
    env["MEME_SIGNAL_DEBUG"] = env.get("MEME_SIGNAL_DEBUG", "1")
    env["MEME_SIGNAL_DEBUG_FILE"] = debug_rel
    env["MEME_RESTORE_OPEN_POSITIONS"] = env.get("MEME_CONT_TRI_RESTORE_OPEN_POSITIONS", "0")
    env["MEME_LAUNCH_SIGNAL_IGNORE_HISTORY"] = env.get("MEME_CONT_TRI_IGNORE_HISTORY", "0")
    env["MEME_SIGNAL_MAX_CANDIDATES_PER_TICK"] = env.get("MEME_CONT_TRI_MAX_CANDIDATES_PER_TICK", "2")
    env["MEME_JUPITER_MAX_CALLS_PER_MIN"] = env.get("MEME_CONT_TRI_JUP_MAX_CALLS_PER_MIN", "6")
    env["MEME_JUPITER_RESERVED_FOR_POSITIONS"] = env.get("MEME_CONT_TRI_JUP_RESERVED_POS", "4")

    log_path = BASE / "logs" / f"meme_continuation_{name}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "a", encoding="utf-8")
    proc = subprocess.Popen(
        [PYTHON, "-u", str(BASE / "src" / "meme_bot.py")],
        cwd=str(BASE),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return int(proc.pid)


def _stop_pid(pid: int) -> None:
    if not _alive(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        return
    t0 = time.time()
    while time.time() - t0 < 4.0:
        if not _alive(pid):
            return
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except Exception:
        pass


def _db_summary(db_path: Path, run_id: str) -> dict[str, float | int]:
    out: dict[str, float | int] = {
        "trade_slices": 0,
        "slice_wins": 0,
        "slice_pnl_usd": 0.0,
        "positions": 0,
        "position_wins": 0,
        "position_pnl_usd": 0.0,
        "open_positions": 0,
    }
    if not db_path.exists():
        return out
    con = sqlite3.connect(str(db_path))
    cur = con.cursor()
    pos_pnl: dict[tuple[str, str], float] = {}
    try:
        cur.execute("SELECT mint, entry_timestamp, side, pnl_usd, metadata FROM trades")
        for mint, entry_timestamp, side, pnl_usd, metadata in cur.fetchall():
            if str(side or "").upper() != "SELL":
                continue
            try:
                md = json.loads(metadata or "{}")
            except Exception:
                md = {}
            if run_id and str((md or {}).get("run_id") or "").strip() != run_id:
                continue
            out["trade_slices"] = int(out["trade_slices"]) + 1
            pnl = float(pnl_usd or 0.0)
            out["slice_pnl_usd"] = float(out["slice_pnl_usd"]) + pnl
            if pnl > 0:
                out["slice_wins"] = int(out["slice_wins"]) + 1
            pos_pnl[(str(mint or ""), str(entry_timestamp or ""))] = float(pos_pnl.get((str(mint or ""), str(entry_timestamp or "")), 0.0)) + pnl
        cur.execute("SELECT metadata FROM positions WHERE status='open'")
        for (metadata,) in cur.fetchall():
            try:
                md = json.loads(metadata or "{}")
            except Exception:
                md = {}
            if run_id and str((md or {}).get("run_id") or "").strip() != run_id:
                continue
            out["open_positions"] = int(out["open_positions"]) + 1
    except sqlite3.OperationalError:
        pass
    finally:
        con.close()
    if pos_pnl:
        out["positions"] = len(pos_pnl)
        out["position_pnl_usd"] = float(sum(pos_pnl.values()))
        out["position_wins"] = int(sum(1 for v in pos_pnl.values() if float(v) > 0.0))
    return out


def cmd_start() -> int:
    load_dotenv(dotenv_path=str(BASE / ".env"), override=True)
    existing = _load_meta()
    lanes = existing.get("lanes", {}) if isinstance(existing.get("lanes"), dict) else {}
    if any(_alive(int((v or {}).get("pid") or 0)) for v in lanes.values() if isinstance(v, dict)):
        print("continuation tri lanes already running; use status or stop first")
        return 0

    base_env = os.environ.copy()
    profile = _resolve_profile(base_env)
    lane_names = _lane_names(profile)
    ts = int(time.time())
    lanes_meta: dict[str, dict[str, str | int]] = {}
    for lane in lane_names:
        run_id = f"cont_{lane}_{ts}"
        db_rel = f"data/positions_cont_{lane}.db"
        debug_rel = f"data/meme_signal_debug_cont_{lane}.jsonl"
        pid = _spawn_lane(lane, run_id, db_rel, debug_rel, base_env, profile)
        lanes_meta[lane] = {
            "pid": pid,
            "run_id": run_id,
            "db": db_rel,
            "log": str(BASE / "logs" / f"meme_continuation_{lane}.log"),
            "debug": debug_rel,
        }
        print(f"started {lane}: pid={pid} run_id={run_id} db={db_rel}")
    _save_meta(
        {
            "started_at": ts,
            "profile_path": profile.get("_path"),
            "lanes": lanes_meta,
        }
    )
    return 0


def cmd_status() -> int:
    meta = _load_meta()
    lanes = meta.get("lanes", {}) if isinstance(meta.get("lanes"), dict) else {}
    if not lanes:
        print("no continuation tri lanes configured")
        return 0
    for lane in sorted(lanes.keys()):
        row = lanes.get(lane) if isinstance(lanes.get(lane), dict) else {}
        pid = int(row.get("pid") or 0)
        run_id = str(row.get("run_id") or "")
        db_rel = str(row.get("db") or "")
        db_path = BASE / db_rel if db_rel else BASE / "data" / f"positions_cont_{lane}.db"
        alive = _alive(pid)
        summ = _db_summary(db_path, run_id)
        positions = int(summ.get("positions") or 0)
        pos_wins = int(summ.get("position_wins") or 0)
        pos_wr = (pos_wins / positions * 100.0) if positions else 0.0
        pos_pnl = float(summ.get("position_pnl_usd") or 0.0)
        open_pos = int(summ.get("open_positions") or 0)
        print(
            f"{lane}: pid={pid} alive={alive} run_id={run_id} "
            f"positions={positions} winrate={pos_wr:.1f}% pnl=${pos_pnl:+.2f} open={open_pos} db={db_rel}"
        )
    return 0


def cmd_stop() -> int:
    meta = _load_meta()
    lanes = meta.get("lanes", {}) if isinstance(meta.get("lanes"), dict) else {}
    if not lanes:
        print("no continuation tri lanes configured")
        return 0
    for lane in sorted(lanes.keys()):
        row = lanes.get(lane) if isinstance(lanes.get(lane), dict) else {}
        pid = int(row.get("pid") or 0)
        if pid > 0:
            _stop_pid(pid)
            print(f"stopped {lane} pid={pid}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["start", "status", "stop"])
    args = ap.parse_args()
    if args.cmd == "start":
        return cmd_start()
    if args.cmd == "status":
        return cmd_status()
    return cmd_stop()


if __name__ == "__main__":
    raise SystemExit(main())
