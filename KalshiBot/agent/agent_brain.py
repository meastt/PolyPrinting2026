#!/usr/bin/env python3
"""
Agent Brain - Telegram Bot for KXBTC Momentum Trader

Provides Telegram commands to monitor and control the momentum trading bot.
"""

import os
import sys
import json
import time
import logging
from datetime import datetime
from pathlib import Path

import telebot
from dotenv import load_dotenv

load_dotenv()

# =============================================================================
# Configuration
# =============================================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

MOMENTUM_STATE_PATH = Path("/app/config/momentum_state.json")
ARBITRAGE_STATE_PATH = Path("/app/config/arbitrage_state.json")
WEATHER_STATE_PATH = Path("/app/config/weather_state.json")
TRADING_SIGNALS_PATH = Path("/app/config/trading_signals.json")
WEATHER_SIGNALS_PATH = Path("/app/config/weather_signals.json")
BIAS_FILE_PATH = Path("/app/config/forecast_bias.json")
LOG_PATH = Path("/app/logs/agent.log")

if not TELEGRAM_BOT_TOKEN:
    sys.exit("ERROR: TELEGRAM_BOT_TOKEN not set")
if not TELEGRAM_CHAT_ID:
    sys.exit("ERROR: TELEGRAM_CHAT_ID not set")

# =============================================================================
# Logging
# =============================================================================

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format='%(asctime)s [AGENT] %(levelname)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_PATH) if LOG_PATH.parent.exists() else logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# =============================================================================
# Telegram Bot
# =============================================================================

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

def is_authorized(message) -> bool:
    """Check if message is from authorized user."""
    authorized = str(message.chat.id) == str(TELEGRAM_CHAT_ID)
    if not authorized:
        logger.warning(f"Unauthorized command from ChatID: {message.chat.id}")
    return authorized

# =============================================================================
# Command Handlers
# =============================================================================

@bot.message_handler(commands=['start', 'help'])
def cmd_help(message):
    if not is_authorized(message):
        return

    help_text = """🤖 *Kalshi Trading Bot*

*Commands:*
/status - Balance, Daily P&L, Active Positions
/weather - Weather predictions & positions
/bias - Forecast bias statistics
/markets - Active weather markets
/pause - Temporarily stop trading
/resume - Resume trading
/logs - System status & diagnostics
/kill - Emergency shutdown

*Strategies Running:*
• Momentum Trader (KXBTC 15-min markets)
• Arbitrage Scanner (Risk-free opportunities)
• Weather Predictor (Temperature markets)
"""
    bot.reply_to(message, help_text, parse_mode='Markdown')


@bot.message_handler(commands=['status'])
def cmd_status(message):
    if not is_authorized(message):
        return

    try:
        # Load momentum state
        momentum = {}
        if MOMENTUM_STATE_PATH.exists():
            with open(MOMENTUM_STATE_PATH, 'r') as f:
                momentum = json.load(f)

        # Load arbitrage state
        arbitrage = {}
        if ARBITRAGE_STATE_PATH.exists():
            with open(ARBITRAGE_STATE_PATH, 'r') as f:
                arbitrage = json.load(f)

        # Calculate totals
        momentum_balance = momentum.get('balance', 0.0)
        arbitrage_balance = arbitrage.get('balance', 0.0)
        total_balance = momentum_balance + arbitrage_balance

        momentum_pnl = momentum.get('daily_pnl', 0.0)
        arbitrage_pnl = arbitrage.get('daily_pnl', 0.0)
        total_pnl = momentum_pnl + arbitrage_pnl

        momentum_trades = momentum.get('trades_today', 0)
        arbitrage_trades = arbitrage.get('trades_today', 0)
        total_trades = momentum_trades + arbitrage_trades

        # Status indicators
        momentum_paused = momentum.get('paused', False)
        momentum_halted = momentum.get('halted', False)
        arbitrage_paused = arbitrage.get('paused', False)
        arbitrage_halted = arbitrage.get('halted', False)

        halted = momentum_halted or arbitrage_halted
        paused = (momentum_paused and arbitrage_paused) if not halted else False

        status_emoji = "🟢" if not paused and not halted else "🔴" if halted else "⏸️"

        status_text = f"""{status_emoji} *Trading Bot Status*

*Combined Balance:* ${total_balance:.2f}
*Daily P&L:* ${total_pnl:+.2f} ({(total_pnl/100)*100:+.1f}%)
*Total Trades Today:* {total_trades}

📊 *Momentum Trader:* {"⏸️" if momentum_paused else "🟢"}
  Balance: ${momentum_balance:.2f}
  P&L: ${momentum_pnl:+.2f}
  Trades: {momentum_trades}

🎰 *Arbitrage Scanner:* {"⏸️" if arbitrage_paused else "🟢"}
  Balance: ${arbitrage_balance:.2f}
  P&L: ${arbitrage_pnl:+.2f}
  Arb Profit: ${arbitrage.get('total_arb_profit', 0):+.2f}
  Trades: {arbitrage_trades}

*Overall Status:* {"HALTED (kill switch)" if halted else "PAUSED" if paused else "ACTIVE"}
"""
        bot.reply_to(message, status_text, parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, f"❌ Could not read bot status: {e}")


