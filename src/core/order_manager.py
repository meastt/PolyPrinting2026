"""
Order Manager

Handles order lifecycle management including:
- Order creation and submission
- Order tracking and status updates
- Order cancellation
- Fill management
- Timeout handling

Prioritizes maker orders over taker orders to earn rebates
and avoid the 3% taker fee.
"""

import time
import uuid
from typing import Optional, Dict, List, Callable, Any
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
import threading

from src.utils.logger import get_logger

logger = get_logger(__name__)


class OrderType(Enum):
    """Order type enumeration."""
    LIMIT = "limit"  # Maker order (earns rebates)
    MARKET = "market"  # Taker order (3% fee - avoid!)


class OrderSide(Enum):
    """Order side enumeration."""
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(Enum):
    """Order status enumeration."""
    PENDING = "pending"  # Created, not yet submitted
    SUBMITTED = "submitted"  # Submitted to exchange
    LIVE = "live"  # On the orderbook
    PARTIAL = "partial"  # Partially filled
    FILLED = "filled"  # Completely filled
    CANCELLED = "cancelled"  # Cancelled
    EXPIRED = "expired"  # Timed out
    REJECTED = "rejected"  # Rejected by exchange
    FAILED = "failed"  # Failed to submit


@dataclass
class ManagedOrder:
    """Represents an order managed by the order manager."""
    # Core fields
    local_id: str  # Our internal ID
    exchange_id: Optional[str] = None  # Exchange-assigned ID
    market_id: str = ""
    token_id: str = ""
    side: OrderSide = OrderSide.BUY
    order_type: OrderType = OrderType.LIMIT
    price: float = 0.0
    size: float = 0.0

    # Status tracking
    status: OrderStatus = OrderStatus.PENDING
    filled_size: float = 0.0
    remaining_size: float = 0.0
    avg_fill_price: float = 0.0

    # Timestamps
    created_at: float = field(default_factory=time.time)
    submitted_at: Optional[float] = None
    filled_at: Optional[float] = None
    cancelled_at: Optional[float] = None

    # Metadata
    strategy: str = ""  # Which strategy created this order
    timeout_seconds: float = 60.0  # Cancel if not filled
    tags: Dict[str, Any] = field(default_factory=dict)

    def is_active(self) -> bool:
        """Check if order is still active."""
        return self.status in [
            OrderStatus.PENDING,
            OrderStatus.SUBMITTED,
            OrderStatus.LIVE,
            OrderStatus.PARTIAL,
        ]

    def is_complete(self) -> bool:
        """Check if order is in a terminal state."""
        return self.status in [
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.EXPIRED,
            OrderStatus.REJECTED,
            OrderStatus.FAILED,
        ]


@dataclass
class OrderFill:
    """Represents a fill (partial or complete) of an order."""
    order_id: str
    fill_id: str
    price: float
    size: float
    timestamp: float
    fee: float = 0.0
    rebate: float = 0.0
    is_maker: bool = True


