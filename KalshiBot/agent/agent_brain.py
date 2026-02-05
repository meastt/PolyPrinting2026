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
TRADING_SIGNALS_PATH = Path("/app/config/trading_signals.json")
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
/pause - Temporarily stop trading
/resume - Resume trading
/logs - System status & diagnostics
/kill - Emergency shutdown

*Strategies Running:*
• Momentum Trader (KXBTC 15-min markets)
• Arbitrage Scanner (Risk-free opportunities)
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
