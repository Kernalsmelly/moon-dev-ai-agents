#!/usr/bin/env python3
"""Run a single base meme lane (no match/bypass lanes)."""

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
META = BASE / "data" / "meme_base_simple_runner.json"
LOG = BASE / "logs" / "meme_base_simple.log"
PROFILE_DEFAULT = BASE / "config" / "meme_ab_tri_profile.json"


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


def _load_profile(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _apply_profile(env: dict[str, str], profile: dict, lane: str = "base") -> None:
    common = profile.get("common")
    if isinstance(common, dict):
        for k, v in common.items():
            env[str(k)] = str(v)
    lane_overrides = ((profile.get("lanes") or {}) if isinstance(profile.get("lanes"), dict) else {}).get(lane)
    if isinstance(lane_overrides, dict):
        for k, v in lane_overrides.items():
            env[str(k)] = str(v)


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
        "slices": 0,
        "slice_wins": 0,
        "slice_pnl_usd": 0.0,
        "positions": 0,
        "position_wins": 0,
        "position_pnl_usd": 0.0,
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
            rid = str((md or {}).get("run_id") or "").strip()
            if run_id and rid != run_id:
                continue
            out["slices"] = int(out["slices"]) + 1
            p = float(pnl_usd or 0.0)
            out["slice_pnl_usd"] = float(out["slice_pnl_usd"]) + p
            if p > 0:
                out["slice_wins"] = int(out["slice_wins"]) + 1
            key = (str(mint or ""), str(entry_timestamp or ""))
            pos_pnl[key] = float(pos_pnl.get(key, 0.0)) + p
    except sqlite3.OperationalError:
        pass
    finally:
        con.close()

    out["positions"] = int(len(pos_pnl))
    out["position_pnl_usd"] = float(sum(pos_pnl.values())) if pos_pnl else 0.0
    out["position_wins"] = int(sum(1 for v in pos_pnl.values() if v > 0))
    return out


def cmd_start() -> int:
    load_dotenv(dotenv_path=str(BASE / ".env"), override=True)
    st = _load_meta()
    pid = int(st.get("pid") or 0)
    if _alive(pid):
        print(f"base simple already running pid={pid} run_id={st.get('run_id')}")
        return 0

    env = os.environ.copy()
    profile_path_raw = str(env.get("MEME_AB_TRI_PROFILE_FILE") or "").strip()
    profile_path = Path(profile_path_raw) if profile_path_raw else PROFILE_DEFAULT
    if not profile_path.is_absolute():
        profile_path = BASE / profile_path
    profile = _load_profile(profile_path)
    if profile:
        _apply_profile(env, profile, "base")

    ts = int(time.time())
    run_id = f"base_simple_{ts}"
    db_rel = env.get("MEME_BASE_SIMPLE_DB", "data/positions_base_simple.db")
    env["MEME_RUN_ID"] = run_id
    env["MEME_POSITIONS_DB"] = db_rel
    env["MEME_PAPER_MODE"] = "true"
    env["MEME_DISCORD_ALERTS"] = "false"

    LOG.parent.mkdir(parents=True, exist_ok=True)
    log = open(LOG, "a", encoding="utf-8")
    p = subprocess.Popen(
        [PYTHON, "-u", str(BASE / "src" / "meme_bot.py")],
        cwd=str(BASE),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    meta = {
        "pid": int(p.pid),
        "run_id": run_id,
        "db": db_rel,
        "log": str(LOG),
        "started_at": ts,
        "profile": str(profile_path),
    }
    _save_meta(meta)
    print(f"started base simple pid={p.pid} run_id={run_id} db={db_rel}")
    return 0


def cmd_status() -> int:
    st = _load_meta()
    if not st:
        print("no base simple run configured")
        return 0
    pid = int(st.get("pid") or 0)
    run_id = str(st.get("run_id") or "")
    db_rel = str(st.get("db") or "data/positions_base_simple.db")
    db_path = BASE / db_rel if not Path(db_rel).is_absolute() else Path(db_rel)
    alive = _alive(pid)
    s = _db_summary(db_path, run_id)
    slices = int(s.get("slices") or 0)
    sw = int(s.get("slice_wins") or 0)
    spnl = float(s.get("slice_pnl_usd") or 0.0)
    positions = int(s.get("positions") or 0)
    pw = int(s.get("position_wins") or 0)
    ppnl = float(s.get("position_pnl_usd") or 0.0)
    swr = (sw / slices * 100.0) if slices else 0.0
    pwr = (pw / positions * 100.0) if positions else 0.0
    print(
        f"base_simple: pid={pid} alive={alive} run_id={run_id} "
        f"positions={positions} pos_winrate={pwr:.1f}% pos_pnl=${ppnl:+.2f} "
        f"slices={slices} slice_winrate={swr:.1f}% slice_pnl=${spnl:+.2f}"
    )
    return 0


def cmd_stop() -> int:
    st = _load_meta()
    if not st:
        print("no base simple run configured")
        return 0
    pid = int(st.get("pid") or 0)
    if pid > 0:
        _stop_pid(pid)
        print(f"stopped base simple pid={pid}")
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
