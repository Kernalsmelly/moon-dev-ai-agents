#!/usr/bin/env python3
"""Run base vs zone-match-only vs zone-bypass-only paper lanes in parallel."""

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
META = BASE / "data" / "meme_ab_zone_tri_runner.json"

LOGS = {
    "base": BASE / "logs" / "meme_ab_zone_tri_base.log",
    "match": BASE / "logs" / "meme_ab_zone_tri_match.log",
    "bypass": BASE / "logs" / "meme_ab_zone_tri_bypass.log",
}


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


def _load_tri_profile(base_env: dict[str, str]) -> dict:
    raw = str(base_env.get("MEME_AB_TRI_PROFILE_FILE") or "").strip()
    rel = raw or "config/meme_ab_tri_profile.json"
    p = Path(rel)
    if not p.is_absolute():
        p = BASE / p
    if not p.exists():
        return {}
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            return {}
    except Exception:
        return {}
    obj["_path"] = str(p)
    return obj


def _apply_profile(env: dict[str, str], profile: dict, lane: str) -> None:
    if not isinstance(profile, dict):
        return
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

            # Position-level accounting: one position = one (mint, entry_timestamp) lifecycle.
            # Partial exits are merged so status reflects true position outcomes.
            key = (str(mint or ""), str(entry_timestamp or ""))
            pos_pnl[key] = float(pos_pnl.get(key, 0.0)) + p
    except sqlite3.OperationalError:
        # Fresh DB before first trade.
        pass
    finally:
        con.close()

    if pos_pnl:
        out["positions"] = int(len(pos_pnl))
        out["position_pnl_usd"] = float(sum(pos_pnl.values()))
        out["position_wins"] = int(sum(1 for v in pos_pnl.values() if float(v) > 0.0))
    return out


