#!/usr/bin/env python3
"""
KXBTC Momentum Trader - Simple & Profitable

Strategy:
- Focus on 15-min BTC markets only
- Trade on momentum signals (velocity > $30/sec)
- Strict bankroll management ($50 account)
- Limit orders only (0% maker fee)
- Max $5 per trade (5 contracts)
"""

import os
import sys
import json
import time
import requests
import logging
from datetime import datetime, timezone
from pathlib import Path
from collections import deque

# Add trader directory to path for imports
sys.path.insert(0, os.path.dirname(__file__))

try:
    from dotenv import load_dotenv
    load_dotenv()
except:
    pass

from market_scanner import MarketScanner

# =============================================================================
# Configuration
# =============================================================================

KALSHI_API_KEY_ID = os.getenv("KALSHI_API_KEY_ID")
KALSHI_PRIVATE_KEY_PATH = os.getenv("KALSHI_PRIVATE_KEY_PATH", "/app/keys/private_key.pem")
KALSHI_USE_DEMO = os.getenv("KALSHI_USE_DEMO", "false").lower() == "true"
KALSHI_BASE_URL = "https://demo-api.elections.kalshi.com" if KALSHI_USE_DEMO else "https://api.elections.kalshi.com"

STATE_PATH = Path("/app/config/momentum_state.json")

# Bankroll Management
STARTING_BALANCE = 50.00
MAX_RISK_PER_TRADE_PCT = 0.10  # 10% max per trade
DAILY_LOSS_LIMIT_PCT = 0.20    # 20% daily loss = halt
MAX_POSITION_SIZE = 5          # contracts

# Trading Parameters
MOMENTUM_THRESHOLD = 7.0       # $/sec BTC velocity (lowered for more trading)
MOMENTUM_DURATION = 10         # seconds sustained
MIN_EDGE_PCT = 0.15           # 15% minimum edge
MIN_TIME_TO_EXPIRY = 5        # minutes
MAX_TIME_TO_EXPIRY = 30       # minutes

LOG_PATH = Path("/app/logs/momentum_trader.log")

# =============================================================================
# Logging
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [MOMENTUM] %(levelname)s %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_PATH) if LOG_PATH.parent.exists() else logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# =============================================================================
# Kalshi API Authentication
# =============================================================================

def load_private_key():
    """Load RSA private key from PEM file."""
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.backends import default_backend

        with open(KALSHI_PRIVATE_KEY_PATH, 'rb') as f:
            private_key = serialization.load_pem_private_key(
                f.read(),
                password=None,
                backend=default_backend()
            )
        return private_key
    except Exception as e:
        logger.error(f"Failed to load private key: {e}")
        return None

