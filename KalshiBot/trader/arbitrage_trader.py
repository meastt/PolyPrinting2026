#!/usr/bin/env python3
"""
Arbitrage Trader - Risk-Free Money Printer

Finds and executes arbitrage opportunities on Kalshi:
1. Strike Monotonicity: Lower strike YES should cost more than higher strike YES
2. Spread Arbitrage: YES + NO < $0.96 (guaranteed profit after fees)

Win Rate Target: ~100%
"""

import os
import sys
import json
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

# =============================================================================
# Configuration
# =============================================================================

KALSHI_API_KEY_ID = os.getenv("KALSHI_API_KEY_ID")
KALSHI_PRIVATE_KEY_PATH = os.getenv("KALSHI_PRIVATE_KEY_PATH")
KALSHI_USE_DEMO = os.getenv("KALSHI_USE_DEMO", "false").lower() == "true"
KALSHI_BASE_URL = "https://demo-api.elections.kalshi.com" if KALSHI_USE_DEMO else "https://api.elections.kalshi.com"

STATE_PATH = Path("/app/config/arbitrage_state.json")
LOG_PATH = Path("/app/logs/arbitrage.log")

# Trading parameters (Split $50 account: $25 momentum, $25 arbitrage)
STARTING_BALANCE = 25.00
MAX_POSITION_SIZE = 5   # contracts per leg (reduced for smaller bankroll)
MIN_PROFIT_PER_TRADE = 0.03  # $0.03 minimum profit after fees
SCAN_INTERVAL = 10  # seconds between scans

# Fee structure (Kalshi)
MAKER_FEE = 0.0  # Limit orders = 0% fee
TAKER_FEE = 0.07  # Market orders = 7% fee

# =============================================================================
# Logging
# =============================================================================

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format='%(asctime)s [ARB] %(levelname)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_PATH) if LOG_PATH.parent.exists() else logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# =============================================================================
# Kalshi API
# =============================================================================