def _spawn_lane(name: str, run_id: str, db_rel: str, base_env: dict[str, str], tri_profile: dict) -> int:
    env = dict(base_env)
    env["MEME_PAPER_MODE"] = "true"
    env["MEME_DISCORD_ALERTS"] = "false"
    env["MEME_RUN_ID"] = run_id
    env["MEME_POSITIONS_DB"] = db_rel
    # Tri runs should measure fresh entries only; do not restore stale open positions.
    env["MEME_RESTORE_OPEN_POSITIONS"] = env.get("MEME_AB_TRI_RESTORE_OPEN_POSITIONS", "0")
    # Keep tri experiment focused on signal/gating quality; avoid probe exits dominating results.
    env["MEME_SCALE_IN_ENABLED"] = env.get("MEME_AB_TRI_SCALE_IN_ENABLED", "0")
    # Isolate zone gate effects; winner-profile scoring can be tested separately.
    env["MEME_WINNER_PROFILE_ENABLED"] = env.get("MEME_AB_TRI_WINNER_PROFILE_ENABLED", "0")

    zone_enabled = name in ("match", "bypass")
    env["MEME_WINNER_ZONE_ENABLED"] = "1" if zone_enabled else "0"
    env["MEME_WINNER_ZONE_BLOCK_WHEN_MISSING"] = env.get("MEME_AB_ZONE_BLOCK_WHEN_MISSING", "0")
    env["MEME_WINNER_ZONE_MATCH_ALLOW_UNKNOWN_MCAP"] = (
        env.get("MEME_AB_ZONE_MATCH_ALLOW_UNKNOWN_MCAP", "1") if zone_enabled else "0"
    )
    env["MEME_WINNER_ZONE_BYPASS_ENABLED"] = (
        env.get("MEME_AB_ZONE_BYPASS_ENABLED", "1") if name == "bypass" else "0"
    )
    env["MEME_WINNER_ZONE_FORCE_BYPASS_ONLY"] = "1" if name == "bypass" else "0"
    if name == "bypass":
        env["MEME_WINNER_ZONE_BYPASS_MIN_SIGNAL_SCORE"] = env.get(
            "MEME_AB_TRI_BYPASS_MIN_SIGNAL_SCORE", env.get("MEME_AB_ZONE_BYPASS_MIN_SIGNAL_SCORE", "64")
        )
        env["MEME_WINNER_ZONE_BYPASS_MIN_HITS"] = env.get(
            "MEME_AB_TRI_BYPASS_MIN_HITS", env.get("MEME_AB_ZONE_BYPASS_MIN_HITS", "4")
        )
        env["MEME_WINNER_ZONE_BYPASS_MIN_UNIQUE_BUYERS"] = env.get(
            "MEME_AB_TRI_BYPASS_MIN_UNIQUE_BUYERS", env.get("MEME_AB_ZONE_BYPASS_MIN_UNIQUE_BUYERS", "3")
        )
        env["MEME_WINNER_ZONE_BYPASS_MIN_NET_SOL_IN"] = env.get(
            "MEME_AB_TRI_BYPASS_MIN_NET_SOL_IN", env.get("MEME_AB_ZONE_BYPASS_MIN_NET_SOL_IN", "1.6")
        )
        env["MEME_WINNER_ZONE_BYPASS_MIN_MCAP_USD"] = env.get(
            "MEME_AB_TRI_BYPASS_MIN_MCAP_USD", env.get("MEME_AB_ZONE_BYPASS_MIN_MCAP_USD", "12000")
        )
        env["MEME_WINNER_ZONE_BYPASS_MAX_TOP_BUYER_SHARE"] = env.get(
            "MEME_AB_TRI_BYPASS_MAX_TOP_BUYER_SHARE", env.get("MEME_AB_ZONE_BYPASS_MAX_TOP_BUYER_SHARE", "0.50")
        )
    else:
        env["MEME_WINNER_ZONE_BYPASS_MIN_SIGNAL_SCORE"] = env.get("MEME_AB_ZONE_BYPASS_MIN_SIGNAL_SCORE", "64")
        env["MEME_WINNER_ZONE_BYPASS_MIN_HITS"] = env.get("MEME_AB_ZONE_BYPASS_MIN_HITS", "4")
        env["MEME_WINNER_ZONE_BYPASS_MIN_UNIQUE_BUYERS"] = env.get("MEME_AB_ZONE_BYPASS_MIN_UNIQUE_BUYERS", "3")
        env["MEME_WINNER_ZONE_BYPASS_MIN_NET_SOL_IN"] = env.get("MEME_AB_ZONE_BYPASS_MIN_NET_SOL_IN", "1.6")
        env["MEME_WINNER_ZONE_BYPASS_MIN_MCAP_USD"] = env.get("MEME_AB_ZONE_BYPASS_MIN_MCAP_USD", "12000")
    env["MEME_WINNER_ZONE_BYPASS_ALLOW_UNKNOWN_MCAP"] = env.get("MEME_AB_ZONE_BYPASS_ALLOW_UNKNOWN_MCAP", "1")
    if "MEME_WINNER_ZONE_BYPASS_MAX_TOP_BUYER_SHARE" not in env:
        env["MEME_WINNER_ZONE_BYPASS_MAX_TOP_BUYER_SHARE"] = env.get("MEME_AB_ZONE_BYPASS_MAX_TOP_BUYER_SHARE", "0.50")

    # Keep per-lane signal debug on to compare entry pipelines.
    env["MEME_SIGNAL_DEBUG"] = env.get("MEME_AB_ZONE_SIGNAL_DEBUG", "1")
    env["MEME_SIGNAL_DEBUG_MAX_PER_MIN"] = env.get("MEME_AB_ZONE_SIGNAL_DEBUG_MAX_PER_MIN", "30")
    env["MEME_SIGNAL_MAX_CANDIDATES_PER_TICK"] = env.get("MEME_AB_ZONE_MAX_CANDIDATES_PER_TICK", "4")
    env["MEME_JUPITER_MAX_CALLS_PER_MIN"] = env.get("MEME_AB_ZONE_JUP_MAX_CALLS_PER_MIN", "12")
    env["MEME_JUPITER_RESERVED_FOR_POSITIONS"] = env.get("MEME_AB_ZONE_JUP_RESERVED_POS", "5")
    tri_ttl = str(env.get("MEME_AB_TRI_LAUNCH_SIGNAL_TTL") or "").strip()
    if tri_ttl:
        env["MEME_LAUNCH_SIGNAL_TTL"] = tri_ttl
    tri_ignore_history = str(env.get("MEME_AB_TRI_LAUNCH_SIGNAL_IGNORE_HISTORY") or "").strip()
    if tri_ignore_history:
        env["MEME_LAUNCH_SIGNAL_IGNORE_HISTORY"] = tri_ignore_history
    tri_min_age = str(env.get("MEME_AB_TRI_SIGNAL_MIN_AGE_SECONDS") or "").strip()
    if tri_min_age:
        env["MEME_SIGNAL_MIN_AGE_SECONDS"] = tri_min_age
    tri_mcap_confirm = str(env.get("MEME_AB_TRI_SIGNAL_MCAP_CONFIRM_SECONDS") or "").strip()
    if tri_mcap_confirm:
        env["MEME_SIGNAL_MCAP_CONFIRM_SECONDS"] = tri_mcap_confirm
    tri_mcap_confirm_recheck = str(env.get("MEME_AB_TRI_SIGNAL_MCAP_CONFIRM_RECHECK_S") or "").strip()
    if tri_mcap_confirm_recheck:
        env["MEME_SIGNAL_MCAP_CONFIRM_RECHECK_S"] = tri_mcap_confirm_recheck
    tri_quote_cooldown = str(env.get("MEME_AB_TRI_SIGNAL_QUOTE_FAIL_COOLDOWN_S") or "").strip()
    if tri_quote_cooldown:
        env["MEME_SIGNAL_QUOTE_FAIL_COOLDOWN_S"] = tri_quote_cooldown
    tri_quote_retry_count = str(env.get("MEME_AB_TRI_SIGNAL_QUOTE_RETRY_COUNT") or "").strip()
    if tri_quote_retry_count:
        env["MEME_SIGNAL_QUOTE_RETRY_COUNT"] = tri_quote_retry_count
    tri_quote_retry_delay = str(env.get("MEME_AB_TRI_SIGNAL_QUOTE_RETRY_DELAY_S") or "").strip()
    if tri_quote_retry_delay:
        env["MEME_SIGNAL_QUOTE_RETRY_DELAY_S"] = tri_quote_retry_delay
    # Let tri runs test conversion under sparse-liquidity provider payloads without
    # altering the main lane defaults.
    env["MEME_SIGNAL_REQUIRE_LIQUIDITY"] = env.get(
        "MEME_AB_TRI_REQUIRE_LIQUIDITY",
        env.get("MEME_SIGNAL_REQUIRE_LIQUIDITY", "true"),
    )
    env["MEME_SIGNAL_REQUIRE_CORE_METRICS"] = env.get(
        "MEME_AB_TRI_REQUIRE_CORE_METRICS",
        env.get("MEME_SIGNAL_REQUIRE_CORE_METRICS", "true"),
    )
    env["MEME_SIGNAL_HYBRID_DEX"] = env.get(
        "MEME_AB_TRI_SIGNAL_HYBRID_DEX",
        env.get("MEME_SIGNAL_HYBRID_DEX", "true"),
    )
    # Tri-specific demand gates so we can tune conversion without touching the main lane.
    for k in ("MIN_BUYS", "MIN_UNIQUE_BUYERS", "MIN_NET_SOL_IN"):
        tri_k = f"MEME_AB_TRI_SIGNAL_{k}"
        base_k = f"MEME_SIGNAL_{k}"
        tri_v = str(env.get(tri_k) or "").strip()
        if tri_v:
            env[base_k] = tri_v
    tri_min_liq = str(env.get("MEME_AB_TRI_MIN_LIQUIDITY_USD") or "").strip()
    if tri_min_liq:
        env["MEME_SIGNAL_MIN_LIQUIDITY_USD"] = tri_min_liq

    # Winner-focused A/B-only prequote gate.
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
        v = str(env.get(ab_key) or "").strip()
        if v:
            env[target_key] = v
    # Align final crowding cap with tri prequote tuning so final filters don't silently dominate.
    env["MEME_SIGNAL_MAX_TOP_BUYER_SHARE"] = env.get(
        "MEME_AB_TRI_FINAL_MAX_TOP_BUYER_SHARE",
        env.get("MEME_AB_ZONE_PREQUOTE_MAX_TOP_BUYER_SHARE", env.get("MEME_SIGNAL_MAX_TOP_BUYER_SHARE", "0.55")),
    )
    # Align final post-prequote guards for tri lanes.
    env["MEME_SIGNAL_MIN_MCAP_USD"] = env.get(
        "MEME_AB_TRI_FINAL_MIN_MCAP_USD",
        env.get("MEME_AB_ZONE_PREQUOTE_MIN_MCAP_USD", env.get("MEME_SIGNAL_MIN_MCAP_USD", "0")),
    )
    env["MEME_SIGNAL_MAX_NET_SOL_IN"] = env.get(
        "MEME_AB_TRI_FINAL_MAX_NET_SOL_IN", env.get("MEME_SIGNAL_MAX_NET_SOL_IN", "0")
    )
    env["MEME_SIGNAL_MAX_AGE_SECONDS"] = env.get(
        "MEME_AB_TRI_SIGNAL_MAX_AGE_SECONDS", env.get("MEME_SIGNAL_MAX_AGE_SECONDS", "900")
    )
    tri_poll = str(env.get("MEME_AB_TRI_POLL_INTERVAL") or "").strip()
    if tri_poll:
        env["MEME_POLL_INTERVAL"] = tri_poll
    tri_sig_max_pos = str(env.get("MEME_AB_TRI_SIGNAL_MAX_POSITION_USD") or "").strip()
    if tri_sig_max_pos:
        env["MEME_SIGNAL_MAX_POSITION_USD"] = tri_sig_max_pos
    tri_max_loss = str(env.get("MEME_AB_TRI_MAX_LOSS_PER_TRADE") or "").strip()
    if tri_max_loss:
        env["MEME_MAX_LOSS_PER_TRADE"] = tri_max_loss

    # Canonical profile is applied last so there is one source of truth for tri tuning.
    _apply_profile(env, tri_profile, name)

    log_path = LOGS[name]
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


