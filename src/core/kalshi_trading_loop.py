"""
Kalshi Trading Loop

Main event loop for automated Kalshi prediction market trading.
Adapted from the Polymarket trading loop for US-legal trading.

Key differences from Polymarket:
- Uses KalshiClient instead of PolymarketClient
- Markets identified by ticker (e.g., "BTCUSD-26FEB02-B101500")
- Zero fees on resting (maker) orders
- Hourly crypto markets available for spike reversion
"""

import time
import signal
import sys
from typing import Optional, Dict, List, Any
from dataclasses import dataclass
import threading

from src.api.kalshi_client import (
    KalshiClient,
    KalshiMarket,
    KalshiOrder,
    KalshiPosition,
    OrderSide,
    OrderAction,
    OrderType,
)
from src.api.price_feeds import PriceFeedAggregator
from src.api.websocket_feeds import WebSocketPriceFeed
from src.core.risk_manager import RiskManager
from src.utils.logger import get_logger
from src.utils.helpers import load_config

logger = get_logger(__name__)


@dataclass
class LoopMetrics:
    """Metrics for the trading loop."""
    iterations: int = 0
    trades_executed: int = 0
    errors: int = 0
    uptime_seconds: float = 0
    last_trade_time: Optional[float] = None
    avg_iteration_ms: float = 0
    maker_orders: int = 0
    taker_orders: int = 0


class KalshiOrderManager:
    """
    Manages orders for Kalshi.

    Simplified order manager specifically for Kalshi's API.
    """

    def __init__(self, client: KalshiClient):
        """
        Initialize order manager.

        Args:
            client: KalshiClient instance
        """
        self.client = client
        self._open_orders: Dict[str, KalshiOrder] = {}
        self._monitoring = False
        self._monitor_thread: Optional[threading.Thread] = None

        # Stats
        self._total_orders = 0
        self._filled_orders = 0
        self._cancelled_orders = 0
        self._total_fees = 0.0  # Should be minimal/zero with maker orders

    def place_order(
        self,
        ticker: str,
        side: str,  # "yes" or "no"
        price: float,
        count: int,
        post_only: bool = True,
    ) -> Optional[KalshiOrder]:
        """
        Place an order on Kalshi.

        Args:
            ticker: Market ticker
            side: "yes" or "no"
            price: Price 0.01-0.99
            count: Number of contracts
            post_only: If True, only place if it adds liquidity

        Returns:
            KalshiOrder or None
        """
        order_side = OrderSide.YES if side.lower() == "yes" else OrderSide.NO

        order = self.client.place_order(
            ticker=ticker,
            side=order_side,
            price=price,
            count=count,
            action=OrderAction.BUY,
            order_type=OrderType.LIMIT,
            post_only=post_only,
        )

        if order:
            self._open_orders[order.order_id] = order
            self._total_orders += 1

            if order.is_maker:
                logger.debug(f"Maker order placed: {order.order_id}")

        return order

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""
        success = self.client.cancel_order(order_id)
        if success:
            self._open_orders.pop(order_id, None)
            self._cancelled_orders += 1
        return success

    def cancel_all_orders(self) -> int:
        """Cancel all open orders."""
        cancelled = 0
        for order_id in list(self._open_orders.keys()):
            if self.cancel_order(order_id):
                cancelled += 1
        return cancelled

    def get_open_orders(self, ticker: Optional[str] = None) -> List[KalshiOrder]:
        """Get open orders."""
        orders = self.client.get_open_orders(ticker)
        # Update local cache
        self._open_orders = {o.order_id: o for o in orders}
        return orders

    def start_monitoring(self) -> None:
        """Start order monitoring thread."""
        if self._monitoring:
            return

        self._monitoring = True

        def monitor_loop():
            while self._monitoring:
                try:
                    self.get_open_orders()
                    time.sleep(5)
                except Exception as e:
                    logger.debug(f"Order monitoring error: {e}")

        self._monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        self._monitor_thread.start()

    def stop_monitoring(self) -> None:
        """Stop order monitoring."""
        self._monitoring = False

    def get_stats(self) -> Dict[str, Any]:
        """Get order statistics."""
        return {
            "total_orders": self._total_orders,
            "filled_orders": self._filled_orders,
            "cancelled_orders": self._cancelled_orders,
            "open_orders": len(self._open_orders),
            "total_fees": self._total_fees,
        }


