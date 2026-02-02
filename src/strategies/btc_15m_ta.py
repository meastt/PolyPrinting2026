"""
BTC 15-Minute Technical Analysis Strategy

A specialized strategy for Polymarket's Bitcoin 15-minute directional
markets, combining professional-grade technical analysis with automated
execution.

Inspired by PolymarketBTC15mAssistant's indicator suite but enhanced with:
- Automated order execution (not just signals)
- Risk-managed position sizing
- Maker order preference for rebates
- Regime-adaptive behavior
- Time-aware edge calculation

This strategy focuses specifically on the short-term BTC up/down
markets that resolve every 15 minutes, using:
- RSI with slope analysis
- MACD histogram momentum
- VWAP position and slope
- Heiken Ashi trend confirmation
- Multi-timeframe momentum
- Market regime detection
- Phase-based edge thresholds

The combination of multiple indicators with regime awareness aims
to filter out low-probability trades and only act on high-conviction
signals with positive edge.
"""

import time
from typing import Dict, List, Any, Optional
from dataclasses import dataclass

from src.strategies.base_strategy import BaseStrategy, TradingSignal
from src.analysis.indicators import TechnicalIndicators, Candle
from src.analysis.scoring import DirectionalScorer, ScoringResult, SignalStrength
from src.analysis.regime import RegimeDetector, MarketRegime, RegimeResult
from src.analysis.edge import EdgeCalculator, EdgeResult
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class BTC15mOpportunity:
    """Represents a trading opportunity in a BTC 15m market."""
    market_id: str
    market_question: str
    yes_token: str
    no_token: str
    yes_price: float
    no_price: float
    remaining_minutes: float
    scoring: ScoringResult
    regime: RegimeResult
    edge: EdgeResult
    recommended_action: str
    recommended_side: str
    recommended_size: float
    timestamp: float


