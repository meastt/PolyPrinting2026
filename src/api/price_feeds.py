"""
Multi-Exchange Price Feed Aggregator

Fetches real-time cryptocurrency prices from multiple exchanges using CCXT
to calculate fair values for Polymarket crypto markets.

Supported exchanges:
- Binance
- Coinbase
- Bybit
- Kraken

Reference: https://github.com/ccxt/ccxt
"""

import time
import statistics
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field
from collections import deque
from threading import Lock
import os

try:
    import ccxt
    CCXT_AVAILABLE = True
except ImportError:
    CCXT_AVAILABLE = False
    print("WARNING: ccxt not installed. Run: pip install ccxt")

from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class PriceData:
    """Represents price data from an exchange."""
    symbol: str
    exchange: str
    price: float
    bid: float
    ask: float
    timestamp: float
    volume_24h: float = 0.0


@dataclass
class AggregatedPrice:
    """Aggregated price from multiple exchanges."""
    symbol: str
    price: float  # Median/average of exchanges
    bid: float  # Best bid
    ask: float  # Best ask
    spread: float
    sources: List[str]
    timestamp: float
    confidence: float  # 0-1 based on source agreement


@dataclass
class PriceHistory:
    """Price history for volatility calculation."""
    prices: deque = field(default_factory=lambda: deque(maxlen=3600))  # 1 hour at 1s intervals
    timestamps: deque = field(default_factory=lambda: deque(maxlen=3600))

    def add(self, price: float, timestamp: float):
        self.prices.append(price)
        self.timestamps.append(timestamp)

    def get_prices_in_window(self, window_seconds: int) -> List[float]:
        """Get prices within the last N seconds."""
        if not self.prices:
            return []
        cutoff = time.time() - window_seconds
        return [p for p, t in zip(self.prices, self.timestamps) if t >= cutoff]


