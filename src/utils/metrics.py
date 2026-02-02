"""
Prometheus Metrics Exporter

Exposes trading bot metrics for monitoring via Prometheus.
Metrics include balance, P&L, trade counts, and system health.
"""

import time
from typing import Optional
from threading import Thread

try:
    from prometheus_client import (
        Counter,
        Gauge,
        Histogram,
        Info,
        start_http_server,
        REGISTRY,
    )
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    print("WARNING: prometheus_client not installed. Metrics disabled.")

from src.utils.logger import get_logger

logger = get_logger(__name__)


class MetricsExporter:
    """
    Prometheus metrics exporter for the trading bot.

    Exposes metrics on a configurable port for scraping by Prometheus.
    """

    def __init__(
        self,
        port: int = 9090,
        enabled: bool = True,
    ):
        """
        Initialize metrics exporter.

        Args:
            port: Port to expose metrics on
            enabled: Whether to enable metrics
        """
        self.port = port
        self.enabled = enabled and PROMETHEUS_AVAILABLE
        self._server_started = False

        if not self.enabled:
            logger.info("Metrics exporter disabled")
            return

        # Initialize metrics
        self._init_metrics()

        logger.info(f"MetricsExporter initialized (port={port})")

    def _init_metrics(self):
        """Initialize Prometheus metrics."""
        # Bot info
        self.info = Info(
            'polybot_info',
            'Information about the trading bot'
        )

        # Balance metrics
        self.balance = Gauge(
            'polybot_balance_usdc',
            'Current wallet balance in USDC'
        )

        self.starting_balance = Gauge(
            'polybot_starting_balance_usdc',
            'Starting balance in USDC'
        )

        # P&L metrics
        self.pnl_total = Gauge(
            'polybot_pnl_total_usdc',
            'Total P&L in USDC'
        )

        self.pnl_daily = Gauge(
            'polybot_pnl_daily_usdc',
            'Daily P&L in USDC'
        )

        self.pnl_daily_pct = Gauge(
            'polybot_pnl_daily_percent',
            'Daily P&L as percentage'
        )

        # Trade metrics
        self.trades_total = Counter(
            'polybot_trades_total',
            'Total number of trades executed',
            ['strategy', 'outcome']
        )

        self.trades_value = Counter(
            'polybot_trades_value_usdc_total',
            'Total value of trades in USDC',
            ['strategy']
        )

        self.trade_latency = Histogram(
            'polybot_trade_latency_seconds',
            'Trade execution latency',
            buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
        )

        # Win/loss metrics
        self.wins_total = Counter(
            'polybot_wins_total',
            'Total winning trades',
            ['strategy']
        )

        self.losses_total = Counter(
            'polybot_losses_total',
            'Total losing trades',
            ['strategy']
        )

        self.win_rate = Gauge(
            'polybot_win_rate_percent',
            'Current win rate percentage'
        )

        # Position metrics
        self.open_positions = Gauge(
            'polybot_open_positions',
            'Number of open positions'
        )

        self.total_exposure = Gauge(
            'polybot_total_exposure_usdc',
            'Total exposure in USDC'
        )

        # Fee metrics
        self.fees_paid = Counter(
            'polybot_fees_paid_usdc_total',
            'Total fees paid in USDC'
        )

        self.rebates_earned = Counter(
            'polybot_rebates_earned_usdc_total',
            'Total rebates earned in USDC'
        )

        # Order metrics
        self.orders_submitted = Counter(
            'polybot_orders_submitted_total',
            'Total orders submitted',
            ['type']  # 'limit' or 'market'
        )

        self.orders_filled = Counter(
            'polybot_orders_filled_total',
            'Total orders filled'
        )

        self.orders_cancelled = Counter(
            'polybot_orders_cancelled_total',
            'Total orders cancelled'
        )

        # System metrics
        self.loop_iterations = Counter(
            'polybot_loop_iterations_total',
            'Total trading loop iterations'
        )

        self.loop_errors = Counter(
            'polybot_loop_errors_total',
            'Total trading loop errors'
        )

        self.uptime_seconds = Gauge(
            'polybot_uptime_seconds',
            'Bot uptime in seconds'
        )

        self.api_requests = Counter(
            'polybot_api_requests_total',
            'Total API requests made',
            ['endpoint', 'status']
        )

        self.api_latency = Histogram(
            'polybot_api_latency_seconds',
            'API request latency',
            ['endpoint'],
            buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
        )

        # Strategy metrics
        self.strategy_signals = Counter(
            'polybot_strategy_signals_total',
            'Signals generated by strategy',
            ['strategy']
        )

        self.arb_opportunities = Counter(
            'polybot_arb_opportunities_total',
            'Arbitrage opportunities found'
        )

        self.spike_detections = Counter(
            'polybot_spike_detections_total',
            'Volatility spikes detected',
            ['asset', 'direction']
        )

    def start(self):
        """Start the metrics HTTP server."""
        if not self.enabled:
            return

        if self._server_started:
            return

        try:
            start_http_server(self.port)
            self._server_started = True
            logger.info(f"Metrics server started on port {self.port}")
        except Exception as e:
            logger.error(f"Failed to start metrics server: {e}")

    def set_info(self, version: str, mode: str):
        """Set bot info."""
        if self.enabled:
            self.info.info({
                'version': version,
                'mode': mode,
            })

    def update_balance(self, balance: float, starting: Optional[float] = None):
        """Update balance metrics."""
        if self.enabled:
            self.balance.set(balance)
            if starting is not None:
                self.starting_balance.set(starting)

    def update_pnl(self, total: float, daily: float, daily_pct: float):
        """Update P&L metrics."""
        if self.enabled:
            self.pnl_total.set(total)
            self.pnl_daily.set(daily)
            self.pnl_daily_pct.set(daily_pct)

    def record_trade(
        self,
        strategy: str,
        outcome: str,
        value: float,
        is_win: Optional[bool] = None,
        latency: Optional[float] = None,
    ):
        """Record a trade."""
        if self.enabled:
            self.trades_total.labels(strategy=strategy, outcome=outcome).inc()
            self.trades_value.labels(strategy=strategy).inc(value)

            if is_win is not None:
                if is_win:
                    self.wins_total.labels(strategy=strategy).inc()
                else:
                    self.losses_total.labels(strategy=strategy).inc()

            if latency is not None:
                self.trade_latency.observe(latency)

    def update_positions(self, count: int, exposure: float):
        """Update position metrics."""
        if self.enabled:
            self.open_positions.set(count)
            self.total_exposure.set(exposure)

    def update_win_rate(self, rate: float):
        """Update win rate."""
        if self.enabled:
            self.win_rate.set(rate)

    def record_fee(self, fee: float):
        """Record fee paid."""
        if self.enabled:
            self.fees_paid.inc(fee)

    def record_rebate(self, rebate: float):
        """Record rebate earned."""
        if self.enabled:
            self.rebates_earned.inc(rebate)

    def record_order(self, order_type: str, filled: bool = False, cancelled: bool = False):
        """Record order event."""
        if self.enabled:
            self.orders_submitted.labels(type=order_type).inc()
            if filled:
                self.orders_filled.inc()
            if cancelled:
                self.orders_cancelled.inc()

    def record_loop_iteration(self, error: bool = False):
        """Record loop iteration."""
        if self.enabled:
            self.loop_iterations.inc()
            if error:
                self.loop_errors.inc()

    def update_uptime(self, seconds: float):
        """Update uptime."""
        if self.enabled:
            self.uptime_seconds.set(seconds)

    def record_api_request(
        self,
        endpoint: str,
        status: str,
        latency: float,
    ):
        """Record API request."""
        if self.enabled:
            self.api_requests.labels(endpoint=endpoint, status=status).inc()
            self.api_latency.labels(endpoint=endpoint).observe(latency)

    def record_strategy_signal(self, strategy: str):
        """Record strategy signal."""
        if self.enabled:
            self.strategy_signals.labels(strategy=strategy).inc()

    def record_arb_opportunity(self):
        """Record arbitrage opportunity."""
        if self.enabled:
            self.arb_opportunities.inc()

    def record_spike(self, asset: str, direction: str):
        """Record volatility spike."""
        if self.enabled:
            self.spike_detections.labels(asset=asset, direction=direction).inc()


# Global metrics instance
_metrics: Optional[MetricsExporter] = None


def get_metrics() -> MetricsExporter:
    """Get the global metrics instance."""
    global _metrics
    if _metrics is None:
        _metrics = MetricsExporter()
    return _metrics


def init_metrics(port: int = 9090, enabled: bool = True) -> MetricsExporter:
    """Initialize and return the metrics exporter."""
    global _metrics
    _metrics = MetricsExporter(port=port, enabled=enabled)
    return _metrics