class BTC15mTAStrategy(BaseStrategy):
    """
    Technical Analysis Strategy for BTC 15-Minute Markets.

    Combines multiple indicators into a unified prediction, then
    compares against market prices to find positive-edge opportunities.

    Key features:
    - Multi-indicator scoring (RSI, MACD, VWAP, Heiken Ashi)
    - Regime-adaptive trading (trend vs range vs chop)
    - Phase-based edge thresholds (higher near close)
    - Time-aware probability decay
    - Automated maker order execution

    This is designed as a higher-frequency strategy for the
    15-minute crypto markets, with the goal of compounding
    small edges over many trades.
    """

    def __init__(
        self,
        polymarket,  # PolymarketClient
        price_feeds,  # PriceFeedAggregator
        config: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize BTC 15m TA strategy.

        Args:
            polymarket: Polymarket API client
            price_feeds: Price feed aggregator for TA data
            config: Strategy configuration
        """
        super().__init__(name="btc_15m_ta", config=config)

        self.polymarket = polymarket
        self.price_feeds = price_feeds

        # Configuration
        self.min_edge = config.get("min_edge", 0.05)  # 5% minimum edge
        self.max_position_size = config.get("max_position_size", 5.0)  # $5 max
        self.min_position_size = config.get("min_position_size", 0.50)  # $0.50 min
        self.min_remaining_minutes = config.get("min_remaining_minutes", 2)  # Don't trade < 2 min
        self.confidence_threshold = config.get("confidence_threshold", 0.55)

        # Technical analysis components
        self.indicators = TechnicalIndicators(
            rsi_period=14,
            macd_fast=12,
            macd_slow=26,
            macd_signal=9,
        )
        self.scorer = DirectionalScorer(
            indicators=self.indicators,
            min_confidence_to_trade=self.confidence_threshold,
        )
        self.regime_detector = RegimeDetector()
        self.edge_calculator = EdgeCalculator(use_maker_orders=True)

        # Market tracking
        self._active_markets: Dict[str, Dict[str, Any]] = {}
        self._last_candle_time: float = 0
        self._candle_interval = 60  # 1-minute candles

        # Performance tracking
        self._opportunities_found = 0
        self._signals_generated = 0
        self._trades_by_regime: Dict[str, int] = {}

        logger.info(
            f"BTC15mTAStrategy initialized "
            f"(min_edge={self.min_edge*100}%, max_size=${self.max_position_size})"
        )

    def evaluate(
        self,
        markets: List[Any],
        positions: List[Any],
        balance: float,
    ) -> List[Dict[str, Any]]:
        """
        Evaluate BTC 15-minute markets for trading opportunities.

        Args:
            markets: List of Market objects
            positions: Current open positions
            balance: Available balance

        Returns:
            List of trading signals
        """
        self._last_evaluation = time.time()
        signals = []

        # Update price data for indicators
        self._update_price_data()

        # Filter to BTC 15m markets
        btc_markets = self._filter_btc_15m_markets(markets)

        for market in btc_markets:
            opportunity = self._analyze_market(market, balance, positions)

            if opportunity and opportunity.recommended_action == "trade":
                self._opportunities_found += 1

                signal = self._create_trade_signal(opportunity, balance)
                if signal:
                    signals.append(signal)

        return [s.to_dict() for s in signals]

    def _filter_btc_15m_markets(self, markets: List[Any]) -> List[Any]:
        """Filter to BTC 15-minute directional markets."""
        filtered = []

        for market in markets:
            if not market.active:
                continue

            question_lower = market.question.lower()

            # Must be BTC related
            if "btc" not in question_lower and "bitcoin" not in question_lower:
                continue

            # Must be directional (up/down)
            if not any(word in question_lower for word in ["up", "down", "higher", "lower", "above", "below"]):
                continue

            # Must have sufficient liquidity
            if market.liquidity < 100:
                continue

            filtered.append(market)

        return filtered

    def _update_price_data(self) -> None:
        """Update price data for technical indicators."""
        current_time = time.time()

        # Only update once per candle interval
        if current_time - self._last_candle_time < self._candle_interval:
            return

        try:
            # Get BTC price
            btc_price = self.price_feeds.get_price("BTC")
            if not btc_price:
                return

            # Create candle (simplified - using current price for all OHLC)
            # In production, you'd want proper candle data
            candle = Candle(
                timestamp=current_time,
                open=btc_price.price,
                high=btc_price.price * 1.001,  # Estimate
                low=btc_price.price * 0.999,
                close=btc_price.price,
                volume=1000,  # Placeholder
            )

            self.indicators.add_candle(candle)
            self._last_candle_time = current_time

        except Exception as e:
            logger.debug(f"Price update error: {e}")

    def _analyze_market(
        self,
        market: Any,
        balance: float,
        positions: List[Any],
    ) -> Optional[BTC15mOpportunity]:
        """
        Perform full analysis on a market.

        Args:
            market: Market to analyze
            balance: Available balance
            positions: Current positions

        Returns:
            BTC15mOpportunity or None
        """
        try:
            # Check if already have position
            for pos in positions:
                if pos.market_id == market.condition_id:
                    return None

            # Get market prices
            yes_price = market.outcome_prices.get("Yes", 0.5)
            no_price = market.outcome_prices.get("No", 0.5)

            # Estimate remaining time (would need actual end time from market)
            # For now, assume markets have varying time remaining
            remaining_minutes = self._estimate_remaining_time(market)

            if remaining_minutes < self.min_remaining_minutes:
                return None

            # Get current BTC price
            btc_price_data = self.price_feeds.get_price("BTC")
            if not btc_price_data:
                return None

            current_price = btc_price_data.price

            # Run scoring analysis
            scoring = self.scorer.get_full_analysis(
                current_price=current_price,
                remaining_minutes=remaining_minutes,
            )

            # Detect market regime
            vwap = self.indicators.compute_vwap()
            regime = self.regime_detector.detect_regime(
                price=current_price,
                vwap=vwap,
            )

            # Calculate edge vs market prices
            edge = self.edge_calculator.compute_edge(
                market_yes_price=yes_price,
                market_no_price=no_price,
                model_up_prob=scoring.adjusted_up_probability,
                remaining_minutes=remaining_minutes,
            )

            # Make trade decision
            decision = self.edge_calculator.decide(
                edge_result=edge,
                signal_confidence=scoring.confidence,
                regime_allows=self.regime_detector.should_trade(
                    regime, scoring.confidence, edge.best_edge
                ),
            )

            # Calculate recommended size
            if decision["action"] == "trade":
                base_size = self._calculate_position_size(
                    edge=edge.best_edge,
                    confidence=scoring.confidence,
                    balance=balance,
                    regime=regime,
                )
            else:
                base_size = 0

            return BTC15mOpportunity(
                market_id=market.condition_id,
                market_question=market.question,
                yes_token=market.tokens.get("Yes", ""),
                no_token=market.tokens.get("No", ""),
                yes_price=yes_price,
                no_price=no_price,
                remaining_minutes=remaining_minutes,
                scoring=scoring,
                regime=regime,
                edge=edge,
                recommended_action=decision["action"],
                recommended_side=decision.get("side", "none"),
                recommended_size=base_size,
                timestamp=time.time(),
            )

        except Exception as e:
            logger.error(f"Market analysis error: {e}")
            return None

    def _estimate_remaining_time(self, market: Any) -> float:
        """
        Estimate remaining time until market closes.

        In production, this should use actual market end time.
        """
        # Placeholder - assume 7.5 minutes average remaining
        # Real implementation would parse market end time
        import random
        return random.uniform(3, 14)

    def _calculate_position_size(
        self,
        edge: float,
        confidence: float,
        balance: float,
        regime: RegimeResult,
    ) -> float:
        """
        Calculate position size based on edge, confidence, and regime.

        Uses Kelly-inspired sizing with regime adjustment.
        """
        # Base Kelly fraction (conservative)
        kelly_fraction = 0.15  # 15% of Kelly

        # Raw size from edge
        raw_size = balance * edge * kelly_fraction * confidence

        # Apply regime multiplier
        raw_size *= regime.recommended_position_multiplier

        # Apply limits
        size = max(self.min_position_size, min(raw_size, self.max_position_size))

        # Don't exceed 2% of balance
        max_by_balance = balance * 0.02
        size = min(size, max_by_balance)

        return round(size, 2)

    def _create_trade_signal(
        self,
        opportunity: BTC15mOpportunity,
        balance: float,
    ) -> Optional[TradingSignal]:
        """Create trading signal from opportunity."""
        if opportunity.recommended_size < self.min_position_size:
            return None

        # Determine token and price based on side
        if opportunity.recommended_side == "up":
            outcome = "Yes"
            token_id = opportunity.yes_token
            price = opportunity.yes_price
        else:
            outcome = "No"
            token_id = opportunity.no_token
            price = opportunity.no_price

        if not token_id:
            return None

        # Build reason string
        reason_parts = [
            f"TA: {opportunity.scoring.signal_strength.value}",
            f"Edge: {opportunity.edge.edge_pct:.1f}%",
            f"Regime: {opportunity.regime.regime.value}",
            f"Conf: {opportunity.scoring.confidence:.2f}",
        ]
        reason = " | ".join(reason_parts)

        signal = self.create_signal(
            market_id=opportunity.market_id,
            token_id=token_id,
            outcome=outcome,
            price=price,
            ev=opportunity.edge.best_edge,
            confidence=opportunity.scoring.confidence,
            reason=reason,
            balance=balance,
            size=opportunity.recommended_size,
            urgency="high" if opportunity.remaining_minutes < 5 else "normal",
            time_horizon_seconds=int(opportunity.remaining_minutes * 60),
        )

        self._signals_generated += 1

        # Track by regime
        regime_key = opportunity.regime.regime.value
        self._trades_by_regime[regime_key] = self._trades_by_regime.get(regime_key, 0) + 1

        logger.info(
            f"BTC15m SIGNAL: {outcome} ${opportunity.recommended_size:.2f} @ {price:.4f} | "
            f"{reason} | Time: {opportunity.remaining_minutes:.1f}min"
        )

        return signal

    def get_current_analysis(self) -> Dict[str, Any]:
        """
        Get current technical analysis state for monitoring.

        Returns analysis dict without generating trades.
        """
        btc_price = self.price_feeds.get_price("BTC")
        if not btc_price:
            return {"error": "No BTC price available"}

        all_indicators = self.indicators.get_all_indicators()

        scoring = self.scorer.get_full_analysis(
            current_price=btc_price.price,
            remaining_minutes=15,  # Assume full window
        )

        vwap = self.indicators.compute_vwap()
        regime = self.regime_detector.detect_regime(
            price=btc_price.price,
            vwap=vwap,
        )

        return {
            "timestamp": time.time(),
            "btc_price": btc_price.price,
            "indicators": {
                "rsi": all_indicators.get("rsi").__dict__ if all_indicators.get("rsi") else None,
                "macd": all_indicators.get("macd").__dict__ if all_indicators.get("macd") else None,
                "vwap": vwap.__dict__ if vwap else None,
                "heiken_ashi_trend": all_indicators.get("heiken_ashi_trend"),
                "momentum": all_indicators.get("momentum"),
            },
            "scoring": {
                "up_probability": scoring.adjusted_up_probability,
                "down_probability": scoring.adjusted_down_probability,
                "confidence": scoring.confidence,
                "signal_strength": scoring.signal_strength.value,
                "direction": scoring.direction,
                "should_trade": scoring.should_trade,
            },
            "regime": {
                "regime": regime.regime.value,
                "reason": regime.reason,
                "confidence": regime.confidence,
                "recommended_strategy": regime.recommended_strategy,
            },
        }

    def get_stats(self) -> Dict[str, Any]:
        """Get strategy statistics."""
        stats = super().get_stats()
        stats.update({
            "opportunities_found": self._opportunities_found,
            "signals_generated": self._signals_generated,
            "trades_by_regime": self._trades_by_regime,
            "min_edge": self.min_edge,
            "confidence_threshold": self.confidence_threshold,
        })
        return stats
