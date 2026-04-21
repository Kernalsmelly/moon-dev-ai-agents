#!/usr/bin/env python3
"""Track pending 6h winner candidates and score them with current baseline models."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any

BASE = Path("/Users/nickdavis/MOON DEV BOT/moon-dev-ai-agents")
OUTCOMES = BASE / "data" / "signal_outcomes.jsonl"
TAPE = BASE / "data" / "meme_launch_signals.jsonl"
DATASET_CSV = BASE / "data" / "meme_reports" / "meme_anchor_dataset.csv"
BASELINE_MODEL_PATH = BASE / "scripts" / "meme_anchor_baseline_model.py"
PERSISTENCE_REPORT_PATH = BASE / "scripts" / "meme_winner_persistence_report.py"
WINNER_SHAPE_REPORT_PATH = BASE / "scripts" / "meme_winner_shape_report.py"
WINNER_SHAPE_JSON = BASE / "data" / "meme_reports" / "meme_winner_shape_report.json"
PROMOTION_REPORT_JSON = BASE / "data" / "meme_reports" / "meme_promotion_rule_report.json"
OUT_JSON = BASE / "data" / "meme_reports" / "pending_maturation_report.json"
OUT_MD = BASE / "data" / "meme_reports" / "pending_maturation_report.md"

KNOWN_HORIZONS = (300, 900, 1800, 3600, 7200, 14400, 21600)
PROGRESS_RANK = {
    "holding_strong": 4,
    "still_alive": 3,
    "fragile_but_green": 2,
    "unclear": 1,
    "waiting_for_data": 0,
    "fading": -1,
}
DECISION_RANK = {
    "promote_now": 5,
    "watch_to_60m": 4,
    "hold_and_recheck": 3,
    "fragile_watch": 2,
    "too_early": 1,
    "cut_bias": -1,
}


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        out = float(value)
        if math.isfinite(out):
            return out
    except Exception:
        return None
    return None


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100.0:.1f}%"


def _fmt_num(value: float | None, digits: int = 0) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def _latest_known(row: dict[str, Any]) -> tuple[int | None, float | None]:
    latest_hz = None
    latest_ret = None
    for hz in KNOWN_HORIZONS:
        value = _to_float(row.get(f"ret_{hz}s"))
        if value is None:
            continue
        latest_hz = hz
        latest_ret = value
    return latest_hz, latest_ret


def _latest_symbol_map() -> dict[str, str]:
    latest: dict[str, tuple[float, str]] = {}
    with TAPE.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            mint = str(row.get("mint") or "").strip()
            if not mint:
                continue
            ts = _to_float(row.get("ts")) or 0.0
            metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
            symbol = str(metrics.get("symbol") or row.get("symbol") or "").strip()
            if not symbol or symbol.lower() == "n/a":
                continue
            prev = latest.get(mint)
            if prev is None or ts >= prev[0]:
                latest[mint] = (ts, symbol)
    return {mint: symbol for mint, (_ts, symbol) in latest.items()}


def _load_promotion_reference(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"rules": {}, "states": {}}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {"rules": {}, "states": {}}
    rules = {str(row.get("label") or ""): row for row in data.get("rules") or []}
    states: dict[str, dict[str, Any]] = {}
    for checkpoint_label, checkpoint in (data.get("checkpoints") or {}).items():
        for state_name, row in (checkpoint.get("states") or {}).items():
            states[f"{checkpoint_label}:{state_name}"] = row
    return {"rules": rules, "states": states}


def _load_shape_reference(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for checkpoint_label, rows in (data.get("by_checkpoint") or {}).items():
        for row in rows or []:
            shape_state = str(row.get("shape_state") or "")
            if checkpoint_label and shape_state:
                out[f"{checkpoint_label}:{shape_state}"] = row
    for row in data.get("by_shape_path") or []:
        key = str(row.get("shape_path") or "")
        if key:
            out[f"path:{key}"] = row
    return out


def _progress_hint(*, latest_hz: int | None, latest_ret: float | None, max_ret_15m: float | None, latest_retention: float | None) -> str:
    if latest_hz is None or latest_ret is None:
        return "waiting_for_data"
    if latest_hz >= 3600 and latest_ret > 0.25 and (latest_retention or 0.0) >= 0.50:
        return "holding_strong"
    if latest_ret > 0.10 and (latest_retention or 0.0) >= 0.30:
        return "still_alive"
    if latest_ret > 0.0 and (max_ret_15m or 0.0) > 0.50:
        return "fragile_but_green"
    if latest_ret <= 0.0:
        return "fading"
    return "unclear"


def _decision_reference(
    *,
    latest_hz: int | None,
    progress_hint: str,
    promotion_ref: dict[str, Any],
) -> dict[str, Any]:
    rules = promotion_ref.get("rules") or {}
    states = promotion_ref.get("states") or {}

    def rule(label: str) -> dict[str, Any]:
        return dict(rules.get(label) or {})

    def state(checkpoint: str, name: str) -> dict[str, Any]:
        return dict(states.get(f"{checkpoint}:{name}") or {})

    if latest_hz is None or latest_hz < 1800:
        return {
            "promotion_decision": "too_early",
            "decision_reason": "Not enough post-signal data yet.",
            "historical_label": None,
            "historical_persistence_precision": None,
            "historical_lift": None,
            "historical_n": None,
        }

    if latest_hz >= 3600:
        if progress_hint == "holding_strong":
            ref = rule("60m holding_strong")
            return {
                "promotion_decision": "promote_now",
                "decision_reason": "Has survived to 60m in the strongest historical promotion bucket.",
                "historical_label": "60m holding_strong",
                "historical_persistence_precision": _to_float(ref.get("persistent_precision")),
                "historical_lift": _to_float(ref.get("lift_vs_baseline")),
                "historical_n": int(ref.get("n") or 0),
            }
        if progress_hint == "fading":
            ref = rule("60m fading")
            return {
                "promotion_decision": "cut_bias",
                "decision_reason": "Fading at 60m has historically produced no persistent runners.",
                "historical_label": "60m fading",
                "historical_persistence_precision": _to_float(ref.get("persistent_precision")),
                "historical_lift": _to_float(ref.get("lift_vs_baseline")),
                "historical_n": int(ref.get("n") or 0),
            }
        if progress_hint == "still_alive":
            ref = rule("60m not_fading")
            return {
                "promotion_decision": "hold_and_recheck",
                "decision_reason": "Still alive at 60m, but not in the strongest bucket.",
                "historical_label": "60m not_fading",
                "historical_persistence_precision": _to_float(ref.get("persistent_precision")),
                "historical_lift": _to_float(ref.get("lift_vs_baseline")),
                "historical_n": int(ref.get("n") or 0),
            }
        if progress_hint == "fragile_but_green":
            ref = state("60m", "fragile_but_green")
            return {
                "promotion_decision": "fragile_watch",
                "decision_reason": "Green at 60m, but this state has not held up historically.",
                "historical_label": "60m fragile_but_green",
                "historical_persistence_precision": _to_float(ref.get("persistent_precision")),
                "historical_lift": _to_float(ref.get("lift_vs_baseline")),
                "historical_n": int(ref.get("n") or 0),
            }

    if progress_hint in {"holding_strong", "still_alive"}:
        ref = rule("30m still_alive+")
        return {
            "promotion_decision": "watch_to_60m",
            "decision_reason": "Alive by 30m; historically better than baseline, but 60m matters more.",
            "historical_label": "30m still_alive+",
            "historical_persistence_precision": _to_float(ref.get("persistent_precision")),
            "historical_lift": _to_float(ref.get("lift_vs_baseline")),
            "historical_n": int(ref.get("n") or 0),
        }
    if progress_hint == "fading":
        ref = rule("30m fading")
        return {
            "promotion_decision": "cut_bias",
            "decision_reason": "Fading by 30m has historically not become persistent.",
            "historical_label": "30m fading",
            "historical_persistence_precision": _to_float(ref.get("persistent_precision")),
            "historical_lift": _to_float(ref.get("lift_vs_baseline")),
            "historical_n": int(ref.get("n") or 0),
        }
    if progress_hint == "fragile_but_green":
        ref = state("30m", "fragile_but_green")
        return {
            "promotion_decision": "fragile_watch",
            "decision_reason": "Green, but not convincingly alive yet.",
            "historical_label": "30m fragile_but_green",
            "historical_persistence_precision": _to_float(ref.get("persistent_precision")),
            "historical_lift": _to_float(ref.get("lift_vs_baseline")),
            "historical_n": int(ref.get("n") or 0),
        }

    return {
        "promotion_decision": "hold_and_recheck",
        "decision_reason": "Needs more time to resolve.",
        "historical_label": None,
        "historical_persistence_precision": None,
        "historical_lift": None,
        "historical_n": None,
    }


def _make_model_row(rank_module: Any, row: dict[str, Any]) -> dict[str, Any]:
    signal_source = str(row.get("signal_source") or "unknown")
    mover_pattern = str(row.get("mover_pattern0") or "missing")
    base_source_family = rank_module.signal_source_family(signal_source)
    snapshot = {
        "mint": row["mint"],
        "symbol": row.get("symbol") or "n/a",
        "signal_ts": float(row.get("signal_ts") or 0.0),
        "signal_source": signal_source,
        "base_source_family": base_source_family,
        "source_family": rank_module._rank_family_key(base_source_family, mover_pattern),
        "signal_profile0": "missing",
        "mover_pattern0": mover_pattern,
        "score0": None,
        "mcap0": _to_float(row.get("mcap0")),
        "liq0": _to_float(row.get("liq0")),
        "pair_age_min0": _to_float(row.get("pair_age_min0")),
        "mom5m0": _to_float(row.get("mom5m0")),
        "hits0": row.get("hits0"),
        "buys0": row.get("buys0"),
        "uniq0": row.get("uniq0"),
        "net_sol_in0": _to_float(row.get("net_sol_in0")),
        "buy_sell_ratio0": None,
        "top_buyer_share0": None,
        "unique_buyers_status": "unknown",
        "top_buyer_share_status": "unknown",
    }
    snapshot["persistence_regime0"] = rank_module._classify_regime(snapshot)
    features = rank_module._features_from_snapshot(snapshot)
    return {
        **snapshot,
        **{f"feat__{field}": value for field, value in features.items()},
    }


def _write_md(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Pending Maturation Report",
        "",
        "Tracks earliest-useful winners that have not fully matured to the 6h persistence horizon yet.",
        "",
        "## Summary",
        "",
        f"- Pending count: `{report['summary']['pending_count']}`",
        f"- Median age now: `{_fmt_num(report['summary']['median_age_hours'], 2)}h`",
        f"- Median ETA to 6h: `{_fmt_num(report['summary']['median_eta_hours'], 2)}h`",
        f"- Promotion-ready now: `{report['summary']['decision_counts'].get('promote_now', 0)}`",
        f"- Cut-bias now: `{report['summary']['decision_counts'].get('cut_bias', 0)}`",
        "",
        "| Symbol | Mint | Source | Regime | Shape | Shape Path | Age Now (h) | ETA 6h (h) | Latest Ret | Retention | Hint | Decision | Hist Persist | Shape Survivor | Useful | Persistent |",
        "|---|---|---|---|---|---|---:|---:|---:|---:|---|---|---:|---:|---:|---:|",
    ]
    for row in report["pending_rows"]:
        lines.append(
            f"| {row['symbol']} | `{row['mint']}` | `{row['signal_source']}` | `{row['persistence_regime0']}` | "
            f"`{row['shape_state']}` | "
            f"`{row.get('shape_path_30_to_60') or 'pending_60m'}` | "
            f"{_fmt_num(row['age_hours'], 2)} | {_fmt_num(row['eta_6h_hours'], 2)} | {_fmt_pct(row['latest_ret'])} | "
            f"{_fmt_pct(row['latest_retention'])} | `{row['progress_hint']}` | `{row['promotion_decision']}` | "
            f"{_fmt_pct(row['historical_persistence_precision'])} | {_fmt_pct(row.get('shape_historical_survivor_precision'))} | "
            f"{_fmt_num(row['useful_score'], 1)} | {_fmt_num(row['persistent_score'], 1)} |"
        )
    lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Track pending 6h maturation candidates.")
    parser.add_argument("--since-hours", type=float, default=24.0)
    parser.add_argument("--winner-ret", type=float, default=0.50)
    parser.add_argument("--persistent-ret", type=float, default=1.00)
    parser.add_argument("--persistent-retain", type=float, default=0.50)
    parser.add_argument("--round-trip-retain", type=float, default=0.15)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=OUT_MD)
    args = parser.parse_args()

    persistence = _load_module("meme_winner_persistence_report_module", PERSISTENCE_REPORT_PATH)
    baseline = _load_module("meme_anchor_baseline_model_module", BASELINE_MODEL_PATH)
    shape_module = _load_module("meme_winner_shape_report_module", WINNER_SHAPE_REPORT_PATH)
    rank_module = baseline._load_rank_module()
    latest_symbol_map = _latest_symbol_map()
    promotion_ref = _load_promotion_reference(PROMOTION_REPORT_JSON)
    shape_ref = _load_shape_reference(WINNER_SHAPE_JSON)

    dataset_rows = baseline.load_rows(DATASET_CSV)
    useful_model = baseline.fit_model(dataset_rows, target_field="label_useful")
    persistent_model = baseline.fit_model(dataset_rows, target_field="label_persistent")

    since_ts = time.time() - (float(args.since_hours) * 3600.0)
    rows_by_mint = persistence.load_rows(OUTCOMES, since_ts)

    pending_rows: list[dict[str, Any]] = []
    now = time.time()
    for mint, rows in rows_by_mint.items():
        useful = persistence.first_useful(rows, float(args.winner_ret))
        if useful is None:
            continue
        klass, _ = persistence.classify_persistence(
            useful,
            persistent_ret=float(args.persistent_ret),
            persistent_retain=float(args.persistent_retain),
            round_trip_retain=float(args.round_trip_retain),
        )
        if klass != "pending_6h":
            continue

        latest_hz, latest_ret = _latest_known(useful)
        max_ret_15m = _to_float(useful.get("max_ret_900s"))
        max_ret_all = _to_float(useful.get("max_ret_all"))
        latest_retention = (
            (latest_ret / max_ret_all) if latest_ret is not None and max_ret_all not in (None, 0.0) else None
        )
        age_hours = max(0.0, (now - float(useful.get("signal_ts") or 0.0)) / 3600.0)
        eta_6h = max(0.0, 6.0 - age_hours)
        model_row = _make_model_row(rank_module, useful)
        useful_score = baseline.score_row(useful_model, model_row)
        persistent_score = baseline.score_row(persistent_model, model_row)
        progress_hint = _progress_hint(
            latest_hz=latest_hz,
            latest_ret=latest_ret,
            max_ret_15m=max_ret_15m,
            latest_retention=latest_retention,
        )
        decision = _decision_reference(
            latest_hz=latest_hz,
            progress_hint=progress_hint,
            promotion_ref=promotion_ref,
        )
        shape = shape_module.classify_live_shape(useful, latest_hz=latest_hz)
        shape30 = shape_module.classify_checkpoint_shape(useful, 1800)
        shape60 = shape_module.classify_checkpoint_shape(useful, 3600) if latest_hz is not None and latest_hz >= 3600 else None
        shape_hist = dict(shape_ref.get(f"{shape.get('checkpoint_label')}:{shape.get('shape_state')}") or {})
        shape_path = (
            f"{shape30.get('shape_state')} -> {shape60.get('shape_state')}"
            if shape60 is not None
            else None
        )
        path_hist = dict(shape_ref.get(f"path:{shape_path}") or {}) if shape_path else {}

        pending_rows.append(
            {
                "mint": mint,
                "symbol": (
                    useful.get("symbol")
                    if str(useful.get("symbol") or "").strip().lower() not in {"", "n/a"}
                    else latest_symbol_map.get(mint, "n/a")
                ),
                "signal_source": useful.get("signal_source") or "unknown",
                "signal_ts": float(useful.get("signal_ts") or 0.0),
                "mcap0": _to_float(useful.get("mcap0")),
                "liq0": _to_float(useful.get("liq0")),
                "pair_age_min0": _to_float(useful.get("pair_age_min0")),
                "mom5m0": _to_float(useful.get("mom5m0")),
                "hits0": int(useful.get("hits0") or 0),
                "buys0": int(useful.get("buys0") or 0),
                "uniq0": int(useful.get("uniq0") or 0),
                "net_sol_in0": _to_float(useful.get("net_sol_in0")),
                "mover_pattern0": useful.get("mover_pattern0") or "unknown",
                "persistence_regime0": model_row["persistence_regime0"],
                "max_ret_15m": max_ret_15m,
                "latest_horizon_s": latest_hz,
                "latest_ret": latest_ret,
                "latest_retention": latest_retention,
                "shape_checkpoint": shape.get("checkpoint_label"),
                "shape_state": shape.get("shape_state"),
                "shape_score": shape.get("shape_score"),
                "shape_reason": shape.get("reason"),
                "shape_steam_loss": bool(shape.get("steam_loss")),
                "shape_30m_state": shape30.get("shape_state"),
                "shape_60m_state": shape60.get("shape_state") if shape60 else None,
                "shape_path_30_to_60": shape_path,
                "shape_retention_vs_15m_peak": shape.get("retention_vs_15m_peak"),
                "shape_extension_vs_15m_peak": shape.get("extension_vs_15m_peak"),
                "shape_historical_survivor_precision": _to_float(shape_hist.get("survivor_precision")),
                "shape_historical_persistent_precision": _to_float(shape_hist.get("persistent_precision")),
                "shape_path_historical_survivor_precision": _to_float(path_hist.get("survivor_precision")),
                "shape_path_historical_persistent_precision": _to_float(path_hist.get("persistent_precision")),
                "age_hours": age_hours,
                "eta_6h_hours": eta_6h,
                "useful_score": float(useful_score["score"]),
                "persistent_score": float(persistent_score["score"]),
                "progress_hint": progress_hint,
                **decision,
            }
        )

    pending_rows.sort(
        key=lambda row: (
            DECISION_RANK.get(str(row["promotion_decision"]), 0),
            PROGRESS_RANK.get(str(row["progress_hint"]), 0),
            float(row["historical_persistence_precision"] if row["historical_persistence_precision"] is not None else -999.0),
            float(row["shape_score"] if row["shape_score"] is not None else -999.0),
            float(row["latest_retention"] if row["latest_retention"] is not None else -999.0),
            float(row["latest_ret"] if row["latest_ret"] is not None else -999.0),
            float(row["useful_score"]),
        ),
        reverse=True,
    )

    age_values = [row["age_hours"] for row in pending_rows]
    eta_values = [row["eta_6h_hours"] for row in pending_rows]
    decision_counts = Counter(str(row["promotion_decision"]) for row in pending_rows)
    report = {
        "generated_at": now,
        "summary": {
            "pending_count": len(pending_rows),
            "median_age_hours": float(statistics.median(age_values)) if age_values else None,
            "median_eta_hours": float(statistics.median(eta_values)) if eta_values else None,
            "decision_counts": dict(decision_counts),
        },
        "pending_rows": pending_rows,
    }

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_md(args.out_md, report)
    print(f"pending_maturation_report: pending={len(pending_rows)}")


if __name__ == "__main__":
    main()
