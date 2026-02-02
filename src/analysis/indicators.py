"""
Technical Indicators Module

Implements professional-grade technical analysis indicators:
- RSI (Relative Strength Index)
- MACD (Moving Average Convergence Divergence)
- VWAP (Volume Weighted Average Price)
- Heiken Ashi candlesticks
- EMA (Exponential Moving Average)
- Price deltas and momentum

Inspired by PolymarketBTC15mAssistant's indicator suite but enhanced
with additional features for automated trading.
"""

from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass
from collections import deque
import statistics

from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class Candle:
    """OHLCV candlestick data."""
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class HeikenAshiCandle:
    """Heiken Ashi smoothed candlestick."""
    open: float
    high: float
    low: float
    close: float
    is_green: bool  # Close >= Open
    body: float  # Absolute body size
    upper_wick: float
    lower_wick: float


@dataclass
class MACDResult:
    """MACD indicator result."""
    macd_line: float  # Fast EMA - Slow EMA
    signal_line: float  # EMA of MACD line
    histogram: float  # MACD - Signal
    hist_delta: float  # Change in histogram (momentum)
    is_bullish: bool  # Histogram > 0
    is_expanding: bool  # Histogram getting more extreme


@dataclass
class RSIResult:
    """RSI indicator result."""
    value: float  # 0-100
    slope: float  # Rate of change
    is_overbought: bool  # > 70
    is_oversold: bool  # < 30
    zone: str  # "overbought", "oversold", "neutral"


@dataclass
class VWAPResult:
    """VWAP indicator result."""
    value: float
    slope: float  # Direction of VWAP
    price_distance: float  # Price - VWAP
    price_distance_pct: float  # As percentage
    cross_count: int  # Number of VWAP crosses in window


