#!/usr/bin/env python3
"""Mine common patterns between winner trades and dump trades.

Outputs:
- JSON summary (machine-readable)
- Markdown report (human-readable)
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class TradeRow:
    pnl_usd: float
    pnl_pct: float
    metadata: dict[str, Any]


FEATURES = [
    ("market_cap_entry", "Market Cap Entry", "high"),
    ("liquidity_entry", "Liquidity Entry", "high"),
    ("price_change_5m_entry", "Price Change 5m Entry", "high"),
    ("txns_5m_entry", "Txns 5m Entry", "high"),
    ("volume_5m_entry", "Volume 5m Entry", "high"),
    ("signal_score", "Signal Score", "high"),
    ("signal_hits", "Signal Hits", "high"),
    ("signal_unique_buyers", "Signal Unique Buyers", "high"),
    ("signal_net_sol_in", "Signal Net SOL In", "high"),
    ("signal_top_buyer_share", "Top Buyer Share", "low"),
]


def _epoch(v: Any) -> float | None:
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


def _f(md: dict[str, Any], k: str) -> float | None:
    v = md.get(k)
    if v is None:
        return None
    try:
        fv = float(v)
    except Exception:
        return None
    if not math.isfinite(fv):
        return None
    return fv


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    q = max(0.0, min(1.0, float(q)))
    pos = (len(xs) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(xs[lo])
    frac = pos - lo
    return float(xs[lo] * (1 - frac) + xs[hi] * frac)


def _has_signal_metadata(md: dict[str, Any]) -> bool:
    keys = ("signal_score", "signal_hits", "signal_unique_buyers", "signal_net_sol_in")
    for k in keys:
        if k in md and md.get(k) not in (None, "", 0, 0.0):
            return True
    return False


def _load_trades(db_path: str, lookback_h: int, signal_only: bool) -> list[TradeRow]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        select created_at, pnl_usd, pnl_pct, metadata
        from trades
        where metadata is not null and metadata != ''
        """,
    ).fetchall()
    con.close()

    cutoff = time.time() - max(1, int(lookback_h)) * 3600
    out: list[TradeRow] = []
    for r in rows:
        ts = _epoch(r["created_at"])
        if ts is None or ts < cutoff:
            continue
        try:
            md = json.loads(r["metadata"] or "{}")
        except Exception:
            md = {}
        if not isinstance(md, dict):
            md = {}
        try:
            out.append(
                TradeRow(
                    pnl_usd=float(r["pnl_usd"] or 0.0),
                    pnl_pct=float(r["pnl_pct"] or 0.0),
                    metadata=md,
                )
            )
        except Exception:
            continue
    if signal_only:
        out = [t for t in out if _has_signal_metadata(t.metadata)]
    return out


def _bucket_table(
    trades: list[TradeRow],
    key: str,
    edges: list[float],
) -> list[dict[str, Any]]:
    bins: dict[str, list[TradeRow]] = {}

    def _label(v: float) -> str:
        for e in edges:
            if v < e:
                return f"<{e:g}"
        return f">={edges[-1]:g}"

    for t in trades:
        val = _f(t.metadata, key)
        if val is None:
            continue
        lb = _label(val)
        bins.setdefault(lb, []).append(t)

    rows: list[dict[str, Any]] = []
    for lb, group in bins.items():
        n = len(group)
        if n == 0:
            continue
        wins = sum(1 for x in group if x.pnl_usd > 0)
        sum_pnl = sum(x.pnl_usd for x in group)
        avg_pct = sum(x.pnl_pct for x in group) / n
        rows.append(
            {
                "bucket": lb,
                "trades": n,
                "win_rate": wins / n,
                "sum_pnl_usd": sum_pnl,
                "avg_pnl_pct": avg_pct,
            }
        )
    rows.sort(key=lambda r: r["sum_pnl_usd"], reverse=True)
    return rows


