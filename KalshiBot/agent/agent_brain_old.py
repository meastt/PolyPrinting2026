#!/usr/bin/env python3
"""
Agent Brain (Slow Brain) - The General

Handles:
- Telegram commands (/status, /defensive, /aggressive, /analyze, /kill)
- Groq API for market sentiment analysis
- CryptoPanic news feed integration
- Atomic writes to strategy.json

This is the intelligence layer. It runs on human timescales (seconds),
not trading timescales (milliseconds).
"""

import os
import sys
import json
import time
import tempfile
import logging
from datetime import datetime
from pathlib import Path

import telebot
from groq import Groq
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# =============================================================================
# Configuration
# =============================================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
CRYPTOPANIC_API_KEY = os.getenv("CRYPTOPANIC_API_KEY")

CONFIG_PATH = Path("/app/config/strategy.json")
MOMENTUM_STATE_PATH = Path("/app/config/momentum_state.json")
LOG_PATH = Path("/app/logs/agent.log")

# Validate required env vars
if not TELEGRAM_BOT_TOKEN:
    sys.exit("ERROR: TELEGRAM_BOT_TOKEN not set")
if not TELEGRAM_CHAT_ID:
    sys.exit("ERROR: TELEGRAM_CHAT_ID not set")

# =============================================================================
# Logging Setup
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
# Telegram Bot Setup
# =============================================================================

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# =============================================================================
# Strategy Config Management (Atomic Writes)
# =============================================================================

def read_strategy() -> dict:
    """Read current strategy config from JSON file."""
    try:
        with open(CONFIG_PATH, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning(f"Could not read strategy.json: {e}")
        return get_default_strategy()


def write_strategy(config: dict) -> bool:
    """
    Atomically write strategy config.
    Writes to temp file first, then renames to prevent corrupted reads.
    """
    config["updated_at"] = int(time.time())
    config["updated_by"] = "agent"
    
    try:
        # Write to temp file in same directory (for atomic rename)
        temp_path = CONFIG_PATH.parent / "strategy.json.tmp"
        with open(temp_path, 'w') as f:
            json.dump(config, f, indent=2)
        
        # Atomic rename
        os.rename(temp_path, CONFIG_PATH)
        logger.info(f"Strategy updated: mode={config['mode']}, spread={config['spread_cents']}c")
        return True
    except Exception as e:
        logger.error(f"Failed to write strategy: {e}")
        return False


def get_default_strategy() -> dict:
    """Return default strategy config."""
    return {
        "mode": "neutral",
        "spread_cents": 5,
        "max_position": 10,
        "skew": 0.0,
        "updated_at": int(time.time()),
        "updated_by": "default"
    }

# =============================================================================
# Groq API (Sentiment Analysis)
# =============================================================================

def analyze_sentiment(news_headlines: list[str]) -> dict:
    """
    Use Groq/Llama to analyze market sentiment from news headlines.
    Returns: {"sentiment": "bullish|bearish|neutral", "confidence": 0.0-1.0, "reasoning": "..."}
    """
    if not GROQ_API_KEY:
        return {"sentiment": "neutral", "confidence": 0.5, "reasoning": "Groq API not configured"}
    
    try:
        client = Groq(api_key=GROQ_API_KEY)
        
        prompt = f"""Analyze these crypto news headlines and determine market sentiment.
        
Headlines:
{chr(10).join(f'- {h}' for h in news_headlines[:10])}

Respond in JSON format only:
{{"sentiment": "bullish" or "bearish" or "neutral", "confidence": 0.0-1.0, "reasoning": "brief explanation"}}
"""
        
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=200
        )
        
        result = json.loads(response.choices[0].message.content)
        logger.info(f"Sentiment: {result['sentiment']} ({result['confidence']:.0%})")
        return result
        
    except Exception as e:
        logger.error(f"Groq analysis failed: {e}")
        return {"sentiment": "neutral", "confidence": 0.5, "reasoning": f"Error: {e}"}

# =============================================================================
# CryptoPanic News Feed
# =============================================================================

