"""
Main Trading Loop

The central event loop that orchestrates all trading activities:
- Fetches market data
- Runs strategy calculations
- Executes trades
- Manages risk
- Handles errors and retries

Designed for 24/7 autonomous operation on Oracle Cloud Infrastructure.
"""

import time
import signal
import sys
from typing import Optional, Dict, List, Any
from dataclasses import dataclass
import threading

from src.api.polymarket_client import PolymarketClient
from src.api.price_feeds import PriceFeedAggregator
from src.api.gamma_api import GammaAPIClient
from src.core.risk_manager import RiskManager
from src.core.order_manager import OrderManager
from src.core.position_manager import PositionManager
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


class TradingLoop:
    """
    Main trading loop orchestrator.

    Coordinates all bot activities:
    1. Fetch market data and prices
    2. Run each enabled strategy
    3. Collect and filter trading signals
    4. Execute approved trades
    5. Update positions and risk
    6. Log and report

    Designed for high reliability:
    - Graceful shutdown handling
    - Error recovery with exponential backoff
    - Rate limit compliance
    - Comprehensive logging

    Runs continuously for 24/7 operation on OCI.
    """

    def __init__(
        self,
        config_path: str = "config/config.yaml",
        simulation_mode: Optional[bool] = None,
    ):
        """
        Initialize the trading loop.

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
            f"TradingLoop initialized (simulation={self.simulation_mode}, "
            f"poll_interval={self.poll_interval}s, balance=${self.starting_balance})"
        )

    def _init_components(self) -> None:
        """Initialize all trading components."""
        # API clients
        self.polymarket = PolymarketClient(
            simulation_mode=self.simulation_mode,
        )

        self.price_feeds = PriceFeedAggregator()

        self.gamma_api = GammaAPIClient()

        # Core managers
        self.risk_manager = RiskManager(
            starting_balance=self.starting_balance,
        )

        self.order_manager = OrderManager(
            api_client=self.polymarket,
        )

        self.position_manager = PositionManager(
            api_client=self.polymarket,
        )

        logger.info("All components initialized")

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
        logger.info("STARTING POLYMARKET TRADING BOT")
        logger.info("=" * 50)
        logger.info(f"Mode: {'SIMULATION' if self.simulation_mode else 'LIVE'}")
        logger.info(f"Starting balance: ${self.starting_balance}")
        logger.info(f"Strategies: {[s.name for s in self._strategies]}")
        logger.info("=" * 50)

        self._running = True
        self._start_time = time.time()

        # Initialize API connections
        if not self.polymarket.initialize():
            logger.error("Failed to initialize Polymarket client")
            if not self.simulation_mode:
                return

        # Start background services
        self.order_manager.start_monitoring()
        self.price_feeds.start_continuous_polling(["BTC", "ETH"])

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
        # 1. Fetch market data
        markets = self._fetch_markets()

        # 2. Update positions
        self.position_manager.update_prices()
        self.position_manager.check_resolutions()

        # 3. Update balance
        balance = self._get_balance()
        self.risk_manager.update_balance(balance)

        # 4. Run strategies and collect signals
        signals = []
        for strategy in self._strategies:
            if not self._is_strategy_enabled(strategy.name):
                continue

            try:
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

    def _fetch_markets(self) -> List[Any]:
        """Fetch available markets from Polymarket."""
        try:
            markets = self.polymarket.get_markets(
                category=self.config.get("markets", {}).get("focus_categories", [None])[0],
                active_only=True,
            )
            return markets
        except Exception as e:
            logger.error(f"Failed to fetch markets: {e}")
            return []

    def _get_balance(self) -> float:
        """Get current balance."""
        if self.simulation_mode:
            return self.risk_manager.current_balance
        else:
            return self.polymarket.get_balance()

    def _is_strategy_enabled(self, strategy_name: str) -> bool:
        """Check if a strategy is enabled in config."""
        strategies_config = self.config.get("strategies", {})

        # Map strategy names to config keys
        name_map = {
            "arbitrage": "arbitrage",
            "market_maker": "market_making",
            "spike_reversion": "spike_reversion",
            "copy_trader": "copy_trading",
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
                market_id=signal.get("market_id", ""),
                outcome=signal.get("outcome", ""),
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
        Execute a trading signal.

        Args:
            signal: Trading signal dict

        Returns:
            True if execution successful
        """
        try:
            # Create order
            from src.core.order_manager import OrderType, OrderSide

            order = self.order_manager.create_order(
                market_id=signal["market_id"],
                token_id=signal["token_id"],
                side=OrderSide.BUY,  # We buy the outcome we're betting on
                price=signal.get("price", 0.5),
                size=signal["size"],
                order_type=OrderType.LIMIT,  # Prefer maker orders
                strategy=signal.get("strategy", ""),
                tags={"signal": signal},
            )

            # Submit order
            success = self.order_manager.submit_order(order)

            if success:
                # Record in risk manager
                self.risk_manager.record_trade(
                    market_id=signal["market_id"],
                    outcome=signal["outcome"],
                    size=signal["size"],
                    price=signal.get("price", 0.5),
                )

                # Open position
                self.position_manager.open_position(
                    market_id=signal["market_id"],
                    token_id=signal["token_id"],
                    outcome=signal["outcome"],
                    size=signal["size"],
                    price=signal.get("price", 0.5),
                    strategy=signal.get("strategy", ""),
                )

                self.metrics.trades_executed += 1
                self.metrics.last_trade_time = time.time()

                logger.info(
                    f"Trade executed: {signal['outcome']} ${signal['size']:.2f} "
                    f"@ {signal.get('price', 0.5):.4f} (EV={signal.get('ev', 0):.3f})"
                )

                return True

        except Exception as e:
            logger.error(f"Trade execution failed: {e}")

        return False

    def _log_status(self) -> None:
        """Log current status."""
        risk_status = self.risk_manager.get_risk_status()
        position_stats = self.position_manager.get_stats()
        fill_stats = self.order_manager.get_fill_stats()

        self.metrics.uptime_seconds = time.time() - (self._start_time or time.time())

        logger.info("-" * 40)
        logger.info("STATUS UPDATE")
        logger.info(f"Uptime: {self.metrics.uptime_seconds / 3600:.1f} hours")
        logger.info(f"Iterations: {self.metrics.iterations}")
        logger.info(f"Trades: {self.metrics.trades_executed}")
        logger.info(f"Balance: ${risk_status['balance']:.2f}")
        logger.info(f"Daily P&L: ${risk_status['daily_pnl']:.2f} ({risk_status['daily_pnl_pct']:.1f}%)")
        logger.info(f"Open positions: {position_stats['open_positions']}")
        logger.info(f"Win rate: {position_stats['win_rate']:.1f}%")
        logger.info(f"Net rebates: ${fill_stats['net_fee_impact']:.2f}")
        logger.info("-" * 40)

    def _cleanup(self) -> None:
        """Cleanup on shutdown."""
        logger.info("Cleaning up...")

        # Stop order monitoring
        self.order_manager.stop_monitoring()

        # Cancel open orders
        cancelled = self.order_manager.cancel_all_orders()
        logger.info(f"Cancelled {cancelled} open orders")

        # Save state
        self.position_manager.export_summary()

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
            "metrics": {
                "iterations": self.metrics.iterations,
                "trades_executed": self.metrics.trades_executed,
                "errors": self.metrics.errors,
                "uptime_seconds": self.metrics.uptime_seconds,
                "avg_iteration_ms": self.metrics.avg_iteration_ms,
            },
            "risk": self.risk_manager.get_risk_status(),
            "positions": self.position_manager.get_stats(),
            "orders": self.order_manager.get_fill_stats(),
            "strategies": [s.name for s in self._strategies],
        }


