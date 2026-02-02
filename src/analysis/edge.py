"""
Edge Calculation Module

Calculates trading edge by comparing our model's predicted
probabilities against Polymarket's implied odds.

Edge = Model Prediction - Market Price

Positive edge means we think the market is mispriced in our favor.
We only trade when edge exceeds our threshold (accounting for fees).

Phase-based thresholds (inspired by PolymarketBTC15mAssistant):
- Early (>10 min remaining): 5% edge required
- Mid (5-10 min remaining): 10% edge required
- Late (<5 min remaining): 20% edge required

Higher thresholds near close because:
1. Less time for prediction to play out
2. Higher uncertainty
3. Avoid getting caught in last-minute volatility
"""

from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass
from enum import Enum

from src.utils.logger import get_logger

logger = get_logger(__name__)


class MarketPhase(Enum):
    """Trading window phase."""
    EARLY = "early"      # > 10 minutes remaining
    MID = "mid"          # 5-10 minutes remaining
    LATE = "late"        # < 5 minutes remaining
    CLOSED = "closed"    # Market resolved


@dataclass
class EdgeResult:
    """Result of edge calculation."""
    # Market probabilities (from prices)
    market_up_prob: float
    market_down_prob: float

    # Model probabilities (our prediction)
    model_up_prob: float
    model_down_prob: float

    # Edge calculations
    edge_up: float  # Model - Market for UP
    edge_down: float  # Model - Market for DOWN

    # Best trade
    best_side: str  # "up" or "down"
    best_edge: float
    edge_pct: float  # As percentage

    # Phase and threshold
    phase: MarketPhase
    threshold: float
    meets_threshold: bool

    # Final recommendation
    should_trade: bool
    recommended_size_multiplier: float