def sign_request(method: str, path: str, timestamp: int) -> str:
    """Sign Kalshi API request using RSA-PSS."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    import base64

    with open(KALSHI_PRIVATE_KEY_PATH, 'rb') as f:
        private_key = serialization.load_pem_private_key(f.read(), password=None)

    msg = f"{timestamp}{method}{path}"
    signature = private_key.sign(
        msg.encode(),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH
        ),
        hashes.SHA256()
    )
    return base64.b64encode(signature).decode()


def kalshi_request(method: str, path: str, data: dict = None) -> dict:
    """Make authenticated request to Kalshi API."""
    url = f"{KALSHI_BASE_URL}{path}"
    timestamp = int(time.time() * 1000)

    # Strip query params from path for signature
    path_for_signature = path.split('?')[0]

    headers = {
        "KALSHI-ACCESS-KEY": KALSHI_API_KEY_ID,
        "KALSHI-ACCESS-SIGNATURE": sign_request(method, path_for_signature, timestamp),
        "KALSHI-ACCESS-TIMESTAMP": str(timestamp),
        "Content-Type": "application/json"
    }

    try:
        if method == "GET":
            response = requests.get(url, headers=headers, timeout=15)
        elif method == "POST":
            response = requests.post(url, headers=headers, json=data, timeout=15)
        else:
            raise ValueError(f"Unsupported method: {method}")

        response.raise_for_status()
        return response.json()

    except requests.exceptions.HTTPError as e:
        logger.error(f"Kalshi API error {e.response.status_code}: {e.response.text}")
        return {}
    except Exception as e:
        logger.error(f"Kalshi request failed: {e}")
        return {}


# =============================================================================
# Market Data
# =============================================================================

def fetch_crypto_markets() -> list[dict]:
    """Fetch crypto prediction markets (BTC/ETH)."""
    markets = []
    series_list = ["KXBTC", "KXBTCD", "INXBTC", "INXD", "KXETH"]

    for series in series_list:
        try:
            result = kalshi_request("GET", f"/trade-api/v2/markets?series_ticker={series}&status=open")
            batch = result.get("markets", [])

            for m in batch:
                markets.append({
                    "ticker": m.get("ticker"),
                    "title": m.get("title", ""),
                    "subtitle": m.get("subtitle", ""),
                    "close_time": m.get("close_time"),
                    "expiration_time": m.get("expiration_time"),
                    "strike": parse_strike(m),
                })

            time.sleep(0.2)  # Rate limit protection

        except Exception as e:
            logger.warning(f"Failed to fetch series {series}: {e}")
            continue

    logger.info(f"Fetched {len(markets)} crypto markets")
    return markets


def fetch_orderbook(ticker: str) -> dict:
    """Fetch orderbook for a specific market."""
    result = kalshi_request("GET", f"/trade-api/v2/markets/{ticker}/orderbook")
    orderbook = result.get("orderbook", {})

    yes_bids = orderbook.get("yes", [])
    no_bids = orderbook.get("no", [])

    # Best bid is last element, best ask is 100 - opposite_best_bid
    best_yes_bid = yes_bids[-1][0] / 100 if yes_bids else 0
    best_yes_ask = (100 - no_bids[-1][0]) / 100 if no_bids else 1.0

    best_no_bid = no_bids[-1][0] / 100 if no_bids else 0
    best_no_ask = (100 - yes_bids[-1][0]) / 100 if yes_bids else 1.0

    return {
        "yes_bid": best_yes_bid,
        "yes_ask": best_yes_ask,
        "no_bid": best_no_bid,
        "no_ask": best_no_ask,
    }


def parse_strike(market: dict) -> Optional[float]:
    """Parse strike price from market title."""
    import re

    title = market.get("title", "") + " " + market.get("subtitle", "")

    patterns = [
        r'\$([0-9,]+)',
        r'\$([0-9.]+)[Kk]',
        r'([0-9,]+)\s*(?:USD|dollars)',
    ]

    for pattern in patterns:
        match = re.search(pattern, title)
        if match:
            value = match.group(1).replace(",", "")
            if "K" in title.upper() and "." in value:
                return float(value) * 1000
            return float(value)

    return None


# =============================================================================
# Arbitrage Detection
# =============================================================================

def find_strike_arbitrage(markets_with_prices: list[dict]) -> list[dict]:
    """
    Find strike monotonicity violations.

    Logic: If Strike A < Strike B, then P(>A) >= P(>B)
    Violation: Price(>A) < Price(>B)
    Trade: Buy A at ask, Sell B at bid
    """
    from collections import defaultdict

    # Group by series (same underlying, same expiry)
    series_groups = defaultdict(list)

    for m in markets_with_prices:
        if m.get('strike') is None:
            continue

        # Group by first 2 parts of ticker (e.g., KXBTC-25FEB15)
        parts = m['ticker'].split('-')
        if len(parts) >= 2:
            group_id = "-".join(parts[:2])
            series_groups[group_id].append(m)

    opportunities = []

    for group, items in series_groups.items():
        items.sort(key=lambda x: x['strike'])

        for i in range(len(items) - 1):
            low_strike = items[i]
            high_strike = items[i + 1]

            ask_low = low_strike.get('yes_ask', 0)
            bid_high = high_strike.get('yes_bid', 0)

            if ask_low == 0 or bid_high == 0:
                continue

            # Arbitrage condition: Buy low strike (high prob) cheaper than sell high strike (low prob)
            if ask_low < bid_high:
                profit = bid_high - ask_low

                # Fees: Both sides use limit orders (0% fee if we place limits)
                # Conservative: assume one side hits taker
                profit_after_fees = profit - (TAKER_FEE * ask_low)

                if profit_after_fees >= MIN_PROFIT_PER_TRADE:
                    opportunities.append({
                        "type": "STRIKE_ARB",
                        "ticker_buy": low_strike['ticker'],
                        "ticker_sell": high_strike['ticker'],
                        "strike_buy": low_strike['strike'],
                        "strike_sell": high_strike['strike'],
                        "buy_price": ask_low,
                        "sell_price": bid_high,
                        "gross_profit": profit,
                        "net_profit": profit_after_fees,
                    })

    return opportunities


def find_spread_arbitrage(markets_with_prices: list[dict]) -> list[dict]:
    """
    Find spread arbitrage opportunities.

    Logic: YES + NO must equal $1.00
    Violation: YES_ask + NO_ask < $0.96
    Trade: Buy both YES and NO
    """
    opportunities = []

    for m in markets_with_prices:
        yes_ask = m.get('yes_ask', 0)
        no_ask = m.get('no_ask', 0)

        if yes_ask == 0 or no_ask == 0:
            continue

        total_cost = yes_ask + no_ask

        # Fee threshold: Conservative 4 cents buffer for fees
        if total_cost < 0.96:
            profit = 1.0 - total_cost

            # Fees: Both sides limit orders ideally (0%), but assume taker on one side
            fees = TAKER_FEE * max(yes_ask, no_ask)
            profit_after_fees = profit - fees

            if profit_after_fees >= MIN_PROFIT_PER_TRADE:
                opportunities.append({
                    "type": "SPREAD_ARB",
                    "ticker": m['ticker'],
                    "yes_price": yes_ask,
                    "no_price": no_ask,
                    "total_cost": total_cost,
                    "gross_profit": profit,
                    "net_profit": profit_after_fees,
                })

    return opportunities


# =============================================================================
# Trade Execution
# =============================================================================

def place_limit_order(ticker: str, side: str, quantity: int, price: float) -> dict:
    """
    Place a limit order on Kalshi.

    Args:
        ticker: Market ticker
        side: "yes" or "no"
        quantity: Number of contracts
        price: Limit price in dollars (0.01 to 0.99)
    """
    data = {
        "ticker": ticker,
        "action": "buy",  # We're always buying (even when "selling" we buy the opposite side)
        "side": side,
        "type": "limit",
        "yes_price": int(price * 100),  # Convert to cents
        "count": quantity,
    }

    result = kalshi_request("POST", "/trade-api/v2/portfolio/orders", data)

    if result.get("order"):
        logger.info(f"Order placed: {side.upper()} {quantity}x {ticker} @ ${price:.2f}")
        return result["order"]
    else:
        logger.error(f"Order failed: {result}")
        return {}


def execute_strike_arbitrage(opp: dict, quantity: int) -> bool:
    """Execute a strike arbitrage trade."""
    logger.info(f"⚡ STRIKE ARB: Buy {opp['ticker_buy']} @ ${opp['buy_price']:.2f}, Sell {opp['ticker_sell']} @ ${opp['sell_price']:.2f} | Profit: ${opp['net_profit']:.2f}")

    # Leg 1: Buy low strike (YES)
    order1 = place_limit_order(
        ticker=opp['ticker_buy'],
        side="yes",
        quantity=quantity,
        price=opp['buy_price']
    )

    if not order1:
        return False

    # Leg 2: Sell high strike (buy NO, which is equivalent to selling YES)
    order2 = place_limit_order(
        ticker=opp['ticker_sell'],
        side="no",
        quantity=quantity,
        price=1.0 - opp['sell_price']  # NO price is complement
    )

    if not order2:
        logger.warning("Second leg failed - HEDGE IMMEDIATELY")
        return False

    return True


def execute_spread_arbitrage(opp: dict, quantity: int) -> bool:
    """Execute a spread arbitrage trade."""
    logger.info(f"⚡ SPREAD ARB: {opp['ticker']} | Buy YES @ ${opp['yes_price']:.2f} + NO @ ${opp['no_price']:.2f} | Profit: ${opp['net_profit']:.2f}")

    # Buy both YES and NO
    order1 = place_limit_order(
        ticker=opp['ticker'],
        side="yes",
        quantity=quantity,
        price=opp['yes_price']
    )

    if not order1:
        return False

    order2 = place_limit_order(
        ticker=opp['ticker'],
        side="no",
        quantity=quantity,
        price=opp['no_price']
    )

    if not order2:
        logger.warning("Second leg failed - HEDGE IMMEDIATELY")
        return False

    return True


# =============================================================================
# State Management
# =============================================================================

def load_state() -> dict:
    """Load trading state from disk."""
    if STATE_PATH.exists():
        with open(STATE_PATH, 'r') as f:
            return json.load(f)

    return {
        "balance": STARTING_BALANCE,
        "trades_today": 0,
        "daily_pnl": 0.0,
        "total_arb_profit": 0.0,
        "last_reset": datetime.now(timezone.utc).date().isoformat(),
        "paused": False,
        "halted": False,
    }


def save_state(state: dict):
    """Save trading state to disk."""
    state['updated_at'] = int(time.time())

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, 'w') as f:
        json.dump(state, f, indent=2)


# =============================================================================
# Main Loop
# =============================================================================

def main():
    logger.info("🎰 Arbitrage Trader starting...")
    logger.info(f"Target: {MIN_PROFIT_PER_TRADE * 100:.0f}¢ minimum profit per trade")
    logger.info(f"Max position: {MAX_POSITION_SIZE} contracts per leg")

    if not KALSHI_API_KEY_ID or not KALSHI_PRIVATE_KEY_PATH:
        sys.exit("ERROR: Kalshi credentials not configured")

    state = load_state()
    logger.info(f"Balance: ${state['balance']:.2f} | Daily P&L: ${state['daily_pnl']:+.2f}")

    cycle_count = 0

    while True:
        try:
            cycle_count += 1

            # Check if paused/halted
            state = load_state()
            if state.get('halted'):
                logger.warning("⏸️ Trading HALTED by kill switch")
                time.sleep(30)
                continue

            if state.get('paused'):
                logger.info("⏸️ Trading paused")
                time.sleep(30)
                continue

            # Reset daily stats at midnight UTC
            today = datetime.now(timezone.utc).date().isoformat()
            if state.get('last_reset') != today:
                logger.info("🌅 New day - resetting daily stats")
                state['trades_today'] = 0
                state['daily_pnl'] = 0.0
                state['last_reset'] = today
                save_state(state)

            logger.info(f"🔍 Scan #{cycle_count} - Looking for arbitrage...")

            # Fetch crypto markets specifically
            crypto_markets = fetch_crypto_markets()

            if not crypto_markets:
                logger.warning("No crypto markets found")
                time.sleep(SCAN_INTERVAL)
                continue

            logger.info(f"Fetching orderbooks for {len(crypto_markets)} crypto markets...")

            # Fetch orderbooks (this is the expensive part)
            markets_with_prices = []
            for i, market in enumerate(crypto_markets):
                try:
                    prices = fetch_orderbook(market['ticker'])
                    markets_with_prices.append({**market, **prices})

                    # Rate limit protection
                    if i % 10 == 0 and i > 0:
                        time.sleep(0.5)

                except Exception as e:
                    logger.error(f"Failed to fetch orderbook for {market['ticker']}: {e}")
                    continue

            logger.info(f"Got pricing for {len(markets_with_prices)} markets")

            # Find arbitrage opportunities
            strike_opps = find_strike_arbitrage(markets_with_prices)
            spread_opps = find_spread_arbitrage(markets_with_prices)

            total_opps = len(strike_opps) + len(spread_opps)

            if total_opps == 0:
                logger.info("✅ No arbitrage found (market is efficient)")
            else:
                logger.info(f"🎯 Found {total_opps} opportunities ({len(strike_opps)} strike, {len(spread_opps)} spread)")

                # Execute best opportunities
                all_opps = strike_opps + spread_opps
                all_opps.sort(key=lambda x: x['net_profit'], reverse=True)

                for opp in all_opps[:3]:  # Top 3 opportunities
                    quantity = min(MAX_POSITION_SIZE, int(state['balance'] / 2))

                    if quantity == 0:
                        logger.warning("Insufficient balance for trades")
                        break

                    success = False
                    if opp['type'] == 'STRIKE_ARB':
                        success = execute_strike_arbitrage(opp, quantity)
                    elif opp['type'] == 'SPREAD_ARB':
                        success = execute_spread_arbitrage(opp, quantity)

                    if success:
                        # Update state (optimistic - assume profit)
                        estimated_profit = opp['net_profit'] * quantity
                        state['balance'] += estimated_profit
                        state['daily_pnl'] += estimated_profit
                        state['total_arb_profit'] += estimated_profit
                        state['trades_today'] += 1
                        save_state(state)

                        logger.info(f"💰 Trade executed | Estimated profit: ${estimated_profit:.2f} | New balance: ${state['balance']:.2f}")

                        # Wait before next trade
                        time.sleep(5)

            # Sleep before next scan
            time.sleep(SCAN_INTERVAL)

        except KeyboardInterrupt:
            logger.info("Shutting down...")
            break
        except Exception as e:
            logger.error(f"Error in main loop: {e}", exc_info=True)
            time.sleep(30)


if __name__ == "__main__":
    main()
