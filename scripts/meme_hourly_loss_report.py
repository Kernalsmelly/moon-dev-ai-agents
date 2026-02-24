#!/usr/bin/env python3
"""Hourly loss-cause report for meme pipeline.

Produces a compact ranked report of:
- loss by exit reason
- loss by market-cap and liquidity entry buckets

Writes to:
- logs/meme_hourly_loss_report.log
- data/meme_hourly_loss_report_latest.json
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from typing import Any


BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
DB_PATH = BASE / "data" / "positions.db"
LOG_PATH = BASE / "logs" / "meme_hourly_loss_report.log"
LATEST_JSON = BASE / "data" / "meme_hourly_loss_report_latest.json"


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))


def _bucket_mcap(v: float | None) -> str:
    if v is None or v <= 0:
        return "unknown"
    if v < 15_000:
        return "<15k"
    if v < 50_000:
        return "15k-50k"
    if v < 250_000:
        return "50k-250k"
    return ">=250k"


def _bucket_liq(v: float | None) -> str:
    if v is None or v <= 0:
        return "unknown"
    if v < 10_000:
        return "<10k"
    if v < 25_000:
        return "10k-25k"
    if v < 50_000:
        return "25k-50k"
    return ">=50k"


def _load_rows(window_minutes: int) -> list[dict[str, Any]]:
    if not DB_PATH.exists():
        return []
    con = sqlite3.connect(str(DB_PATH))
    try:
        cur = con.cursor()
        rows = cur.execute(
            """
            SELECT
              COALESCE(exit_reason, 'UNKNOWN') AS reason,
              CAST(pnl_usd AS REAL) AS pnl,
              metadata
            FROM trades
            WHERE side='SELL'
              AND datetime(created_at) >= datetime('now', ?)
            """,
            (f"-{int(window_minutes)} minutes",),
        ).fetchall()
    finally:
        con.close()

    out = []
    for reason, pnl, md_raw in rows:
        md = {}
        try:
            md = json.loads(md_raw or "{}")
            if not isinstance(md, dict):
                md = {}
        except Exception:
            md = {}
        try:
            p = float(pnl or 0.0)
        except Exception:
            p = 0.0
        out.append({"reason": str(reason or "UNKNOWN"), "pnl": p, "metadata": md})
    return out


def _rank_map(rows: list[dict[str, Any]], key_fn) -> list[dict[str, Any]]:
    agg = defaultdict(lambda: {"n": 0, "loss_pnl": 0.0, "net_pnl": 0.0})
    for r in rows:
        k = key_fn(r)
        pnl = float(r.get("pnl") or 0.0)
        agg[k]["n"] += 1
        agg[k]["net_pnl"] += pnl
        if pnl < 0:
            agg[k]["loss_pnl"] += pnl
    items = [{"key": k, **v} for k, v in agg.items()]
    items.sort(key=lambda x: float(x["loss_pnl"]))
    return items


def _build_report(window_minutes: int) -> dict[str, Any]:
    rows = _load_rows(window_minutes)
    losses = [r for r in rows if float(r.get("pnl") or 0.0) < 0.0]

    by_reason = _rank_map(rows, lambda r: str(r.get("reason") or "UNKNOWN"))
    by_mcap = _rank_map(
        rows,
        lambda r: _bucket_mcap(
            (r.get("metadata") or {}).get("market_cap_entry")
            if isinstance(r.get("metadata"), dict)
            else None
        ),
    )
    by_liq = _rank_map(
        rows,
        lambda r: _bucket_liq(
            (r.get("metadata") or {}).get("liquidity_entry")
            if isinstance(r.get("metadata"), dict)
            else None
        ),
    )

    return {
        "ts": _iso(time.time()),
        "window_minutes": int(window_minutes),
        "sell_trades": len(rows),
        "losing_trades": len(losses),
        "net_pnl": round(sum(float(r.get("pnl") or 0.0) for r in rows), 4),
        "loss_pnl_total": round(sum(float(r.get("pnl") or 0.0) for r in losses), 4),
        "top_loss_reasons": by_reason[:8],
        "top_loss_mcap_buckets": by_mcap[:6],
        "top_loss_liq_buckets": by_liq[:6],
    }


def _line(report: dict[str, Any]) -> str:
    top_r = report.get("top_loss_reasons") or []
    top_m = report.get("top_loss_mcap_buckets") or []
    top_l = report.get("top_loss_liq_buckets") or []

    def _fmt(items: list[dict[str, Any]]) -> str:
        parts = []
        for it in items[:3]:
            parts.append(
                f"{it.get('key')}[{int(it.get('n') or 0)}:{float(it.get('loss_pnl') or 0.0):+.2f}]"
            )
        return " ".join(parts)

    return (
        f"hourly_loss_report ts={report.get('ts')} window={report.get('window_minutes')}m "
        f"trades={report.get('sell_trades')} losses={report.get('losing_trades')} "
        f"net={float(report.get('net_pnl') or 0.0):+.2f} loss={float(report.get('loss_pnl_total') or 0.0):+.2f} "
        f"reasons={_fmt(top_r)} mcap={_fmt(top_m)} liq={_fmt(top_l)}"
    )


def main() -> int:
    interval_s = int(float(os.getenv("MEME_HOURLY_LOSS_REPORT_INTERVAL_S", "3600") or 3600))
    window_m = int(float(os.getenv("MEME_HOURLY_LOSS_REPORT_WINDOW_MIN", "60") or 60))
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LATEST_JSON.parent.mkdir(parents=True, exist_ok=True)

    while True:
        report = _build_report(window_m)
        LATEST_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(_line(report) + "\n")
        time.sleep(max(60, interval_s))


if __name__ == "__main__":
    raise SystemExit(main())

