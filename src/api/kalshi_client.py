"""
Kalshi Exchange API Client

CFTC-regulated prediction market API wrapper for US-based trading.
Kalshi is legal in the US as a Designated Contract Market (DCM).

API Documentation: https://docs.kalshi.com
Python SDK: https://pypi.org/project/kalshi-python/

Key features:
- Binary YES/NO contracts (same as Polymarket)
- Zero fees on resting (maker) orders
- Hourly crypto markets (ideal for spike reversion)
- RSA-PSS authentication for API access

References:
- https://help.kalshi.com/kalshi-api
- https://github.com/AndrewNolte/KalshiPythonClient
"""

import os
import time
import uuid
import base64
import hashlib
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

import requests

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.backends import default_backend
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False

from src.utils.logger import get_logger

logger = get_logger(__name__)


class OrderSide(Enum):
    """Order side for Kalshi contracts."""
    YES = "yes"
    NO = "no"


class OrderAction(Enum):
    """Order action type."""
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    """Order type."""
    LIMIT = "limit"
    MARKET = "market"


class OrderStatus(Enum):
    """Order status."""
    PENDING = "pending"
    RESTING = "resting"
    FILLED = "filled"
    CANCELED = "canceled"
    PARTIAL = "partial"


@dataclass
class KalshiMarket:
    """Represents a Kalshi prediction market."""
    ticker: str
    event_ticker: str
    title: str
    subtitle: str
    status: str  # "open", "closed", "settled"
    yes_bid: float
    yes_ask: float
    no_bid: float
    no_ask: float
    last_price: float
    volume: int
    volume_24h: int
    open_interest: int
    expiration_time: datetime
    result: Optional[str] = None  # "yes", "no", None
    category: str = ""

    @property
    def mid_price(self) -> float:
        """Get mid price for YES outcome."""
        if self.yes_bid > 0 and self.yes_ask > 0:
            return (self.yes_bid + self.yes_ask) / 2
        return self.last_price or 0.5

    @property
    def spread(self) -> float:
        """Get bid-ask spread."""
        if self.yes_bid > 0 and self.yes_ask > 0:
            return self.yes_ask - self.yes_bid
        return 0

    @property
    def is_active(self) -> bool:
        """Check if market is actively tradeable."""
        return self.status == "open"

    @property
    def time_to_expiry_seconds(self) -> float:
        """Get seconds until market expires."""
        now = datetime.now(timezone.utc)
        if self.expiration_time.tzinfo is None:
            self.expiration_time = self.expiration_time.replace(tzinfo=timezone.utc)
        return (self.expiration_time - now).total_seconds()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "ticker": self.ticker,
            "event_ticker": self.event_ticker,
            "title": self.title,
            "subtitle": self.subtitle,
            "status": self.status,
            "yes_bid": self.yes_bid,
            "yes_ask": self.yes_ask,
            "no_bid": self.no_bid,
            "no_ask": self.no_ask,
            "last_price": self.last_price,
            "volume": self.volume,
            "volume_24h": self.volume_24h,
            "open_interest": self.open_interest,
            "expiration_time": self.expiration_time.isoformat(),
            "result": self.result,
            "category": self.category,
            "mid_price": self.mid_price,
            "spread": self.spread,
        }


@dataclass
class KalshiOrder:
    """Represents a Kalshi order."""
    order_id: str
    ticker: str
    side: OrderSide
    action: OrderAction
    type: OrderType
    price: float  # In cents (1-99)
    count: int  # Number of contracts
    status: OrderStatus
    filled_count: int = 0
    remaining_count: int = 0
    created_time: Optional[datetime] = None
    client_order_id: Optional[str] = None

    @property
    def is_maker(self) -> bool:
        """Check if this is a maker (resting) order."""
        return self.status == OrderStatus.RESTING

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "order_id": self.order_id,
            "ticker": self.ticker,
            "side": self.side.value,
            "action": self.action.value,
            "type": self.type.value,
            "price": self.price,
            "count": self.count,
            "status": self.status.value,
            "filled_count": self.filled_count,
            "remaining_count": self.remaining_count,
            "client_order_id": self.client_order_id,
        }


