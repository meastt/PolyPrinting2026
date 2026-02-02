"""
Risk Manager

Enforces position limits, drawdown controls, and risk rules to protect
capital and ensure the bot operates within safe parameters.

Key responsibilities:
- Position size limits (2% max per trade for $50 start)
- Daily drawdown monitoring (5% limit)
- Maximum exposure limits
- Volatility filters
- Emergency stop functionality
"""

import time
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from src.utils.logger import get_logger

logger = get_logger(__name__)


# TA-based confidence adjustment factors
TA_CONFIDENCE_FACTORS = {
    "rsi_extreme": 0.15,      # RSI at extremes (< 30 or > 70)
    "macd_confirm": 0.10,     # MACD confirms direction
    "regime_favor": 0.10,     # Regime favorable for strategy
    "vwap_confirm": 0.05,     # Price action confirms at VWAP
    "heiken_trend": 0.10,     # Heiken Ashi trend alignment
    "multi_signal": 0.15,     # Multiple indicators align
}


class RiskLevel(Enum):
    """Risk level classifications."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class RiskLimits:
    """Configuration for risk limits."""
    # Position sizing
    max_position_percent: float = 2.0  # Max 2% of balance per trade
    max_total_exposure_percent: float = 20.0  # Max 20% total exposure
    max_open_positions: int = 10

    # Drawdown limits
    daily_drawdown_limit: float = 0.05  # 5% daily loss limit
    weekly_drawdown_limit: float = 0.15  # 15% weekly loss limit

    # Balance thresholds
    min_balance: float = 10.0  # Stop trading if balance falls below
    max_single_loss: float = 5.0  # Max loss on any single trade

    # Volatility
    max_volatility: float = 0.10  # Pause if hourly vol > 10%

    # Position limits
    max_correlation_exposure: float = 0.5  # Max exposure to correlated positions


@dataclass
class DailyStats:
    """Track daily trading statistics."""
    date: str
    starting_balance: float
    current_balance: float
    pnl: float = 0.0
    trades: int = 0
    wins: int = 0
    losses: int = 0
    max_drawdown: float = 0.0
    high_water_mark: float = 0.0


@dataclass
class TradeRiskAssessment:
    """Assessment of risk for a potential trade."""
    approved: bool
    risk_level: RiskLevel
    max_size: float
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class RiskManager:
    """
    Manages trading risk and enforces safety limits.

    Starting with $50, we use micro-sizing (0.5-2% per trade) to
    compound profits while protecting against catastrophic losses.

    Risk rules enforced:
    - Never risk more than 2% of balance on a single trade
    - Stop trading if daily loss exceeds 5%
    - Maintain minimum balance of $10
    - Limit total exposure to 20% of balance
    """

    def __init__(
        self,
        limits: Optional[RiskLimits] = None,
        starting_balance: float = 50.0,
    ):
        """
        Initialize risk manager.

        Args:
            limits: Risk limit configuration
            starting_balance: Initial trading balance (USDC)
        """
        self.limits = limits or RiskLimits()
        self.starting_balance = starting_balance
        self.current_balance = starting_balance

        # Track daily stats
        self._daily_stats: Dict[str, DailyStats] = {}
        self._current_day = self._get_date_key()

        # Initialize today's stats
        self._init_daily_stats()

        # Emergency stop flag
        self._emergency_stop = False
        self._stop_reason: Optional[str] = None

        # Position tracking
        self._open_positions: Dict[str, Dict[str, Any]] = {}
        self._total_exposure: float = 0.0

        # Volatility tracking
        self._volatility_readings: List[float] = []

        logger.info(
            f"RiskManager initialized (balance=${starting_balance}, "
            f"max_position={self.limits.max_position_percent}%)"
        )

    def _get_date_key(self) -> str:
        """Get current date as string key."""
        return datetime.now().strftime("%Y-%m-%d")

    def _init_daily_stats(self) -> None:
        """Initialize stats for current day."""
        today = self._get_date_key()
        if today not in self._daily_stats:
            self._daily_stats[today] = DailyStats(
                date=today,
                starting_balance=self.current_balance,
                current_balance=self.current_balance,
                high_water_mark=self.current_balance,
            )
            self._current_day = today
            logger.info(f"Initialized daily stats for {today}")

    def update_balance(self, new_balance: float) -> None:
        """
        Update current balance and recalculate stats.

        Args:
            new_balance: New balance after trade
        """
        # Check for day change
        today = self._get_date_key()
        if today != self._current_day:
            self._init_daily_stats()

        old_balance = self.current_balance
        self.current_balance = new_balance

        # Update daily stats
        stats = self._daily_stats[today]
        stats.current_balance = new_balance
        stats.pnl = new_balance - stats.starting_balance

        # Update high water mark
        if new_balance > stats.high_water_mark:
            stats.high_water_mark = new_balance

        # Calculate drawdown from high water mark
        if stats.high_water_mark > 0:
            drawdown = (stats.high_water_mark - new_balance) / stats.high_water_mark
            stats.max_drawdown = max(stats.max_drawdown, drawdown)

        # Check for critical conditions
        self._check_critical_conditions()

        logger.debug(
            f"Balance updated: ${old_balance:.2f} -> ${new_balance:.2f} "
            f"(daily P&L: ${stats.pnl:.2f})"
        )

    def _check_critical_conditions(self) -> None:
        """Check for conditions that trigger emergency stop."""
        today = self._get_date_key()
        stats = self._daily_stats.get(today)

        if not stats:
            return

        # Check minimum balance
        if self.current_balance < self.limits.min_balance:
            self.trigger_emergency_stop(
                f"Balance (${self.current_balance:.2f}) below minimum "
                f"(${self.limits.min_balance:.2f})"
            )
            return

        # Check daily drawdown
        daily_loss_pct = abs(stats.pnl / stats.starting_balance) if stats.pnl < 0 else 0
        if daily_loss_pct >= self.limits.daily_drawdown_limit:
            self.trigger_emergency_stop(
                f"Daily loss ({daily_loss_pct*100:.1f}%) exceeds limit "
                f"({self.limits.daily_drawdown_limit*100:.1f}%)"
            )
            return

    def assess_trade(
        self,
        trade_size: float,
        market_id: str,
        outcome: str,
        current_volatility: Optional[float] = None,
    ) -> TradeRiskAssessment:
        """
        Assess whether a trade is within risk limits.

        Args:
            trade_size: Proposed trade size in USDC
            market_id: Market identifier
            outcome: "Yes" or "No"
            current_volatility: Current market volatility

        Returns:
            TradeRiskAssessment with approval and limits
        """
        reasons = []
        warnings = []
        risk_level = RiskLevel.LOW

        # Check emergency stop
        if self._emergency_stop:
            return TradeRiskAssessment(
                approved=False,
                risk_level=RiskLevel.CRITICAL,
                max_size=0,
                reasons=[f"Emergency stop active: {self._stop_reason}"],
            )

        # Calculate maximum allowed position size
        max_by_percent = self.current_balance * (self.limits.max_position_percent / 100)
        max_by_single_loss = self.limits.max_single_loss
        max_size = min(max_by_percent, max_by_single_loss)

        # Check if trade size exceeds limits
        if trade_size > max_size:
            reasons.append(
                f"Trade size (${trade_size:.2f}) exceeds max (${max_size:.2f})"
            )
            risk_level = RiskLevel.HIGH

        # Check total exposure
        new_exposure = self._total_exposure + trade_size
        max_exposure = self.current_balance * (self.limits.max_total_exposure_percent / 100)

        if new_exposure > max_exposure:
            reasons.append(
                f"Total exposure (${new_exposure:.2f}) would exceed limit (${max_exposure:.2f})"
            )
            risk_level = RiskLevel.HIGH

        # Check number of open positions
        if len(self._open_positions) >= self.limits.max_open_positions:
            reasons.append(
                f"Max open positions ({self.limits.max_open_positions}) reached"
            )
            risk_level = RiskLevel.MEDIUM

        # Check volatility
        if current_volatility and current_volatility > self.limits.max_volatility:
            warnings.append(
                f"High volatility detected ({current_volatility*100:.1f}%)"
            )
            risk_level = max(risk_level, RiskLevel.MEDIUM, key=lambda x: x.value)

        # Check if already have position in same market
        if market_id in self._open_positions:
            warnings.append(f"Already have position in market {market_id[:10]}...")

        # Determine approval
        approved = len(reasons) == 0

        return TradeRiskAssessment(
            approved=approved,
            risk_level=risk_level,
            max_size=max_size if approved else 0,
            reasons=reasons,
            warnings=warnings,
        )

    def calculate_position_size(
        self,
        edge: float,
        confidence: float = 1.0,
        max_override: Optional[float] = None,
    ) -> float:
        """
        Calculate optimal position size based on edge and confidence.

        Uses a fraction of Kelly criterion for conservative sizing,
        suitable for our $50 starting capital strategy.

        Args:
            edge: Expected edge (e.g., 0.02 for 2%)
            confidence: Confidence in the edge (0-1)
            max_override: Optional maximum size override

        Returns:
            Recommended position size in USDC
        """
        if edge <= 0:
            return 0

        # Conservative Kelly: fraction of full Kelly
        kelly_fraction = 0.25  # Use 25% of Kelly

        # Simplified Kelly for binary outcomes
        # Kelly = edge / odds, for binary: edge / 1 = edge
        kelly_bet = edge * kelly_fraction * confidence

        # Apply to balance
        raw_size = self.current_balance * kelly_bet

        # Apply limits
        max_size = self.current_balance * (self.limits.max_position_percent / 100)
        if max_override:
            max_size = min(max_size, max_override)

        # Minimum size threshold ($0.50)
        min_size = 0.50

        position_size = max(min_size, min(raw_size, max_size))

        logger.debug(
            f"Position size calculation: edge={edge:.3f}, "
            f"confidence={confidence:.2f}, size=${position_size:.2f}"
        )

        return round(position_size, 2)

    def record_trade(
        self,
        market_id: str,
        outcome: str,
        size: float,
        price: float,
        is_win: Optional[bool] = None,
        pnl: Optional[float] = None,
    ) -> None:
        """
        Record a trade for tracking purposes.

        Args:
            market_id: Market identifier
            outcome: "Yes" or "No"
            size: Trade size
            price: Entry price
            is_win: Whether trade was profitable (for closed trades)
            pnl: Profit/loss (for closed trades)
        """
        today = self._get_date_key()
        if today != self._current_day:
            self._init_daily_stats()

        stats = self._daily_stats[today]
        stats.trades += 1

        if is_win is not None:
            if is_win:
                stats.wins += 1
            else:
                stats.losses += 1

        if pnl is not None:
            self.update_balance(self.current_balance + pnl)

        # Track open position
        position_key = f"{market_id}:{outcome}"
        self._open_positions[position_key] = {
            "market_id": market_id,
            "outcome": outcome,
            "size": size,
            "price": price,
            "timestamp": time.time(),
        }
        self._total_exposure += size

        logger.info(
            f"Trade recorded: {outcome} ${size:.2f} @ {price:.4f} "
            f"(market={market_id[:10]}...)"
        )

    def close_position(
        self,
        market_id: str,
        outcome: str,
        pnl: float,
    ) -> None:
        """
        Record a position closure.

        Args:
            market_id: Market identifier
            outcome: "Yes" or "No"
            pnl: Realized profit/loss
        """
        position_key = f"{market_id}:{outcome}"

        if position_key in self._open_positions:
            position = self._open_positions.pop(position_key)
            self._total_exposure -= position["size"]

            # Update stats
            is_win = pnl > 0
            self.record_trade(
                market_id=market_id,
                outcome=outcome,
                size=position["size"],
                price=position["price"],
                is_win=is_win,
                pnl=pnl,
            )

            logger.info(
                f"Position closed: {outcome} P&L=${pnl:.2f} "
                f"(market={market_id[:10]}...)"
            )

    def trigger_emergency_stop(self, reason: str) -> None:
        """
        Trigger emergency stop to halt all trading.

        Args:
            reason: Reason for the emergency stop
        """
        self._emergency_stop = True
        self._stop_reason = reason
        logger.critical(f"EMERGENCY STOP TRIGGERED: {reason}")

    def reset_emergency_stop(self) -> None:
        """Reset emergency stop flag (use with caution)."""
        self._emergency_stop = False
        self._stop_reason = None
        logger.warning("Emergency stop has been reset")

    def is_trading_allowed(self) -> bool:
        """Check if trading is currently allowed."""
        return not self._emergency_stop

    def get_daily_stats(self, date: Optional[str] = None) -> Optional[DailyStats]:
        """
        Get stats for a specific day.

        Args:
            date: Date string (YYYY-MM-DD) or None for today

        Returns:
            DailyStats or None
        """
        date_key = date or self._get_date_key()
        return self._daily_stats.get(date_key)

    def get_risk_status(self) -> Dict[str, Any]:
        """
        Get current risk status summary.

        Returns:
            Dict with risk metrics and status
        """
        today = self._get_date_key()
        stats = self._daily_stats.get(today)

        daily_pnl_pct = 0
        if stats and stats.starting_balance > 0:
            daily_pnl_pct = (stats.pnl / stats.starting_balance) * 100

        return {
            "balance": self.current_balance,
            "starting_balance": self.starting_balance,
            "total_pnl": self.current_balance - self.starting_balance,
            "daily_pnl": stats.pnl if stats else 0,
            "daily_pnl_pct": daily_pnl_pct,
            "open_positions": len(self._open_positions),
            "total_exposure": self._total_exposure,
            "exposure_pct": (self._total_exposure / self.current_balance * 100)
                           if self.current_balance > 0 else 0,
            "emergency_stop": self._emergency_stop,
            "stop_reason": self._stop_reason,
            "trades_today": stats.trades if stats else 0,
            "win_rate": (stats.wins / stats.trades * 100)
                       if stats and stats.trades > 0 else 0,
        }

    def get_available_capital(self) -> float:
        """
        Get capital available for new trades.

        Returns:
            Available USDC for trading
        """
        max_exposure = self.current_balance * (self.limits.max_total_exposure_percent / 100)
        available = max_exposure - self._total_exposure
        return max(0, available)

    def report_volatility(self, volatility: float) -> None:
        """
        Report current market volatility.

        Args:
            volatility: Volatility reading (0-1)
        """
        self._volatility_readings.append(volatility)
        # Keep last 100 readings
        if len(self._volatility_readings) > 100:
            self._volatility_readings = self._volatility_readings[-100:]

    def get_average_volatility(self) -> float:
        """Get average of recent volatility readings."""
        if not self._volatility_readings:
            return 0
        return sum(self._volatility_readings) / len(self._volatility_readings)

    def calculate_ta_adjusted_size(
        self,
        base_size: float,
        ta_signals: Optional[Dict[str, Any]] = None,
        edge: float = 0.0,
        base_confidence: float = 0.6,
    ) -> Dict[str, Any]:
        """
        Calculate position size adjusted by technical analysis signals.

        Uses TA indicators to boost or reduce confidence, which affects
        final position sizing. This is the PolymarketBTC15mAssistant-inspired
        enhancement for smarter risk management.

        Args:
            base_size: Initial position size calculation
            ta_signals: Dict with TA indicator results:
                - rsi_value: 0-100
                - rsi_zone: "overbought", "oversold", "neutral"
                - macd_histogram: Float value
                - macd_rising: Boolean
                - regime: MarketRegime enum value
                - vwap_position: "above", "below", "at"
                - heiken_trend: "bullish", "bearish", "neutral"
                - direction: "up" or "down" (our intended trade direction)
            edge: Expected edge on the trade
            base_confidence: Starting confidence level

        Returns:
            Dict with:
                - adjusted_size: Final position size
                - final_confidence: TA-adjusted confidence
                - confidence_adjustments: List of adjustments made
                - risk_level: Overall risk assessment
        """
        if not ta_signals:
            return {
                "adjusted_size": base_size,
                "final_confidence": base_confidence,
                "confidence_adjustments": [],
                "risk_level": RiskLevel.MEDIUM,
            }

        adjustments = []
        confidence_delta = 0.0
        trade_direction = ta_signals.get("direction", "up")

        # RSI extreme confirmation
        rsi_value = ta_signals.get("rsi_value", 50)
        rsi_zone = ta_signals.get("rsi_zone", "neutral")

        if rsi_zone == "overbought" and trade_direction == "down":
            confidence_delta += TA_CONFIDENCE_FACTORS["rsi_extreme"]
            adjustments.append(f"+{TA_CONFIDENCE_FACTORS['rsi_extreme']*100:.0f}%: RSI overbought ({rsi_value:.0f})")
        elif rsi_zone == "oversold" and trade_direction == "up":
            confidence_delta += TA_CONFIDENCE_FACTORS["rsi_extreme"]
            adjustments.append(f"+{TA_CONFIDENCE_FACTORS['rsi_extreme']*100:.0f}%: RSI oversold ({rsi_value:.0f})")
        elif rsi_zone == "overbought" and trade_direction == "up":
            confidence_delta -= TA_CONFIDENCE_FACTORS["rsi_extreme"]
            adjustments.append(f"-{TA_CONFIDENCE_FACTORS['rsi_extreme']*100:.0f}%: RSI overbought, betting up")
        elif rsi_zone == "oversold" and trade_direction == "down":
            confidence_delta -= TA_CONFIDENCE_FACTORS["rsi_extreme"]
            adjustments.append(f"-{TA_CONFIDENCE_FACTORS['rsi_extreme']*100:.0f}%: RSI oversold, betting down")

        # MACD confirmation
        macd_rising = ta_signals.get("macd_rising")
        if macd_rising is not None:
            if macd_rising and trade_direction == "up":
                confidence_delta += TA_CONFIDENCE_FACTORS["macd_confirm"]
                adjustments.append(f"+{TA_CONFIDENCE_FACTORS['macd_confirm']*100:.0f}%: MACD rising, bullish")
            elif not macd_rising and trade_direction == "down":
                confidence_delta += TA_CONFIDENCE_FACTORS["macd_confirm"]
                adjustments.append(f"+{TA_CONFIDENCE_FACTORS['macd_confirm']*100:.0f}%: MACD falling, bearish")
            elif macd_rising and trade_direction == "down":
                confidence_delta -= TA_CONFIDENCE_FACTORS["macd_confirm"] * 0.5
                adjustments.append(f"-{TA_CONFIDENCE_FACTORS['macd_confirm']*50:.0f}%: MACD rising, betting down")

        # Regime alignment
        regime = ta_signals.get("regime")
        if regime:
            from src.analysis.regime import MarketRegime

            # For reversion strategies, RANGE is favorable
            strategy_type = ta_signals.get("strategy_type", "reversion")

            if strategy_type == "reversion":
                if regime == MarketRegime.RANGE:
                    confidence_delta += TA_CONFIDENCE_FACTORS["regime_favor"]
                    adjustments.append(f"+{TA_CONFIDENCE_FACTORS['regime_favor']*100:.0f}%: Range-bound market favors reversion")
                elif regime == MarketRegime.CHOP:
                    confidence_delta -= TA_CONFIDENCE_FACTORS["regime_favor"] * 0.5
                    adjustments.append(f"-{TA_CONFIDENCE_FACTORS['regime_favor']*50:.0f}%: Choppy market, uncertain")
                elif regime == MarketRegime.TREND_UP and trade_direction == "down":
                    confidence_delta -= TA_CONFIDENCE_FACTORS["regime_favor"]
                    adjustments.append(f"-{TA_CONFIDENCE_FACTORS['regime_favor']*100:.0f}%: Uptrend resists down bets")
                elif regime == MarketRegime.TREND_DOWN and trade_direction == "up":
                    confidence_delta -= TA_CONFIDENCE_FACTORS["regime_favor"]
                    adjustments.append(f"-{TA_CONFIDENCE_FACTORS['regime_favor']*100:.0f}%: Downtrend resists up bets")
            else:
                # For trend-following strategies
                if regime == MarketRegime.TREND_UP and trade_direction == "up":
                    confidence_delta += TA_CONFIDENCE_FACTORS["regime_favor"]
                    adjustments.append(f"+{TA_CONFIDENCE_FACTORS['regime_favor']*100:.0f}%: Uptrend confirms long")
                elif regime == MarketRegime.TREND_DOWN and trade_direction == "down":
                    confidence_delta += TA_CONFIDENCE_FACTORS["regime_favor"]
                    adjustments.append(f"+{TA_CONFIDENCE_FACTORS['regime_favor']*100:.0f}%: Downtrend confirms short")

        # Heiken Ashi trend confirmation
        heiken_trend = ta_signals.get("heiken_trend")
        if heiken_trend:
            if heiken_trend == "bullish" and trade_direction == "up":
                confidence_delta += TA_CONFIDENCE_FACTORS["heiken_trend"]
                adjustments.append(f"+{TA_CONFIDENCE_FACTORS['heiken_trend']*100:.0f}%: Heiken Ashi bullish")
            elif heiken_trend == "bearish" and trade_direction == "down":
                confidence_delta += TA_CONFIDENCE_FACTORS["heiken_trend"]
                adjustments.append(f"+{TA_CONFIDENCE_FACTORS['heiken_trend']*100:.0f}%: Heiken Ashi bearish")

        # Multi-signal alignment bonus
        positive_signals = sum(1 for adj in adjustments if adj.startswith("+"))
        if positive_signals >= 3:
            confidence_delta += TA_CONFIDENCE_FACTORS["multi_signal"]
            adjustments.append(f"+{TA_CONFIDENCE_FACTORS['multi_signal']*100:.0f}%: Multiple indicators align ({positive_signals})")

        # Calculate final confidence (clamped 0.1 to 0.95)
        final_confidence = max(0.1, min(0.95, base_confidence + confidence_delta))

        # Determine risk level based on confidence and edge
        if final_confidence >= 0.8 and edge >= 0.05:
            risk_level = RiskLevel.LOW
        elif final_confidence >= 0.6 and edge >= 0.03:
            risk_level = RiskLevel.MEDIUM
        elif final_confidence < 0.5 or edge < 0.02:
            risk_level = RiskLevel.HIGH
        else:
            risk_level = RiskLevel.MEDIUM

        # Adjust size based on confidence change
        confidence_ratio = final_confidence / base_confidence if base_confidence > 0 else 1.0
        adjusted_size = base_size * confidence_ratio

        # Apply minimum and maximum bounds
        max_size = self.current_balance * (self.limits.max_position_percent / 100)
        min_size = 0.50  # $0.50 minimum

        adjusted_size = max(min_size, min(adjusted_size, max_size))

        logger.debug(
            f"TA-adjusted sizing: base=${base_size:.2f} -> ${adjusted_size:.2f} "
            f"(confidence: {base_confidence:.2f} -> {final_confidence:.2f}, "
            f"adjustments: {len(adjustments)})"
        )

        return {
            "adjusted_size": round(adjusted_size, 2),
            "final_confidence": final_confidence,
            "confidence_adjustments": adjustments,
            "risk_level": risk_level,
            "confidence_delta": confidence_delta,
        }

    def should_reduce_exposure(
        self,
        regime: Optional[Any] = None,
        volatility: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Determine if we should reduce overall exposure based on conditions.

        Args:
            regime: Current market regime from RegimeDetector
            volatility: Current volatility reading

        Returns:
            Dict with recommendation and reasoning
        """
        recommendations = []
        should_reduce = False
        reduction_factor = 1.0

        # Check volatility
        avg_vol = self.get_average_volatility()
        if volatility and volatility > self.limits.max_volatility:
            should_reduce = True
            reduction_factor *= 0.5
            recommendations.append(f"High volatility ({volatility*100:.1f}% > {self.limits.max_volatility*100:.1f}%)")

        # Check regime
        if regime:
            from src.analysis.regime import MarketRegime

            if regime == MarketRegime.CHOP:
                should_reduce = True
                reduction_factor *= 0.7
                recommendations.append("Choppy market regime detected")

        # Check current exposure
        exposure_pct = (self._total_exposure / self.current_balance * 100) if self.current_balance > 0 else 0
        if exposure_pct > self.limits.max_total_exposure_percent * 0.8:
            should_reduce = True
            reduction_factor *= 0.8
            recommendations.append(f"High exposure ({exposure_pct:.1f}%)")

        # Check daily P&L
        today = self._get_date_key()
        stats = self._daily_stats.get(today)
        if stats and stats.starting_balance > 0:
            daily_pnl_pct = stats.pnl / stats.starting_balance
            if daily_pnl_pct < -self.limits.daily_drawdown_limit * 0.5:
                should_reduce = True
                reduction_factor *= 0.5
                recommendations.append(f"Approaching daily loss limit ({daily_pnl_pct*100:.1f}%)")

        return {
            "should_reduce": should_reduce,
            "reduction_factor": reduction_factor,
            "recommendations": recommendations,
        }
