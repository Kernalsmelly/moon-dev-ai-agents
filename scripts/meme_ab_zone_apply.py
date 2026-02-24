#!/usr/bin/env python3
"""Apply actionable A/B zone decisions to .env and restart A/B lanes."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
DECISION = BASE / "data" / "meme_reports" / "ab_zone_decision.json"
READINESS = BASE / "data" / "meme_reports" / "ab_zone_ready.json"
STATE = BASE / "data" / "meme_reports" / "ab_zone_apply_state.json"
ENV_PATH = BASE / ".env"
LOG_PATH = BASE / "logs" / "meme_ab_zone_apply.log"
PYTHON = "/opt/homebrew/bin/python3"
ENV_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$")


def _log(msg: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(msg.rstrip() + "\n")


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


def _update_env(change: dict[str, Any]) -> list[tuple[str, str | None, str]]:
    lines = ENV_PATH.read_text(encoding="utf-8", errors="ignore").splitlines() if ENV_PATH.exists() else []
    idx: dict[str, int] = {}
    for i, ln in enumerate(lines):
        m = ENV_RE.match(ln)
        if m:
            idx[m.group(1)] = i

    applied: list[tuple[str, str | None, str]] = []
    for k, v in change.items():
        if not isinstance(k, str) or not k:
            continue
        nv = str(v)
        nline = f"{k}={nv}"
        if k in idx:
            old = lines[idx[k]]
            lines[idx[k]] = nline
            applied.append((k, old, nline))
        else:
            lines.append(nline)
            applied.append((k, None, nline))

    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return applied


def _restart_ab_lanes(env: dict[str, str]) -> None:
    subprocess.run([PYTHON, "-u", str(BASE / "scripts" / "meme_ab_zone_runner.py"), "stop"], cwd=str(BASE), env=env, check=False)
    time.sleep(1.0)
    subprocess.run([PYTHON, "-u", str(BASE / "scripts" / "meme_ab_zone_runner.py"), "start"], cwd=str(BASE), env=env, check=False)


def _trigger_main_rollout(env: dict[str, str]) -> None:
    subprocess.run([PYTHON, "-u", str(BASE / "scripts" / "meme_zone_main_rollout.py")], cwd=str(BASE), env=env, check=False)


def _rebuild_winner_zones(env: dict[str, str]) -> None:
    cmd = [
        PYTHON,
        "-u",
        str(BASE / "scripts" / "meme_winner_zone_builder.py"),
        "--file",
        str(env.get("MEME_WINNER_ZONE_SOURCE_FILE", "data/signal_outcomes.jsonl")),
        "--out",
        str(env.get("MEME_WINNER_ZONE_PATH", "data/meme_winner_zones.json")),
        "--out-md",
        str(env.get("MEME_WINNER_ZONE_OUT_MD", "data/meme_winner_zones.md")),
        "--horizon",
        str(env.get("MEME_WINNER_ZONE_HORIZON_S", "120")),
        "--lookback-hours",
        str(env.get("MEME_WINNER_ZONE_LOOKBACK_HOURS", "96")),
        "--roundtrip-cost-pct",
        str(env.get("MEME_WINNER_ZONE_ROUNDTRIP_COST_PCT", "0.03")),
        "--min-samples",
        str(env.get("MEME_WINNER_ZONE_MIN_SAMPLES", "20")),
        "--min-win-rate",
        str(env.get("MEME_WINNER_ZONE_MIN_WIN_RATE", "0.50")),
        "--min-mean-adj",
        str(env.get("MEME_WINNER_ZONE_MIN_MEAN_ADJ", "0.00")),
        "--max-zones",
        str(env.get("MEME_WINNER_ZONE_MAX_ZONES", "12")),
        "--coarse-fallback",
        str(env.get("MEME_WINNER_ZONE_COARSE_FALLBACK", "1")),
        "--coarse-min-samples",
        str(env.get("MEME_WINNER_ZONE_COARSE_MIN_SAMPLES", "5")),
        "--coarse-min-win-rate",
        str(env.get("MEME_WINNER_ZONE_COARSE_MIN_WIN_RATE", "0.45")),
        "--coarse-min-mean-adj",
        str(env.get("MEME_WINNER_ZONE_COARSE_MIN_MEAN_ADJ", "-0.005")),
    ]
    subprocess.run(cmd, cwd=str(BASE), env=env, check=False)


def main() -> int:
    load_dotenv(dotenv_path=str(BASE / ".env"), override=True)
    env = os.environ.copy()
    enabled = str(env.get("MEME_AB_ZONE_AUTO_APPLY", "0")).strip().lower() in ("1", "true", "yes")
    if not enabled:
        return 0

    decision = _load_json(DECISION, {})
    if not isinstance(decision, dict):
        return 0
    action = str(decision.get("action") or "").strip()
    change = decision.get("change") if isinstance(decision.get("change"), dict) else {}
    if not action or not change:
        return 0

    # only apply controlled actions automatically
    if action not in ("loosen_zone_bypass", "widen_zone_builder"):
        promote_main = str(env.get("MEME_AB_ZONE_AUTO_PROMOTE_MAIN", "0")).strip().lower() in ("1", "true", "yes")
        if not (action == "promote_zone" and promote_main):
            return 0
        ready_obj = _load_json(READINESS, {})
        if not bool((ready_obj or {}).get("ready")):
            _log(
                f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} skip promote_zone: readiness not satisfied"
            )
            return 0

    st = _load_json(STATE, {})
    dedup_s = float(env.get("MEME_AB_ZONE_AUTO_APPLY_DEDUP_S", "1800") or 1800)
    now = time.time()
    sig = json.dumps({"action": action, "change": change}, sort_keys=True, separators=(",", ":"))
    last_sig = str(st.get("last_sig") or "")
    last_ts = float(st.get("last_ts") or 0.0)
    if sig == last_sig and (now - last_ts) < dedup_s:
        return 0

    applied = _update_env(change)
    for k, old, new in applied:
        _log(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} apply {action}: {k}: {old or '(new)'} -> {new}")

    if action == "loosen_zone_bypass":
        _restart_ab_lanes(env)
        _log(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} restarted A/B lanes after {action}")
    elif action == "widen_zone_builder":
        load_dotenv(dotenv_path=str(BASE / ".env"), override=True)
        env2 = os.environ.copy()
        _rebuild_winner_zones(env2)
        _log(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} rebuilt winner zones after {action}")
    elif action == "promote_zone":
        _trigger_main_rollout(env)
        _log(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} triggered main rollout for promote_zone")

    st.update({"last_sig": sig, "last_ts": now, "last_action": action})
    _save_json(STATE, st)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
