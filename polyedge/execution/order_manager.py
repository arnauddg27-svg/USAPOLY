import time
import logging
from polyedge.models import OpenOrder

logger = logging.getLogger(__name__)


class OrderManager:
    """Tracks open orders and handles TTL-based expiry cancellation."""

    def __init__(self, poly_client):
        self.poly = poly_client
        self.open_orders: dict[str, OpenOrder] = {}

    def track(self, order: OpenOrder) -> None:
        """Add an order to the tracked set."""
        self.open_orders[order.order_id] = order
        logger.debug("Tracking order %s (%s)", order.order_id, order.token_id)

    def check_expiry(self, close_before_event_sec: int = 0) -> list[OpenOrder]:
        """Cancel and remove orders that have exceeded their TTL.

        Returns the list of orders that were cancelled.
        """
        now = time.time()
        close_before_event_sec = max(0, int(close_before_event_sec))
        cancelled: list[OpenOrder] = []

        for oid, order in list(self.open_orders.items()):
            ttl_expired = now - order.placed_at > order.ttl_sec
            is_pre_event_window = (
                close_before_event_sec > 0
                and order.event_start_ts is not None
                and now >= order.event_start_ts - close_before_event_sec
            )
            if ttl_expired or is_pre_event_window:
                try:
                    self.poly.orders.cancel(oid, {})
                    reason = "expired" if ttl_expired else "pre-event"
                    logger.info("Cancelled %s order %s", reason, oid)
                    del self.open_orders[oid]
                    cancelled.append(order)
                except Exception as e:
                    logger.warning("Cancel failed for %s — keeping tracked: %s", oid, e)

        return cancelled

    def remove(self, order_id: str) -> None:
        """Remove an order from tracking (e.g. after fill confirmation)."""
        self.open_orders.pop(order_id, None)

    def has_position(self, condition_id: str) -> bool:
        """Check whether any tracked order exists for the given condition."""
        return any(
            o.condition_id == condition_id for o in self.open_orders.values()
        )

    @property
    def count(self) -> int:
        """Number of currently tracked open orders."""
        return len(self.open_orders)