def fetch_crypto_news() -> list[str]:
    """Fetch latest crypto news headlines from CryptoPanic API."""
    if not CRYPTOPANIC_API_KEY:
        return ["No news API configured"]
    
    try:
        url = f"https://cryptopanic.com/api/developer/v2/posts/"
        params = {
            "auth_token": CRYPTOPANIC_API_KEY,
            "currencies": "BTC,ETH",
            "filter": "important",
            "public": "true"
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        headlines = [post["title"] for post in data.get("results", [])[:10]]
        logger.info(f"Fetched {len(headlines)} news headlines")
        return headlines
        
    except Exception as e:
        logger.error(f"CryptoPanic fetch failed: {e}")
        return []

# =============================================================================
# Telegram Command Handlers
# =============================================================================

def is_authorized(message) -> bool:
    """Check if message is from authorized user."""
    authorized = str(message.chat.id) == str(TELEGRAM_CHAT_ID)
    if not authorized:
        logger.warning(f"Unauthorized command from ChatID: {message.chat.id} (Expected: {TELEGRAM_CHAT_ID})")
    return authorized


@bot.message_handler(commands=['start', 'help'])
def cmd_help(message):
    if not is_authorized(message):
        return

    help_text = """🤖 *KXBTC Momentum Bot*

*Commands:*
/status - Balance, Daily P&L, Active Position
/pause - Temporarily stop trading
/resume - Resume trading
/logs - System status & diagnostics
/kill - Emergency shutdown

*Strategy:*
• Trades 15-min BTC markets only
• Max $5 per trade (10% bankroll)
• Triggers on $30/sec momentum
• Limit orders only (0% fee)
"""
    bot.reply_to(message, help_text, parse_mode='Markdown')


@bot.message_handler(commands=['status'])
def cmd_status(message):
    if not is_authorized(message):
        return

    # Read momentum trader state
    try:
        with open(MOMENTUM_STATE_PATH, 'r') as f:
            state = json.load(f)

        balance = state.get('balance', 50.0)
        daily_pnl = state.get('daily_pnl', 0.0)
        trades = state.get('trades_today', 0)
        position = state.get('active_position')
        paused = state.get('paused', False)
        halted = state.get('halted', False)

        status_emoji = "🟢" if not paused and not halted else "🔴" if halted else "⏸️"

        status_text = f"""{status_emoji} *Momentum Bot Status*

*Balance:* ${balance:.2f}
*Daily P&L:* ${daily_pnl:+.2f} ({(daily_pnl/50)*100:+.1f}%)
*Trades Today:* {trades}

*Active Position:* {"None" if not position else position.get('ticker', 'Unknown')}
*Status:* {"HALTED (loss limit)" if halted else "PAUSED" if paused else "ACTIVE"}

*Bankroll:* ${50.00} → ${balance:.2f}
"""
        bot.reply_to(message, status_text, parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, f"❌ Could not read bot status: {e}")


@bot.message_handler(commands=['pause'])
def cmd_pause(message):
    if not is_authorized(message):
        return

    try:
        # Read current state
        with open(MOMENTUM_STATE_PATH, 'r') as f:
            state = json.load(f)

        # Set paused flag
        state['paused'] = True
        state['updated_at'] = int(time.time())

        # Write back
        with open(MOMENTUM_STATE_PATH, 'w') as f:
            json.dump(state, f, indent=2)

        bot.reply_to(message, "⏸️ *TRADING PAUSED*\n\nBot will not place new trades until resumed.", parse_mode='Markdown')
        logger.info("Trading paused via Telegram")
    except Exception as e:
        bot.reply_to(message, f"❌ Failed to pause: {e}")


@bot.message_handler(commands=['resume'])
def cmd_resume(message):
    if not is_authorized(message):
        return

    try:
        # Read current state
        with open(MOMENTUM_STATE_PATH, 'r') as f:
            state = json.load(f)

        # Clear paused flag
        state['paused'] = False
        state['updated_at'] = int(time.time())

        # Write back
        with open(MOMENTUM_STATE_PATH, 'w') as f:
            json.dump(state, f, indent=2)

        bot.reply_to(message, "▶️ *TRADING RESUMED*\n\nBot is now actively looking for trades.", parse_mode='Markdown')
        logger.info("Trading resumed via Telegram")
    except Exception as e:
        bot.reply_to(message, f"❌ Failed to resume: {e}")


# Old commands removed (defensive, aggressive, neutral, portfolio, analyze, sniper)

@bot.message_handler(commands=['kill'])
def cmd_kill(message):
    if not is_authorized(message):
        return

    bot.reply_to(message, "☠️ *EMERGENCY STOP INITIATED*\n\nHalting momentum trader...", parse_mode='Markdown')

    try:
        # Halt the momentum trader
        with open(MOMENTUM_STATE_PATH, 'r') as f:
            state = json.load(f)

        state['halted'] = True
        state['paused'] = True
        state['updated_at'] = int(time.time())

        with open(MOMENTUM_STATE_PATH, 'w') as f:
            json.dump(state, f, indent=2)

        logger.critical("KILL COMMAND RECEIVED - Trading halted")
        bot.send_message(message.chat.id, "✅ Trading halted. Bot will not place new trades.")
    except Exception as e:
        logger.error(f"Kill command failed: {e}")
        bot.send_message(message.chat.id, f"❌ Error: {e}")


@bot.message_handler(commands=['analyze'])
def cmd_analyze(message):
    if not is_authorized(message):
        return
    
    bot.reply_to(message, "🔍 Analyzing market sentiment...")
    
    headlines = fetch_crypto_news()
    if not headlines:
        bot.send_message(message.chat.id, "⚠️ Could not fetch news")
        return
    
    analysis = analyze_sentiment(headlines)
    
    # Update strategy based on sentiment
    config = read_strategy()
    if analysis["sentiment"] == "bearish" and analysis["confidence"] > 0.7:
        config["skew"] = -0.3
        config["mode"] = "cautious"
    elif analysis["sentiment"] == "bullish" and analysis["confidence"] > 0.7:
        config["skew"] = 0.3
        config["mode"] = "opportunistic"
    else:
        config["skew"] = 0.0
        config["mode"] = "neutral"
    
    write_strategy(config)
    
    result_text = f"""📰 *Sentiment Analysis*

*Verdict:* {analysis['sentiment'].upper()}
*Confidence:* {analysis['confidence']:.0%}
*Reasoning:* {analysis['reasoning']}

*Strategy Updated:* Skew={config['skew']:+.1f}, Mode={config['mode']}
"""
    bot.send_message(message.chat.id, result_text, parse_mode='Markdown')


@bot.message_handler(commands=['kill'])
def cmd_kill(message):
    if not is_authorized(message):
        return

    bot.reply_to(message, "☠️ *EMERGENCY STOP INITIATED*\n\nHalting momentum trader...", parse_mode='Markdown')

    try:
        # Halt the momentum trader
        with open(MOMENTUM_STATE_PATH, 'r') as f:
            state = json.load(f)

        state['halted'] = True
        state['paused'] = True
        state['updated_at'] = int(time.time())

        with open(MOMENTUM_STATE_PATH, 'w') as f:
            json.dump(state, f, indent=2)

        logger.critical("KILL COMMAND RECEIVED - Trading halted")
        bot.send_message(message.chat.id, "✅ Trading halted. Bot will not place new trades.")
    except Exception as e:
        logger.error(f"Kill command failed: {e}")
        bot.send_message(message.chat.id, f"❌ Error: {e}")

# =============================================================================
# Logs Command
# =============================================================================

@bot.message_handler(commands=['logs'])
def cmd_sniper(message):
    if not is_authorized(message):
        return

    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "Usage: /sniper [on|off]")
        return

    action = args[1].lower()
    config = read_strategy()

    if action == "on":
        config["mode"] = "sniper"
        bot.reply_to(message, "🎯 *SNIPER MODE ACTIVATED* 🎯\n\nHunting for 15-min momentum targets.\nVelocity Threshold: >$30/sec\n\nStay alert.", parse_mode='Markdown')
    elif action == "off":
        config["mode"] = "neutral"
        bot.reply_to(message, "🛡️ Sniper Mode DEACTIVATED. Returning to Neutral.", parse_mode='Markdown')
    else:
        bot.reply_to(message, "Usage: /sniper [on|off]")
        return

    write_strategy(config)
    logger.info(f"Command /sniper {action} executed")


