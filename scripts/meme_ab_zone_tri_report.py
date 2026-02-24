#!/usr/bin/env python3
"""Summarize tri-lane zone experiment (base vs match-only vs bypass-only)."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
META = BASE / "data" / "meme_ab_zone_tri_runner.json"


def _f(v: Any, d: float = 0.0) -> float:
    try:
        if v is None:
            return d
        return float(v)
    except Exception:
        return d


def _debug_summary(run_id: str, debug_file: Path, minutes: float) -> dict[str, Any]:
    out = {
        "events": 0,
        "pass_prequote": 0,
        "zone_match_passes": 0,
        "zone_bypass_passes": 0,
        "reject_total": 0,
        "top_rejects": [],
    }
    if not run_id or not debug_file.exists():
        return out
    cutoff = time.time() - (max(1.0, float(minutes)) * 60.0)
    rejects = Counter()
    with debug_file.open("r", encoding="utf-8") as fh:
        for ln in fh:
            try:
                obj = json.loads(ln)
            except Exception:
                continue
            if str(obj.get("run_id") or "").strip() != run_id:
                continue
            ts = _f(obj.get("ts"), 0.0)
            if ts > 0 and ts < cutoff:
                continue
            kind = str(obj.get("kind") or "")
            out["events"] = int(out["events"]) + 1
            if kind == "pass_prequote":
                out["pass_prequote"] = int(out["pass_prequote"]) + 1
            elif kind == "pass_winner_zone":
                out["zone_match_passes"] = int(out["zone_match_passes"]) + 1
            elif kind == "pass_winner_zone_bypass":
                out["zone_bypass_passes"] = int(out["zone_bypass_passes"]) + 1
            elif kind.startswith("reject_"):
                out["reject_total"] = int(out["reject_total"]) + 1
                rejects[kind] += 1
    out["top_rejects"] = rejects.most_common(6)
    return out


def _lane_summary(name: str, run_id: str, db_path: Path, debug_file: Path, debug_minutes: float) -> dict[str, Any]:
    out = {
        "name": name,
        "run_id": run_id,
        "db_path": str(db_path),
        "entries": 0,
        "open_positions_run": 0,
        "open_positions_total": 0,
        "trades": 0,
        "wins": 0,
        "winrate": 0.0,
        "pnl_usd": 0.0,
        "avg_pnl_usd": 0.0,
        "entry_attribution": {
            "counts": {"zone_match": 0, "zone_bypass": 0, "non_zone": 0},
            "pnl_usd": {"zone_match": 0.0, "zone_bypass": 0.0, "non_zone": 0.0},
            "expectancy_usd": {"zone_match": 0.0, "zone_bypass": 0.0, "non_zone": 0.0},
        },
        "debug": _debug_summary(run_id, debug_file, debug_minutes),
    }
    if not db_path.exists():
        return out

    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    try:
        rows = cur.execute("SELECT pnl_usd, metadata FROM trades").fetchall()
    except sqlite3.OperationalError:
        rows = []
    try:
        pos_rows = cur.execute("SELECT status, metadata FROM positions").fetchall()
    except sqlite3.OperationalError:
        pos_rows = []
    con.close()

    # Position-level coverage so we can distinguish "no exits yet" from "no entries".
    for r in pos_rows:
        status = str(r["status"] or "").strip().lower()
        try:
            md = json.loads(r["metadata"] or "{}")
        except Exception:
            md = {}
        rid = str((md or {}).get("run_id") or "").strip()
        if status == "open":
            out["open_positions_total"] = int(out["open_positions_total"]) + 1
        if run_id and rid != run_id:
            continue
        out["entries"] = int(out["entries"]) + 1
        if status == "open":
            out["open_positions_run"] = int(out["open_positions_run"]) + 1

    pnls: list[float] = []
    counts = out["entry_attribution"]["counts"]
    pnl_by = out["entry_attribution"]["pnl_usd"]
    for r in rows:
        try:
            md = json.loads(r["metadata"] or "{}")
        except Exception:
            md = {}
        rid = str((md or {}).get("run_id") or "").strip()
        if run_id and rid != run_id:
            continue
        pnl = _f(r["pnl_usd"], 0.0)
        pnls.append(pnl)
        zone_id = str((md or {}).get("winner_zone_id") or "").strip()
        zone_bypassed = bool((md or {}).get("winner_zone_bypassed", False))
        key = "zone_bypass" if zone_bypassed else ("zone_match" if zone_id else "non_zone")
        counts[key] = int(counts.get(key) or 0) + 1
        pnl_by[key] = float(pnl_by.get(key) or 0.0) + float(pnl)

    n = len(pnls)
    wins = sum(1 for x in pnls if x > 0)
    total = float(sum(pnls))
    out["trades"] = n
    out["wins"] = wins
    out["winrate"] = (float(wins) / float(n)) if n > 0 else 0.0
    out["pnl_usd"] = total
    out["avg_pnl_usd"] = (total / float(n)) if n > 0 else 0.0

    exp_by = out["entry_attribution"]["expectancy_usd"]
    for k in ("zone_match", "zone_bypass", "non_zone"):
        c = int(counts.get(k) or 0)
        p = float(pnl_by.get(k) or 0.0)
        exp_by[k] = (p / float(c)) if c > 0 else 0.0
    return out


def _fmt_usd(v: float) -> str:
    return f"${v:+.2f}"


def _fmt_pct(v: float) -> str:
    return f"{100.0 * v:.1f}%"


def _write_md(out: Path, summary: dict[str, Any]) -> None:
    lanes = summary["lanes"]
    lines: list[str] = []
    lines.append("# A/B Zone Tri Report")
    lines.append("")
    lines.append(f"- generated_at: {summary['generated_at']}")
    lines.append(f"- debug_window_min: {summary['debug_window_min']}")
    lines.append("")
    lines.append("## Lane Metrics")
    lines.append("")
    lines.append("| lane | entries | open(run) | open(total) | trades | winrate | pnl_usd | avg_pnl | debug_events | prequote | zone_match | zone_bypass | rejects |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for name in ("base", "match", "bypass"):
        x = lanes.get(name, {})
        d = x.get("debug", {})
        lines.append(
            f"| {name} | {int(x.get('entries') or 0)} | {int(x.get('open_positions_run') or 0)} | "
            f"{int(x.get('open_positions_total') or 0)} | {int(x.get('trades') or 0)} | {_fmt_pct(float(x.get('winrate') or 0.0))} | "
            f"{_fmt_usd(float(x.get('pnl_usd') or 0.0))} | {_fmt_usd(float(x.get('avg_pnl_usd') or 0.0))} | "
            f"{int(d.get('events') or 0)} | {int(d.get('pass_prequote') or 0)} | "
            f"{int(d.get('zone_match_passes') or 0)} | {int(d.get('zone_bypass_passes') or 0)} | "
            f"{int(d.get('reject_total') or 0)} |"
        )
    lines.append("")
    lines.append("## Entry-Type Expectancy (USD per Trade)")
    lines.append("")
    lines.append("| lane | zone_match | zone_bypass | non_zone |")
    lines.append("|---|---:|---:|---:|")
    for name in ("base", "match", "bypass"):
        exp = ((lanes.get(name) or {}).get("entry_attribution") or {}).get("expectancy_usd") or {}
        lines.append(
            f"| {name} | {_fmt_usd(float(exp.get('zone_match') or 0.0))} | "
            f"{_fmt_usd(float(exp.get('zone_bypass') or 0.0))} | {_fmt_usd(float(exp.get('non_zone') or 0.0))} |"
        )
    lines.append("")
    lines.append("## Top Rejects (Signal Debug)")
    lines.append("")
    for name in ("base", "match", "bypass"):
        d = (lanes.get(name) or {}).get("debug") or {}
        rej = d.get("top_rejects") or []
        if not rej:
            lines.append(f"- {name}: none")
            continue
        top = ", ".join(f"{k}={int(v)}" for k, v in rej)
        lines.append(f"- {name}: {top}")
    lines.append("")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--meta", default=str(META))
    ap.add_argument("--signal-debug", default=str(BASE / "data" / "meme_signal_debug.jsonl"))
    ap.add_argument("--signal-debug-minutes", type=float, default=180.0)
    ap.add_argument("--out-md", default=str(BASE / "data" / "meme_reports" / "ab_zone_tri_latest.md"))
    ap.add_argument("--out-json", default=str(BASE / "data" / "meme_reports" / "ab_zone_tri_latest.json"))
    args = ap.parse_args()

    meta = Path(args.meta)
    if not meta.exists():
        print(f"missing tri meta: {meta}")
        return 0
    obj = json.loads(meta.read_text(encoding="utf-8"))
    lanes = obj.get("lanes") if isinstance(obj, dict) else {}
    if not isinstance(lanes, dict):
        print("invalid tri meta lanes")
        return 0

    debug_file = Path(args.signal_debug)
    out_lanes: dict[str, Any] = {}
    for name in ("base", "match", "bypass"):
        lane = lanes.get(name) if isinstance(lanes.get(name), dict) else {}
        run_id = str(lane.get("run_id") or "").strip()
        db_rel = str(lane.get("db") or "")
        db_path = Path(db_rel)
        if db_rel and not db_path.is_absolute():
            db_path = BASE / db_path
        out_lanes[name] = _lane_summary(name, run_id, db_path, debug_file, float(args.signal_debug_minutes))

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "debug_window_min": float(args.signal_debug_minutes),
        "lanes": out_lanes,
    }

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    out_md = Path(args.out_md)
    _write_md(out_md, summary)
    print(f"wrote {out_md}")
    print(f"wrote {out_json}")
    for name in ("base", "match", "bypass"):
        x = out_lanes[name]
        print(
            f"{name}: entries={int(x['entries'])} open_run={int(x['open_positions_run'])} "
            f"trades={int(x['trades'])} wr={100.0*float(x['winrate']):.1f}% "
            f"pnl={_fmt_usd(float(x['pnl_usd']))} avg={_fmt_usd(float(x['avg_pnl_usd']))}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
