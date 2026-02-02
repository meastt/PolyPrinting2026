"""
Volatility Spike Reversion Strategy

Bets on mean reversion after sharp price moves in crypto markets.
When spot prices spike >3-5% in a short window, this strategy
places bets expecting the price to revert toward the mean.

How it works:
1. Monitor spot crypto prices every 1-2 seconds
2. Detect moves >3-5% within a 1-minute window
3. When spike detected, bet on reversal in Polymarket
4. Use maker orders to earn rebates
5. Size positions at 1-2% of balance ($0.50-$1 starting)

Risk considerations:
- Mean reversion is probabilistic, not guaranteed
- Strong trends can continue (not all spikes revert)
- Use cooldown periods to avoid over-trading
- Conservative position sizing (1-2% max)

This strategy works best in:
- Range-bound markets
- After news-driven overreactions
- During low-liquidity periods with exaggerated moves
"""

import time
from typing import Dict, List, Any, Optional
from collections import deque
from dataclasses import dataclass

from src.strategies.base_strategy import BaseStrategy, TradingSignal
from src.analysis.indicators import TechnicalIndicators
from src.analysis.scoring import DirectionalScorer
from src.analysis.regime import RegimeDetector, MarketRegime
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SpikeEvent:
    """Represents a detected price spike."""
    asset: str
    direction: str  # "up" or "down"
    magnitude_pct: float
    start_price: float
    spike_price: float
    timestamp: float
    window_seconds: int


