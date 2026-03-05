import pytest
from polyedge.data.odds_api import (
    _extract_available_sport_keys,
    augment_sport_keys_with_fallbacks,
    expand_sport_keys,
    parse_all_books_response,
)
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

SOCCER_THREE_WAY_RESPONSE = [
    {
        "sport_key": "soccer_epl",
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "commence_time": "2026-02-21T00:00:00Z",
        "bookmakers": [
            {
                "key": "book_a",
                "title": "BookA",
                "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Arsenal", "price": 120},
                    {"name": "Draw", "price": 240},
                    {"name": "Chelsea", "price": 210},
                ]}],
            }
        ],
    }
]

SOCCER_THREE_WAY_MISMATCH_RESPONSE = [
    {
        "sport_key": "soccer_epl",
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "commence_time": "2026-02-21T00:00:00Z",
        "bookmakers": [
            {
                "key": "book_a",
                "title": "BookA",
                "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Team A", "price": 120},
                    {"name": "Draw", "price": 240},
                    {"name": "Team B", "price": 210},
                ]}],
            }
        ],
    }
]

SOCCER_THREE_WAY_ABBREV_RESPONSE = [
    {
        "sport_key": "soccer_ligue_one",
        "home_team": "Paris Saint Germain",
        "away_team": "Olympique Marseille",
        "commence_time": "2026-02-21T00:00:00Z",
        "bookmakers": [
            {
                "key": "book_abbrev",
                "title": "BookAbbrev",
                "markets": [{"key": "h2h", "outcomes": [
                    {"name": "PSG", "price": -120},
                    {"name": "Draw", "price": 260},
                    {"name": "Marseille", "price": 310},
                ]}],
            }
        ],
    }
]

SOCCER_THREE_WAY_MAN_UTD_RESPONSE = [
    {
        "sport_key": "soccer_epl",
        "home_team": "Manchester United",
        "away_team": "Liverpool",
        "commence_time": "2026-02-21T00:00:00Z",
        "bookmakers": [
            {
                "key": "book_mu",
                "title": "BookMU",
                "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Man Utd", "price": 145},
                    {"name": "Draw", "price": 250},
                    {"name": "Liverpool", "price": 180},
                ]}],
            }
        ],
    }
]

SOCCER_THREE_WAY_MUNCHEN_RESPONSE = [
    {
        "sport_key": "soccer_germany_bundesliga",
        "home_team": "Bayern Munich",
        "away_team": "Borussia Dortmund",
        "commence_time": "2026-02-21T00:00:00Z",
        "bookmakers": [
            {
                "key": "book_de",
                "title": "BookDE",
                "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Bayern Munchen", "price": -140},
                    {"name": "Draw", "price": 280},
                    {"name": "Dortmund", "price": 360},
                ]}],
            }
        ],
    }
]

SOCCER_SPREAD_ABBREV_RESPONSE = [
    {
        "sport_key": "soccer_epl",
        "home_team": "Manchester City",
        "away_team": "Arsenal",
        "commence_time": "2026-03-08T15:00:00Z",
        "bookmakers": [
            {
                "key": "book_a",
                "title": "BookA",
                "markets": [{"key": "spreads", "outcomes": [
                    {"name": "Man City", "price": -110, "point": -0.5},
                    {"name": "Arsenal", "price": -110, "point": 0.5},
                ]}],
            },
            {
                "key": "book_b",
                "title": "BookB",
                "markets": [{"key": "spreads", "outcomes": [
                    {"name": "Arsenal", "price": -105, "point": 0.5},
                    {"name": "Manchester City", "price": -115, "point": -0.5},
                ]}],
            },
        ],
    }
]

