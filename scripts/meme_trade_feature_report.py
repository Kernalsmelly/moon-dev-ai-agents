#!/usr/bin/env python3
"""
Quick report: P&L by exit reason and by persisted launch-signal features.

This is meant to be run while the pipeline is running to decide which single
knob to turn next.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
import time


def _ts_to_epoch(v: Any) -> float | None:
    """Best-effort parse for exit_timestamp/entry_timestamp.

    Older rows store epoch seconds (float/int). Some rows may store ISO strings.
    """
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
    # Fast path: epoch encoded as string
    try:
        return float(s)
    except Exception:
        pass
    # ISO-ish path
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def _bucket(val: float, edges: list[float]) -> str:
    for e in edges:
        if val < e:
            return f"<{e:g}"
    return f">={edges[-1]:g}"


def _auto_run_id() -> str:
    log_path = Path("logs/meme_bot_early_edge_auto.log")
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


def _load_trades(db_path: str, minutes: int) -> list[dict[str, Any]]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    cutoff_epoch = time.time() - (minutes * 60)
    rows = cur.execute(
        """
        select trade_id, created_at, mint, symbol, side, entry_price, exit_price, entry_timestamp, exit_timestamp,
               amount_usd, pnl_usd, pnl_pct, exit_reason, metadata
        from trades
        where created_at is not null and created_at != ''
        """,
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        # `created_at` is stored as local time ("YYYY-MM-DD HH:MM:SS") and is what
        # other status/report scripts use for time windows.
        exit_ts = _ts_to_epoch(r["created_at"])
        if exit_ts is None or float(exit_ts) < cutoff_epoch:
            continue
        try:
            md = json.loads(r["metadata"] or "{}")
        except Exception:
            md = {}
        if not isinstance(md, dict):
            md = {}
        d = dict(r)
        d["metadata"] = md
        d["_exit_epoch"] = exit_ts
        out.append(d)
    out.sort(key=lambda t: float(t.get("_exit_epoch") or 0), reverse=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/positions.db")
    ap.add_argument("--minutes", type=int, default=240, help="lookback window")
    ap.add_argument("--top", type=int, default=8, help="show top winners/losers")
    ap.add_argument("--run-id", default="", help="optional: filter to a specific run_id from trade metadata")
    ap.add_argument("--auto-run-id", action="store_true", help="auto-detect run_id from bot log")
    args = ap.parse_args()

    trades = _load_trades(args.db, args.minutes)
    run_id = str(args.run_id or "").strip()
    if args.auto_run_id and not run_id:
        run_id = _auto_run_id()
    if run_id:
        trades = [t for t in trades if str((t.get("metadata") or {}).get("run_id") or "") == run_id]
    if not trades:
        if run_id:
            print(f"no trades in last {args.minutes} minutes for run_id={run_id}")
        else:
            print(f"no trades in last {args.minutes} minutes")
        return 0

    total_pnl = sum(float(t["pnl_usd"] or 0) for t in trades)
    wins = sum(1 for t in trades if float(t["pnl_usd"] or 0) > 0)
    if run_id:
        print(f"run_id={run_id}")
    print(f"trades={len(trades)} wins={wins} wr={wins/len(trades)*100:.1f}% pnl=${total_pnl:+.2f}")

    def _f(md: dict, k: str, default: float = 0.0) -> float:
        v = md.get(k)
        try:
            return float(v)
        except Exception:
            return float(default)

    # Extremes (what is actually killing / making the run)
    topn = max(0, int(args.top))
    if topn:
        srt = sorted(trades, key=lambda t: float(t.get("pnl_usd") or 0.0))
        print("\nTop losers:")
        for t in srt[:topn]:
            md = t["metadata"] or {}
            print(
                f"  {str(t.get('symbol') or ''):12s} pnl=${float(t.get('pnl_usd') or 0):+6.2f} "
                f"reason={str(t.get('exit_reason') or ''):18s} "
                f"mcap=${_f(md,'market_cap_entry'):,.0f} liq=${_f(md,'liquidity_entry'):,.0f} "
                f"5m%={_f(md,'price_change_5m_entry'):+.1f} bs={_f(md,'buys_5m_entry'):.0f}/{_f(md,'sells_5m_entry'):.0f} "
                f"tx={_f(md,'txns_5m_entry'):.0f} vol5m=${_f(md,'volume_5m_entry'):,.0f}"
            )
        print("\nTop winners:")
        for t in reversed(srt[-topn:]):
            md = t["metadata"] or {}
            print(
                f"  {str(t.get('symbol') or ''):12s} pnl=${float(t.get('pnl_usd') or 0):+6.2f} "
                f"reason={str(t.get('exit_reason') or ''):18s} "
                f"mcap=${_f(md,'market_cap_entry'):,.0f} liq=${_f(md,'liquidity_entry'):,.0f} "
                f"5m%={_f(md,'price_change_5m_entry'):+.1f} bs={_f(md,'buys_5m_entry'):.0f}/{_f(md,'sells_5m_entry'):.0f} "
                f"tx={_f(md,'txns_5m_entry'):.0f} vol5m=${_f(md,'volume_5m_entry'):,.0f}"
            )

    # Exit reason table
    by_reason: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        by_reason[str(t.get("exit_reason") or "UNKNOWN")].append(float(t["pnl_usd"] or 0))

    print("\nP&L by exit_reason:")
    for reason, pnls in sorted(by_reason.items(), key=lambda kv: sum(kv[1])):
        pnl = sum(pnls)
        avg = pnl / len(pnls)
        print(f"{reason:22s} n={len(pnls):4d} pnl=${pnl:+7.2f} avg=${avg:+.3f}")

    # Feature buckets
    # These are persisted on SELL legs as of 2026-02-10 changes.
    feats = {
        "signal_score": ("signal_score", [40, 55, 65, 75]),
        "signal_hits": ("signal_hits", [3, 5, 7, 10]),
        "signal_unique_buyers": ("signal_unique_buyers", [2, 3, 4, 6]),
        "signal_net_sol_in": ("signal_net_sol_in", [0.5, 1.0, 2.0, 5.0]),
        "signal_top_buyer_share": ("signal_top_buyer_share", [0.45, 0.55, 0.70, 0.85]),
        "signal_t_first_sell_s": ("signal_t_first_sell_s", [1, 3, 8, 15]),
        "market_cap_entry": ("market_cap_entry", [25_000, 50_000, 100_000, 250_000]),
    }

    print("\nP&L by feature bucket (when present):")
    for label, (key, edges) in feats.items():
        buckets: dict[str, list[float]] = defaultdict(list)
        present = 0
        for t in trades:
            md = t["metadata"] or {}
            v = md.get(key)
            if v is None:
                continue
            try:
                vf = float(v)
            except Exception:
                continue
            # In non-signal modes we persist signal_* fields as 0. Treat those as missing.
            if key.startswith("signal_") and vf <= 0:
                continue
            present += 1
            buckets[_bucket(vf, edges)].append(float(t["pnl_usd"] or 0))
        if present < 10:
            continue
        print(f"\n{label} present={present}/{len(trades)}")
        for b, pnls in sorted(buckets.items(), key=lambda kv: sum(kv[1])):
            pnl = sum(pnls)
            avg = pnl / len(pnls)
            print(f"  {b:10s} n={len(pnls):4d} pnl=${pnl:+7.2f} avg=${avg:+.3f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
