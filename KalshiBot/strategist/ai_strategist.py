#!/usr/bin/env python3
"""
AI Strategist - The Brain

Responsible for:
1. Fetching market data (Scanner + CryptoPanic)
2. Reasoning about trades using Groq/Llama
3. Generating disciplined trading signals
4. Writing signals to shared JSON for Trader to execute
"""

import os
import sys
import json
import time
import logging
from datetime import datetime, timezone
from pathlib import Path

from groq import Groq
import requests
from dotenv import load_dotenv

# Import scanner (assuming shared volume or copies)
# In this architecture, we'll import the modules we just built
sys.path.append("/app/trader") 
# Note: In production Docker, we need to ensure these files exist in PYTHONPATH
# We will copy them into the strategist container

try:
    from market_scanner import MarketScanner
    from probability import ProbabilityCalculator
    from arbitrage_scanner import ArbitrageScanner
except ImportError:
    # Fallback for local testing or if path setup is different
    sys.path.append(os.path.join(os.path.dirname(__file__), "..", "trader"))
    from market_scanner import MarketScanner
    from probability import ProbabilityCalculator
    from arbitrage_scanner import ArbitrageScanner

load_dotenv()

# =============================================================================
# Configuration
# =============================================================================

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
CRYPTOPANIC_API_KEY = os.getenv("CRYPTOPANIC_API_KEY")
# Kalshi keys for scanning
KALSHI_API_KEY_ID = os.getenv("KALSHI_API_KEY_ID")
KALSHI_PRIVATE_KEY_PATH = os.getenv("KALSHI_PRIVATE_KEY_PATH", "/app/keys/private_key.pem")

SIGNALS_PATH = Path("/app/config/trading_signals.json")
SNIPER_TARGET_PATH = Path("/app/config/sniper_target.json")
LOG_PATH = Path("/app/logs/strategist.log")

# Guardrails
MAX_DAILY_LOSS_PCT = 0.10  # 10%
MAX_POSITION_PCT = 0.20    # 20%
MIN_EDGE_CENTS = 5         # 5 cents

# =============================================================================
# Logging Setup
# =============================================================================

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format='%(asctime)s [STRATEGIST] %(levelname)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_PATH) if LOG_PATH.parent.exists() else logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# =============================================================================
# Data Aggregation
# =============================================================================

def fetch_news_context() -> list[str]:
    """Fetch latest crypto news for context."""
    if not CRYPTOPANIC_API_KEY:
        return ["No news source configured"]
        
    try:
        url = "https://cryptopanic.com/api/developer/v2/posts/"
        params = {
            "auth_token": CRYPTOPANIC_API_KEY,
            "currencies": "BTC",
            "filter": "important",
            "public": "true"
        }
        res = requests.get(url, params=params, timeout=10)
        res.raise_for_status()
        results = res.json().get("results", [])
        return [f"{p['title']} (Source: {p.get('domain', 'Unknown')})" for p in results[:5]]
    except Exception as e:
        logger.error(f"News fetch failed: {e}")
        return []

def get_current_btc_price() -> float:
    """Get real-time BTC price via Coinbase public API (fallback/check)."""
    try:
        res = requests.get("https://api.coinbase.com/v2/prices/BTC-USD/spot", timeout=5)
        data = res.json()
        return float(data["data"]["amount"])
    except Exception as e:
        logger.error(f"Coinbase price fetch failed: {e}")
        return 0.0

# =============================================================================
# AI Reasoning
# =============================================================================

def generate_ai_decision(opportunity: dict, news: list[str], btc_price: float) -> dict:
    """ask Groq/Llama to validate a trade opportunity."""
    if not GROQ_API_KEY:
        return {"decision": "SKIP", "reason": "No AI Brain"}

    client = Groq(api_key=GROQ_API_KEY)
    
    prompt = f"""
You are a disciplined, professional crypto options trader. 
Evaluate this digital option trade on Kalshi.

MARKET CONTEXT:
- BTC Spot Price: ${btc_price:,.2f}
- News Headlines:
{chr(10).join(f"- {n}" for n in news)}

TRADE OPPORTUNITY:
- Ticker: {opportunity['ticker']}
- Market Title: {opportunity['title']}
- Option Type: {opportunity['side']} (Betting YES)
- Strike Price: ${opportunity['strike']:,.2f}
- Hours to Expiry: {opportunity['hours_to_expiry']:.1f} hours
- Market Price: ${opportunity['market_price']:.2f} (Implied Prob: {opportunity['market_price']:.0%})
- Our Model Fair Value: ${opportunity['fair_price']:.2f} (Prob: {opportunity['fair_price']:.0%})
- Theoretical Edge: ${opportunity['edge']:.2f}

DISCIPLINE RULES:
1. ONLY trade if you believe the edge is real and not due to model error (e.g., immediate volatility event pending).
2. Consider the news sentiment. If news is VERY opposite to the trade direction, REJECT it.
3. If the trade is betting BTC goes UP, verify news isn't bearish.
4. If the trade is betting BTC goes DOWN, verify news isn't bullish.

DECISION REQUIRED:
Determine if we should execute this trade.
Return JSON ONLY:
{{
  "decision": "BUY" or "SKIP",
  "confidence": 0.0 to 1.0,
  "size": 1 to 5 (contracts),
  "reasoning": "brief explanation (max 1 sentence)"
}}
"""

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile", # Using latest model
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1, # Low temp for discipline
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        logger.error(f"AI reasoning failed: {e}")
        return {"decision": "SKIP", "reason": f"AI Error: {e}"}

