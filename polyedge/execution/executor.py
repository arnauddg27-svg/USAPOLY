import time
import logging
from polyedge.models import EdgeOpportunity, OpenOrder
from polyedge.config import EdgeConfig

logger = logging.getLogger(__name__)


class EdgeExecutor:
    """Places limit orders on Polymarket based on identified edge opportunities."""

    def __init__(self, poly_client):
        self.poly = poly_client
        self.last_error = ""

    def place_order(self, opp: EdgeOpportunity, cfg: EdgeConfig) -> OpenOrder | None:
        """Build and submit a limit BUY order for the given opportunity.

        Returns an OpenOrder on success, or None if trading is disabled,
        the opportunity is invalid, or the API call fails/rejects.
        """
        if not cfg.trading_enabled:
            logger.info("Trading disabled — skipping %s", opp.buy_token_id)
            self.last_error = "trading_disabled"
            return None

        if opp.bet_usd <= 0 or opp.shares <= 0:
            logger.debug(
                "Skipping order: bet_usd=%.2f shares=%d", opp.bet_usd, opp.shares
            )
            self.last_error = "invalid_order_size"
            return None

        # Two execution modes:
        # - resting quotes: maker-only post-only near mid
        # - no-resting: aggressive capped-price buy + immediate cancel of remainder (IOC-like)
        if cfg.no_resting_orders:
            limit_price = round(float(opp.poly_fill_price), 4)
        else:
            limit_price = round(opp.poly_mid - cfg.order_offset, 4)
        limit_price = max(0.01, min(0.99, limit_price))

        try:
            from py_clob_client.clob_types import MarketOrderArgs, OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY

            if cfg.no_resting_orders:
                # Use true taker-style execution with immediate-or-cancel semantics.
                signed = self.poly.create_market_order(
                    MarketOrderArgs(
                        token_id=opp.buy_token_id,
                        amount=round(float(opp.bet_usd), 6),
                        side=BUY,
                        price=limit_price,
                        order_type=OrderType.FOK,
                    )
                )
                result = self.poly.post_order(
                    signed,
                    orderType=OrderType.FOK,
                    post_only=False,
                )
            else:
                signed = self.poly.create_order(
                    OrderArgs(
                        price=limit_price,
                        size=opp.shares,
                        side=BUY,
                        token_id=opp.buy_token_id,
                    )
                )
                result = self.poly.post_order(
                    signed,
                    orderType=OrderType.GTC,
                    post_only=True,
                )
        except Exception as e:
            self.last_error = str(e)
            logger.error("Order failed for %s: %s", opp.buy_token_id, e)
            return None

        if not result:
            self.last_error = "empty_response"
            logger.warning("Order rejected for %s: empty response", opp.buy_token_id)
            return None

        if not isinstance(result, dict):
            self.last_error = f"non_dict_response:{type(result).__name__}"
            logger.warning("Order rejected for %s: unexpected response type %s",
                           opp.buy_token_id, type(result).__name__)
            return None

        order_id = str(result.get("orderID", result.get("order_id", result.get("orderId", "")))).strip()
        if not order_id:
            self.last_error = "missing_order_id"
            logger.warning("Order rejected for %s: missing order id in response %s",
                           opp.buy_token_id, result)
            return None
        self.last_error = ""

        order = OpenOrder(
            order_id=order_id,
            token_id=opp.buy_token_id,
            condition_id=opp.matched_event.poly_market.condition_id,
            sport=opp.matched_event.sport,
            side="BUY",
            price=limit_price,
            size=opp.shares,
            placed_at=time.time(),
            ttl_sec=cfg.order_ttl_sec,
            original_edge=opp.adjusted_edge,
            amount_usd=round(float(opp.bet_usd), 2),
        )

        logger.info(
            "Placed order %s: %s %d @ %.4f (edge=%.4f)",
            order_id, opp.buy_token_id, opp.shares, limit_price, opp.adjusted_edge,
        )
        return order
