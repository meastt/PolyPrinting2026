"""
Alerts Module

Send notifications via Discord, Telegram, or Email when
important events occur in the trading bot.

Usage:
    alerts = AlertManager(config)
    alerts.send("Trade executed: YES $2 @ 0.55 on BTCUSD")
    alerts.send_trade(trade_details)
    alerts.send_daily_summary(stats)
"""

import os
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, Any, Optional, List
from datetime import datetime
from dataclasses import dataclass
from enum import Enum

import requests

from src.utils.logger import get_logger

logger = get_logger(__name__)


class AlertType(Enum):
    """Types of alerts."""
    TRADE_EXECUTED = "trade_executed"
    DAILY_SUMMARY = "daily_summary"
    ERROR = "error"
    DRAWDOWN_WARNING = "drawdown_warning"
    BIG_WIN = "big_win"
    EMERGENCY_STOP = "emergency_stop"
    BOT_STARTED = "bot_started"
    BOT_STOPPED = "bot_stopped"


@dataclass
class Alert:
    """Alert message."""
    type: AlertType
    title: str
    message: str
    timestamp: datetime
    data: Optional[Dict[str, Any]] = None


class DiscordAlert:
    """Send alerts to Discord via webhook."""

    def __init__(self, webhook_url: str):
        """
        Initialize Discord alerter.

        Args:
            webhook_url: Discord webhook URL
        """
        self.webhook_url = webhook_url

    def send(self, alert: Alert) -> bool:
        """Send alert to Discord."""
        try:
            # Format message with emoji based on type
            emoji_map = {
                AlertType.TRADE_EXECUTED: "📈",
                AlertType.DAILY_SUMMARY: "📊",
                AlertType.ERROR: "🚨",
                AlertType.DRAWDOWN_WARNING: "⚠️",
                AlertType.BIG_WIN: "🎉",
                AlertType.EMERGENCY_STOP: "🛑",
                AlertType.BOT_STARTED: "🟢",
                AlertType.BOT_STOPPED: "🔴",
            }

            emoji = emoji_map.get(alert.type, "📌")

            # Build embed for rich formatting
            embed = {
                "title": f"{emoji} {alert.title}",
                "description": alert.message,
                "timestamp": alert.timestamp.isoformat(),
                "color": self._get_color(alert.type),
            }

            # Add fields from data
            if alert.data:
                fields = []
                for key, value in alert.data.items():
                    fields.append({
                        "name": key.replace("_", " ").title(),
                        "value": str(value),
                        "inline": True,
                    })
                embed["fields"] = fields[:25]  # Discord limit

            payload = {"embeds": [embed]}

            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=10,
            )

            if response.status_code == 204:
                return True
            else:
                logger.warning(f"Discord alert failed: {response.status_code}")
                return False

        except Exception as e:
            logger.error(f"Discord alert error: {e}")
            return False

    def _get_color(self, alert_type: AlertType) -> int:
        """Get Discord embed color."""
        colors = {
            AlertType.TRADE_EXECUTED: 0x00FF00,  # Green
            AlertType.DAILY_SUMMARY: 0x0099FF,  # Blue
            AlertType.ERROR: 0xFF0000,  # Red
            AlertType.DRAWDOWN_WARNING: 0xFFAA00,  # Orange
            AlertType.BIG_WIN: 0xFFD700,  # Gold
            AlertType.EMERGENCY_STOP: 0xFF0000,  # Red
            AlertType.BOT_STARTED: 0x00FF00,  # Green
            AlertType.BOT_STOPPED: 0x808080,  # Gray
        }
        return colors.get(alert_type, 0x808080)