class OrderManager:
    """
    Manages the complete order lifecycle.

    Key features:
    - Tracks all orders with local IDs
    - Handles order submission via API client
    - Monitors for fills and updates
    - Cancels stale orders (timeout handling)
    - Tracks maker rebates earned

    The bot prioritizes maker orders to earn rebates (up to 100%
    in early phases) rather than paying the 3% taker fee.
    """

    def __init__(
        self,
        api_client,  # PolymarketClient
        default_timeout: float = 60.0,
    ):
        """
        Initialize order manager.

        Args:
            api_client: Polymarket API client for order operations
            default_timeout: Default order timeout in seconds
        """
        self.api_client = api_client
        self.default_timeout = default_timeout

        # Order storage
        self._orders: Dict[str, ManagedOrder] = {}
        self._lock = Lock()

        # Fills tracking
        self._fills: List[OrderFill] = []

        # Rebate tracking
        self._total_rebates: float = 0.0
        self._total_fees: float = 0.0

        # Callbacks
        self._fill_callbacks: List[Callable[[OrderFill], None]] = []
        self._status_callbacks: List[Callable[[ManagedOrder], None]] = []

        # Background monitoring
        self._monitor_thread: Optional[threading.Thread] = None
        self._running = False

        logger.info(f"OrderManager initialized (default_timeout={default_timeout}s)")

    def create_order(
        self,
        market_id: str,
        token_id: str,
        side: OrderSide,
        price: float,
        size: float,
        order_type: OrderType = OrderType.LIMIT,
        strategy: str = "",
        timeout: Optional[float] = None,
        tags: Optional[Dict[str, Any]] = None,
    ) -> ManagedOrder:
        """
        Create a new managed order.

        Args:
            market_id: Market condition ID
            token_id: Token to trade
            side: BUY or SELL
            price: Limit price (ignored for market orders)
            size: Order size in contracts
            order_type: LIMIT (maker) or MARKET (taker)
            strategy: Strategy name for tracking
            timeout: Override default timeout
            tags: Additional metadata

        Returns:
            ManagedOrder object
        """
        local_id = str(uuid.uuid4())

        order = ManagedOrder(
            local_id=local_id,
            market_id=market_id,
            token_id=token_id,
            side=side,
            order_type=order_type,
            price=price,
            size=size,
            remaining_size=size,
            strategy=strategy,
            timeout_seconds=timeout or self.default_timeout,
            tags=tags or {},
        )

        with self._lock:
            self._orders[local_id] = order

        logger.info(
            f"Order created: {local_id[:8]} {side.value} {size:.2f} @ {price:.4f} "
            f"({order_type.value}, strategy={strategy})"
        )

        return order

    def submit_order(self, order: ManagedOrder) -> bool:
        """
        Submit an order to the exchange.

        Args:
            order: Order to submit

        Returns:
            True if submission successful
        """
        if order.status != OrderStatus.PENDING:
            logger.warning(f"Cannot submit order {order.local_id[:8]}: status={order.status}")
            return False

        try:
            # Submit via API client
            if order.order_type == OrderType.LIMIT:
                result = self.api_client.place_limit_order(
                    token_id=order.token_id,
                    side=order.side.value,
                    price=order.price,
                    size=order.size,
                )
            else:
                result = self.api_client.place_market_order(
                    token_id=order.token_id,
                    side=order.side.value,
                    size=order.size,
                )

            if result:
                order.exchange_id = result.order_id
                order.status = OrderStatus.LIVE if order.order_type == OrderType.LIMIT else OrderStatus.FILLED
                order.submitted_at = time.time()

                # Market orders fill immediately
                if order.order_type == OrderType.MARKET:
                    order.filled_size = order.size
                    order.remaining_size = 0
                    order.filled_at = time.time()
                    self._record_fill(order, order.size, order.price)

                logger.info(
                    f"Order submitted: {order.local_id[:8]} -> exchange_id={order.exchange_id}"
                )
                return True
            else:
                order.status = OrderStatus.FAILED
                logger.error(f"Order submission failed: {order.local_id[:8]}")
                return False

        except Exception as e:
            order.status = OrderStatus.FAILED
            logger.error(f"Order submission error: {e}")
            return False

    def cancel_order(self, order: ManagedOrder, reason: str = "") -> bool:
        """
        Cancel an order.

        Args:
            order: Order to cancel
            reason: Reason for cancellation

        Returns:
            True if cancellation successful
        """
        if not order.is_active():
            return True  # Already done

        try:
            if order.exchange_id:
                success = self.api_client.cancel_order(order.exchange_id)
                if success:
                    order.status = OrderStatus.CANCELLED
                    order.cancelled_at = time.time()
                    logger.info(
                        f"Order cancelled: {order.local_id[:8]} ({reason})"
                    )
                    return True
            else:
                # Not yet submitted, just mark as cancelled
                order.status = OrderStatus.CANCELLED
                order.cancelled_at = time.time()
                return True

        except Exception as e:
            logger.error(f"Cancel error for {order.local_id[:8]}: {e}")

        return False

    def cancel_all_orders(self, strategy: Optional[str] = None) -> int:
        """
        Cancel all active orders.

        Args:
            strategy: Only cancel orders from this strategy (optional)

        Returns:
            Number of orders cancelled
        """
        cancelled = 0

        with self._lock:
            active_orders = [
                o for o in self._orders.values()
                if o.is_active() and (strategy is None or o.strategy == strategy)
            ]

        for order in active_orders:
            if self.cancel_order(order, "cancel_all"):
                cancelled += 1

        logger.info(f"Cancelled {cancelled} orders" +
                   (f" for strategy={strategy}" if strategy else ""))

        return cancelled

    def get_order(self, local_id: str) -> Optional[ManagedOrder]:
        """Get order by local ID."""
        return self._orders.get(local_id)

    def get_order_by_exchange_id(self, exchange_id: str) -> Optional[ManagedOrder]:
        """Get order by exchange ID."""
        for order in self._orders.values():
            if order.exchange_id == exchange_id:
                return order
        return None

    def get_active_orders(
        self,
        strategy: Optional[str] = None,
        market_id: Optional[str] = None,
    ) -> List[ManagedOrder]:
        """
        Get all active orders.

        Args:
            strategy: Filter by strategy
            market_id: Filter by market

        Returns:
            List of active orders
        """
        with self._lock:
            orders = [o for o in self._orders.values() if o.is_active()]

        if strategy:
            orders = [o for o in orders if o.strategy == strategy]
        if market_id:
            orders = [o for o in orders if o.market_id == market_id]

        return orders

    def update_order_status(self, local_id: str, new_status: OrderStatus) -> None:
        """
        Update order status.

        Args:
            local_id: Order local ID
            new_status: New status
        """
        order = self.get_order(local_id)
        if order:
            old_status = order.status
            order.status = new_status

            if new_status == OrderStatus.FILLED:
                order.filled_at = time.time()
                order.remaining_size = 0
                order.filled_size = order.size

            logger.debug(
                f"Order status update: {local_id[:8]} {old_status.value} -> {new_status.value}"
            )

            # Trigger callbacks
            for callback in self._status_callbacks:
                try:
                    callback(order)
                except Exception as e:
                    logger.error(f"Status callback error: {e}")

    def _record_fill(
        self,
        order: ManagedOrder,
        fill_size: float,
        fill_price: float,
    ) -> None:
        """Record an order fill."""
        # Calculate fees/rebates
        # Maker orders earn rebates, taker orders pay fees
        is_maker = order.order_type == OrderType.LIMIT
        fill_value = fill_size * fill_price

        if is_maker:
            # Maker rebate (currently up to 100% in promotional periods)
            rebate = fill_value * 0.01  # 1% rebate estimate
            fee = 0
            self._total_rebates += rebate
        else:
            # Taker fee
            fee = fill_value * 0.03  # 3% taker fee
            rebate = 0
            self._total_fees += fee

        fill = OrderFill(
            order_id=order.local_id,
            fill_id=str(uuid.uuid4()),
            price=fill_price,
            size=fill_size,
            timestamp=time.time(),
            fee=fee,
            rebate=rebate,
            is_maker=is_maker,
        )

        self._fills.append(fill)

        logger.info(
            f"Fill recorded: {order.local_id[:8]} {fill_size:.2f} @ {fill_price:.4f} "
            f"(maker={is_maker}, rebate=${rebate:.4f}, fee=${fee:.4f})"
        )

        # Trigger callbacks
        for callback in self._fill_callbacks:
            try:
                callback(fill)
            except Exception as e:
                logger.error(f"Fill callback error: {e}")

    def check_timeouts(self) -> List[ManagedOrder]:
        """
        Check for and cancel timed-out orders.

        Returns:
            List of orders that were expired
        """
        expired = []
        current_time = time.time()

        with self._lock:
            active_orders = [o for o in self._orders.values() if o.is_active()]

        for order in active_orders:
            if order.submitted_at:
                elapsed = current_time - order.submitted_at
                if elapsed > order.timeout_seconds:
                    order.status = OrderStatus.EXPIRED
                    self.cancel_order(order, "timeout")
                    expired.append(order)

        if expired:
            logger.info(f"Expired {len(expired)} timed-out orders")

        return expired

    def on_fill(self, callback: Callable[[OrderFill], None]) -> None:
        """Register a callback for order fills."""
        self._fill_callbacks.append(callback)

    def on_status_change(self, callback: Callable[[ManagedOrder], None]) -> None:
        """Register a callback for status changes."""
        self._status_callbacks.append(callback)

    def start_monitoring(self, interval: float = 5.0) -> None:
        """
        Start background order monitoring.

        Args:
            interval: Check interval in seconds
        """
        if self._running:
            return

        self._running = True

        def monitor_loop():
            while self._running:
                try:
                    # Check for timeouts
                    self.check_timeouts()

                    # Sync with exchange (if needed)
                    self._sync_with_exchange()

                except Exception as e:
                    logger.error(f"Monitoring error: {e}")

                time.sleep(interval)

        self._monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        self._monitor_thread.start()
        logger.info("Order monitoring started")

    def stop_monitoring(self) -> None:
        """Stop background monitoring."""
        self._running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
        logger.info("Order monitoring stopped")

    def _sync_with_exchange(self) -> None:
        """Sync order status with exchange."""
        # Get orders from exchange
        try:
            exchange_orders = self.api_client.get_orders(open_only=True)
            exchange_ids = {o.order_id for o in exchange_orders}

            # Check for orders that completed on exchange
            with self._lock:
                for order in self._orders.values():
                    if order.is_active() and order.exchange_id:
                        if order.exchange_id not in exchange_ids:
                            # Order no longer on exchange - assume filled
                            self.update_order_status(order.local_id, OrderStatus.FILLED)
                            self._record_fill(order, order.size, order.price)

        except Exception as e:
            logger.debug(f"Exchange sync error: {e}")

    def get_fill_stats(self) -> Dict[str, Any]:
        """
        Get fill statistics.

        Returns:
            Dict with fill metrics
        """
        total_fills = len(self._fills)
        maker_fills = sum(1 for f in self._fills if f.is_maker)
        total_volume = sum(f.size * f.price for f in self._fills)

        return {
            "total_fills": total_fills,
            "maker_fills": maker_fills,
            "taker_fills": total_fills - maker_fills,
            "maker_ratio": maker_fills / total_fills if total_fills > 0 else 0,
            "total_volume": total_volume,
            "total_rebates": self._total_rebates,
            "total_fees": self._total_fees,
            "net_fee_impact": self._total_rebates - self._total_fees,
        }

    def get_pending_orders_value(self) -> float:
        """Get total value of pending orders."""
        total = 0
        for order in self.get_active_orders():
            total += order.remaining_size * order.price
        return total

    def cleanup_old_orders(self, max_age_hours: int = 24) -> int:
        """
        Remove old completed orders from memory.

        Args:
            max_age_hours: Remove orders older than this

        Returns:
            Number of orders removed
        """
        cutoff = time.time() - (max_age_hours * 3600)
        removed = 0

        with self._lock:
            to_remove = [
                oid for oid, order in self._orders.items()
                if order.is_complete() and order.created_at < cutoff
            ]

            for oid in to_remove:
                del self._orders[oid]
                removed += 1

        if removed:
            logger.info(f"Cleaned up {removed} old orders")

        return removed