class KalshiPositionManager:
    """
    Manages positions for Kalshi.
    """

    def __init__(self, client: KalshiClient):
        """
        Initialize position manager.

        Args:
            client: KalshiClient instance
        """
        self.client = client
        self._positions: Dict[str, KalshiPosition] = {}

        # Stats
        self._total_pnl = 0.0
        self._win_count = 0
        self._loss_count = 0

    def update_positions(self) -> None:
        """Update positions from API."""
        positions = self.client.get_positions()
        self._positions = {p.ticker: p for p in positions}

    def get_position(self, ticker: str) -> Optional[KalshiPosition]:
        """Get position for a market."""
        return self._positions.get(ticker)

    def get_all_positions(self) -> List[KalshiPosition]:
        """Get all positions."""
        return list(self._positions.values())

    def has_position(self, ticker: str) -> bool:
        """Check if we have a position in a market."""
        pos = self._positions.get(ticker)
        return pos is not None and (pos.yes_count != 0 or pos.no_count != 0)

    def get_stats(self) -> Dict[str, Any]:
        """Get position statistics."""
        total_exposure = sum(p.market_exposure for p in self._positions.values())
        total_unrealized = sum(p.unrealized_pnl for p in self._positions.values())

        total_trades = self._win_count + self._loss_count
        win_rate = (self._win_count / total_trades * 100) if total_trades > 0 else 0

        return {
            "open_positions": len(self._positions),
            "total_exposure": total_exposure,
            "unrealized_pnl": total_unrealized,
            "total_pnl": self._total_pnl,
            "win_count": self._win_count,
            "loss_count": self._loss_count,
            "win_rate": win_rate,
        }


