"""
Kalshi Arbitrage Strategy

Exploits YES/NO pricing inefficiencies on Kalshi markets.
When YES_price + NO_price < $1, there's guaranteed profit.

On Kalshi, this is even more profitable due to zero maker fees!
"""

import time
from typing import Dict, List, Any, Optional

from src.api.kalshi_client import KalshiClient, KalshiMarket
from src.strategies.base_strategy import BaseStrategy
from src.utils.logger import get_logger

logger = get_logger(__name__)


class KalshiArbitrageStrategy(BaseStrategy):
    """
    YES/NO Arbitrage Strategy for Kalshi.

    Identifies and exploits pricing inefficiencies where the sum
    of YES and NO prices is less than $1.

    With Kalshi's zero maker fees, even small inefficiencies
    can be profitably exploited.
    """

    def __init__(
        self,
        kalshi: KalshiClient,
        config: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize Kalshi Arbitrage Strategy.

        Args:
            kalshi: Kalshi API client
            config: Strategy configuration
        """
        super().__init__(name="kalshi_arbitrage", config=config)

        self.kalshi = kalshi

        # Configuration
        config = config or {}
        self.min_spread = config.get("min_spread", 0.01)  # 1% minimum profit
        self.max_position_size = config.get("max_position_size", 5.0)
        self.min_position_size = config.get("min_position_size", 1.0)

        # Stats
        self._opportunities_found = 0
        self._trades_executed = 0
        self._total_arb_profit = 0.0

        logger.info(
            f"KalshiArbitrageStrategy initialized "
            f"(min_spread={self.min_spread:.1%})"
        )

    def evaluate(
        self,
        markets: List[KalshiMarket],
        positions: List[Any],
        balance: float,
    ) -> List[Dict[str, Any]]:
        """
        Evaluate markets for arbitrage opportunities.

        Args:
            markets: List of KalshiMarket objects
            positions: Current positions
            balance: Available balance

        Returns:
            List of trading signals (pairs of YES/NO trades)
        """
        self._last_evaluation = time.time()
        signals = []

        for market in markets:
            if not market.is_active:
                continue

            # Check for arbitrage opportunity
            arb_opportunity = self._check_arbitrage(market)

            if arb_opportunity:
                self._opportunities_found += 1

                # Create paired signals
                arb_signals = self._create_arb_signals(
                    market=market,
                    opportunity=arb_opportunity,
                    balance=balance,
                )

                signals.extend(arb_signals)

        return signals

    def _check_arbitrage(self, market: KalshiMarket) -> Optional[Dict[str, Any]]:
        """
        Check if arbitrage opportunity exists.

        Arbitrage exists when:
        YES_ask + NO_ask < 1.00

        This means you can buy both outcomes for less than $1
        and guarantee a $1 payout.

        Args:
            market: Kalshi market to check

        Returns:
            Opportunity dict or None
        """
        yes_ask = market.yes_ask
        no_ask = market.no_ask

        # Both must be available
        if yes_ask <= 0 or no_ask <= 0:
            return None

        total_cost = yes_ask + no_ask

        # Check if there's profit after threshold
        if total_cost < (1 - self.min_spread):
            profit_per_contract = 1 - total_cost

            logger.info(
                f"ARBITRAGE FOUND: {market.ticker} "
                f"YES@{yes_ask:.2f} + NO@{no_ask:.2f} = ${total_cost:.4f} "
                f"(profit: {profit_per_contract:.4f} per contract)"
            )

            return {
                "yes_price": yes_ask,
                "no_price": no_ask,
                "total_cost": total_cost,
                "profit_per_contract": profit_per_contract,
                "profit_pct": profit_per_contract / total_cost,
            }

        return None

    def _create_arb_signals(
        self,
        market: KalshiMarket,
        opportunity: Dict[str, Any],
        balance: float,
    ) -> List[Dict[str, Any]]:
        """Create paired YES/NO signals for arbitrage."""
        # Calculate position size
        # We need to buy equal amounts of YES and NO
        max_size = min(balance / 2, self.max_position_size)
        size = max(self.min_position_size, max_size)

        # Number of contracts we can afford
        total_cost = opportunity["total_cost"]
        contracts = int(size / total_cost)

        if contracts < 1:
            return []

        actual_size = contracts * total_cost
        expected_profit = contracts * opportunity["profit_per_contract"]

        self._trades_executed += 1
        self._total_arb_profit += expected_profit

        # Create two signals - one for YES, one for NO
        base_signal = {
            "ticker": market.ticker,
            "market_id": market.ticker,
            "strategy": self.name,
            "reason": f"Arbitrage: guaranteed {opportunity['profit_pct']:.2%} profit",
            "urgency": "critical",  # Arb opportunities disappear quickly
            "is_arbitrage": True,
        }

        yes_signal = {
            **base_signal,
            "side": "yes",
            "outcome": "YES",
            "price": opportunity["yes_price"],
            "size": actual_size / 2,
            "ev": opportunity["profit_pct"],
            "confidence": 1.0,  # Arbitrage is risk-free
        }

        no_signal = {
            **base_signal,
            "side": "no",
            "outcome": "NO",
            "price": opportunity["no_price"],
            "size": actual_size / 2,
            "ev": opportunity["profit_pct"],
            "confidence": 1.0,
        }

        return [yes_signal, no_signal]

    def get_stats(self) -> Dict[str, Any]:
        """Get strategy statistics."""
        stats = super().get_stats()
        stats.update({
            "opportunities_found": self._opportunities_found,
            "trades_executed": self._trades_executed,
            "total_arb_profit": self._total_arb_profit,
            "min_spread": self.min_spread,
        })
        return stats