@bot.message_handler(commands=['pause'])
def cmd_pause(message):
    if not is_authorized(message):
        return

    try:
        # Pause momentum trader
        if MOMENTUM_STATE_PATH.exists():
            with open(MOMENTUM_STATE_PATH, 'r') as f:
                state = json.load(f)
            state['paused'] = True
            state['updated_at'] = int(time.time())
            with open(MOMENTUM_STATE_PATH, 'w') as f:
                json.dump(state, f, indent=2)

        # Pause arbitrage scanner
        if ARBITRAGE_STATE_PATH.exists():
            with open(ARBITRAGE_STATE_PATH, 'r') as f:
                state = json.load(f)
            state['paused'] = True
            state['updated_at'] = int(time.time())
            with open(ARBITRAGE_STATE_PATH, 'w') as f:
                json.dump(state, f, indent=2)

        bot.reply_to(message, "⏸️ *ALL TRADING PAUSED*\n\nBoth momentum and arbitrage bots paused.", parse_mode='Markdown')
        logger.info("All trading paused via Telegram")
    except Exception as e:
        bot.reply_to(message, f"❌ Failed to pause: {e}")


@bot.message_handler(commands=['resume'])
def cmd_resume(message):
    if not is_authorized(message):
        return

    try:
        # Resume momentum trader
        if MOMENTUM_STATE_PATH.exists():
            with open(MOMENTUM_STATE_PATH, 'r') as f:
                state = json.load(f)
            state['paused'] = False
            state['updated_at'] = int(time.time())
            with open(MOMENTUM_STATE_PATH, 'w') as f:
                json.dump(state, f, indent=2)

        # Resume arbitrage scanner
        if ARBITRAGE_STATE_PATH.exists():
            with open(ARBITRAGE_STATE_PATH, 'r') as f:
                state = json.load(f)
            state['paused'] = False
            state['updated_at'] = int(time.time())
            with open(ARBITRAGE_STATE_PATH, 'w') as f:
                json.dump(state, f, indent=2)

        bot.reply_to(message, "▶️ *ALL TRADING RESUMED*\n\nBoth momentum and arbitrage bots active.", parse_mode='Markdown')
        logger.info("All trading resumed via Telegram")
    except Exception as e:
        bot.reply_to(message, f"❌ Failed to resume: {e}")