def create_trading_loop(config_path: str = "config/config.yaml") -> TradingLoop:
    """
    Factory function to create and configure a trading loop.

    Args:
        config_path: Path to configuration file

    Returns:
        Configured TradingLoop instance
    """
    loop = TradingLoop(config_path=config_path)

    # Import and register strategies based on config
    config = load_config(config_path)
    strategies_config = config.get("strategies", {})

    if strategies_config.get("arbitrage", {}).get("enabled", False):
        from src.strategies.arbitrage import ArbitrageStrategy
        loop.register_strategy(ArbitrageStrategy(
            polymarket=loop.polymarket,
            config=strategies_config.get("arbitrage", {}),
        ))

    if strategies_config.get("market_making", {}).get("enabled", False):
        from src.strategies.market_maker import MarketMakerStrategy
        loop.register_strategy(MarketMakerStrategy(
            polymarket=loop.polymarket,
            price_feeds=loop.price_feeds,
            config=strategies_config.get("market_making", {}),
        ))

    if strategies_config.get("spike_reversion", {}).get("enabled", False):
        from src.strategies.spike_reversion import SpikeReversionStrategy
        loop.register_strategy(SpikeReversionStrategy(
            polymarket=loop.polymarket,
            price_feeds=loop.price_feeds,
            config=strategies_config.get("spike_reversion", {}),
        ))

    if strategies_config.get("copy_trading", {}).get("enabled", False):
        from src.strategies.copy_trader import CopyTraderStrategy
        loop.register_strategy(CopyTraderStrategy(
            polymarket=loop.polymarket,
            gamma_api=loop.gamma_api,
            config=strategies_config.get("copy_trading", {}),
        ))

    return loop