class KalshiTradingLoop:
    """
    Main trading loop for Kalshi.

    Coordinates all bot activities for US-legal prediction market trading:
    1. Fetch crypto market data from Kalshi
    2. Run each enabled strategy
    3. Collect and filter trading signals
    4. Execute approved trades using maker orders
    5. Update positions and risk
    6. Log and report

    Designed for 24/7 operation with:
    - Zero fees on maker orders
    - Hourly crypto markets
    - CFTC-regulated trading
    """

    def __init__(
        self,
        config_path: str = "config/config.yaml",
        simulation_mode: Optional[bool] = None,
    ):
        """
        Initialize the Kalshi trading loop.

        Args:
            config_path: Path to configuration file
            simulation_mode: Override config simulation mode
        """
        # Load configuration
        self.config = load_config(config_path)

        # Determine mode
        if simulation_mode is not None:
            self.simulation_mode = simulation_mode
        else:
            self.simulation_mode = self.config.get("general", {}).get("mode", "simulation") == "simulation"

        # Configuration
        self.poll_interval = self.config.get("general", {}).get("poll_interval_seconds", 2)
        self.starting_balance = self.config.get("general", {}).get("starting_balance", 50.0)

        # Initialize components
        self._init_components()

        # Strategies (initialized later)
        self._strategies: List[Any] = []

        # Control flags
        self._running = False
        self._paused = False
        self._shutdown_event = threading.Event()

        # Metrics
        self.metrics = LoopMetrics()
        self._start_time: Optional[float] = None
        self._iteration_times: List[float] = []

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        logger.info(
            f"KalshiTradingLoop initialized (simulation={self.simulation_mode}, "
            f"poll_interval={self.poll_interval}s, balance=${self.starting_balance})"
        )

    def _init_components(self) -> None:
        """Initialize all trading components."""
        # Kalshi API client
        kalshi_config = self.config.get("api", {}).get("kalshi", {})
        use_demo = kalshi_config.get("use_demo", self.simulation_mode)

        self.kalshi = KalshiClient(
            use_demo=use_demo,
        )

        # Price feeds for spot data (used by spike reversion)
        self.price_feeds = PriceFeedAggregator()

        # WebSocket for real-time prices
        ws_config = self.config.get("strategies", {}).get("btc_15m_ta", {}).get("websocket", {})
        if ws_config.get("enabled", True):
            self.ws_feeds = WebSocketPriceFeed(
                symbols=["BTC", "ETH"],
                exchanges=ws_config.get("exchanges", ["binance"]),
            )
        else:
            self.ws_feeds = None

        # Core managers
        self.risk_manager = RiskManager(
            starting_balance=self.starting_balance,
        )

        self.order_manager = KalshiOrderManager(
            client=self.kalshi,
        )

        self.position_manager = KalshiPositionManager(
            client=self.kalshi,
        )

        logger.info("All Kalshi components initialized")

    def register_strategy(self, strategy) -> None:
        """
        Register a trading strategy.

        Args:
            strategy: Strategy instance with evaluate() method
        """
        self._strategies.append(strategy)
        logger.info(f"Registered strategy: {strategy.name}")

    def _signal_handler(self, signum, frame) -> None:
        """Handle shutdown signals."""
        logger.info(f"Received signal {signum}, initiating shutdown...")
        self.stop()

    def start(self) -> None:
        """Start the trading loop."""
        if self._running:
            logger.warning("Trading loop already running")
            return

        logger.info("=" * 50)
        logger.info("STARTING KALSHI TRADING BOT")
        logger.info("US-Legal CFTC-Regulated Trading")
        logger.info("=" * 50)
        logger.info(f"Mode: {'SIMULATION' if self.simulation_mode else 'LIVE'}")
        logger.info(f"Starting balance: ${self.starting_balance}")
        logger.info(f"Strategies: {[s.name for s in self._strategies]}")
        logger.info("=" * 50)

        self._running = True
        self._start_time = time.time()

        # Check API health
        if not self.kalshi.health_check():
            logger.error("Failed to connect to Kalshi API")
            if not self.simulation_mode:
                return

        # Get initial balance
        if not self.simulation_mode:
            balance = self.kalshi.get_balance()
            if balance > 0:
                self.risk_manager.update_balance(balance)
                logger.info(f"Account balance: ${balance:.2f}")

        # Start background services
        self.order_manager.start_monitoring()
        self.price_feeds.start_continuous_polling(["BTC", "ETH"])

        if self.ws_feeds:
            self.ws_feeds.start()

        # Main loop
        try:
            self._run_loop()
        except Exception as e:
            logger.critical(f"Fatal error in trading loop: {e}", exc_info=True)
        finally:
            self._cleanup()

    def stop(self) -> None:
        """Stop the trading loop gracefully."""
        logger.info("Stopping trading loop...")
        self._running = False
        self._shutdown_event.set()

    def pause(self) -> None:
        """Pause trading (keep loop running but don't execute)."""
        self._paused = True
        logger.info("Trading paused")

    def resume(self) -> None:
        """Resume trading after pause."""
        self._paused = False
        logger.info("Trading resumed")

    def _run_loop(self) -> None:
        """Main trading loop."""
        consecutive_errors = 0
        max_consecutive_errors = 10

        while self._running:
            iteration_start = time.time()

            try:
                # Check if paused
                if self._paused:
                    time.sleep(1)
                    continue

                # Check risk manager status
                if not self.risk_manager.is_trading_allowed():
                    logger.warning("Trading not allowed by risk manager")
                    time.sleep(10)
                    continue

                # Run one iteration
                self._run_iteration()

                # Reset error counter on success
                consecutive_errors = 0

                # Update metrics
                self.metrics.iterations += 1
                iteration_time = (time.time() - iteration_start) * 1000
                self._iteration_times.append(iteration_time)
                if len(self._iteration_times) > 100:
                    self._iteration_times = self._iteration_times[-100:]
                self.metrics.avg_iteration_ms = sum(self._iteration_times) / len(self._iteration_times)

            except Exception as e:
                consecutive_errors += 1
                self.metrics.errors += 1
                logger.error(f"Iteration error ({consecutive_errors}): {e}")

                if consecutive_errors >= max_consecutive_errors:
                    logger.critical("Too many consecutive errors, pausing...")
                    self.pause()
                    time.sleep(60)
                    self.resume()
                    consecutive_errors = 0

            # Sleep for poll interval
            elapsed = time.time() - iteration_start
            sleep_time = max(0, self.poll_interval - elapsed)
            if sleep_time > 0 and self._running:
                self._shutdown_event.wait(timeout=sleep_time)

    def _run_iteration(self) -> None:
        """Run a single iteration of the trading loop."""
        # 1. Fetch crypto markets from Kalshi
        markets = self._fetch_markets()

        # 2. Update positions
        self.position_manager.update_positions()

        # 3. Update balance
        balance = self._get_balance()
        self.risk_manager.update_balance(balance)

        # 4. Run strategies and collect signals
        signals = []
        for strategy in self._strategies:
            if not self._is_strategy_enabled(strategy.name):
                continue

            try:
                # Adapt markets to strategy format
                strategy_signals = strategy.evaluate(
                    markets=markets,
                    positions=self.position_manager.get_all_positions(),
                    balance=balance,
                )
                signals.extend(strategy_signals)
            except Exception as e:
                logger.error(f"Strategy {strategy.name} error: {e}")

        # 5. Filter and rank signals
        approved_signals = self._filter_signals(signals)

        # 6. Execute trades
        for signal in approved_signals:
            self._execute_signal(signal)

        # 7. Log status periodically
        if self.metrics.iterations % 30 == 0:  # Every ~minute at 2s intervals
            self._log_status()

    def _fetch_markets(self) -> List[KalshiMarket]:
        """Fetch crypto markets from Kalshi."""
        try:
            # Get hourly crypto markets (ideal for our strategies)
            btc_markets = self.kalshi.get_hourly_crypto_markets("BTC")
            eth_markets = self.kalshi.get_hourly_crypto_markets("ETH")

            all_markets = btc_markets + eth_markets

            # Filter by config
            market_config = self.config.get("markets", {})
            min_liquidity = market_config.get("min_liquidity", 100)

            filtered = [m for m in all_markets if m.volume_24h >= min_liquidity]

            return filtered

        except Exception as e:
            logger.error(f"Failed to fetch markets: {e}")
            return []

    def _get_balance(self) -> float:
        """Get current balance."""
        if self.simulation_mode:
            return self.risk_manager.current_balance
        else:
            return self.kalshi.get_balance()

    def _is_strategy_enabled(self, strategy_name: str) -> bool:
        """Check if a strategy is enabled in config."""
        strategies_config = self.config.get("strategies", {})

        # Map strategy names to config keys
        name_map = {
            "arbitrage": "arbitrage",
            "market_maker": "market_making",
            "spike_reversion": "spike_reversion",
            "copy_trader": "copy_trading",
            "btc_15m_ta": "btc_15m_ta",
            "kalshi_crypto_ta": "btc_15m_ta",
        }

        config_key = name_map.get(strategy_name.lower(), strategy_name.lower())
        strategy_config = strategies_config.get(config_key, {})

        return strategy_config.get("enabled", False)

    def _filter_signals(self, signals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Filter and prioritize trading signals.

        Applies:
        - EV threshold check
        - Risk assessment
        - Position limit checks
        """
        if not signals:
            return []

        ev_threshold = self.config.get("ev", {}).get("min_ev", 0.02)
        approved = []

        for signal in signals:
            # Check EV threshold
            if signal.get("ev", 0) < ev_threshold:
                logger.debug(f"Signal rejected: EV {signal.get('ev'):.3f} < {ev_threshold}")
                continue

            # Get risk assessment
            assessment = self.risk_manager.assess_trade(
                trade_size=signal.get("size", 0),
                market_id=signal.get("ticker", signal.get("market_id", "")),
                outcome=signal.get("side", signal.get("outcome", "")),
            )

            if not assessment.approved:
                logger.debug(f"Signal rejected by risk manager: {assessment.reasons}")
                continue

            # Apply size limits
            if signal.get("size", 0) > assessment.max_size:
                signal["size"] = assessment.max_size

            approved.append(signal)

        # Sort by EV descending
        approved.sort(key=lambda s: s.get("ev", 0), reverse=True)

        # Limit number of trades per iteration
        max_trades = 3
        return approved[:max_trades]

    def _execute_signal(self, signal: Dict[str, Any]) -> bool:
        """
        Execute a trading signal on Kalshi.

        Args:
            signal: Trading signal dict

        Returns:
            True if execution successful
        """
        try:
            ticker = signal.get("ticker", signal.get("market_id"))
            side = signal.get("side", signal.get("outcome", "yes")).lower()
            price = signal.get("price", 0.5)
            size = signal.get("size", 1.0)

            # Calculate number of contracts
            # Each contract costs (price * 100) cents
            # Size is in USD, so contracts = size / price
            count = max(1, int(size / price))

            # Place order (prefer maker orders for zero fees)
            order = self.order_manager.place_order(
                ticker=ticker,
                side=side,
                price=price,
                count=count,
                post_only=True,  # Zero fees on maker orders!
            )

            if order:
                # Record in risk manager
                self.risk_manager.record_trade(
                    market_id=ticker,
                    outcome=side,
                    size=count * price,
                    price=price,
                )

                self.metrics.trades_executed += 1
                self.metrics.last_trade_time = time.time()

                if order.is_maker:
                    self.metrics.maker_orders += 1
                else:
                    self.metrics.taker_orders += 1

                logger.info(
                    f"Trade executed: {side.upper()} {count}x @ {price:.2f} "
                    f"on {ticker} (EV={signal.get('ev', 0):.3f}, "
                    f"maker={order.is_maker})"
                )

                return True

        except Exception as e:
            logger.error(f"Trade execution failed: {e}")

        return False

    def _log_status(self) -> None:
        """Log current status."""
        risk_status = self.risk_manager.get_risk_status()
        position_stats = self.position_manager.get_stats()
        order_stats = self.order_manager.get_stats()

        self.metrics.uptime_seconds = time.time() - (self._start_time or time.time())

        logger.info("-" * 40)
        logger.info("KALSHI BOT STATUS")
        logger.info(f"Uptime: {self.metrics.uptime_seconds / 3600:.1f} hours")
        logger.info(f"Iterations: {self.metrics.iterations}")
        logger.info(f"Trades: {self.metrics.trades_executed} (maker: {self.metrics.maker_orders})")
        logger.info(f"Balance: ${risk_status['balance']:.2f}")
        logger.info(f"Daily P&L: ${risk_status['daily_pnl']:.2f} ({risk_status['daily_pnl_pct']:.1f}%)")
        logger.info(f"Open positions: {position_stats['open_positions']}")
        logger.info(f"Win rate: {position_stats['win_rate']:.1f}%")
        logger.info(f"Fees paid: ${order_stats['total_fees']:.2f}")  # Should be ~$0
        logger.info("-" * 40)

    def _cleanup(self) -> None:
        """Cleanup on shutdown."""
        logger.info("Cleaning up...")

        # Stop order monitoring
        self.order_manager.stop_monitoring()

        # Stop WebSocket feeds
        if self.ws_feeds:
            self.ws_feeds.stop()

        # Cancel open orders
        cancelled = self.order_manager.cancel_all_orders()
        logger.info(f"Cancelled {cancelled} open orders")

        # Final stats
        self._log_status()

        logger.info("Shutdown complete")

    def get_status(self) -> Dict[str, Any]:
        """
        Get current bot status.

        Returns:
            Dict with comprehensive status
        """
        return {
            "running": self._running,
            "paused": self._paused,
            "simulation_mode": self.simulation_mode,
            "exchange": "kalshi",
            "metrics": {
                "iterations": self.metrics.iterations,
                "trades_executed": self.metrics.trades_executed,
                "maker_orders": self.metrics.maker_orders,
                "taker_orders": self.metrics.taker_orders,
                "errors": self.metrics.errors,
                "uptime_seconds": self.metrics.uptime_seconds,
                "avg_iteration_ms": self.metrics.avg_iteration_ms,
            },
            "risk": self.risk_manager.get_risk_status(),
            "positions": self.position_manager.get_stats(),
            "orders": self.order_manager.get_stats(),
            "strategies": [s.name for s in self._strategies],
        }


def create_kalshi_trading_loop(config_path: str = "config/config.yaml") -> KalshiTradingLoop:
    """
    Factory function to create and configure a Kalshi trading loop.

    Args:
        config_path: Path to configuration file

    Returns:
        Configured KalshiTradingLoop instance
    """
    loop = KalshiTradingLoop(config_path=config_path)

    # Import and register strategies based on config
    config = load_config(config_path)
    strategies_config = config.get("strategies", {})

    # Register Kalshi-compatible strategies
    if strategies_config.get("spike_reversion", {}).get("enabled", False):
        from src.strategies.kalshi_spike_reversion import KalshiSpikeReversionStrategy
        loop.register_strategy(KalshiSpikeReversionStrategy(
            kalshi=loop.kalshi,
            price_feeds=loop.price_feeds,
            ws_feeds=loop.ws_feeds,
            config=strategies_config.get("spike_reversion", {}),
        ))

    if strategies_config.get("btc_15m_ta", {}).get("enabled", False):
        from src.strategies.kalshi_crypto_ta import KalshiCryptoTAStrategy
        loop.register_strategy(KalshiCryptoTAStrategy(
            kalshi=loop.kalshi,
            price_feeds=loop.price_feeds,
            ws_feeds=loop.ws_feeds,
            config=strategies_config.get("btc_15m_ta", {}),
        ))

    if strategies_config.get("arbitrage", {}).get("enabled", False):
        from src.strategies.kalshi_arbitrage import KalshiArbitrageStrategy
        loop.register_strategy(KalshiArbitrageStrategy(
            kalshi=loop.kalshi,
            config=strategies_config.get("arbitrage", {}),
        ))

    if strategies_config.get("market_making", {}).get("enabled", False):
        from src.strategies.kalshi_market_maker import KalshiMarketMakerStrategy
        loop.register_strategy(KalshiMarketMakerStrategy(
            kalshi=loop.kalshi,
            price_feeds=loop.price_feeds,
            config=strategies_config.get("market_making", {}),
        ))

    return loop
