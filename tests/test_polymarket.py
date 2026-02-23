import pytest
from polyedge.models import BookLevel, OrderBook
from polyedge.data.polymarket import (
    compute_avg_fill_price,
    _extract_tradeable_markets,
    _parse_book_levels,
    _parse_outcomes_tokens,
)

class TestFillSimulation:
    def test_single_level_exact(self):
        asks = [BookLevel(price=0.55, size=500)]
        avg, filled = compute_avg_fill_price(asks, 500)
        assert abs(avg - 0.55) < 0.001
        assert abs(filled - 500) < 0.01

    def test_multi_level_walk(self):
        asks = [BookLevel(price=0.55, size=200), BookLevel(price=0.56, size=300)]
        avg, filled = compute_avg_fill_price(asks, 400)
        # 200*0.55 + 200*0.56 = 110 + 112 = 222, avg = 222/400 = 0.555
        assert abs(avg - 0.555) < 0.001
        assert abs(filled - 400) < 0.01

    def test_insufficient_depth(self):
        asks = [BookLevel(price=0.55, size=100)]
        avg, filled = compute_avg_fill_price(asks, 500)
        assert abs(filled - 100) < 0.01
        assert abs(avg - 0.55) < 0.001

    def test_empty_book(self):
        avg, filled = compute_avg_fill_price([], 500)
        assert filled == 0
        assert avg == 0


class TestMarketExtraction:
    def test_skips_yes_no_market_case_insensitive(self):
        event = {
            "title": "Will TeamA win?",
            "markets": [{
                "active": True,
                "closed": False,
                "question": "Winner",
                "conditionId": "cond1",
                "outcomes": ["yes", "no"],
                "clobTokenIds": ["1", "2"],
            }],
        }
        markets = _extract_tradeable_markets(event, sport_tag="nba")
        assert markets == []

    def test_skips_market_with_missing_token(self):
        event = {
            "title": "TeamA vs TeamB",
            "markets": [{
                "active": True,
                "closed": False,
                "question": "Match winner",
                "conditionId": "cond1",
                "outcomes": ["TeamA", "TeamB"],
                "clobTokenIds": ["", "2"],
            }],
        }
        markets = _extract_tradeable_markets(event, sport_tag="nba")
        assert markets == []

    def test_parse_outcomes_tokens_rejects_non_sequence(self):
        parsed = _parse_outcomes_tokens({
            "outcomes": {"a": "TeamA", "b": "TeamB"},
            "clobTokenIds": ["1", "2"],
        })
        assert parsed is None

    def test_keeps_spread_outcomes(self):
        event = {
            "title": "TeamA vs TeamB",
            "markets": [{
                "active": True,
                "closed": False,
                "question": "Spread",
                "conditionId": "cond1",
                "outcomes": ["TeamA +3.5", "TeamB -3.5"],
                "clobTokenIds": ["1", "2"],
            }],
        }
        markets = _extract_tradeable_markets(event, sport_tag="nba")
        assert len(markets) == 1
        assert markets[0].market_type == "spread"

    def test_keeps_moneyline_market_and_maps_metadata(self):
        event = {
            "title": "TeamA vs TeamB",
            "startDate": "2026-02-20T20:00:00Z",
            "markets": [{
                "active": True,
                "closed": False,
                "question": "Match winner",
                "conditionId": "cond1",
                "outcomes": ["TeamA", "TeamB"],
                "clobTokenIds": ["1", "2"],
            }],
        }
        markets = _extract_tradeable_markets(event, sport_tag="nba")
        assert len(markets) == 1
        assert markets[0].market_type == "moneyline"
        assert markets[0].question == "Match winner"
        assert markets[0].start_iso == "2026-02-20T20:00:00Z"

    def test_skips_total_market(self):
        event = {
            "title": "TeamA vs TeamB",
            "markets": [{
                "active": True,
                "closed": False,
                "question": "TeamA vs TeamB: O/U 221.5",
                "conditionId": "cond1",
                "outcomes": ["Over", "Under"],
                "clobTokenIds": ["1", "2"],
            }],
        }
        markets = _extract_tradeable_markets(event, sport_tag="nba")
        assert markets == []


class TestBookLevelParsing:
    def test_parse_book_levels_skips_invalid_rows(self):
        levels = _parse_book_levels([
            {"price": "0.45", "size": "120"},
            {"price": None, "size": 10},
            {"price": "bad", "size": "10"},
            {"price": "0.5", "size": 0},
            "not-a-dict",
        ])
        assert len(levels) == 1
        assert levels[0].price == 0.45
        assert levels[0].size == 120
