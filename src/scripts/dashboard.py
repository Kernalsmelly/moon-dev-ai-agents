#!/usr/bin/env python3
"""Live Trading Dashboard: Real-time monitoring of positions, trades, and system health.

A terminal UI that shows:
- Current positions with live P&L
- Recent activity feed
- RPC health status
- Session and daily performance stats

Usage:
    python src/scripts/dashboard.py              # Run dashboard
    python src/scripts/dashboard.py --refresh 5  # Custom refresh rate (seconds)

Run in a tmux pane alongside your orchestrator for real-time visibility.
"""
import argparse
import asyncio
import csv
import json
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Optional

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Import rich from site-packages, not local stub
import importlib.util
def import_real_rich():
    """Import the real rich library, bypassing local stub."""
    import site
    for path in site.getsitepackages():
        rich_path = os.path.join(path, 'rich')
        if os.path.isdir(rich_path):
            spec = importlib.util.spec_from_file_location('rich', os.path.join(rich_path, '__init__.py'))
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules['_real_rich'] = module
                spec.loader.exec_module(module)
                return module
    return None

# Try to get real rich, fall back to basic output
try:
    # Check if we have the stub or real rich
    import rich as test_rich
    if not hasattr(test_rich, 'print'):
        # It's the stub, try to import real one
        _real_rich = import_real_rich()
        if _real_rich:
            from _real_rich.console import Console
            from _real_rich.table import Table
            from _real_rich.panel import Panel
            from _real_rich.text import Text
            from _real_rich import box
            HAS_RICH = True
        else:
            HAS_RICH = False
    else:
        from rich.console import Console
        from rich.table import Table
        from rich.panel import Panel
        from rich.text import Text
        from rich import box
        HAS_RICH = True
except Exception:
    HAS_RICH = False

# Basic fallback console
class BasicConsole:
    def print(self, *args, **kwargs):
        text = ' '.join(str(a) for a in args)
        # Strip basic rich markup
        import re
        text = re.sub(r'\[/?[^\]]+\]', '', text)
        print(text)

    def clear(self):
        os.system('clear' if os.name != 'nt' else 'cls')

if HAS_RICH:
    console = Console()
else:
    console = BasicConsole()

# Optional imports
try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

try:
    import src.config as config
    HAS_CONFIG = True
except ImportError:
    HAS_CONFIG = False

try:
    from src.position_store import PositionStore
    HAS_STORE = True
except ImportError:
    HAS_STORE = False

# Data paths
DATA_DIR = os.path.join(PROJECT_ROOT, 'data')
EXECUTION_EVENTS_CSV = os.path.join(DATA_DIR, 'execution_events.csv')
SHADOW_TRADES_CSV = os.path.join(DATA_DIR, 'shadow_trades.csv')
RPC_POOL_PATH = os.path.join(PROJECT_ROOT, 'config', 'rpc_pool.json')


class DashboardState:
    """Holds current dashboard state."""

    def __init__(self):
        self.positions: dict = {}
        self.recent_events: list = []
        self.recent_trades: list = []
        self.rpc_health: dict = {}
        self.sol_price: float = 0.0
        self.session_start: datetime = datetime.now()
        self.session_pnl: float = 0.0
        self.session_trades: int = 0
        self.session_wins: int = 0
        self.today_pnl: float = 0.0
        self.today_trades: int = 0
        self.mode: str = 'SHADOW'
        self.last_update: datetime = datetime.now()
        self.errors: list = []

    def update_mode(self):
        """Determine current mode from config."""
        if HAS_CONFIG:
            shadow = getattr(config, 'SHADOW_MODE', True)
            self.mode = 'SHADOW' if shadow else 'LIVE'
        else:
            self.mode = 'UNKNOWN'


def load_recent_events(limit: int = 20) -> list:
    """Load recent events from execution_events.csv."""
    events = []

    if not os.path.exists(EXECUTION_EVENTS_CSV):
        return events

    try:
        with open(EXECUTION_EVENTS_CSV, 'r', newline='') as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)

        for row in reversed(rows[-limit:]):
            ts_str = row.get('ts', '')
            try:
                ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00').replace('+00:00', ''))
            except Exception:
                ts = datetime.now()

            events.append({
                'ts': ts,
                'type': row.get('event_type', 'unknown'),
                'mint': row.get('mint', ''),
                'data': json.loads(row.get('data_json', '{}') or '{}'),
            })
    except Exception:
        pass

    return events


