#!/usr/bin/env python3
"""Run-scoped winner-miss report.

Tracks mints that pass prequote, then identifies where they get blocked next.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from meme_run_id_utils import auto_run_id


def _load_traded_and_open_mints(db_path: Path, run_id: str) -> tuple[set[str], set[str]]:
    traded: set[str] = set()
    open_pos: set[str] = set()
    if not db_path.exists():
        return traded, open_pos
    con = sqlite3.connect(str(db_path))
    cur = con.cursor()
    try:
        cur.execute("SELECT mint, metadata FROM trades")
        for mint, metadata in cur.fetchall():
            m = str(mint or "").strip()
            if not m:
                continue
            try:
                md = json.loads(metadata or "{}")
                rid = str((md or {}).get("run_id") or "").strip()
            except Exception:
                rid = ""
            if run_id and rid != run_id:
                continue
            traded.add(m)
        cur.execute("SELECT mint, metadata FROM positions WHERE status='open'")
        for mint, metadata in cur.fetchall():
            m = str(mint or "").strip()
            if not m:
                continue
            try:
                md = json.loads(metadata or "{}")
                rid = str((md or {}).get("run_id") or "").strip()
            except Exception:
                rid = ""
            if run_id and rid != run_id:
                continue
            open_pos.add(m)
    finally:
        con.close()
    return traded, open_pos


def _write_md(path: Path, obj: dict[str, Any]) -> None:
    lines = [
        "# Meme Winner Miss Report",
        "",
        f"- Generated: `{obj.get('generated_at')}`",
        f"- Run id: `{obj.get('run_id') or 'all'}`",
        f"- Lookback minutes: `{obj.get('since_minutes')}`",
        f"- Prequote-pass mints: `{obj.get('prequote_pass_mints', 0)}`",
        f"- Converted to trades: `{obj.get('converted_to_trades', 0)}`",
        f"- Still open positions: `{obj.get('still_open_positions', 0)}`",
        f"- Pending (no downstream reject/trade): `{obj.get('pending', 0)}`",
        "",
        "## Downstream Rejects",
        "| reject_kind | mints |",
        "|---|---:|",
    ]
    rej = obj.get("downstream_rejects", {})
    if isinstance(rej, dict) and rej:
        for k, v in sorted(rej.items(), key=lambda kv: kv[1], reverse=True):
            lines.append(f"| {k} | {int(v)} |")
    else:
        lines.append("| (none) | 0 |")

    examples = obj.get("examples", [])
    lines.append("")
    lines.append("## Examples")
    if not examples:
        lines.append("- (none)")
    else:
        for ex in examples[:12]:
            lines.append(
                "- "
                + f"mint=`{ex.get('mint')}` stage=`{ex.get('stage')}` "
                + f"reject=`{ex.get('reject_kind') or ''}` score=`{ex.get('signal_score')}` "
                + f"net_sol=`{ex.get('net_sol_in')}` uniq=`{ex.get('unique_buyers')}`"
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug-file", default="data/meme_signal_debug.jsonl")
    ap.add_argument("--db", default="data/positions.db")
    ap.add_argument("--since-minutes", type=int, default=240)
    ap.add_argument("--run-id", default="")
    ap.add_argument("--auto-run-id", action="store_true")
    ap.add_argument("--out-json", default="data/meme_winner_miss_report.json")
    ap.add_argument("--out-md", default="data/meme_winner_miss_report.md")
    args = ap.parse_args()

    run_id = str(args.run_id or "").strip()
    if args.auto_run_id and not run_id:
        run_id = str(auto_run_id() or "").strip()

    debug_path = Path(args.debug_file)
    if not debug_path.exists():
        print(f"missing: {debug_path}")
        return 1

    cutoff = time.time() - (float(args.since_minutes) * 60.0)
    lines = debug_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-200000:]

    by_mint: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            row = json.loads(ln)
        except Exception:
            continue
        try:
            ts = float(row.get("ts") or 0.0)
        except Exception:
            ts = 0.0
        if ts < cutoff:
            continue
        if run_id and str(row.get("run_id") or "").strip() != run_id:
            continue
        mint = str(row.get("mint") or "").strip()
        if not mint:
            continue
        by_mint[mint].append(row)

    traded_mints, open_mints = _load_traded_and_open_mints(Path(args.db), run_id=run_id)
    prequote_pass_mints = 0
    converted_to_trades = 0
    still_open_positions = 0
    pending = 0
    downstream_rejects: dict[str, int] = {}
    examples: list[dict[str, Any]] = []

    prequote_reject_kinds = {
        "reject_prequote_missing_demand",
        "reject_prequote_score",
        "reject_prequote_hits",
        "reject_prequote_buys",
        "reject_prequote_uniq",
        "reject_prequote_net",
    }

    for mint, evs in by_mint.items():
        evs_sorted = sorted(evs, key=lambda r: float(r.get("ts") or 0.0))
        pass_ev = next((e for e in evs_sorted if str(e.get("kind") or "") == "pass_prequote"), None)
        if not pass_ev:
            continue
        prequote_pass_mints += 1

        if mint in traded_mints:
            converted_to_trades += 1
            examples.append(
                {
                    "mint": mint,
                    "stage": "traded",
                    "reject_kind": "",
                    "signal_score": (pass_ev.get("m") or {}).get("score"),
                    "net_sol_in": (pass_ev.get("m") or {}).get("net_sol_in"),
                    "unique_buyers": (pass_ev.get("m") or {}).get("unique_buyers"),
                }
            )
            continue
        if mint in open_mints:
            still_open_positions += 1
            examples.append(
                {
                    "mint": mint,
                    "stage": "open",
                    "reject_kind": "",
                    "signal_score": (pass_ev.get("m") or {}).get("score"),
                    "net_sol_in": (pass_ev.get("m") or {}).get("net_sol_in"),
                    "unique_buyers": (pass_ev.get("m") or {}).get("unique_buyers"),
                }
            )
            continue

        pass_ts = float(pass_ev.get("ts") or 0.0)
        down_reject = None
        for ev in evs_sorted:
            try:
                ev_ts = float(ev.get("ts") or 0.0)
            except Exception:
                ev_ts = 0.0
            if ev_ts <= pass_ts:
                continue
            kind = str(ev.get("kind") or "")
            if not kind.startswith("reject_"):
                continue
            if kind in prequote_reject_kinds:
                continue
            down_reject = ev
            break

        if down_reject is None:
            pending += 1
            examples.append(
                {
                    "mint": mint,
                    "stage": "pending",
                    "reject_kind": "",
                    "signal_score": (pass_ev.get("m") or {}).get("score"),
                    "net_sol_in": (pass_ev.get("m") or {}).get("net_sol_in"),
                    "unique_buyers": (pass_ev.get("m") or {}).get("unique_buyers"),
                }
            )
            continue

        rk = str(down_reject.get("kind") or "reject_unknown")
        downstream_rejects[rk] = int(downstream_rejects.get(rk, 0) or 0) + 1
        examples.append(
            {
                "mint": mint,
                "stage": "rejected_after_prequote",
                "reject_kind": rk,
                "signal_score": (pass_ev.get("m") or {}).get("score"),
                "net_sol_in": (pass_ev.get("m") or {}).get("net_sol_in"),
                "unique_buyers": (pass_ev.get("m") or {}).get("unique_buyers"),
            }
        )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id or None,
        "since_minutes": int(args.since_minutes),
        "prequote_pass_mints": int(prequote_pass_mints),
        "converted_to_trades": int(converted_to_trades),
        "still_open_positions": int(still_open_positions),
        "pending": int(pending),
        "downstream_rejects": dict(sorted(downstream_rejects.items(), key=lambda kv: kv[1], reverse=True)),
        "examples": examples[:50],
    }

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_md(Path(args.out_md), report)
    print(f"wrote {out_json}")
    print(f"wrote {args.out_md}")
    print(
        f"prequote_pass={prequote_pass_mints} traded={converted_to_trades} "
        f"open={still_open_positions} pending={pending}"
    )
    if downstream_rejects:
        top_k, top_v = next(iter(sorted(downstream_rejects.items(), key=lambda kv: kv[1], reverse=True)))
        print(f"top_downstream_reject={top_k} n={top_v}")
    else:
        print("top_downstream_reject=none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

