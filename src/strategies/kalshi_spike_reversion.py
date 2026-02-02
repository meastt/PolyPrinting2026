"""
Kalshi Spike Reversion Strategy

Bets on mean reversion after sharp crypto price moves.
Optimized for Kalshi's hourly crypto markets with zero maker fees.

Key features:
- Detects >3% moves in short timeframes
- TA confirmation using RSI/MACD/Regime
- Zero fees on maker orders (Kalshi advantage!)
- Hourly markets ideal for reversion plays
"""

import time
from typing import Dict, List, Any, Optional
from collections import deque
from dataclasses import dataclass

from src.api.kalshi_client import KalshiClient, KalshiMarket
from src.api.price_feeds import PriceFeedAggregator
from src.api.websocket_feeds import WebSocketPriceFeed
from src.analysis.indicators import TechnicalIndicators
from src.analysis.regime import RegimeDetector, MarketRegime
from src.strategies.base_strategy import BaseStrategy, TradingSignal
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SpikeEvent:
    """Detected price spike."""
    asset: str
    direction: str  # "up" or "down"
    magnitude_pct: float
    start_price: float
    spike_price: float
    timestamp: float
    window_seconds: int


class KalshiSpikeReversionStrategy(BaseStrategy):
    """
    Spike Reversion Strategy for Kalshi.

    Detects sharp crypto price moves and bets on mean reversion
    using Kalshi's hourly crypto markets.

    Enhanced with TA confirmation for higher win rate.
    """

    def __init__(
        self,
        kalshi: KalshiClient,
        price_feeds: PriceFeedAggregator,
        ws_feeds: Optional[WebSocketPriceFeed] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize Kalshi Spike Reversion Strategy.

        Args:
            kalshi: Kalshi API client
            price_feeds: Price feed aggregator
            ws_feeds: Optional WebSocket feeds
            config: Strategy configuration
        """
        super().__init__(name="kalshi_spike_reversion", config=config)

        self.kalshi = kalshi
        self.price_feeds = price_feeds
        self.ws_feeds = ws_feeds

        # TA components
        self.ta_indicators = TechnicalIndicators()
        self.regime_detector = RegimeDetector()

        # Configuration
        config = config or {}
        self.threshold_percent = config.get("threshold_percent", 3.0)
        self.lookback_seconds = config.get("lookback_seconds", 60)
        self.cooldown_seconds = config.get("cooldown_seconds", 300)
        self.position_size_percent = config.get("position_size_percent", 1.0)
        self.max_position_size = config.get("max_position_size", 2.0)
        self.min_confidence = config.get("min_confidence", 0.6)
        self.monitored_assets = config.get("monitored_assets", ["BTC", "ETH"])

        # TA configuration
        self.use_ta_confirmation = config.get("use_ta_confirmation", True)
        self.ta_confidence_boost = config.get("ta_confidence_boost", 0.1)
        self.regime_filter = config.get("regime_filter", True)

        # Spike tracking
        self._recent_spikes: List[SpikeEvent] = []
        self._last_spike_time: Dict[str, float] = {}

        # Reversion tracking
        self._reversion_history: deque = deque(maxlen=100)

        # Stats
        self._spikes_detected = 0
        self._signals_triggered = 0
        self._ta_rejections = 0
        self._successful_reversions = 0

        logger.info(
            f"KalshiSpikeReversionStrategy initialized "
            f"(threshold={self.threshold_percent}%, assets={self.monitored_assets})"
        )

    def evaluate(
        self,
        markets: List[KalshiMarket],
        positions: List[Any],
        balance: float,
    ) -> List[Dict[str, Any]]:
        """
        Evaluate for spike reversion opportunities.

        Args:
            markets: List of KalshiMarket objects
            positions: Current positions
            balance: Available balance

        Returns:
            List of trading signals
        """
        self._last_evaluation = time.time()
        signals = []

        # Check each monitored asset for spikes
        for asset in self.monitored_assets:
            spike = self._detect_spike(asset)

            if spike:
                self._spikes_detected += 1
                self._recent_spikes.append(spike)

                # Check cooldown
                if not self._check_cooldown(asset):
                    logger.debug(f"Spike detected but in cooldown: {asset}")
                    continue

                # Find matching Kalshi market
                matching_market = self._find_matching_market(markets, spike)

                if matching_market:
                    # Check if already have position
                    if self._has_position(matching_market.ticker, positions):
                        continue

                    # Create reversion signal
                    signal = self._create_reversion_signal(
                        market=matching_market,
                        spike=spike,
                        balance=balance,
                    )

                    if signal:
                        signals.append(signal)
                        self._last_spike_time[asset] = time.time()

        return signals

    def _detect_spike(self, asset: str) -> Optional[SpikeEvent]:
        """Detect if a price spike has occurred."""
        # Prefer WebSocket data for lower latency
        if self.ws_feeds:
            spike_data = self.ws_feeds.detect_spike(
                symbol=asset,
                threshold_percent=self.threshold_percent,
                window_seconds=self.lookback_seconds,
            )
        else:
            spike_data = self.price_feeds.detect_spike(
                symbol=asset,
                threshold_percent=self.threshold_percent,
                window_seconds=self.lookback_seconds,
            )

        if not spike_data:
            return None

        logger.info(
            f"SPIKE DETECTED: {asset} moved {spike_data['direction']} "
            f"{spike_data['magnitude_pct']:.1f}% in {self.lookback_seconds}s "
            f"(price: ${spike_data['current_price']:,.2f})"
        )

        # Calculate start price
        change_pct = spike_data['magnitude_pct'] / 100
        if spike_data['direction'] == 'up':
            start_price = spike_data['current_price'] / (1 + change_pct)
        else:
            start_price = spike_data['current_price'] / (1 - change_pct)

        return SpikeEvent(
            asset=asset,
            direction=spike_data["direction"],
            magnitude_pct=spike_data["magnitude_pct"],
            start_price=start_price,
            spike_price=spike_data["current_price"],
            timestamp=spike_data["timestamp"],
            window_seconds=self.lookback_seconds,
        )

    def _check_cooldown(self, asset: str) -> bool:
        """Check if past cooldown period."""
        last_spike = self._last_spike_time.get(asset, 0)
        return (time.time() - last_spike) > self.cooldown_seconds

    def _find_matching_market(
        self,
        markets: List[KalshiMarket],
        spike: SpikeEvent,
    ) -> Optional[KalshiMarket]:
        """Find a Kalshi market matching the spike asset."""
        asset_lower = spike.asset.lower()

        for market in markets:
            if not market.is_active:
                continue

            ticker_lower = market.ticker.lower()
            title_lower = market.title.lower()

            # Check if market is about our asset
            if asset_lower in ticker_lower or asset_lower in title_lower:
                # Prefer markets expiring in 15-60 minutes
                time_to_expiry = market.time_to_expiry_seconds / 60
                if 10 < time_to_expiry < 60:
                    return market

            # Handle BTC/Bitcoin and ETH/Ethereum aliases
            if spike.asset == "BTC" and ("btc" in ticker_lower or "bitcoin" in title_lower):
                return market
            if spike.asset == "ETH" and ("eth" in ticker_lower or "ethereum" in title_lower):
                return market

        return None

    def _create_reversion_signal(
        self,
        market: KalshiMarket,
        spike: SpikeEvent,
        balance: float,
    ) -> Optional[Dict[str, Any]]:
        """Create a reversion signal."""
        # Determine reversion direction
        if spike.direction == "up":
            # Price spiked up, bet on reversal (NO on "price up" market)
            outcome = "no"
            reversion_direction = "down"
        else:
            # Price spiked down, bet on reversal (YES on "price up" market)
            outcome = "yes"
            reversion_direction = "up"

        # Base confidence from spike magnitude
        base_confidence = self.min_confidence
        magnitude_bonus = min(0.2, (spike.magnitude_pct - self.threshold_percent) * 0.05)
        confidence = min(0.9, base_confidence + magnitude_bonus)

        # TA confirmation
        regime = None
        if self.use_ta_confirmation:
            ta_result = self._get_ta_confirmation(spike, reversion_direction)

            if ta_result:
                regime = ta_result.get("regime")

                # Regime filter - avoid trading reversions in strong trends
                if self.regime_filter and regime:
                    if regime == MarketRegime.TREND_UP and spike.direction == "up":
                        logger.debug(f"TA rejection: Strong uptrend, spike may continue")
                        self._ta_rejections += 1
                        return None
                    if regime == MarketRegime.TREND_DOWN and spike.direction == "down":
                        logger.debug(f"TA rejection: Strong downtrend, spike may continue")
                        self._ta_rejections += 1
                        return None

                # Apply TA confidence adjustment
                ta_adj = ta_result.get("confidence_adjustment", 0)
                confidence = min(0.95, confidence + ta_adj)

                # RSI confirmation boost
                rsi_value = ta_result.get("rsi_value", 50)
                if spike.direction == "up" and rsi_value > 70:
                    confidence = min(0.95, confidence + 0.05)
                elif spike.direction == "down" and rsi_value < 30:
                    confidence = min(0.95, confidence + 0.05)

        # Calculate edge
        market_price = market.yes_bid if outcome == "yes" else market.no_bid
        if market_price <= 0:
            market_price = market.mid_price

        # Our model probability - we believe reversion is more likely
        fair_value = market_price + 0.05 + (confidence - 0.6) * 0.05

        edge = fair_value - market_price
        ev = self.calculate_ev(fair_value, market_price, is_maker=True)

        if ev < 0.02:
            logger.debug(f"Spike reversion EV too low: {ev:.3f}")
            return None

        # Position sizing
        max_size = balance * (self.position_size_percent / 100)
        max_size = min(max_size, self.max_position_size)

        # Scale by confidence
        size_multiplier = 0.5 + (confidence - 0.5)
        size = max_size * size_multiplier

        self._signals_triggered += 1

        # Build reason
        reason_parts = [
            f"Spike reversion: {spike.asset} {spike.direction} "
            f"{spike.magnitude_pct:.1f}%, betting on reversal"
        ]
        if regime:
            reason_parts.append(f"regime={regime.value}")

        logger.info(
            f"REVERSION SIGNAL: {outcome.upper()} ${size:.2f} @ {market_price:.4f} "
            f"(spike={spike.direction} {spike.magnitude_pct:.1f}%, confidence={confidence:.2f})"
        )

        return {
            "ticker": market.ticker,
            "market_id": market.ticker,
            "side": outcome,
            "outcome": outcome.upper(),
            "price": market_price,
            "size": size,
            "ev": ev,
            "confidence": confidence,
            "strategy": self.name,
            "reason": " | ".join(reason_parts),
            "urgency": "high",
            "time_horizon_seconds": int(market.time_to_expiry_seconds),
            "spike_data": {
                "asset": spike.asset,
                "direction": spike.direction,
                "magnitude_pct": spike.magnitude_pct,
            },
        }

    def _get_ta_confirmation(
        self,
        spike: SpikeEvent,
        reversion_direction: str,
    ) -> Optional[Dict[str, Any]]:
        """Get TA confirmation for reversion trade."""
        try:
            # Get price history
            if self.ws_feeds:
                history = self.ws_feeds.get_price_history(spike.asset, periods=50)
            else:
                return None  # Need WebSocket for proper TA

            if not history or len(history) < 20:
                return None

            prices = [p["close"] for p in history]
            volumes = [p.get("volume", 0) for p in history]
            highs = [p.get("high", p["close"]) for p in history]
            lows = [p.get("low", p["close"]) for p in history]

            # Calculate RSI
            rsi_result = self.ta_indicators.calculate_rsi(prices, period=14)

            # Calculate MACD
            macd_result = self.ta_indicators.calculate_macd(prices)

            # Detect regime
            regime = self.regime_detector.detect(
                prices=prices,
                highs=highs,
                lows=lows,
                volumes=volumes,
            )

            # Calculate confidence adjustment
            confidence_adj = 0.0

            if rsi_result:
                if rsi_result.is_overbought and reversion_direction == "down":
                    confidence_adj += 0.1
                elif rsi_result.is_oversold and reversion_direction == "up":
                    confidence_adj += 0.1
                elif 40 <= rsi_result.value <= 60:
                    confidence_adj -= 0.05

            if macd_result:
                if macd_result.histogram_rising and reversion_direction == "up":
                    confidence_adj += 0.05
                elif not macd_result.histogram_rising and reversion_direction == "down":
                    confidence_adj += 0.05

            if regime:
                if regime.regime == MarketRegime.RANGE:
                    confidence_adj += 0.1
                elif regime.regime == MarketRegime.CHOP:
                    confidence_adj -= 0.05

            return {
                "rsi_value": rsi_result.value if rsi_result else 50,
                "macd_histogram": macd_result.histogram if macd_result else 0,
                "regime": regime.regime if regime else None,
                "confidence_adjustment": confidence_adj,
            }

        except Exception as e:
            logger.debug(f"TA confirmation failed: {e}")
            return None

    def _has_position(self, ticker: str, positions: List[Any]) -> bool:
        """Check if we have a position."""
        for pos in positions:
            pos_ticker = getattr(pos, "ticker", None) or pos.get("ticker", "")
            if pos_ticker == ticker:
                return True
        return False

    def get_stats(self) -> Dict[str, Any]:
        """Get strategy statistics."""
        stats = super().get_stats()
        stats.update({
            "spikes_detected": self._spikes_detected,
            "signals_triggered": self._signals_triggered,
            "ta_rejections": self._ta_rejections,
            "successful_reversions": self._successful_reversions,
            "monitored_assets": self.monitored_assets,
            "threshold_percent": self.threshold_percent,
        })
        return stats
