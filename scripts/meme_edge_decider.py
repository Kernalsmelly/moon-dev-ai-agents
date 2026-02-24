#!/usr/bin/env python3
"""Watch edge reports and propose the next single tuning change.

This keeps iteration disciplined:
- Wait until we have enough sample size under the current settings (run n >= threshold)
- Propose exactly one knob change
- Write the proposal to data/next_tuning.json and logs/meme_edge_decider.log

By default this does NOT auto-apply env changes.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
EDGE_LOG = BASE / "logs" / "meme_edge_report.log"
OUT_JSON = BASE / "data" / "next_tuning.json"
STATE_JSON = BASE / "data" / "edge_decider_state.json"
DECIDER_LOG = BASE / "logs" / "meme_edge_decider.log"

# Ensure .env is loaded even when the decider is launched outside the supervisor.
load_dotenv(dotenv_path=str(BASE / ".env"), override=True)

RUN_N_THRESHOLD = int(os.getenv("EDGE_DECIDER_MIN_RUN_EXITS", "30") or 30)
POLL_S = float(os.getenv("EDGE_DECIDER_POLL_S", "15") or 15)
DEDUP_HOLD_S = float(os.getenv("EDGE_DECIDER_DEDUP_HOLD_S", "21600") or 21600)  # 6h


@dataclass
class Window:
    ts: str
    n: int
    pnl: float
    max_loss_n: int
    max_loss_pnl: float
    ff_n: int
    ff_pnl: float
    dump_n: int
    dump_pnl: float


LINE_RE = re.compile(
    r"edge_report ts=(?P<ts>\S+)\s+"
    r"run n=(?P<run_n>\d+) pnl=(?P<run_pnl>[+-]?[0-9]*\.?[0-9]+).*?"
    r"MAX_LOSS\((?P<ml_n>\d+),(?P<ml_pnl>[+-]?[0-9]*\.?[0-9]+)\).*?"
    r"FF\((?P<ff_n>\d+),(?P<ff_pnl>[+-]?[0-9]*\.?[0-9]+)\).*?"
    r"DUMP\((?P<dump_n>\d+),(?P<dump_pnl>[+-]?[0-9]*\.?[0-9]+)\)"
)


def _read_last_line(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            if size <= 0:
                return None
            # read last ~4KB
            fh.seek(max(0, size - 4096))
            chunk = fh.read().decode("utf-8", errors="ignore")
        lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
        return lines[-1] if lines else None
    except Exception:
        return None


def _parse(line: str) -> Window | None:
    m = LINE_RE.search(line or "")
    if not m:
        return None
    try:
        return Window(
            ts=str(m.group("ts")),
            n=int(m.group("run_n")),
            pnl=float(m.group("run_pnl")),
            max_loss_n=int(m.group("ml_n")),
            max_loss_pnl=float(m.group("ml_pnl")),
            ff_n=int(m.group("ff_n")),
            ff_pnl=float(m.group("ff_pnl")),
            dump_n=int(m.group("dump_n")),
            dump_pnl=float(m.group("dump_pnl")),
        )
    except Exception:
        return None


def _proposal_signature(prop: dict[str, Any]) -> str:
    change = prop.get("change") if isinstance(prop, dict) else {}
    why = prop.get("why") if isinstance(prop, dict) else ""
    try:
        return json.dumps({"change": change, "why": why}, sort_keys=True, separators=(",", ":"))
    except Exception:
        return str(change) + "|" + str(why)


def _load_state() -> dict[str, Any]:
    if not STATE_JSON.exists():
        return {}
    try:
        return json.loads(STATE_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(st: dict[str, Any]) -> None:
    try:
        STATE_JSON.parent.mkdir(parents=True, exist_ok=True)
        STATE_JSON.write_text(json.dumps(st, indent=2))
    except Exception:
        pass


def _log(msg: str) -> None:
    DECIDER_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(DECIDER_LOG, "a", encoding="utf-8") as fh:
        fh.write(msg + "\n")


def _proposal(win: Window) -> dict[str, Any] | None:
    # One lever only. Prefer reducing tail risk first.
    if win.n < RUN_N_THRESHOLD:
        return None

    # If max-loss events are happening, they dominate variance. Tighten the cap first.
    if win.max_loss_n > 0 and win.max_loss_pnl < 0:
        cur = float(os.getenv("MEME_MAX_LOSS_PER_TRADE", "1.00") or 1.0)
        nxt = max(0.50, round(cur - 0.25, 2))
        if nxt >= cur:
            nxt = max(0.50, round(cur * 0.8, 2))
        return {
            "why": "Tail losses still present (MAX_LOSS_CAP). Tighten the max loss cap to reduce drawdowns.",
            "change": {"MEME_MAX_LOSS_PER_TRADE": f"{nxt:.2f}"},
            "notes": {
                "run_n": win.n,
                "run_pnl": win.pnl,
                "max_loss_n": win.max_loss_n,
                "max_loss_pnl": win.max_loss_pnl,
                "ff_n": win.ff_n,
                "ff_pnl": win.ff_pnl,
                "dump_n": win.dump_n,
                "dump_pnl": win.dump_pnl,
            },
        }

    # If momentum dump exits are bleeding, exit earlier (less deep threshold).
    if win.dump_n > 0 and win.dump_pnl < -0.5:
        cur = float(os.getenv("MEME_DUMP_THRESHOLD", "-20.0") or -20.0)
        # Move toward 0 (earlier exit), but keep a sane floor.
        nxt = min(-5.0, round(cur + 5.0, 1))
        if nxt >= cur:
            nxt = min(-5.0, round(cur * 0.8, 1))
        return {
            "why": "Momentum-dump exits are bleeding. Exit earlier by tightening the 5m dump threshold.",
            "change": {"MEME_DUMP_THRESHOLD": f"{nxt:.1f}"},
            "notes": {
                "run_n": win.n,
                "run_pnl": win.pnl,
                "max_loss_n": win.max_loss_n,
                "max_loss_pnl": win.max_loss_pnl,
                "ff_n": win.ff_n,
                "ff_pnl": win.ff_pnl,
                "dump_n": win.dump_n,
                "dump_pnl": win.dump_pnl,
            },
        }

    # If fail-fast is the main bleed, entry quality likely still too loose.
    if win.ff_n > 0 and win.ff_pnl < -0.5:
        cur = float(os.getenv("MEME_SIGNAL_MIN_NET_SOL_IN", "1.00") or 1.0)
        nxt = round(cur + 0.25, 2)
        return {
            "why": "Fail-fast exits still bleeding. Raise entry quality by requiring more early net SOL inflow.",
            "change": {"MEME_SIGNAL_MIN_NET_SOL_IN": f"{nxt:.2f}", "PUMP_SIGNAL_MIN_NET_SOL_IN": f"{nxt:.2f}"},
            "notes": {
                "run_n": win.n,
                "run_pnl": win.pnl,
                "max_loss_n": win.max_loss_n,
                "max_loss_pnl": win.max_loss_pnl,
                "ff_n": win.ff_n,
                "ff_pnl": win.ff_pnl,
                "dump_n": win.dump_n,
                "dump_pnl": win.dump_pnl,
            },
        }

    return {
        "why": "No obvious drag in run window. Hold settings and collect more sample before changing knobs.",
        "change": {},
        "notes": {
            "run_n": win.n,
            "run_pnl": win.pnl,
            "max_loss_n": win.max_loss_n,
            "max_loss_pnl": win.max_loss_pnl,
            "ff_n": win.ff_n,
            "ff_pnl": win.ff_pnl,
            "dump_n": win.dump_n,
            "dump_pnl": win.dump_pnl,
        },
    }


def main() -> int:
    st = _load_state()
    last_line = st.get("last_line")
    last_written_at = float(st.get("last_written_at") or 0.0)
    last_run_n = int(st.get("last_run_n") or 0)
    last_proposal_sig = str(st.get("last_proposal_sig") or "")
    last_report_ts = str(st.get("last_report_ts") or "")

    while True:
        line = _read_last_line(EDGE_LOG)
        if line and line != last_line:
            st["last_line"] = line
            win = _parse(line)
            if win:
                st["last_report_ts"] = str(win.ts)
                # Track run_n so we only propose once when it crosses the threshold.
                # run_n is monotonic within a run window and resets when the reporter restarts.
                try:
                    last_run_n = int(st.get("last_run_n") or 0)
                except Exception:
                    last_run_n = 0
                st["last_run_n"] = int(win.n)

                prop = _proposal(win)
                if prop is not None:
                    crossed = (last_run_n < RUN_N_THRESHOLD) and (win.n >= RUN_N_THRESHOLD)
                    sig = _proposal_signature(prop)
                    now = time.time()
                    same_recent = (sig == last_proposal_sig) and ((now - last_written_at) < DEDUP_HOLD_S)
                    # Only write once per threshold crossing, and suppress duplicate identical proposals.
                    if crossed and (now - last_written_at) > 10 and not same_recent:
                        OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
                        OUT_JSON.write_text(json.dumps({"generated_at": now, **prop}, indent=2))
                        _log(
                            f"{time.strftime('%Y-%m-%dT%H:%M:%S')} proposal: {prop.get('change') or 'none'} "
                            f"why={prop.get('why')} sig={sig}"
                        )
                        st["last_written_at"] = now
                        st["last_proposal_sig"] = sig
                        last_written_at = now
                        last_proposal_sig = sig
            _save_state(st)

        time.sleep(max(5.0, POLL_S))


if __name__ == "__main__":
    raise SystemExit(main())
