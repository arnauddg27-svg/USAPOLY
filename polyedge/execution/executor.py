import time
import logging
from polyedge.models import EdgeOpportunity, OpenOrder
from polyedge.config import EdgeConfig

logger = logging.getLogger(__name__)


class EdgeExecutor:
    """Places limit orders on Polymarket based on identified edge opportunities."""

    def __init__(self, poly_client):
        self.poly = poly_client

    def place_order(self, opp: EdgeOpportunity, cfg: EdgeConfig) -> OpenOrder | None:
        """Build and submit a limit BUY order for the given opportunity.

        Returns an OpenOrder on success, or None if trading is disabled,
        the opportunity is invalid, or the API call fails/rejects.
        """
        if not cfg.trading_enabled:
            logger.info("Trading disabled — skipping %s", opp.buy_token_id)
            return None

        if opp.bet_usd <= 0 or opp.shares <= 0:
            logger.debug(
                "Skipping order: bet_usd=%.2f shares=%d", opp.bet_usd, opp.shares
            )
            return None

        # Place the limit slightly below mid to avoid crossing the spread
        limit_price = round(opp.poly_mid - cfg.order_offset, 4)
        limit_price = max(0.01, min(0.99, limit_price))

        try:
            result = self.poly.post_order(
                token_id=opp.buy_token_id,
                side="BUY",
                price=limit_price,
                size=opp.shares,
                post_only=True,
            )
        except Exception as e:
            logger.error("Order failed for %s: %s", opp.buy_token_id, e)
            return None

        if not result or not result.get("ok"):
            logger.warning("Order rejected for %s: %s", opp.buy_token_id, result)
            return None

        order_id = result.get("orderID", result.get("order_id", ""))

        order = OpenOrder(
            order_id=order_id,
            token_id=opp.buy_token_id,
            condition_id=opp.matched_event.poly_market.condition_id,
            side="BUY",
            price=limit_price,
            size=opp.shares,
            placed_at=time.time(),
            ttl_sec=cfg.order_ttl_sec,
            original_edge=opp.adjusted_edge,
        )

        logger.info(
            "Placed order %s: %s %d @ %.4f (edge=%.4f)",
            order_id, opp.buy_token_id, opp.shares, limit_price, opp.adjusted_edge,
        )
        return order
