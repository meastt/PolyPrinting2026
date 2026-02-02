"""
Directional Scoring Module

Combines multiple technical indicators into a unified directional
probability score, inspired by PolymarketBTC15mAssistant's scoring system.

The scoring system:
1. Assigns points based on each indicator's signal
2. Normalizes to a 0-1 probability
3. Applies time-awareness decay near market close
4. Provides confidence levels for position sizing
"""

from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass
from enum import Enum

from src.analysis.indicators import (
    TechnicalIndicators,
    RSIResult,
    MACDResult,
    VWAPResult,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


class SignalStrength(Enum):
    """Signal strength classification."""
    STRONG_BULLISH = "strong_bullish"
    BULLISH = "bullish"
    WEAK_BULLISH = "weak_bullish"
    NEUTRAL = "neutral"
    WEAK_BEARISH = "weak_bearish"
    BEARISH = "bearish"
    STRONG_BEARISH = "strong_bearish"


@dataclass
class ScoringResult:
    """Result of directional scoring."""
    # Raw scores
    up_score: float
    down_score: float

    # Probabilities
    raw_up_probability: float  # Before time adjustment
    adjusted_up_probability: float  # After time adjustment
    adjusted_down_probability: float

    # Confidence and strength
    confidence: float  # 0-1, how confident in the signal
    signal_strength: SignalStrength
    edge_magnitude: float  # How far from 50/50

    # Component scores for transparency
    component_scores: Dict[str, Dict[str, float]]

    # Recommendation
    direction: str  # "up", "down", or "neutral"
    should_trade: bool


class DirectionalScorer:
    """
    Combines technical indicators into directional probability.

    Scoring weights (inspired by PolymarketBTC15mAssistant):
    - VWAP position: 2 points
    - VWAP slope: 2 points
    - RSI level + slope: 2 points
    - MACD histogram: 2 points + 1 for line
    - Heiken Ashi color: 1 point
    - Failed VWAP reclaim: -3 points

    The system normalizes scores to probability and applies
    time-awareness near market close.
    """

    # Scoring weights
    WEIGHT_VWAP_POSITION = 2
    WEIGHT_VWAP_SLOPE = 2
    WEIGHT_RSI = 2
    WEIGHT_MACD_HISTOGRAM = 2
    WEIGHT_MACD_LINE = 1
    WEIGHT_HEIKEN_ASHI = 1
    WEIGHT_MOMENTUM = 1
    PENALTY_FAILED_RECLAIM = 3

    def __init__(
        self,
        indicators: Optional[TechnicalIndicators] = None,
        min_confidence_to_trade: float = 0.55,
    ):
        """
        Initialize directional scorer.

        Args:
            indicators: TechnicalIndicators instance
            min_confidence_to_trade: Minimum confidence to recommend trade
        """
        self.indicators = indicators or TechnicalIndicators()
        self.min_confidence_to_trade = min_confidence_to_trade

        # Track previous values for reclaim detection
        self._prev_above_vwap: Optional[bool] = None
        self._vwap_cross_history: list = []

        logger.debug("DirectionalScorer initialized")

    def score_direction(
        self,
        rsi: Optional[RSIResult] = None,
        macd: Optional[MACDResult] = None,
        vwap: Optional[VWAPResult] = None,
        heiken_ashi_trend: Optional[Tuple[str, int, float]] = None,
        momentum: Optional[Dict[str, Any]] = None,
        current_price: Optional[float] = None,
    ) -> ScoringResult:
        """
        Calculate directional score from indicators.

        Args:
            rsi: RSI result
            macd: MACD result
            vwap: VWAP result
            heiken_ashi_trend: (trend, consecutive, strength)
            momentum: Momentum dict
            current_price: Current price

        Returns:
            ScoringResult with probabilities and recommendation
        """
        up_score = 0.0
        down_score = 0.0
        component_scores = {}

        # =====================================================================
        # VWAP Scoring
        # =====================================================================
        if vwap is not None and current_price is not None:
            vwap_scores = {"position": 0, "slope": 0}

            # Price vs VWAP position
            if current_price > vwap.value:
                up_score += self.WEIGHT_VWAP_POSITION
                vwap_scores["position"] = self.WEIGHT_VWAP_POSITION
            else:
                down_score += self.WEIGHT_VWAP_POSITION
                vwap_scores["position"] = -self.WEIGHT_VWAP_POSITION

            # VWAP slope
            if vwap.slope > 0.0001:  # Upward slope
                up_score += self.WEIGHT_VWAP_SLOPE
                vwap_scores["slope"] = self.WEIGHT_VWAP_SLOPE
            elif vwap.slope < -0.0001:  # Downward slope
                down_score += self.WEIGHT_VWAP_SLOPE
                vwap_scores["slope"] = -self.WEIGHT_VWAP_SLOPE

            # Check for failed VWAP reclaim (bearish signal)
            above_vwap = current_price > vwap.value
            if self._prev_above_vwap is not None:
                if self._prev_above_vwap and not above_vwap:
                    # Price dropped back below VWAP - failed reclaim
                    down_score += self.PENALTY_FAILED_RECLAIM
                    vwap_scores["failed_reclaim"] = -self.PENALTY_FAILED_RECLAIM

            self._prev_above_vwap = above_vwap
            component_scores["vwap"] = vwap_scores

        # =====================================================================
        # RSI Scoring
        # =====================================================================
        if rsi is not None:
            rsi_scores = {"level": 0, "slope": 0}

            # RSI level + slope combination
            if rsi.value > 55 and rsi.slope > 0:
                up_score += self.WEIGHT_RSI
                rsi_scores["level"] = self.WEIGHT_RSI
            elif rsi.value < 45 and rsi.slope < 0:
                down_score += self.WEIGHT_RSI
                rsi_scores["level"] = -self.WEIGHT_RSI

            # Overbought/oversold extremes (potential reversal)
            if rsi.is_overbought and rsi.slope < 0:
                down_score += 1  # Potential reversal down
                rsi_scores["reversal"] = -1
            elif rsi.is_oversold and rsi.slope > 0:
                up_score += 1  # Potential reversal up
                rsi_scores["reversal"] = 1

            component_scores["rsi"] = rsi_scores

        # =====================================================================
        # MACD Scoring
        # =====================================================================
        if macd is not None:
            macd_scores = {"histogram": 0, "line": 0}

            # Histogram direction and expansion
            if macd.is_bullish and macd.is_expanding:
                up_score += self.WEIGHT_MACD_HISTOGRAM
                macd_scores["histogram"] = self.WEIGHT_MACD_HISTOGRAM
            elif not macd.is_bullish and macd.is_expanding:
                down_score += self.WEIGHT_MACD_HISTOGRAM
                macd_scores["histogram"] = -self.WEIGHT_MACD_HISTOGRAM

            # MACD line position
            if macd.macd_line > 0:
                up_score += self.WEIGHT_MACD_LINE
                macd_scores["line"] = self.WEIGHT_MACD_LINE
            elif macd.macd_line < 0:
                down_score += self.WEIGHT_MACD_LINE
                macd_scores["line"] = -self.WEIGHT_MACD_LINE

            component_scores["macd"] = macd_scores

        # =====================================================================
        # Heiken Ashi Scoring
        # =====================================================================
        if heiken_ashi_trend is not None:
            trend, consecutive, strength = heiken_ashi_trend
            ha_scores = {"trend": 0}

            if trend == "bullish" and consecutive >= 2:
                up_score += self.WEIGHT_HEIKEN_ASHI
                ha_scores["trend"] = self.WEIGHT_HEIKEN_ASHI
            elif trend == "bearish" and consecutive >= 2:
                down_score += self.WEIGHT_HEIKEN_ASHI
                ha_scores["trend"] = -self.WEIGHT_HEIKEN_ASHI

            component_scores["heiken_ashi"] = ha_scores

        # =====================================================================
        # Momentum Scoring
        # =====================================================================
        if momentum is not None:
            mom_scores = {"score": 0}

            if momentum.get("direction") == "bullish":
                up_score += self.WEIGHT_MOMENTUM
                mom_scores["score"] = self.WEIGHT_MOMENTUM
            elif momentum.get("direction") == "bearish":
                down_score += self.WEIGHT_MOMENTUM
                mom_scores["score"] = -self.WEIGHT_MOMENTUM

            component_scores["momentum"] = mom_scores

        # =====================================================================
        # Calculate Probabilities
        # =====================================================================
        total_score = up_score + down_score

        if total_score > 0:
            raw_up_probability = up_score / total_score
        else:
            raw_up_probability = 0.5  # Neutral

        raw_down_probability = 1 - raw_up_probability

        # Edge magnitude (how far from 50/50)
        edge_magnitude = abs(raw_up_probability - 0.5)

        # Confidence based on indicator agreement
        confidence = self._calculate_confidence(
            component_scores,
            raw_up_probability,
        )

        # Determine signal strength
        signal_strength = self._classify_signal_strength(
            raw_up_probability,
            confidence,
        )

        # Determine direction and trade recommendation
        if raw_up_probability > 0.55:
            direction = "up"
        elif raw_up_probability < 0.45:
            direction = "down"
        else:
            direction = "neutral"

        should_trade = (
            confidence >= self.min_confidence_to_trade and
            edge_magnitude >= 0.05 and
            direction != "neutral"
        )

        return ScoringResult(
            up_score=up_score,
            down_score=down_score,
            raw_up_probability=raw_up_probability,
            adjusted_up_probability=raw_up_probability,  # Will be adjusted by time
            adjusted_down_probability=raw_down_probability,
            confidence=confidence,
            signal_strength=signal_strength,
            edge_magnitude=edge_magnitude,
            component_scores=component_scores,
            direction=direction,
            should_trade=should_trade,
        )

    def apply_time_awareness(
        self,
        result: ScoringResult,
        remaining_minutes: float,
        window_minutes: float = 15,
    ) -> ScoringResult:
        """
        Adjust probabilities based on time until market close.

        As time runs out, predictions converge toward neutral (0.5)
        because there's less time for predicted moves to occur.

        Args:
            result: Original scoring result
            remaining_minutes: Minutes until market closes
            window_minutes: Total market window (default 15 min)

        Returns:
            Updated ScoringResult with time-adjusted probabilities
        """
        if remaining_minutes <= 0 or window_minutes <= 0:
            # Market closed or invalid - return neutral
            result.adjusted_up_probability = 0.5
            result.adjusted_down_probability = 0.5
            result.should_trade = False
            return result

        # Time decay factor (1.0 at start, 0.0 at close)
        time_decay = remaining_minutes / window_minutes
        time_decay = max(0, min(1, time_decay))

        # Apply decay: converge toward 0.5 as time runs out
        raw_edge = result.raw_up_probability - 0.5
        adjusted_edge = raw_edge * time_decay

        result.adjusted_up_probability = 0.5 + adjusted_edge
        result.adjusted_down_probability = 1 - result.adjusted_up_probability

        # Clamp to valid range
        result.adjusted_up_probability = max(0, min(1, result.adjusted_up_probability))
        result.adjusted_down_probability = max(0, min(1, result.adjusted_down_probability))

        # Reduce confidence near close
        result.confidence *= time_decay

        # Update trade recommendation with time factor
        result.should_trade = (
            result.should_trade and
            remaining_minutes >= 2 and  # At least 2 minutes remaining
            time_decay >= 0.3  # At least 30% of time remaining
        )

        return result

    def _calculate_confidence(
        self,
        component_scores: Dict[str, Dict[str, float]],
        probability: float,
    ) -> float:
        """
        Calculate confidence based on indicator agreement.

        High confidence when multiple indicators agree.
        Low confidence when indicators conflict.
        """
        if not component_scores:
            return 0.5

        # Count agreeing vs disagreeing indicators
        agreeing = 0
        disagreeing = 0
        total = 0

        direction = 1 if probability > 0.5 else -1

        for component, scores in component_scores.items():
            for metric, value in scores.items():
                if value != 0:
                    total += 1
                    if (value > 0 and direction > 0) or (value < 0 and direction < 0):
                        agreeing += 1
                    else:
                        disagreeing += 1

        if total == 0:
            return 0.5

        # Agreement ratio
        agreement_ratio = agreeing / total

        # Edge magnitude boost
        edge_boost = min(0.2, abs(probability - 0.5))

        confidence = 0.5 + (agreement_ratio - 0.5) * 0.8 + edge_boost

        return max(0.3, min(0.95, confidence))

    def _classify_signal_strength(
        self,
        probability: float,
        confidence: float,
    ) -> SignalStrength:
        """Classify signal strength based on probability and confidence."""
        combined_score = (probability - 0.5) * confidence

        if combined_score > 0.2:
            return SignalStrength.STRONG_BULLISH
        elif combined_score > 0.1:
            return SignalStrength.BULLISH
        elif combined_score > 0.03:
            return SignalStrength.WEAK_BULLISH
        elif combined_score > -0.03:
            return SignalStrength.NEUTRAL
        elif combined_score > -0.1:
            return SignalStrength.WEAK_BEARISH
        elif combined_score > -0.2:
            return SignalStrength.BEARISH
        else:
            return SignalStrength.STRONG_BEARISH

    def get_full_analysis(
        self,
        current_price: float,
        remaining_minutes: float = 15,
    ) -> ScoringResult:
        """
        Run full analysis pipeline with all indicators.

        Args:
            current_price: Current market price
            remaining_minutes: Minutes until market close

        Returns:
            Complete ScoringResult with all adjustments
        """
        # Get all indicator values
        all_indicators = self.indicators.get_all_indicators()

        # Score direction
        result = self.score_direction(
            rsi=all_indicators.get("rsi"),
            macd=all_indicators.get("macd"),
            vwap=all_indicators.get("vwap"),
            heiken_ashi_trend=all_indicators.get("heiken_ashi_trend"),
            momentum=all_indicators.get("momentum"),
            current_price=current_price,
        )

        # Apply time awareness
        result = self.apply_time_awareness(result, remaining_minutes)

        return result
