#!/usr/bin/env python3
"""Exit-only tuner from recent trade history.

Reads SELL trades from positions DB and proposes a single exit/risk knob update.
This intentionally does not touch entry/discovery gates.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
DEFAULT_DB = BASE / "data" / "positions.db"
DEFAULT_OUT = BASE / "data" / "meme_exit_tuning_recommendation.json"


@dataclass
class ReasonStat:
    reason: str
    n: int
    pnl: float
    avg: float


def _load_reason_stats(db_path: Path, hours: float) -> list[ReasonStat]:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        cur = con.cursor()
        rows = cur.execute(
            """
            SELECT
              COALESCE(exit_reason, 'UNKNOWN') AS reason,
              CAST(pnl_usd AS REAL) AS pnl,
              COALESCE(metadata, '{}') AS metadata
            FROM trades
            WHERE side='SELL'
              AND datetime(created_at) >= datetime('now', ?)
            """,
            (f"-{hours} hours",),
        ).fetchall()
    finally:
        con.close()

    by_reason: dict[str, list[float]] = {}
    for r in rows:
        reason = str(r["reason"] or "UNKNOWN")
        pnl = float(r["pnl"] or 0.0)
        by_reason.setdefault(reason, []).append(pnl)

    out: list[ReasonStat] = []
    for reason, pnls in by_reason.items():
        n = len(pnls)
        s = float(sum(pnls))
        avg = s / n if n else 0.0
        out.append(ReasonStat(reason=reason, n=n, pnl=s, avg=avg))
    out.sort(key=lambda x: x.pnl)
    return out


def _load_reason_stats_for_run(db_path: Path, hours: float, run_id: str) -> list[ReasonStat]:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        cur = con.cursor()
        rows = cur.execute(
            """
            SELECT
              COALESCE(exit_reason, 'UNKNOWN') AS reason,
              CAST(pnl_usd AS REAL) AS pnl,
              COALESCE(metadata, '{}') AS metadata
            FROM trades
            WHERE side='SELL'
              AND datetime(created_at) >= datetime('now', ?)
            """,
            (f"-{hours} hours",),
        ).fetchall()
    finally:
        con.close()

    by_reason: dict[str, list[float]] = {}
    for r in rows:
        try:
            md = json.loads(r["metadata"] or "{}")
        except Exception:
            md = {}
        if str((md or {}).get("run_id") or "").strip() != run_id:
            continue
        reason = str(r["reason"] or "UNKNOWN")
        pnl = float(r["pnl"] or 0.0)
        by_reason.setdefault(reason, []).append(pnl)

    out: list[ReasonStat] = []
    for reason, pnls in by_reason.items():
        n = len(pnls)
        s = float(sum(pnls))
        avg = s / n if n else 0.0
        out.append(ReasonStat(reason=reason, n=n, pnl=s, avg=avg))
    out.sort(key=lambda x: x.pnl)
    return out


def _auto_run_id() -> str:
    log_path = BASE / "logs" / "meme_bot_early_edge_auto.log"
    if not log_path.exists():
        return ""
    try:
        data = log_path.read_bytes()
        if len(data) > 250_000:
            data = data[-250_000:]
        text = data.decode("utf-8", errors="ignore")
        lines = [ln for ln in text.splitlines() if "run_id=" in ln]
        if not lines:
            return ""
        rid = lines[-1].split("run_id=", 1)[1].strip()
        rid = rid.replace("[/dim]", "").split()[0].strip()
        return rid
    except Exception:
        return ""


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except Exception:
        return float(default)


def _proposal(stats: list[ReasonStat]) -> dict[str, Any]:
    by_reason = {s.reason: s for s in stats}

    # 1) Tail-risk control: if MAX_LOSS_CAP is still the biggest drag, tighten cap.
    ml = by_reason.get("MAX_LOSS_CAP")
    if ml and ml.n >= 2 and ml.pnl < -2.0:
        cur = _env_float("MEME_MAX_LOSS_PER_TRADE", 0.90)
        nxt = max(0.50, round(cur - 0.15, 2))
        return {
            "why": "MAX_LOSS_CAP is the largest loss contributor in the recent window.",
            "change": {"MEME_MAX_LOSS_PER_TRADE": f"{nxt:.2f}"},
            "driver": {"reason": ml.reason, "n": ml.n, "pnl": round(ml.pnl, 2), "avg": round(ml.avg, 3)},
        }

    # 2) Scale-in abort drag: make abort threshold less permissive (faster cut).
    sa = by_reason.get("SCALE_IN_ABORT")
    if sa and sa.n >= 3 and sa.pnl < -1.0:
        cur = _env_float("MEME_SCALE_IN_ABORT_BELOW_PCT", -0.8)
        # Move closer to 0, capped at -0.2%
        nxt = min(-0.2, round(cur + 0.2, 2))
        return {
            "why": "SCALE_IN_ABORT exits are a material drag; tighten probe abort threshold.",
            "change": {"MEME_SCALE_IN_ABORT_BELOW_PCT": f"{nxt:.2f}"},
            "driver": {"reason": sa.reason, "n": sa.n, "pnl": round(sa.pnl, 2), "avg": round(sa.avg, 3)},
        }

    # 3) Volume-collapse churn: only allow this exit below a small negative PnL.
    vc = by_reason.get("VOLUME_COLLAPSE")
    if vc and vc.n >= 8 and vc.pnl < -1.0:
        cur = _env_float("MEME_VOL_COLLAPSE_ONLY_IF_PNL_BELOW_PCT", 0.0)
        nxt = min(cur, -1.0)
        return {
            "why": "VOLUME_COLLAPSE exits are net negative; restrict to true losers only.",
            "change": {"MEME_VOL_COLLAPSE_ONLY_IF_PNL_BELOW_PCT": f"{nxt:.1f}"},
            "driver": {"reason": vc.reason, "n": vc.n, "pnl": round(vc.pnl, 2), "avg": round(vc.avg, 3)},
        }

    return {
        "why": "No dominant exit-side drag requiring immediate change.",
        "change": {},
        "driver": {},
    }


def _write_env(path: Path, updates: dict[str, str]) -> None:
    if not updates:
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    for k, v in updates.items():
        replaced = False
        prefix = f"{k}="
        for i, line in enumerate(lines):
            if line.startswith(prefix):
                lines[i] = f"{k}={v}"
                replaced = True
                break
        if not replaced:
            lines.append(f"{k}={v}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--run-id", default="", help="optional: filter to a specific run_id")
    ap.add_argument("--auto-run-id", action="store_true", help="auto-detect run_id from bot log")
    ap.add_argument("--apply", action="store_true", help="Apply proposed change to .env")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"missing db: {db_path}")

    run_id = str(args.run_id or "").strip()
    if args.auto_run_id and not run_id:
        run_id = _auto_run_id()

    if run_id:
        stats = _load_reason_stats_for_run(db_path, args.hours, run_id)
    else:
        stats = _load_reason_stats(db_path, args.hours)
    proposal = _proposal(stats)
    out = {
        "window_hours": float(args.hours),
        "run_id": run_id or None,
        "top_reasons": [
            {"reason": s.reason, "n": s.n, "pnl": round(s.pnl, 2), "avg": round(s.avg, 3)} for s in stats[:12]
        ],
        "proposal": proposal,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")

    print(f"wrote {out_path}")
    print(json.dumps(proposal, indent=2))

    if args.apply and proposal.get("change"):
        env_path = BASE / ".env"
        _write_env(env_path, {str(k): str(v) for k, v in (proposal.get("change") or {}).items()})
        print("applied changes to .env")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
