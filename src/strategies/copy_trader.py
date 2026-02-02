"""
Selective Copy Trading Strategy

Mirrors positions of top Polymarket traders with an edge filter.
Only copies trades that align with our fair value estimate and
meet minimum EV thresholds.

Inspired by portfolio analysis techniques from openclaw/polyskills.

How it works:
1. Monitor top 5-10 traders via leaderboard API
2. Detect when they open new positions
3. Filter for crypto markets only
4. Check if trade has positive EV based on our fair value
5. Mirror proportionally (0.1% of their size, min $1)
6. Use maker orders when possible

Key considerations:
- Don't blindly copy - filter for +EV opportunities
- Small position sizes relative to copied traders
- Focus on traders with proven track records
- Delay copies slightly to avoid front-running detection
"""

import time
from typing import Dict, List, Any, Optional, Set
from dataclasses import dataclass

from src.strategies.base_strategy import BaseStrategy, TradingSignal
from src.api.gamma_api import TraderProfile, TraderPosition
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class TrackedTrader:
    """Represents a trader we're monitoring."""
    profile: TraderProfile
    known_positions: Set[str]  # Set of market_ids
    last_check: float
    copies_made: int = 0


class CopyTraderStrategy(BaseStrategy):
    """
    Selective Copy Trading Strategy.

    Monitors top Polymarket traders and mirrors their positions
    when they align with positive expected value.

    This is a medium-risk strategy that leverages the research
    and analysis of successful traders while maintaining our
    own edge requirements.
    """

    def __init__(
        self,
        polymarket,  # PolymarketClient
        gamma_api,  # GammaAPIClient
        config: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize copy trading strategy.

        Args:
            polymarket: Polymarket API client
            gamma_api: Gamma API client for leaderboard
            config: Strategy configuration
        """
        super().__init__(name="copy_trader", config=config)

        self.polymarket = polymarket
        self.gamma_api = gamma_api

        # Configuration
        self.num_traders = config.get("num_traders", 10)
        self.min_win_rate = config.get("min_win_rate", 0.55)  # 55% win rate
        self.min_edge = config.get("min_edge", 0.04)  # 4% edge required
        self.copy_fraction = config.get("copy_fraction", 0.001)  # 0.1% of their size
        self.min_copy_size = config.get("min_copy_size", 1.0)  # $1 minimum
        self.max_copy_size = config.get("max_copy_size", 5.0)  # $5 maximum
        self.copy_delay = config.get("copy_delay", 5)  # 5 second delay
        self.refresh_interval = config.get("refresh_interval", 3600)  # 1 hour
        self.allowed_categories = config.get("allowed_categories", ["Crypto"])

        # Tracked traders
        self._tracked_traders: Dict[str, TrackedTrader] = {}
        self._last_trader_refresh = 0

        # Copy queue (for delay implementation)
        self._pending_copies: List[Dict[str, Any]] = []

        # Stats
        self._copies_executed = 0
        self._copies_filtered = 0

        logger.info(
            f"CopyTraderStrategy initialized "
            f"(num_traders={self.num_traders}, min_edge={self.min_edge*100}%)"
        )

    def evaluate(
        self,
        markets: List[Any],
        positions: List[Any],
        balance: float,
    ) -> List[Dict[str, Any]]:
        """
        Evaluate for copy trading opportunities.

        Args:
            markets: List of Market objects
            positions: Current open positions
            balance: Available balance

        Returns:
            List of trading signals
        """
        self._last_evaluation = time.time()
        signals = []

        # Refresh trader list if needed
        if self._should_refresh_traders():
            self._refresh_tracked_traders()

        # Process any pending delayed copies
        delayed_signals = self._process_pending_copies(markets, balance)
        signals.extend(delayed_signals)

        # Check each tracked trader for new positions
        for trader_addr, tracked in self._tracked_traders.items():
            new_positions = self._check_new_positions(tracked)

            for new_pos in new_positions:
                # Filter and potentially copy
                copy_signal = self._evaluate_copy(
                    position=new_pos,
                    trader=tracked,
                    markets=markets,
                    balance=balance,
                    existing_positions=positions,
                )

                if copy_signal:
                    # Add to pending queue with delay
                    self._pending_copies.append({
                        "signal": copy_signal,
                        "submit_time": time.time() + self.copy_delay,
                    })

        return [s.to_dict() for s in signals if s]

    def _should_refresh_traders(self) -> bool:
        """Check if we should refresh the trader list."""
        return (time.time() - self._last_trader_refresh) > self.refresh_interval

    def _refresh_tracked_traders(self) -> None:
        """Refresh the list of traders we're tracking."""
        try:
            top_traders = self.gamma_api.get_top_traders(
                num_traders=self.num_traders,
                min_win_rate=self.min_win_rate,
            )

            # Update tracked traders
            current_addrs = set(self._tracked_traders.keys())
            new_addrs = {t.address for t in top_traders}

            # Remove traders who fell off the list
            for addr in current_addrs - new_addrs:
                del self._tracked_traders[addr]
                logger.info(f"Stopped tracking trader: {addr[:10]}...")

            # Add new traders
            for trader in top_traders:
                if trader.address not in self._tracked_traders:
                    # Get their current positions to establish baseline
                    positions = self.gamma_api.get_trader_positions(trader.address)
                    known_markets = {p.market_id for p in positions}

                    self._tracked_traders[trader.address] = TrackedTrader(
                        profile=trader,
                        known_positions=known_markets,
                        last_check=time.time(),
                    )

                    logger.info(
                        f"Now tracking trader: {trader.username or trader.address[:10]} "
                        f"(win_rate={trader.win_rate*100:.1f}%, "
                        f"positions={len(known_markets)})"
                    )

            self._last_trader_refresh = time.time()

            logger.info(f"Tracking {len(self._tracked_traders)} top traders")

        except Exception as e:
            logger.error(f"Failed to refresh traders: {e}")

    def _check_new_positions(
        self,
        tracked: TrackedTrader,
    ) -> List[TraderPosition]:
        """
        Check for new positions from a tracked trader.

        Args:
            tracked: TrackedTrader object

        Returns:
            List of new positions
        """
        try:
            current_positions = self.gamma_api.get_trader_positions(
                tracked.profile.address
            )

            new_positions = []
            current_markets = set()

            for pos in current_positions:
                current_markets.add(pos.market_id)

                if pos.market_id not in tracked.known_positions:
                    new_positions.append(pos)
                    tracked.known_positions.add(pos.market_id)

                    logger.info(
                        f"New position detected: "
                        f"{tracked.profile.username or tracked.profile.address[:10]} "
                        f"entered {pos.outcome} on {pos.market_question[:30]}..."
                    )

            tracked.last_check = time.time()

            return new_positions

        except Exception as e:
            logger.debug(f"Failed to check positions for {tracked.profile.address[:10]}: {e}")
            return []

    def _evaluate_copy(
        self,
        position: TraderPosition,
        trader: TrackedTrader,
        markets: List[Any],
        balance: float,
        existing_positions: List[Any],
    ) -> Optional[TradingSignal]:
        """
        Evaluate whether to copy a position.

        Applies filters:
        - Category filter (crypto only)
        - EV filter (must meet minimum edge)
        - Position limit check

        Args:
            position: Position to potentially copy
            trader: Trader who opened the position
            markets: Available markets
            balance: Our balance
            existing_positions: Our current positions

        Returns:
            TradingSignal or None
        """
        # Check if we already have position
        for pos in existing_positions:
            if pos.market_id == position.market_id:
                logger.debug(f"Already have position in {position.market_id[:10]}")
                self._copies_filtered += 1
                return None

        # Find the market
        market = None
        for m in markets:
            if m.condition_id == position.market_id:
                market = m
                break

        if not market:
            # Try to fetch directly
            market = self.polymarket.get_market(position.market_id)

        if not market:
            logger.debug(f"Market not found: {position.market_id}")
            self._copies_filtered += 1
            return None

        # Category filter
        if self.allowed_categories:
            if not any(cat.lower() in market.category.lower()
                      for cat in self.allowed_categories):
                logger.debug(f"Category filtered: {market.category}")
                self._copies_filtered += 1
                return None

        # Get current price
        current_price = market.outcome_prices.get(position.outcome, 0.5)

        # Calculate our fair value estimate
        # Use a simple approach: trust the trader's entry if they're profitable
        trust_factor = min(1.0, trader.profile.win_rate / 0.55)  # Scale by win rate
        our_fair_value = current_price + (0.05 * trust_factor)  # Assume 5% edge

        # Calculate EV
        ev = self.calculate_ev(our_fair_value, current_price, is_maker=True)

        if ev < self.min_edge:
            logger.debug(
                f"Copy filtered: EV {ev:.3f} < {self.min_edge} for "
                f"{position.market_question[:30]}..."
            )
            self._copies_filtered += 1
            return None

        # Calculate copy size
        # Proportional to trader's position, with min/max limits
        copy_size = position.size * self.copy_fraction
        copy_size = max(self.min_copy_size, min(copy_size, self.max_copy_size))

        # Get token ID
        token_id = market.tokens.get(position.outcome)
        if not token_id:
            return None

        # Create signal
        signal = self.create_signal(
            market_id=position.market_id,
            token_id=token_id,
            outcome=position.outcome,
            price=current_price,
            ev=ev,
            confidence=trust_factor * 0.8,  # Scale confidence by trust
            reason=(
                f"Copy {trader.profile.username or trader.profile.address[:10]} "
                f"(win_rate={trader.profile.win_rate*100:.1f}%)"
            ),
            balance=balance,
            size=copy_size,
            urgency="normal",
        )

        tracker = self._tracked_traders.get(trader.profile.address)
        if tracker:
            tracker.copies_made += 1

        logger.info(
            f"COPY SIGNAL: {position.outcome} ${copy_size:.2f} @ {current_price:.4f} "
            f"(copying {trader.profile.username or trader.profile.address[:10]}, "
            f"EV={ev:.3f})"
        )

        return signal

    def _process_pending_copies(
        self,
        markets: List[Any],
        balance: float,
    ) -> List[TradingSignal]:
        """
        Process delayed copy signals.

        Returns signals that have passed their delay period.
        """
        ready = []
        still_pending = []
        current_time = time.time()

        for pending in self._pending_copies:
            if current_time >= pending["submit_time"]:
                ready.append(pending["signal"])
                self._copies_executed += 1
            else:
                still_pending.append(pending)

        self._pending_copies = still_pending

        return ready

    def get_tracked_traders(self) -> List[Dict[str, Any]]:
        """Get information about tracked traders."""
        return [
            {
                "address": addr,
                "username": tracked.profile.username,
                "win_rate": tracked.profile.win_rate,
                "pnl": tracked.profile.total_pnl,
                "positions_known": len(tracked.known_positions),
                "copies_made": tracked.copies_made,
            }
            for addr, tracked in self._tracked_traders.items()
        ]

    def get_stats(self) -> Dict[str, Any]:
        """Get strategy statistics."""
        stats = super().get_stats()
        stats.update({
            "tracked_traders": len(self._tracked_traders),
            "copies_executed": self._copies_executed,
            "copies_filtered": self._copies_filtered,
            "pending_copies": len(self._pending_copies),
            "min_edge_required": self.min_edge,
            "copy_fraction": self.copy_fraction,
        })
        return stats

    def force_refresh(self) -> None:
        """Force refresh of tracked traders."""
        self._last_trader_refresh = 0
        self._refresh_tracked_traders()
