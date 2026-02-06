#!/usr/bin/env python3
"""
Market Maker (Fast Brain) - The Soldier

Handles:
- Coinbase WebSocket for real-time BTC price
- Kalshi API for order execution
- Toxic Flow Guard (circuit breaker)
- Hot-reload of strategy.json from Agent

This is the execution layer. Runs on a 10ms loop for minimal latency.
"""

import os
import sys
import json
import time
import threading
import logging
from datetime import datetime
from pathlib import Path
from collections import deque

import websocket
import requests
from dotenv import load_dotenv
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend

# Load environment variables
load_dotenv()

# =============================================================================
# Configuration
# =============================================================================

KALSHI_API_KEY_ID = os.getenv("KALSHI_API_KEY_ID")
KALSHI_PRIVATE_KEY_PATH = os.getenv("KALSHI_PRIVATE_KEY_PATH", "/app/keys/private_key.pem")
KALSHI_USE_DEMO = os.getenv("KALSHI_USE_DEMO", "true").lower() == "true"

KALSHI_BASE_URL = "https://demo-api.elections.kalshi.com" if KALSHI_USE_DEMO else "https://api.elections.kalshi.com"

CONFIG_PATH = "/app/config/strategy.json"
SIGNALS_PATH = "/app/config/trading_signals.json"
WEATHER_SIGNALS_PATH = "/app/config/weather_signals.json"
SNIPER_TARGET_PATH = "/app/config/sniper_target.json"
STATE_PATH = "/app/config/portfolio_state.json"
WEATHER_STATE_PATH = "/app/config/weather_state.json"
LOG_PATH = "/app/logs/trader.log"

# Import Position Manager
from position_manager import PositionManager
folio = PositionManager(STATE_PATH)

# Guardrails
MAX_DAILY_LOSS_PCT = 0.10
TOXIC_FLOW_THRESHOLD = 50.0  # $/sec change to trigger halt (Magnitude)
SNIPER_TRIGGER_THRESHOLD = 30.0 # $/sec change to trigger snipe (Magnitude)
PRICE_HISTORY_SECONDS = 5     # Lookback window for velocity calc

# Coinbase WebSocket
COINBASE_WS_URL = "wss://ws-feed.exchange.coinbase.com"

# =============================================================================
# Logging Setup
# =============================================================================

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format='%(asctime)s [TRADER] %(levelname)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_PATH) if Path(LOG_PATH).parent.exists() else logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# =============================================================================
# Global State
# =============================================================================

# Current BTC price from Coinbase
current_price = {"btc": 0.0, "timestamp": 0}

# Price history for velocity calculation (deque of (timestamp, price))
price_history = deque(maxlen=100)

# Current strategy config (hot-reloaded from JSON)
current_strategy = {
    "mode": "neutral",
    "spread_cents": 5,
    "max_position": 10,
    "skew": 0.0,
    "updated_at": 0
}

# Trading state
is_trading_halted = False
open_orders = []
last_snipe_time = 0


# =============================================================================
# Strategy Config Reader (Hot Reload)
# =============================================================================

def reload_strategy():
    """Reload strategy.json from shared volume."""
    global current_strategy
    
    try:
        with open(CONFIG_PATH, 'r') as f:
            new_config = json.load(f)
        
        # Check if config actually changed
        if new_config.get("updated_at") != current_strategy.get("updated_at"):
            old_mode = current_strategy.get("mode")
            current_strategy = new_config
            logger.info(f"Strategy reloaded: mode={new_config['mode']}, spread={new_config['spread_cents']}c")
            
            # Handle mode changes
            if new_config.get("mode") == "HALT":
                logger.warning("🛑 HALT MODE - Canceling all orders")
                cancel_all_orders()
                
    except (FileNotFoundError, json.JSONEDecoderError) as e:
        logger.warning(f"Could not reload strategy: {e}")

# =============================================================================
# Coinbase WebSocket (Price Feed)
# =============================================================================

