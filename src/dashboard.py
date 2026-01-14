#!/usr/bin/env python3
"""Real-time dashboard for Moon Dev trading state using Rich.

Reads `data/positions_state.json` and `data/positions.csv` (if present)
and displays an updating dashboard every 2 seconds.
"""
import os
import time
import json
from datetime import datetime
from typing import Optional

import pandas as pd
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich import box
from rich.console import Console
from rich.text import Text

# Try to import helpers from nice_funcs
try:
    from src.nice_funcs import TOKEN_CACHE, get_cached_symbol, token_price, token_overview
except Exception:
    # Fallback if running from different CWD or package layout
    try:
        from nice_funcs import TOKEN_CACHE, get_cached_symbol, token_price, token_overview
    except Exception:
        TOKEN_CACHE = {}
        def get_cached_symbol(a):
            return str(a)[:4]
        def token_price(a):
            return None
        def token_overview(a):
            return None

console = Console()

# Paths to watch (try project-root 'data' and 'src/data')
POSSIBLE_POS_STATE = [os.path.join('data', 'positions_state.json'), os.path.join('src', 'data', 'positions_state.json')]
POSSIBLE_POS_CSV = [os.path.join('data', 'positions.csv'), os.path.join('src', 'data', 'positions.csv')]
POSSIBLE_PORTFOLIO = [os.path.join('data', 'portfolio_balance.csv'), os.path.join('src', 'data', 'portfolio_balance.csv')]

def find_existing(paths):
    for p in paths:
        if os.path.exists(p):
            return p
    return None

POS_STATE_FILE = find_existing(POSSIBLE_POS_STATE) or os.path.join('data', 'positions_state.json')
POS_CSV_FILE = find_existing(POSSIBLE_POS_CSV)
PORTFOLIO_FILE = find_existing(POSSIBLE_PORTFOLIO)

def safe_load_json(path: str) -> dict:
    """Attempt to safely load JSON even if another process is writing it."""
    if not path or not os.path.exists(path):
        return {}
    for _ in range(3):
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except Exception:
            time.sleep(0.1)
            continue
    # Last-ditch: try reading raw and repairing common partial-write cases
    try:
        with open(path, 'r', errors='ignore') as f:
            txt = f.read()
            # attempt to find last closing brace
            idx = txt.rfind('}')
            if idx != -1:
                txt = txt[:idx+1]
            return json.loads(txt)
    except Exception:
        return {}

def load_positions_csv(path: Optional[str]) -> pd.DataFrame:
    if not path or not os.path.exists(path):
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        try:
            return pd.read_csv(path, engine='python')
        except Exception:
            return pd.DataFrame()

def human_age(ts: Optional[float]) -> str:
    if not ts:
        return "-"
    try:
        delta = datetime.utcnow().timestamp() - ts
        if delta < 60:
            return f"{int(delta)}s"
        if delta < 3600:
            return f"{int(delta//60)}m"
        if delta < 86400:
            return f"{int(delta//3600)}h"
        return f"{int(delta//86400)}d"
    except Exception:
        return "-"

