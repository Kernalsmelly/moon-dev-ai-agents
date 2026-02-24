"""
Telegram Alerts for Polymarket Micro-Edge Agent

Sends notifications when:
- High-edge signals are detected
- Trades are executed
- Performance milestones are hit

Setup:
1. Message @BotFather on Telegram
2. Create a new bot with /newbot
3. Copy the token to .env as TELEGRAM_BOT_TOKEN
4. Message your bot to start a chat
5. Get your chat_id from https://api.telegram.org/bot<TOKEN>/getUpdates
6. Add TELEGRAM_CHAT_ID to .env
"""

import os
import httpx
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)

# We keep the module name for backward compatibility, but switch to a
# Discord-webhook-backed sender so alerts go to the configured webhook.
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "") or os.getenv("DISCORD_WEBHOOK", "")


class TelegramAlerts:
    """Compatibility shim: preserves API but sends to Discord webhook instead of Telegram.

    This avoids changing callers across the repo while ensuring all alerts
    go to the single configured Discord webhook.
    """

    def __init__(self, bot_token: str = None, chat_id: str = None):
        self.webhook = DISCORD_WEBHOOK_URL
        self.enabled = bool(self.webhook)
        self.client = httpx.Client(timeout=10.0)

        if not self.enabled:
            logger.warning("Discord webhook not configured. Set DISCORD_WEBHOOK_URL in .env to enable alerts.")

    def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send a message to the configured Discord webhook.

        Returns True on success (HTTP 200/204).
        """
        if not self.enabled:
            return False

        try:
            # Send as regular content (not embed) for proper markdown rendering
            payload = {"content": text}
            resp = self.client.post(self.webhook, json=payload)
            return resp.status_code in (200, 204)
        except Exception as e:
            logger.error(f"Discord webhook error: {e}")
            return False

    def send_embed(self, title: str, fields: list, color: int = 0x00FF00) -> bool:
        """Send a rich embed to Discord."""
        if not self.enabled:
            return False

        try:
            embed = {
                "title": title,
                "color": color,
                "fields": fields,
                "footer": {"text": "Polymarket Micro-Edge Bot"}
            }
            payload = {"embeds": [embed]}
            resp = self.client.post(self.webhook, json=payload)
            return resp.status_code in (200, 204)
        except Exception as e:
            logger.error(f"Discord webhook error: {e}")
            return False

    def alert_signal(self, signal: Dict) -> bool:
        """Send alert for a new high-edge signal."""
        edge = signal.get('edge_pct', 0)
        side = signal.get('side', '?')
        entry = signal.get('entry_price', 0)
        signal_type = signal.get('signal_type', 'unknown')
        question = signal.get('question', signal.get('notes', ''))[:80]

        emoji = "🟢" if side == "YES" else "🔴"
        color = 0x00FF00 if side == "YES" else 0xFF5555

        fields = [
            {"name": "Side", "value": f"{emoji} {side} @ ${entry:.2f}", "inline": True},
            {"name": "Edge", "value": f"{edge:.1f}%", "inline": True},
            {"name": "Type", "value": signal_type, "inline": True},
            {"name": "Market", "value": question, "inline": False},
        ]

        return self.send_embed(f"{emoji} NEW SIGNAL", fields, color)

    def alert_trade(self, trade: Dict, is_entry: bool = True) -> bool:
        """Send alert for executed trade."""
        side = trade.get('side', '?')
        price = trade.get('entry_price' if is_entry else 'exit_price', 0)
        amount = trade.get('amount_usd', 0)
        question = trade.get('question', '')[:50]

        action = "OPENED" if is_entry else "CLOSED"
        emoji = "📈" if is_entry else "📉"

        pnl_text = ""
        if not is_entry and 'pnl_usd' in trade:
            pnl = trade['pnl_usd']
            pnl_emoji = "✅" if pnl >= 0 else "❌"
            pnl_text = f"\n**PnL:** {pnl_emoji} ${pnl:+.2f}"

        text = f"""{emoji} **TRADE {action}** {emoji}

**Side:** {side} @ ${price:.2f}
**Amount:** ${amount:.2f}{pnl_text}

**Market:** {question}..."""
        return self.send_message(text.strip())

    def alert_performance(self, stats: Dict) -> bool:
        """Send daily performance summary."""
        total_pnl = stats.get('total_pnl', 0)
        win_rate = stats.get('win_rate', 0)
        total_trades = stats.get('total_trades', 0)

        emoji = "🎉" if total_pnl >= 0 else "😔"

        text = f"""{emoji} **DAILY SUMMARY** {emoji}

**Total PnL:** ${total_pnl:+.2f}
**Win Rate:** {win_rate:.1f}%
**Trades:** {total_trades}

_Polymarket Micro-Edge Bot_"""
        return self.send_message(text.strip())

    def alert_startup(self, config: Dict) -> bool:
        """Send startup notification."""
        mode = "🔴 LIVE" if not config.get('dry_run', True) else "🟡 DRY RUN"
        position_size = config.get('position_size', 10)

        text = f"""🚀 **BOT STARTED** 🚀

**Mode:** {mode}
**Position Size:** ${position_size}
**Scan Interval:** 120s

_Polymarket Micro-Edge Bot_"""
        return self.send_message(text.strip())

    def close(self):
        """Close HTTP client."""
        self.client.close()


# Module-level instance
_alerts: Optional[TelegramAlerts] = None


def get_alerts() -> TelegramAlerts:
    """Get global TelegramAlerts instance."""
    global _alerts
    if _alerts is None:
        _alerts = TelegramAlerts()
    return _alerts


def send_signal_alert(signal: Dict) -> bool:
    """Convenience function to send signal alert."""
    return get_alerts().alert_signal(signal)


def send_trade_alert(trade: Dict, is_entry: bool = True) -> bool:
    """Convenience function to send trade alert."""
    return get_alerts().alert_trade(trade, is_entry)
