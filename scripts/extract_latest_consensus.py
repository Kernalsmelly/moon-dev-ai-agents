#!/usr/bin/env python3
"""
Extract the latest AI consensus predictions from the repository CSVs.

Checks both data/polymarket/predictions.csv and
data/polymarket_websearch/predictions.csv and picks the most recently
modified file. Then it selects the rows for the most recent
analysis_run_id and prints a concise table of the top markets.

Dependencies: pandas, pathlib
"""

from pathlib import Path
import pandas as pd
import sys


def find_latest_predictions_file():
    candidates = [
        Path("data/polymarket/predictions.csv"),
        Path("data/polymarket_websearch/predictions.csv"),
    ]

    existing = [p for p in candidates if p.exists()]
    if not existing:
        return None

    # Choose the file with the newest modification time
    existing.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return existing[0]


def load_and_extract(path: Path, max_rows: int = 10):
    try:
        df = pd.read_csv(path)
    except Exception as e:
        print(f"Error reading CSV {path}: {e}")
        return None

    if df.empty:
        print(f"CSV {path} is empty.")
        return None

    # Determine the most recent analysis_run_id
    if 'analysis_timestamp' in df.columns:
        # Parse timestamps safely
        try:
            df['__parsed_ts'] = pd.to_datetime(df['analysis_timestamp'], errors='coerce')
            newest_ts = df['__parsed_ts'].max()
            if pd.isna(newest_ts):
                # Fallback to last row's run id
                analysis_run_id = df.iloc[-1]['analysis_run_id'] if 'analysis_run_id' in df.columns else None
            else:
                row = df[df['__parsed_ts'] == newest_ts]
                analysis_run_id = row.iloc[0]['analysis_run_id'] if not row.empty and 'analysis_run_id' in row.columns else None
        except Exception:
            analysis_run_id = df.iloc[-1]['analysis_run_id'] if 'analysis_run_id' in df.columns else None
    else:
        analysis_run_id = df.iloc[-1]['analysis_run_id'] if 'analysis_run_id' in df.columns else None

    if not analysis_run_id:
        print(f"Could not determine analysis_run_id from {path}.")
        return None

    # Filter to rows with this analysis_run_id
    if 'analysis_run_id' in df.columns:
        df_run = df[df['analysis_run_id'] == analysis_run_id].copy()
    else:
        df_run = df.copy()

    if df_run.empty:
        print(f"No rows found for analysis_run_id={analysis_run_id} in {path}.")
        return None

    # Select and normalize columns
    cols = ['market_title', 'market_slug', 'consensus_prediction', 'num_models_responded']
    have_web = 'web_search_used' in df_run.columns
    if have_web:
        cols.append('web_search_used')

    # Some CSVs may use slightly different column names; guard for that
    available = [c for c in cols if c in df_run.columns]

    display = df_run[available].head(max_rows)

    # Construct link column if market_slug present
    if 'market_slug' in display.columns:
        display = display.copy()
        display['link'] = display['market_slug'].apply(lambda s: f"https://polymarket.com/event/{s}" if pd.notna(s) and str(s).strip() else "")
        # Move link to immediately after market_slug for readability
        cols_order = []
        for c in available:
            cols_order.append(c)
            if c == 'market_slug':
                cols_order.append('link')
        display = display[cols_order]

    print(f"\nUsing file: {path} (analysis_run_id={analysis_run_id})\n")
    print(display.to_string(index=False))
    return True


def main():
    path = find_latest_predictions_file()
    if path is None:
        print("No predictions.csv found in data/polymarket or data/polymarket_websearch.")
        sys.exit(0)

    ok = load_and_extract(path, max_rows=10)
    if not ok:
        sys.exit(0)


if __name__ == '__main__':
    main()
