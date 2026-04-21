#!/usr/bin/env python3
"""Audit the normalized meme launch signal tape.

This report is source-aware. It answers:
- which sources are feeding the tape
- which fields each source actually provides
- which fields are observed vs estimated
- which fields the trading stack should trust for ranking
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
load_dotenv(dotenv_path=str(BASE / ".env"), override=True)

SIGNALS_DEFAULT = str(
    Path(str(os.getenv("MEME_LAUNCH_SIGNALS_FILE") or "").strip() or BASE / "data" / "meme_launch_signals.jsonl")
)

CORE_FIELDS = [
    "market_cap",
    "liquidity",
    "price_change_5m",
    "price_change_1h",
    "hits",
    "buys",
    "sells",
    "unique_buyers",
    "net_sol_in",
    "top_buyer_share",
    "buy_sell_ratio",
    "pair_age_min",
    "mover_pattern",
    "buyer_wallets",
]

SOURCE_CONTRACT: dict[str, dict[str, Any]] = {
    "ws_logs": {
        "purpose": "Early on-chain Pump flow",
        "trust": {
            "observed": [
                "hits",
                "buys",
                "sells",
                "unique_buyers",
                "net_sol_in",
                "top_buyer_share",
                "buyer_wallets",
                "buy_accel",
                "t_first_sell_s",
            ],
            "estimated": [],
            "missing": ["market_cap", "liquidity", "pair_age_min", "price_change_5m", "buy_sell_ratio"],
        },
    },
    "dex_mover": {
        "purpose": "Dex market-state mover feed",
        "trust": {
            "observed": [
                "market_cap",
                "liquidity",
                "price_change_5m",
                "price_change_1h",
                "hits",
                "buys",
                "sells",
                "net_sol_in",
                "buy_sell_ratio",
                "pair_age_min",
                "mover_pattern",
            ],
            "estimated": ["unique_buyers", "top_buyer_share"],
            "missing": ["buyer_wallets"],
        },
    },
    "ds_sidecar": {
        "purpose": "DexScreener sidecar ranking feed",
        "trust": {
            "observed": [
                "market_cap",
                "liquidity",
                "price_change_5m",
                "price_change_1h",
                "hits",
                "buys",
                "sells",
                "net_sol_in",
                "buy_sell_ratio",
                "pair_age_min",
                "mover_pattern",
                "ds_score",
                "ds_breakout_readiness",
                "ds_relative_strength",
                "ds_risk_score",
            ],
            "estimated": ["unique_buyers", "top_buyer_share"],
            "missing": ["buyer_wallets"],
        },
    },
    "wallet_outlier": {
        "purpose": "Wallet-alpha overlay on top of early signals",
        "trust": {
            "observed": [
                "wallet_alpha_score",
                "wallet_alpha_confidence",
                "wallet_alpha_wallet",
                "wallet_alpha_origin",
                "wallet_alpha_signals_n",
                "wallet_alpha_outcomes_n",
            ],
            "estimated": [],
            "missing": [],
        },
    },
}


def _to_float(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)
    except Exception:
        return None


def _is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return len(value) > 0
    return True


def _row_source(obj: dict[str, Any]) -> str:
    metrics = obj.get("metrics") if isinstance(obj.get("metrics"), dict) else {}
    return str(metrics.get("source") or obj.get("source") or "unknown").strip() or "unknown"


def _iter_rows(path: Path, max_rows: int = 0) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    if max_rows > 0 and len(rows) > max_rows:
        rows = rows[-max_rows:]
    return rows


def build_report(rows: list[dict[str, Any]], recent_hours: float) -> dict[str, Any]:
    now = time.time()
    recent_cutoff = now - max(0.1, float(recent_hours)) * 3600.0

    source_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    recent_counts: Counter[str] = Counter()
    all_sources: Counter[str] = Counter()

    for obj in rows:
        src = _row_source(obj)
        source_rows[src].append(obj)
        all_sources[src] += 1
        ts = _to_float(obj.get("ts")) or 0.0
        if ts >= recent_cutoff:
            recent_counts[src] += 1

    sources_out: list[dict[str, Any]] = []
    for src, src_rows in sorted(source_rows.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        field_counts: Counter[str] = Counter()
        extra_field_counts: Counter[str] = Counter()
        estimated_unique = 0
        estimated_top = 0
        buyer_wallet_rows = 0
        scores: list[float] = []

        for obj in src_rows:
            metrics = obj.get("metrics") if isinstance(obj.get("metrics"), dict) else {}
            for field in CORE_FIELDS:
                if _is_present(metrics.get(field)):
                    field_counts[field] += 1
            for field, value in metrics.items():
                if field in CORE_FIELDS or field.endswith("_usd") or field.endswith("_pct"):
                    continue
                if _is_present(value):
                    extra_field_counts[field] += 1
            if bool(metrics.get("unique_buyers_estimated")):
                estimated_unique += 1
            if bool(metrics.get("top_buyer_share_estimated")):
                estimated_top += 1
            if _is_present(metrics.get("buyer_wallets")):
                buyer_wallet_rows += 1
            score = _to_float(obj.get("score"))
            if score is not None:
                scores.append(score)

        n = len(src_rows)
        contract = SOURCE_CONTRACT.get(src, {})
        trust = contract.get("trust", {}) if isinstance(contract, dict) else {}
        sources_out.append(
            {
                "source": src,
                "purpose": contract.get("purpose", ""),
                "rows": n,
                "rows_recent": int(recent_counts.get(src) or 0),
                "avg_score": round(sum(scores) / len(scores), 2) if scores else None,
                "field_coverage": {field: round((field_counts.get(field, 0) / n), 4) if n else 0.0 for field in CORE_FIELDS},
                "extra_fields": [name for name, _ in extra_field_counts.most_common(20)],
                "estimated_rows": {
                    "unique_buyers": estimated_unique,
                    "top_buyer_share": estimated_top,
                },
                "buyer_wallet_rows": buyer_wallet_rows,
                "trust_contract": {
                    "observed": list(trust.get("observed", [])),
                    "estimated": list(trust.get("estimated", [])),
                    "missing": list(trust.get("missing", [])),
                },
            }
        )

    return {
        "generated_at": now,
        "signals_file": SIGNALS_DEFAULT,
        "rows": len(rows),
        "recent_hours": recent_hours,
        "sources": sources_out,
        "source_counts": dict(all_sources),
        "source_counts_recent": dict(recent_counts),
        "core_fields": CORE_FIELDS,
    }


def _write_md(report: dict[str, Any], out_md: Path) -> None:
    lines: list[str] = []
    lines.append("# Meme Signal Tape Report")
    lines.append("")
    lines.append(f"- Signals: `{report['rows']}`")
    lines.append(f"- Recent window: `{report['recent_hours']}` hours")
    lines.append(f"- Tape: `{report['signals_file']}`")
    lines.append("")
    lines.append("## Sources")
    lines.append("")
    lines.append("| Source | Purpose | Rows | Recent | Avg Score |")
    lines.append("|---|---|---:|---:|---:|")
    for src in report["sources"]:
        avg = src["avg_score"]
        avg_txt = f"{avg:.2f}" if isinstance(avg, (int, float)) else "-"
        lines.append(
            f"| {src['source']} | {src['purpose'] or '-'} | {src['rows']} | {src['rows_recent']} | {avg_txt} |"
        )
    lines.append("")
    lines.append("## Core Field Coverage")
    lines.append("")
    for src in report["sources"]:
        lines.append(f"### {src['source']}")
        lines.append("")
        lines.append("| Field | Coverage |")
        lines.append("|---|---:|")
        for field, pct in src["field_coverage"].items():
            lines.append(f"| {field} | {pct*100:.1f}% |")
        lines.append("")
        trust = src["trust_contract"]
        lines.append(f"- Observed: `{', '.join(trust['observed']) or '-'}`")
        lines.append(f"- Estimated: `{', '.join(trust['estimated']) or '-'}`")
        lines.append(f"- Missing by design: `{', '.join(trust['missing']) or '-'}`")
        lines.append(
            f"- Estimated rows: unique_buyers=`{src['estimated_rows']['unique_buyers']}`, "
            f"top_buyer_share=`{src['estimated_rows']['top_buyer_share']}`"
        )
        lines.append(f"- Buyer wallet rows: `{src['buyer_wallet_rows']}`")
        if src["extra_fields"]:
            lines.append(f"- Extra fields: `{', '.join(src['extra_fields'][:12])}`")
        lines.append("")
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", default=SIGNALS_DEFAULT)
    ap.add_argument("--max-rows", type=int, default=0)
    ap.add_argument("--recent-hours", type=float, default=24.0)
    ap.add_argument("--out-json", default=str(BASE / "data" / "meme_reports" / "signal_tape_report.json"))
    ap.add_argument("--out-md", default=str(BASE / "data" / "meme_reports" / "signal_tape_report.md"))
    args = ap.parse_args()

    rows = _iter_rows(Path(args.signals), int(args.max_rows))
    report = build_report(rows, float(args.recent_hours))

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_md(report, Path(args.out_md))

    print(f"wrote {out_json}")
    print(f"wrote {args.out_md}")
    print(f"rows={report['rows']} sources={len(report['sources'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