def on_coinbase_message(ws, message):
    """Handle incoming Coinbase price updates."""
    global current_price
    
    try:
        data = json.loads(message)
        
        if data.get("type") == "ticker" and data.get("product_id") == "BTC-USD":
            price = float(data["price"])
            ts = time.time()
            
            current_price = {"btc": price, "timestamp": ts}
            price_history.append((ts, price))
            
    except Exception as e:
        logger.error(f"Coinbase message error: {e}")


def on_coinbase_error(ws, error):
    logger.error(f"Coinbase WebSocket error: {error}")


def on_coinbase_close(ws, close_status, close_msg):
    logger.warning(f"Coinbase WebSocket closed: {close_status} - {close_msg}")


def on_coinbase_open(ws):
    """Subscribe to BTC-USD ticker on connection."""
    logger.info("Coinbase WebSocket connected")
    
    subscribe_msg = {
        "type": "subscribe",
        "product_ids": ["BTC-USD"],
        "channels": ["ticker"]
    }
    ws.send(json.dumps(subscribe_msg))


def start_coinbase_feed():
    """Start Coinbase WebSocket in background thread."""
    ws = websocket.WebSocketApp(
        COINBASE_WS_URL,
        on_message=on_coinbase_message,
        on_error=on_coinbase_error,
        on_close=on_coinbase_close,
        on_open=on_coinbase_open
    )
    
    ws_thread = threading.Thread(target=ws.run_forever, daemon=True)
    ws_thread.start()
    logger.info("Coinbase price feed started")
    return ws

# =============================================================================
# Toxic Flow Guard (Circuit Breaker)
# =============================================================================

def calculate_price_velocity() -> float:
    """
    Calculate BTC price velocity in $/second.
    Returns absolute value of price change per second over lookback window.
    """
    if len(price_history) < 2:
        return 0.0
    
    now = time.time()
    
    # Get prices from last N seconds
    recent = [(ts, p) for ts, p in price_history if now - ts <= PRICE_HISTORY_SECONDS]
    
    if len(recent) < 2:
        return 0.0
    
    oldest_ts, oldest_price = recent[0]
    newest_ts, newest_price = recent[-1]
    
    time_delta = newest_ts - oldest_ts
    if time_delta == 0:
        return 0.0
    
    price_change = abs(newest_price - oldest_price)
    velocity = price_change / time_delta
    
    return velocity


def calculate_momentum() -> float:
    """
    Calculate BTC price momentum (Velocity with Direction).
    Returns price change per second (Signed).
    Positive = Up, Negative = Down.
    """
    if len(price_history) < 2:
        return 0.0
    
    now = time.time()
    recent = [(ts, p) for ts, p in price_history if now - ts <= PRICE_HISTORY_SECONDS]
    
    if len(recent) < 2:
        return 0.0
    
    oldest_ts, oldest_price = recent[0]
    newest_ts, newest_price = recent[-1]
    
    time_delta = newest_ts - oldest_ts
    if time_delta == 0:
        return 0.0
    
    velocity = (newest_price - oldest_price) / time_delta
    return velocity



def check_toxic_flow() -> bool:
    """
    Check if price velocity exceeds toxic flow threshold.
    Returns True if we should panic and cancel all orders.
    """
    velocity = calculate_price_velocity()
    
    if velocity > TOXIC_FLOW_THRESHOLD:
        logger.critical(f"🚨 TOXIC FLOW DETECTED! Velocity: ${velocity:.2f}/sec")
        return True
    
    return False

# =============================================================================
# Kalshi API Client
# =============================================================================

def load_private_key():
    """Load Kalshi RSA private key for authentication."""
    try:
        with open(KALSHI_PRIVATE_KEY_PATH, 'rb') as f:
            return serialization.load_pem_private_key(
                f.read(),
                password=None,
                backend=default_backend()
            )
    except Exception as e:
        logger.error(f"Failed to load private key: {e}")
        return None


def sign_request(method: str, path: str, timestamp: int) -> str:
    """Sign API request with RSA private key using RSA-PSS (required by Kalshi)."""
    private_key = load_private_key()
    if not private_key:
        return ""
    
    # Kalshi signature format: timestamp + method + path (no query params)
    message = f"{timestamp}{method}{path}".encode()
    
    # Use RSA-PSS padding with SHA256 (Kalshi requirement)
    signature = private_key.sign(
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH
        ),
        hashes.SHA256()
    )
    
    import base64
    return base64.b64encode(signature).decode()


