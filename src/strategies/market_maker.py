"""
Maker Market Making Strategy

Earns rebates by providing liquidity through resting limit orders.
Posts bid and ask orders around fair value, earning maker rebates
(up to 100% in promotional periods) instead of paying taker fees.

How it works:
1. Calculate fair value from external price feeds
2. Post bid (buy) orders slightly below fair value
3. Post ask (sell) orders slightly above fair value
4. When filled, earn maker rebates on each side
5. Rebalance positions and re-quote as prices move

Key advantages:
- Earns rebates instead of paying fees
- Can profit from bid-ask spread
- Provides liquidity to the market

Post-2026 fee considerations:
- Maker rebates are key to profitability
- Focus on high-volume periods for more fills
- Avoid taker orders at all costs
"""

import time
from typing import Dict, List, Any, Optional, Tuple

from src.strategies.base_strategy import BaseStrategy, TradingSignal
from src.utils.logger import get_logger

logger = get_logger(__name__)


class MarketMakerStrategy(BaseStrategy):
    """
    Maker Market Making Strategy.

    Provides liquidity in Polymarket crypto markets by posting
    resting limit orders around fair value. Earns maker rebates
    on each fill.

    This is a relatively low-risk strategy when fair value is
    accurately estimated and positions are properly hedged.
    """

    def __init__(
        self,
        polymarket,  # PolymarketClient
        price_feeds,  # PriceFeedAggregator
        config: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize market making strategy.

        Args:
            polymarket: Polymarket API client
            price_feeds: Price feed aggregator for fair value
            config: Strategy configuration
        """
        super().__init__(name="market_maker", config=config)

        self.polymarket = polymarket
        self.price_feeds = price_feeds

        # Configuration
        self.spread_offset = config.get("spread_offset", 0.02)  # 2% from FV
        self.order_size = config.get("order_size", 2.0)  # $2 per side
        self.num_levels = config.get("num_levels", 1)  # Price levels
        self.level_spacing = config.get("level_spacing", 0.01)  # 1% between levels
        self.min_edge = config.get("min_edge", 0.005)  # Min 0.5% edge
        self.rebalance_threshold = config.get("rebalance_threshold", 0.02)
        self.max_inventory_ratio = config.get("max_inventory_ratio", 3.0)

        # Track active quotes
        self._active_quotes: Dict[str, Dict[str, Any]] = {}

        # Inventory tracking (net position)
        self._inventory: Dict[str, float] = {}

        # Daily rebate tracking
        self._daily_rebates = 0.0
        self._last_rebate_reset = time.time()

        # Stats
        self._quotes_posted = 0
        self._fills_received = 0

        logger.info(
            f"MarketMakerStrategy initialized (spread={self.spread_offset*100}%, "
            f"order_size=${self.order_size})"
        )

    def evaluate(
        self,
        markets: List[Any],
        positions: List[Any],
        balance: float,
    ) -> List[Dict[str, Any]]:
        """
        Evaluate markets and generate market making signals.

        Args:
            markets: List of Market objects
            positions: Current open positions
            balance: Available balance

        Returns:
            List of trading signals
        """
        self._last_evaluation = time.time()
        signals = []

        # Reset daily rebates if new day
        self._check_daily_reset()

        # Filter to markets we can make markets in
        mm_markets = self._filter_mm_markets(markets)

        for market in mm_markets:
            # Calculate fair value
            fair_value = self._calculate_fair_value(market)

            if fair_value is None:
                continue

            # Check existing quotes
            existing = self._active_quotes.get(market.condition_id)

            if existing:
                # Check if need to rebalance
                if self._should_rebalance(market, existing, fair_value):
                    # Cancel existing quotes
                    self._cancel_quotes(market.condition_id)
                else:
                    continue  # Keep existing quotes

            # Generate new quotes
            quote_signals = self._create_quote_signals(
                market=market,
                fair_value=fair_value,
                balance=balance,
                positions=positions,
            )

            signals.extend(quote_signals)

        return [s.to_dict() for s in signals]

    def _filter_mm_markets(self, markets: List[Any]) -> List[Any]:
        """Filter markets suitable for market making."""
        filtered = []

        for market in markets:
            # Must be active
            if not market.active:
                continue

            # Binary markets only
            if len(market.outcomes) != 2:
                continue

            # Need sufficient liquidity
            if market.liquidity < 500:
                continue

            # Focus on crypto markets where we have price feeds
            if "crypto" not in market.category.lower() and \
               "bitcoin" not in market.category.lower() and \
               "ethereum" not in market.category.lower():
                continue

            filtered.append(market)

        return filtered

    def _calculate_fair_value(self, market: Any) -> Optional[float]:
        """
        Calculate fair value for a market using external price feeds.

        For crypto price markets (e.g., "Will BTC be above $X?"),
        we use current spot prices to estimate probabilities.

        Args:
            market: Market to value

        Returns:
            Fair value for YES outcome (0-1) or None
        """
        try:
            question = market.question.lower()

            # Extract crypto asset from question
            asset = None
            if "btc" in question or "bitcoin" in question:
                asset = "BTC"
            elif "eth" in question or "ethereum" in question:
                asset = "ETH"
            elif "sol" in question or "solana" in question:
                asset = "SOL"

            if not asset:
                # Use market price as estimate if no external data
                return market.outcome_prices.get("Yes", 0.5)

            # Get current price
            price_data = self.price_feeds.get_price(asset)
            if not price_data:
                return None

            current_price = price_data.price

            # Simple fair value model
            # For "price above X" markets: use probability based on distance
            # This is simplified - real models would use volatility

            # Default to market mid if can't determine direction
            yes_price = market.outcome_prices.get("Yes", 0.5)
            no_price = market.outcome_prices.get("No", 0.5)
            market_mid = (yes_price + (1 - no_price)) / 2

            # Use slightly adjusted market mid as our fair value estimate
            fair_value = market_mid

            logger.debug(
                f"Fair value for {market.condition_id[:10]}: "
                f"{fair_value:.4f} (market_mid={market_mid:.4f})"
            )

            return fair_value

        except Exception as e:
            logger.debug(f"Fair value calculation error: {e}")
            return None

    def _should_rebalance(
        self,
        market: Any,
        existing: Dict[str, Any],
        new_fair_value: float,
    ) -> bool:
        """
        Check if existing quotes should be cancelled and replaced.

        Rebalance when:
        - Fair value moved significantly
        - Inventory too imbalanced
        - Quotes are stale
        """
        old_fv = existing.get("fair_value", 0.5)
        fv_change = abs(new_fair_value - old_fv)

        if fv_change > self.rebalance_threshold:
            logger.debug(f"Rebalancing: FV moved {fv_change:.4f}")
            return True

        # Check quote age
        quote_age = time.time() - existing.get("created_at", 0)
        if quote_age > 300:  # 5 minutes
            logger.debug("Rebalancing: quotes stale")
            return True

        return False

    def _cancel_quotes(self, market_id: str) -> None:
        """Cancel existing quotes for a market."""
        if market_id in self._active_quotes:
            del self._active_quotes[market_id]
            logger.debug(f"Cancelled quotes for {market_id[:10]}")

    def _create_quote_signals(
        self,
        market: Any,
        fair_value: float,
        balance: float,
        positions: List[Any],
    ) -> List[TradingSignal]:
        """
        Create bid and ask quote signals around fair value.

        Args:
            market: Market to quote
            fair_value: Our fair value estimate
            balance: Available balance
            positions: Current positions

        Returns:
            List of TradingSignal objects
        """
        signals = []

        # Get token IDs
        yes_token = market.tokens.get("Yes")
        no_token = market.tokens.get("No")

        if not yes_token or not no_token:
            return []

        # Check inventory
        net_inventory = self._get_net_inventory(market.condition_id)

        # Calculate quote prices
        bid_price = fair_value - self.spread_offset  # We buy YES
        ask_price = fair_value + self.spread_offset  # We sell YES (buy NO at 1-ask)

        # Ensure prices are valid
        bid_price = max(0.01, min(0.99, bid_price))
        ask_price = max(0.01, min(0.99, ask_price))

        # Calculate EV for each side
        # Bid: We buy at bid_price, expect FV
        bid_ev = self.calculate_ev(fair_value, bid_price, is_maker=True)

        # Ask: We sell at ask_price (buy NO at 1-ask), expect 1-FV for NO
        no_fair_value = 1 - fair_value
        no_buy_price = 1 - ask_price
        ask_ev = self.calculate_ev(no_fair_value, no_buy_price, is_maker=True)

        # Adjust sizes based on inventory
        bid_size = self._adjust_size_for_inventory(
            base_size=self.order_size,
            net_inventory=net_inventory,
            is_buy=True,
        )

        ask_size = self._adjust_size_for_inventory(
            base_size=self.order_size,
            net_inventory=net_inventory,
            is_buy=False,
        )

        # Create bid signal (buy YES) if EV positive
        if bid_ev > self.min_edge and bid_size > 0:
            bid_signal = self.create_signal(
                market_id=market.condition_id,
                token_id=yes_token,
                outcome="Yes",
                price=bid_price,
                ev=bid_ev,
                confidence=0.8,  # MM is probabilistic
                reason=f"MM bid: FV={fair_value:.4f}, spread={self.spread_offset:.3f}",
                balance=balance,
                size=bid_size,
            )
            signals.append(bid_signal)

        # Create ask signal (buy NO) if EV positive
        if ask_ev > self.min_edge and ask_size > 0:
            ask_signal = self.create_signal(
                market_id=market.condition_id,
                token_id=no_token,
                outcome="No",
                price=no_buy_price,
                ev=ask_ev,
                confidence=0.8,
                reason=f"MM ask: FV={fair_value:.4f}, spread={self.spread_offset:.3f}",
                balance=balance,
                size=ask_size,
            )
            signals.append(ask_signal)

        # Track quotes
        if signals:
            self._active_quotes[market.condition_id] = {
                "fair_value": fair_value,
                "bid_price": bid_price,
                "ask_price": ask_price,
                "created_at": time.time(),
            }
            self._quotes_posted += len(signals)

        return signals

    def _get_net_inventory(self, market_id: str) -> float:
        """Get net inventory for a market (positive = long YES)."""
        return self._inventory.get(market_id, 0.0)

    def _adjust_size_for_inventory(
        self,
        base_size: float,
        net_inventory: float,
        is_buy: bool,
    ) -> float:
        """
        Adjust order size based on current inventory.

        Reduce size on the side that would increase imbalance.
        """
        # Calculate inventory ratio
        inventory_ratio = abs(net_inventory) / base_size if base_size > 0 else 0

        if inventory_ratio > self.max_inventory_ratio:
            # Too imbalanced - only allow reducing positions
            if is_buy and net_inventory > 0:
                return 0  # Already long, don't buy more
            if not is_buy and net_inventory < 0:
                return 0  # Already short, don't sell more

        # Gradually reduce size as inventory grows
        adjustment = 1 - (inventory_ratio / (self.max_inventory_ratio * 2))
        adjustment = max(0.25, min(1.0, adjustment))

        return base_size * adjustment

    def _check_daily_reset(self) -> None:
        """Reset daily rebate tracking at midnight."""
        now = time.time()
        if now - self._last_rebate_reset > 86400:  # 24 hours
            logger.info(f"Daily rebates: ${self._daily_rebates:.2f}")
            self._daily_rebates = 0.0
            self._last_rebate_reset = now

    def record_fill(
        self,
        market_id: str,
        outcome: str,
        size: float,
        is_buy: bool,
    ) -> None:
        """
        Record a fill and update inventory.

        Args:
            market_id: Market identifier
            outcome: "Yes" or "No"
            size: Fill size
            is_buy: Whether we bought
        """
        # Update inventory
        if market_id not in self._inventory:
            self._inventory[market_id] = 0.0

        inventory_delta = size if is_buy else -size
        if outcome == "No":
            inventory_delta = -inventory_delta  # NO is inverse of YES

        self._inventory[market_id] += inventory_delta

        # Track rebate
        estimated_rebate = size * self.MAKER_REBATE
        self._daily_rebates += estimated_rebate

        self._fills_received += 1

        logger.info(
            f"MM fill: {outcome} {'buy' if is_buy else 'sell'} {size:.2f} "
            f"(inventory={self._inventory[market_id]:.2f}, "
            f"rebate=${estimated_rebate:.4f})"
        )

    def get_stats(self) -> Dict[str, Any]:
        """Get strategy statistics."""
        stats = super().get_stats()
        stats.update({
            "quotes_posted": self._quotes_posted,
            "fills_received": self._fills_received,
            "daily_rebates": self._daily_rebates,
            "active_quotes": len(self._active_quotes),
            "total_inventory_markets": len(self._inventory),
            "spread_offset": self.spread_offset,
        })
        return stats