class EdgeCalculator:
    """
    Calculates trading edge against market prices.

    Edge represents our expected advantage over the market.
    We only trade when edge exceeds phase-specific thresholds.
    """

    # Phase thresholds (minimum edge to trade)
    THRESHOLD_EARLY = 0.05   # 5% edge when > 10 min
    THRESHOLD_MID = 0.10     # 10% edge when 5-10 min
    THRESHOLD_LATE = 0.20    # 20% edge when < 5 min

    # Fee considerations
    TAKER_FEE = 0.03  # 3% taker fee
    MAKER_REBATE = 0.01  # 1% maker rebate

    def __init__(self, use_maker_orders: bool = True):
        """
        Initialize edge calculator.

        Args:
            use_maker_orders: Whether we use maker orders (affects fee calc)
        """
        self.use_maker_orders = use_maker_orders
        self.fee_adjustment = self.MAKER_REBATE if use_maker_orders else -self.TAKER_FEE

        logger.debug(
            f"EdgeCalculator initialized "
            f"(maker={use_maker_orders}, fee_adj={self.fee_adjustment})"
        )

    def compute_edge(
        self,
        market_yes_price: float,
        market_no_price: float,
        model_up_prob: float,
        remaining_minutes: float,
    ) -> EdgeResult:
        """
        Calculate edge between model prediction and market prices.

        Args:
            market_yes_price: Polymarket YES price (0-1)
            market_no_price: Polymarket NO price (0-1)
            model_up_prob: Our model's UP probability (0-1)
            remaining_minutes: Minutes until market closes

        Returns:
            EdgeResult with recommendations
        """
        # Normalize market prices to probabilities
        price_sum = market_yes_price + market_no_price

        if price_sum > 0:
            market_up_prob = market_yes_price / price_sum
            market_down_prob = market_no_price / price_sum
        else:
            market_up_prob = 0.5
            market_down_prob = 0.5

        # Clamp to valid probability range
        market_up_prob = max(0.01, min(0.99, market_up_prob))
        market_down_prob = max(0.01, min(0.99, market_down_prob))

        # Model probabilities
        model_down_prob = 1 - model_up_prob

        # Calculate raw edge
        edge_up = model_up_prob - market_up_prob
        edge_down = model_down_prob - market_down_prob

        # Adjust for fees (edge needs to overcome fee drag)
        # For maker orders, we actually get a rebate boost
        edge_up_adjusted = edge_up + self.fee_adjustment
        edge_down_adjusted = edge_down + self.fee_adjustment

        # Determine best side
        if abs(edge_up_adjusted) > abs(edge_down_adjusted):
            if edge_up_adjusted > 0:
                best_side = "up"
                best_edge = edge_up_adjusted
            else:
                best_side = "down"
                best_edge = edge_down_adjusted
        else:
            if edge_down_adjusted > 0:
                best_side = "down"
                best_edge = edge_down_adjusted
            else:
                best_side = "up"
                best_edge = edge_up_adjusted

        # Get phase and threshold
        phase = self._get_phase(remaining_minutes)
        threshold = self._get_threshold(phase)

        # Check if edge meets threshold
        meets_threshold = best_edge >= threshold

        # Size multiplier based on edge magnitude
        if meets_threshold:
            # Scale size with edge (more edge = larger position)
            edge_ratio = best_edge / threshold
            size_multiplier = min(2.0, 0.5 + (edge_ratio * 0.5))
        else:
            size_multiplier = 0.0

        # Final trade recommendation
        should_trade = (
            meets_threshold and
            phase != MarketPhase.CLOSED and
            best_edge > 0
        )

        return EdgeResult(
            market_up_prob=market_up_prob,
            market_down_prob=market_down_prob,
            model_up_prob=model_up_prob,
            model_down_prob=model_down_prob,
            edge_up=edge_up_adjusted,
            edge_down=edge_down_adjusted,
            best_side=best_side,
            best_edge=best_edge,
            edge_pct=best_edge * 100,
            phase=phase,
            threshold=threshold,
            meets_threshold=meets_threshold,
            should_trade=should_trade,
            recommended_size_multiplier=size_multiplier,
        )

    def _get_phase(self, remaining_minutes: float) -> MarketPhase:
        """Determine market phase from remaining time."""
        if remaining_minutes <= 0:
            return MarketPhase.CLOSED
        elif remaining_minutes < 5:
            return MarketPhase.LATE
        elif remaining_minutes < 10:
            return MarketPhase.MID
        else:
            return MarketPhase.EARLY

    def _get_threshold(self, phase: MarketPhase) -> float:
        """Get edge threshold for market phase."""
        if phase == MarketPhase.EARLY:
            return self.THRESHOLD_EARLY
        elif phase == MarketPhase.MID:
            return self.THRESHOLD_MID
        elif phase == MarketPhase.LATE:
            return self.THRESHOLD_LATE
        else:
            return 1.0  # Impossible threshold if closed

    def decide(
        self,
        edge_result: EdgeResult,
        signal_confidence: float,
        regime_allows: bool = True,
    ) -> Dict[str, Any]:
        """
        Make final trade decision incorporating all factors.

        Args:
            edge_result: Edge calculation result
            signal_confidence: Confidence from signal scoring
            regime_allows: Whether regime detector allows trading

        Returns:
            Decision dict with action and parameters
        """
        decision = {
            "action": "none",
            "side": None,
            "size_multiplier": 0.0,
            "confidence": 0.0,
            "reasons": [],
        }

        # Check all conditions
        if not regime_allows:
            decision["reasons"].append("regime_blocked")
            return decision

        if edge_result.phase == MarketPhase.CLOSED:
            decision["reasons"].append("market_closed")
            return decision

        if not edge_result.meets_threshold:
            decision["reasons"].append(
                f"edge_{edge_result.edge_pct:.1f}%_below_threshold_{edge_result.threshold*100:.0f}%"
            )
            return decision

        if signal_confidence < 0.5:
            decision["reasons"].append(f"low_confidence_{signal_confidence:.2f}")
            return decision

        # All checks passed - recommend trade
        decision["action"] = "trade"
        decision["side"] = edge_result.best_side
        decision["size_multiplier"] = edge_result.recommended_size_multiplier * signal_confidence
        decision["confidence"] = signal_confidence
        decision["edge"] = edge_result.best_edge
        decision["edge_pct"] = edge_result.edge_pct
        decision["phase"] = edge_result.phase.value
        decision["reasons"].append(
            f"edge_{edge_result.edge_pct:.1f}%_exceeds_{edge_result.threshold*100:.0f}%_threshold"
        )

        return decision

    def calculate_expected_value(
        self,
        edge: float,
        win_probability: float,
        position_size: float,
    ) -> Dict[str, float]:
        """
        Calculate expected value of a trade.

        Args:
            edge: Our edge over market
            win_probability: Probability we're right
            position_size: Position size in USDC

        Returns:
            Dict with EV calculations
        """
        # Expected profit if we win
        # Win = position resolves to $1, we paid (1 - edge) effectively
        win_payout = position_size  # $1 per share on win
        loss_payout = 0  # $0 on loss

        # Calculate EV
        ev = (win_probability * win_payout) + ((1 - win_probability) * loss_payout) - position_size

        # As percentage of position
        ev_pct = (ev / position_size * 100) if position_size > 0 else 0

        return {
            "expected_value": ev,
            "expected_value_pct": ev_pct,
            "win_probability": win_probability,
            "position_size": position_size,
            "break_even_prob": position_size,  # Need this prob to break even
        }


def calculate_arbitrage_edge(
    yes_price: float,
    no_price: float,
) -> Dict[str, Any]:
    """
    Calculate arbitrage edge when YES + NO < $1.

    This is the simplest form of edge - guaranteed profit
    regardless of outcome.

    Args:
        yes_price: YES token price
        no_price: NO token price

    Returns:
        Arbitrage analysis
    """
    total_cost = yes_price + no_price
    guaranteed_payout = 1.0

    if total_cost >= guaranteed_payout:
        return {
            "has_arbitrage": False,
            "total_cost": total_cost,
            "profit": 0,
            "profit_pct": 0,
            "reason": "no_arbitrage_sum_gte_1",
        }

    profit = guaranteed_payout - total_cost
    profit_pct = (profit / total_cost) * 100

    return {
        "has_arbitrage": True,
        "total_cost": total_cost,
        "profit_per_pair": profit,
        "profit_pct": profit_pct,
        "yes_price": yes_price,
        "no_price": no_price,
        "is_risk_free": True,
        "reason": f"arb_profit_{profit_pct:.2f}%",
    }