def cmd_logs(message):
    if not is_authorized(message):
        return

    lines = ["🔍 *Momentum Bot Diagnostics*\n"]
    now = int(time.time())

    # Check momentum state
    try:
        with open(MOMENTUM_STATE_PATH, 'r') as f:
            state = json.load(f)
        age = now - state.get('updated_at', 0)
        lines.append(f"*Momentum Trader:* Active")
        lines.append(f"  Last update: {age}s ago")
        lines.append(f"  Status: {'HALTED' if state.get('halted') else 'PAUSED' if state.get('paused') else 'RUNNING'}")
        lines.append(f"  Balance: ${state.get('balance', 50):.2f}")
        lines.append(f"  Daily P&L: ${state.get('daily_pnl', 0):+.2f}")
        if state.get('active_position'):
            pos = state['active_position']
            lines.append(f"  Position: {pos.get('ticker', 'Unknown')}")
    except Exception as e:
        lines.append(f"*Momentum Trader:* Error ({e})")

    lines.append("")

    # Legacy check for strategist (if still running)
    sniper_path = Path("/app/config/sniper_target.json")
    if sniper_path.exists():
        try:
            with open(sniper_path, 'r') as f:
                target = json.load(f)
            age = now - target.get("updated_at", 0)
            lines.append("*Sniper Target:* Found")
            lines.append(f"  Ticker: `{target.get('ticker', 'N/A')}`")
            lines.append(f"  Side: `{target.get('side', 'N/A')}`")
            lines.append(f"  Status: `{target.get('status', 'MISSING')}`")
            lines.append(f"  Size: `{target.get('size', 'MISSING')}`")
            lines.append(f"  Target Price: `{target.get('target_price', 'MISSING')}`")
            lines.append(f"  Age: {age}s ago")
            if target.get('status') != 'PENDING':
                lines.append("  ⚠️ Status not PENDING - won't trigger!")
            if 'size' not in target or 'target_price' not in target:
                lines.append("  ⚠️ Missing required fields!")
        except Exception as e:
            lines.append(f"*Sniper Target:* Error reading ({e})")
    else:
        lines.append("*Sniper Target:* Not found")
        lines.append("  ⚠️ Strategist may not be running")

    lines.append("")

    # Check trading_signals.json
    signals_path = Path("/app/config/trading_signals.json")
    if signals_path.exists():
        try:
            with open(signals_path, 'r') as f:
                data = json.load(f)
            signals = data.get("signals", [])
            pending = [s for s in signals if s.get("status") == "PENDING"]
            age = now - data.get("updated_at", 0)
            lines.append(f"*Trading Signals:* {len(signals)} total, {len(pending)} pending")
            lines.append(f"  Last update: {age}s ago")
            if pending:
                latest = pending[-1]
                lines.append(f"  Latest: `{latest.get('ticker')}` {latest.get('side')}")
        except Exception as e:
            lines.append(f"*Trading Signals:* Error ({e})")
    else:
        lines.append("*Trading Signals:* Not found")

    lines.append("")

    # Check strategy.json age
    config = read_strategy()
    strat_age = now - config.get("updated_at", 0)
    lines.append(f"*Strategy:* mode=`{config.get('mode')}`, updated {strat_age}s ago")

    # Service health hints
    lines.append("")
    lines.append("*Service Hints:*")

    # Use trading_signals.json age for strategist health (it's touched every cycle)
    signals_age = 999999
    if signals_path.exists():
        try:
            with open(signals_path, 'r') as f:
                data = json.load(f)
                signals_age = now - data.get("updated_at", 0)
        except Exception:
            pass

    if signals_age > 900:  # 15 min (3x the 5-min cycle)
        lines.append("  - Strategist: likely DOWN or stalled")
    elif signals_age > 600:  # 10 min
        lines.append("  - Strategist: possibly stale")
    else:
        lines.append(f"  - Strategist: OK (last seen {signals_age}s ago)")

    lines.append("  - Agent: OK (you're reading this)")

    bot.reply_to(message, "\n".join(lines), parse_mode='Markdown')


def main():
    logger.info("🧠 Agent Brain starting...")
    logger.info(f"Telegram Chat ID: {TELEGRAM_CHAT_ID}")
    logger.info(f"Groq API: {'configured' if GROQ_API_KEY else 'NOT SET'}")
    logger.info(f"CryptoPanic: {'configured' if CRYPTOPANIC_API_KEY else 'NOT SET'}")
    
    # Startup message
    try:
        user = bot.get_me()
        logger.info(f"🤖 Bot Identity Verified: @{user.username} (ID: {user.id})")
        bot.send_message(TELEGRAM_CHAT_ID, "🧠 *Agent online.* Waiting for commands...", parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Could not send startup message: {e}")
    
    logger.info("Starting Telegram bot polling...")
    bot.infinity_polling(timeout=30, long_polling_timeout=30)


if __name__ == "__main__":
    main()
