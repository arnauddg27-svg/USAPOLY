import pytest
from polyedge.data.odds_api import parse_all_books_response
from polyedge.data.polymarket import compute_avg_fill_price
from polyedge.pipeline.devig import devig
from polyedge.pipeline.aggregator import aggregate_probs
from polyedge.pipeline.matcher import match_events
from polyedge.pipeline.edge_detector import detect_edge
from polyedge.execution.sizing import compute_bet_size
from polyedge.models import (
    SportsOutcome, AllBookOdds, PolyMarket, BookLine,
    BookLevel, OrderBook, EdgeOpportunity, MatchedEvent,
    AggregatedProb, ConfidenceTier, EdgeSource,
)
from polyedge.config import EdgeConfig

def test_full_pipeline_finds_edge():
    """End-to-end: odds → devig → aggregate → match → detect → size."""
    # 1. Simulate 8 books with Celtics as ~62% favorite
    books = {}
    for i, (odds_a, odds_b) in enumerate([
        (-160, 140), (-155, 135), (-165, 145), (-150, 130),
        (-160, 140), (-158, 138), (-162, 142), (-155, 135),
    ]):
        name = f"Book{i}"
        books[name] = (
            SportsOutcome("Boston Celtics", odds_a, name),
            SportsOutcome("Los Angeles Lakers", odds_b, name),
        )
    game = AllBookOdds("basketball_nba", "Boston Celtics", "Los Angeles Lakers",
                       "2026-02-21T20:00:00Z", books)

    # 2. Polymarket has Celtics at 0.48 (underpriced vs true ~0.60)
    poly = PolyMarket("NBA: Celtics vs Lakers", "cond1", "Boston Celtics",
                      "Los Angeles Lakers", "tok_celtics", "tok_lakers")

    # 3. Match
    matches = match_events([game], [poly])
    assert len(matches) == 1

    # 4. Devig and aggregate
    lines = []
    for bk, (oa, ob) in game.books.items():
        pa, pb = devig(oa.decimal_odds, ob.decimal_odds, "power")
        lines.append(BookLine(bk, pa, pb, "power"))
    agg = aggregate_probs(lines, min_books=6)
    assert agg is not None
    assert agg.prob_a > 0.58  # Celtics should be ~60%

    # 5. Simulate order book — asks at 0.48 to create a clear edge
    book_a = OrderBook("tok_celtics", "Boston Celtics",
                       asks=[BookLevel(0.48, 300), BookLevel(0.49, 500)],
                       bids=[BookLevel(0.475, 400)])
    book_b = OrderBook("tok_lakers", "Los Angeles Lakers",
                       asks=[BookLevel(0.51, 500)],
                       bids=[BookLevel(0.505, 400)])

    # 6. Detect edge — true ~0.60, fill ~0.484, adj_edge ~0.10
    cfg = EdgeConfig()
    opps = detect_edge(matches[0], agg, book_a, book_b, cfg, hours_until=5.0)
    assert len(opps) >= 1
    opp = opps[0]
    assert opp.buy_outcome == "a"  # buy Celtics
    assert opp.adjusted_edge > 0.05

    # 7. Size
    bet = compute_bet_size(
        opp.adjusted_edge, opp.poly_fill_price, bankroll=1000,
        fraction_kelly=0.15, max_per_event_pct=0.02,
        total_exposure=0, max_total_pct=0.30, cash_buffer_pct=0.20,
        book_depth_usd=800*0.48, min_bet=5.0,
    )
    assert bet > 0

def test_full_pipeline_no_edge():
    """Efficient market — no edge detected."""
    books = {}
    for i in range(8):
        books[f"Book{i}"] = (
            SportsOutcome("TeamA", -110, f"Book{i}"),
            SportsOutcome("TeamB", -110, f"Book{i}"),
        )
    game = AllBookOdds("basketball_nba", "TeamA", "TeamB", "2026-02-21T20:00:00Z", books)
    poly = PolyMarket("Game", "cond2", "TeamA", "TeamB", "tok_a", "tok_b")
    matches = match_events([game], [poly])
    assert len(matches) == 1

    lines = []
    for bk, (oa, ob) in game.books.items():
        pa, pb = devig(oa.decimal_odds, ob.decimal_odds, "power")
        lines.append(BookLine(bk, pa, pb, "power"))
    agg = aggregate_probs(lines, min_books=6)

    # Polymarket at fair value (0.50)
    book_a = OrderBook("tok_a", "TeamA", asks=[BookLevel(0.50, 1000)], bids=[BookLevel(0.495, 800)])
    book_b = OrderBook("tok_b", "TeamB", asks=[BookLevel(0.50, 1000)], bids=[BookLevel(0.495, 800)])

    cfg = EdgeConfig()
    opps = detect_edge(matches[0], agg, book_a, book_b, cfg, hours_until=5.0)
    assert len(opps) == 0  # no edge
