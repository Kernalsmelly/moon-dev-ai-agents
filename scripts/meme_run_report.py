#!/usr/bin/env python3
"""Run-scoped performance report for the MemeCoinBot.

Why this exists:
- The repo has long-lived paper stats and many restarts. Aggregate PnL is noisy.
- We persist `run_id` into trade metadata so we can iterate on config using clean windows.

Usage:
  python3 scripts/meme_run_report.py --auto --hours 2
  python3 scripts/meme_run_report.py --run-id run_123 --hours 6 --out docs/meme_run_reports/run_123.md
  python3 scripts/meme_run_report.py --auto --hours 2 --cluster-entry-tolerance-sec 180
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable


BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
LOG_BOT = BASE / "logs" / "meme_bot_early_edge_auto.log"
DB_DEFAULT = BASE / "data" / "positions.db"
RUNNER_META = BASE / "data" / "meme_base_simple_runner.json"


def _tail_last_matching(path: Path, needle: str, max_bytes: int = 250_000) -> str | None:
    try:
        if not path.exists():
            return None
        data = path.read_bytes()
        if len(data) > max_bytes:
            data = data[-max_bytes:]
        text = data.decode("utf-8", errors="ignore")
        lines = [ln for ln in text.splitlines() if needle in ln]
        return lines[-1] if lines else None
    except Exception:
        return None


def _auto_run_id() -> str | None:
    try:
        if RUNNER_META.exists():
            obj = json.loads(RUNNER_META.read_text(encoding="utf-8"))
            rid = str((obj or {}).get("run_id") or "").strip()
            if rid:
                return rid
    except Exception:
        pass
    ln = _tail_last_matching(LOG_BOT, "run_id=")
    if not ln:
        return None
    try:
        parts = ln.split("run_id=", 1)
        if len(parts) != 2:
            return None
        rid = parts[1].strip()
        # strip rich tags if present
        rid = rid.replace("[/dim]", "").strip()
        return rid or None
    except Exception:
        return None


def _auto_db_path() -> Path:
    try:
        if RUNNER_META.exists():
            obj = json.loads(RUNNER_META.read_text(encoding="utf-8"))
            db_raw = str((obj or {}).get("db") or "").strip()
            if db_raw:
                p = Path(db_raw)
                if not p.is_absolute():
                    p = BASE / p
                if p.exists():
                    return p
    except Exception:
        pass
    return DB_DEFAULT


def _parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        # trades.exit_timestamp is stored as isoformat() without tz
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def _safe_float(x: Any) -> float | None:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def _safe_int(x: Any) -> int | None:
    try:
        if x is None:
            return None
        return int(x)
    except Exception:
        return None


@dataclass
class TradeRow:
    trade_id: int
    mint: str
    symbol: str
    exit_reason: str
    pnl_usd: float
    pnl_pct: float
    amount_usd: float
    exit_timestamp: str
    metadata: dict[str, Any]


@dataclass
class TradeCluster:
    mint: str
    symbol: str
    start_ts: datetime | None
    end_ts: datetime | None
    approx_entry_epoch: float | None
    trade_count: int = 0
    pnl_usd: float = 0.0
    amount_usd: float = 0.0
    wins: int = 0
    exit_reasons: set[str] | None = None


def _load_trades(db_path: Path) -> list[TradeRow]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    out: list[TradeRow] = []
    for r in cur.execute("SELECT * FROM trades ORDER BY exit_timestamp DESC").fetchall():
        md: dict[str, Any] = {}
        try:
            raw = (r["metadata"] or "").strip()
            if raw:
                md = json.loads(raw)
                if not isinstance(md, dict):
                    md = {}
        except Exception:
            md = {}
        out.append(
            TradeRow(
                trade_id=int(r["trade_id"]),
                mint=str(r["mint"] or ""),
                symbol=str(r["symbol"] or ""),
                exit_reason=str(r["exit_reason"] or ""),
                pnl_usd=float(r["pnl_usd"] or 0.0),
                pnl_pct=float(r["pnl_pct"] or 0.0),
                amount_usd=float(r["amount_usd"] or 0.0),
                exit_timestamp=str(r["exit_timestamp"] or ""),
                metadata=md,
            )
        )
    return out


def _summ(xs: Iterable[float]) -> tuple[float, float, float]:
    xs = [float(x) for x in xs]
    if not xs:
        return 0.0, 0.0, 0.0
    xs2 = sorted(xs)
    n = len(xs2)
    mean = sum(xs2) / n
    med = xs2[n // 2] if n % 2 else (xs2[n // 2 - 1] + xs2[n // 2]) / 2
    return mean, med, sum(xs2)


def _fmt_num(x: float | None) -> str:
    if x is None:
        return "n/a"
    if abs(x) >= 1000:
        return f"{x:,.0f}"
    if abs(x) >= 100:
        return f"{x:,.1f}"
    return f"{x:,.2f}"


def _entry_anchor_epoch(tr: TradeRow) -> float | None:
    dt = _parse_iso(tr.exit_timestamp)
    hold_s = _safe_float(tr.metadata.get("hold_time_sec"))
    if dt is None or hold_s is None or hold_s < 0:
        return None
    return dt.timestamp() - hold_s


def _cluster_trades(
    run_trades: list[TradeRow],
    *,
    entry_tolerance_sec: int = 180,
    gap_fallback_sec: int = 900,
) -> list[TradeCluster]:
    """Group leg-level exits into approximate position clusters.

    Primary key is (mint, reconstructed entry time) where entry time is inferred as
    exit_timestamp - hold_time_sec. If hold_time is missing, fallback to mint +
    close-by exit timestamps.
    """
    clusters_by_mint: dict[str, list[TradeCluster]] = defaultdict(list)

    def _sort_key(t: TradeRow) -> datetime:
        return _parse_iso(t.exit_timestamp) or datetime.min

    for tr in sorted(run_trades, key=_sort_key):
        mint = tr.mint or "UNKNOWN_MINT"
        symbol = tr.symbol or mint[:8]
        dt = _parse_iso(tr.exit_timestamp)
        anchor = _entry_anchor_epoch(tr)
        bucket = clusters_by_mint[mint]

        chosen: TradeCluster | None = None

        # Best match: same mint + near-identical reconstructed entry timestamp.
        if anchor is not None:
            best_dist: float | None = None
            for cl in bucket:
                if cl.approx_entry_epoch is None:
                    continue
                dist = abs(cl.approx_entry_epoch - anchor)
                if dist <= float(entry_tolerance_sec) and (best_dist is None or dist < best_dist):
                    best_dist = dist
                    chosen = cl

        # Fallback only when anchor is unavailable (legacy rows).
        if chosen is None and anchor is None and dt is not None and bucket:
            last = bucket[-1]
            if last.end_ts is not None:
                gap = (dt - last.end_ts).total_seconds()
                if 0 <= gap <= float(gap_fallback_sec):
                    chosen = last

        if chosen is None:
            chosen = TradeCluster(
                mint=mint,
                symbol=symbol,
                start_ts=dt,
                end_ts=dt,
                approx_entry_epoch=anchor,
                exit_reasons=set(),
            )
            bucket.append(chosen)

        chosen.trade_count += 1
        chosen.pnl_usd += float(tr.pnl_usd or 0.0)
        chosen.amount_usd += float(tr.amount_usd or 0.0)
        if float(tr.pnl_usd or 0.0) > 0:
            chosen.wins += 1
        if chosen.exit_reasons is None:
            chosen.exit_reasons = set()
        chosen.exit_reasons.add(str(tr.exit_reason or "UNKNOWN"))
        if dt is not None:
            if chosen.start_ts is None or dt < chosen.start_ts:
                chosen.start_ts = dt
            if chosen.end_ts is None or dt > chosen.end_ts:
                chosen.end_ts = dt

    out: list[TradeCluster] = []
    for xs in clusters_by_mint.values():
        out.extend(xs)
    out.sort(key=lambda c: c.end_ts or datetime.min, reverse=True)
    return out


def _report(
    run_id: str,
    trades: list[TradeRow],
    *,
    hours: float,
    cluster_entry_tolerance_sec: int = 180,
    cluster_gap_fallback_sec: int = 900,
) -> str:
    cutoff = datetime.now() - timedelta(hours=float(hours))

    run_trades: list[TradeRow] = []
    for tr in trades:
        rid = str(tr.metadata.get("run_id") or "").strip()
        if rid != run_id:
            continue
        dt = _parse_iso(tr.exit_timestamp)
        if dt and dt < cutoff:
            continue
        run_trades.append(tr)

    n = len(run_trades)
    wins = sum(1 for t in run_trades if t.pnl_usd > 0)
    pnl = sum(t.pnl_usd for t in run_trades)
    wr = (wins / n * 100.0) if n else 0.0
    clusters = _cluster_trades(
        run_trades,
        entry_tolerance_sec=int(cluster_entry_tolerance_sec),
        gap_fallback_sec=int(cluster_gap_fallback_sec),
    )
    cn = len(clusters)
    c_wins = sum(1 for c in clusters if c.pnl_usd > 0)
    c_wr = (c_wins / cn * 100.0) if cn else 0.0
    c_trade_mean = (sum(c.trade_count for c in clusters) / cn) if cn else 0.0
    dominant_cluster_pnl_share = 0.0
    dominant_cluster_leg_share = 0.0
    if clusters:
        try:
            dominant = max(clusters, key=lambda c: abs(float(c.pnl_usd or 0.0)))
            tot_abs_pnl = sum(abs(float(c.pnl_usd or 0.0)) for c in clusters)
            if tot_abs_pnl > 0:
                dominant_cluster_pnl_share = abs(float(dominant.pnl_usd or 0.0)) / tot_abs_pnl
            if n > 0:
                dominant_cluster_leg_share = float(dominant.trade_count or 0) / float(n)
        except Exception:
            dominant_cluster_pnl_share = 0.0
            dominant_cluster_leg_share = 0.0

    by_reason: dict[str, list[TradeRow]] = defaultdict(list)
    for t in run_trades:
        by_reason[t.exit_reason or "UNKNOWN"].append(t)

    feature_keys = [
        "market_cap_entry",
        "liquidity_entry",
        "price_change_5m_entry",
        "txns_5m_entry",
        "buys_5m_entry",
        "sells_5m_entry",
        "volume_5m_entry",
        "hold_time_sec",
    ]

    winners = [t for t in run_trades if t.pnl_usd > 0]
    losers = [t for t in run_trades if t.pnl_usd <= 0]

    def _feat_vals(ts: list[TradeRow], k: str) -> list[float]:
        out2: list[float] = []
        for t in ts:
            v = t.metadata.get(k)
            if k.endswith("_entry") or k.endswith("_sec"):
                fv = _safe_float(v)
                if fv is not None:
                    out2.append(fv)
            else:
                fv = _safe_float(v)
                if fv is not None:
                    out2.append(fv)
        return out2

    lines: list[str] = []
    lines.append(f"# Meme Run Report: `{run_id}`")
    lines.append("")
    lines.append(f"- Window: last {hours:g}h")
    lines.append(f"- Trades: {n}")
    lines.append(f"- Win rate: {wr:.1f}% ({wins}W/{n-wins}L)")
    lines.append(f"- PnL: ${pnl:+.2f}")
    lines.append(f"- Position clusters (normalized): {cn}")
    lines.append(f"- Cluster win rate: {c_wr:.1f}% ({c_wins}W/{cn-c_wins}L)")
    lines.append(f"- Avg exits per cluster: {c_trade_mean:.2f}")
    lines.append(f"- Dominant cluster |abs(PnL)| share: {dominant_cluster_pnl_share*100.0:.1f}%")
    lines.append(f"- Dominant cluster leg share: {dominant_cluster_leg_share*100.0:.1f}%")
    lines.append("")

    if not run_trades:
        return "\n".join(lines) + "\n"

    # Sanity: entry market-cap availability + floors (helps catch unknown-mcap trading regressions).
    try:
        mcap_total = 0
        mcap_unknown = 0
        below_10k = 0
        below_25k = 0
        mcaps: list[float] = []
        for t in run_trades:
            if "market_cap_entry" not in t.metadata:
                continue
            mcap_total += 1
            mv = _safe_float(t.metadata.get("market_cap_entry")) or 0.0
            if mv <= 0:
                mcap_unknown += 1
                continue
            mcaps.append(mv)
            if mv < 10_000:
                below_10k += 1
            if mv < 25_000:
                below_25k += 1
        lines.append("## MCap Sanity")
        lines.append(f"- trades_with_mcap: {mcap_total}")
        lines.append(f"- unknown_or_zero_mcap: {mcap_unknown}")
        lines.append(f"- below_10k_mcap: {below_10k}")
        lines.append(f"- below_25k_mcap: {below_25k}")
        if mcaps:
            s = sorted(mcaps)
            lines.append(f"- mcap_min_med_max: ${s[0]:,.0f} / ${s[len(s)//2]:,.0f} / ${s[-1]:,.0f}")
        lines.append("")
    except Exception:
        pass

    lines.append("## Exit Reasons")
    for reason, xs in sorted(by_reason.items(), key=lambda kv: sum(t.pnl_usd for t in kv[1])):
        rpnl = sum(t.pnl_usd for t in xs)
        lines.append(f"- `{reason}`: n={len(xs)} pnl=${rpnl:+.2f}")
    lines.append("")

    # Quick mcap cohort view helps isolate where edge is concentrated.
    try:
        buckets = [
            (0.0, 10_000.0, "0-10k"),
            (10_000.0, 15_000.0, "10-15k"),
            (15_000.0, 25_000.0, "15-25k"),
            (25_000.0, 50_000.0, "25-50k"),
            (50_000.0, 100_000.0, "50-100k"),
            (100_000.0, float("inf"), "100k+"),
        ]
        bstats: dict[str, dict[str, float]] = {
            lbl: {"n": 0.0, "wins": 0.0, "pnl": 0.0} for _, _, lbl in buckets
        }
        unknown = {"n": 0.0, "wins": 0.0, "pnl": 0.0}
        for t in run_trades:
            mv = _safe_float((t.metadata or {}).get("market_cap_entry")) or 0.0
            slot = None
            if mv > 0:
                for lo, hi, lbl in buckets:
                    if lo <= mv < hi:
                        slot = bstats[lbl]
                        break
            if slot is None:
                slot = unknown
            slot["n"] += 1.0
            slot["pnl"] += float(t.pnl_usd or 0.0)
            if float(t.pnl_usd or 0.0) > 0:
                slot["wins"] += 1.0
        lines.append("## MCap Cohorts")
        for _, _, lbl in buckets:
            d = bstats[lbl]
            n_b = int(d["n"])
            if n_b <= 0:
                continue
            wr_b = (d["wins"] / d["n"] * 100.0) if d["n"] > 0 else 0.0
            lines.append(f"- `{lbl}`: n={n_b} wr={wr_b:.1f}% pnl=${d['pnl']:+.2f}")
        if int(unknown["n"]) > 0:
            wr_u = (unknown["wins"] / unknown["n"] * 100.0) if unknown["n"] > 0 else 0.0
            lines.append(f"- `unknown`: n={int(unknown['n'])} wr={wr_u:.1f}% pnl=${unknown['pnl']:+.2f}")
        lines.append("")
    except Exception:
        pass

    lines.append("## Position-Normalized View")
    if not clusters:
        lines.append("- n/a")
        lines.append("")
    else:
        lines.append("- Cluster key: mint + reconstructed entry time (`exit_ts - hold_time_sec`).")
        lines.append("- Useful when one position exits in multiple legs (TP0/TP1/trailing/etc).")
        lines.append("")
        lines.append("Top cluster winners:")
        cluster_winners = sorted([c for c in clusters if c.pnl_usd > 0], key=lambda x: x.pnl_usd, reverse=True)[:10]
        if cluster_winners:
            for c in cluster_winners:
                rs = ", ".join(sorted(c.exit_reasons or set()))
                lines.append(
                    f"- `{c.symbol}` legs={c.trade_count} pnl=${c.pnl_usd:+.2f} reasons=`{rs}`"
                )
        else:
            lines.append("- n/a")
        lines.append("Top cluster losers:")
        cluster_losers = sorted([c for c in clusters if c.pnl_usd < 0], key=lambda x: x.pnl_usd)[:10]
        if cluster_losers:
            for c in cluster_losers:
                rs = ", ".join(sorted(c.exit_reasons or set()))
                lines.append(
                    f"- `{c.symbol}` legs={c.trade_count} pnl=${c.pnl_usd:+.2f} reasons=`{rs}`"
                )
        else:
            lines.append("- n/a")
        lines.append("")

    lines.append("## Top Trades")
    top_win = sorted([t for t in run_trades if t.pnl_usd > 0], key=lambda t: t.pnl_usd, reverse=True)[:10]
    top_lose = sorted([t for t in run_trades if t.pnl_usd < 0], key=lambda t: t.pnl_usd)[:10]
    lines.append("Winners:")
    if top_win:
        for t in top_win:
            lines.append(f"- `{t.symbol}` pnl=${t.pnl_usd:+.2f} ({t.pnl_pct:+.1f}%) reason=`{t.exit_reason}`")
    else:
        lines.append("- n/a")
    lines.append("Losers:")
    if top_lose:
        for t in top_lose:
            lines.append(f"- `{t.symbol}` pnl=${t.pnl_usd:+.2f} ({t.pnl_pct:+.1f}%) reason=`{t.exit_reason}`")
    else:
        lines.append("- n/a")
    lines.append("")

    lines.append("## Entry Feature Drift (Winners vs Losers)")
    for k in feature_keys:
        wv = _feat_vals(winners, k)
        lv = _feat_vals(losers, k)
        w_mean, w_med, _ = _summ(wv)
        l_mean, l_med, _ = _summ(lv)
        if not (wv or lv):
            continue
        lines.append(f"- `{k}`: win_mean={_fmt_num(w_mean)} win_med={_fmt_num(w_med)} lose_mean={_fmt_num(l_mean)} lose_med={_fmt_num(l_med)}")
    lines.append("")

    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="", help="Path to positions.db (auto-detects active runner DB when omitted)")
    ap.add_argument("--hours", type=float, default=2.0, help="Lookback window for run trades")
    ap.add_argument("--run-id", default="", help="Run id to report on")
    ap.add_argument("--auto", action="store_true", help="Auto-detect run id from bot log")
    ap.add_argument("--out", default="", help="Optional markdown output path")
    ap.add_argument(
        "--cluster-entry-tolerance-sec",
        type=int,
        default=180,
        help="Tolerance when matching reconstructed entry anchors for same position clustering",
    )
    ap.add_argument(
        "--cluster-gap-fallback-sec",
        type=int,
        default=900,
        help="Fallback max exit-time gap to cluster legacy rows missing hold_time_sec",
    )
    args = ap.parse_args()

    db_path = Path(args.db).expanduser() if str(args.db).strip() else _auto_db_path()
    trades = _load_trades(db_path)

    rid = (args.run_id or "").strip()
    if args.auto and not rid:
        rid = _auto_run_id() or ""
    if not rid:
        print("No run id provided and --auto could not detect one.")
        return 2

    md = _report(
        rid,
        trades,
        hours=float(args.hours),
        cluster_entry_tolerance_sec=int(args.cluster_entry_tolerance_sec),
        cluster_gap_fallback_sec=int(args.cluster_gap_fallback_sec),
    )
    print(md, end="")

    if args.out:
        outp = Path(args.out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(md, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