def sign_request(method: str, path: str, timestamp: int) -> str:
    """Sign API request with RSA-PSS."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    import base64

    private_key = load_private_key()
    if not private_key:
        return ""

    # Kalshi signature: timestamp + method + path (no query params)
    message = f"{timestamp}{method}{path}".encode()

    signature = private_key.sign(
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH
        ),
        hashes.SHA256()
    )

    return base64.b64encode(signature).decode()

def kalshi_request(method: str, path: str, data: dict = None) -> dict:
    """Make authenticated Kalshi API request."""
    url = f"{KALSHI_BASE_URL}{path}"
    timestamp = int(time.time() * 1000)

    # Strip query params for signature
    path_for_signature = path.split('?')[0]

    headers = {
        "KALSHI-ACCESS-KEY": KALSHI_API_KEY_ID,
        "KALSHI-ACCESS-SIGNATURE": sign_request(method, path_for_signature, timestamp),
        "KALSHI-ACCESS-TIMESTAMP": str(timestamp),
        "Content-Type": "application/json"
    }

    try:
        if method == "GET":
            response = requests.get(url, headers=headers, timeout=5)
        elif method == "POST":
            response = requests.post(url, headers=headers, json=data, timeout=5)
        elif method == "DELETE":
            response = requests.delete(url, headers=headers, timeout=5)

        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Kalshi API error: {e}")
        return {}

# =============================================================================
# Orderbook Fetching
# =============================================================================

def fetch_orderbook(ticker: str) -> dict:
    """Fetch orderbook for specific market."""
    result = kalshi_request("GET", f"/trade-api/v2/markets/{ticker}/orderbook")
    orderbook = result.get("orderbook", {})

    yes_bids = orderbook.get("yes", [])
    no_bids = orderbook.get("no", [])

    # Best prices (last element in arrays)
    best_yes_bid = yes_bids[-1][0] / 100 if yes_bids else 0
    best_yes_ask = (100 - no_bids[-1][0]) / 100 if no_bids else 1.0

    best_no_bid = no_bids[-1][0] / 100 if no_bids else 0
    best_no_ask = (100 - yes_bids[-1][0]) / 100 if yes_bids else 1.0

    return {
        "yes_bid": best_yes_bid,
        "yes_ask": best_yes_ask,
        "no_bid": best_no_bid,
        "no_ask": best_no_ask,
        "yes_depth": sum(q for p, q in yes_bids[-5:]) if yes_bids else 0,
        "no_depth": sum(q for p, q in no_bids[-5:]) if no_bids else 0
    }

# =============================================================================
# BTC Price Feed (Simple)
# =============================================================================

class SimpleBTCFeed:
    """Simple BTC price tracker using Coinbase API."""

    def __init__(self):
        self.price = 0.0
        self.price_history = deque(maxlen=60)  # Last 60 seconds
        self.last_update = 0

    def update(self):
        """Fetch current BTC price."""
        try:
            response = requests.get("https://api.coinbase.com/v2/prices/BTC-USD/spot", timeout=2)
            data = response.json()
            self.price = float(data["data"]["amount"])
            self.price_history.append((time.time(), self.price))
            self.last_update = time.time()
            return self.price
        except Exception as e:
            logger.error(f"BTC price fetch failed: {e}")
            return self.price

    def get_velocity(self) -> float:
        """Calculate price velocity ($/sec) over last 10 seconds."""
        if len(self.price_history) < 2:
            return 0.0

        now = time.time()
        recent = [(t, p) for t, p in self.price_history if now - t <= 10]

        if len(recent) < 2:
            return 0.0

        time_diff = recent[-1][0] - recent[0][0]
        price_diff = recent[-1][1] - recent[0][1]

        if time_diff > 0:
            return abs(price_diff / time_diff)
        return 0.0

# =============================================================================
# Bankroll Manager
# =============================================================================

class BankrollManager:
    """Manage $50 bankroll with strict limits."""

    def __init__(self, starting_balance: float):
        self.starting_balance = starting_balance
        self.current_balance = starting_balance
        self.daily_pnl = 0.0
        self.trades_today = 0
        self.active_position = None
        self.halted = False
        self.paused = False  # User can pause via Telegram

    def can_trade(self) -> bool:
        """Check if we can place a new trade."""
        if self.halted or self.paused:
            return False

        if self.active_position:
            return False  # Only 1 position at a time

        # Check daily loss limit
        if self.daily_pnl < -(self.starting_balance * DAILY_LOSS_LIMIT_PCT):
            logger.critical(f"🛑 DAILY LOSS LIMIT HIT: ${self.daily_pnl:.2f}")
            self.halted = True
            return False

        return True

    def get_position_size(self) -> int:
        """Calculate position size based on bankroll."""
        max_risk = self.current_balance * MAX_RISK_PER_TRADE_PCT
        # With binary options, max loss = cost of contracts
        # 5 contracts @ $1 = $5 max (if price is $1)
        # But if price is 50¢, cost is only $2.50
        return min(MAX_POSITION_SIZE, 5)

    def record_trade(self, pnl: float):
        """Record trade result."""
        self.current_balance += pnl
        self.daily_pnl += pnl
        self.trades_today += 1
        self.active_position = None

        logger.info(f"💰 Trade PnL: ${pnl:.2f} | Balance: ${self.current_balance:.2f} | Daily PnL: ${self.daily_pnl:.2f}")
        self.write_state()

    def write_state(self):
        """Write current state to file for Telegram bot."""
        try:
            state = {
                "balance": self.current_balance,
                "daily_pnl": self.daily_pnl,
                "trades_today": self.trades_today,
                "active_position": self.active_position,
                "halted": self.halted,
                "paused": self.paused,
                "updated_at": int(time.time())
            }
            with open(STATE_PATH, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to write state: {e}")

    def read_control(self):
        """Read control commands from Telegram (pause/resume)."""
        try:
            if STATE_PATH.exists():
                with open(STATE_PATH, 'r') as f:
                    state = json.load(f)
                    self.paused = state.get("paused", False)
        except Exception:
            pass

# =============================================================================
# Market Finder
# =============================================================================

def find_kxbtc_markets(scanner: MarketScanner) -> list:
    """Find KXBTC markets expiring in 5-30 minutes."""
    markets = scanner.get_all_markets()
    candidates = []
    now = datetime.now(timezone.utc)

    for m in markets:
        ticker = m.get("ticker", "")
        if "KXBTC" not in ticker:
            continue

        # Check expiry
        expiry_str = m.get("expiration_time") or m.get("close_time")
        if not expiry_str:
            continue

        try:
            expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
            minutes_to_expiry = (expiry - now).total_seconds() / 60

            if MIN_TIME_TO_EXPIRY <= minutes_to_expiry <= MAX_TIME_TO_EXPIRY:
                candidates.append({
                    "ticker": ticker,
                    "strike": m.get("strike"),
                    "expiry": expiry,
                    "minutes_left": minutes_to_expiry,
                    "volume": m.get("volume", 0)
                })
        except Exception:
            continue

    # Sort by volume (prefer liquid markets)
    candidates.sort(key=lambda x: x["volume"], reverse=True)
    return candidates[:5]  # Top 5

# =============================================================================
# Edge Calculator
# =============================================================================

def calculate_edge(btc_price: float, strike: float, yes_ask: float, minutes_to_expiry: float) -> dict:
    """Calculate edge for buying YES."""
    # Simple model: probability BTC > strike in next 15 min
    # Based on distance and volatility

    distance_pct = (strike - btc_price) / btc_price

    # Rough probability model (can be improved)
    # If BTC is $100 below strike with 15 min left, ~40% chance
    # If BTC is $100 above strike, ~60% chance
    # Assume 1% hourly volatility = $730 std dev per hour

    hourly_vol = 0.01  # 1% per hour
    time_factor = (minutes_to_expiry / 60) ** 0.5
    expected_move = btc_price * hourly_vol * time_factor

    # Z-score
    z = distance_pct / (hourly_vol * time_factor) if time_factor > 0 else 0

    # Rough probability (simplified normal CDF)
    if z < -1:
        prob = 0.70
    elif z < 0:
        prob = 0.50 + (0.20 * (1 + z))
    elif z < 1:
        prob = 0.50 - (0.20 * z)
    else:
        prob = 0.30

    fair_value = prob
    edge = (fair_value - yes_ask) / yes_ask if yes_ask > 0 else 0

    return {
        "fair_value": fair_value,
        "market_price": yes_ask,
        "edge_pct": edge,
        "expected_profit": (prob * 1.0 - yes_ask) if yes_ask > 0 else 0
    }

# =============================================================================
# Main Trading Loop
# =============================================================================

def main():
    logger.info("🚀 KXBTC Momentum Trader Starting...")
    logger.info(f"Bankroll: ${STARTING_BALANCE} | Max Risk: ${STARTING_BALANCE * MAX_RISK_PER_TRADE_PCT:.2f} per trade")

    # Initialize
    try:
        logger.info("Initializing MarketScanner...")
        scanner = MarketScanner(KALSHI_API_KEY_ID, KALSHI_PRIVATE_KEY_PATH)
        logger.info("Initializing BTC Feed...")
        btc_feed = SimpleBTCFeed()
        logger.info("Initializing Bankroll Manager...")
        bankroll = BankrollManager(STARTING_BALANCE)
        logger.info("✅ All systems initialized")
    except Exception as e:
        logger.error(f"❌ Initialization failed: {e}")
        import traceback
        traceback.print_exc()
        return

    momentum_triggered = False
    momentum_start_time = 0
    loop_count = 0

    # Initial state write
    bankroll.write_state()

    while True:
        try:
            loop_count += 1

            # Check for control commands every 10 seconds
            if loop_count % 10 == 0:
                bankroll.read_control()
                bankroll.write_state()

            # 1. Update BTC price
            btc_price = btc_feed.update()
            velocity = btc_feed.get_velocity()

            # 2. Check momentum signal
            if velocity > MOMENTUM_THRESHOLD:
                if not momentum_triggered:
                    momentum_triggered = True
                    momentum_start_time = time.time()
                    logger.info(f"🔥 MOMENTUM DETECTED: ${velocity:.1f}/sec")

                # Check if momentum sustained
                if time.time() - momentum_start_time >= MOMENTUM_DURATION:
                    if bankroll.can_trade():
                        logger.info("✅ Momentum sustained - Looking for trade...")

                        # 3. Find KXBTC markets
                        markets = find_kxbtc_markets(scanner)
                        if not markets:
                            logger.warning("No KXBTC markets found")
                            momentum_triggered = False
                            time.sleep(5)
                            continue

                        # 4. Evaluate best market
                        best_market = markets[0]
                        logger.info(f"📊 Evaluating: {best_market['ticker']} (Strike: ${best_market['strike']}, {best_market['minutes_left']:.1f}min left)")

                        # 5. Fetch orderbook
                        orderbook = fetch_orderbook(best_market['ticker'])
                        if not orderbook or orderbook['yes_ask'] == 0:
                            logger.warning("No orderbook data")
                            momentum_triggered = False
                            time.sleep(5)
                            continue

                        logger.info(f"💹 Orderbook: YES bid={orderbook['yes_bid']:.2f}, ask={orderbook['yes_ask']:.2f}")

                        # 6. Calculate edge
                        edge = calculate_edge(
                            btc_price,
                            best_market['strike'],
                            orderbook['yes_ask'],
                            best_market['minutes_left']
                        )

                        logger.info(f"📈 Edge Analysis: Fair={edge['fair_value']:.2f}, Ask={edge['market_price']:.2f}, Edge={edge['edge_pct']*100:.1f}%")

                        # 7. Place trade if edge sufficient
                        if edge['edge_pct'] >= MIN_EDGE_PCT:
                            size = bankroll.get_position_size()
                            cost = orderbook['yes_ask'] * size

                            logger.info(f"🎯 PLACING TRADE: BUY {size} YES @ {orderbook['yes_ask']:.2f} (Cost: ${cost:.2f})")

                            # Place limit order
                            order_data = {
                                "ticker": best_market['ticker'],
                                "action": "buy",
                                "type": "limit",
                                "side": "yes",
                                "count": size,
                                "price": int(orderbook['yes_ask'] * 100),  # Convert to cents
                            }

                            result = kalshi_request("POST", "/trade-api/v2/portfolio/orders", order_data)

                            if result.get("order_id"):
                                logger.info(f"✅ ORDER PLACED: {result['order_id']}")
                                bankroll.active_position = {
                                    "ticker": best_market['ticker'],
                                    "size": size,
                                    "cost": cost,
                                    "order_id": result['order_id']
                                }
                            else:
                                logger.error(f"❌ ORDER FAILED: {result}")
                        else:
                            logger.info(f"⏭️  Edge too small ({edge['edge_pct']*100:.1f}% < {MIN_EDGE_PCT*100:.0f}%)")

                        momentum_triggered = False
            else:
                momentum_triggered = False

            # Status update every 30 seconds
            if int(time.time()) % 30 == 0:
                logger.info(f"💹 BTC=${btc_price:,.2f} | Velocity=${velocity:.1f}/s | Balance=${bankroll.current_balance:.2f} | Daily PnL=${bankroll.daily_pnl:.2f}")

            time.sleep(1)

        except KeyboardInterrupt:
            logger.info("Shutting down...")
            break
        except Exception as e:
            logger.error(f"Loop error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
