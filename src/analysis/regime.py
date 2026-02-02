"""
Market Regime Detection Module

Identifies the current market regime to adjust trading behavior:
- TREND_UP: Strong upward trend - follow momentum
- TREND_DOWN: Strong downward trend - follow momentum
- RANGE: Sideways oscillation - fade extremes
- CHOP: Low volume/indecision - reduce position size or avoid

Different regimes require different strategies:
- Trending: Go with the flow, larger positions
- Ranging: Mean reversion, tighter stops
- Choppy: Reduce size or sit out

Inspired by PolymarketBTC15mAssistant's regime detection.
"""

from typing import Optional, Dict, Any
from dataclasses import dataclass
from enum import Enum

from src.analysis.indicators import VWAPResult
from src.utils.logger import get_logger

logger = get_logger(__name__)


class MarketRegime(Enum):
    """Market regime classification."""
    TREND_UP = "trend_up"
    TREND_DOWN = "trend_down"
    RANGE = "range"
    CHOP = "chop"
    UNKNOWN = "unknown"


@dataclass
class RegimeResult:
    """Result of regime detection."""
    regime: MarketRegime
    reason: str
    confidence: float  # 0-1
    recommended_position_multiplier: float  # Scale positions by this
    recommended_strategy: str  # "momentum", "mean_reversion", "avoid"