def kalshi_request(method: str, path: str, data: dict = None) -> dict:
    """Make authenticated request to Kalshi API."""
    url = f"{KALSHI_BASE_URL}{path}"
    timestamp = int(time.time() * 1000)

    # Strip query params from path for signature (Kalshi requirement)
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
        else:
            return {}
        
        response.raise_for_status()
        return response.json()
        
    except Exception as e:
        logger.error(f"Kalshi API error: {e}")
        return {}


def get_balance() -> float:
    """Get current account balance."""
    result = kalshi_request("GET", "/trade-api/v2/portfolio/balance")
    return result.get("balance", 0) / 100  # Convert cents to dollars


def cancel_all_orders():
    """Cancel all open orders."""
    global open_orders
    
    logger.warning("Canceling all open orders...")
    
    # Get current open orders
    result = kalshi_request("GET", "/trade-api/v2/portfolio/orders?status=resting")
    orders = result.get("orders", [])
    
    for order in orders:
        order_id = order.get("order_id")
        if order_id:
            kalshi_request("DELETE", f"/trade-api/v2/portfolio/orders/{order_id}")
            logger.info(f"Canceled order: {order_id}")
    
    open_orders = []

# =============================================================================
# AI Signal Processing
# =============================================================================

def execute_order(signal: dict) -> bool:
    """Execute a single order signal."""
    ticker = signal["ticker"]
    side = signal["side"] # BUY_YES, SELL_YES (currently only supporting BUY_YES/NO logic for simplicity)
    count = signal["size"]
    price = signal["market_price"] # This might be stale, but we use it as limit
    
    # Map high-level side to Kalshi params
    # signal['side'] from scanner is BUY_YES or SELL_YES
    # Kalshi API create_order:
    # side: 'yes' or 'no'
    # action: 'buy' or 'sell'
    
    kalshi_side = 'yes'
    action = 'buy'
    
    if side == "BUY_YES":
        kalshi_side = 'yes'
        action = 'buy'
    elif side == "SELL_YES":
        kalshi_side = 'yes'
        action = 'sell'
    elif side == "BUY_NO": # Future proofing
        kalshi_side = 'no'
        action = 'buy'
        
    logger.info(f"🤖 AI EXECUTION: {action.upper()} {count} {ticker} {kalshi_side.upper()} @ {price}")
    
    # Place Limit Order
    # API Docs: POST /portfolio/orders
    order_data = {
        "ticker": ticker,
        "action": action,
        "type": "limit",
        "side": kalshi_side,
        "count": count,
        "price": int(price * 100), # Convert to cents.
        "expiration_ts": None,     # GTC
        "client_order_id": signal["id"]
    }
    
    try:
        # 1. Place Order
        if not KALSHI_USE_DEMO and current_strategy["mode"] == "neutral":
             # Safety: In neutral mode, maybe we execute? 
             # For now, let's assume we execute if mode is not HALT.
             pass
             
        res = kalshi_request("POST", "/trade-api/v2/portfolio/orders", order_data)
        
        if "order_id" in res:
            logger.info(f"Order placed: {res['order_id']}")
            folio.record_trade(ticker, kalshi_side, price, count, action.upper())
            return True
        else:
            logger.error(f"Order failed: {res}")
            return False
            
    except Exception as e:
        logger.error(f"Execution error: {e}")
        return False

