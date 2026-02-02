"""
Position Manager

Tracks open positions, calculates P&L, and manages position lifecycle.

Key responsibilities:
- Track all open positions
- Calculate unrealized P&L
- Handle position opens/closes
- Monitor for market resolution
- Export position history for analysis
"""

import time
import csv
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
import json

from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class Position:
    """Represents an open position."""
    # Identifiers
    position_id: str
    market_id: str
    token_id: str
    outcome: str  # "Yes" or "No"

    # Position details
    size: float  # Number of contracts
    entry_price: float  # Average entry price
    entry_cost: float  # Total cost to enter

    # Current state
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0

    # Metadata
    strategy: str = ""
    market_question: str = ""
    entry_time: float = field(default_factory=time.time)
    last_update: float = field(default_factory=time.time)
    tags: Dict[str, Any] = field(default_factory=dict)

    def update_price(self, new_price: float) -> None:
        """Update current price and recalculate P&L."""
        self.current_price = new_price
        self.last_update = time.time()

        # Calculate unrealized P&L
        # For a long position (bought outcome), profit if price goes up
        current_value = self.size * new_price
        self.unrealized_pnl = current_value - self.entry_cost

        if self.entry_cost > 0:
            self.unrealized_pnl_pct = (self.unrealized_pnl / self.entry_cost) * 100


@dataclass
class ClosedPosition:
    """Represents a closed (realized) position."""
    position_id: str
    market_id: str
    token_id: str
    outcome: str

    # Entry
    size: float
    entry_price: float
    entry_cost: float
    entry_time: float

    # Exit
    exit_price: float
    exit_value: float
    exit_time: float

    # P&L
    realized_pnl: float
    realized_pnl_pct: float

    # Resolution
    resolution: Optional[str] = None  # "win", "loss", or None if sold
    strategy: str = ""
    market_question: str = ""