def load_recent_trades(limit: int = 10) -> list:
    """Load recent trades from shadow_trades.csv."""
    trades = []

    if not os.path.exists(SHADOW_TRADES_CSV):
        return trades

    try:
        with open(SHADOW_TRADES_CSV, 'r', newline='') as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)

        for row in reversed(rows[-limit:]):
            trades.append({
                'ts': row.get('ts_exit', ''),
                'symbol': row.get('symbol', '???'),
                'pnl_usd': float(row.get('pnl_usd', 0) or 0),
                'pnl_pct': float(row.get('pnl_pct', 0) or 0),
                'exit_reason': row.get('exit_reason', ''),
            })
    except Exception:
        pass

    return trades


def load_rpc_health() -> dict:
    """Load RPC pool health status."""
    health = {'status': 'unknown', 'active': '', 'pool_size': 0}

    if not os.path.exists(RPC_POOL_PATH):
        return health

    try:
        with open(RPC_POOL_PATH, 'r') as fh:
            data = json.load(fh)

        pool = data.get('pool', [])
        health['pool_size'] = len(pool)

        if pool:
            first = pool[0]
            url = first.get('url', '')
            if 'helius' in url.lower():
                health['active'] = 'Helius'
            elif 'alchemy' in url.lower():
                health['active'] = 'Alchemy'
            elif 'chainstack' in url.lower():
                health['active'] = 'Chainstack'
            elif 'ankr' in url.lower():
                health['active'] = 'Ankr'
            else:
                health['active'] = url[:20] + '...'

            health['status'] = 'healthy'
    except Exception:
        health['status'] = 'error'

    return health


def calculate_session_stats(state: DashboardState):
    """Calculate session statistics from trades."""
    if not HAS_STORE:
        return

    try:
        store = PositionStore()
        trades = store.get_trades(days=1)

        today_pnl = 0.0
        today_count = 0
        session_pnl = 0.0
        session_count = 0
        session_wins = 0

        for trade in trades:
            pnl = trade.get('pnl_usd', 0) or 0
            today_pnl += pnl
            today_count += 1

            try:
                ts_str = trade.get('exit_timestamp', '')
                ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00').replace('+00:00', ''))
                if ts >= state.session_start:
                    session_pnl += pnl
                    session_count += 1
                    if pnl > 0:
                        session_wins += 1
            except Exception:
                pass

        state.today_pnl = today_pnl
        state.today_trades = today_count
        state.session_pnl = session_pnl
        state.session_trades = session_count
        state.session_wins = session_wins

    except Exception:
        pass


def format_pnl(amount: float) -> str:
    """Format P&L with color codes."""
    if amount > 0:
        return f"[green]+${amount:.2f}[/green]"
    elif amount < 0:
        return f"[red]${amount:.2f}[/red]"
    else:
        return "$0.00"