def process_ai_signals():
    """Check for new signals from Strategist (Crypto & Weather)."""
    # Process crypto signals
    if Path(SIGNALS_PATH).exists():
        try:
            with open(SIGNALS_PATH, 'r') as f:
                data = json.load(f)

            signals = data.get("signals", [])
            modified = False

            for signal in signals:
                if signal.get("status") == "PENDING":
                    success = execute_order(signal)
                    signal["status"] = "EXECUTED" if success else "FAILED"
                    modified = True

            if modified:
                 with open(SIGNALS_PATH, 'w') as f:
                    json.dump(data, f, indent=2)

        except Exception as e:
            logger.error(f"Crypto signal processing error: {e}")

    # Process weather signals
    if Path(WEATHER_SIGNALS_PATH).exists():
        try:
            with open(WEATHER_SIGNALS_PATH, 'r') as f:
                data = json.load(f)

            signals = data.get("signals", [])
            modified = False

            for signal in signals:
                if signal.get("status") == "PENDING":
                    logger.info(f"Processing weather signal: {signal.get('ticker')}")
                    success = execute_order(signal)
                    signal["status"] = "EXECUTED" if success else "FAILED"
                    modified = True

            if modified:
                 with open(WEATHER_SIGNALS_PATH, 'w') as f:
                    json.dump(data, f, indent=2)

        except Exception as e:
            logger.error(f"Weather signal processing error: {e}")

# =============================================================================
# Sniper Mode
# =============================================================================

def check_sniper_trigger():
    """
    Checks if conditions are met to execute a sniper order.
    This is a high-speed, reactive trade.
    """
    global last_snipe_time
    
    if not Path(SNIPER_TARGET_PATH).exists():
        return
    
    # Only attempt to snipe every 5 seconds to avoid spamming
    if time.time() - last_snipe_time < 5:
        return
        
    try:
        with open(SNIPER_TARGET_PATH, 'r') as f:
            sniper_target = json.load(f)
            
        if sniper_target.get("status") != "PENDING":
            return
            
        # Check price momentum
        momentum = calculate_momentum()
        
        # Example: Snipe if strong upward momentum and target is a BUY_YES
        # Or strong downward momentum and target is a SELL_YES
        
        trigger_momentum = sniper_target.get("trigger_momentum", SNIPER_TRIGGER_THRESHOLD)
        
        if sniper_target["side"] == "BUY_YES" and momentum > trigger_momentum:
            logger.info(f"🎯 SNIPER TRIGGERED (BUY_YES): Momentum {momentum:.2f} > {trigger_momentum:.2f}")
            execute_sniper_order(sniper_target)
            last_snipe_time = time.time()
            
        elif sniper_target["side"] == "SELL_YES" and momentum < -trigger_momentum:
            logger.info(f"🎯 SNIPER TRIGGERED (SELL_YES): Momentum {momentum:.2f} < {-trigger_momentum:.2f}")
            execute_sniper_order(sniper_target)
            last_snipe_time = time.time()
            
    except Exception as e:
        logger.error(f"Sniper trigger check error: {e}")