class SpikeReversionStrategy(BaseStrategy):
    """
    Volatility Spike Reversion Strategy.

    Detects sharp price moves and bets on mean reversion.
    Uses conservative sizing to manage risk since reversion
    is probabilistic, not certain.

    This is a medium-risk strategy with potential for
    high returns when market conditions are favorable.
    """

    def __init__(
        self,
        polymarket,  # PolymarketClient
        price_feeds,  # PriceFeedAggregator
        config: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize spike reversion strategy.

        Args:
            polymarket: Polymarket API client
            price_feeds: Price feed aggregator
            config: Strategy configuration
        """
        super().__init__(name="spike_reversion", config=config)

        self.polymarket = polymarket
        self.price_feeds = price_feeds

        # Configuration
        self.threshold_percent = config.get("threshold_percent", 3.0)  # 3% move
        self.lookback_seconds = config.get("lookback_seconds", 60)  # 1 minute
        self.cooldown_seconds = config.get("cooldown_seconds", 300)  # 5 min cooldown
        self.position_size_percent = config.get("position_size_percent", 1.0)  # 1% of balance
        self.max_position_size = config.get("max_position_size", 2.0)  # $2 max
        self.min_confidence = config.get("min_confidence", 0.6)  # 60% confidence
        self.monitored_assets = config.get("monitored_assets", ["BTC", "ETH"])

        # Spike tracking
        self._recent_spikes: List[SpikeEvent] = []
        self._last_spike_time: Dict[str, float] = {}  # Asset -> last spike time

        # Historical reversion tracking
        self._reversion_history: deque = deque(maxlen=100)  # Track success rate

        # Technical Analysis components (PolymarketBTC15mAssistant inspired)
        self.ta_indicators = TechnicalIndicators()
        self.ta_scorer = DirectionalScorer()
        self.regime_detector = RegimeDetector()

        # TA configuration
        self.use_ta_confirmation = config.get("use_ta_confirmation", True)
        self.ta_min_confidence_boost = config.get("ta_confidence_boost", 0.1)
        self.ta_regime_filter = config.get("regime_filter", True)

        # Stats
        self._spikes_detected = 0
        self._signals_triggered = 0
        self._successful_reversions = 0
        self._ta_rejections = 0  # Spikes rejected by TA confirmation

        logger.info(
            f"SpikeReversionStrategy initialized "
            f"(threshold={self.threshold_percent}%, lookback={self.lookback_seconds}s, "
            f"assets={self.monitored_assets}, ta_confirmation={self.use_ta_confirmation})"
        )

    def evaluate(
        self,
        markets: List[Any],
        positions: List[Any],
        balance: float,
    ) -> List[Dict[str, Any]]:
        """
        Evaluate for spike reversion opportunities.

        Args:
            markets: List of Market objects
            positions: Current open positions
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

                # Find matching Polymarket market
                matching_market = self._find_matching_market(markets, spike)

                if matching_market:
                    # Create reversion signal
                    signal = self._create_reversion_signal(
                        market=matching_market,
                        spike=spike,
                        balance=balance,
                        positions=positions,
                    )

                    if signal:
                        signals.append(signal)
                        self._last_spike_time[asset] = time.time()

        return [s.to_dict() for s in signals if s]

    def _detect_spike(self, asset: str) -> Optional[SpikeEvent]:
        """
        Detect if a price spike has occurred.

        Args:
            asset: Asset to check (e.g., "BTC")

        Returns:
            SpikeEvent or None
        """
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

        # Get start price from history
        vol_data = self.price_feeds.get_volatility(asset, self.lookback_seconds)
        start_price = 0
        if vol_data:
            # Rough estimate
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
        """Check if we're past the cooldown period for an asset."""
        last_spike = self._last_spike_time.get(asset, 0)
        return (time.time() - last_spike) > self.cooldown_seconds

    def _find_matching_market(
        self,
        markets: List[Any],
        spike: SpikeEvent,
    ) -> Optional[Any]:
        """
        Find a Polymarket market that matches the spike.

        We're looking for short-term binary markets about the
        asset's price direction.

        Args:
            markets: Available markets
            spike: Detected spike

        Returns:
            Matching market or None
        """
        asset_lower = spike.asset.lower()

        for market in markets:
            if not market.active:
                continue

            question_lower = market.question.lower()

            # Check if market is about our asset
            if asset_lower not in question_lower:
                if spike.asset == "BTC" and "bitcoin" not in question_lower:
                    continue
                elif spike.asset == "ETH" and "ethereum" not in question_lower:
                    continue
                elif spike.asset not in ["BTC", "ETH"]:
                    continue

            # Prefer short-term markets
            # Look for price direction markets

            return market

        return None

    def _create_reversion_signal(
        self,
        market: Any,
        spike: SpikeEvent,
        balance: float,
        positions: List[Any],
    ) -> Optional[TradingSignal]:
        """
        Create a signal betting on mean reversion.

        If price spiked UP, bet on DOWN (NO on "price up" market)
        If price spiked DOWN, bet on UP (YES on "price up" market)

        Enhanced with TA confirmation from PolymarketBTC15mAssistant approach:
        - RSI oversold/overbought confirmation
        - Regime detection (avoid trading in strong trends)
        - MACD histogram divergence check

        Args:
            market: Polymarket market
            spike: Detected spike
            balance: Available balance
            positions: Current positions

        Returns:
            TradingSignal or None
        """
        # Check if already have position
        for pos in positions:
            if pos.market_id == market.condition_id:
                logger.debug(f"Already have position in {market.condition_id[:10]}")
                return None

        # Determine which outcome to bet on
        # Betting against the spike direction (mean reversion)
        if spike.direction == "up":
            # Price went up, bet it comes back down
            # This typically means betting NO on "price up" markets
            outcome = "No"
            reversion_direction = "down"
        else:
            # Price went down, bet it comes back up
            outcome = "Yes"
            reversion_direction = "up"

        # Get token and price
        token_id = market.tokens.get(outcome)
        if not token_id:
            return None

        market_price = market.outcome_prices.get(outcome, 0.5)

        # Base confidence from spike magnitude
        base_confidence = self.min_confidence
        magnitude_bonus = min(0.2, (spike.magnitude_pct - self.threshold_percent) * 0.05)
        confidence = min(0.9, base_confidence + magnitude_bonus)

        # TA-enhanced confidence scoring
        ta_confirmation = None
        regime = None

        if self.use_ta_confirmation:
            ta_confirmation = self._get_ta_confirmation(spike, reversion_direction)

            if ta_confirmation:
                regime = ta_confirmation.get("regime")

                # Regime filter - avoid trading reversions in strong trends
                if self.ta_regime_filter and regime:
                    if regime == MarketRegime.TREND_UP and spike.direction == "up":
                        logger.debug(f"TA rejection: Strong uptrend, spike may continue")
                        self._ta_rejections += 1
                        return None
                    if regime == MarketRegime.TREND_DOWN and spike.direction == "down":
                        logger.debug(f"TA rejection: Strong downtrend, spike may continue")
                        self._ta_rejections += 1
                        return None

                # Adjust confidence based on TA signals
                ta_confidence_adj = ta_confirmation.get("confidence_adjustment", 0)
                confidence = min(0.95, confidence + ta_confidence_adj)

                # RSI confirmation boost
                rsi_value = ta_confirmation.get("rsi_value", 50)
                if spike.direction == "up" and rsi_value > 70:
                    # RSI overbought confirms likely reversion
                    confidence = min(0.95, confidence + 0.05)
                    logger.debug(f"RSI overbought ({rsi_value:.1f}) confirms reversion")
                elif spike.direction == "down" and rsi_value < 30:
                    # RSI oversold confirms likely reversion
                    confidence = min(0.95, confidence + 0.05)
                    logger.debug(f"RSI oversold ({rsi_value:.1f}) confirms reversion")

        # Calculate fair value - we believe true probability is higher
        # because spike will likely revert. Adjust based on TA confidence.
        edge_multiplier = 0.05 + (confidence - 0.6) * 0.05  # 5-7% edge based on confidence
        fair_value = market_price + edge_multiplier

        # Calculate EV
        ev = self.calculate_ev(fair_value, market_price, is_maker=True)

        if ev < 0.02:  # Minimum 2% EV
            logger.debug(f"Spike reversion EV too low: {ev:.3f}")
            return None

        # Calculate position size (conservative, scaled by confidence)
        max_size = balance * (self.position_size_percent / 100)
        max_size = min(max_size, self.max_position_size)

        # Scale size by confidence (higher confidence = larger position)
        size_multiplier = 0.5 + (confidence - 0.5)  # 0.5x to 1.0x based on confidence
        adjusted_size = max_size * size_multiplier

        # Build reason string with TA info
        reason_parts = [
            f"Spike reversion: {spike.asset} {spike.direction} "
            f"{spike.magnitude_pct:.1f}%, betting on reversal"
        ]
        if ta_confirmation:
            if regime:
                reason_parts.append(f"regime={regime.value}")
            if "rsi_value" in ta_confirmation:
                reason_parts.append(f"RSI={ta_confirmation['rsi_value']:.0f}")

        signal = self.create_signal(
            market_id=market.condition_id,
            token_id=token_id,
            outcome=outcome,
            price=market_price,
            ev=ev,
            confidence=confidence,
            reason=" | ".join(reason_parts),
            balance=balance,
            size=adjusted_size,
            urgency="high",
            time_horizon_seconds=900,  # 15 minutes
        )

        self._signals_triggered += 1

        logger.info(
            f"REVERSION SIGNAL: {outcome} ${signal.size:.2f} @ {market_price:.4f} "
            f"(spike={spike.direction} {spike.magnitude_pct:.1f}%, confidence={confidence:.2f}, "
            f"regime={regime.value if regime else 'unknown'})"
        )

        return signal

    def _get_ta_confirmation(
        self,
        spike: SpikeEvent,
        reversion_direction: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get technical analysis confirmation for a reversion trade.

        Uses RSI, MACD, and regime detection to assess whether
        the spike is likely to revert.

        Args:
            spike: Detected spike event
            reversion_direction: Direction we're betting on ("up" or "down")

        Returns:
            Dict with TA confirmation data or None
        """
        try:
            # Get price history for TA calculations
            price_history = self.price_feeds.get_price_history(
                spike.asset,
                periods=50,
                interval_seconds=60,  # 1-minute bars
            )

            if not price_history or len(price_history) < 20:
                logger.debug(f"Insufficient price history for TA ({len(price_history) if price_history else 0} bars)")
                return None

            prices = [p["close"] for p in price_history]
            volumes = [p.get("volume", 0) for p in price_history]

            # Calculate RSI
            rsi_result = self.ta_indicators.calculate_rsi(prices, period=14)

            # Calculate MACD
            macd_result = self.ta_indicators.calculate_macd(prices)

            # Detect regime
            highs = [p.get("high", p["close"]) for p in price_history]
            lows = [p.get("low", p["close"]) for p in price_history]
            regime = self.regime_detector.detect(
                prices=prices,
                highs=highs,
                lows=lows,
                volumes=volumes,
            )

            # Calculate confidence adjustment
            confidence_adj = 0.0

            # RSI extremes increase confidence in reversion
            if rsi_result:
                if rsi_result.is_overbought and reversion_direction == "down":
                    confidence_adj += 0.1
                elif rsi_result.is_oversold and reversion_direction == "up":
                    confidence_adj += 0.1
                # Moderate zones suggest less certain reversion
                elif 40 <= rsi_result.value <= 60:
                    confidence_adj -= 0.05

            # MACD divergence check
            if macd_result:
                if macd_result.histogram_rising and reversion_direction == "up":
                    confidence_adj += 0.05
                elif not macd_result.histogram_rising and reversion_direction == "down":
                    confidence_adj += 0.05

            # Regime considerations
            if regime:
                if regime.regime == MarketRegime.RANGE:
                    # Range-bound markets favor mean reversion
                    confidence_adj += 0.1
                elif regime.regime == MarketRegime.CHOP:
                    # Choppy markets are uncertain
                    confidence_adj -= 0.05

            return {
                "rsi_value": rsi_result.value if rsi_result else 50,
                "rsi_zone": rsi_result.zone if rsi_result else "neutral",
                "macd_histogram": macd_result.histogram if macd_result else 0,
                "macd_rising": macd_result.histogram_rising if macd_result else None,
                "regime": regime.regime if regime else None,
                "regime_strength": regime.strength if regime else 0,
                "confidence_adjustment": confidence_adj,
            }

        except Exception as e:
            logger.warning(f"TA confirmation failed: {e}")
            return None

    def record_outcome(self, market_id: str, was_successful: bool) -> None:
        """
        Record whether a reversion trade was successful.

        Used to track historical success rate and adjust confidence.

        Args:
            market_id: Market that resolved
            was_successful: Whether reversion occurred
        """
        self._reversion_history.append(was_successful)

        if was_successful:
            self._successful_reversions += 1

        logger.info(
            f"Reversion outcome: {'SUCCESS' if was_successful else 'FAIL'} "
            f"(historical rate: {self.get_reversion_rate():.1f}%)"
        )

    def get_reversion_rate(self) -> float:
        """Get historical reversion success rate."""
        if not self._reversion_history:
            return 60.0  # Default assumption

        successes = sum(1 for r in self._reversion_history if r)
        return (successes / len(self._reversion_history)) * 100

    def get_recent_spikes(self, hours: int = 24) -> List[SpikeEvent]:
        """Get spikes detected in the last N hours."""
        cutoff = time.time() - (hours * 3600)
        return [s for s in self._recent_spikes if s.timestamp > cutoff]

    def get_stats(self) -> Dict[str, Any]:
        """Get strategy statistics."""
        stats = super().get_stats()
        stats.update({
            "spikes_detected": self._spikes_detected,
            "signals_triggered": self._signals_triggered,
            "successful_reversions": self._successful_reversions,
            "reversion_rate": self.get_reversion_rate(),
            "monitored_assets": self.monitored_assets,
            "threshold_percent": self.threshold_percent,
            "recent_spikes_24h": len(self.get_recent_spikes(24)),
            "ta_rejections": self._ta_rejections,
            "ta_confirmation_enabled": self.use_ta_confirmation,
        })
        return stats
