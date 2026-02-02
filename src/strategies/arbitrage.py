"""
YES/NO Arbitrage Strategy

Exploits pricing inefficiencies in binary markets where YES + NO prices
sum to less than $1, guaranteeing profit regardless of outcome.

This is inspired by successful community trading tactics (Moltbot, etc.)
that have turned small stakes into significant profits through systematic
arbitrage in short-term crypto markets.

How it works:
1. Scan binary markets for YES price + NO price < $0.99
2. If found, buy equal amounts of both YES and NO
3. One side will resolve to $1, the other to $0
4. Guaranteed profit = $1 - (YES + NO cost)

Example:
- YES trading at $0.48
- NO trading at $0.50
- Total cost: $0.98 per share pair
- Payout: $1.00 (one side wins)
- Profit: $0.02 per share pair = 2% risk-free

Post-2026 fee considerations:
- Use maker orders to earn rebates (not pay fees)
- Account for spread/slippage in profitability calculation
- Minimum 1% profit threshold after all costs
"""

import time
from typing import Dict, List, Any, Optional

from src.strategies.base_strategy import BaseStrategy, TradingSignal
from src.utils.logger import get_logger

logger = get_logger(__name__)


class ArbitrageStrategy(BaseStrategy):
    """
    YES/NO Arbitrage Strategy.

    Scans for and exploits pricing inefficiencies in binary markets.
    Focus on short-term crypto markets (15-min BTC/ETH up/down) where
    pricing inefficiencies are more common due to high trading activity.

    This is a very low risk strategy when sum of prices < $1.
    """

    def __init__(
        self,
        polymarket,  # PolymarketClient
        config: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize arbitrage strategy.

        Args:
            polymarket: Polymarket API client
            config: Strategy configuration
        """
        super().__init__(name="arbitrage", config=config)

        self.polymarket = polymarket

        # Configuration with defaults
        self.min_spread = config.get("min_spread", 0.01)  # 1% minimum profit
        self.max_position_size = config.get("max_position_size", 5.0)  # $5 max
        self.min_position_size = config.get("min_position_size", 1.0)  # $1 min
        self.order_type = config.get("order_type", "maker")  # Prefer maker
        self.maker_buffer = config.get("maker_buffer", 0.005)  # 0.5% buffer
        self.order_timeout = config.get("order_timeout", 60)  # seconds

        # Focus on crypto markets for higher frequency opportunities
        self.target_categories = ["Crypto", "Bitcoin", "Ethereum"]

        # Track active arbitrage opportunities
        self._active_arbs: Dict[str, Dict[str, Any]] = {}

        # Stats
        self._opportunities_found = 0
        self._arbs_executed = 0

        logger.info(
            f"ArbitrageStrategy initialized (min_spread={self.min_spread}, "
            f"max_size=${self.max_position_size})"
        )

    def evaluate(
        self,
        markets: List[Any],
        positions: List[Any],
        balance: float,
    ) -> List[Dict[str, Any]]:
        """
        Evaluate markets for arbitrage opportunities.

        Args:
            markets: List of Market objects
            positions: Current open positions
            balance: Available balance

        Returns:
            List of trading signals
        """
        self._last_evaluation = time.time()
        signals = []

        # Filter to relevant markets
        filtered_markets = self._filter_arb_markets(markets)

        for market in filtered_markets:
            opportunity = self._check_arbitrage(market)

            if opportunity:
                self._opportunities_found += 1

                # Create signals for both sides
                arb_signals = self._create_arb_signals(
                    opportunity=opportunity,
                    balance=balance,
                    positions=positions,
                )

                signals.extend(arb_signals)

        return [s.to_dict() for s in signals]

    def _filter_arb_markets(self, markets: List[Any]) -> List[Any]:
        """Filter markets suitable for arbitrage."""
        filtered = []

        for market in markets:
            # Must be active binary market
            if not market.active:
                continue

            if len(market.outcomes) != 2:
                continue

            # Prefer crypto markets
            is_crypto = any(
                cat.lower() in market.category.lower()
                for cat in self.target_categories
            )

            # Check liquidity
            if market.liquidity < 100:
                continue

            # Short-term markets have more inefficiencies
            # (15-min, 1-hour crypto price markets)

            filtered.append(market)

        return filtered

    def _check_arbitrage(self, market: Any) -> Optional[Dict[str, Any]]:
        """
        Check if a market has an arbitrage opportunity.

        An arbitrage exists when:
        YES price + NO price < $1 - min_spread - fees

        Args:
            market: Market to check

        Returns:
            Opportunity dict or None
        """
        try:
            # Get prices
            yes_price = market.outcome_prices.get("Yes", 0.5)
            no_price = market.outcome_prices.get("No", 0.5)

            # Get token IDs
            yes_token = market.tokens.get("Yes")
            no_token = market.tokens.get("No")

            if not yes_token or not no_token:
                return None

            # Calculate total cost for one share of each
            total_cost = yes_price + no_price

            # Profit per pair (one side pays $1)
            raw_profit = 1.0 - total_cost

            # Account for maker rebate on both sides
            # (We place limit orders for both YES and NO)
            fee_impact = 2 * self.MAKER_REBATE  # Rebate on both orders

            # Net profit after fees
            net_profit = raw_profit + fee_impact

            # Check threshold
            if net_profit < self.min_spread:
                return None

            # Valid arbitrage opportunity!
            logger.info(
                f"ARB FOUND: {market.question[:50]}... "
                f"YES={yes_price:.4f} + NO={no_price:.4f} = {total_cost:.4f} "
                f"(profit={net_profit:.4f} = {net_profit*100:.1f}%)"
            )

            return {
                "market_id": market.condition_id,
                "market_question": market.question,
                "yes_price": yes_price,
                "no_price": no_price,
                "yes_token": yes_token,
                "no_token": no_token,
                "total_cost": total_cost,
                "raw_profit": raw_profit,
                "net_profit": net_profit,
                "profit_pct": net_profit * 100,
                "timestamp": time.time(),
            }

        except Exception as e:
            logger.debug(f"Error checking arb for {market.condition_id}: {e}")
            return None

    def _create_arb_signals(
        self,
        opportunity: Dict[str, Any],
        balance: float,
        positions: List[Any],
    ) -> List[TradingSignal]:
        """
        Create trading signals for an arbitrage opportunity.

        For a proper arbitrage, we need to buy equal amounts of
        both YES and NO tokens.

        Args:
            opportunity: Arbitrage opportunity dict
            balance: Available balance
            positions: Current positions

        Returns:
            List of TradingSignal objects
        """
        signals = []

        # Check if we already have position in this market
        market_id = opportunity["market_id"]
        for pos in positions:
            if pos.market_id == market_id:
                logger.debug(f"Already have position in {market_id[:10]}")
                return []

        # Calculate position size
        # We need to buy equal amounts of YES and NO
        # Total cost = (YES price + NO price) * size
        total_cost_per_pair = opportunity["total_cost"]

        # Available for arb (split between YES and NO)
        max_pairs = min(
            balance * 0.5 / total_cost_per_pair,  # Use max 50% of balance for one arb
            self.max_position_size / total_cost_per_pair,
        )

        # Round to reasonable size
        num_pairs = max(1, min(max_pairs, 10))  # 1-10 share pairs

        # Size per side (buying num_pairs of each)
        yes_size = num_pairs
        no_size = num_pairs

        # Adjust prices for maker orders
        # Post slightly worse prices to ensure maker status
        yes_buy_price = opportunity["yes_price"] - self.maker_buffer
        no_buy_price = opportunity["no_price"] - self.maker_buffer

        # Create YES signal
        yes_signal = self.create_signal(
            market_id=market_id,
            token_id=opportunity["yes_token"],
            outcome="Yes",
            price=yes_buy_price,
            ev=opportunity["net_profit"] / 2,  # Half the EV per side
            confidence=1.0,  # Arbitrage is certain
            reason=f"Arb YES side: total_cost={opportunity['total_cost']:.4f}",
            balance=balance,
            size=yes_size * yes_buy_price,
            urgency="high",
            time_horizon_seconds=3600,  # Hold until resolution
        )
        signals.append(yes_signal)

        # Create NO signal
        no_signal = self.create_signal(
            market_id=market_id,
            token_id=opportunity["no_token"],
            outcome="No",
            price=no_buy_price,
            ev=opportunity["net_profit"] / 2,
            confidence=1.0,
            reason=f"Arb NO side: total_cost={opportunity['total_cost']:.4f}",
            balance=balance,
            size=no_size * no_buy_price,
            urgency="high",
            time_horizon_seconds=3600,
        )
        signals.append(no_signal)

        # Track this arbitrage
        self._active_arbs[market_id] = {
            "opportunity": opportunity,
            "yes_signal": yes_signal,
            "no_signal": no_signal,
            "created_at": time.time(),
        }

        self._arbs_executed += 1

        logger.info(
            f"ARB SIGNALS: {market_id[:10]} "
            f"YES ${yes_signal.size:.2f} @ {yes_buy_price:.4f}, "
            f"NO ${no_signal.size:.2f} @ {no_buy_price:.4f} "
            f"(total profit=${opportunity['net_profit'] * num_pairs:.2f})"
        )

        return signals

    def get_active_arbitrages(self) -> Dict[str, Dict[str, Any]]:
        """Get currently active arbitrage positions."""
        return self._active_arbs.copy()

    def get_stats(self) -> Dict[str, Any]:
        """Get strategy statistics."""
        stats = super().get_stats()
        stats.update({
            "opportunities_found": self._opportunities_found,
            "arbs_executed": self._arbs_executed,
            "active_arbs": len(self._active_arbs),
            "min_spread": self.min_spread,
            "max_position": self.max_position_size,
        })
        return stats

    def scan_all_markets(self) -> List[Dict[str, Any]]:
        """
        Scan all markets for arbitrage opportunities (utility method).

        Returns:
            List of all found opportunities (without executing)
        """
        opportunities = []

        try:
            markets = self.polymarket.get_markets(active_only=True)

            for market in markets:
                opp = self._check_arbitrage(market)
                if opp:
                    opportunities.append(opp)

        except Exception as e:
            logger.error(f"Error scanning markets: {e}")

        return opportunities
