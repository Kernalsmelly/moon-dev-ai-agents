#!/usr/bin/env python3
"""Run baseline vs winner-zone paper bots in parallel (isolated DBs)."""

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
META = BASE / "data" / "meme_ab_zone_runner.json"
LOG_BASE = BASE / "logs" / "meme_ab_zone_base.log"
LOG_ZONE = BASE / "logs" / "meme_ab_zone_enabled.log"


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


def _spawn_lane(name: str, run_id: str, db_rel: str, zone_enabled: bool, base_env: dict[str, str], log_path: Path) -> int:
    env = dict(base_env)
    env["MEME_PAPER_MODE"] = "true"
    env["MEME_DISCORD_ALERTS"] = "false"
    env["MEME_RUN_ID"] = run_id
    env["MEME_POSITIONS_DB"] = db_rel
    env["MEME_WINNER_ZONE_ENABLED"] = "1" if zone_enabled else "0"
    env["MEME_WINNER_ZONE_BLOCK_WHEN_MISSING"] = env.get("MEME_AB_ZONE_BLOCK_WHEN_MISSING", "0")
    env["MEME_WINNER_ZONE_MATCH_ALLOW_UNKNOWN_MCAP"] = (
        env.get("MEME_AB_ZONE_MATCH_ALLOW_UNKNOWN_MCAP", "1") if zone_enabled else "0"
    )
    env["MEME_WINNER_ZONE_BYPASS_ENABLED"] = (
        env.get("MEME_AB_ZONE_BYPASS_ENABLED", "1") if zone_enabled else "0"
    )
    env["MEME_WINNER_ZONE_BYPASS_MIN_SIGNAL_SCORE"] = env.get("MEME_AB_ZONE_BYPASS_MIN_SIGNAL_SCORE", "78")
    env["MEME_WINNER_ZONE_BYPASS_MIN_HITS"] = env.get("MEME_AB_ZONE_BYPASS_MIN_HITS", "6")
    env["MEME_WINNER_ZONE_BYPASS_MIN_UNIQUE_BUYERS"] = env.get("MEME_AB_ZONE_BYPASS_MIN_UNIQUE_BUYERS", "4")
    env["MEME_WINNER_ZONE_BYPASS_MIN_NET_SOL_IN"] = env.get("MEME_AB_ZONE_BYPASS_MIN_NET_SOL_IN", "2.5")
    env["MEME_WINNER_ZONE_BYPASS_MIN_MCAP_USD"] = env.get("MEME_AB_ZONE_BYPASS_MIN_MCAP_USD", "12000")
    env["MEME_WINNER_ZONE_BYPASS_ALLOW_UNKNOWN_MCAP"] = env.get("MEME_AB_ZONE_BYPASS_ALLOW_UNKNOWN_MCAP", "1")
    env["MEME_WINNER_ZONE_BYPASS_MAX_TOP_BUYER_SHARE"] = env.get("MEME_AB_ZONE_BYPASS_MAX_TOP_BUYER_SHARE", "0.45")
    # Keep per-lane signal debug on so we can compare reject mix before enough closed trades exist.
    env["MEME_SIGNAL_DEBUG"] = env.get("MEME_AB_ZONE_SIGNAL_DEBUG", "1")
    env["MEME_SIGNAL_DEBUG_MAX_PER_MIN"] = env.get("MEME_AB_ZONE_SIGNAL_DEBUG_MAX_PER_MIN", "20")
    # Keep A/B API load lightweight.
    env["MEME_SIGNAL_MAX_CANDIDATES_PER_TICK"] = env.get("MEME_AB_ZONE_MAX_CANDIDATES_PER_TICK", "2")
    env["MEME_JUPITER_MAX_CALLS_PER_MIN"] = env.get("MEME_AB_ZONE_JUP_MAX_CALLS_PER_MIN", "8")
    env["MEME_JUPITER_RESERVED_FOR_POSITIONS"] = env.get("MEME_AB_ZONE_JUP_RESERVED_POS", "4")
    # Let A/B runs loosen/tighten prequote demand independently from main lane.
    prequote_overrides = {
        "MEME_SIGNAL_PREQUOTE_MIN_SIGNAL_SCORE": "MEME_AB_ZONE_PREQUOTE_MIN_SIGNAL_SCORE",
        "MEME_SIGNAL_PREQUOTE_MIN_HITS": "MEME_AB_ZONE_PREQUOTE_MIN_HITS",
        "MEME_SIGNAL_PREQUOTE_MIN_BUYS": "MEME_AB_ZONE_PREQUOTE_MIN_BUYS",
        "MEME_SIGNAL_PREQUOTE_MIN_UNIQUE_BUYERS": "MEME_AB_ZONE_PREQUOTE_MIN_UNIQUE_BUYERS",
        "MEME_SIGNAL_PREQUOTE_MIN_NET_SOL_IN": "MEME_AB_ZONE_PREQUOTE_MIN_NET_SOL_IN",
        "MEME_SIGNAL_PREQUOTE_MIN_MCAP_USD": "MEME_AB_ZONE_PREQUOTE_MIN_MCAP_USD",
        "MEME_SIGNAL_PREQUOTE_MIN_BUY_SELL_RATIO": "MEME_AB_ZONE_PREQUOTE_MIN_BUY_SELL_RATIO",
        "MEME_SIGNAL_PREQUOTE_MAX_TOP_BUYER_SHARE": "MEME_AB_ZONE_PREQUOTE_MAX_TOP_BUYER_SHARE",
        "MEME_SIGNAL_PREQUOTE_SCORE_BYPASS_ENABLED": "MEME_AB_ZONE_PREQUOTE_SCORE_BYPASS_ENABLED",
        "MEME_SIGNAL_PREQUOTE_SCORE_BYPASS_MIN_HITS": "MEME_AB_ZONE_PREQUOTE_SCORE_BYPASS_MIN_HITS",
        "MEME_SIGNAL_PREQUOTE_SCORE_BYPASS_MIN_BUYS": "MEME_AB_ZONE_PREQUOTE_SCORE_BYPASS_MIN_BUYS",
        "MEME_SIGNAL_PREQUOTE_SCORE_BYPASS_MIN_UNIQUE_BUYERS": "MEME_AB_ZONE_PREQUOTE_SCORE_BYPASS_MIN_UNIQUE_BUYERS",
        "MEME_SIGNAL_PREQUOTE_SCORE_BYPASS_MIN_NET_SOL_IN": "MEME_AB_ZONE_PREQUOTE_SCORE_BYPASS_MIN_NET_SOL_IN",
        "MEME_SIGNAL_PREQUOTE_SCORE_BYPASS_MAX_TOP_BUYER_SHARE": "MEME_AB_ZONE_PREQUOTE_SCORE_BYPASS_MAX_TOP_BUYER_SHARE",
    }
    for target_key, ab_key in prequote_overrides.items():
        if ab_key in env and str(env.get(ab_key) or "").strip():
            env[target_key] = str(env.get(ab_key))

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
        if any(_alive(int((v or {}).get("pid") or 0)) for v in lanes.values() if isinstance(v, dict)):
            print("A/B zone lanes already running; use status/stop first.")
            return 0

    ts = int(time.time())
    base_run = f"ab_base_{ts}"
    zone_run = f"ab_zone_{ts}"
    base_db = "data/positions_ab_zone_base.db"
    zone_db = "data/positions_ab_zone_enabled.db"
    env = os.environ.copy()

    base_pid = _spawn_lane("base", base_run, base_db, False, env, LOG_BASE)
    zone_pid = _spawn_lane("zone", zone_run, zone_db, True, env, LOG_ZONE)

    meta = {
        "started_at": ts,
        "lanes": {
            "base": {"pid": base_pid, "run_id": base_run, "db": base_db, "log": str(LOG_BASE)},
            "zone": {"pid": zone_pid, "run_id": zone_run, "db": zone_db, "log": str(LOG_ZONE)},
        },
    }
    _save_meta(meta)
    print(f"started base pid={base_pid} run_id={base_run} db={base_db}")
    print(f"started zone pid={zone_pid} run_id={zone_run} db={zone_db}")
    return 0


def cmd_status() -> int:
    st = _load_meta()
    lanes = st.get("lanes", {}) if isinstance(st.get("lanes"), dict) else {}
    if not lanes:
        print("no ab zone lanes configured")
        return 0
    for name in ("base", "zone"):
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
        print(f"{name}: pid={pid} alive={alive} run_id={run_id} trades={trades} winrate={wr:.1f}% pnl=${pnl:+.2f} db={db_rel}")
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
        print("no ab zone lanes configured")
        return 0
    for name in ("base", "zone"):
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