@bot.message_handler(commands=['logs'])
def cmd_logs(message):
    if not is_authorized(message):
        return

    lines = ["🔍 *Trading Bot Diagnostics*\n"]
    now = int(time.time())

    # Check momentum state
    try:
        with open(MOMENTUM_STATE_PATH, 'r') as f:
            state = json.load(f)
        age = now - state.get('updated_at', 0)
        lines.append(f"*Momentum Trader:* {'🟢 Active' if age < 60 else '🟡 Stale'}")
        lines.append(f"  Last update: {age}s ago")
        lines.append(f"  Status: {'HALTED' if state.get('halted') else 'PAUSED' if state.get('paused') else 'RUNNING'}")
        lines.append(f"  Balance: ${state.get('balance', 0):.2f}")
        lines.append(f"  Daily P&L: ${state.get('daily_pnl', 0):+.2f}")
        if state.get('active_position'):
            pos = state['active_position']
            lines.append(f"  Position: {pos.get('ticker', 'Unknown')}")
    except Exception as e:
        lines.append(f"*Momentum Trader:* ❌ Error ({e})")

    lines.append("")

    # Check arbitrage state
    try:
        with open(ARBITRAGE_STATE_PATH, 'r') as f:
            state = json.load(f)
        age = now - state.get('updated_at', 0)
        lines.append(f"*Arbitrage Scanner:* {'🟢 Active' if age < 60 else '🟡 Stale'}")
        lines.append(f"  Last update: {age}s ago")
        lines.append(f"  Status: {'HALTED' if state.get('halted') else 'PAUSED' if state.get('paused') else 'RUNNING'}")
        lines.append(f"  Balance: ${state.get('balance', 0):.2f}")
        lines.append(f"  Daily P&L: ${state.get('daily_pnl', 0):+.2f}")
        lines.append(f"  Total Arb Profit: ${state.get('total_arb_profit', 0):+.2f}")
    except Exception as e:
        lines.append(f"*Arbitrage Scanner:* ❌ Error ({e})")

    lines.append("")
    lines.append("*Agent:* 🟢 OK (you're reading this)")

    bot.reply_to(message, "\n".join(lines), parse_mode='Markdown')


@bot.message_handler(commands=['weather'])
def cmd_weather(message):
    if not is_authorized(message):
        return

    try:
        # Load weather state
        weather = {}
        if WEATHER_STATE_PATH.exists():
            with open(WEATHER_STATE_PATH, 'r') as f:
                weather = json.load(f)

        # Load recent signals
        signals = []
        if WEATHER_SIGNALS_PATH.exists():
            with open(WEATHER_SIGNALS_PATH, 'r') as f:
                data = json.load(f)
                signals = data.get("signals", [])[-5:]  # Last 5 signals

        balance = weather.get('balance', 20.0)
        pnl = weather.get('daily_pnl', 0.0)
        trades = weather.get('trades_today', 0)

        status_text = f"""🌤️ *Weather Trading Status*

*Balance:* ${balance:.2f}
*Daily P&L:* ${pnl:+.2f}
*Trades Today:* {trades}
*Win Rate:* {weather.get('win_rate', 0):.0%}

*Recent Signals:*
"""
        if signals:
            for sig in signals:
                status = sig.get('status', 'UNKNOWN')
                emoji = "✅" if status == "EXECUTED" else "❌" if status == "FAILED" else "⏳"
                ticker = sig.get('ticker', 'Unknown')
                confidence = sig.get('confidence', 0)
                status_text += f"{emoji} {ticker} ({confidence:.0%})\n"
        else:
            status_text += "_No recent signals_\n"

        bot.reply_to(message, status_text, parse_mode='Markdown')

    except Exception as e:
        bot.reply_to(message, f"❌ Error loading weather data: {e}")


@bot.message_handler(commands=['bias'])
def cmd_bias(message):
    if not is_authorized(message):
        return

    try:
        if not BIAS_FILE_PATH.exists():
            bot.reply_to(message, "📊 No bias data available yet.\n\nBias data is collected over 180 days.")
            return

        with open(BIAS_FILE_PATH, 'r') as f:
            bias_data = json.load(f)

        if not bias_data or bias_data.get("_comment"):
            bot.reply_to(message, "📊 No bias data collected yet.\n\nData will appear after first calibration.")
            return

        status_text = "📊 *Forecast Bias Statistics*\n\n"

        for station, sources in list(bias_data.items())[:3]:  # Show first 3 stations
            status_text += f"*{station}:*\n"
            for source, metrics in sources.items():
                high_bias = metrics.get('high_bias', 0)
                low_bias = metrics.get('low_bias', 0)
                samples = metrics.get('high_samples', 0)
                status_text += f"  • {source}: {high_bias:+.1f}°F high, {low_bias:+.1f}°F low ({samples} samples)\n"
            status_text += "\n"

        bot.reply_to(message, status_text, parse_mode='Markdown')

    except Exception as e:
        bot.reply_to(message, f"❌ Error loading bias data: {e}")