class TelegramAlert:
    """Send alerts to Telegram."""

    def __init__(self, bot_token: str, chat_id: str):
        """
        Initialize Telegram alerter.

        Args:
            bot_token: Telegram bot token
            chat_id: Chat ID to send messages to
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{bot_token}"

    def send(self, alert: Alert) -> bool:
        """Send alert to Telegram."""
        try:
            # Format message
            emoji_map = {
                AlertType.TRADE_EXECUTED: "📈",
                AlertType.DAILY_SUMMARY: "📊",
                AlertType.ERROR: "🚨",
                AlertType.DRAWDOWN_WARNING: "⚠️",
                AlertType.BIG_WIN: "🎉",
                AlertType.EMERGENCY_STOP: "🛑",
                AlertType.BOT_STARTED: "🟢",
                AlertType.BOT_STOPPED: "🔴",
            }

            emoji = emoji_map.get(alert.type, "📌")

            # Build message text
            lines = [
                f"{emoji} *{alert.title}*",
                "",
                alert.message,
            ]

            if alert.data:
                lines.append("")
                for key, value in alert.data.items():
                    lines.append(f"• {key.replace('_', ' ').title()}: `{value}`")

            lines.append(f"\n_{alert.timestamp.strftime('%Y-%m-%d %H:%M:%S')}_")

            text = "\n".join(lines)

            response = requests.post(
                f"{self.api_url}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                },
                timeout=10,
            )

            if response.status_code == 200:
                return True
            else:
                logger.warning(f"Telegram alert failed: {response.text}")
                return False

        except Exception as e:
            logger.error(f"Telegram alert error: {e}")
            return False


class EmailAlert:
    """Send alerts via email."""

    def __init__(
        self,
        smtp_server: str,
        smtp_port: int,
        username: str,
        password: str,
        to_address: str,
        from_address: Optional[str] = None,
    ):
        """
        Initialize email alerter.

        Args:
            smtp_server: SMTP server hostname
            smtp_port: SMTP port (usually 587 for TLS)
            username: SMTP username
            password: SMTP password
            to_address: Recipient email address
            from_address: Sender email address (defaults to username)
        """
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
        self.to_address = to_address
        self.from_address = from_address or username

    def send(self, alert: Alert) -> bool:
        """Send alert via email."""
        try:
            # Create message
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"[Trading Bot] {alert.title}"
            msg["From"] = self.from_address
            msg["To"] = self.to_address

            # Plain text version
            text_content = f"{alert.title}\n\n{alert.message}"
            if alert.data:
                text_content += "\n\nDetails:\n"
                for key, value in alert.data.items():
                    text_content += f"  {key}: {value}\n"

            # HTML version
            html_content = f"""
            <html>
            <body>
                <h2>{alert.title}</h2>
                <p>{alert.message}</p>
            """

            if alert.data:
                html_content += "<table border='1' cellpadding='5'>"
                for key, value in alert.data.items():
                    html_content += f"<tr><td><b>{key}</b></td><td>{value}</td></tr>"
                html_content += "</table>"

            html_content += f"""
                <p><small>Sent at {alert.timestamp}</small></p>
            </body>
            </html>
            """

            msg.attach(MIMEText(text_content, "plain"))
            msg.attach(MIMEText(html_content, "html"))

            # Send
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.username, self.password)
                server.sendmail(self.from_address, self.to_address, msg.as_string())

            return True

        except Exception as e:
            logger.error(f"Email alert error: {e}")
            return False


class AlertManager:
    """
    Manages all alert channels.

    Sends alerts to configured channels (Discord, Telegram, Email)
    based on alert type and configuration.
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize alert manager.

        Args:
            config: Alert configuration from config.yaml
        """
        self.config = config
        self.alerters: List[tuple] = []  # (alerter, enabled_events)

        self._init_alerters()

    def _init_alerters(self) -> None:
        """Initialize configured alert channels."""
        alerts_config = self.config.get("alerts", {})

        # Discord
        discord_config = alerts_config.get("discord", {})
        if discord_config.get("enabled") and discord_config.get("webhook_url"):
            alerter = DiscordAlert(discord_config["webhook_url"])
            events = discord_config.get("events", ["all"])
            self.alerters.append((alerter, events))
            logger.info("Discord alerts enabled")

        # Telegram
        telegram_config = alerts_config.get("telegram", {})
        if telegram_config.get("enabled"):
            if telegram_config.get("bot_token") and telegram_config.get("chat_id"):
                alerter = TelegramAlert(
                    telegram_config["bot_token"],
                    telegram_config["chat_id"],
                )
                events = telegram_config.get("events", ["all"])
                self.alerters.append((alerter, events))
                logger.info("Telegram alerts enabled")

        # Email
        email_config = alerts_config.get("email", {})
        if email_config.get("enabled"):
            alerter = EmailAlert(
                smtp_server=email_config.get("smtp_server", "smtp.gmail.com"),
                smtp_port=email_config.get("smtp_port", 587),
                username=email_config["username"],
                password=email_config["password"],
                to_address=email_config["to_address"],
            )
            events = email_config.get("events", ["daily_summary", "error", "emergency_stop"])
            self.alerters.append((alerter, events))
            logger.info("Email alerts enabled")

        if not self.alerters:
            logger.info("No alert channels configured")

    def send(
        self,
        alert_type: AlertType,
        title: str,
        message: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Send alert to all configured channels.

        Args:
            alert_type: Type of alert
            title: Alert title
            message: Alert message
            data: Additional data to include
        """
        alert = Alert(
            type=alert_type,
            title=title,
            message=message,
            timestamp=datetime.now(),
            data=data,
        )

        for alerter, events in self.alerters:
            # Check if this event type is enabled
            if "all" in events or alert_type.value in events:
                try:
                    alerter.send(alert)
                except Exception as e:
                    logger.error(f"Alert send error: {e}")

    def send_trade(self, trade: Dict[str, Any]) -> None:
        """Send trade executed alert."""
        side = trade.get("side", "unknown").upper()
        price = trade.get("price", 0)
        size = trade.get("size", 0)
        ticker = trade.get("ticker", "unknown")

        self.send(
            alert_type=AlertType.TRADE_EXECUTED,
            title=f"Trade Executed: {side}",
            message=f"Placed {side} order on {ticker}",
            data={
                "ticker": ticker,
                "side": side,
                "price": f"${price:.2f}",
                "size": f"${size:.2f}",
                "strategy": trade.get("strategy", "unknown"),
                "ev": f"{trade.get('ev', 0):.1%}",
            },
        )

    def send_daily_summary(self, stats: Dict[str, Any]) -> None:
        """Send daily summary alert."""
        pnl = stats.get("daily_pnl", 0)
        pnl_pct = stats.get("daily_pnl_pct", 0)

        emoji = "📈" if pnl >= 0 else "📉"

        self.send(
            alert_type=AlertType.DAILY_SUMMARY,
            title=f"{emoji} Daily Summary",
            message=f"Today's P&L: ${pnl:.2f} ({pnl_pct:.1f}%)",
            data={
                "balance": f"${stats.get('balance', 0):.2f}",
                "trades": stats.get("trades_today", 0),
                "win_rate": f"{stats.get('win_rate', 0):.1f}%",
                "open_positions": stats.get("open_positions", 0),
            },
        )

    def send_error(self, error: str, details: Optional[str] = None) -> None:
        """Send error alert."""
        self.send(
            alert_type=AlertType.ERROR,
            title="Bot Error",
            message=error,
            data={"details": details} if details else None,
        )

    def send_drawdown_warning(self, current_drawdown: float, limit: float) -> None:
        """Send drawdown warning alert."""
        self.send(
            alert_type=AlertType.DRAWDOWN_WARNING,
            title="Drawdown Warning",
            message=f"Daily loss at {current_drawdown:.1%}, approaching {limit:.1%} limit",
            data={
                "current_drawdown": f"{current_drawdown:.1%}",
                "limit": f"{limit:.1%}",
                "remaining": f"{(limit - current_drawdown):.1%}",
            },
        )

    def send_big_win(self, profit: float, trade: Dict[str, Any]) -> None:
        """Send big win alert."""
        self.send(
            alert_type=AlertType.BIG_WIN,
            title=f"Big Win! +${profit:.2f}",
            message=f"Position resolved with ${profit:.2f} profit",
            data={
                "ticker": trade.get("ticker", "unknown"),
                "profit": f"${profit:.2f}",
                "entry_price": f"${trade.get('entry_price', 0):.2f}",
            },
        )

    def send_emergency_stop(self, reason: str) -> None:
        """Send emergency stop alert."""
        self.send(
            alert_type=AlertType.EMERGENCY_STOP,
            title="EMERGENCY STOP TRIGGERED",
            message=f"Trading halted: {reason}",
            data={"reason": reason},
        )

    def send_bot_started(self, config: Dict[str, Any]) -> None:
        """Send bot started alert."""
        self.send(
            alert_type=AlertType.BOT_STARTED,
            title="Bot Started",
            message="Trading bot is now running",
            data={
                "exchange": config.get("exchange", "kalshi"),
                "mode": config.get("mode", "simulation"),
                "balance": f"${config.get('balance', 0):.2f}",
            },
        )

    def send_bot_stopped(self, reason: str = "User requested") -> None:
        """Send bot stopped alert."""
        self.send(
            alert_type=AlertType.BOT_STOPPED,
            title="Bot Stopped",
            message=f"Trading bot has stopped: {reason}",
        )


# Global alert manager instance (initialized by trading loop)
_alert_manager: Optional[AlertManager] = None


def init_alerts(config: Dict[str, Any]) -> AlertManager:
    """Initialize global alert manager."""
    global _alert_manager
    _alert_manager = AlertManager(config)
    return _alert_manager


def get_alerts() -> Optional[AlertManager]:
    """Get global alert manager."""
    return _alert_manager