class TechnicalIndicators:
    """
    Comprehensive technical analysis toolkit.

    Calculates multiple indicators and maintains history for
    slope/momentum calculations.
    """

    def __init__(
        self,
        rsi_period: int = 14,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        vwap_lookback: int = 60,  # Minutes
    ):
        """
        Initialize technical indicators calculator.

        Args:
            rsi_period: RSI calculation period
            macd_fast: MACD fast EMA period
            macd_slow: MACD slow EMA period
            macd_signal: MACD signal line period
            vwap_lookback: VWAP calculation window
        """
        self.rsi_period = rsi_period
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal
        self.vwap_lookback = vwap_lookback

        # History storage
        self._candle_history: deque = deque(maxlen=500)
        self._rsi_history: deque = deque(maxlen=50)
        self._vwap_history: deque = deque(maxlen=50)
        self._price_history: deque = deque(maxlen=200)

        logger.debug(
            f"TechnicalIndicators initialized "
            f"(RSI={rsi_period}, MACD={macd_fast}/{macd_slow}/{macd_signal})"
        )

    def add_candle(self, candle: Candle) -> None:
        """Add a new candle to history."""
        self._candle_history.append(candle)
        self._price_history.append(candle.close)

    def add_price(self, price: float, timestamp: float = 0) -> None:
        """Add a price point (for tick data)."""
        self._price_history.append(price)

    def get_closes(self) -> List[float]:
        """Get closing prices from candle history."""
        return [c.close for c in self._candle_history]

    # =========================================================================
    # RSI (Relative Strength Index)
    # =========================================================================

    def compute_rsi(
        self,
        closes: Optional[List[float]] = None,
        period: Optional[int] = None,
    ) -> Optional[RSIResult]:
        """
        Calculate RSI with slope and zone detection.

        RSI measures momentum by comparing recent gains vs losses.
        - > 70: Overbought (potential reversal down)
        - < 30: Oversold (potential reversal up)
        - Slope shows momentum direction

        Args:
            closes: Price series (uses history if not provided)
            period: RSI period (uses default if not provided)

        Returns:
            RSIResult or None if insufficient data
        """
        closes = closes or self.get_closes()
        period = period or self.rsi_period

        if len(closes) < period + 1:
            return None

        # Calculate gains and losses over period
        gains = 0.0
        losses = 0.0

        for i in range(len(closes) - period, len(closes)):
            diff = closes[i] - closes[i - 1]
            if diff > 0:
                gains += diff
            else:
                losses += abs(diff)

        avg_gain = gains / period
        avg_loss = losses / period

        # Calculate RSI
        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))

        rsi = max(0, min(100, rsi))  # Clamp to 0-100

        # Calculate slope from history
        self._rsi_history.append(rsi)
        slope = 0.0
        if len(self._rsi_history) >= 3:
            recent = list(self._rsi_history)[-3:]
            slope = (recent[-1] - recent[0]) / 2

        # Determine zone
        if rsi > 70:
            zone = "overbought"
        elif rsi < 30:
            zone = "oversold"
        else:
            zone = "neutral"

        return RSIResult(
            value=rsi,
            slope=slope,
            is_overbought=rsi > 70,
            is_oversold=rsi < 30,
            zone=zone,
        )

    # =========================================================================
    # MACD (Moving Average Convergence Divergence)
    # =========================================================================

    def _ema(self, values: List[float], period: int) -> Optional[float]:
        """Calculate Exponential Moving Average."""
        if len(values) < period:
            return None

        k = 2 / (period + 1)
        ema = values[0]

        for price in values[1:]:
            ema = price * k + ema * (1 - k)

        return ema

    def compute_macd(
        self,
        closes: Optional[List[float]] = None,
    ) -> Optional[MACDResult]:
        """
        Calculate MACD indicator with histogram momentum.

        MACD shows trend direction and momentum:
        - MACD Line = Fast EMA - Slow EMA
        - Signal Line = EMA of MACD Line
        - Histogram = MACD - Signal (momentum)
        - Histogram expanding = strengthening trend

        Args:
            closes: Price series (uses history if not provided)

        Returns:
            MACDResult or None if insufficient data
        """
        closes = closes or self.get_closes()

        required_len = self.macd_slow + self.macd_signal
        if len(closes) < required_len:
            return None

        # Calculate EMAs
        fast_ema = self._ema(closes, self.macd_fast)
        slow_ema = self._ema(closes, self.macd_slow)

        if fast_ema is None or slow_ema is None:
            return None

        # MACD line
        macd_line = fast_ema - slow_ema

        # Build MACD series for signal calculation
        macd_series = []
        for i in range(self.macd_slow, len(closes) + 1):
            subset = closes[:i]
            fast = self._ema(subset, self.macd_fast)
            slow = self._ema(subset, self.macd_slow)
            if fast and slow:
                macd_series.append(fast - slow)

        # Signal line (EMA of MACD)
        signal_line = self._ema(macd_series, self.macd_signal) if len(macd_series) >= self.macd_signal else macd_line

        # Histogram
        histogram = macd_line - signal_line

        # Histogram delta (momentum of momentum)
        prev_histogram = 0
        if len(macd_series) >= 2:
            prev_signal = self._ema(macd_series[:-1], self.macd_signal) if len(macd_series) > self.macd_signal else macd_series[-2]
            prev_histogram = macd_series[-2] - prev_signal if prev_signal else 0

        hist_delta = histogram - prev_histogram

        # Determine state
        is_bullish = histogram > 0
        is_expanding = abs(histogram) > abs(prev_histogram)

        return MACDResult(
            macd_line=macd_line,
            signal_line=signal_line,
            histogram=histogram,
            hist_delta=hist_delta,
            is_bullish=is_bullish,
            is_expanding=is_expanding,
        )

    # =========================================================================
    # VWAP (Volume Weighted Average Price)
    # =========================================================================

    def compute_vwap(
        self,
        candles: Optional[List[Candle]] = None,
    ) -> Optional[VWAPResult]:
        """
        Calculate VWAP with slope and cross detection.

        VWAP = Sum(Typical Price * Volume) / Sum(Volume)
        Typical Price = (High + Low + Close) / 3

        - Price above VWAP: Bullish
        - Price below VWAP: Bearish
        - Frequent crosses: Ranging/choppy

        Args:
            candles: Candle data (uses history if not provided)

        Returns:
            VWAPResult or None if insufficient data
        """
        candles = candles or list(self._candle_history)

        if not candles:
            return None

        # Calculate VWAP
        cumulative_tp_vol = 0.0
        cumulative_vol = 0.0

        for candle in candles:
            typical_price = (candle.high + candle.low + candle.close) / 3
            cumulative_tp_vol += typical_price * candle.volume
            cumulative_vol += candle.volume

        if cumulative_vol == 0:
            # No volume data - use simple average
            vwap = sum(c.close for c in candles) / len(candles)
        else:
            vwap = cumulative_tp_vol / cumulative_vol

        # Current price
        current_price = candles[-1].close

        # Price distance from VWAP
        price_distance = current_price - vwap
        price_distance_pct = (price_distance / vwap * 100) if vwap > 0 else 0

        # VWAP slope
        self._vwap_history.append(vwap)
        slope = 0.0
        if len(self._vwap_history) >= 3:
            recent = list(self._vwap_history)[-3:]
            slope = (recent[-1] - recent[0]) / recent[0] if recent[0] > 0 else 0

        # Count VWAP crosses
        cross_count = 0
        if len(candles) >= 2:
            prev_above = candles[0].close > vwap
            for candle in candles[1:]:
                curr_above = candle.close > vwap
                if curr_above != prev_above:
                    cross_count += 1
                prev_above = curr_above

        return VWAPResult(
            value=vwap,
            slope=slope,
            price_distance=price_distance,
            price_distance_pct=price_distance_pct,
            cross_count=cross_count,
        )

    # =========================================================================
    # Heiken Ashi
    # =========================================================================

    def compute_heiken_ashi(
        self,
        candles: Optional[List[Candle]] = None,
    ) -> List[HeikenAshiCandle]:
        """
        Convert candlesticks to Heiken Ashi format.

        Heiken Ashi smooths price action for trend identification:
        - Green candles with no lower wick = strong uptrend
        - Red candles with no upper wick = strong downtrend
        - Small bodies with wicks = indecision/reversal

        Args:
            candles: Standard candles (uses history if not provided)

        Returns:
            List of HeikenAshiCandle
        """
        candles = candles or list(self._candle_history)

        if not candles:
            return []

        ha_candles = []

        for i, candle in enumerate(candles):
            # HA Close = (Open + High + Low + Close) / 4
            ha_close = (candle.open + candle.high + candle.low + candle.close) / 4

            # HA Open = (Previous HA Open + Previous HA Close) / 2
            if i == 0:
                ha_open = (candle.open + candle.close) / 2
            else:
                prev = ha_candles[i - 1]
                ha_open = (prev.open + prev.close) / 2

            # HA High = Max(High, HA Open, HA Close)
            ha_high = max(candle.high, ha_open, ha_close)

            # HA Low = Min(Low, HA Open, HA Close)
            ha_low = min(candle.low, ha_open, ha_close)

            # Calculate wicks
            body_top = max(ha_open, ha_close)
            body_bottom = min(ha_open, ha_close)
            upper_wick = ha_high - body_top
            lower_wick = body_bottom - ha_low

            ha_candles.append(HeikenAshiCandle(
                open=ha_open,
                high=ha_high,
                low=ha_low,
                close=ha_close,
                is_green=ha_close >= ha_open,
                body=abs(ha_close - ha_open),
                upper_wick=upper_wick,
                lower_wick=lower_wick,
            ))

        return ha_candles

    def get_heiken_ashi_trend(
        self,
        lookback: int = 5,
    ) -> Tuple[str, int, float]:
        """
        Analyze Heiken Ashi for trend direction.

        Args:
            lookback: Number of candles to analyze

        Returns:
            Tuple of (trend direction, consecutive count, strength)
        """
        ha_candles = self.compute_heiken_ashi()

        if len(ha_candles) < lookback:
            return ("neutral", 0, 0.0)

        recent = ha_candles[-lookback:]

        green_count = sum(1 for c in recent if c.is_green)
        red_count = lookback - green_count

        # Count consecutive
        consecutive = 1
        last_color = recent[-1].is_green
        for c in reversed(recent[:-1]):
            if c.is_green == last_color:
                consecutive += 1
            else:
                break

        # Determine trend
        if green_count >= lookback * 0.7:
            trend = "bullish"
            strength = green_count / lookback
        elif red_count >= lookback * 0.7:
            trend = "bearish"
            strength = red_count / lookback
        else:
            trend = "neutral"
            strength = 0.5

        return (trend, consecutive, strength)

    # =========================================================================
    # Price Deltas / Momentum
    # =========================================================================

    def compute_price_delta(
        self,
        minutes: int = 1,
    ) -> Optional[Dict[str, float]]:
        """
        Calculate price change over specified time window.

        Args:
            minutes: Lookback window in minutes

        Returns:
            Dict with delta info or None
        """
        closes = self.get_closes()

        if len(closes) < minutes + 1:
            return None

        current = closes[-1]
        past = closes[-(minutes + 1)]

        delta = current - past
        delta_pct = (delta / past * 100) if past > 0 else 0

        return {
            "current": current,
            "past": past,
            "delta": delta,
            "delta_pct": delta_pct,
            "direction": "up" if delta > 0 else "down" if delta < 0 else "flat",
        }

    def compute_momentum(self) -> Optional[Dict[str, Any]]:
        """
        Calculate multi-timeframe momentum.

        Returns combined momentum from 1m, 3m, and 5m deltas.
        """
        delta_1m = self.compute_price_delta(1)
        delta_3m = self.compute_price_delta(3)
        delta_5m = self.compute_price_delta(5)

        if not all([delta_1m, delta_3m, delta_5m]):
            return None

        # Weighted momentum score
        momentum_score = (
            delta_1m["delta_pct"] * 0.5 +
            delta_3m["delta_pct"] * 0.3 +
            delta_5m["delta_pct"] * 0.2
        )

        return {
            "delta_1m": delta_1m,
            "delta_3m": delta_3m,
            "delta_5m": delta_5m,
            "momentum_score": momentum_score,
            "direction": "bullish" if momentum_score > 0.1 else "bearish" if momentum_score < -0.1 else "neutral",
        }

    # =========================================================================
    # Combined Analysis
    # =========================================================================

    def get_all_indicators(self) -> Dict[str, Any]:
        """
        Calculate all indicators at once.

        Returns comprehensive analysis dict.
        """
        return {
            "rsi": self.compute_rsi(),
            "macd": self.compute_macd(),
            "vwap": self.compute_vwap(),
            "heiken_ashi_trend": self.get_heiken_ashi_trend(),
            "momentum": self.compute_momentum(),
            "price_delta_1m": self.compute_price_delta(1),
            "price_delta_3m": self.compute_price_delta(3),
        }
