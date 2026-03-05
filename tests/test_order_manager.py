import time
from unittest.mock import MagicMock

from polyedge.execution.order_manager import OrderManager
from polyedge.models import OpenOrder


def _expired_order(order_id: str = "ord1") -> OpenOrder:
    return OpenOrder(
        order_id=order_id,
        token_id="tok1",
        condition_id="cond1",
        risk_event_id="risk-cond1",
        sport="basketball_nba",
        side="BUY",
        price=0.5,
        size=10,
        placed_at=time.time() - 120,
        ttl_sec=30,
        original_edge=0.05,
        amount_usd=12.34,
    )


def test_check_expiry_cancels_with_clob_cancel_api():
    mock_poly = MagicMock()
    mgr = OrderManager(mock_poly)
    mgr.track(_expired_order())

    cancelled = mgr.check_expiry()

    mock_poly.cancel.assert_called_once_with("ord1")
    assert len(cancelled) == 1
    assert cancelled[0].order_id == "ord1"
    assert cancelled[0].amount_usd == 12.34
    assert "ord1" not in mgr.open_orders


def test_check_expiry_keeps_order_when_cancel_fails():
    mock_poly = MagicMock()
    mock_poly.cancel.side_effect = Exception("temporary API error")
    mgr = OrderManager(mock_poly)
    mgr.track(_expired_order())

    cancelled = mgr.check_expiry()

    assert cancelled == []
    assert "ord1" in mgr.open_orders


def test_check_expiry_cancels_before_event_start_window():
    mock_poly = MagicMock()
    mgr = OrderManager(mock_poly)
    order = OpenOrder(
        order_id="ord2",
        token_id="tok2",
        condition_id="cond2",
        risk_event_id="risk-cond2",
        sport="basketball_nba",
        side="BUY",
        price=0.45,
        size=20,
        placed_at=time.time(),
        ttl_sec=3600,
        original_edge=0.07,
        amount_usd=9.0,
        event_start_ts=time.time() + 45,
    )
    mgr.track(order)

    cancelled = mgr.check_expiry(close_before_event_sec=60)

    mock_poly.cancel.assert_called_once_with("ord2")
    assert len(cancelled) == 1
    assert cancelled[0].order_id == "ord2"
