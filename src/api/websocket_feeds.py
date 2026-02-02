"""
WebSocket Price Feed Module

Real-time streaming price data via WebSocket connections.
Inspired by PolymarketBTC15mAssistant's approach to real-time data.

This complements the REST-based PriceFeedAggregator with:
- Lower latency (sub-second updates)
- Continuous streaming without polling overhead
- Automatic reconnection on disconnect
- OHLCV candle construction for TA

Supported exchanges:
- Binance WebSocket streams
- Coinbase WebSocket (future)
- Bybit WebSocket (future)
"""

import asyncio
import json
import time
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, field
from collections import deque
from datetime import datetime
import threading

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False

from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class Candle:
    """OHLCV candle data."""
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float
    trades: int = 0
    complete: bool = False


@dataclass
class StreamingPrice:
    """Real-time price update from WebSocket."""
    symbol: str
    price: float
    bid: float
    ask: float
    volume: float
    timestamp: float
    exchange: str


class CandleBuilder:
    """
    Builds OHLCV candles from streaming price data.

    Used to construct candles for TA indicators (RSI, MACD, etc.)
    from real-time tick data.
    """

    def __init__(self, interval_seconds: int = 60):
        """
        Initialize candle builder.

        Args:
            interval_seconds: Candle interval (default 60 = 1 minute)
        """
        self.interval = interval_seconds
        self.candles: Dict[str, deque] = {}  # symbol -> candle history
        self.current_candle: Dict[str, Candle] = {}  # symbol -> building candle
        self.max_candles = 500  # Keep 500 candles of history

    def add_tick(self, symbol: str, price: float, volume: float, timestamp: float) -> Optional[Candle]:
        """
        Add a price tick and potentially complete a candle.

        Args:
            symbol: Trading symbol
            price: Current price
            volume: Trade volume
            timestamp: Unix timestamp

        Returns:
            Completed candle if one was finished, else None
        """
        # Get candle start time for this interval
        candle_start = (timestamp // self.interval) * self.interval

        if symbol not in self.candles:
            self.candles[symbol] = deque(maxlen=self.max_candles)

        # Check if we need to start a new candle
        if symbol not in self.current_candle:
            self.current_candle[symbol] = Candle(
                timestamp=candle_start,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=volume,
                trades=1,
            )
            return None

        current = self.current_candle[symbol]

        # If this tick belongs to a new candle interval
        if candle_start > current.timestamp:
            # Complete the current candle
            current.complete = True
            self.candles[symbol].append(current)

            # Start new candle
            self.current_candle[symbol] = Candle(
                timestamp=candle_start,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=volume,
                trades=1,
            )

            return current

        # Update current candle
        current.high = max(current.high, price)
        current.low = min(current.low, price)
        current.close = price
        current.volume += volume
        current.trades += 1

        return None

    def get_candles(self, symbol: str, count: int = 50) -> List[Candle]:
        """
        Get recent completed candles.

        Args:
            symbol: Trading symbol
            count: Number of candles to retrieve

        Returns:
            List of completed candles, oldest first
        """
        if symbol not in self.candles:
            return []

        candles = list(self.candles[symbol])[-count:]
        return candles

    def get_price_history(self, symbol: str, periods: int = 50) -> List[Dict[str, Any]]:
        """
        Get price history in dict format for TA indicators.

        Args:
            symbol: Trading symbol
            periods: Number of periods

        Returns:
            List of OHLCV dicts
        """
        candles = self.get_candles(symbol, periods)
        return [
            {
                "timestamp": c.timestamp,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
            }
            for c in candles
        ]


class BinanceWebSocket:
    """
    Binance WebSocket client for real-time price streaming.

    Uses Binance's public WebSocket API (no auth required).
    """

    WS_URL = "wss://stream.binance.com:9443/ws"

    def __init__(
        self,
        symbols: List[str],
        on_price: Optional[Callable[[StreamingPrice], None]] = None,
        on_candle: Optional[Callable[[str, Candle], None]] = None,
    ):
        """
        Initialize Binance WebSocket.

        Args:
            symbols: List of symbols to subscribe (e.g., ["BTC", "ETH"])
            on_price: Callback for price updates
            on_candle: Callback for completed candles
        """
        self.symbols = [s.upper() for s in symbols]
        self.on_price = on_price
        self.on_candle = on_candle

        self.candle_builder = CandleBuilder(interval_seconds=60)
        self._running = False
        self._ws = None
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Price cache for quick access
        self._latest_prices: Dict[str, StreamingPrice] = {}
        self._price_lock = threading.Lock()

        logger.info(f"BinanceWebSocket initialized for {symbols}")

    def _get_stream_names(self) -> List[str]:
        """Get Binance stream names for our symbols."""
        streams = []
        for symbol in self.symbols:
            # Aggregate trade stream for real-time prices
            streams.append(f"{symbol.lower()}usdt@aggTrade")
            # 1-minute kline stream for candles
            streams.append(f"{symbol.lower()}usdt@kline_1m")
        return streams

    async def _connect(self):
        """Connect to Binance WebSocket."""
        streams = self._get_stream_names()
        url = f"{self.WS_URL}/{'/'.join(streams)}" if len(streams) == 1 else self.WS_URL

        # For multiple streams, use combined stream endpoint
        if len(streams) > 1:
            combined_streams = "/".join(streams)
            url = f"wss://stream.binance.com:9443/stream?streams={combined_streams}"

        while self._running:
            try:
                async with websockets.connect(url, ping_interval=30) as ws:
                    self._ws = ws
                    logger.info(f"Connected to Binance WebSocket")

                    while self._running:
                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=35)
                            await self._handle_message(message)
                        except asyncio.TimeoutError:
                            # Send ping to keep connection alive
                            await ws.ping()

            except Exception as e:
                logger.warning(f"WebSocket error: {e}, reconnecting in 5s...")
                await asyncio.sleep(5)

    async def _handle_message(self, message: str):
        """Handle incoming WebSocket message."""
        try:
            data = json.loads(message)

            # Combined stream format wraps data
            if "stream" in data:
                stream = data["stream"]
                data = data["data"]

            # Aggregate trade (real-time price)
            if "e" in data and data["e"] == "aggTrade":
                await self._handle_trade(data)

            # Kline/candlestick
            elif "e" in data and data["e"] == "kline":
                await self._handle_kline(data)

        except Exception as e:
            logger.debug(f"Error handling message: {e}")

    async def _handle_trade(self, data: Dict):
        """Handle aggregate trade message."""
        try:
            symbol_pair = data["s"]  # e.g., "BTCUSDT"
            symbol = symbol_pair.replace("USDT", "")

            price = float(data["p"])
            volume = float(data["q"])
            timestamp = data["T"] / 1000  # Convert ms to seconds

            # Create streaming price
            streaming_price = StreamingPrice(
                symbol=symbol,
                price=price,
                bid=price,  # Approximate from trade
                ask=price,
                volume=volume,
                timestamp=timestamp,
                exchange="binance",
            )

            # Update cache
            with self._price_lock:
                self._latest_prices[symbol] = streaming_price

            # Build candles
            completed = self.candle_builder.add_tick(symbol, price, volume, timestamp)
            if completed and self.on_candle:
                self.on_candle(symbol, completed)

            # Callback
            if self.on_price:
                self.on_price(streaming_price)

        except Exception as e:
            logger.debug(f"Error handling trade: {e}")

    async def _handle_kline(self, data: Dict):
        """Handle kline/candlestick message."""
        try:
            k = data["k"]
            symbol_pair = k["s"]
            symbol = symbol_pair.replace("USDT", "")

            # Only process completed candles
            if k["x"]:  # Is candle closed
                candle = Candle(
                    timestamp=k["t"] / 1000,
                    open=float(k["o"]),
                    high=float(k["h"]),
                    low=float(k["l"]),
                    close=float(k["c"]),
                    volume=float(k["v"]),
                    trades=k["n"],
                    complete=True,
                )

                # Add to candle history
                if symbol not in self.candle_builder.candles:
                    self.candle_builder.candles[symbol] = deque(maxlen=500)
                self.candle_builder.candles[symbol].append(candle)

                if self.on_candle:
                    self.on_candle(symbol, candle)

        except Exception as e:
            logger.debug(f"Error handling kline: {e}")

    def start(self):
        """Start WebSocket connection in background thread."""
        if not WEBSOCKETS_AVAILABLE:
            logger.error("websockets library not installed. Run: pip install websockets")
            return

        self._running = True

        def run_loop():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._connect())

        self._thread = threading.Thread(target=run_loop, daemon=True)
        self._thread.start()
        logger.info("Started Binance WebSocket thread")

    def stop(self):
        """Stop WebSocket connection."""
        self._running = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        logger.info("Stopped Binance WebSocket")

    def get_price(self, symbol: str) -> Optional[StreamingPrice]:
        """
        Get latest price for a symbol.

        Args:
            symbol: Symbol like "BTC" or "ETH"

        Returns:
            Latest StreamingPrice or None
        """
        with self._price_lock:
            return self._latest_prices.get(symbol.upper())

    def get_candles(self, symbol: str, count: int = 50) -> List[Candle]:
        """Get recent candles for TA calculations."""
        return self.candle_builder.get_candles(symbol.upper(), count)

    def get_price_history(self, symbol: str, periods: int = 50, interval_seconds: int = 60) -> List[Dict[str, Any]]:
        """
        Get price history formatted for TA indicators.

        Args:
            symbol: Trading symbol
            periods: Number of periods
            interval_seconds: Candle interval (for compatibility, uses 1m candles)

        Returns:
            List of OHLCV dicts
        """
        return self.candle_builder.get_price_history(symbol.upper(), periods)


