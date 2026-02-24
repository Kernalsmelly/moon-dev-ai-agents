#!/usr/bin/env python3
"""Estimate opportunity cost from recent signal rejections.

The report highlights which reject reasons are blocking high-demand candidates and
whether those blocked mints later appeared in winning trades.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from meme_run_id_utils import auto_run_id


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    val = str(ts).strip()
    if not val:
        return None
    # Unix seconds (float/int)
    try:
        fv = float(val)
        if fv > 1e9:
            return datetime.fromtimestamp(fv, tz=timezone.utc)
    except Exception:
        pass
    if val.endswith("Z"):
        val = val[:-1] + "+00:00"
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
    ):
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


def _load_trade_mints(db_path: Path, cutoff_dt: datetime, run_id: str = "") -> tuple[set[str], set[str]]:
    winning_mints: set[str] = set()
    traded_mints: set[str] = set()
    if not db_path.exists():
        return winning_mints, traded_mints
    con = sqlite3.connect(str(db_path))
    cur = con.cursor()
    try:
        cur.execute("SELECT mint, entry_timestamp, exit_timestamp, pnl_usd, metadata FROM trades")
        for mint, entry_ts, exit_ts, pnl_usd, metadata in cur.fetchall():
            m = str(mint or "").strip()
            if not m:
                continue
            dt = _parse_ts(exit_ts) or _parse_ts(entry_ts)
            if dt is None or dt < cutoff_dt:
                continue
            if run_id:
                try:
                    md = json.loads(metadata or "{}")
                    md_run_id = str((md or {}).get("run_id") or "").strip()
                except Exception:
                    md_run_id = ""
                if md_run_id != run_id:
                    continue
            traded_mints.add(m)
            try:
                if float(pnl_usd or 0.0) > 0:
                    winning_mints.add(m)
            except Exception:
                pass
    finally:
        con.close()
    return winning_mints, traded_mints


def _write_md(path: Path, obj: dict) -> None:
    rows = obj.get("rows", [])
    lines = [
        "# Meme Opportunity Cost Report",
        "",
        f"- Generated: `{obj.get('generated_at')}`",
        f"- Lookback minutes: `{obj.get('since_minutes')}`",
        f"- Events analyzed: `{obj.get('events_analyzed')}`",
        "",
        "| reject_kind | events | strong_events | strong_mints | blocked_to_trade_mints | blocked_to_win_mints |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            "| "
            + f"{r.get('kind')} | {r.get('events')} | {r.get('strong_events')} | "
            + f"{r.get('strong_mints')} | {r.get('blocked_to_trade_mints')} | {r.get('blocked_to_win_mints')} |"
        )
    if not rows:
        lines.append("| (none) | 0 | 0 | 0 | 0 | 0 |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug-file", default="data/meme_signal_debug.jsonl")
    ap.add_argument("--db", default="data/positions.db")
    ap.add_argument("--since-minutes", type=int, default=240)
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--run-id", default="")
    ap.add_argument("--auto-run-id", action="store_true", help="Auto-detect run id from bot log if --run-id is empty")
    ap.add_argument("--strong-min-hits", type=int, default=4)
    ap.add_argument("--strong-min-uniq", type=int, default=4)
    ap.add_argument("--strong-min-net-sol", type=float, default=2.5)
    ap.add_argument("--out-json", default="data/meme_opportunity_cost_report.json")
    ap.add_argument("--out-md", default="data/meme_opportunity_cost_report.md")
    args = ap.parse_args()

    debug_path = Path(args.debug_file)
    if not debug_path.exists():
        print(f"missing: {debug_path}")
        return 1

    cutoff_ts = time.time() - (args.since_minutes * 60.0)
    cutoff_dt = datetime.now(timezone.utc) - timedelta(minutes=args.since_minutes)
    run_id = str(args.run_id or "").strip()
    if args.auto_run_id and not run_id:
        run_id = str(auto_run_id() or "").strip()

    winning_mints, traded_mints = _load_trade_mints(Path(args.db), cutoff_dt, run_id=run_id)

    events = Counter()
    strong_events = Counter()
    mints_by_kind: dict[str, set[str]] = defaultdict(set)
    strong_mints_by_kind: dict[str, set[str]] = defaultdict(set)
    total_events = 0

    lines = debug_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-120000:]
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        try:
            ts = float(row.get("ts") or 0.0)
        except Exception:
            ts = 0.0
        if ts < cutoff_ts:
            continue
        if run_id and str(row.get("run_id") or "").strip() != run_id:
            continue
        kind = str(row.get("kind") or "").strip()
        if not kind.startswith("reject_"):
            continue
        total_events += 1
        events[kind] += 1
        mint = str(row.get("mint") or "").strip()
        if mint:
            mints_by_kind[kind].add(mint)

        m = row.get("m") if isinstance(row.get("m"), dict) else {}
        try:
            hits = int(m.get("hits") or 0)
        except Exception:
            hits = 0
        try:
            uniq = int(m.get("unique_buyers") or 0)
        except Exception:
            uniq = 0
        try:
            net_sol = float(m.get("net_sol_in") or 0.0)
        except Exception:
            net_sol = 0.0
        is_strong = (
            hits >= int(args.strong_min_hits)
            and uniq >= int(args.strong_min_uniq)
            and net_sol >= float(args.strong_min_net_sol)
        )
        if is_strong:
            strong_events[kind] += 1
            if mint:
                strong_mints_by_kind[kind].add(mint)

    ranked = sorted(events.keys(), key=lambda k: (strong_events[k], events[k]), reverse=True)
    rows = []
    for k in ranked[: max(1, int(args.top))]:
        strong_mints = strong_mints_by_kind.get(k, set())
        rows.append(
            {
                "kind": k,
                "events": int(events[k]),
                "strong_events": int(strong_events[k]),
                "unique_mints": int(len(mints_by_kind.get(k, set()))),
                "strong_mints": int(len(strong_mints)),
                "blocked_to_trade_mints": int(len(strong_mints & traded_mints)),
                "blocked_to_win_mints": int(len(strong_mints & winning_mints)),
            }
        )

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "since_minutes": int(args.since_minutes),
        "run_id": run_id or None,
        "events_analyzed": int(total_events),
        "strong_rule": {
            "min_hits": int(args.strong_min_hits),
            "min_unique_buyers": int(args.strong_min_uniq),
            "min_net_sol_in": float(args.strong_min_net_sol),
        },
        "rows": rows,
    }

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(out, indent=2), encoding="utf-8")
    _write_md(Path(args.out_md), out)

    print(f"wrote {out_json}")
    print(f"wrote {args.out_md}")
    if rows:
        top = rows[0]
        print(
            f"top_kind={top['kind']} events={top['events']} strong_events={top['strong_events']} "
            f"blocked_to_win={top['blocked_to_win_mints']}"
        )
    else:
        print("no reject events in window")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