SOCCER_THREE_WAY_LISBON_RESPONSE = [
    {
        "sport_key": "soccer_portugal_primeira_liga",
        "home_team": "Sporting CP",
        "away_team": "Porto",
        "commence_time": "2026-02-21T00:00:00Z",
        "bookmakers": [
            {
                "key": "book_pt",
                "title": "BookPT",
                "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Sporting Lisbon", "price": -110},
                    {"name": "Draw", "price": 240},
                    {"name": "Porto", "price": 290},
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

    def test_parses_soccer_three_way_h2h_as_two_way(self):
        game = parse_all_books_response(SOCCER_THREE_WAY_RESPONSE)[0]
        assert "BookA" in game.books
        out_a, out_b = game.books["BookA"]
        assert out_a.name == "Arsenal"
        assert out_b.name == "Chelsea"
        assert out_a.american_odds == 120
        assert out_b.american_odds == 210

    def test_skips_three_way_when_teams_do_not_match(self):
        result = parse_all_books_response(SOCCER_THREE_WAY_MISMATCH_RESPONSE)
        assert result == []

    def test_parses_three_way_soccer_with_common_abbreviation(self):
        game = parse_all_books_response(SOCCER_THREE_WAY_ABBREV_RESPONSE)[0]
        assert "BookAbbrev" in game.books
        out_a, out_b = game.books["BookAbbrev"]
        assert out_a.name == "Paris Saint Germain"
        assert out_b.name == "Olympique Marseille"
        assert out_a.american_odds == -120
        assert out_b.american_odds == 310

    def test_parses_three_way_soccer_with_man_utd_abbreviation(self):
        game = parse_all_books_response(SOCCER_THREE_WAY_MAN_UTD_RESPONSE)[0]
        out_a, out_b = game.books["BookMU"]
        assert out_a.name == "Manchester United"
        assert out_b.name == "Liverpool"
        assert out_a.american_odds == 145
        assert out_b.american_odds == 180

    def test_parses_three_way_soccer_with_munchen_transliteration(self):
        game = parse_all_books_response(SOCCER_THREE_WAY_MUNCHEN_RESPONSE)[0]
        out_a, out_b = game.books["BookDE"]
        assert out_a.name == "Bayern Munich"
        assert out_b.name == "Borussia Dortmund"
        assert out_a.american_odds == -140
        assert out_b.american_odds == 360

    def test_parses_three_way_soccer_with_lisbon_alias(self):
        game = parse_all_books_response(SOCCER_THREE_WAY_LISBON_RESPONSE)[0]
        out_a, out_b = game.books["BookPT"]
        assert out_a.name == "Sporting CP"
        assert out_b.name == "Porto"
        assert out_a.american_odds == -110
        assert out_b.american_odds == 290


    def test_soccer_spread_canonicalizes_bookmaker_names(self):
        """Spread outcomes should use the API event's canonical team names,
        not the bookmaker's raw names, so orient_book_outcomes succeeds."""
        game = parse_all_books_response(SOCCER_SPREAD_ABBREV_RESPONSE)[0]
        assert "BookA" in game.spread_books
        assert "BookB" in game.spread_books
        # BookA reported "Man City" but should be canonicalized to "Manchester City"
        a_a, a_b = game.spread_books["BookA"]
        assert a_a.name == "Manchester City (-0.5)"
        assert a_b.name == "Arsenal (+0.5)"
        # BookB had swapped order; should be oriented and canonicalized
        b_a, b_b = game.spread_books["BookB"]
        assert b_a.name == "Manchester City (-0.5)"
        assert b_b.name == "Arsenal (+0.5)"


class TestSportExpansion:
    def test_expands_soccer_tennis_cricket_rugby_table_tennis_wildcards(self):
        requested = ["soccer_all", "tennis_all", "cricket_all", "rugby_all", "table_tennis_all"]
        available = [
            "basketball_nba",
            "soccer_epl",
            "soccer_uefa_champs_league",
            "tennis_atp_us_open",
            "tennis_wta_french_open",
            "cricket_odi",
            "rugby_union_six_nations",
            "rugbyleague_nrl",
            "table_tennis_china_open",
        ]
        resolved = expand_sport_keys(requested, available)
        assert resolved == [
            "soccer_epl",
            "soccer_uefa_champs_league",
            "tennis_atp_us_open",
            "tennis_wta_french_open",
            "cricket_odi",
            "rugby_union_six_nations",
            "rugbyleague_nrl",
            "table_tennis_china_open",
        ]

    def test_wildcard_expansion_preserves_order_and_deduplicates(self):
        requested = ["soccer_all", "soccer_epl", "tennis_all", "tennis_wta_french_open"]
        available = [
            "soccer_epl",
            "soccer_spain_la_liga",
            "tennis_wta_french_open",
            "tennis_atp_us_open",
        ]
        resolved = expand_sport_keys(requested, available)
        assert resolved == [
            "soccer_epl",
            "soccer_spain_la_liga",
            "tennis_wta_french_open",
            "tennis_atp_us_open",
        ]

    def test_non_wildcard_tokens_are_kept(self):
        requested = ["basketball_nba", "soccer_all"]
        available = ["soccer_epl"]
        resolved = expand_sport_keys(requested, available)
        assert resolved == ["basketball_nba", "soccer_epl"]

    def test_appends_cricket_fallback_keys_for_cricket_family_tokens(self):
        requested = ["basketball_nba", "cricket"]
        resolved = ["basketball_nba", "cricket_t20_world_cup"]
        augmented = augment_sport_keys_with_fallbacks(requested, resolved)
        assert "basketball_nba" in augmented
        assert "cricket_t20_world_cup" in augmented
        assert "cricket_ipl" in augmented
        assert "cricket_psl" in augmented
        assert "cricket_test_match" in augmented

    def test_does_not_append_cricket_fallback_keys_without_cricket_scope(self):
        requested = ["basketball_nba", "soccer_all"]
        resolved = ["basketball_nba", "soccer_epl"]
        augmented = augment_sport_keys_with_fallbacks(requested, resolved)
        assert augmented == ["basketball_nba", "soccer_epl"]

    def test_resolves_rugby_family_alias_to_available_rugby_keys(self):
        requested = ["rugby"]
        available = ["rugby_union_six_nations", "rugbyleague_nrl", "soccer_epl"]
        resolved = expand_sport_keys(requested, available)
        assert resolved == ["rugby_union_six_nations", "rugbyleague_nrl"]


class TestAvailableSportsExtraction:
    def test_keeps_matchup_keys_when_has_outrights_true(self):
        payload = [
            {"key": "soccer_epl", "active": True, "has_outrights": True},
            {"key": "tennis_atp_us_open", "active": True, "has_outrights": True},
        ]
        keys = _extract_available_sport_keys(payload)
        assert keys == ["soccer_epl", "tennis_atp_us_open"]

    def test_filters_outright_only_keys(self):
        payload = [
            {"key": "soccer_epl_winner", "active": True},
            {"key": "tennis_atp_winner_outright", "active": True},
            {"key": "soccer_uefa_champs_league", "active": True},
        ]
        keys = _extract_available_sport_keys(payload)
        assert keys == ["soccer_uefa_champs_league"]

    def test_filters_inactive_and_deduplicates(self):
        payload = [
            {"key": "soccer_epl", "active": False},
            {"key": "soccer_epl", "active": True},
            {"key": "basketball_nba"},
            {"key": "basketball_nba"},
        ]
        keys = _extract_available_sport_keys(payload)
        assert keys == ["soccer_epl", "basketball_nba"]