class WebSocketPriceFeed:
    """
    Unified WebSocket price feed interface.

    Manages WebSocket connections to multiple exchanges and provides
    a unified interface for real-time price data.
    """

    def __init__(
        self,
        symbols: List[str] = None,
        exchanges: List[str] = None,
    ):
        """
        Initialize WebSocket price feed.

        Args:
            symbols: Symbols to stream (default: BTC, ETH)
            exchanges: Exchanges to connect (default: binance)
        """
        self.symbols = symbols or ["BTC", "ETH"]
        self.exchanges = exchanges or ["binance"]

        self._feeds: Dict[str, Any] = {}
        self._candle_callbacks: List[Callable] = []
        self._price_callbacks: List[Callable] = []

        # Initialize exchange feeds
        if "binance" in self.exchanges:
            self._feeds["binance"] = BinanceWebSocket(
                symbols=self.symbols,
                on_price=self._on_price,
                on_candle=self._on_candle,
            )

    def _on_price(self, price: StreamingPrice):
        """Handle price update from any exchange."""
        for callback in self._price_callbacks:
            try:
                callback(price)
            except Exception as e:
                logger.debug(f"Price callback error: {e}")

    def _on_candle(self, symbol: str, candle: Candle):
        """Handle candle completion from any exchange."""
        for callback in self._candle_callbacks:
            try:
                callback(symbol, candle)
            except Exception as e:
                logger.debug(f"Candle callback error: {e}")

    def on_price(self, callback: Callable[[StreamingPrice], None]):
        """Register callback for price updates."""
        self._price_callbacks.append(callback)

    def on_candle(self, callback: Callable[[str, Candle], None]):
        """Register callback for completed candles."""
        self._candle_callbacks.append(callback)

    def start(self):
        """Start all WebSocket connections."""
        for name, feed in self._feeds.items():
            logger.info(f"Starting {name} WebSocket feed...")
            feed.start()

        # Wait for initial data
        time.sleep(2)
        logger.info("WebSocket price feeds started")

    def stop(self):
        """Stop all WebSocket connections."""
        for feed in self._feeds.values():
            feed.stop()
        logger.info("WebSocket price feeds stopped")

    def get_price(self, symbol: str) -> Optional[StreamingPrice]:
        """Get latest price from any exchange."""
        for feed in self._feeds.values():
            price = feed.get_price(symbol)
            if price:
                return price
        return None

    def get_price_history(
        self,
        symbol: str,
        periods: int = 50,
        interval_seconds: int = 60,
    ) -> List[Dict[str, Any]]:
        """
        Get price history for TA calculations.

        Args:
            symbol: Trading symbol
            periods: Number of periods
            interval_seconds: Candle interval

        Returns:
            List of OHLCV dicts
        """
        # Try each feed
        for feed in self._feeds.values():
            history = feed.get_price_history(symbol, periods, interval_seconds)
            if history and len(history) >= periods * 0.5:  # At least 50% of requested data
                return history
        return []

    def get_candles(self, symbol: str, count: int = 50) -> List[Candle]:
        """Get recent candles from any exchange."""
        for feed in self._feeds.values():
            candles = feed.get_candles(symbol, count)
            if candles:
                return candles
        return []

    def detect_spike(
        self,
        symbol: str,
        threshold_percent: float = 3.0,
        window_seconds: int = 60,
    ) -> Optional[Dict[str, Any]]:
        """
        Detect price spike using WebSocket data.

        Args:
            symbol: Symbol to check
            threshold_percent: Minimum move to trigger
            window_seconds: Time window

        Returns:
            Spike info or None
        """
        candles = self.get_candles(symbol, count=5)
        if len(candles) < 2:
            return None

        # Calculate change from oldest to newest
        oldest_price = candles[0].close
        newest_price = candles[-1].close

        if oldest_price <= 0:
            return None

        change_pct = ((newest_price - oldest_price) / oldest_price) * 100

        if abs(change_pct) >= threshold_percent:
            return {
                "symbol": symbol,
                "direction": "up" if change_pct > 0 else "down",
                "magnitude_pct": abs(change_pct),
                "current_price": newest_price,
                "timestamp": time.time(),
                "window_seconds": window_seconds,
            }

        return None

    def get_volatility(
        self,
        symbol: str,
        window_seconds: int = 60,
    ) -> Optional[Dict[str, float]]:
        """
        Calculate volatility from WebSocket data.

        Args:
            symbol: Symbol to analyze
            window_seconds: Lookback window

        Returns:
            Volatility metrics or None
        """
        import statistics

        # Get candles for the window
        candles_needed = max(2, window_seconds // 60)
        candles = self.get_candles(symbol, count=candles_needed + 5)

        if len(candles) < 2:
            return None

        prices = [c.close for c in candles]

        current_price = prices[-1]
        start_price = prices[0]

        price_change = (current_price - start_price) / start_price if start_price > 0 else 0

        # Calculate returns
        returns = [(prices[i] - prices[i-1]) / prices[i-1]
                   for i in range(1, len(prices)) if prices[i-1] > 0]

        volatility = statistics.stdev(returns) if len(returns) > 1 else 0

        return {
            "current_price": current_price,
            "price_change_pct": price_change * 100,
            "volatility": volatility,
            "data_points": len(candles),
            "window_seconds": window_seconds,
        }
