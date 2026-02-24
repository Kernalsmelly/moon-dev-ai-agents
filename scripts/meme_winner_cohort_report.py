#!/usr/bin/env python3
"""Report winner-score cohort performance from trades metadata."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any


def _ts(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        try:
            return float(v)
        except Exception:
            return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def _load(db: str, since_hours: int) -> list[dict[str, float]]:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        select created_at, pnl_usd, pnl_pct, metadata
        from trades
        where metadata is not null and metadata != ''
        """,
    ).fetchall()
    con.close()
    cutoff = time.time() - max(1, int(since_hours)) * 3600
    out = []
    for r in rows:
        ts = _ts(r["created_at"])
        if ts is None or ts < cutoff:
            continue
        try:
            md = json.loads(r["metadata"] or "{}")
        except Exception:
            md = {}
        if not isinstance(md, dict):
            md = {}
        try:
            ws = float(md.get("winner_score") or 0.0)
            pnlu = float(r["pnl_usd"] or 0.0)
            pnlp = float(r["pnl_pct"] or 0.0)
        except Exception:
            continue
        if ws <= 0:
            continue
        out.append({"winner_score": ws, "pnl_usd": pnlu, "pnl_pct": pnlp})
    return out


def _stats(rows: list[dict[str, float]]) -> dict[str, float]:
    n = len(rows)
    if n == 0:
        return {"n": 0, "wr": 0.0, "sum_pnl_usd": 0.0, "avg_pnl_usd": 0.0, "avg_pnl_pct": 0.0}
    wins = sum(1 for r in rows if float(r["pnl_usd"]) > 0)
    sum_usd = sum(float(r["pnl_usd"]) for r in rows)
    avg_usd = sum_usd / n
    avg_pct = sum(float(r["pnl_pct"]) for r in rows) / n
    return {
        "n": n,
        "wr": wins / n,
        "sum_pnl_usd": sum_usd,
        "avg_pnl_usd": avg_usd,
        "avg_pnl_pct": avg_pct,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/positions.db")
    ap.add_argument("--since-hours", type=int, default=72)
    ap.add_argument("--min-trades", type=int, default=20)
    ap.add_argument("--out-json", default="data/meme_winner_cohort_report.json")
    ap.add_argument("--out-md", default="data/meme_winner_cohort_report.md")
    args = ap.parse_args()

    rows = _load(args.db, args.since_hours)
    rows.sort(key=lambda r: float(r["winner_score"]))
    n = len(rows)
    if n == 0:
        print("no winner_score trades in lookback")
        return 0

    q30 = rows[: max(1, int(n * 0.30))]
    q40 = rows[max(1, int(n * 0.30)) : max(2, int(n * 0.70))]
    q30_top = rows[max(1, int(n * 0.70)) :]
    q20_top = rows[max(1, int(n * 0.80)) :]
    q10_top = rows[max(1, int(n * 0.90)) :]

    cohorts = {
        "bottom30": _stats(q30),
        "mid40": _stats(q40),
        "top30": _stats(q30_top),
        "top20": _stats(q20_top),
        "top10": _stats(q10_top),
    }
    all_stats = _stats(rows)
    out = {
        "generated_at": time.time(),
        "since_hours": int(args.since_hours),
        "total_winner_scored_trades": n,
        "all": all_stats,
        "cohorts": cohorts,
    }

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(out, indent=2), encoding="utf-8")

    lines = [
        "# Winner Cohort Report",
        "",
        f"- Trades with `winner_score`: `{n}`",
        f"- Overall WR: `{all_stats['wr']*100:.1f}%` | Avg PnL USD: `${all_stats['avg_pnl_usd']:+.3f}`",
        "",
        "| Cohort | N | Win Rate | Avg PnL USD | Sum PnL USD | Avg PnL % |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in ("bottom30", "mid40", "top30", "top20", "top10"):
        s = cohorts[name]
        lines.append(
            f"| {name} | {s['n']} | {s['wr']*100:.1f}% | ${s['avg_pnl_usd']:+.3f} | "
            f"${s['sum_pnl_usd']:+.2f} | {s['avg_pnl_pct']:+.2f}% |"
        )
    out_md = Path(args.out_md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"wrote {out_json}")
    print(f"wrote {out_md}")
    # Simple pass/fail signal for iteration:
    t30 = cohorts["top30"]
    b30 = cohorts["bottom30"]
    edge = float(t30["avg_pnl_usd"]) - float(b30["avg_pnl_usd"])
    print(f"top30_minus_bottom30_avg_pnl_usd={edge:+.4f}")
    if n >= int(args.min_trades):
        print("sample_ok=true")
    else:
        print("sample_ok=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