@dataclass
class KalshiPosition:
    """Represents a position in a Kalshi market."""
    ticker: str
    market_title: str
    yes_count: int  # Positive = long YES, Negative = short YES
    no_count: int
    avg_yes_price: float
    avg_no_price: float
    market_exposure: float  # Total $ at risk
    realized_pnl: float
    unrealized_pnl: float

    @property
    def net_position(self) -> int:
        """Get net position (positive = bullish, negative = bearish)."""
        return self.yes_count - self.no_count

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "ticker": self.ticker,
            "market_title": self.market_title,
            "yes_count": self.yes_count,
            "no_count": self.no_count,
            "avg_yes_price": self.avg_yes_price,
            "avg_no_price": self.avg_no_price,
            "market_exposure": self.market_exposure,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": self.unrealized_pnl,
        }


class KalshiClient:
    """
    Kalshi Exchange API client.

    Provides methods for:
    - Market data retrieval
    - Order placement and management
    - Position tracking
    - Portfolio analytics

    Authentication uses RSA-PSS signatures for secure API access.

    Usage:
        client = KalshiClient(
            api_key_id="your-key-id",
            private_key_path="/path/to/private.pem"
        )

        # Get crypto markets
        markets = client.get_crypto_markets()

        # Place a limit order
        order = client.place_order(
            ticker="BTCUSD-26FEB02-B101500",
            side=OrderSide.YES,
            price=0.65,
            count=10
        )
    """

    # API endpoints
    BASE_URL = "https://trading-api.kalshi.com/trade-api/v2"
    DEMO_URL = "https://demo-api.kalshi.co/trade-api/v2"

    def __init__(
        self,
        api_key_id: Optional[str] = None,
        private_key_path: Optional[str] = None,
        private_key_pem: Optional[str] = None,
        email: Optional[str] = None,
        password: Optional[str] = None,
        use_demo: bool = False,
    ):
        """
        Initialize Kalshi client.

        Two authentication methods supported:
        1. RSA-PSS (recommended): api_key_id + private_key
        2. Email/Password: email + password

        Args:
            api_key_id: API key ID from Kalshi dashboard
            private_key_path: Path to RSA private key PEM file
            private_key_pem: RSA private key as PEM string
            email: Account email (alternative auth)
            password: Account password (alternative auth)
            use_demo: Use demo environment for testing
        """
        self.api_key_id = api_key_id or os.getenv("KALSHI_API_KEY_ID")
        self.private_key_path = private_key_path or os.getenv("KALSHI_PRIVATE_KEY_PATH")
        self.private_key_pem = private_key_pem or os.getenv("KALSHI_PRIVATE_KEY")
        self.email = email or os.getenv("KALSHI_EMAIL")
        self.password = password or os.getenv("KALSHI_PASSWORD")

        self.base_url = self.DEMO_URL if use_demo else self.BASE_URL
        self.use_demo = use_demo

        # Session management
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

        # Auth token (for email/password auth)
        self._auth_token: Optional[str] = None
        self._token_expiry: float = 0

        # Private key (for RSA-PSS auth)
        self._private_key = None

        # Rate limiting
        self._last_request_time: float = 0
        self._min_request_interval: float = 0.1  # 100ms between requests

        # Initialize authentication
        self._init_auth()

        logger.info(
            f"KalshiClient initialized "
            f"(env={'demo' if use_demo else 'production'}, "
            f"auth={'rsa' if self._private_key else 'token'})"
        )

    def _init_auth(self) -> None:
        """Initialize authentication method."""
        # Prefer RSA-PSS authentication
        if self.api_key_id and (self.private_key_path or self.private_key_pem):
            if not CRYPTO_AVAILABLE:
                logger.warning("cryptography package not installed, falling back to token auth")
            else:
                self._load_private_key()
                return

        # Fall back to email/password
        if self.email and self.password:
            self._login()
            return

        logger.warning("No authentication configured. API calls may fail.")

    def _load_private_key(self) -> None:
        """Load RSA private key for PSS signing."""
        try:
            if self.private_key_path and os.path.exists(self.private_key_path):
                with open(self.private_key_path, "rb") as f:
                    key_data = f.read()
            elif self.private_key_pem:
                key_data = self.private_key_pem.encode()
            else:
                logger.error("No private key available")
                return

            self._private_key = serialization.load_pem_private_key(
                key_data,
                password=None,
                backend=default_backend()
            )
            logger.info("RSA private key loaded successfully")

        except Exception as e:
            logger.error(f"Failed to load private key: {e}")
            self._private_key = None

    def _login(self) -> None:
        """Login with email/password to get auth token."""
        try:
            response = self._session.post(
                f"{self.base_url}/login",
                json={"email": self.email, "password": self.password}
            )
            response.raise_for_status()

            data = response.json()
            self._auth_token = data.get("token")
            # Token expires in 30 minutes
            self._token_expiry = time.time() + 1800

            self._session.headers["Authorization"] = f"Bearer {self._auth_token}"
            logger.info("Logged in successfully with email/password")

        except Exception as e:
            logger.error(f"Login failed: {e}")

    def _sign_request(
        self,
        method: str,
        path: str,
        timestamp: str,
    ) -> str:
        """
        Create RSA-PSS signature for request.

        Args:
            method: HTTP method (GET, POST, etc.)
            path: API path
            timestamp: ISO 8601 timestamp

        Returns:
            Base64-encoded signature
        """
        if not self._private_key:
            raise ValueError("Private key not loaded")

        # Create message to sign: timestamp + method + path
        message = f"{timestamp}{method}{path}".encode()

        # Sign with RSA-PSS
        signature = self._private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )

        return base64.b64encode(signature).decode()

    def _get_headers(self, method: str, path: str) -> Dict[str, str]:
        """Get headers for authenticated request."""
        headers = {}

        if self._private_key:
            # RSA-PSS authentication
            timestamp = datetime.now(timezone.utc).isoformat()
            signature = self._sign_request(method, path, timestamp)

            headers["KALSHI-ACCESS-KEY"] = self.api_key_id
            headers["KALSHI-ACCESS-SIGNATURE"] = signature
            headers["KALSHI-ACCESS-TIMESTAMP"] = timestamp

        elif self._auth_token:
            # Token authentication
            # Check if token needs refresh
            if time.time() > self._token_expiry - 60:
                self._login()

            headers["Authorization"] = f"Bearer {self._auth_token}"

        return headers

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict] = None,
        json_data: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Make authenticated API request.

        Args:
            method: HTTP method
            path: API path (without base URL)
            params: Query parameters
            json_data: JSON body data

        Returns:
            Response JSON data
        """
        # Rate limiting
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_request_interval:
            time.sleep(self._min_request_interval - elapsed)

        url = f"{self.base_url}{path}"
        headers = self._get_headers(method, path)

        try:
            response = self._session.request(
                method=method,
                url=url,
                params=params,
                json=json_data,
                headers=headers,
                timeout=30,
            )

            self._last_request_time = time.time()

            response.raise_for_status()
            return response.json()

        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error {response.status_code}: {response.text}")
            raise
        except Exception as e:
            logger.error(f"Request failed: {e}")
            raise

    # =========================================================================
    # Market Data Methods
    # =========================================================================

    def get_markets(
        self,
        status: str = "open",
        series_ticker: Optional[str] = None,
        event_ticker: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Tuple[List[KalshiMarket], Optional[str]]:
        """
        Get list of markets.

        Args:
            status: Market status filter ("open", "closed", "settled")
            series_ticker: Filter by series (e.g., "BTCUSD")
            event_ticker: Filter by event
            limit: Results per page (max 1000)
            cursor: Pagination cursor

        Returns:
            Tuple of (markets list, next cursor)
        """
        params = {
            "status": status,
            "limit": min(limit, 1000),
        }

        if series_ticker:
            params["series_ticker"] = series_ticker
        if event_ticker:
            params["event_ticker"] = event_ticker
        if cursor:
            params["cursor"] = cursor

        data = self._request("GET", "/markets", params=params)

        markets = []
        for m in data.get("markets", []):
            try:
                market = self._parse_market(m)
                markets.append(market)
            except Exception as e:
                logger.debug(f"Failed to parse market: {e}")

        next_cursor = data.get("cursor")
        return markets, next_cursor

    def get_market(self, ticker: str) -> Optional[KalshiMarket]:
        """
        Get single market by ticker.

        Args:
            ticker: Market ticker (e.g., "BTCUSD-26FEB02-B101500")

        Returns:
            KalshiMarket or None
        """
        try:
            data = self._request("GET", f"/markets/{ticker}")
            return self._parse_market(data.get("market", {}))
        except Exception as e:
            logger.error(f"Failed to get market {ticker}: {e}")
            return None

    def get_crypto_markets(
        self,
        asset: str = "BTC",
        status: str = "open",
    ) -> List[KalshiMarket]:
        """
        Get crypto price prediction markets.

        Args:
            asset: Crypto asset (BTC, ETH, etc.)
            status: Market status

        Returns:
            List of crypto markets
        """
        # Kalshi uses series tickers like "BTCUSD", "ETHUSD"
        series_ticker = f"{asset.upper()}USD"

        all_markets = []
        cursor = None

        while True:
            markets, cursor = self.get_markets(
                status=status,
                series_ticker=series_ticker,
                cursor=cursor,
            )
            all_markets.extend(markets)

            if not cursor:
                break

        logger.info(f"Found {len(all_markets)} {asset} markets")
        return all_markets

    def get_hourly_crypto_markets(
        self,
        asset: str = "BTC",
    ) -> List[KalshiMarket]:
        """
        Get hourly crypto markets (ideal for spike reversion).

        Kalshi runs hourly markets that reset every hour.

        Args:
            asset: Crypto asset

        Returns:
            List of hourly markets
        """
        markets = self.get_crypto_markets(asset)

        # Filter to hourly markets (expire within 1-60 minutes)
        hourly = []
        for m in markets:
            time_to_expiry = m.time_to_expiry_seconds
            if 60 < time_to_expiry < 3600:  # 1-60 minutes
                hourly.append(m)

        hourly.sort(key=lambda m: m.time_to_expiry_seconds)

        logger.info(f"Found {len(hourly)} hourly {asset} markets")
        return hourly

    def _parse_market(self, data: Dict) -> KalshiMarket:
        """Parse market data from API response."""
        # Parse expiration time
        exp_str = data.get("expiration_time", "")
        if exp_str:
            expiration = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
        else:
            expiration = datetime.now(timezone.utc)

        return KalshiMarket(
            ticker=data.get("ticker", ""),
            event_ticker=data.get("event_ticker", ""),
            title=data.get("title", ""),
            subtitle=data.get("subtitle", ""),
            status=data.get("status", ""),
            yes_bid=data.get("yes_bid", 0) / 100,  # Convert cents to dollars
            yes_ask=data.get("yes_ask", 0) / 100,
            no_bid=data.get("no_bid", 0) / 100,
            no_ask=data.get("no_ask", 0) / 100,
            last_price=data.get("last_price", 50) / 100,
            volume=data.get("volume", 0),
            volume_24h=data.get("volume_24h", 0),
            open_interest=data.get("open_interest", 0),
            expiration_time=expiration,
            result=data.get("result"),
            category=data.get("category", ""),
        )

    # =========================================================================
    # Order Methods
    # =========================================================================

    def place_order(
        self,
        ticker: str,
        side: OrderSide,
        price: float,
        count: int,
        action: OrderAction = OrderAction.BUY,
        order_type: OrderType = OrderType.LIMIT,
        client_order_id: Optional[str] = None,
        post_only: bool = True,
    ) -> Optional[KalshiOrder]:
        """
        Place an order.

        Args:
            ticker: Market ticker
            side: YES or NO
            price: Price 0.01-0.99 (will be converted to cents)
            count: Number of contracts
            action: BUY or SELL
            order_type: LIMIT or MARKET
            client_order_id: Custom order ID
            post_only: If True, order will only be placed if it adds liquidity

        Returns:
            KalshiOrder or None
        """
        if not client_order_id:
            client_order_id = str(uuid.uuid4())

        # Convert price to cents (1-99)
        price_cents = int(price * 100)
        price_cents = max(1, min(99, price_cents))

        order_data = {
            "ticker": ticker,
            "action": action.value,
            "type": order_type.value,
            "side": side.value,
            "count": count,
            "client_order_id": client_order_id,
        }

        if order_type == OrderType.LIMIT:
            if side == OrderSide.YES:
                order_data["yes_price"] = price_cents
            else:
                order_data["no_price"] = price_cents

        # Post-only flag for maker orders
        if post_only and order_type == OrderType.LIMIT:
            order_data["post_only"] = True

        try:
            response = self._request("POST", "/portfolio/orders", json_data=order_data)
            order = response.get("order", {})

            result = KalshiOrder(
                order_id=order.get("order_id", ""),
                ticker=ticker,
                side=side,
                action=action,
                type=order_type,
                price=price,
                count=count,
                status=OrderStatus(order.get("status", "pending")),
                filled_count=order.get("filled_count", 0),
                remaining_count=order.get("remaining_count", count),
                client_order_id=client_order_id,
            )

            logger.info(
                f"Order placed: {action.value} {count}x {side.value} @ {price:.2f} "
                f"on {ticker} (id={result.order_id[:8]}...)"
            )

            return result

        except Exception as e:
            logger.error(f"Failed to place order: {e}")
            return None

    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an order.

        Args:
            order_id: Order ID to cancel

        Returns:
            True if successful
        """
        try:
            self._request("DELETE", f"/portfolio/orders/{order_id}")
            logger.info(f"Order canceled: {order_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    def get_order(self, order_id: str) -> Optional[KalshiOrder]:
        """Get order by ID."""
        try:
            data = self._request("GET", f"/portfolio/orders/{order_id}")
            order = data.get("order", {})

            return KalshiOrder(
                order_id=order.get("order_id", ""),
                ticker=order.get("ticker", ""),
                side=OrderSide(order.get("side", "yes")),
                action=OrderAction(order.get("action", "buy")),
                type=OrderType(order.get("type", "limit")),
                price=order.get("yes_price", order.get("no_price", 50)) / 100,
                count=order.get("count", 0),
                status=OrderStatus(order.get("status", "pending")),
                filled_count=order.get("filled_count", 0),
                remaining_count=order.get("remaining_count", 0),
                client_order_id=order.get("client_order_id"),
            )
        except Exception as e:
            logger.error(f"Failed to get order {order_id}: {e}")
            return None

    def get_open_orders(self, ticker: Optional[str] = None) -> List[KalshiOrder]:
        """
        Get all open orders.

        Args:
            ticker: Filter by market ticker

        Returns:
            List of open orders
        """
        params = {"status": "resting"}
        if ticker:
            params["ticker"] = ticker

        try:
            data = self._request("GET", "/portfolio/orders", params=params)
            orders = []

            for o in data.get("orders", []):
                orders.append(KalshiOrder(
                    order_id=o.get("order_id", ""),
                    ticker=o.get("ticker", ""),
                    side=OrderSide(o.get("side", "yes")),
                    action=OrderAction(o.get("action", "buy")),
                    type=OrderType(o.get("type", "limit")),
                    price=o.get("yes_price", o.get("no_price", 50)) / 100,
                    count=o.get("count", 0),
                    status=OrderStatus(o.get("status", "pending")),
                    filled_count=o.get("filled_count", 0),
                    remaining_count=o.get("remaining_count", 0),
                    client_order_id=o.get("client_order_id"),
                ))

            return orders

        except Exception as e:
            logger.error(f"Failed to get open orders: {e}")
            return []

    # =========================================================================
    # Portfolio Methods
    # =========================================================================

    def get_balance(self) -> float:
        """
        Get account balance in USD.

        Returns:
            Available balance
        """
        try:
            data = self._request("GET", "/portfolio/balance")
            # Balance is in cents
            balance_cents = data.get("balance", 0)
            return balance_cents / 100
        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            return 0.0

    def get_positions(self) -> List[KalshiPosition]:
        """
        Get all open positions.

        Returns:
            List of positions
        """
        try:
            data = self._request("GET", "/portfolio/positions")
            positions = []

            for p in data.get("market_positions", []):
                positions.append(KalshiPosition(
                    ticker=p.get("ticker", ""),
                    market_title=p.get("market_title", ""),
                    yes_count=p.get("position", 0),  # Positive = long YES
                    no_count=0,  # Kalshi uses net position
                    avg_yes_price=p.get("average_price", 0) / 100,
                    avg_no_price=0,
                    market_exposure=p.get("market_exposure", 0) / 100,
                    realized_pnl=p.get("realized_pnl", 0) / 100,
                    unrealized_pnl=p.get("total_traded", 0) / 100,
                ))

            return positions

        except Exception as e:
            logger.error(f"Failed to get positions: {e}")
            return []

    def get_portfolio_summary(self) -> Dict[str, Any]:
        """
        Get portfolio summary including P&L.

        Returns:
            Portfolio summary dict
        """
        try:
            balance = self.get_balance()
            positions = self.get_positions()

            total_exposure = sum(p.market_exposure for p in positions)
            total_unrealized = sum(p.unrealized_pnl for p in positions)
            total_realized = sum(p.realized_pnl for p in positions)

            return {
                "balance": balance,
                "total_exposure": total_exposure,
                "position_count": len(positions),
                "realized_pnl": total_realized,
                "unrealized_pnl": total_unrealized,
                "total_pnl": total_realized + total_unrealized,
            }

        except Exception as e:
            logger.error(f"Failed to get portfolio summary: {e}")
            return {}

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def health_check(self) -> bool:
        """
        Check if API is accessible.

        Returns:
            True if healthy
        """
        try:
            self._request("GET", "/exchange/status")
            return True
        except Exception:
            return False

    def get_exchange_status(self) -> Dict[str, Any]:
        """Get exchange status and schedule."""
        try:
            return self._request("GET", "/exchange/status")
        except Exception as e:
            logger.error(f"Failed to get exchange status: {e}")
            return {}