def build_active_trades_table(positions_state: dict, positions_df: pd.DataFrame) -> Table:
    table = Table(title="Active Trades", box=box.SIMPLE_HEAVY, expand=True)
    table.add_column("Token", no_wrap=True)
    table.add_column("Age", justify="right")
    table.add_column("Entry", justify="right")
    table.add_column("Current Price", justify="right")
    table.add_column("PnL %", justify="right")
    table.add_column("Status", justify="center")
    table.add_column("Peak", justify="right")

    for token, meta in positions_state.items():
        try:
            symbol = get_cached_symbol(token) if token else str(token)[:4]
        except Exception:
            symbol = str(token)[:4]

        # Age: prefer explicit timestamp field, else fallback to file mtime
        ts = None
        if isinstance(meta, dict):
            ts = meta.get('created_at') or meta.get('entry_time') or meta.get('timestamp')
        if ts:
            try:
                # accept ISO string or epoch
                if isinstance(ts, str):
                    try:
                        parsed = datetime.fromisoformat(ts)
                        ts_val = parsed.timestamp()
                    except Exception:
                        ts_val = float(ts)
                else:
                    ts_val = float(ts)
            except Exception:
                ts_val = None
        else:
            try:
                ts_val = os.path.getmtime(POS_STATE_FILE) if os.path.exists(POS_STATE_FILE) else None
            except Exception:
                ts_val = None

        age = human_age(ts_val)

        entry_price = meta.get('entry_price') if isinstance(meta, dict) else None
        peak_price = meta.get('highest_price_seen') if isinstance(meta, dict) else None
        de_risked = bool(meta.get('de_risked')) if isinstance(meta, dict) else False

        # Current price: try positions_df lookups first
        current_price = None
        if not positions_df.empty:
            # try common column names
            for col in ('token', 'mint', 'address'):
                if col in positions_df.columns:
                    row = positions_df[positions_df[col].astype(str) == str(token)]
                    if not row.empty:
                        # try various price column names
                        for pcol in ('price', 'current_price', 'usd_price', 'value', 'close', 'last_price'):
                            if pcol in row.columns:
                                try:
                                    current_price = float(row.iloc[0][pcol])
                                except Exception:
                                    current_price = None
                                break
                        if current_price is None:
                            # maybe USD value and amount available
                            if 'valueUsd' in row.columns and 'uiAmount' in row.columns:
                                try:
                                    current_price = float(row.iloc[0]['valueUsd']) / float(row.iloc[0]['uiAmount'])
                                except Exception:
                                    current_price = None
                        if current_price is not None:
                            break

        # Fallback: call token_price (may be slower)
        if current_price is None:
            try:
                p = token_price(token)
                current_price = float(p) if p is not None else None
            except Exception:
                current_price = None

        # Compute PnL%
        pnl_pct = None
        try:
            if entry_price and current_price:
                pnl_pct = (float(current_price) - float(entry_price)) / float(entry_price) * 100.0
        except Exception:
            pnl_pct = None

        # Styling
        if pnl_pct is None:
            pnl_text = "-"
            pnl_style = "white"
        else:
            pnl_text = f"{pnl_pct:+.2f}%"
            pnl_style = "green" if pnl_pct > 0 else ("red" if pnl_pct < 0 else "yellow")

        status_text = "De-Risked" if de_risked else "Active"
        if de_risked:
            status_render = Text(status_text, style="bold cyan")
        else:
            status_render = Text(status_text)

        entry_display = f"${float(entry_price):.6f}" if entry_price else "-"
        current_display = f"${float(current_price):.6f}" if current_price else "-"
        peak_display = f"${float(peak_price):.6f}" if peak_price else "-"

        token_label = f"{symbol}\n{str(token)[:8]}"

        table.add_row(
            token_label,
            age,
            entry_display,
            current_display,
            Text(pnl_text, style=pnl_style),
            status_render,
            peak_display,
        )

    if len(positions_state) == 0:
        table.add_row("—", "—", "—", "—", "—", "—", "—")

    return table

def build_global_panel(portfolio_file: Optional[str]) -> Panel:
    total_sol = "N/A"
    daily_pnl = "N/A"

    if portfolio_file and os.path.exists(portfolio_file):
        try:
            df = pd.read_csv(portfolio_file)
            if not df.empty:
                # assume balance column exists
                last = df.iloc[-1]['balance']
                total_sol = f"{float(last):.4f} SOL"
                if len(df) >= 2:
                    prev = float(df.iloc[-2]['balance'])
                    daily_pnl = f"{(float(last)-prev):+.4f} SOL"
        except Exception:
            pass

    status = "🟢 SCANNING"
    body = f"Total SOL Balance: {total_sol}\nTotal Daily PnL: {daily_pnl}\nStatus: {status}"
    return Panel(body, title="Global Stats", box=box.SQUARE)

def main_loop():
    console.clear()
    positions_df = load_positions_csv(POS_CSV_FILE)

    layout = Layout()
    layout.split_column(
        Layout(name="upper", size=6),
        Layout(name="lower", ratio=4)
    )

    with Live(layout, refresh_per_second=4, screen=True, transient=True) as live:
        # Symbol populator: if symbols are missing in TOKEN_CACHE, call token_overview once to fill cache
        try:
            pos_state = safe_load_json(POS_STATE_FILE) or {}
            for mint, meta in pos_state.items():
                try:
                    sym = TOKEN_CACHE.get(mint) or get_cached_symbol(mint)
                    # Treat first-4-chars as unknown symbol
                    if sym == str(mint)[:4] or (isinstance(sym, str) and sym.lower().startswith('unk')):
                        # best-effort one-shot call to populate cache
                        try:
                            token_overview(mint)
                        except Exception:
                            pass
                        # Respectful rate-limit pause to avoid IP throttling
                        time.sleep(1.0)
                except Exception:
                    continue
        except Exception:
            pass

        while True:
            # reload files
            positions_state = safe_load_json(POS_STATE_FILE)
            positions_df = load_positions_csv(POS_CSV_FILE)

            # Build panels
            global_panel = build_global_panel(PORTFOLIO_FILE)
            trades_table = build_active_trades_table(positions_state or {}, positions_df)

            layout["upper"].update(global_panel)
            layout["lower"].update(Panel(trades_table, title="Positions", box=box.ROUNDED))

            live.update(layout)
            time.sleep(2)

if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        print("Exiting dashboard")
