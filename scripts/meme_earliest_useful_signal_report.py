#!/usr/bin/env python3
"""Identify the earliest useful signal per mint from signal outcomes.

Purpose:
- collapse repeated signal snapshots into mint-level progress
- find the first signal that still led to a meaningful move
- separate "winner existed" from "this was an actionable early signal"
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
DEFAULT_IN = BASE / "data" / "signal_outcomes.jsonl"
DEFAULT_OUT_JSON = BASE / "data" / "meme_reports" / "earliest_useful_signal_report.json"
DEFAULT_OUT_MD = BASE / "data" / "meme_reports" / "earliest_useful_signal_report.md"


def _to_float(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        out = float(value)
        if math.isfinite(out):
            return out
    except Exception:
        return None
    return None


def _signal_key(row: dict[str, Any]) -> str | None:
    raw = row.get("signal_key")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    mint = str(row.get("mint") or "").strip()
    signal_ts = _to_float(row.get("signal_ts"))
    signal_source = str(row.get("signal_source") or "").strip().lower()
    if mint and signal_ts is not None:
        return f"{mint}|{signal_ts:.6f}|{signal_source}"
    return None


def _max_ret(rets: dict[int, float], horizon_s: int) -> float | None:
    vals = [ret for hz, ret in rets.items() if int(hz) <= int(horizon_s)]
    if not vals:
        return None
    return max(vals)


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100.0:.1f}%"


def _median(rows: list[float]) -> float | None:
    vals = [float(v) for v in rows if v is not None and math.isfinite(float(v))]
    if not vals:
        return None
    return float(statistics.median(vals))


def _extract_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    return {
        "mint": str(row.get("mint") or "").strip(),
        "symbol": str((metrics.get("symbol") or row.get("symbol") or "")).strip() or "n/a",
        "signal_ts": float(row.get("signal_ts") or 0.0),
        "signal_source": str(row.get("signal_source") or metrics.get("source") or "").strip().lower() or "unknown",
        "mcap0": _to_float(row.get("mcap0") if row.get("mcap0") is not None else row.get("marketcap0")),
        "liq0": _to_float(row.get("liq0")),
        "pair_age_min0": _to_float(row.get("pair_age_min0")),
        "mom5m0": _to_float(metrics.get("price_change_5m") if metrics.get("price_change_5m") is not None else metrics.get("momentum_5m_pct")),
        "hits0": int(row.get("hits0") or 0),
        "buys0": int(row.get("buys0") or 0),
        "uniq0": int(row.get("uniq0") or 0),
        "net_sol_in0": _to_float(row.get("net_sol_in0")),
        "mover_pattern0": str(row.get("mover_pattern0") or metrics.get("mover_pattern") or "").strip().lower() or "unknown",
        "url": str(metrics.get("url") or "").strip(),
    }


def load_signals(path: Path, since_ts: float) -> dict[str, list[dict[str, Any]]]:
    by_mint: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            signal_ts = _to_float(row.get("signal_ts"))
            if signal_ts is None or signal_ts < since_ts:
                continue
            mint = str(row.get("mint") or "").strip()
            key = _signal_key(row)
            if not mint or not key:
                continue
            grp = by_mint[mint].setdefault(
                key,
                {
                    "snapshot": _extract_snapshot(row),
                    "rets": {},
                },
            )
            hz = int(row.get("horizon_s") or 0)
            ret = _to_float(row.get("ret"))
            if hz > 0 and ret is not None:
                grp["rets"][hz] = ret
    out: dict[str, list[dict[str, Any]]] = {}
    for mint, items in by_mint.items():
        rows: list[dict[str, Any]] = []
        for key, item in items.items():
            snap = dict(item["snapshot"])
            rets = dict(item["rets"])
            snap["signal_key"] = key
            snap["ret_300s"] = _to_float(rets.get(300))
            snap["ret_900s"] = _to_float(rets.get(900))
            snap["ret_1800s"] = _to_float(rets.get(1800))
            snap["max_ret_300s"] = _max_ret(rets, 300)
            snap["max_ret_900s"] = _max_ret(rets, 900)
            snap["max_ret_1800s"] = _max_ret(rets, 1800)
            snap["max_ret_3600s"] = _max_ret(rets, 3600)
            snap["max_ret_all"] = max(rets.values()) if rets else None
            rows.append(snap)
        rows.sort(key=lambda r: float(r.get("signal_ts") or 0.0))
        out[mint] = rows
    return out


def first_match(rows: list[dict[str, Any]], field: str, threshold: float) -> dict[str, Any] | None:
    for row in rows:
        value = _to_float(row.get(field))
        if value is not None and value >= threshold:
            return row
    return None


def summarize(rows_by_mint: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    cohort_25_15m: list[dict[str, Any]] = []
    cohort_50_15m: list[dict[str, Any]] = []
    cohort_100_30m: list[dict[str, Any]] = []
    mint_rows: list[dict[str, Any]] = []
    first_source = Counter()
    useful_source = Counter()

    for mint, rows in rows_by_mint.items():
        first = rows[0]
        early_25 = first_match(rows, "max_ret_900s", 0.25)
        early_50 = first_match(rows, "max_ret_900s", 0.50)
        early_100 = first_match(rows, "max_ret_1800s", 1.00)
        best = max(rows, key=lambda r: float(r.get("max_ret_all") or -1.0))
        first_source[str(first.get("signal_source") or "unknown")] += 1
        if early_50:
            useful_source[str(early_50.get("signal_source") or "unknown")] += 1
            cohort_50_15m.append(early_50)
        if early_25:
            cohort_25_15m.append(early_25)
        if early_100:
            cohort_100_30m.append(early_100)
        mint_rows.append(
            {
                "mint": mint,
                "symbol": first.get("symbol") or "n/a",
                "first_signal": first,
                "earliest_25_15m": early_25,
                "earliest_50_15m": early_50,
                "earliest_100_30m": early_100,
                "best_signal": best,
                "signal_count": len(rows),
            }
        )

    def cohort_stats(cohort: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "n": len(cohort),
            "median_mcap0": _median([_to_float(r.get("mcap0")) for r in cohort]),
            "median_age0": _median([_to_float(r.get("pair_age_min0")) for r in cohort]),
            "median_mom5m0": _median([_to_float(r.get("mom5m0")) for r in cohort]),
            "median_hits0": _median([_to_float(r.get("hits0")) for r in cohort]),
            "median_net_sol_in0": _median([_to_float(r.get("net_sol_in0")) for r in cohort]),
            "source_counts": dict(Counter(str(r.get("signal_source") or "unknown") for r in cohort)),
            "pattern_counts": dict(Counter(str(r.get("mover_pattern0") or "unknown") for r in cohort)),
        }

    mint_rows.sort(
        key=lambda row: (
            1 if row.get("earliest_100_30m") else 0,
            1 if row.get("earliest_50_15m") else 0,
            float((row.get("best_signal") or {}).get("max_ret_all") or -1.0),
        ),
        reverse=True,
    )

    return {
        "summary": {
            "unique_mints": len(rows_by_mint),
            "first_signal_source_counts": dict(first_source),
            "earliest_50_15m_source_counts": dict(useful_source),
        },
        "cohorts": {
            "earliest_25_15m": cohort_stats(cohort_25_15m),
            "earliest_50_15m": cohort_stats(cohort_50_15m),
            "earliest_100_30m": cohort_stats(cohort_100_30m),
        },
        "top_mints": mint_rows[:25],
    }


def write_md(path: Path, report: dict[str, Any], *, since_hours: float) -> None:
    s = report["summary"]
    c25 = report["cohorts"]["earliest_25_15m"]
    c50 = report["cohorts"]["earliest_50_15m"]
    c100 = report["cohorts"]["earliest_100_30m"]
    lines = [
        "# Earliest Useful Signal Report",
        "",
        f"- Window: last `{since_hours:.0f}h`",
        f"- Unique mints: `{int(s.get('unique_mints') or 0)}`",
        "",
        "## Cohort Sizes",
        "",
        f"- Earliest signal still leading to `+25% within 15m`: `{int(c25.get('n') or 0)}`",
        f"- Earliest signal still leading to `+50% within 15m`: `{int(c50.get('n') or 0)}`",
        f"- Earliest signal still leading to `+100% within 30m`: `{int(c100.get('n') or 0)}`",
        "",
        "## Earliest `+50% within 15m` Cohort",
        "",
        f"- Median mcap0: `{float(c50.get('median_mcap0') or 0.0):.0f}`",
        f"- Median age0: `{float(c50.get('median_age0') or 0.0):.1f} min`",
        f"- Median mom5m0: `{float(c50.get('median_mom5m0') or 0.0):.2f}%`",
        f"- Median hits0: `{float(c50.get('median_hits0') or 0.0):.0f}`",
        f"- Median net_sol_in0: `{float(c50.get('median_net_sol_in0') or 0.0):.2f}`",
        "",
        "Source counts:",
    ]
    for k, v in sorted((c50.get("source_counts") or {}).items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"- `{k}`: `{v}`")
    lines.extend(["", "Pattern counts:"])
    for k, v in sorted((c50.get("pattern_counts") or {}).items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"- `{k}`: `{v}`")
    lines.extend([
        "",
        "## Top Mints",
        "",
        "| Symbol | Mint | Signals | First Source | Earliest +50%/15m | Earliest +100%/30m | Best All |",
        "|---|---|---:|---|---|---|---:|",
    ])
    for row in report.get("top_mints") or []:
        first = row.get("first_signal") or {}
        e50 = row.get("earliest_50_15m") or {}
        e100 = row.get("earliest_100_30m") or {}
        best = row.get("best_signal") or {}
        lines.append(
            f"| {row.get('symbol') or 'n/a'} | `{row.get('mint')}` | {int(row.get('signal_count') or 0)} | "
            f"`{first.get('signal_source') or 'n/a'}` | "
            f"`{e50.get('signal_source') or 'n/a'}` @ {_fmt_pct(_to_float(e50.get('max_ret_900s')))} | "
            f"`{e100.get('signal_source') or 'n/a'}` @ {_fmt_pct(_to_float(e100.get('max_ret_1800s')))} | "
            f"{_fmt_pct(_to_float(best.get('max_ret_all')))} |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(DEFAULT_IN))
    ap.add_argument("--since-hours", type=float, default=24.0)
    ap.add_argument("--out-json", default=str(DEFAULT_OUT_JSON))
    ap.add_argument("--out-md", default=str(DEFAULT_OUT_MD))
    args = ap.parse_args()

    src = Path(args.file)
    if not src.exists():
        raise SystemExit(f"missing file: {src}")

    rows_by_mint = load_signals(src, time.time() - float(args.since_hours) * 3600.0)
    report = summarize(rows_by_mint)
    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_md(out_md, report, since_hours=float(args.since_hours))
    print(f"wrote {out_json}")
    print(f"wrote {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
