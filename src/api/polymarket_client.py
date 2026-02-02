"""
Polymarket CLOB API Client Wrapper

Provides a clean interface to the Polymarket Central Limit Order Book (CLOB)
using the official py-clob-client library.

Reference: https://github.com/Polymarket/py-clob-client

This module handles:
- Authentication with API keys
- Market data retrieval
- Order placement and management
- Position tracking
- Balance queries
"""

import os
import time
from typing import Optional, Dict, List, Any
from dataclasses import dataclass
from enum import Enum

# Polymarket official client
# Install: pip install py-clob-client
try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import (
        OrderArgs,
        OrderType,
        MarketOrderArgs,
        ApiCreds,
    )
    from py_clob_client.order_builder.constants import BUY, SELL
    CLOB_AVAILABLE = True
except ImportError:
    CLOB_AVAILABLE = False
    print("WARNING: py-clob-client not installed. Run: pip install py-clob-client")

from src.utils.logger import get_logger

logger = get_logger(__name__)


class OrderSide(Enum):
    """Order side enumeration."""
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(Enum):
    """Order status enumeration."""
    PENDING = "PENDING"
    LIVE = "LIVE"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


@dataclass
class Market:
    """Represents a Polymarket market."""
    condition_id: str
    question: str
    slug: str
    outcomes: List[str]  # ["Yes", "No"]
    outcome_prices: Dict[str, float]  # {"Yes": 0.55, "No": 0.45}
    tokens: Dict[str, str]  # {"Yes": token_id, "No": token_id}
    liquidity: float
    volume_24h: float
    end_date: Optional[str]
    category: str
    active: bool


@dataclass
class Order:
    """Represents an order."""
    order_id: str
    market_id: str
    token_id: str
    side: OrderSide
    price: float
    size: float
    filled_size: float
    status: OrderStatus
    created_at: str
    order_type: str  # "limit" or "market"


@dataclass
class Position:
    """Represents a position."""
    market_id: str
    token_id: str
    outcome: str
    size: float
    avg_price: float
    current_price: float
    unrealized_pnl: float


