#!/usr/bin/env python3
"""Export anchor-level training rows for meme signal modeling."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import time
from collections import Counter
from pathlib import Path
from typing import Any

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
MODULE_PATH = BASE / "scripts" / "meme_persistent_rank_monitor.py"
OUT_CSV = BASE / "data" / "meme_reports" / "meme_anchor_dataset.csv"
OUT_JSON = BASE / "data" / "meme_reports" / "meme_anchor_dataset_summary.json"
OUT_MD = BASE / "data" / "meme_reports" / "meme_anchor_dataset_summary.md"


def _load_rank_module():
    spec = importlib.util.spec_from_file_location("meme_persistent_rank_monitor_module", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load module from {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def _label_useful(anchor: dict[str, Any]) -> bool:
    kind = str(anchor.get("anchor_kind") or "")
    return kind.startswith("earliest_useful:")


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100.0:.1f}%"


def _flatten_anchor(anchor: dict[str, Any]) -> dict[str, Any]:
    features = anchor.get("features") if isinstance(anchor.get("features"), dict) else {}
    row = {
        "mint": anchor.get("mint") or "",
        "symbol": anchor.get("symbol") or "n/a",
        "signal_ts": float(anchor.get("signal_ts") or 0.0),
        "signal_source": anchor.get("signal_source") or "unknown",
        "base_source_family": anchor.get("base_source_family") or "unknown",
        "source_family": anchor.get("source_family") or "unknown",
        "anchor_kind": anchor.get("anchor_kind") or "unknown",
        "persistence_class": anchor.get("persistence_class") or "unknown",
        "label_useful": int(_label_useful(anchor)),
        "label_persistent": int(bool(anchor.get("label_persistent"))),
        "retention_6h": _to_float(anchor.get("retention_6h")),
        "max_ret_900s": _to_float(anchor.get("max_ret_900s")),
        "ret_21600s": _to_float(anchor.get("ret_21600s")),
        "mcap0": _to_float(anchor.get("mcap0")),
        "liq0": _to_float(anchor.get("liq0")),
        "pair_age_min0": _to_float(anchor.get("pair_age_min0")),
        "mom5m0": _to_float(anchor.get("mom5m0")),
        "hits0": anchor.get("hits0"),
        "buys0": anchor.get("buys0"),
        "uniq0": anchor.get("uniq0"),
        "net_sol_in0": _to_float(anchor.get("net_sol_in0")),
        "buy_sell_ratio0": _to_float(anchor.get("buy_sell_ratio0")),
        "top_buyer_share0": _to_float(anchor.get("top_buyer_share0")),
        "mover_pattern0": anchor.get("mover_pattern0") or "missing",
        "persistence_regime0": features.get("persistence_regime0") or "missing",
    }
    for key, value in sorted(features.items()):
        row[f"feat__{key}"] = value
    return row


def _write_md(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Meme Anchor Dataset",
        "",
        "Anchor-level export for useful-winner and persistent-runner modeling.",
        "",
        "## Summary",
        "",
        f"- Rows: `{report['rows']}`",
        f"- Useful winners: `{report['useful_rows']}` (`{_fmt_pct(report['useful_rate'])}`)",
        f"- Persistent runners: `{report['persistent_rows']}` (`{_fmt_pct(report['persistent_rate'])}`)",
        f"- Source counts: `{report['source_counts']}`",
        f"- Family counts: `{report['family_counts']}`",
        f"- Regime counts: `{report['regime_counts']}`",
        f"- Persistence class counts: `{report['class_counts']}`",
        "",
        "## Sample Rows",
        "",
        "| Symbol | Mint | Source | Family | Regime | Useful | Persistent | Class | MCap0 | Age0 | Mom5m0 | Hits | NetSOL |",
        "|---|---|---|---|---|---:|---:|---|---:|---:|---:|---:|---:|",
    ]
    for row in report["sample_rows"]:
        lines.append(
            f"| {row['symbol']} | `{row['mint']}` | `{row['signal_source']}` | `{row['source_family']}` | "
            f"`{row['persistence_regime0']}` | {row['label_useful']} | {row['label_persistent']} | "
            f"`{row['persistence_class']}` | {row.get('mcap0') if row.get('mcap0') is not None else 'n/a'} | "
            f"{row.get('pair_age_min0') if row.get('pair_age_min0') is not None else 'n/a'} | "
            f"{row.get('mom5m0') if row.get('mom5m0') is not None else 'n/a'} | "
            f"{row.get('hits0') if row.get('hits0') is not None else 'n/a'} | "
            f"{row.get('net_sol_in0') if row.get('net_sol_in0') is not None else 'n/a'} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export anchor-level dataset for meme signal modeling.")
    parser.add_argument("--since-hours", type=float, default=168.0)
    parser.add_argument("--winner-ret", type=float, default=0.50)
    parser.add_argument("--persistent-ret", type=float, default=1.00)
    parser.add_argument("--persistent-retain", type=float, default=0.50)
    parser.add_argument("--round-trip-retain", type=float, default=0.15)
    parser.add_argument("--out-csv", type=Path, default=OUT_CSV)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=OUT_MD)
    args = parser.parse_args()

    module = _load_rank_module()
    since_ts = time.time() - (float(args.since_hours) * 3600.0)
    rows_by_mint = module.load_outcome_rows(since_ts=since_ts)
    anchors = module.build_anchor_set(
        rows_by_mint,
        min_ts=None,
        max_ts=None,
        winner_ret=float(args.winner_ret),
        persistent_ret=float(args.persistent_ret),
        persistent_retain=float(args.persistent_retain),
        round_trip_retain=float(args.round_trip_retain),
    )
    rows = [_flatten_anchor(anchor) for anchor in anchors]
    rows.sort(key=lambda row: float(row["signal_ts"]))

    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    report = {
        "generated_at": time.time(),
        "rows": len(rows),
        "useful_rows": sum(int(row["label_useful"]) for row in rows),
        "useful_rate": (sum(int(row["label_useful"]) for row in rows) / len(rows)) if rows else 0.0,
        "persistent_rows": sum(int(row["label_persistent"]) for row in rows),
        "persistent_rate": (sum(int(row["label_persistent"]) for row in rows) / len(rows)) if rows else 0.0,
        "source_counts": dict(Counter(str(row["signal_source"]) for row in rows)),
        "family_counts": dict(Counter(str(row["source_family"]) for row in rows)),
        "regime_counts": dict(Counter(str(row["persistence_regime0"]) for row in rows)),
        "class_counts": dict(Counter(str(row["persistence_class"]) for row in rows)),
        "fieldnames": fieldnames,
        "sample_rows": rows[:10],
        "paths": {
            "csv": str(args.out_csv),
            "json": str(args.out_json),
            "md": str(args.out_md),
        },
    }
    args.out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_md(args.out_md, report)
    print(f"meme_anchor_dataset_export: rows={report['rows']} useful={report['useful_rows']} persistent={report['persistent_rows']}")


if __name__ == "__main__":
    main()