class RegimeDetector:
    """
    Detects current market regime for strategy adaptation.

    Uses VWAP relationship, volume, and price action to determine
    whether the market is trending, ranging, or choppy.
    """

    # Thresholds
    LOW_VOLUME_RATIO = 0.6  # Below this is "low volume"
    FLAT_DISTANCE_PCT = 0.1  # Within this % of VWAP is "flat"
    FREQUENT_CROSS_COUNT = 3  # This many crosses = ranging

    def __init__(self):
        """Initialize regime detector."""
        self._regime_history: list = []
        logger.debug("RegimeDetector initialized")

    def detect_regime(
        self,
        price: Optional[float] = None,
        vwap: Optional[VWAPResult] = None,
        volume_recent: Optional[float] = None,
        volume_avg: Optional[float] = None,
        atr: Optional[float] = None,  # Average True Range
    ) -> RegimeResult:
        """
        Detect current market regime.

        Args:
            price: Current price
            vwap: VWAP result
            volume_recent: Recent volume
            volume_avg: Average volume
            atr: Average True Range (volatility)

        Returns:
            RegimeResult with classification and recommendations
        """
        # Validate inputs
        if price is None or vwap is None:
            return RegimeResult(
                regime=MarketRegime.UNKNOWN,
                reason="missing_inputs",
                confidence=0.0,
                recommended_position_multiplier=0.5,
                recommended_strategy="avoid",
            )

        # =====================================================================
        # Check for CHOP (low volume, no direction)
        # =====================================================================
        is_low_volume = False
        if volume_recent is not None and volume_avg is not None and volume_avg > 0:
            is_low_volume = volume_recent < self.LOW_VOLUME_RATIO * volume_avg

        # Price very close to VWAP
        distance_pct = abs(vwap.price_distance_pct)
        is_flat = distance_pct < self.FLAT_DISTANCE_PCT

        if is_low_volume and is_flat:
            return RegimeResult(
                regime=MarketRegime.CHOP,
                reason="low_volume_flat",
                confidence=0.8,
                recommended_position_multiplier=0.25,  # Very small positions
                recommended_strategy="avoid",
            )

        # =====================================================================
        # Check for TREND_UP
        # =====================================================================
        above_vwap = price > vwap.value
        vwap_trending_up = vwap.slope > 0.0001

        if above_vwap and vwap_trending_up:
            # Stronger trend if also far from VWAP
            if distance_pct > 0.3:
                confidence = 0.9
            elif distance_pct > 0.1:
                confidence = 0.75
            else:
                confidence = 0.6

            return RegimeResult(
                regime=MarketRegime.TREND_UP,
                reason="price_above_vwap_slope_up",
                confidence=confidence,
                recommended_position_multiplier=1.0 + (confidence - 0.5),  # Up to 1.4x
                recommended_strategy="momentum",
            )

        # =====================================================================
        # Check for TREND_DOWN
        # =====================================================================
        below_vwap = price < vwap.value
        vwap_trending_down = vwap.slope < -0.0001

        if below_vwap and vwap_trending_down:
            if distance_pct > 0.3:
                confidence = 0.9
            elif distance_pct > 0.1:
                confidence = 0.75
            else:
                confidence = 0.6

            return RegimeResult(
                regime=MarketRegime.TREND_DOWN,
                reason="price_below_vwap_slope_down",
                confidence=confidence,
                recommended_position_multiplier=1.0 + (confidence - 0.5),
                recommended_strategy="momentum",
            )

        # =====================================================================
        # Check for RANGE (frequent VWAP crosses)
        # =====================================================================
        if vwap.cross_count >= self.FREQUENT_CROSS_COUNT:
            return RegimeResult(
                regime=MarketRegime.RANGE,
                reason="frequent_vwap_cross",
                confidence=0.7,
                recommended_position_multiplier=0.75,  # Slightly reduced
                recommended_strategy="mean_reversion",
            )

        # =====================================================================
        # Default to RANGE
        # =====================================================================
        return RegimeResult(
            regime=MarketRegime.RANGE,
            reason="default",
            confidence=0.5,
            recommended_position_multiplier=0.75,
            recommended_strategy="mean_reversion",
        )

    def get_strategy_adjustments(
        self,
        regime: RegimeResult,
    ) -> Dict[str, Any]:
        """
        Get strategy-specific adjustments based on regime.

        Returns configuration adjustments for:
        - Position sizing
        - Stop loss levels
        - Entry thresholds
        - Strategy selection
        """
        adjustments = {
            "position_multiplier": regime.recommended_position_multiplier,
            "strategy": regime.recommended_strategy,
        }

        if regime.regime == MarketRegime.TREND_UP:
            adjustments.update({
                "favor_direction": "up",
                "entry_threshold": 0.03,  # Lower threshold to enter
                "stop_loss_pct": 0.05,  # Wider stops in trends
                "take_profit_pct": 0.10,  # Let profits run
                "use_trailing_stop": True,
                "strategies_enabled": ["momentum", "breakout"],
            })

        elif regime.regime == MarketRegime.TREND_DOWN:
            adjustments.update({
                "favor_direction": "down",
                "entry_threshold": 0.03,
                "stop_loss_pct": 0.05,
                "take_profit_pct": 0.10,
                "use_trailing_stop": True,
                "strategies_enabled": ["momentum", "breakout"],
            })

        elif regime.regime == MarketRegime.RANGE:
            adjustments.update({
                "favor_direction": None,  # No directional bias
                "entry_threshold": 0.05,  # Higher threshold
                "stop_loss_pct": 0.03,  # Tighter stops
                "take_profit_pct": 0.04,  # Take quick profits
                "use_trailing_stop": False,
                "strategies_enabled": ["mean_reversion", "arbitrage"],
            })

        elif regime.regime == MarketRegime.CHOP:
            adjustments.update({
                "favor_direction": None,
                "entry_threshold": 0.10,  # Very high threshold
                "stop_loss_pct": 0.02,  # Very tight stops
                "take_profit_pct": 0.02,  # Quick scalps only
                "use_trailing_stop": False,
                "strategies_enabled": ["arbitrage"],  # Only risk-free trades
                "reduce_frequency": True,  # Trade less often
            })

        else:  # UNKNOWN
            adjustments.update({
                "favor_direction": None,
                "entry_threshold": 0.08,
                "stop_loss_pct": 0.03,
                "take_profit_pct": 0.03,
                "use_trailing_stop": False,
                "strategies_enabled": ["arbitrage"],
            })

        return adjustments

    def should_trade(
        self,
        regime: RegimeResult,
        signal_confidence: float,
        edge: float,
    ) -> bool:
        """
        Determine if we should trade given regime and signal.

        Args:
            regime: Current regime
            signal_confidence: Confidence in the trading signal
            edge: Expected edge of the trade

        Returns:
            True if trade is recommended
        """
        # Never trade in CHOP with low confidence
        if regime.regime == MarketRegime.CHOP:
            return signal_confidence > 0.8 and edge > 0.08

        # In trends, need moderate confidence
        if regime.regime in [MarketRegime.TREND_UP, MarketRegime.TREND_DOWN]:
            return signal_confidence > 0.55 and edge > 0.03

        # In ranges, need higher edge (mean reversion)
        if regime.regime == MarketRegime.RANGE:
            return signal_confidence > 0.6 and edge > 0.05

        # Default: require good confidence and edge
        return signal_confidence > 0.6 and edge > 0.05

    def track_regime(self, regime: RegimeResult) -> None:
        """Track regime for history analysis."""
        self._regime_history.append(regime)
        if len(self._regime_history) > 100:
            self._regime_history = self._regime_history[-100:]

    def get_regime_distribution(self) -> Dict[str, float]:
        """Get distribution of recent regimes."""
        if not self._regime_history:
            return {}

        counts = {}
        for r in self._regime_history:
            key = r.regime.value
            counts[key] = counts.get(key, 0) + 1

        total = len(self._regime_history)
        return {k: v / total for k, v in counts.items()}