def execute_sniper_order(sniper_target: dict):
    """Executes the sniper order and updates its status."""
    
    ticker = sniper_target["ticker"]
    side = sniper_target["side"]
    count = sniper_target["size"]
    price = sniper_target["target_price"] # Use target price as limit
    
    kalshi_side = 'yes'
    action = 'buy'
    
    if side == "BUY_YES":
        kalshi_side = 'yes'
        action = 'buy'
    elif side == "SELL_YES":
        kalshi_side = 'yes'
        action = 'sell'
    
    logger.critical(f"💥 SNIPER EXECUTION: {action.upper()} {count} {ticker} {kalshi_side.upper()} @ {price}")
    
    order_data = {
        "ticker": ticker,
        "action": action,
        "type": "limit",
        "side": kalshi_side,
        "count": count,
        "price": int(price * 100),
        "expiration_ts": None,
        "client_order_id": sniper_target["id"]
    }
    
    try:
        res = kalshi_request("POST", "/trade-api/v2/portfolio/orders", order_data)
        
        if "order_id" in res:
            logger.critical(f"SNIPER ORDER PLACED: {res['order_id']}")
            folio.record_trade(ticker, kalshi_side, price, count, action.upper())
            sniper_target["status"] = "EXECUTED"
        else:
            logger.error(f"SNIPER ORDER FAILED: {res}")
            sniper_target["status"] = "FAILED"
            
    except Exception as e:
        logger.error(f"Sniper execution error: {e}")
        sniper_target["status"] = "FAILED"
        
    # Update sniper_target.json
    try:
        with open(SNIPER_TARGET_PATH, 'w') as f:
            json.dump(sniper_target, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to update sniper_target.json: {e}")

# =============================================================================
# Main Trading Loop
# =============================================================================

def trading_loop():
    """
    Main execution loop. Runs every ~10ms.
    
    1. Reload strategy.json
    2. Check toxic flow guard
    3. Calculate fair value
    4. Manage orders
    """
    global is_trading_halted
    
    logger.info("Starting main loop...")
    loop_count = 0
    loop_start = time.time()
    
    while True:
        try:
            loop_start = time.time()
            loop_count += 1
            
            # 0. Check Sniper Trigger (Fastest Check)
            if current_strategy.get("mode") == "sniper":
                check_sniper_trigger()
            
            # 1. Check Strategy Updates
            if loop_count % 500 == 0: # Every ~5 seconds
             reload_strategy()
            
            # 2. Check for HALT mode
            if current_strategy.get("mode") == "HALT":
                if not is_trading_halted:
                    logger.warning("Trading HALTED by Agent command")
                    cancel_all_orders()
                    is_trading_halted = True
                time.sleep(1)
                continue
            else:
                is_trading_halted = False
            
            # 3. Toxic Flow Guard
            if check_toxic_flow():
                logger.critical("🚨 PANIC MODE - Canceling all orders due to toxic flow")
                cancel_all_orders()
                time.sleep(5)  # Cool down before resuming
                continue
            
            # 4. Log status periodically (every ~30 seconds)
            if loop_count % 3000 == 0:
                btc = current_price.get("btc", 0)
                velocity = calculate_price_velocity()
                logger.info(
                    f"Status: BTC=${btc:,.2f} | Velocity=${velocity:.2f}/s | "
                    f"Mode={current_strategy['mode']} | Spread={current_strategy['spread_cents']}c"
                )
            
            # 5. Process AI Signals (Strategist)
            # Check every 100 loops (~1 second) to match strategy reload rate
            if loop_count % 100 == 0:
                process_ai_signals()
            
            # Sleep to maintain ~10ms loop
            elapsed = time.time() - loop_start
            sleep_time = max(0.01 - elapsed, 0)
            time.sleep(sleep_time)
            
        except KeyboardInterrupt:
            logger.info("Shutting down trading loop...")
            cancel_all_orders()
            break
        except Exception as e:
            logger.error(f"Trading loop error: {e}")
            time.sleep(1)

# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    logger.info("⚡ Market Maker (Fast Brain) starting...")
    logger.info(f"Kalshi API: {'DEMO' if KALSHI_USE_DEMO else 'LIVE'}")
    logger.info(f"Toxic Flow Threshold: ${TOXIC_FLOW_THRESHOLD}/sec")
    
    # Validate Kalshi credentials
    if not KALSHI_API_KEY_ID:
        logger.error("KALSHI_API_KEY_ID not set!")
        sys.exit(1)
    
    if not Path(KALSHI_PRIVATE_KEY_PATH).exists():
        logger.error(f"Private key not found at {KALSHI_PRIVATE_KEY_PATH}")
        sys.exit(1)
    
    # Load initial strategy
    reload_strategy()
    
    # Start Coinbase price feed
    start_coinbase_feed()
    
    # Wait for first price
    logger.info("Waiting for Coinbase price feed...")
    timeout = 30
    while current_price["btc"] == 0 and timeout > 0:
        time.sleep(1)
        timeout -= 1
    
    if current_price["btc"] == 0:
        logger.error("Failed to receive Coinbase prices")
        sys.exit(1)
    
    logger.info(f"First BTC price received: ${current_price['btc']:,.2f}")
    
    # Check Kalshi connection
    try:
        balance = get_balance()
        logger.info(f"Kalshi balance: ${balance:.2f}")
    except Exception as e:
        logger.error(f"Kalshi connection failed: {e}")
        sys.exit(1)
    
    # Start trading loop
    logger.info("🚀 Starting trading loop...")
    trading_loop()


if __name__ == "__main__":
    main()