class PositionManager:
    """
    Manages all trading positions.

    Tracks both open positions and closed position history.
    Provides real-time P&L calculation and position analytics.

    For our $50 starting capital strategy, careful position tracking
    is essential to monitor compounding growth.
    """

    def __init__(
        self,
        api_client,  # PolymarketClient for price updates
        history_file: str = "logs/position_history.csv",
    ):
        """
        Initialize position manager.

        Args:
            api_client: Polymarket client for price updates
            history_file: Path to save closed position history
        """
        self.api_client = api_client
        self.history_file = Path(history_file)

        # Ensure directory exists
        self.history_file.parent.mkdir(parents=True, exist_ok=True)

        # Position storage
        self._open_positions: Dict[str, Position] = {}
        self._closed_positions: List[ClosedPosition] = []
        self._lock = Lock()

        # Statistics
        self._total_realized_pnl: float = 0.0
        self._total_trades: int = 0
        self._winning_trades: int = 0

        # Load history if exists
        self._load_history()

        logger.info(
            f"PositionManager initialized (history_file={history_file}, "
            f"historical_trades={len(self._closed_positions)})"
        )

    def _load_history(self) -> None:
        """Load closed position history from file."""
        if not self.history_file.exists():
            return

        try:
            with open(self.history_file, "r") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    closed = ClosedPosition(
                        position_id=row["position_id"],
                        market_id=row["market_id"],
                        token_id=row.get("token_id", ""),
                        outcome=row["outcome"],
                        size=float(row["size"]),
                        entry_price=float(row["entry_price"]),
                        entry_cost=float(row["entry_cost"]),
                        entry_time=float(row["entry_time"]),
                        exit_price=float(row["exit_price"]),
                        exit_value=float(row["exit_value"]),
                        exit_time=float(row["exit_time"]),
                        realized_pnl=float(row["realized_pnl"]),
                        realized_pnl_pct=float(row["realized_pnl_pct"]),
                        resolution=row.get("resolution"),
                        strategy=row.get("strategy", ""),
                        market_question=row.get("market_question", ""),
                    )
                    self._closed_positions.append(closed)
                    self._total_realized_pnl += closed.realized_pnl
                    self._total_trades += 1
                    if closed.realized_pnl > 0:
                        self._winning_trades += 1

            logger.info(f"Loaded {len(self._closed_positions)} historical positions")

        except Exception as e:
            logger.error(f"Failed to load position history: {e}")

    def _save_to_history(self, closed: ClosedPosition) -> None:
        """Save closed position to history file."""
        try:
            file_exists = self.history_file.exists()

            with open(self.history_file, "a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=[
                    "position_id", "market_id", "token_id", "outcome",
                    "size", "entry_price", "entry_cost", "entry_time",
                    "exit_price", "exit_value", "exit_time",
                    "realized_pnl", "realized_pnl_pct",
                    "resolution", "strategy", "market_question",
                ])

                if not file_exists:
                    writer.writeheader()

                writer.writerow({
                    "position_id": closed.position_id,
                    "market_id": closed.market_id,
                    "token_id": closed.token_id,
                    "outcome": closed.outcome,
                    "size": closed.size,
                    "entry_price": closed.entry_price,
                    "entry_cost": closed.entry_cost,
                    "entry_time": closed.entry_time,
                    "exit_price": closed.exit_price,
                    "exit_value": closed.exit_value,
                    "exit_time": closed.exit_time,
                    "realized_pnl": closed.realized_pnl,
                    "realized_pnl_pct": closed.realized_pnl_pct,
                    "resolution": closed.resolution or "",
                    "strategy": closed.strategy,
                    "market_question": closed.market_question,
                })

        except Exception as e:
            logger.error(f"Failed to save position history: {e}")

    def open_position(
        self,
        market_id: str,
        token_id: str,
        outcome: str,
        size: float,
        price: float,
        strategy: str = "",
        market_question: str = "",
        tags: Optional[Dict[str, Any]] = None,
    ) -> Position:
        """
        Open a new position.

        Args:
            market_id: Market condition ID
            token_id: Token ID
            outcome: "Yes" or "No"
            size: Position size in contracts
            price: Entry price
            strategy: Strategy that opened this position
            market_question: Market question text
            tags: Additional metadata

        Returns:
            New Position object
        """
        import uuid
        position_id = str(uuid.uuid4())

        position = Position(
            position_id=position_id,
            market_id=market_id,
            token_id=token_id,
            outcome=outcome,
            size=size,
            entry_price=price,
            entry_cost=size * price,
            current_price=price,
            strategy=strategy,
            market_question=market_question,
            tags=tags or {},
        )

        with self._lock:
            self._open_positions[position_id] = position

        logger.info(
            f"Position opened: {position_id[:8]} {outcome} {size:.2f} @ {price:.4f} "
            f"(cost=${position.entry_cost:.2f}, strategy={strategy})"
        )

        return position

    def close_position(
        self,
        position_id: str,
        exit_price: float,
        resolution: Optional[str] = None,  # "win", "loss", or None
    ) -> Optional[ClosedPosition]:
        """
        Close an open position.

        Args:
            position_id: Position to close
            exit_price: Price at which position was closed
            resolution: Whether market resolved in our favor

        Returns:
            ClosedPosition or None if position not found
        """
        with self._lock:
            if position_id not in self._open_positions:
                logger.warning(f"Position not found: {position_id}")
                return None

            position = self._open_positions.pop(position_id)

        # Calculate realized P&L
        exit_value = position.size * exit_price
        realized_pnl = exit_value - position.entry_cost
        realized_pnl_pct = (realized_pnl / position.entry_cost * 100) if position.entry_cost > 0 else 0

        closed = ClosedPosition(
            position_id=position.position_id,
            market_id=position.market_id,
            token_id=position.token_id,
            outcome=position.outcome,
            size=position.size,
            entry_price=position.entry_price,
            entry_cost=position.entry_cost,
            entry_time=position.entry_time,
            exit_price=exit_price,
            exit_value=exit_value,
            exit_time=time.time(),
            realized_pnl=realized_pnl,
            realized_pnl_pct=realized_pnl_pct,
            resolution=resolution,
            strategy=position.strategy,
            market_question=position.market_question,
        )

        # Update stats
        self._closed_positions.append(closed)
        self._total_realized_pnl += realized_pnl
        self._total_trades += 1
        if realized_pnl > 0:
            self._winning_trades += 1

        # Save to history
        self._save_to_history(closed)

        logger.info(
            f"Position closed: {position_id[:8]} P&L=${realized_pnl:.2f} "
            f"({realized_pnl_pct:.1f}%, resolution={resolution})"
        )

        return closed

    def add_to_position(
        self,
        position_id: str,
        additional_size: float,
        price: float,
    ) -> bool:
        """
        Add to an existing position (average up/down).

        Args:
            position_id: Existing position ID
            additional_size: Size to add
            price: Price of additional shares

        Returns:
            True if successful
        """
        with self._lock:
            if position_id not in self._open_positions:
                return False

            position = self._open_positions[position_id]

            # Calculate new average price
            total_cost = position.entry_cost + (additional_size * price)
            total_size = position.size + additional_size
            new_avg_price = total_cost / total_size if total_size > 0 else 0

            # Update position
            position.size = total_size
            position.entry_price = new_avg_price
            position.entry_cost = total_cost

        logger.info(
            f"Position added: {position_id[:8]} +{additional_size:.2f} @ {price:.4f} "
            f"(new avg={new_avg_price:.4f}, total_size={total_size:.2f})"
        )

        return True

    def update_prices(self) -> None:
        """Update current prices for all open positions."""
        with self._lock:
            positions = list(self._open_positions.values())

        for position in positions:
            try:
                # Get current price from API
                price = self.api_client.get_midpoint_price(position.token_id)
                if price:
                    position.update_price(price)
            except Exception as e:
                logger.debug(f"Price update failed for {position.position_id[:8]}: {e}")

    def get_position(self, position_id: str) -> Optional[Position]:
        """Get position by ID."""
        return self._open_positions.get(position_id)

    def get_position_by_market(
        self,
        market_id: str,
        outcome: Optional[str] = None,
    ) -> Optional[Position]:
        """
        Get position by market ID and optionally outcome.

        Args:
            market_id: Market condition ID
            outcome: "Yes" or "No" (optional)

        Returns:
            Position or None
        """
        for position in self._open_positions.values():
            if position.market_id == market_id:
                if outcome is None or position.outcome == outcome:
                    return position
        return None

    def get_all_positions(
        self,
        strategy: Optional[str] = None,
    ) -> List[Position]:
        """
        Get all open positions.

        Args:
            strategy: Filter by strategy

        Returns:
            List of positions
        """
        with self._lock:
            positions = list(self._open_positions.values())

        if strategy:
            positions = [p for p in positions if p.strategy == strategy]

        return positions

    def get_total_unrealized_pnl(self) -> float:
        """Get total unrealized P&L across all positions."""
        self.update_prices()
        return sum(p.unrealized_pnl for p in self._open_positions.values())

    def get_total_exposure(self) -> float:
        """Get total exposure (entry cost) across all positions."""
        return sum(p.entry_cost for p in self._open_positions.values())

    def get_position_count(self) -> int:
        """Get number of open positions."""
        return len(self._open_positions)

    def get_stats(self) -> Dict[str, Any]:
        """
        Get position statistics.

        Returns:
            Dict with position metrics
        """
        open_positions = list(self._open_positions.values())

        return {
            "open_positions": len(open_positions),
            "total_exposure": sum(p.entry_cost for p in open_positions),
            "total_unrealized_pnl": sum(p.unrealized_pnl for p in open_positions),
            "total_realized_pnl": self._total_realized_pnl,
            "total_trades": self._total_trades,
            "winning_trades": self._winning_trades,
            "win_rate": (self._winning_trades / self._total_trades * 100)
                       if self._total_trades > 0 else 0,
            "avg_pnl_per_trade": (self._total_realized_pnl / self._total_trades)
                                if self._total_trades > 0 else 0,
        }

    def get_strategy_stats(self, strategy: str) -> Dict[str, Any]:
        """
        Get statistics for a specific strategy.

        Args:
            strategy: Strategy name

        Returns:
            Dict with strategy-specific metrics
        """
        strategy_closed = [c for c in self._closed_positions if c.strategy == strategy]
        strategy_open = [p for p in self._open_positions.values() if p.strategy == strategy]

        total_pnl = sum(c.realized_pnl for c in strategy_closed)
        wins = sum(1 for c in strategy_closed if c.realized_pnl > 0)

        return {
            "strategy": strategy,
            "open_positions": len(strategy_open),
            "closed_trades": len(strategy_closed),
            "total_realized_pnl": total_pnl,
            "winning_trades": wins,
            "win_rate": (wins / len(strategy_closed) * 100) if strategy_closed else 0,
            "avg_pnl": total_pnl / len(strategy_closed) if strategy_closed else 0,
        }

    def check_resolutions(self) -> List[ClosedPosition]:
        """
        Check for market resolutions and close winning/losing positions.

        Returns:
            List of positions that were resolved
        """
        resolved = []

        with self._lock:
            positions = list(self._open_positions.values())

        for position in positions:
            try:
                # Check market status
                market = self.api_client.get_market(position.market_id)

                if market and not market.active:
                    # Market has resolved
                    # Determine if we won or lost
                    winning_outcome = None  # Would need resolution data

                    # For now, use price as proxy (price = 1 means won)
                    final_price = market.outcome_prices.get(position.outcome, 0.5)

                    if final_price >= 0.95:
                        # We won - position worth $1 per share
                        resolution = "win"
                        exit_price = 1.0
                    elif final_price <= 0.05:
                        # We lost - position worth $0
                        resolution = "loss"
                        exit_price = 0.0
                    else:
                        # Still undetermined
                        continue

                    closed = self.close_position(
                        position.position_id,
                        exit_price=exit_price,
                        resolution=resolution,
                    )
                    if closed:
                        resolved.append(closed)

            except Exception as e:
                logger.debug(f"Resolution check failed for {position.position_id[:8]}: {e}")

        if resolved:
            logger.info(f"Resolved {len(resolved)} positions")

        return resolved

    def export_summary(self, filepath: str = "logs/position_summary.json") -> None:
        """
        Export position summary to JSON.

        Args:
            filepath: Output file path
        """
        try:
            summary = {
                "timestamp": time.time(),
                "stats": self.get_stats(),
                "open_positions": [
                    {
                        "position_id": p.position_id,
                        "market_id": p.market_id,
                        "outcome": p.outcome,
                        "size": p.size,
                        "entry_price": p.entry_price,
                        "current_price": p.current_price,
                        "unrealized_pnl": p.unrealized_pnl,
                        "strategy": p.strategy,
                    }
                    for p in self._open_positions.values()
                ],
                "recent_closed": [
                    {
                        "position_id": c.position_id,
                        "outcome": c.outcome,
                        "realized_pnl": c.realized_pnl,
                        "resolution": c.resolution,
                        "strategy": c.strategy,
                    }
                    for c in self._closed_positions[-20:]  # Last 20
                ],
            }

            Path(filepath).parent.mkdir(parents=True, exist_ok=True)
            with open(filepath, "w") as f:
                json.dump(summary, f, indent=2)

            logger.info(f"Position summary exported to {filepath}")

        except Exception as e:
            logger.error(f"Failed to export summary: {e}")