class PolymarketClient:
    """
    Wrapper for Polymarket CLOB API interactions.

    Inspired by successful trading bots in the Polymarket community
    (Moltbot, Clawdbot) that turn small stakes into significant profits
    through systematic arbitrage and market making.

    Usage:
        client = PolymarketClient()
        markets = client.get_markets(category="Crypto")
        client.place_limit_order(market_id, "YES", 0.50, 10.0)
    """

    # Polymarket API endpoints
    MAINNET_HOST = "https://clob.polymarket.com"
    TESTNET_HOST = "https://clob.polymarket.com"  # Update when testnet available

    def __init__(
        self,
        host: Optional[str] = None,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        api_passphrase: Optional[str] = None,
        private_key: Optional[str] = None,
        funder: Optional[str] = None,
        simulation_mode: bool = True,
    ):
        """
        Initialize the Polymarket client.

        Args:
            host: API host URL (defaults to mainnet)
            api_key: Polymarket API key (or POLYMARKET_API_KEY env var)
            api_secret: Polymarket API secret (or POLYMARKET_API_SECRET env var)
            api_passphrase: Polymarket passphrase (or POLYMARKET_API_PASSPHRASE env var)
            private_key: Ethereum private key for signing (or POLYMARKET_PRIVATE_KEY env var)
            funder: Funder wallet address (or POLYMARKET_FUNDER env var)
            simulation_mode: If True, don't execute real trades
        """
        self.host = host or self.MAINNET_HOST
        self.simulation_mode = simulation_mode

        # Load credentials from environment if not provided
        self.api_key = api_key or os.getenv("POLYMARKET_API_KEY")
        self.api_secret = api_secret or os.getenv("POLYMARKET_API_SECRET")
        self.api_passphrase = api_passphrase or os.getenv("POLYMARKET_API_PASSPHRASE")
        self.private_key = private_key or os.getenv("POLYMARKET_PRIVATE_KEY")
        self.funder = funder or os.getenv("POLYMARKET_FUNDER")

        # Validate credentials
        self._validate_credentials()

        # Initialize the official client
        self.client: Optional[ClobClient] = None
        self._initialized = False

        # Rate limiting
        self._last_request_time = 0
        self._min_request_interval = 0.1  # 100ms between requests

        # Cache
        self._market_cache: Dict[str, Market] = {}
        self._cache_ttl = 60  # seconds
        self._cache_timestamps: Dict[str, float] = {}

        logger.info(
            f"PolymarketClient initialized (simulation={simulation_mode}, host={self.host})"
        )

    def _validate_credentials(self) -> None:
        """Validate that required credentials are present."""
        if not self.simulation_mode:
            missing = []
            if not self.api_key:
                missing.append("POLYMARKET_API_KEY")
            if not self.api_secret:
                missing.append("POLYMARKET_API_SECRET")
            if not self.private_key:
                missing.append("POLYMARKET_PRIVATE_KEY")

            if missing:
                logger.warning(
                    f"Missing credentials for live trading: {', '.join(missing)}. "
                    "Set environment variables or pass to constructor."
                )

    def initialize(self) -> bool:
        """
        Initialize the CLOB client connection.

        Returns:
            True if initialization successful
        """
        if self._initialized:
            return True

        if not CLOB_AVAILABLE:
            logger.error("py-clob-client not available. Install with: pip install py-clob-client")
            return False

        try:
            # Create API credentials object
            creds = None
            if self.api_key and self.api_secret and self.api_passphrase:
                creds = ApiCreds(
                    api_key=self.api_key,
                    api_secret=self.api_secret,
                    api_passphrase=self.api_passphrase,
                )

            # Initialize client
            # Chain ID: 137 for Polygon mainnet
            self.client = ClobClient(
                host=self.host,
                chain_id=137,
                key=self.private_key,
                creds=creds,
                funder=self.funder,
            )

            # Test connection
            self.client.get_sampling_markets()

            self._initialized = True
            logger.info("Polymarket CLOB client initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize CLOB client: {e}")
            return False

    def _rate_limit(self) -> None:
        """Enforce rate limiting between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_request_interval:
            time.sleep(self._min_request_interval - elapsed)
        self._last_request_time = time.time()

    def _is_cache_valid(self, key: str) -> bool:
        """Check if cached data is still valid."""
        if key not in self._cache_timestamps:
            return False
        return (time.time() - self._cache_timestamps[key]) < self._cache_ttl

    def get_markets(
        self,
        category: Optional[str] = None,
        active_only: bool = True,
        limit: int = 100,
    ) -> List[Market]:
        """
        Fetch available markets from Polymarket.

        Args:
            category: Filter by category (e.g., "Crypto", "Politics")
            active_only: Only return active markets
            limit: Maximum number of markets to return

        Returns:
            List of Market objects
        """
        if not self._initialized and not self.initialize():
            logger.error("Cannot fetch markets: client not initialized")
            return []

        self._rate_limit()

        try:
            # Fetch markets from API
            # The sampling_markets endpoint returns actively traded markets
            response = self.client.get_sampling_markets()

            markets = []
            for market_data in response[:limit]:
                market = self._parse_market(market_data)
                if market:
                    # Apply filters
                    if active_only and not market.active:
                        continue
                    if category and market.category.lower() != category.lower():
                        continue

                    markets.append(market)
                    self._market_cache[market.condition_id] = market
                    self._cache_timestamps[market.condition_id] = time.time()

            logger.debug(f"Fetched {len(markets)} markets")
            return markets

        except Exception as e:
            logger.error(f"Failed to fetch markets: {e}")
            return []

    def get_market(self, condition_id: str) -> Optional[Market]:
        """
        Get a specific market by condition ID.

        Args:
            condition_id: The market's condition ID

        Returns:
            Market object or None if not found
        """
        # Check cache first
        if self._is_cache_valid(condition_id):
            return self._market_cache.get(condition_id)

        if not self._initialized and not self.initialize():
            return None

        self._rate_limit()

        try:
            response = self.client.get_market(condition_id)
            market = self._parse_market(response)
            if market:
                self._market_cache[condition_id] = market
                self._cache_timestamps[condition_id] = time.time()
            return market
        except Exception as e:
            logger.error(f"Failed to fetch market {condition_id}: {e}")
            return None

    def _parse_market(self, data: Dict[str, Any]) -> Optional[Market]:
        """Parse market data from API response."""
        try:
            # Extract outcome prices
            outcomes = data.get("outcomes", ["Yes", "No"])
            outcome_prices = {}
            tokens = {}

            # Parse tokens array for prices
            tokens_data = data.get("tokens", [])
            for i, token in enumerate(tokens_data):
                outcome_name = outcomes[i] if i < len(outcomes) else f"Outcome{i}"
                outcome_prices[outcome_name] = float(token.get("price", 0.5))
                tokens[outcome_name] = token.get("token_id", "")

            return Market(
                condition_id=data.get("condition_id", ""),
                question=data.get("question", ""),
                slug=data.get("slug", ""),
                outcomes=outcomes,
                outcome_prices=outcome_prices,
                tokens=tokens,
                liquidity=float(data.get("liquidity", 0)),
                volume_24h=float(data.get("volume_24h", 0)),
                end_date=data.get("end_date_iso"),
                category=data.get("category", ""),
                active=data.get("active", True),
            )
        except Exception as e:
            logger.error(f"Failed to parse market data: {e}")
            return None

    def get_orderbook(
        self,
        token_id: str,
        depth: int = 10,
    ) -> Dict[str, List[Dict[str, float]]]:
        """
        Get orderbook for a specific token.

        Args:
            token_id: The token ID to get orderbook for
            depth: Number of price levels to return

        Returns:
            Dict with 'bids' and 'asks' lists
        """
        if not self._initialized and not self.initialize():
            return {"bids": [], "asks": []}

        self._rate_limit()

        try:
            response = self.client.get_order_book(token_id)

            bids = [
                {"price": float(level["price"]), "size": float(level["size"])}
                for level in response.get("bids", [])[:depth]
            ]
            asks = [
                {"price": float(level["price"]), "size": float(level["size"])}
                for level in response.get("asks", [])[:depth]
            ]

            return {"bids": bids, "asks": asks}

        except Exception as e:
            logger.error(f"Failed to fetch orderbook for {token_id}: {e}")
            return {"bids": [], "asks": []}

    def get_midpoint_price(self, token_id: str) -> Optional[float]:
        """
        Get the midpoint price between best bid and ask.

        Args:
            token_id: The token ID

        Returns:
            Midpoint price or None
        """
        orderbook = self.get_orderbook(token_id, depth=1)

        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])

        if not bids or not asks:
            return None

        best_bid = bids[0]["price"]
        best_ask = asks[0]["price"]

        return (best_bid + best_ask) / 2

    def place_limit_order(
        self,
        token_id: str,
        side: str,  # "BUY" or "SELL"
        price: float,
        size: float,
    ) -> Optional[Order]:
        """
        Place a limit order (maker order for rebates).

        This is the preferred order type to avoid taker fees.
        Inspired by maker-based market making strategies from
        successful Polymarket traders.

        Args:
            token_id: Token to trade
            side: "BUY" or "SELL"
            price: Limit price (0 to 1)
            size: Order size in contracts

        Returns:
            Order object or None if failed
        """
        if self.simulation_mode:
            logger.info(
                f"[SIMULATION] Limit order: {side} {size:.2f} @ {price:.4f} "
                f"(token={token_id[:8]}...)"
            )
            return self._create_simulated_order(token_id, side, price, size, "limit")

        if not self._initialized and not self.initialize():
            return None

        self._rate_limit()

        try:
            # Build order using py-clob-client
            order_side = BUY if side.upper() == "BUY" else SELL

            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=order_side,
            )

            # Create and sign order
            signed_order = self.client.create_order(order_args)

            # Post order to API
            response = self.client.post_order(signed_order, OrderType.GTC)

            logger.info(
                f"Limit order placed: {side} {size:.2f} @ {price:.4f} "
                f"(order_id={response.get('orderID', 'unknown')})"
            )

            return Order(
                order_id=response.get("orderID", ""),
                market_id="",  # Not returned directly
                token_id=token_id,
                side=OrderSide.BUY if side.upper() == "BUY" else OrderSide.SELL,
                price=price,
                size=size,
                filled_size=0.0,
                status=OrderStatus.LIVE,
                created_at=str(time.time()),
                order_type="limit",
            )

        except Exception as e:
            logger.error(f"Failed to place limit order: {e}")
            return None

    def place_market_order(
        self,
        token_id: str,
        side: str,
        size: float,
    ) -> Optional[Order]:
        """
        Place a market order (taker order - incurs 3% fee).

        WARNING: Market orders incur a 3% taker fee. Prefer limit orders
        when possible to earn rebates instead.

        Args:
            token_id: Token to trade
            side: "BUY" or "SELL"
            size: Order size in contracts

        Returns:
            Order object or None if failed
        """
        if self.simulation_mode:
            logger.info(
                f"[SIMULATION] Market order: {side} {size:.2f} (token={token_id[:8]}...)"
            )
            return self._create_simulated_order(token_id, side, 0.5, size, "market")

        if not self._initialized and not self.initialize():
            return None

        self._rate_limit()

        try:
            order_side = BUY if side.upper() == "BUY" else SELL

            order_args = MarketOrderArgs(
                token_id=token_id,
                amount=size,
                side=order_side,
            )

            signed_order = self.client.create_market_order(order_args)
            response = self.client.post_order(signed_order, OrderType.FOK)

            logger.info(
                f"Market order placed: {side} {size:.2f} "
                f"(order_id={response.get('orderID', 'unknown')})"
            )

            return Order(
                order_id=response.get("orderID", ""),
                market_id="",
                token_id=token_id,
                side=OrderSide.BUY if side.upper() == "BUY" else OrderSide.SELL,
                price=0.0,  # Market order, no set price
                size=size,
                filled_size=size,  # Assume filled
                status=OrderStatus.FILLED,
                created_at=str(time.time()),
                order_type="market",
            )

        except Exception as e:
            logger.error(f"Failed to place market order: {e}")
            return None

    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an existing order.

        Args:
            order_id: The order ID to cancel

        Returns:
            True if cancelled successfully
        """
        if self.simulation_mode:
            logger.info(f"[SIMULATION] Cancel order: {order_id}")
            return True

        if not self._initialized and not self.initialize():
            return False

        self._rate_limit()

        try:
            self.client.cancel(order_id)
            logger.info(f"Order cancelled: {order_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    def cancel_all_orders(self) -> int:
        """
        Cancel all open orders.

        Returns:
            Number of orders cancelled
        """
        if self.simulation_mode:
            logger.info("[SIMULATION] Cancel all orders")
            return 0

        if not self._initialized and not self.initialize():
            return 0

        self._rate_limit()

        try:
            response = self.client.cancel_all()
            cancelled = response.get("canceled", [])
            logger.info(f"Cancelled {len(cancelled)} orders")
            return len(cancelled)
        except Exception as e:
            logger.error(f"Failed to cancel all orders: {e}")
            return 0

    def get_orders(self, open_only: bool = True) -> List[Order]:
        """
        Get current orders.

        Args:
            open_only: Only return open (unfilled) orders

        Returns:
            List of Order objects
        """
        if not self._initialized and not self.initialize():
            return []

        self._rate_limit()

        try:
            response = self.client.get_orders()

            orders = []
            for order_data in response:
                status = order_data.get("status", "LIVE")

                if open_only and status not in ["LIVE", "PENDING"]:
                    continue

                orders.append(Order(
                    order_id=order_data.get("id", ""),
                    market_id=order_data.get("market", ""),
                    token_id=order_data.get("asset_id", ""),
                    side=OrderSide.BUY if order_data.get("side") == "BUY" else OrderSide.SELL,
                    price=float(order_data.get("price", 0)),
                    size=float(order_data.get("original_size", 0)),
                    filled_size=float(order_data.get("size_matched", 0)),
                    status=OrderStatus[status] if status in OrderStatus.__members__ else OrderStatus.PENDING,
                    created_at=order_data.get("created_at", ""),
                    order_type="limit",
                ))

            return orders

        except Exception as e:
            logger.error(f"Failed to fetch orders: {e}")
            return []

    def get_positions(self) -> List[Position]:
        """
        Get current positions.

        Returns:
            List of Position objects
        """
        if not self._initialized and not self.initialize():
            return []

        self._rate_limit()

        # Note: Position fetching requires additional API calls
        # This is a simplified implementation
        try:
            # The py-clob-client may have position endpoints
            # Placeholder for position logic
            logger.debug("Fetching positions...")
            return []
        except Exception as e:
            logger.error(f"Failed to fetch positions: {e}")
            return []

    def get_balance(self) -> float:
        """
        Get USDC balance available for trading.

        Returns:
            Balance in USDC
        """
        if self.simulation_mode:
            # Return simulated balance
            return 50.0

        if not self._initialized and not self.initialize():
            return 0.0

        self._rate_limit()

        try:
            # Balance fetching depends on wallet integration
            # This may require web3 calls to check USDC balance
            logger.debug("Fetching balance...")
            return 0.0
        except Exception as e:
            logger.error(f"Failed to fetch balance: {e}")
            return 0.0

    def _create_simulated_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
        order_type: str,
    ) -> Order:
        """Create a simulated order for paper trading."""
        import uuid
        return Order(
            order_id=str(uuid.uuid4()),
            market_id="simulated",
            token_id=token_id,
            side=OrderSide.BUY if side.upper() == "BUY" else OrderSide.SELL,
            price=price,
            size=size,
            filled_size=size if order_type == "market" else 0.0,
            status=OrderStatus.FILLED if order_type == "market" else OrderStatus.LIVE,
            created_at=str(time.time()),
            order_type=order_type,
        )

    def health_check(self) -> bool:
        """
        Check if the API is reachable and authenticated.

        Returns:
            True if healthy
        """
        try:
            self._rate_limit()
            if self.client:
                self.client.get_sampling_markets()
            return True
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False
