import pytest
from polyedge.models import (
    MatchedEvent, AllBookOdds, SportsOutcome, PolyMarket,
    AggregatedProb, BookLine, OrderBook, BookLevel, EdgeOpportunity,
)
from polyedge.pipeline.edge_detector import detect_edge, check_gates
from polyedge.config import EdgeConfig

def _matched(prob_a=0.62):
    game = AllBookOdds("basketball_nba", "TeamA", "TeamB", "2026-02-21T12:00:00Z", {})
    poly = PolyMarket("Game", "cond1", "TeamA", "TeamB", "tok_a", "tok_b")
    agg = AggregatedProb(prob_a=prob_a, prob_b=1-prob_a, books_used=8,
                         outliers_dropped=0, method="power", per_book=[])
    return MatchedEvent("basketball_nba", game, poly, "TeamA", "TeamB"), agg

def _book(best_ask=0.55, depth=800):
    asks = [BookLevel(price=best_ask, size=depth)]
    bids = [BookLevel(price=best_ask - 0.008, size=500)]
    return OrderBook(token_id="tok_a", outcome_name="TeamA", asks=asks, bids=bids)

class TestEdgeDetection:
    def test_positive_edge_detected(self):
        matched, agg = _matched(0.62)
        book_a = _book(0.55, 800)
        cfg = EdgeConfig()
        opps = detect_edge(matched, agg, book_a, _book(0.40, 800), cfg)
        assert len(opps) >= 1
        opp = opps[0]
        assert opp.buy_outcome == "a"
        assert opp.adjusted_edge > 0.05

    def test_no_edge(self):
        matched, agg = _matched(0.56)
        book_a = _book(0.55, 800)
        cfg = EdgeConfig()
        opps = detect_edge(matched, agg, book_a, _book(0.44, 800), cfg)
        assert len(opps) == 0

    def test_both_sides_checked(self):
        matched, agg = _matched(0.40)
        book_a = _book(0.55, 800)
        book_b = _book(0.30, 800)
        cfg = EdgeConfig()
        opps = detect_edge(matched, agg, book_a, book_b, cfg)
        assert any(o.buy_outcome == "b" for o in opps)

class TestGates:
    def test_spread_gate_fails(self):
        cfg = EdgeConfig()
        book = OrderBook("tok", "A",
                         asks=[BookLevel(0.60, 500)],
                         bids=[BookLevel(0.50, 500)])
        gates = check_gates(
            adjusted_edge=0.06, books_used=8, depth=800,
            fill_price=0.60, book=book,
            hours_until=5.0, cfg=cfg,
        )
        assert gates["spread"]["passed"] is False

    def test_all_gates_pass(self):
        cfg = EdgeConfig()
        book = OrderBook("tok", "A",
                         asks=[BookLevel(0.55, 800)],
                         bids=[BookLevel(0.545, 500)])
        gates = check_gates(
            adjusted_edge=0.06, books_used=8, depth=800,
            fill_price=0.553, book=book,
            hours_until=5.0, cfg=cfg,
        )
        assert all(g["passed"] for g in gates.values())
