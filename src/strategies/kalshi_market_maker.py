"""
Kalshi Market Maker Strategy

Provides liquidity on Kalshi markets to earn from the bid-ask spread.

Key advantage: Kalshi has ZERO fees on resting (maker) orders!
This means pure spread capture without fee erosion.
"""

import time
from typing import Dict, List, Any, Optional

from src.api.kalshi_client import KalshiClient, KalshiMarket
from src.api.price_feeds import PriceFeedAggregator
from src.strategies.base_strategy import BaseStrategy
from src.utils.logger import get_logger

logger = get_logger(__name__)


class KalshiMarketMakerStrategy(BaseStrategy):
    """
    Market Making Strategy for Kalshi.

    Provides liquidity by posting limit orders on both sides
    of markets, capturing the bid-ask spread.

    With Kalshi's zero maker fees, this strategy captures
    the full spread without fee erosion.
    """

    def __init__(
        self,
        kalshi: KalshiClient,
        price_feeds: PriceFeedAggregator,
        config: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize Kalshi Market Maker Strategy.

        Args:
            kalshi: Kalshi API client
            price_feeds: Price feeds for fair value calculation
            config: Strategy configuration
        """
        super().__init__(name="kalshi_market_maker", config=config)

        self.kalshi = kalshi
        self.price_feeds = price_feeds

        # Configuration
        config = config or {}
        self.spread_offset = config.get("spread_offset", 0.02)  # 2% from fair value
        self.order_size = config.get("order_size", 2.0)  # $2 per side
        self.min_edge = config.get("min_edge", 0.005)  # 0.5% minimum edge
        self.rebalance_threshold = config.get("rebalance_threshold", 0.02)
        self.max_inventory_ratio = config.get("max_inventory_ratio", 3.0)

        # Active quotes
        self._active_quotes: Dict[str, Dict[str, Any]] = {}

        # Stats
        self._quotes_placed = 0
        self._quotes_filled = 0
        self._spread_captured = 0.0

        logger.info(
            f"KalshiMarketMakerStrategy initialized "
            f"(spread_offset={self.spread_offset:.1%}, size=${self.order_size})"
        )

    def evaluate(
        self,
        markets: List[KalshiMarket],
        positions: List[Any],
        balance: float,
    ) -> List[Dict[str, Any]]:
        """
        Evaluate markets for market making opportunities.

        Args:
            markets: List of KalshiMarket objects
            positions: Current positions
            balance: Available balance

        Returns:
            List of trading signals (quote pairs)
        """
        self._last_evaluation = time.time()
        signals = []

        # Focus on liquid markets
        liquid_markets = self._filter_liquid_markets(markets)

        for market in liquid_markets:
            # Calculate fair value
            fair_value = self._calculate_fair_value(market)

            if fair_value is None:
                continue

            # Check inventory limits
            inventory_ok = self._check_inventory(market.ticker, positions)
            if not inventory_ok:
                continue

            # Generate quote signals
            quote_signals = self._generate_quotes(
                market=market,
                fair_value=fair_value,
                balance=balance,
            )

            signals.extend(quote_signals)

        return signals

    def _filter_liquid_markets(
        self,
        markets: List[KalshiMarket],
    ) -> List[KalshiMarket]:
        """Filter to markets with sufficient liquidity."""
        liquid = []

        for market in markets:
            if not market.is_active:
                continue

            # Need reasonable spread
            if market.spread > 0.10:  # Skip if spread > 10%
                continue

            # Need volume
            if market.volume_24h < 100:
                continue

            # Need time to expiry (avoid last-minute markets)
            if market.time_to_expiry_seconds < 600:  # 10 min minimum
                continue

            liquid.append(market)

        return liquid

    def _calculate_fair_value(self, market: KalshiMarket) -> Optional[float]:
        """
        Calculate fair value for a market.

        Uses spot price data when available for crypto markets.

        Args:
            market: Kalshi market

        Returns:
            Fair value (0-1) or None
        """
        # For crypto markets, try to use spot price data
        if "BTC" in market.ticker or "ETH" in market.ticker:
            asset = "BTC" if "BTC" in market.ticker else "ETH"

            fair_value = self.price_feeds.get_fair_value(
                symbol=asset,
                direction="up",  # Assuming "price up" markets
                time_horizon_minutes=int(market.time_to_expiry_seconds / 60),
            )

            if fair_value:
                return fair_value

        # Fall back to mid-price
        return market.mid_price

    def _check_inventory(
        self,
        ticker: str,
        positions: List[Any],
    ) -> bool:
        """Check if inventory is within limits."""
        for pos in positions:
            pos_ticker = getattr(pos, "ticker", None) or pos.get("ticker", "")
            if pos_ticker == ticker:
                # Check position size
                yes_count = getattr(pos, "yes_count", 0) or pos.get("yes_count", 0)
                no_count = getattr(pos, "no_count", 0) or pos.get("no_count", 0)

                net_position = abs(yes_count - no_count)
                max_allowed = self.order_size * self.max_inventory_ratio

                if net_position * 0.5 > max_allowed:  # Rough $ conversion
                    logger.debug(f"Inventory limit reached for {ticker}")
                    return False

        return True

    def _generate_quotes(
        self,
        market: KalshiMarket,
        fair_value: float,
        balance: float,
    ) -> List[Dict[str, Any]]:
        """
        Generate bid/ask quote signals.

        Args:
            market: Kalshi market
            fair_value: Calculated fair value
            balance: Available balance

        Returns:
            List of quote signals
        """
        signals = []

        # Calculate bid and ask prices
        bid_price = fair_value - self.spread_offset
        ask_price = fair_value + self.spread_offset

        # Clamp to valid range
        bid_price = max(0.01, min(0.99, bid_price))
        ask_price = max(0.01, min(0.99, ask_price))

        # Check if quotes have edge
        # Bid: we buy if price drops to our bid
        bid_edge = fair_value - bid_price
        # Ask: we sell if price rises to our ask
        ask_edge = ask_price - fair_value

        # Size management
        available_per_side = min(balance / 4, self.order_size)

        # Generate YES bid (we buy YES if price drops)
        if bid_edge >= self.min_edge and bid_price < market.yes_bid * 1.01:
            self._quotes_placed += 1
            signals.append({
                "ticker": market.ticker,
                "market_id": market.ticker,
                "side": "yes",
                "outcome": "YES",
                "price": bid_price,
                "size": available_per_side,
                "ev": bid_edge,
                "confidence": 0.7,
                "strategy": self.name,
                "reason": f"MM bid: YES @ {bid_price:.2f} (FV={fair_value:.2f})",
                "urgency": "low",
                "is_maker": True,
                "quote_type": "bid",
            })

        # Generate NO bid (equivalent to YES ask)
        no_bid_price = 1 - ask_price
        if ask_edge >= self.min_edge and no_bid_price < market.no_bid * 1.01:
            self._quotes_placed += 1
            signals.append({
                "ticker": market.ticker,
                "market_id": market.ticker,
                "side": "no",
                "outcome": "NO",
                "price": no_bid_price,
                "size": available_per_side,
                "ev": ask_edge,
                "confidence": 0.7,
                "strategy": self.name,
                "reason": f"MM bid: NO @ {no_bid_price:.2f} (FV={1-fair_value:.2f})",
                "urgency": "low",
                "is_maker": True,
                "quote_type": "bid",
            })

        return signals

    def get_stats(self) -> Dict[str, Any]:
        """Get strategy statistics."""
        stats = super().get_stats()
        stats.update({
            "quotes_placed": self._quotes_placed,
            "quotes_filled": self._quotes_filled,
            "spread_captured": self._spread_captured,
            "spread_offset": self.spread_offset,
            "order_size": self.order_size,
        })
        return stats
