import pytest
from polyedge.data.odds_api import parse_all_books_response
from polyedge.models import AllBookOdds

SAMPLE_RESPONSE = [
    {
        "sport_key": "basketball_nba",
        "home_team": "Boston Celtics",
        "away_team": "Los Angeles Lakers",
        "commence_time": "2026-02-21T00:00:00Z",
        "bookmakers": [
            {
                "key": "draftkings",
                "title": "DraftKings",
                "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Boston Celtics", "price": -200},
                    {"name": "Los Angeles Lakers", "price": 170},
                ]}],
            },
            {
                "key": "fanduel",
                "title": "FanDuel",
                "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Boston Celtics", "price": -190},
                    {"name": "Los Angeles Lakers", "price": 160},
                ]}],
            },
        ],
    }
]

SPREAD_ONLY_RESPONSE = [
    {
        "sport_key": "basketball_nba",
        "home_team": "Boston Celtics",
        "away_team": "Los Angeles Lakers",
        "commence_time": "2026-02-21T00:00:00Z",
        "bookmakers": [
            {
                "title": "DraftKings",
                "markets": [{"key": "spreads", "outcomes": [
                    {"name": "Boston Celtics", "price": -110, "point": -3.5},
                    {"name": "Los Angeles Lakers", "price": -110, "point": 3.5},
                ]}],
            }
        ],
    }
]


class TestParseAllBooks:
    def test_parses_multiple_books(self):
        result = parse_all_books_response(SAMPLE_RESPONSE)
        assert len(result) == 1
        game = result[0]
        assert isinstance(game, AllBookOdds)
        assert len(game.books) == 2
        assert "DraftKings" in game.books
        assert "FanDuel" in game.books

    def test_outcome_odds_correct(self):
        game = parse_all_books_response(SAMPLE_RESPONSE)[0]
        dk_a, dk_b = game.books["DraftKings"]
        assert dk_a.name == "Boston Celtics"
        assert dk_a.american_odds == -200
        assert abs(dk_a.decimal_odds - 1.50) < 0.01

    def test_skips_non_h2h(self):
        data = [{"sport_key": "nba", "home_team": "A", "away_team": "B",
                 "commence_time": "2026-01-01T00:00:00Z",
                 "bookmakers": [{"key": "x", "title": "X",
                    "markets": [{"key": "spreads", "outcomes": [
                        {"name": "A", "price": -110},
                        {"name": "B", "price": -110},
                    ]}]}]}]
        result = parse_all_books_response(data)
        # Should have no books since only "spreads" market, not "h2h"
        assert len(result) == 0 or len(result[0].books) == 0

    def test_empty_response(self):
        result = parse_all_books_response([])
        assert result == []

    def test_home_away_preserved(self):
        game = parse_all_books_response(SAMPLE_RESPONSE)[0]
        assert game.home == "Boston Celtics"
        assert game.away == "Los Angeles Lakers"
        assert game.sport == "basketball_nba"

    def test_parses_spread_books_with_points(self):
        game = parse_all_books_response(SPREAD_ONLY_RESPONSE)[0]
        assert "DraftKings" in game.spread_books
        spread_a, spread_b = game.spread_books["DraftKings"]
        assert spread_a.name == "Boston Celtics (-3.5)"
        assert spread_b.name == "Los Angeles Lakers (+3.5)"
        assert spread_a.american_odds == -110
        assert spread_b.american_odds == -110

    def test_skips_malformed_outcome_rows(self):
        data = [{
            "sport_key": "basketball_nba",
            "home_team": "A",
            "away_team": "B",
            "commence_time": "2026-01-01T00:00:00Z",
            "bookmakers": [{
                "title": "BadBook",
                "markets": [{"key": "h2h", "outcomes": [
                    {"name": "A", "price": "x"},
                    {"name": "B", "price": 110},
                ]}],
            }],
        }]
        result = parse_all_books_response(data)
        assert result == []