@bot.message_handler(commands=['markets'])
def cmd_markets(message):
    if not is_authorized(message):
        return

    try:
        if not WEATHER_SIGNALS_PATH.exists():
            bot.reply_to(message, "No weather signals file found.")
            return

        with open(WEATHER_SIGNALS_PATH, 'r') as f:
            data = json.load(f)
            signals = data.get("signals", [])

        # Get active signals (pending)
        active = [s for s in signals if s.get('status') == 'PENDING']

        status_text = f"🎯 *Active Weather Markets*\n\n"

        if active:
            for sig in active[:10]:  # Show up to 10
                ticker = sig.get('ticker', 'Unknown')
                temp = sig.get('predicted_temp', 0)
                conf = sig.get('confidence', 0)
                price = sig.get('market_price', 0)
                status_text += f"• {ticker}\n  Pred: {temp:.1f}°F ({conf:.0%}) @ ${price:.2f}\n\n"
        else:
            status_text += "_No active signals_\n"

        bot.reply_to(message, status_text, parse_mode='Markdown')

    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")


@bot.message_handler(commands=['kill'])
def cmd_kill(message):
    if not is_authorized(message):
        return

    bot.reply_to(message, "☠️ *EMERGENCY STOP INITIATED*\n\nHalting all trading...", parse_mode='Markdown')

    try:
        # Halt momentum trader
        if MOMENTUM_STATE_PATH.exists():
            with open(MOMENTUM_STATE_PATH, 'r') as f:
                state = json.load(f)
            state['halted'] = True
            state['paused'] = True
            state['updated_at'] = int(time.time())
            with open(MOMENTUM_STATE_PATH, 'w') as f:
                json.dump(state, f, indent=2)

        # Halt arbitrage scanner
        if ARBITRAGE_STATE_PATH.exists():
            with open(ARBITRAGE_STATE_PATH, 'r') as f:
                state = json.load(f)
            state['halted'] = True
            state['paused'] = True
            state['updated_at'] = int(time.time())
            with open(ARBITRAGE_STATE_PATH, 'w') as f:
                json.dump(state, f, indent=2)

        # Halt weather trader
        if WEATHER_STATE_PATH.exists():
            with open(WEATHER_STATE_PATH, 'r') as f:
                state = json.load(f)
            state['halted'] = True
            state['paused'] = True
            state['updated_at'] = int(time.time())
            with open(WEATHER_STATE_PATH, 'w') as f:
                json.dump(state, f, indent=2)

        logger.critical("KILL COMMAND RECEIVED - All trading halted")
        bot.send_message(message.chat.id, "✅ All trading halted. No new trades will be placed.")
    except Exception as e:
        logger.error(f"Kill command failed: {e}")
        bot.send_message(message.chat.id, f"❌ Error: {e}")


# =============================================================================
# Main
# =============================================================================

def main():
    logger.info("🧠 Agent Brain starting...")
    logger.info(f"Telegram Chat ID: {TELEGRAM_CHAT_ID}")

    try:
        user = bot.get_me()
        logger.info(f"🤖 Bot Identity Verified: @{user.username} (ID: {user.id})")
        bot.send_message(TELEGRAM_CHAT_ID, "🧠 *Agent online.* Type /help for commands.", parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Could not send startup message: {e}")

    logger.info("Starting Telegram bot polling...")
    bot.infinity_polling(timeout=30, long_polling_timeout=30)


if __name__ == "__main__":
    main()
