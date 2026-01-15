#!/usr/bin/env python3
"""Analyze alpha_journal.csv and print a performance summary.

Usage: .venv/bin/python src/analyzer.py

This script expects `data/alpha_journal.csv` created by the orchestrator.
It uses pandas to compute simple metrics and prints a Rich table summary.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    import pandas as pd
except Exception:
    print("pandas is required. Install with: .venv/bin/python -m pip install pandas")
    raise

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
CSV_PATH = DATA_DIR / "alpha_journal.csv"

console = Console()


def load_data(path: Path):
    if not path.exists():
        console.print(Panel(f"No data file found at {path}. Run the orchestrator first.", style="red"))
        sys.exit(1)
    df = pd.read_csv(path)
    return df


def normalize_expected_out(df: pd.DataFrame):
    """Try to normalize expected_out_raw to a float amount (USDC) when possible.

    Heuristic: if values are large (>1e6), assume USDC base units and divide by 1e6.
    Otherwise leave as-is.
    """
    if "expected_out_raw" not in df.columns:
        df["expected_out_raw"] = pd.NA

    # coerce to numeric
    df["expected_out_raw"] = pd.to_numeric(df["expected_out_raw"], errors="coerce")
    maxv = df["expected_out_raw"].max(skipna=True)
    use_usdc = False
    if pd.notna(maxv) and maxv > 1_000_000:
        use_usdc = True
        df["expected_out"] = df["expected_out_raw"] / 1_000_000.0
    else:
        df["expected_out"] = df["expected_out_raw"].astype(float)

    return df, use_usdc


def compute_metrics(df: pd.DataFrame):
    total = len(df)
    units_col = "unitsConsumed"
    if units_col not in df.columns:
        df[units_col] = pd.NA
    df[units_col] = pd.to_numeric(df[units_col], errors="coerce")

    avg_units = float(df[units_col].dropna().mean()) if total > 0 else 0.0

    # Theoretical win-rate: proportion of entries with a positive expected_out
    valid_out = df["expected_out"].dropna()
    wins = (valid_out > 0).sum()
    win_rate = (wins / total * 100.0) if total > 0 else 0.0

    # Efficiency: expected_out per compute unit
    df["efficiency"] = None
    mask = df[units_col].notna() & df["expected_out"].notna() & (df[units_col] > 0)
    if mask.any():
        df.loc[mask, "efficiency"] = df.loc[mask, "expected_out"] / df.loc[mask, units_col]

    avg_efficiency = float(pd.to_numeric(df["efficiency"], errors="coerce").dropna().mean() or 0.0)
    avg_expected_out = float(valid_out.mean()) if len(valid_out) > 0 else 0.0

    return {
        "total_signals": total,
        "avg_units": avg_units,
        "win_rate": win_rate,
        "avg_efficiency": avg_efficiency,
        "avg_expected_out": avg_expected_out,
        "df": df,
    }


def performance_by_alpha_score(df: pd.DataFrame):
    """Group results into alpha score buckets and compute win rate and avg P/L per bucket.

    Buckets: 0-10, 10-25, 25+
    Returns a list of tuples: (label, count, win_rate_pct, avg_pl)
    """
    # ensure needed columns exist
    if 'alpha_score' not in df.columns:
        df['alpha_score'] = 0.0
    if 'whale_multiplier' not in df.columns:
        df['whale_multiplier'] = pd.NA
    if 'input_amount_sol' not in df.columns:
        df['input_amount_sol'] = pd.NA

    # coerce numerics
    df['alpha_score'] = pd.to_numeric(df['alpha_score'], errors='coerce').fillna(0.0)
    df['input_amount_sol'] = pd.to_numeric(df['input_amount_sol'], errors='coerce').fillna(0.0)
    df['expected_out_sol'] = pd.to_numeric(df.get('expected_out_sol', pd.Series([0.0]*len(df))), errors='coerce').fillna(0.0)

    # profit/loss per row
    df['pl'] = df['expected_out_sol'] - df['input_amount_sol']

    buckets = [ (0.0, 10.0, '0-10'), (10.0, 25.0, '10-25'), (25.0, float('inf'), '25+') ]
    results = []
    for lo, hi, label in buckets:
        mask = (df['alpha_score'] >= lo) & (df['alpha_score'] < hi)
        sub = df[mask]
        count = len(sub)
        if count == 0:
            results.append((label, 0, 0.0, 0.0))
            continue
        wins = (sub['pl'] > 0).sum()
        win_rate = float(wins) / float(count) * 100.0
        avg_pl = float(sub['pl'].mean()) if count > 0 else 0.0
        results.append((label, int(count), float(win_rate), float(avg_pl)))

    return results


def alpha_concentration(df: pd.DataFrame):
    # Prefer grouping by whale if present, otherwise by 'name' (signal name) or 'mint'
    if "whale" in df.columns:
        key = "whale"
    elif "name" in df.columns:
        key = "name"
    elif "mint" in df.columns:
        key = "mint"
    else:
        return "unknown", []

    grp = df.groupby(key).agg(count=("ts", "count"), mean_expected=("expected_out", "mean"))
    grp = grp.sort_values("count", ascending=False)
    top = grp.head(5)
    top_list = [(idx, int(row["count"]), float(row["mean_expected"] or 0.0)) for idx, row in top.iterrows()]
    return key, top_list


def status_decision(metrics: dict):
    # Heuristics for green/red
    total = metrics["total_signals"]
    if total == 0:
        return "RED", "No signals captured yet."

    # require at least 30% win-rate and a non-trivial efficiency
    if metrics["win_rate"] >= 30.0 and metrics["avg_efficiency"] > 0 and metrics["avg_units"] < 1_000_000:
        return "GREEN", "Performance within acceptable thresholds."

    return "RED", "Performance below thresholds (low win-rate or high compute cost)."


def render_table(metrics: dict, top_sources, use_usdc: bool):
    t = Table(title="Alpha Journal Summary")
    t.add_column("Metric")
    t.add_column("Value", justify="right")

    currency = "USDC" if use_usdc else "units"

    t.add_row("Total Signals", str(metrics["total_signals"]))
    t.add_row("Avg Compute Units (CU)", f"{metrics['avg_units']:.0f}")
    t.add_row(f"Avg Expected Out ({currency})", f"{metrics['avg_expected_out']:.4f}")
    t.add_row("Avg Efficiency (out/unit)", f"{metrics['avg_efficiency']:.8f}")
    t.add_row("Theoretical Win Rate", f"{metrics['win_rate']:.2f}%")

    key, top_list = top_sources
    if top_list:
        details = "\n".join([f"{i+1}. {s[0]} — signals: {s[1]}, mean_out: {s[2]:.4f}" for i, s in enumerate(top_list)])
    else:
        details = "No sources yet"

    t.add_row(f"Top Sources (by {key})", details)

    console.print(t)


def render_alpha_performance(buckets: list[tuple]):
    t = Table(title="Performance by Alpha Score")
    t.add_column("Bucket")
    t.add_column("Signals", justify="right")
    t.add_column("Win Rate", justify="right")
    t.add_column("Avg P/L (SOL)", justify="right")

    for label, count, win_rate, avg_pl in buckets:
        t.add_row(label, str(count), f"{win_rate:.2f}%", f"{avg_pl:.6f}")

    console.print(t)


def main():
    df = load_data(CSV_PATH)
    df, use_usdc = normalize_expected_out(df)
    metrics = compute_metrics(df)
    top_sources = alpha_concentration(df)
    render_table(metrics, top_sources, use_usdc)
    buckets = performance_by_alpha_score(metrics['df'])
    render_alpha_performance(buckets)

    status, reason = status_decision(metrics)
    style = "green" if status == "GREEN" else "red"
    console.print(Panel(f"STATUS: {status}\n{reason}", style=style))


if __name__ == "__main__":
    main()