# =============================================================================
# Signal Management
# =============================================================================

def update_signals(new_signal: dict):
    """Read existing signals, append new one, write back."""
    signals = []
    if SIGNALS_PATH.exists():
        try:
            with open(SIGNALS_PATH, 'r') as f:
                data = json.load(f)
                signals = data.get("signals", [])
        except Exception:
            pass # Start fresh if corrupt
    
    # Add timestamp and ID
    new_signal["created_at"] = int(time.time())
    new_signal["id"] = f"{new_signal['ticker']}_{new_signal['created_at']}"
    new_signal["status"] = "PENDING" # Valid statuses: PENDING, EXECUTED, CANCELED
    
    signals.append(new_signal)
    
    # Clean up old handled signals (optional, maybe keep for history)
    # For now, just keep last 50
    if len(signals) > 50:
        signals = signals[-50:]

    with open(SIGNALS_PATH, 'w') as f:
        json.dump({"updated_at": int(time.time()), "signals": signals}, f, indent=2)
    
    logger.info(f"Signal generated: {new_signal['side']} {new_signal['ticker']} (Size: {new_signal['size']})")

def identify_sniper_targets(markets: list[dict], btc_price: float):
    """
    Identify high-liquidity BTC markets expiring soon for Latency Sniping.
    Writes best candidate to sniper_target.json.
    """
    try:
        # Filter for KXBTC and valid strikes
        candidates = []
        now = datetime.now(timezone.utc)
        
        for m in markets:
            if "KXBTC" not in m.get("ticker", ""):
                continue
                
            # Must be active
            if m.get("status", "active") != "active":
                continue
                
            # Parse expiry
            expiry_str = m.get("expiration_time")
            if not expiry_str:
                continue
            
            expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
            minutes_to_expiry = (expiry - now).total_seconds() / 60
            
            # Target 15-min markets (expiry within 5-60 mins)
            # Avoid < 5 mins (too risky)
            if 5 <= minutes_to_expiry <= 60:
                # Check liquidity
                vol = m.get("volume", 0)
                if vol < 100: # Need liquidity
                    continue
                    
                # Calculate distance to spot (ATM preference)
                strike = m.get("strike")
                if not strike:
                    continue
                
                distance = abs(strike - btc_price)
                
                candidates.append({
                    "ticker": m["ticker"],
                    "strike": strike,
                    "volume": vol,
                    "expiry": expiry_str,
                    "distance": distance,
                    "yes_ask": m.get("yes_ask", 1.0),
                    "no_ask": m.get("no_ask", 1.0)
                })
        
        if not candidates:
            # Clear target if none found
            if SNIPER_TARGET_PATH.exists():
                os.remove(SNIPER_TARGET_PATH)
            return

        # Sort by distance (closest to ATM) then Volume
        # Actually ATM is best for gamma.
        candidates.sort(key=lambda x: (x["distance"], -x["volume"]))
        
        best = candidates[0]
        
        # Write to target file
        target_data = {
            "updated_at": int(time.time()),
            "ticker": best["ticker"],
            "strike": best["strike"],
            "side": "BUY_YES" if btc_price > best["strike"] else "BUY_NO", # Default bias? No, handled by velocity.
            # Actually we just need the ticker.
            "market_price": best["yes_ask"],
            "expiry": best["expiry"]
        }
        
        with open(SNIPER_TARGET_PATH, 'w') as f:
            json.dump(target_data, f)
            
        logger.info(f"🎯 Sniper Target Updated: {best['ticker']} (Strike: {best['strike']}, Vol: {best['volume']})")

    except Exception as e:
        logger.error(f"Sniper target id failed: {e}")

# =============================================================================
# Main Loop
# =============================================================================

