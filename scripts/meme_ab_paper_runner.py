#!/usr/bin/env python3
"""Run conservative A/B paper bots with isolated run IDs and databases.

Lane A (strict): scout disabled.
Lane B (scout): scout enabled.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
PYTHON = "/opt/homebrew/bin/python3"
META = BASE / "data" / "meme_ab_runner.json"
LOG_STRICT = BASE / "logs" / "meme_ab_strict.log"
LOG_SCOUT = BASE / "logs" / "meme_ab_scout.log"


@dataclass
class Lane:
    name: str
    pid: int
    run_id: str
    db: str
    log: str


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _spawn_lane(name: str, run_id: str, db_rel: str, scout_enabled: bool, base_env: dict[str, str], log_path: Path) -> int:
    env = dict(base_env)
    env["MEME_PAPER_MODE"] = "true"
    env["MEME_DISCORD_ALERTS"] = "false"
    env["MEME_RUN_ID"] = run_id
    env["MEME_POSITIONS_DB"] = db_rel
    env["MEME_SIGNAL_MCAP_SCOUT_ENABLED"] = "true" if scout_enabled else "false"
    # Keep A/B footprint conservative to avoid starving primary pipeline budgets.
    env["MEME_SIGNAL_MAX_CANDIDATES_PER_TICK"] = env.get("MEME_AB_MAX_CANDIDATES_PER_TICK", "2")
    env["MEME_JUPITER_MAX_CALLS_PER_MIN"] = env.get("MEME_AB_JUP_MAX_CALLS_PER_MIN", "8")
    env["MEME_JUPITER_RESERVED_FOR_POSITIONS"] = env.get("MEME_AB_JUP_RESERVED_POS", "4")

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "a", encoding="utf-8")
    p = subprocess.Popen(
        [PYTHON, "-u", str(BASE / "src" / "meme_bot.py")],
        cwd=str(BASE),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return int(p.pid)


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


def _db_summary(db_path: Path, run_id: str) -> dict[str, float | int]:
    out: dict[str, float | int] = {"trades": 0, "wins": 0, "pnl_usd": 0.0}
    if not db_path.exists():
        return out
    con = sqlite3.connect(str(db_path))
    cur = con.cursor()
    try:
        cur.execute("SELECT pnl_usd, metadata FROM trades")
        for pnl_usd, metadata in cur.fetchall():
            try:
                md = json.loads(metadata or "{}")
                rid = str((md or {}).get("run_id") or "").strip()
            except Exception:
                rid = ""
            if run_id and rid != run_id:
                continue
            out["trades"] = int(out["trades"]) + 1
            p = float(pnl_usd or 0.0)
            out["pnl_usd"] = float(out["pnl_usd"]) + p
            if p > 0:
                out["wins"] = int(out["wins"]) + 1
    finally:
        con.close()
    return out


def cmd_start() -> int:
    load_dotenv(dotenv_path=str(BASE / ".env"), override=True)
    st = _load_meta()
    lanes = st.get("lanes", {}) if isinstance(st.get("lanes"), dict) else {}
    if lanes:
        alive_any = False
        for _, v in lanes.items():
            try:
                if _alive(int(v.get("pid") or 0)):
                    alive_any = True
            except Exception:
                pass
        if alive_any:
            print("A/B lanes already running; use `status` or `stop` first.")
            return 0

    ts = int(time.time())
    strict_run = f"ab_strict_{ts}"
    scout_run = f"ab_scout_{ts}"
    strict_db = "data/positions_ab_strict.db"
    scout_db = "data/positions_ab_scout.db"
    env = os.environ.copy()

    strict_pid = _spawn_lane("strict", strict_run, strict_db, False, env, LOG_STRICT)
    scout_pid = _spawn_lane("scout", scout_run, scout_db, True, env, LOG_SCOUT)

    meta = {
        "started_at": ts,
        "lanes": {
            "strict": {"pid": strict_pid, "run_id": strict_run, "db": strict_db, "log": str(LOG_STRICT)},
            "scout": {"pid": scout_pid, "run_id": scout_run, "db": scout_db, "log": str(LOG_SCOUT)},
        },
    }
    _save_meta(meta)
    print(f"started strict pid={strict_pid} run_id={strict_run} db={strict_db}")
    print(f"started scout  pid={scout_pid} run_id={scout_run} db={scout_db}")
    return 0


def cmd_status() -> int:
    st = _load_meta()
    lanes = st.get("lanes", {}) if isinstance(st.get("lanes"), dict) else {}
    if not lanes:
        print("no ab lanes configured")
        return 0
    for name in ("strict", "scout"):
        v = lanes.get(name) if isinstance(lanes.get(name), dict) else {}
        pid = int(v.get("pid") or 0)
        run_id = str(v.get("run_id") or "")
        db_rel = str(v.get("db") or "")
        db_path = BASE / db_rel if db_rel and not Path(db_rel).is_absolute() else Path(db_rel or "")
        alive = _alive(pid)
        summ = _db_summary(db_path, run_id) if db_path else {"trades": 0, "wins": 0, "pnl_usd": 0.0}
        trades = int(summ.get("trades") or 0)
        wins = int(summ.get("wins") or 0)
        pnl = float(summ.get("pnl_usd") or 0.0)
        wr = (wins / trades * 100.0) if trades > 0 else 0.0
        print(
            f"{name}: pid={pid} alive={alive} run_id={run_id} "
            f"trades={trades} winrate={wr:.1f}% pnl=${pnl:+.2f} db={db_rel}"
        )
    return 0


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


def cmd_stop() -> int:
    st = _load_meta()
    lanes = st.get("lanes", {}) if isinstance(st.get("lanes"), dict) else {}
    if not lanes:
        print("no ab lanes configured")
        return 0
    for name in ("strict", "scout"):
        v = lanes.get(name) if isinstance(lanes.get(name), dict) else {}
        pid = int(v.get("pid") or 0)
        if pid > 0:
            _stop_pid(pid)
            print(f"stopped {name} pid={pid}")
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