def _feature_diff(winners: list[TradeRow], losers: list[TradeRow]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, label, direction in FEATURES:
        wvals = [_f(t.metadata, key) for t in winners]
        lvals = [_f(t.metadata, key) for t in losers]
        wv = [x for x in wvals if x is not None]
        lv = [x for x in lvals if x is not None]
        if len(wv) < 8 or len(lv) < 8:
            continue
        w50 = _pct(wv, 0.5)
        l50 = _pct(lv, 0.5)
        w25 = _pct(wv, 0.25)
        w75 = _pct(wv, 0.75)
        l25 = _pct(lv, 0.25)
        l75 = _pct(lv, 0.75)
        if direction == "high":
            sep = w50 - l50
            threshold = (w25 + l75) / 2.0
        else:
            sep = l50 - w50
            threshold = (w75 + l25) / 2.0
        spread = abs(w75 - w25) + abs(l75 - l25) + 1e-9
        strength = sep / spread
        rows.append(
            {
                "key": key,
                "label": label,
                "direction": direction,
                "winner_median": w50,
                "loser_median": l50,
                "winner_iqr": [w25, w75],
                "loser_iqr": [l25, l75],
                "separation": sep,
                "strength": strength,
                "candidate_threshold": threshold,
                "winner_n": len(wv),
                "loser_n": len(lv),
            }
        )
    rows.sort(key=lambda r: r["strength"], reverse=True)
    return rows


def _fmt_pct(x: float) -> str:
    return f"{x*100:.1f}%"


def build_report(
    trades: list[TradeRow],
    winners: list[TradeRow],
    losers: list[TradeRow],
    feature_rows: list[dict[str, Any]],
    out_md: Path,
) -> None:
    lines: list[str] = []
    wins = sum(1 for t in trades if t.pnl_usd > 0)
    wr = (wins / len(trades)) if trades else 0.0
    total_pnl = sum(t.pnl_usd for t in trades)
    lines.append("# Meme Pump/Dump Commonality")
    lines.append("")
    lines.append(f"- Trades analyzed: `{len(trades)}`")
    lines.append(f"- Win rate: `{_fmt_pct(wr)}`")
    lines.append(f"- Total PnL: `${total_pnl:+.2f}`")
    lines.append(f"- Winner cohort: `{len(winners)}` | Dump cohort: `{len(losers)}`")
    lines.append("")
    lines.append("## Strongest Separators (Winner vs Dump)")
    lines.append("")
    lines.append("| Feature | Direction | Winner Median | Dump Median | Strength | Candidate Threshold |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for r in feature_rows[:10]:
        lines.append(
            f"| {r['label']} | {r['direction']} | {r['winner_median']:.4g} | "
            f"{r['loser_median']:.4g} | {r['strength']:.3f} | {r['candidate_threshold']:.4g} |"
        )
    lines.append("")

    lines.append("## Market Cap Buckets")
    lines.append("")
    mcap_rows = _bucket_table(trades, "market_cap_entry", [25_000, 50_000, 100_000, 250_000, 500_000])
    lines.append("| Bucket | Trades | Win Rate | Sum PnL USD | Avg PnL % |")
    lines.append("|---|---:|---:|---:|---:|")
    for r in mcap_rows:
        lines.append(
            f"| {r['bucket']} | {r['trades']} | {_fmt_pct(r['win_rate'])} | "
            f"${r['sum_pnl_usd']:+.2f} | {r['avg_pnl_pct']:+.2f}% |"
        )
    lines.append("")

    lines.append("## Signal Score Buckets")
    lines.append("")
    sig_rows = _bucket_table(trades, "signal_score", [30, 40, 50, 60, 70, 80])
    lines.append("| Bucket | Trades | Win Rate | Sum PnL USD | Avg PnL % |")
    lines.append("|---|---:|---:|---:|---:|")
    for r in sig_rows:
        lines.append(
            f"| {r['bucket']} | {r['trades']} | {_fmt_pct(r['win_rate'])} | "
            f"${r['sum_pnl_usd']:+.2f} | {r['avg_pnl_pct']:+.2f}% |"
        )

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/positions.db")
    ap.add_argument("--lookback-hours", type=int, default=168)
    ap.add_argument("--winner-pnl-pct", type=float, default=15.0)
    ap.add_argument("--dump-pnl-pct", type=float, default=-8.0)
    ap.add_argument("--out-json", default="data/meme_pump_dump_commonality.json")
    ap.add_argument("--out-md", default="data/meme_pump_dump_commonality.md")
    ap.add_argument("--signal-only", action="store_true", help="Only include trades with signal metadata keys.")
    args = ap.parse_args()

    trades = _load_trades(args.db, args.lookback_hours, args.signal_only)
    if not trades:
        print("No trades found in lookback.")
        return 1

    winners = [t for t in trades if t.pnl_pct >= float(args.winner_pnl_pct) and t.pnl_usd > 0]
    losers = [t for t in trades if t.pnl_pct <= float(args.dump_pnl_pct) and t.pnl_usd < 0]
    if len(winners) < 20 or len(losers) < 20:
        winners = [t for t in trades if t.pnl_usd > 0]
        losers = [t for t in trades if t.pnl_usd < 0]

    feature_rows = _feature_diff(winners, losers)
    out_obj = {
        "generated_at": time.time(),
        "lookback_hours": int(args.lookback_hours),
        "total_trades": len(trades),
        "winner_count": len(winners),
        "dump_count": len(losers),
        "winner_pnl_pct_threshold": float(args.winner_pnl_pct),
        "dump_pnl_pct_threshold": float(args.dump_pnl_pct),
        "feature_separators": feature_rows,
        "market_cap_buckets": _bucket_table(trades, "market_cap_entry", [25_000, 50_000, 100_000, 250_000, 500_000]),
        "signal_score_buckets": _bucket_table(trades, "signal_score", [30, 40, 50, 60, 70, 80]),
    }

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(out_obj, indent=2), encoding="utf-8")

    out_md = Path(args.out_md)
    build_report(trades, winners, losers, feature_rows, out_md)

    print(f"Wrote {out_json}")
    print(f"Wrote {out_md}")
    if feature_rows:
        print("Top separators:")
        for row in feature_rows[:6]:
            print(
                f"  {row['key']}: direction={row['direction']} "
                f"strength={row['strength']:.3f} threshold~{row['candidate_threshold']:.4g}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
