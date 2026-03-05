import pytest
from unittest.mock import MagicMock, patch
from polyedge.execution.executor import EdgeExecutor
from polyedge.models import EdgeOpportunity, MatchedEvent, AllBookOdds, PolyMarket, AggregatedProb, OrderBook, BookLevel
from polyedge.config import EdgeConfig

def _make_opportunity(edge=0.06, fill=0.55, bet_usd=20.0, shares=36):
    game = AllBookOdds("nba", "A", "B", "2026-02-21T12:00:00Z", {})
    poly = PolyMarket("Game", "cond1", "A", "B", "tok_a", "tok_b")
    matched = MatchedEvent("nba", game, poly, "A", "B")
    agg = AggregatedProb(0.62, 0.38, 8, 0, "power", [])
    opp = EdgeOpportunity(
        matched_event=matched, aggregated=agg,
        buy_outcome="a", buy_token_id="tok_a",
        true_prob=0.62, poly_mid=0.55, poly_fill_price=fill,
        poly_depth_shares=800, poly_spread=0.005,
        raw_edge=0.07, adjusted_edge=edge,
        bet_usd=bet_usd, shares=shares,
    )
    return opp

class TestExecutor:
    def test_places_limit_order(self):
        mock_poly = MagicMock()
        mock_poly.create_order.return_value = {"signed": True}
        mock_poly.post_order.return_value = {"orderID": "order123"}
        executor = EdgeExecutor(mock_poly)
        cfg = EdgeConfig()
        cfg.trading_enabled = True
        cfg.no_resting_orders = False
        opp = _make_opportunity()
        result = executor.place_order(opp, cfg)
        assert result is not None
        mock_poly.create_order.assert_called_once()
        mock_poly.post_order.assert_called_once()

    def test_skips_when_trading_disabled(self):
        mock_poly = MagicMock()
        executor = EdgeExecutor(mock_poly)
        cfg = EdgeConfig()
        cfg.trading_enabled = False
        opp = _make_opportunity()
        result = executor.place_order(opp, cfg)
        assert result is None
        mock_poly.create_order.assert_not_called()

    def test_skips_when_zero_bet(self):
        mock_poly = MagicMock()
        executor = EdgeExecutor(mock_poly)
        cfg = EdgeConfig()
        cfg.trading_enabled = True
        opp = _make_opportunity(bet_usd=0.0, shares=0)
        result = executor.place_order(opp, cfg)
        assert result is None
        mock_poly.create_order.assert_not_called()

    def test_returns_none_on_api_exception(self):
        mock_poly = MagicMock()
        mock_poly.create_order.side_effect = Exception("API timeout")
        executor = EdgeExecutor(mock_poly)
        cfg = EdgeConfig()
        cfg.trading_enabled = True
        opp = _make_opportunity()
        result = executor.place_order(opp, cfg)
        assert result is None

    def test_returns_none_on_rejected_order(self):
        mock_poly = MagicMock()
        mock_poly.create_order.return_value = {"signed": True}
        mock_poly.post_order.return_value = None  # CLOB returns None on rejection
        executor = EdgeExecutor(mock_poly)
        cfg = EdgeConfig()
        cfg.trading_enabled = True
        opp = _make_opportunity()
        result = executor.place_order(opp, cfg)
        assert result is None

    def test_order_fields_correct(self):
        mock_poly = MagicMock()
        mock_poly.create_order.return_value = {"signed": True}
        mock_poly.post_order.return_value = {"orderID": "ord_abc"}
        executor = EdgeExecutor(mock_poly)
        cfg = EdgeConfig()
        cfg.trading_enabled = True
        opp = _make_opportunity(edge=0.08, fill=0.50)
        result = executor.place_order(opp, cfg)
        assert result is not None
        assert result.order_id == "ord_abc"
        assert result.token_id == "tok_a"
        assert result.condition_id == "cond1"
        assert result.sport == "nba"
        assert result.side == "BUY"
        assert result.size == 36
        assert result.original_edge == 0.08
        assert result.ttl_sec == cfg.order_ttl_sec
        assert result.amount_usd == 20.0

    def test_limit_price_clamped(self):
        mock_poly = MagicMock()
        mock_poly.create_order.return_value = {"signed": True}
        mock_poly.post_order.return_value = {"orderID": "ord_x"}
        executor = EdgeExecutor(mock_poly)
        cfg = EdgeConfig()
        cfg.trading_enabled = True
        cfg.order_offset = 0.005
        opp = _make_opportunity(fill=0.55)
        # poly_mid=0.55, offset=0.005 => limit = 0.545
        result = executor.place_order(opp, cfg)
        assert result is not None
        assert 0.01 <= result.price <= 0.99

    def test_no_resting_orders_uses_market_fok_mode(self):
        mock_poly = MagicMock()
        mock_poly.create_market_order.return_value = {"signed": True}
        mock_poly.post_order.return_value = {"orderID": "ord_ioc"}
        executor = EdgeExecutor(mock_poly)
        cfg = EdgeConfig()
        cfg.trading_enabled = True
        cfg.no_resting_orders = True
        opp = _make_opportunity(fill=0.44)

        result = executor.place_order(opp, cfg)

        assert result is not None
        mock_poly.create_market_order.assert_called_once()
        post_kwargs = mock_poly.post_order.call_args.kwargs
        assert post_kwargs["post_only"] is False
        assert post_kwargs["orderType"] == "FOK"
        mock_poly.cancel.assert_not_called()

    def test_resting_orders_mode_keeps_post_only_and_no_immediate_cancel(self):
        mock_poly = MagicMock()
        mock_poly.create_order.return_value = {"signed": True}
        mock_poly.post_order.return_value = {"orderID": "ord_rest"}
        executor = EdgeExecutor(mock_poly)
        cfg = EdgeConfig()
        cfg.trading_enabled = True
        cfg.no_resting_orders = False
        cfg.order_offset = 0.005
        opp = _make_opportunity(fill=0.44)

        result = executor.place_order(opp, cfg)

        assert result is not None
        mock_poly.post_order.assert_called_once_with({"signed": True}, orderType="GTC", post_only=True)
        mock_poly.cancel.assert_not_called()

    def test_returns_none_when_order_id_missing(self):
        mock_poly = MagicMock()
        mock_poly.create_order.return_value = {"signed": True}
        mock_poly.post_order.return_value = {"ok": True}
        executor = EdgeExecutor(mock_poly)
        cfg = EdgeConfig()
        cfg.trading_enabled = True
        opp = _make_opportunity()
        result = executor.place_order(opp, cfg)
        assert result is None

    def test_returns_none_on_non_dict_response(self):
        mock_poly = MagicMock()
        mock_poly.create_order.return_value = {"signed": True}
        mock_poly.post_order.return_value = "accepted"
        executor = EdgeExecutor(mock_poly)
        cfg = EdgeConfig()
        cfg.trading_enabled = True
        opp = _make_opportunity()
        result = executor.place_order(opp, cfg)
        assert result is None

    def test_sets_last_error_on_exception(self):
        mock_poly = MagicMock()
        mock_poly.create_order.side_effect = Exception("boom")
        executor = EdgeExecutor(mock_poly)
        cfg = EdgeConfig()
        cfg.trading_enabled = True
        cfg.no_resting_orders = False
        opp = _make_opportunity()
        result = executor.place_order(opp, cfg)
        assert result is None
        assert "boom" in executor.last_error

    def test_place_cashout_order_success(self):
        mock_poly = MagicMock()
        mock_poly.create_order.return_value = {"signed": True}
        mock_poly.post_order.return_value = {"orderID": "ord_cashout"}
        executor = EdgeExecutor(mock_poly)

        result = executor.place_cashout_order(token_id="tok_sell", size=40.0, price=0.99)

        assert result["ok"] is True
        assert result["order_id"] == "ord_cashout"
        mock_poly.create_order.assert_called_once()
        post_kwargs = mock_poly.post_order.call_args.kwargs
        assert post_kwargs["post_only"] is False
        assert post_kwargs["orderType"] == "GTC"

    def test_place_cashout_order_rejects_missing_order_id(self):
        mock_poly = MagicMock()
        mock_poly.create_order.return_value = {"signed": True}
        mock_poly.post_order.return_value = {"ok": True}
        executor = EdgeExecutor(mock_poly)

        result = executor.place_cashout_order(token_id="tok_sell", size=40.0, price=0.99)

        assert result["ok"] is False
        assert result["error"] == "missing_order_id"