def render_dashboard(state: DashboardState) -> str:
    """Render the dashboard as a string."""
    lines = []
    width = 70

    # Header
    mode_indicator = f"[yellow]{state.mode}[/yellow]" if state.mode == 'SHADOW' else f"[green]{state.mode}[/green]"
    lines.append("=" * width)
    lines.append(f"  MOON DEV TRADING SYSTEM                    {mode_indicator} MODE")
    lines.append(f"  SOL: ${state.sol_price:.2f}" if state.sol_price else "  SOL: --" + f"                              {state.last_update.strftime('%H:%M:%S')}")
    lines.append("=" * width)
    lines.append("")

    # Positions
    lines.append("--- OPEN POSITIONS ---")
    if not state.positions:
        lines.append("  [dim]No open positions[/dim]")
    else:
        lines.append(f"  {'Symbol':<10} {'Entry':>12} {'Current':>12} {'P&L':>10} {'Status':<15}")
        lines.append("  " + "-" * 60)
        for mint, pos in state.positions.items():
            symbol = pos.get('symbol', mint[:6])
            entry = pos.get('entry_price', 0)
            current = pos.get('current_price', entry)

            if entry and current and entry > 0:
                pnl_pct = ((current - entry) / entry) * 100
                if pnl_pct >= 0:
                    pnl_str = f"[green]+{pnl_pct:.1f}%[/green]"
                else:
                    pnl_str = f"[red]{pnl_pct:.1f}%[/red]"
            else:
                pnl_str = "--"

            status = "de-risked" if pos.get('de_risked') else "watching"
            entry_str = f"${entry:.4f}" if entry else "--"
            current_str = f"${current:.4f}" if current else "--"

            lines.append(f"  {symbol:<10} {entry_str:>12} {current_str:>12} {pnl_str:>10} {status:<15}")

    lines.append("")

    # Recent Activity
    lines.append("--- RECENT ACTIVITY ---")
    activity_shown = 0

    for trade in state.recent_trades[:5]:
        ts_str = trade.get('ts', '')[:19]
        symbol = trade['symbol']
        pnl = trade['pnl_usd']
        reason = trade['exit_reason']

        pnl_str = format_pnl(pnl)
        lines.append(f"  {ts_str[11:19]}  {reason:<6} on {symbol:<8} -> {pnl_str}")
        activity_shown += 1

    for event in state.recent_events[:5]:
        if activity_shown >= 8:
            break

        ts = event['ts']
        event_type = event['type']
        data = event.get('data', {})

        if event_type == 'exit_simulated':
            symbol = data.get('symbol', '???')
            lines.append(f"  {ts.strftime('%H:%M:%S')}  Exit simulated: {symbol}")
            activity_shown += 1
        elif event_type == 'rpc_rotation':
            lines.append(f"  {ts.strftime('%H:%M:%S')}  [yellow]RPC rotated[/yellow]")
            activity_shown += 1
        elif 'jito' in event_type:
            symbol = data.get('symbol', '???')
            lines.append(f"  {ts.strftime('%H:%M:%S')}  [cyan]Jito bundle[/cyan]: {symbol}")
            activity_shown += 1

    if activity_shown == 0:
        lines.append("  [dim]No recent activity - waiting for signals...[/dim]")

    lines.append("")

    # Stats footer
    lines.append("-" * width)

    if state.session_trades > 0:
        win_rate = (state.session_wins / state.session_trades) * 100
        session_record = f"{state.session_wins}W/{state.session_trades - state.session_wins}L ({win_rate:.0f}%)"
    else:
        session_record = "0 trades"

    session_pnl_str = format_pnl(state.session_pnl)
    today_pnl_str = format_pnl(state.today_pnl)

    rpc_info = f"{state.rpc_health.get('active', 'unknown')} ({state.rpc_health.get('pool_size', 0)} in pool)"

    lines.append(f"  SESSION: {session_pnl_str} {session_record}")
    lines.append(f"  TODAY:   {today_pnl_str} ({state.today_trades} trades)")
    lines.append(f"  RPC:     {rpc_info}")
    lines.append("-" * width)

    return "\n".join(lines)


def print_dashboard(state: DashboardState):
    """Print the dashboard to console."""
    console.clear()
    output = render_dashboard(state)

    if HAS_RICH:
        console.print(output)
    else:
        # Strip rich markup for basic console
        import re
        output = re.sub(r'\[/?[^\]]+\]', '', output)
        print(output)


async def update_state(state: DashboardState):
    """Update dashboard state with latest data."""
    state.last_update = datetime.now()
    state.update_mode()

    # Load positions
    if HAS_STORE:
        try:
            store = PositionStore()
            state.positions = store.get_all_positions(status='open')
        except Exception:
            pass

    # Load recent events
    state.recent_events = load_recent_events(limit=20)

    # Load recent trades
    state.recent_trades = load_recent_trades(limit=10)

    # Load RPC health
    state.rpc_health = load_rpc_health()

    # Calculate session stats
    calculate_session_stats(state)


async def run_dashboard(refresh_rate: float = 3.0):
    """Main dashboard loop."""
    state = DashboardState()

    print("Starting Moon Dev Dashboard...")
    print("Press Ctrl+C to exit\n")

    try:
        while True:
            await update_state(state)
            print_dashboard(state)
            await asyncio.sleep(refresh_rate)
    except KeyboardInterrupt:
        print("\nDashboard stopped.")


def main():
    parser = argparse.ArgumentParser(description='Live trading dashboard')
    parser.add_argument('--refresh', type=float, default=3.0, help='Refresh rate in seconds')
    args = parser.parse_args()

    try:
        asyncio.run(run_dashboard(refresh_rate=args.refresh))
    except KeyboardInterrupt:
        print("\nDashboard stopped.")


if __name__ == '__main__':
    main()
