import time
import logging
from polyedge.models import EdgeOpportunity, OpenOrder
from polyedge.config import EdgeConfig

logger = logging.getLogger(__name__)


class EdgeExecutor:
    """Places orders on Polymarket US based on identified edge opportunities."""

    def __init__(self, poly_client):
        self.poly = poly_client
        self.last_error = ""

    @staticmethod
    def _extract_order_id(result) -> str:
        if not isinstance(result, dict):
            if hasattr(result, "id"):
                return str(result.id).strip()
            return ""
        return str(
            result.get("orderID", result.get("order_id", result.get("orderId", result.get("id", ""))))
        ).strip()

    def place_order(self, opp: EdgeOpportunity, cfg: EdgeConfig) -> OpenOrder | None:
        """Build and submit a limit BUY order for the given opportunity."""
        if not cfg.trading_enabled:
            logger.info("Trading disabled — skipping %s", opp.buy_token_id)
            self.last_error = "trading_disabled"
            return None

        if opp.bet_usd <= 0 or opp.shares <= 0:
            logger.debug("Skipping order: bet_usd=%.2f shares=%d", opp.bet_usd, opp.shares)
            self.last_error = "invalid_order_size"
            return None

        if cfg.no_resting_orders:
            limit_price = round(float(opp.poly_fill_price), 4)
        else:
            limit_price = round(opp.poly_mid - cfg.order_offset, 4)
        limit_price = max(0.01, min(0.99, limit_price))

        configured_cap = float(getattr(cfg, "max_fill_price", 0.91))
        max_buy_price = max(0.01, min(0.99, configured_cap))
        if limit_price >= max_buy_price:
            self.last_error = f"buy_price_cap:{limit_price:.4f}>={max_buy_price:.4f}"
            logger.info(
                "Skipping order for %s due to buy cap (limit=%.4f cap=%.4f)",
                opp.buy_token_id, limit_price, max_buy_price,
            )
            return None

        # Determine order intent based on which outcome we're buying
        market_slug = getattr(opp.matched_event.poly_market, "market_slug", "")
        # outcome_a is typically "Yes"/Long, outcome_b is "No"/Short
        # If we're buying token_id_a, we want BUY_LONG; token_id_b means BUY_SHORT
        if opp.buy_token_id == opp.matched_event.poly_market.token_id_a:
            intent = "ORDER_INTENT_BUY_LONG"
        else:
            intent = "ORDER_INTENT_BUY_SHORT"

        tif = ("TIME_IN_FORCE_FILL_OR_KILL" if cfg.no_resting_orders
               else "TIME_IN_FORCE_GOOD_TILL_CANCEL")

        try:
            result = self.poly.orders.create({
                "marketSlug": market_slug,
                "intent": intent,
                "type": "ORDER_TYPE_LIMIT",
                "price": {"value": str(limit_price), "currency": "USD"},
                "quantity": int(opp.shares),
                "tif": tif,
            })
        except Exception as e:
            self.last_error = str(e)
            logger.error("Order failed for %s: %s", opp.buy_token_id, e)
            return None

        if not result:
            self.last_error = "empty_response"
            logger.warning("Order rejected for %s: empty response", opp.buy_token_id)
            return None

        order_id = self._extract_order_id(result)
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
            risk_event_id="",
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

    def place_cashout_order(self, *, token_id: str, size: float, price: float,
                            market_slug: str = "", sell_intent: str = "ORDER_INTENT_SELL_LONG") -> dict:
        token = str(token_id or "").strip()
        qty = float(size or 0.0)
        limit_price = round(float(price or 0.0), 4)
        limit_price = max(0.01, min(0.99, limit_price))

        if not token or qty <= 0:
            self.last_error = "invalid_cashout_size"
            return {"ok": False, "error": self.last_error}

        try:
            result = self.poly.orders.create({
                "marketSlug": market_slug,
                "intent": sell_intent,
                "type": "ORDER_TYPE_LIMIT",
                "price": {"value": str(limit_price), "currency": "USD"},
                "quantity": int(qty),
                "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
            })
        except Exception as exc:
            self.last_error = str(exc)
            return {"ok": False, "error": self.last_error}

        order_id = self._extract_order_id(result)
        if not order_id:
            self.last_error = "missing_order_id"
            return {"ok": False, "error": self.last_error, "raw": result}

        self.last_error = ""
        return {"ok": True, "order_id": order_id, "price": limit_price, "size": qty}
