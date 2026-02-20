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
        mock_poly.post_order.return_value = {"ok": True, "orderID": "order123"}
        executor = EdgeExecutor(mock_poly)
        cfg = EdgeConfig()
        cfg.trading_enabled = True
        opp = _make_opportunity()
        result = executor.place_order(opp, cfg)
        assert result is not None
        mock_poly.post_order.assert_called_once()

    def test_skips_when_trading_disabled(self):
        mock_poly = MagicMock()
        executor = EdgeExecutor(mock_poly)
        cfg = EdgeConfig()
        cfg.trading_enabled = False
        opp = _make_opportunity()
        result = executor.place_order(opp, cfg)
        assert result is None
        mock_poly.post_order.assert_not_called()

    def test_skips_when_zero_bet(self):
        mock_poly = MagicMock()
        executor = EdgeExecutor(mock_poly)
        cfg = EdgeConfig()
        cfg.trading_enabled = True
        opp = _make_opportunity(bet_usd=0.0, shares=0)
        result = executor.place_order(opp, cfg)
        assert result is None
        mock_poly.post_order.assert_not_called()

    def test_returns_none_on_api_exception(self):
        mock_poly = MagicMock()
        mock_poly.post_order.side_effect = Exception("API timeout")
        executor = EdgeExecutor(mock_poly)
        cfg = EdgeConfig()
        cfg.trading_enabled = True
        opp = _make_opportunity()
        result = executor.place_order(opp, cfg)
        assert result is None

    def test_returns_none_on_rejected_order(self):
        mock_poly = MagicMock()
        mock_poly.post_order.return_value = {"ok": False, "error": "insufficient funds"}
        executor = EdgeExecutor(mock_poly)
        cfg = EdgeConfig()
        cfg.trading_enabled = True
        opp = _make_opportunity()
        result = executor.place_order(opp, cfg)
        assert result is None

    def test_order_fields_correct(self):
        mock_poly = MagicMock()
        mock_poly.post_order.return_value = {"ok": True, "orderID": "ord_abc"}
        executor = EdgeExecutor(mock_poly)
        cfg = EdgeConfig()
        cfg.trading_enabled = True
        opp = _make_opportunity(edge=0.08, fill=0.50)
        result = executor.place_order(opp, cfg)
        assert result is not None
        assert result.order_id == "ord_abc"
        assert result.token_id == "tok_a"
        assert result.condition_id == "cond1"
        assert result.side == "BUY"
        assert result.size == 36
        assert result.original_edge == 0.08
        assert result.ttl_sec == cfg.order_ttl_sec

    def test_limit_price_clamped(self):
        mock_poly = MagicMock()
        mock_poly.post_order.return_value = {"ok": True, "orderID": "ord_x"}
        executor = EdgeExecutor(mock_poly)
        cfg = EdgeConfig()
        cfg.trading_enabled = True
        cfg.order_offset = 0.005
        opp = _make_opportunity(fill=0.55)
        # poly_mid=0.55, offset=0.005 => limit = 0.545
        result = executor.place_order(opp, cfg)
        assert result is not None
        assert 0.01 <= result.price <= 0.99
