#!/usr/bin/env python3
"""Decide whether scout lane should be kept, tightened, or disabled.

This avoids overfitting by using simple run-scoped comparisons:
- strict lane (mcap_scout_mode=false)
- scout lane (mcap_scout_mode=true)
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from meme_run_id_utils import auto_run_id


def parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    val = str(ts).strip()
    if not val:
        return None
    if val.endswith("Z"):
        val = val[:-1] + "+00:00"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(val, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            pass
    try:
        dt = datetime.fromisoformat(val)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


@dataclass
class Stats:
    n: int = 0
    wins: int = 0
    pnl_usd: float = 0.0
    pnl_pct_sum: float = 0.0

    def add(self, pnl_usd: float, pnl_pct: float) -> None:
        self.n += 1
        self.pnl_usd += float(pnl_usd)
        self.pnl_pct_sum += float(pnl_pct)
        if pnl_usd > 0:
            self.wins += 1

    def as_dict(self) -> dict:
        if self.n <= 0:
            return {"trades": 0, "win_rate": 0.0, "avg_pnl_usd": 0.0, "avg_pnl_pct": 0.0, "sum_pnl_usd": 0.0}
        return {
            "trades": int(self.n),
            "win_rate": round(float(self.wins) / float(self.n), 4),
            "avg_pnl_usd": round(float(self.pnl_usd) / float(self.n), 4),
            "avg_pnl_pct": round(float(self.pnl_pct_sum) / float(self.n), 4),
            "sum_pnl_usd": round(float(self.pnl_usd), 4),
        }


def decide(strict: Stats, scout: Stats, *, min_scout_trades: int, min_strict_trades: int, pnl_tol: float, win_tol: float) -> tuple[str, str]:
    if scout.n < min_scout_trades or strict.n < min_strict_trades:
        return (
            "collect",
            f"Not enough sample (strict={strict.n}, scout={scout.n}); keep settings and collect more data.",
        )

    strict_avg = (strict.pnl_usd / strict.n) if strict.n else 0.0
    scout_avg = (scout.pnl_usd / scout.n) if scout.n else 0.0
    strict_wr = (strict.wins / strict.n) if strict.n else 0.0
    scout_wr = (scout.wins / scout.n) if scout.n else 0.0

    # Disable if scout clearly worse on both expectancy and win rate.
    if scout_avg < (strict_avg - pnl_tol) and scout_wr < (strict_wr - win_tol):
        return (
            "disable_scout",
            "Scout underperforms strict lane on both avg PnL and win rate beyond tolerance.",
        )
    # Tighten when scout expectancy is worse, even if win rate is similar.
    if scout_avg < (strict_avg - pnl_tol):
        return (
            "tighten_scout",
            "Scout expectancy trails strict lane; tighten scout demand thresholds or reduce scout size.",
        )
    # Keep (and optionally expand later) only if scout is at least as good.
    if scout_avg >= (strict_avg + pnl_tol) and scout_wr >= (strict_wr - win_tol):
        return (
            "keep_or_expand_scout",
            "Scout lane matches/exceeds strict lane after tolerance; keep and consider cautious expansion.",
        )
    return (
        "keep_scout",
        "Scout lane is within tolerance band; keep unchanged and collect more sample.",
    )


def write_md(path: Path, report: dict) -> None:
    strict = report.get("strict", {})
    scout = report.get("scout", {})
    lines = [
        "# Meme Scout Decider",
        "",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Since hours: `{report.get('since_hours')}`",
        f"- Run id filter: `{report.get('run_id') or 'all'}`",
        "",
        "## Decision",
        f"- Action: `{report.get('decision')}`",
        f"- Reason: {report.get('reason')}",
        "",
        "## Strict Lane",
        f"- Trades: {strict.get('trades', 0)}",
        f"- Win rate: {strict.get('win_rate', 0.0)}",
        f"- Avg pnl usd: {strict.get('avg_pnl_usd', 0.0)}",
        f"- Sum pnl usd: {strict.get('sum_pnl_usd', 0.0)}",
        "",
        "## Scout Lane",
        f"- Trades: {scout.get('trades', 0)}",
        f"- Win rate: {scout.get('win_rate', 0.0)}",
        f"- Avg pnl usd: {scout.get('avg_pnl_usd', 0.0)}",
        f"- Sum pnl usd: {scout.get('sum_pnl_usd', 0.0)}",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/positions.db")
    ap.add_argument("--since-hours", type=float, default=24.0)
    ap.add_argument("--run-id", default="")
    ap.add_argument("--auto-run-id", action="store_true", help="Auto-detect run id from bot log if --run-id is empty")
    ap.add_argument("--min-scout-trades", type=int, default=12)
    ap.add_argument("--min-strict-trades", type=int, default=12)
    ap.add_argument("--pnl-tol-usd", type=float, default=0.08)
    ap.add_argument("--win-tol", type=float, default=0.08)
    ap.add_argument("--out-json", default="data/meme_scout_decider.json")
    ap.add_argument("--out-md", default="data/meme_scout_decider.md")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"missing db: {db_path}")
        return 1

    cutoff = datetime.now(timezone.utc) - timedelta(hours=float(args.since_hours))
    run_id = str(args.run_id or "").strip()
    if args.auto_run_id and not run_id:
        run_id = str(auto_run_id() or "").strip()

    strict = Stats()
    scout = Stats()

    con = sqlite3.connect(str(db_path))
    cur = con.cursor()
    try:
        cur.execute("SELECT entry_timestamp, exit_timestamp, pnl_usd, pnl_pct, metadata FROM trades")
        for entry_ts, exit_ts, pnl_usd, pnl_pct, metadata in cur.fetchall():
            dt = parse_ts(exit_ts) or parse_ts(entry_ts)
            if dt is None or dt < cutoff:
                continue
            try:
                md = json.loads(metadata or "{}")
                if not isinstance(md, dict):
                    md = {}
            except Exception:
                md = {}
            if run_id and str(md.get("run_id") or "").strip() != run_id:
                continue
            try:
                pnl_u = float(pnl_usd or 0.0)
            except Exception:
                pnl_u = 0.0
            try:
                pnl_p = float(pnl_pct or 0.0)
            except Exception:
                pnl_p = 0.0
            mode = bool(md.get("mcap_scout_mode"))
            if mode:
                scout.add(pnl_u, pnl_p)
            else:
                strict.add(pnl_u, pnl_p)
    finally:
        con.close()

    decision, reason = decide(
        strict,
        scout,
        min_scout_trades=int(args.min_scout_trades),
        min_strict_trades=int(args.min_strict_trades),
        pnl_tol=float(args.pnl_tol_usd),
        win_tol=float(args.win_tol),
    )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "since_hours": float(args.since_hours),
        "run_id": run_id or None,
        "decision": decision,
        "reason": reason,
        "strict": strict.as_dict(),
        "scout": scout.as_dict(),
        "params": {
            "min_scout_trades": int(args.min_scout_trades),
            "min_strict_trades": int(args.min_strict_trades),
            "pnl_tol_usd": float(args.pnl_tol_usd),
            "win_tol": float(args.win_tol),
        },
    }

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_md(Path(args.out_md), report)

    print(f"wrote {out_json}")
    print(f"wrote {args.out_md}")
    print(f"decision={decision} reason={reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
