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
    
    help_text = """🤖 *Kalshi Trading Bot Commands*

*Status & Info:*
/status - Current mode, spread, position settings
/portfolio - View positions and realized P&L
/logs - Debug info: sniper target, services status

*Trading Modes:*
/neutral - Default mode (5c spread, 10 size)
/defensive - Wide spreads, min size
/aggressive - Tight spreads, max size
/sniper [on|off] - Latency arb mode (momentum triggers)

*Analysis:*
/analyze - Run AI sentiment analysis on news

*Emergency:*
/kill - STOP all trading and shutdown
"""
    bot.reply_to(message, help_text, parse_mode='Markdown')


@bot.message_handler(commands=['status'])
def cmd_status(message):
    if not is_authorized(message):
        return
    
    config = read_strategy()
    
    status_text = f"""📊 *Bot Status*

*Mode:* `{config['mode']}`
*Spread:* {config['spread_cents']} cents
*Max Position:* {config['max_position']} contracts
*Skew:* {config['skew']:+.2f}
*Last Update:* {datetime.fromtimestamp(config['updated_at']).strftime('%H:%M:%S')}
*Updated By:* {config['updated_by']}
"""
    bot.reply_to(message, status_text, parse_mode='Markdown')


@bot.message_handler(commands=['defensive'])
def cmd_defensive(message):
    if not is_authorized(message):
        return
    
    config = read_strategy()
    config["mode"] = "defensive"
    config["spread_cents"] = 10  # Wide spread
    config["max_position"] = 1   # Minimum size
    config["skew"] = 0.0
    
    if write_strategy(config):
        bot.reply_to(message, "🛡️ *DEFENSIVE MODE ACTIVATED*\n\nSpread: 10c | Size: 1", parse_mode='Markdown')
    else:
        bot.reply_to(message, "❌ Failed to update strategy")


@bot.message_handler(commands=['aggressive'])
def cmd_aggressive(message):
    if not is_authorized(message):
        return
    
    config = read_strategy()
    config["mode"] = "aggressive"
    config["spread_cents"] = 2   # Tight spread
    config["max_position"] = 20  # Max size
    
    if write_strategy(config):
        bot.reply_to(message, "🚀 *AGGRESSIVE MODE ACTIVATED*\n\nSpread: 2c | Size: 20", parse_mode='Markdown')
    else:
        bot.reply_to(message, "❌ Failed to update strategy")


@bot.message_handler(commands=['neutral'])
def cmd_neutral(message):
    if not is_authorized(message):
        return
    
    config = read_strategy()
    config["mode"] = "neutral"
    config["spread_cents"] = 5   # Default spread
    config["max_position"] = 10  # Default size
    config["skew"] = 0.0
    
    if write_strategy(config):
        bot.reply_to(message, "⚖️ *NEUTRAL MODE*\n\nSpread: 5c | Size: 10 | Skew: 0", parse_mode='Markdown')
    else:
        bot.reply_to(message, "❌ Failed to update strategy")


def read_portfolio_state() -> dict:
    """Read portfolio state from shared JSON file."""
    try:
        path = Path("/app/config/portfolio_state.json")
        if path.exists():
            with open(path, 'r') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Could not read portfolio state: {e}")
    return {}


@bot.message_handler(commands=['portfolio', 'pnl'])
def cmd_portfolio(message):
    if not is_authorized(message):
        return
        
    state = read_portfolio_state()
    cash = state.get("cash_balance", 0.0) # Note: PositionManager doesn't update cash yet, Trader does? 
    # Actually Trader logged balance but didn't write to this file? 
    # Wait, simple PositionManager I wrote doesn't fetch API balance automatically.
    # It relies on `update_balance` being called. 
    # MarketScanner/Trader should update it. 
    # For MVP, let's just show positions and realized PnL.
    
    positions = state.get("positions", {})
    realized_pnl = state.get("realized_pnl", 0.0)
    
    pos_text = ""
    if not positions:
        pos_text = "No active positions."
    else:
        for ticker, pos in positions.items():
            # pos might be complex structure later, for now just keys
            # My PositionManager implementation was simple.
            pos_text += f"- {ticker}: {pos}\n"
            
    summary = f"""💰 *Portfolio Summary*

*Realized P&L:* ${realized_pnl:.2f}
*Active Positions:* {len(positions)}

{pos_text}
"""
    bot.reply_to(message, summary, parse_mode='Markdown')


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
    
    bot.reply_to(message, "☠️ *EMERGENCY STOP INITIATED*\n\nShutting down...", parse_mode='Markdown')
    
    # Set strategy to halt trading
    config = read_strategy()
    config["mode"] = "HALT"
    config["max_position"] = 0
    write_strategy(config)
    
    logger.critical("KILL COMMAND RECEIVED - Shutting down")
    
    # Exit the container (Docker will NOT restart due to exit code 0)
    sys.exit(0)

# =============================================================================
# Main Loop
# =============================================================================

@bot.message_handler(commands=['sniper'])
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


@bot.message_handler(commands=['logs'])
def cmd_logs(message):
    if not is_authorized(message):
        return

    lines = ["🔍 *Debug Status*\n"]
    now = int(time.time())

    # Check sniper_target.json
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
    if not sniper_path.exists():
        lines.append("  - Strategist: likely DOWN")
    elif age > 600:  # 10 min
        lines.append("  - Strategist: possibly stale (>10min)")
    else:
        lines.append("  - Strategist: likely OK")

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