def cmd_start() -> int:
    load_dotenv(dotenv_path=str(BASE / ".env"), override=True)
    st = _load_meta()
    lanes = st.get("lanes", {}) if isinstance(st.get("lanes"), dict) else {}
    if lanes and any(_alive(int((v or {}).get("pid") or 0)) for v in lanes.values() if isinstance(v, dict)):
        print("tri lanes already running; use status/stop first")
        return 0

    ts = int(time.time())
    env = os.environ.copy()
    tri_profile = _load_tri_profile(env)
    if tri_profile:
        print(f"using tri profile: {tri_profile.get('_path')}")
    lane_defs = {
        "base": {"run_id": f"ab_tri_base_{ts}", "db": "data/positions_ab_zone_tri_base.db"},
        "match": {"run_id": f"ab_tri_match_{ts}", "db": "data/positions_ab_zone_tri_match.db"},
        "bypass": {"run_id": f"ab_tri_bypass_{ts}", "db": "data/positions_ab_zone_tri_bypass.db"},
    }
    reset_dbs = str(env.get("MEME_AB_TRI_RESET_DBS_ON_START") or "").strip().lower() in ("1", "true", "yes")
    if reset_dbs:
        for lane in ("base", "match", "bypass"):
            db_rel = str(lane_defs[lane]["db"])
            db_path = (BASE / db_rel) if not Path(db_rel).is_absolute() else Path(db_rel)
            for suffix in ("", "-wal", "-shm"):
                try:
                    p = Path(str(db_path) + suffix)
                    if p.exists():
                        p.unlink()
                except Exception:
                    pass
    for name in ("base", "match", "bypass"):
        lane_defs[name]["pid"] = _spawn_lane(
            name,
            str(lane_defs[name]["run_id"]),
            str(lane_defs[name]["db"]),
            env,
            tri_profile,
        )
        lane_defs[name]["log"] = str(LOGS[name])
        print(
            f"started {name} pid={lane_defs[name]['pid']} run_id={lane_defs[name]['run_id']} db={lane_defs[name]['db']}"
        )
    _save_meta({"started_at": ts, "lanes": lane_defs})
    return 0


