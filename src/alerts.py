#!/usr/bin/env python3
"""Trading Alerts: Discord notifications for trades and system events.

Sends formatted alerts to Discord when:
- Trades execute (TP1, TP2, SL)
- System events occur (RPC rotation, errors)
- Daily summaries

Requires DISCORD_WEBHOOK or DISCORD_WEBHOOK_URL environment variable.

Usage:
    from src.alerts import send_trade_alert, send_system_alert, send_daily_summary

    # Trade alert
    send_trade_alert(
        symbol='SOL',
        side='SELL',
        entry_price=150.0,
        exit_price=165.0,
        pnl_usd=7.50,
        pnl_pct=10.0,
        exit_reason='TP1',
    )

    # System alert
    send_system_alert('RPC rotated', 'Helius -> Alchemy (429 rate limit)')

    # Daily summary
    send_daily_summary()
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Try to import requests for webhook calls
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    try:
        import httpx
        HAS_REQUESTS = False
        HAS_HTTPX = True
    except ImportError:
        HAS_REQUESTS = False
        HAS_HTTPX = False

# Try to import position store for summaries
try:
    from src.position_store import PositionStore, get_performance_summary
    HAS_STORE = True
except ImportError:
    HAS_STORE = False

# Alert settings
ALERT_ENABLED = True
MIN_PNL_FOR_ALERT = 0.0  # Alert on all trades, set higher to filter noise


def _get_webhook_url() -> Optional[str]:
    """Get Discord webhook URL from environment."""
    # Prefer environment variables so operators can rotate webhooks without code edits.
    env_url = os.getenv("DISCORD_WEBHOOK") or os.getenv("DISCORD_WEBHOOK_URL")
    if env_url:
        return env_url

    # Fallback: explicit configuration in src.config if present.
    try:
        # import config lazily to avoid circular imports during early init
        from src import config
        cfg = getattr(config, 'DISCORD_WEBHOOK_URL', None)
        if cfg:
            return cfg
    except Exception:
        pass

    return None


def _send_discord(content: str = None, embed: dict = None) -> bool:
    """Send a message to Discord webhook.

    Args:
        content: Plain text message
        embed: Rich embed object (Discord embed format)

    Returns:
        True if sent successfully, False otherwise
    """
    webhook = _get_webhook_url()
    if not webhook:
        print(f"[Discord disabled] {content or embed.get('title', 'Alert')}")
        return False

    payload = {}
    if content:
        payload['content'] = content
    if embed:
        payload['embeds'] = [embed]

    import time as _time

    for attempt in range(3):
        try:
            if HAS_REQUESTS:
                res = requests.post(webhook, json=payload, timeout=10)
            elif HAS_HTTPX:
                import httpx
                res = httpx.post(webhook, json=payload, timeout=10)
            else:
                print(f"[No HTTP library] {content or 'Alert'}")
                return False

            if res.status_code in (200, 204):
                return True

            # Handle rate limiting with Retry-After header
            if res.status_code == 429:
                retry_after = float(res.headers.get('Retry-After', 2 ** attempt))
                _time.sleep(min(retry_after, 10))
                continue

            # Other error — retry with backoff
            _time.sleep(2 ** attempt)

        except Exception as e:
            print(f"[Discord error attempt {attempt + 1}] {e}")
            if attempt < 2:
                _time.sleep(2 ** attempt)

    return False


def send_trade_alert(
    symbol: str,
    side: str,
    entry_price: float,
    exit_price: float,
    pnl_usd: float,
    pnl_pct: float,
    exit_reason: str,
    amount_usd: Optional[float] = None,
    mint: Optional[str] = None,
    session_pnl: Optional[float] = None,
    session_record: Optional[str] = None,
) -> bool:
    """Send a trade alert to Discord.

    Args:
        symbol: Token symbol (e.g., 'SOL')
        side: Trade side ('BUY' or 'SELL')
        entry_price: Entry price in USD
        exit_price: Exit price in USD
        pnl_usd: Profit/loss in USD
        pnl_pct: Profit/loss percentage
        exit_reason: Why the trade closed (TP1, TP2, SL, manual)
        amount_usd: Position size in USD
        mint: Token mint address
        session_pnl: Current session P&L
        session_record: Session win/loss record (e.g., "5W/2L")

    Returns:
        True if sent successfully
    """
    if not ALERT_ENABLED:
        return False

    # Skip small P&L if configured
    if abs(pnl_usd) < MIN_PNL_FOR_ALERT:
        return False

    # Determine color and emoji
    if pnl_usd > 0:
        color = 0x00FF00  # Green
        emoji = "🟢"
        pnl_str = f"+${pnl_usd:.2f}"
        pct_str = f"+{pnl_pct:.1f}%"
    else:
        color = 0xFF0000  # Red
        emoji = "🔴"
        pnl_str = f"${pnl_usd:.2f}"
        pct_str = f"{pnl_pct:.1f}%"

    # Exit reason formatting
    reason_display = {
        'TP1': 'Take Profit 1',
        'TP2': 'Take Profit 2',
        'SL': 'Stop Loss',
        'manual': 'Manual Exit',
        'shadow': 'Shadow Mode',
    }.get(exit_reason, exit_reason)

    # Build embed
    embed = {
        'title': f"{emoji} {exit_reason} Hit: {symbol}",
        'color': color,
        'fields': [
            {
                'name': 'P&L',
                'value': f"**{pnl_str}** ({pct_str})",
                'inline': True,
            },
            {
                'name': 'Entry → Exit',
                'value': f"${entry_price:.4f} → ${exit_price:.4f}",
                'inline': True,
            },
        ],
        'footer': {
            'text': f"Moon Dev Trading System • {datetime.now(timezone.utc).strftime('%H:%M:%S')}",
            },
            'timestamp': datetime.now(timezone.utc).isoformat(),
    }

    # Add position size if available
    if amount_usd:
        embed['fields'].append({
            'name': 'Size',
            'value': f"${amount_usd:.2f}",
            'inline': True,
        })

    # Add session stats if available
    if session_pnl is not None or session_record:
        session_str = ""
        if session_pnl is not None:
            session_str = f"${session_pnl:+.2f}"
        if session_record:
            session_str += f" ({session_record})" if session_str else session_record
        if session_str:
            embed['fields'].append({
                'name': 'Session',
                'value': session_str,
                'inline': True,
            })

    return _send_discord(embed=embed)


def send_system_alert(
    title: str,
    description: str,
    level: str = 'warning',
    fields: Optional[list] = None,
) -> bool:
    """Send a system alert to Discord.

    Args:
        title: Alert title
        description: Alert description
        level: Alert level ('info', 'warning', 'error', 'critical')
        fields: Optional list of {'name': str, 'value': str} dicts

    Returns:
        True if sent successfully
    """
    if not ALERT_ENABLED:
        return False

    # Color and emoji by level
    levels = {
        'info': (0x0099FF, 'ℹ️'),      # Blue
        'warning': (0xFFAA00, '⚠️'),   # Orange
        'error': (0xFF0000, '❌'),      # Red
        'critical': (0xFF0000, '🚨'),   # Red with siren
        'success': (0x00FF00, '✅'),    # Green
    }
    color, emoji = levels.get(level, levels['info'])

    embed = {
        'title': f"{emoji} {title}",
        'description': description,
        'color': color,
        'footer': {
            'text': f"Moon Dev System • {datetime.now(timezone.utc).strftime('%H:%M:%S')}",
        },
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }

    if fields:
        embed['fields'] = [
            {'name': f['name'], 'value': f['value'], 'inline': f.get('inline', True)}
            for f in fields
        ]

    return _send_discord(embed=embed)


def send_daily_summary() -> bool:
    """Send a daily performance summary to Discord.

    Returns:
        True if sent successfully
    """
    if not HAS_STORE:
        return send_system_alert(
            'Daily Summary Unavailable',
            'Position store not available for summary generation.',
            level='warning'
        )

    try:
        perf = get_performance_summary(days=1)
    except Exception as e:
        return send_system_alert(
            'Daily Summary Error',
            f'Failed to generate summary: {e}',
            level='error'
        )

    if perf['total_trades'] == 0:
        return send_system_alert(
            'Daily Summary',
            'No trades today. System is running in shadow mode.',
            level='info'
        )

    # Determine overall color
    if perf['net_pnl'] > 0:
        color = 0x00FF00  # Green
        emoji = "📈"
        verdict = "Profitable day!"
    elif perf['net_pnl'] < 0:
        color = 0xFF0000  # Red
        emoji = "📉"
        verdict = "Loss day - review strategy"
    else:
        color = 0xFFAA00  # Orange
        emoji = "📊"
        verdict = "Break-even"

    pnl_str = f"${perf['net_pnl']:+.2f}"

    embed = {
        'title': f"{emoji} Daily Summary",
        'description': verdict,
        'color': color,
        'fields': [
            {
                'name': 'Net P&L',
                'value': f"**{pnl_str}**",
                'inline': True,
            },
            {
                'name': 'Trades',
                'value': f"{perf['total_trades']} ({perf['winners']}W/{perf['losers']}L)",
                'inline': True,
            },
            {
                'name': 'Win Rate',
                'value': f"{perf['win_rate']:.1f}%",
                'inline': True,
            },
            {
                'name': 'Best Trade',
                'value': f"${perf['best_trade']:+.2f}",
                'inline': True,
            },
            {
                'name': 'Worst Trade',
                'value': f"${perf['worst_trade']:.2f}",
                'inline': True,
            },
            {
                'name': 'Profit Factor',
                'value': f"{perf['profit_factor']:.2f}" if perf['profit_factor'] != float('inf') else "∞",
                'inline': True,
            },
        ],
        'footer': {
            'text': f"Moon Dev Trading System • {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        },
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }

    return _send_discord(embed=embed)


def send_startup_alert(mode: str = 'SHADOW') -> bool:
    """Send a startup notification."""
    return send_system_alert(
        'System Started',
        f'Moon Dev Trading System is now running in **{mode}** mode.',
        level='success',
        fields=[
            {'name': 'Mode', 'value': mode, 'inline': True},
            {'name': 'Time', 'value': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'), 'inline': True},
        ]
    )


def send_shutdown_alert(reason: str = 'Manual shutdown') -> bool:
    """Send a shutdown notification."""
    return send_system_alert(
        'System Stopped',
        f'Moon Dev Trading System has stopped.',
        level='warning',
        fields=[
            {'name': 'Reason', 'value': reason, 'inline': True},
            {'name': 'Time', 'value': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'), 'inline': True},
        ]
    )


def send_rpc_alert(old_rpc: str, new_rpc: str, reason: str) -> bool:
    """Send RPC rotation alert."""
    # Extract provider names
    def get_provider(url: str) -> str:
        if 'helius' in url.lower():
            return 'Helius'
        elif 'alchemy' in url.lower():
            return 'Alchemy'
        elif 'chainstack' in url.lower():
            return 'Chainstack'
        elif 'ankr' in url.lower():
            return 'Ankr'
        else:
            return url[:25] + '...'

    return send_system_alert(
        'RPC Rotated',
        f'{get_provider(old_rpc)} → {get_provider(new_rpc)}',
        level='warning',
        fields=[
            {'name': 'Reason', 'value': reason, 'inline': True},
        ]
    )


def send_error_alert(error_type: str, message: str, details: Optional[str] = None) -> bool:
    """Send an error alert."""
    fields = [{'name': 'Error', 'value': message, 'inline': False}]
    if details:
        fields.append({'name': 'Details', 'value': details[:500], 'inline': False})

    return send_system_alert(
        f'Error: {error_type}',
        'An error occurred in the trading system.',
        level='error',
        fields=fields,
    )


# Quick test function
def test_alerts():
    """Test Discord alerts (run manually)."""
    print("Testing Discord alerts...")

    webhook = _get_webhook_url()
    if not webhook:
        print("No DISCORD_WEBHOOK configured. Set it in .env to test.")
        return

    # Test trade alert
    print("Sending test trade alert...")
    send_trade_alert(
        symbol='TEST',
        side='SELL',
        entry_price=100.0,
        exit_price=110.0,
        pnl_usd=2.50,
        pnl_pct=10.0,
        exit_reason='TP1',
        amount_usd=25.0,
        session_pnl=15.0,
        session_record='3W/1L',
    )

    # Test system alert
    print("Sending test system alert...")
    send_system_alert(
        'Test Alert',
        'This is a test alert from Moon Dev Trading System.',
        level='info',
    )

    print("Done! Check your Discord channel.")


if __name__ == '__main__':
    test_alerts()
