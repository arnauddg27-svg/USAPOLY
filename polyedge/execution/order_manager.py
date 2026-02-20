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

    def check_expiry(self) -> list[str]:
        """Cancel and remove orders that have exceeded their TTL.

        Returns a list of order IDs that were cancelled.
        """
        now = time.time()
        cancelled: list[str] = []

        for oid, order in list(self.open_orders.items()):
            if now - order.placed_at > order.ttl_sec:
                try:
                    self.poly.cancel_order(oid)
                    logger.info("Cancelled expired order %s", oid)
                except Exception as e:
                    logger.warning("Cancel failed for %s: %s", oid, e)
                del self.open_orders[oid]
                cancelled.append(oid)

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