def cmd_status() -> int:
    st = _load_meta()
    lanes = st.get("lanes", {}) if isinstance(st.get("lanes"), dict) else {}
    if not lanes:
        print("no tri lanes configured")
        return 0
    for name in ("base", "match", "bypass"):
        v = lanes.get(name) if isinstance(lanes.get(name), dict) else {}
        pid = int(v.get("pid") or 0)
        run_id = str(v.get("run_id") or "")
        db_rel = str(v.get("db") or "")
        db_path = BASE / db_rel if db_rel and not Path(db_rel).is_absolute() else Path(db_rel or "")
        summ = _db_summary(db_path, run_id) if db_path else {"trades": 0, "wins": 0, "pnl_usd": 0.0}
        positions = int(summ.get("positions") or 0)
        position_wins = int(summ.get("position_wins") or 0)
        position_pnl = float(summ.get("position_pnl_usd") or 0.0)
        pos_wr = (position_wins / positions * 100.0) if positions > 0 else 0.0

        slices = int(summ.get("slices") or 0)
        slice_wins = int(summ.get("slice_wins") or 0)
        slice_wr = (slice_wins / slices * 100.0) if slices > 0 else 0.0

        print(
            f"{name}: pid={pid} alive={_alive(pid)} run_id={run_id} "
            f"positions={positions} pos_winrate={pos_wr:.1f}% pos_pnl=${position_pnl:+.2f} "
            f"slices={slices} slice_winrate={slice_wr:.1f}%"
        )
    return 0


def cmd_stop() -> int:
    st = _load_meta()
    lanes = st.get("lanes", {}) if isinstance(st.get("lanes"), dict) else {}
    if not lanes:
        print("no tri lanes configured")
        return 0
    for name in ("base", "match", "bypass"):
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