def main():
    logger.info("🧠 AI Strategist starting...")
    
    # Initialize scanner
    try:
        scanner = MarketScanner(api_key_id=KALSHI_API_KEY_ID, private_key_path=KALSHI_PRIVATE_KEY_PATH)
        arb_scanner = ArbitrageScanner()
    except Exception as e:
        logger.error(f"Failed to init scanner: {e}")
        return

    # Main loop (run every 5-10 minutes)
    while True:
        try:
            print("DEBUG: Starting scan cycle...", flush=True)
            logger.info("Starting scan cycle...")
            
            # 0. Arbitrage Scan (Priority: HIGH)
            try:
                # Use GLOBAL scan for Arbitrage (as requested by user)
                # This fetches all active markets (paginated)
                all_markets = scanner.get_all_markets()
                
                # Check 1: Strike Monotonicity
                arbs = arb_scanner.check_strike_monotonicity(all_markets)
                logger.info(f"Strike Arb Check: Found {len(arbs)} opportunities")
                
                for arb in arbs:
                    logger.info(f"🚨 STRIKE ARB FOUND: Buy {arb['ticker_buy']} (${arb['buy_price']}) / Sell {arb['ticker_sell']} (${arb['sell_price']})")
                    
                    sig_buy = {
                        "ticker": arb['ticker_buy'],
                        "side": "BUY_YES",
                        "market_price": arb['buy_price'],
                        "edge": arb['raw_profit'],
                        "size": 5, 
                        "reasoning": f"ARBITRAGE: Strike Inversion vs {arb['ticker_sell']}"
                    }
                    update_signals(sig_buy)
                    
                    sig_sell = {
                        "ticker": arb['ticker_sell'],
                        "side": "SELL_YES",
                        "market_price": arb['sell_price'],
                        "edge": arb['raw_profit'],
                        "size": 5,
                        "reasoning": f"ARBITRAGE: Strike Inversion vs {arb['ticker_buy']}"
                    }
                    update_signals(sig_sell)

                # Check 2: Spread Arbitrage (YES + NO < 0.94)
                spread_arbs = arb_scanner.check_spread_arb(all_markets)
                logger.info(f"Spread Arb Check: Found {len(spread_arbs)} opportunities")

                for arb in spread_arbs:
                    logger.info(f"🚨 SPREAD ARB FOUND: {arb['ticker']} (Cost: ${arb['total_cost']:.2f})")
                    
                    # Buy YES
                    sig_yes = {
                        "ticker": arb['ticker'],
                        "side": "BUY_YES",
                        "market_price": arb['yes_price'],
                        "edge": arb['guaranteed_profit'],
                        "size": 5,
                        "reasoning": f"SPREAD ARB: guaranteed profit (Cost {arb['total_cost']})"
                    }
                    update_signals(sig_yes)
                    
                    # Buy NO
                    sig_no = {
                        "ticker": arb['ticker'],
                        "side": "BUY_NO",
                        "market_price": arb['no_price'],
                        "edge": arb['guaranteed_profit'],
                        "size": 5,
                        "reasoning": f"SPREAD ARB: guaranteed profit (Cost {arb['total_cost']})"
                    }
                    update_signals(sig_no)
                    
            except Exception as e:
                logger.error(f"Arbitrage scan failed: {e}")

            # 1. Get Context
            btc_price = get_current_btc_price()
            news = fetch_news_context()
            logger.info(f"BTC: ${btc_price:,.2f} | News items: {len(news)}")
            
            # 1.5 Identify Sniper Targets (New)
            identify_sniper_targets(all_markets, btc_price)
            
            
            # 2. Find Opportunities (Raw mathematical edge)
            opportunities = scanner.find_opportunities(btc_price=btc_price, min_edge=MIN_EDGE_CENTS/100)
            logger.info(f"Scanner found {len(opportunities)} raw opportunities")
            
            # 3. AI Reasoning on best opportunities
            # Only evaluate top 3 to save API tokens and avoid overtrading
            for op in opportunities[:3]:
                logger.info(f"Evaluating {op['ticker']} ({op['side']})...")
                
                decision = generate_ai_decision(op, news, btc_price)
                
                if decision.get("decision") == "BUY" and decision.get("confidence", 0) > 0.7:
                    # Construct signal
                    signal = {
                        "ticker": op["ticker"],
                        "side": op["side"], # BUY_YES or SELL_YES
                        "market_price": op["market_price"],
                        "edge": op["edge"],
                        "size": min(decision.get("size", 1), 5), # Hard cap 5
                        "reasoning": decision.get("reasoning", "")
                    }
                    update_signals(signal)
                else:
                    logger.info(f"Rejected {op['ticker']}: {decision.get('reasoning')}")
                
                time.sleep(2) # Rate limit Groq slightly
                
            logger.info("Scan cycle complete. Sleeping 5 minutes...")
            time.sleep(300) 
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Strategist loop error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