class PriceFeedAggregator:
    """
    Aggregates cryptocurrency prices from multiple exchanges.

    Used for:
    - Calculating fair values for crypto prediction markets
    - Detecting volatility spikes for the spike reversion strategy
    - Validating Polymarket prices against spot markets

    The aggregator maintains price history for volatility calculations
    and provides confidence scores based on exchange agreement.
    """

    # Default exchanges to query (free tier, no auth required for public data)
    DEFAULT_EXCHANGES = ["binance", "coinbase", "bybit", "kraken"]

    # Common trading pairs
    SYMBOL_MAP = {
        "BTC": "BTC/USDT",
        "ETH": "ETH/USDT",
        "SOL": "SOL/USDT",
        "MATIC": "MATIC/USDT",
        "DOGE": "DOGE/USDT",
    }

    def __init__(
        self,
        exchanges: Optional[List[str]] = None,
        cache_ttl: float = 1.0,  # Cache prices for 1 second
    ):
        """
        Initialize the price feed aggregator.

        Args:
            exchanges: List of exchange IDs to use
            cache_ttl: How long to cache prices (seconds)
        """
        if not CCXT_AVAILABLE:
            logger.error("CCXT not available. Price feeds will not work.")
            self.exchanges = {}
            return

        self.exchange_ids = exchanges or self.DEFAULT_EXCHANGES
        self.cache_ttl = cache_ttl
        self.exchanges: Dict[str, ccxt.Exchange] = {}
        self._price_cache: Dict[str, Tuple[AggregatedPrice, float]] = {}
        self._price_history: Dict[str, PriceHistory] = {}
        self._lock = Lock()

        self._initialize_exchanges()

    def _initialize_exchanges(self) -> None:
        """Initialize exchange connections."""
        for exchange_id in self.exchange_ids:
            try:
                exchange_class = getattr(ccxt, exchange_id)

                # Configure exchange
                config = {
                    "enableRateLimit": True,
                    "timeout": 10000,  # 10 second timeout
                }

                # Add API keys if available (for higher rate limits)
                api_key_env = f"{exchange_id.upper()}_API_KEY"
                api_secret_env = f"{exchange_id.upper()}_API_SECRET"

                if os.getenv(api_key_env):
                    config["apiKey"] = os.getenv(api_key_env)
                    config["secret"] = os.getenv(api_secret_env, "")

                self.exchanges[exchange_id] = exchange_class(config)
                logger.info(f"Initialized exchange: {exchange_id}")

            except Exception as e:
                logger.warning(f"Failed to initialize {exchange_id}: {e}")

        logger.info(f"Price feed aggregator ready with {len(self.exchanges)} exchanges")

    def get_price(self, symbol: str) -> Optional[AggregatedPrice]:
        """
        Get aggregated price for a symbol.

        Args:
            symbol: Symbol like "BTC" or "ETH"

        Returns:
            AggregatedPrice or None if unavailable
        """
        # Check cache first
        cache_key = symbol.upper()
        with self._lock:
            if cache_key in self._price_cache:
                cached_price, cached_time = self._price_cache[cache_key]
                if time.time() - cached_time < self.cache_ttl:
                    return cached_price

        # Fetch fresh prices
        prices = self._fetch_prices(symbol)

        if not prices:
            return None

        # Aggregate
        aggregated = self._aggregate_prices(symbol, prices)

        # Update cache and history
        with self._lock:
            self._price_cache[cache_key] = (aggregated, time.time())

            if cache_key not in self._price_history:
                self._price_history[cache_key] = PriceHistory()
            self._price_history[cache_key].add(aggregated.price, aggregated.timestamp)

        return aggregated

    def _fetch_prices(self, symbol: str) -> List[PriceData]:
        """Fetch prices from all exchanges."""
        trading_pair = self.SYMBOL_MAP.get(symbol.upper(), f"{symbol.upper()}/USDT")
        prices = []

        for exchange_id, exchange in self.exchanges.items():
            try:
                ticker = exchange.fetch_ticker(trading_pair)

                price_data = PriceData(
                    symbol=symbol.upper(),
                    exchange=exchange_id,
                    price=float(ticker.get("last", 0) or ticker.get("close", 0)),
                    bid=float(ticker.get("bid", 0) or 0),
                    ask=float(ticker.get("ask", 0) or 0),
                    timestamp=time.time(),
                    volume_24h=float(ticker.get("quoteVolume", 0) or 0),
                )

                if price_data.price > 0:
                    prices.append(price_data)
                    logger.debug(
                        f"{exchange_id} {symbol}: ${price_data.price:.2f}"
                    )

            except Exception as e:
                logger.debug(f"Failed to fetch {symbol} from {exchange_id}: {e}")

        return prices

    def _aggregate_prices(self, symbol: str, prices: List[PriceData]) -> AggregatedPrice:
        """Aggregate prices from multiple sources."""
        if not prices:
            return AggregatedPrice(
                symbol=symbol,
                price=0.0,
                bid=0.0,
                ask=0.0,
                spread=0.0,
                sources=[],
                timestamp=time.time(),
                confidence=0.0,
            )

        # Calculate median price (robust to outliers)
        price_values = [p.price for p in prices]
        median_price = statistics.median(price_values)

        # Best bid/ask across exchanges
        best_bid = max(p.bid for p in prices if p.bid > 0) if any(p.bid > 0 for p in prices) else 0
        best_ask = min(p.ask for p in prices if p.ask > 0) if any(p.ask > 0 for p in prices) else 0

        # Calculate spread
        spread = (best_ask - best_bid) / median_price if median_price > 0 and best_ask > 0 else 0

        # Calculate confidence based on price agreement
        # Lower std dev = higher confidence
        if len(price_values) > 1:
            std_dev = statistics.stdev(price_values)
            # Confidence decreases as std dev increases relative to price
            relative_std = std_dev / median_price if median_price > 0 else 1
            confidence = max(0, 1 - (relative_std * 100))  # Scale appropriately
        else:
            confidence = 0.5  # Single source = medium confidence

        return AggregatedPrice(
            symbol=symbol.upper(),
            price=median_price,
            bid=best_bid,
            ask=best_ask,
            spread=spread,
            sources=[p.exchange for p in prices],
            timestamp=time.time(),
            confidence=min(1.0, confidence),
        )

    def get_volatility(
        self,
        symbol: str,
        window_seconds: int = 60,
    ) -> Optional[Dict[str, float]]:
        """
        Calculate price volatility over a time window.

        Used by the spike reversion strategy to detect sharp moves.

        Args:
            symbol: Symbol like "BTC" or "ETH"
            window_seconds: Lookback window in seconds

        Returns:
            Dict with volatility metrics or None
        """
        cache_key = symbol.upper()

        with self._lock:
            if cache_key not in self._price_history:
                return None

            history = self._price_history[cache_key]
            prices = history.get_prices_in_window(window_seconds)

        if len(prices) < 2:
            return None

        current_price = prices[-1]
        min_price = min(prices)
        max_price = max(prices)
        start_price = prices[0]

        # Calculate metrics
        price_change = (current_price - start_price) / start_price if start_price > 0 else 0
        price_range = (max_price - min_price) / start_price if start_price > 0 else 0

        # Calculate returns for std dev
        returns = [(prices[i] - prices[i-1]) / prices[i-1]
                   for i in range(1, len(prices)) if prices[i-1] > 0]

        volatility = statistics.stdev(returns) if len(returns) > 1 else 0

        return {
            "current_price": current_price,
            "price_change_pct": price_change * 100,
            "price_range_pct": price_range * 100,
            "volatility": volatility,
            "data_points": len(prices),
            "window_seconds": window_seconds,
        }

    def detect_spike(
        self,
        symbol: str,
        threshold_percent: float = 3.0,
        window_seconds: int = 60,
    ) -> Optional[Dict[str, any]]:
        """
        Detect if a price spike has occurred.

        Used by the spike reversion strategy to identify trading opportunities.

        Args:
            symbol: Symbol to check
            threshold_percent: Minimum move to trigger (e.g., 3.0 for 3%)
            window_seconds: Time window to check

        Returns:
            Spike info dict or None if no spike
        """
        vol_data = self.get_volatility(symbol, window_seconds)

        if not vol_data:
            return None

        price_change_pct = vol_data["price_change_pct"]

        if abs(price_change_pct) >= threshold_percent:
            return {
                "symbol": symbol,
                "direction": "up" if price_change_pct > 0 else "down",
                "magnitude_pct": abs(price_change_pct),
                "current_price": vol_data["current_price"],
                "timestamp": time.time(),
                "window_seconds": window_seconds,
            }

        return None

    def get_fair_value(
        self,
        symbol: str,
        direction: str,  # "up" or "down"
        threshold_percent: float = 0.0,
        time_horizon_minutes: int = 15,
    ) -> Optional[float]:
        """
        Estimate fair value for a binary crypto market.

        For a "Will BTC go up in 15 minutes?" market, this estimates
        the probability based on current price action and volatility.

        This is a simplified model - real implementations should use
        more sophisticated pricing (e.g., Black-Scholes for binary options).

        Args:
            symbol: The crypto asset
            direction: "up" or "down"
            threshold_percent: Price change threshold for the market
            time_horizon_minutes: Time until market resolution

        Returns:
            Estimated fair probability (0-1)
        """
        vol_data = self.get_volatility(symbol, window_seconds=300)  # 5 min lookback

        if not vol_data:
            return 0.5  # No data, assume 50/50

        # Get current momentum
        recent_vol = self.get_volatility(symbol, window_seconds=60)
        momentum = recent_vol["price_change_pct"] if recent_vol else 0

        # Base probability
        fair_value = 0.5

        # Adjust based on momentum
        # This is a simplified model - momentum tends to mean-revert
        # so we actually reduce probability slightly in the momentum direction
        momentum_factor = 0.02  # Small effect

        if direction == "up":
            # If price is already up, slight reduction (mean reversion)
            fair_value -= momentum * momentum_factor
        else:
            # If price is down, slight reduction for "down" bet
            fair_value += momentum * momentum_factor

        # Clamp to reasonable range
        fair_value = max(0.3, min(0.7, fair_value))

        return fair_value

    def start_continuous_polling(
        self,
        symbols: List[str],
        interval_seconds: float = 1.0,
    ) -> None:
        """
        Start continuous price polling in the background.

        This keeps the price history populated for volatility calculations.

        Args:
            symbols: List of symbols to poll
            interval_seconds: Polling interval
        """
        import threading

        def poll_loop():
            while True:
                for symbol in symbols:
                    try:
                        self.get_price(symbol)
                    except Exception as e:
                        logger.debug(f"Polling error for {symbol}: {e}")
                time.sleep(interval_seconds)

        thread = threading.Thread(target=poll_loop, daemon=True)
        thread.start()
        logger.info(f"Started continuous price polling for {symbols}")

    def get_all_prices(
        self,
        symbols: Optional[List[str]] = None,
    ) -> Dict[str, AggregatedPrice]:
        """
        Get prices for multiple symbols.

        Args:
            symbols: List of symbols (default: BTC, ETH)

        Returns:
            Dict mapping symbol to AggregatedPrice
        """
        symbols = symbols or ["BTC", "ETH"]
        prices = {}

        for symbol in symbols:
            price = self.get_price(symbol)
            if price:
                prices[symbol] = price

        return prices

    def health_check(self) -> Dict[str, bool]:
        """
        Check health of all exchange connections.

        Returns:
            Dict mapping exchange ID to health status
        """
        health = {}

        for exchange_id, exchange in self.exchanges.items():
            try:
                exchange.fetch_ticker("BTC/USDT")
                health[exchange_id] = True
            except Exception:
                health[exchange_id] = False

        return health
