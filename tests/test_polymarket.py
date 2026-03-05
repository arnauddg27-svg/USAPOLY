import pytest
from polyedge.models import BookLevel, OrderBook
from polyedge.data.polymarket import (
    compute_avg_fill_price,
    _extract_tradeable_markets,
    _parse_book_levels,
    _parse_outcomes_tokens,
    sport_to_tag_slug,
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
    def test_skips_yes_no_market_without_matchup_context(self):
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

    def test_keeps_rugby_yes_no_matchup_win_market(self):
        event = {
            "title": "NRL: Brisbane Broncos vs Penrith Panthers",
            "markets": [{
                "active": True,
                "closed": False,
                "question": "Will Brisbane Broncos win?",
                "conditionId": "cond_rugby_yes_no",
                "outcomes": ["Yes", "No"],
                "clobTokenIds": ["1", "2"],
            }],
        }
        markets = _extract_tradeable_markets(event, sport_tag="rugby")
        assert len(markets) == 1
        assert markets[0].market_type == "moneyline"

    def test_skips_rugby_yes_no_draw_market(self):
        event = {
            "title": "NRL: Brisbane Broncos vs Penrith Panthers",
            "markets": [{
                "active": True,
                "closed": False,
                "question": "Will the match end in a draw?",
                "conditionId": "cond_rugby_draw",
                "outcomes": ["Yes", "No"],
                "clobTokenIds": ["1", "2"],
            }],
        }
        markets = _extract_tradeable_markets(event, sport_tag="rugby")
        assert markets == []

    def test_keeps_soccer_yes_no_spread_market_and_blocks_halftime(self):
        event = {
            "title": "Tottenham Hotspur vs Arsenal",
            "markets": [
                {
                    "active": True,
                    "closed": False,
                    "question": "Will Tottenham Hotspur (+1.5) cover the spread?",
                    "conditionId": "cond_soccer_spread",
                    "outcomes": ["Yes", "No"],
                    "clobTokenIds": ["1", "2"],
                },
                {
                    "active": True,
                    "closed": False,
                    "question": "Will Tottenham Hotspur (+0.5) cover the spread in 1st half?",
                    "conditionId": "cond_soccer_spread_half",
                    "outcomes": ["Yes", "No"],
                    "clobTokenIds": ["3", "4"],
                },
                {
                    "active": True,
                    "closed": False,
                    "question": "Will Tottenham Hotspur (+0.5) cover the spread at halftime?",
                    "conditionId": "cond_soccer_spread_halftime",
                    "outcomes": ["Yes", "No"],
                    "clobTokenIds": ["5", "6"],
                },
            ],
        }
        markets = _extract_tradeable_markets(event, sport_tag="soccer")
        assert len(markets) == 1
        assert markets[0].condition_id == "cond_soccer_spread"
        assert markets[0].market_type == "spread"

    def test_keeps_soccer_yes_no_win_market_as_moneyline(self):
        event = {
            "title": "Tottenham Hotspur vs Arsenal",
            "markets": [
                {
                    "active": True,
                    "closed": False,
                    "question": "Will Tottenham Hotspur win?",
                    "conditionId": "cond_soccer_moneyline",
                    "outcomes": ["Yes", "No"],
                    "clobTokenIds": ["1", "2"],
                },
            ],
        }
        markets = _extract_tradeable_markets(event, sport_tag="soccer")
        assert len(markets) == 1
        assert markets[0].condition_id == "cond_soccer_moneyline"
        assert markets[0].market_type == "moneyline"

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

    def test_prefers_market_level_start_timestamp(self):
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
                "startDate": "2026-02-20T21:30:00Z",
            }],
        }
        markets = _extract_tradeable_markets(event, sport_tag="nba")
        assert len(markets) == 1
        assert markets[0].start_iso == "2026-02-20T21:30:00Z"

    def test_prefers_game_start_time_over_start_date(self):
        event = {
            "title": "TeamA vs TeamB",
            "markets": [{
                "active": True,
                "closed": False,
                "question": "Match winner",
                "conditionId": "cond1",
                "outcomes": ["TeamA", "TeamB"],
                "clobTokenIds": ["1", "2"],
                "startDate": "2026-01-30T16:33:04.552706Z",
                "gameStartTime": "2026-02-27 00:00:00+00",
            }],
        }
        markets = _extract_tradeable_markets(event, sport_tag="nba")
        assert len(markets) == 1
        assert markets[0].start_iso == "2026-02-27 00:00:00+00"

    def test_falls_back_to_end_date_for_start_time(self):
        event = {
            "title": "TeamA vs TeamB",
            "endDate": "2026-02-21T03:00:00Z",
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
        assert markets[0].start_iso == "2026-02-21T03:00:00Z"

    def test_skips_tennis_first_set_winner_market(self):
        event = {
            "title": "Arthur Rinderknech vs Jack Draper",
            "markets": [{
                "active": True,
                "closed": False,
                "question": "1st Set Winner",
                "conditionId": "cond1",
                "outcomes": ["Arthur Rinderknech", "Jack Draper"],
                "clobTokenIds": ["1", "2"],
            }],
        }
        markets = _extract_tradeable_markets(event, sport_tag="tennis")
        assert markets == []

    def test_skips_tennis_set_number_winner_market(self):
        event = {
            "title": "Arthur Rinderknech vs Jack Draper",
            "markets": [{
                "active": True,
                "closed": False,
                "question": "Set 1 Winner",
                "conditionId": "cond1",
                "outcomes": ["Arthur Rinderknech", "Jack Draper"],
                "clobTokenIds": ["1", "2"],
            }],
        }
        markets = _extract_tradeable_markets(event, sport_tag="tennis")
        assert markets == []

    def test_keeps_tennis_match_winner_but_skips_first_set_winner(self):
        event = {
            "title": "Arthur Rinderknech vs Jack Draper",
            "markets": [
                {
                    "active": True,
                    "closed": False,
                    "question": "Match winner",
                    "conditionId": "cond_match",
                    "outcomes": ["Arthur Rinderknech", "Jack Draper"],
                    "clobTokenIds": ["1", "2"],
                },
                {
                    "active": True,
                    "closed": False,
                    "question": "1st Set Winner",
                    "conditionId": "cond_set",
                    "outcomes": ["Arthur Rinderknech", "Jack Draper"],
                    "clobTokenIds": ["3", "4"],
                },
            ],
        }
        markets = _extract_tradeable_markets(event, sport_tag="tennis")
        assert len(markets) == 1
        assert markets[0].condition_id == "cond_match"
        assert markets[0].market_type == "moneyline"

    def test_skips_hockey_regulation_winner_market(self):
        event = {
            "title": "Predators vs Blackhawks",
            "markets": [{
                "active": True,
                "closed": False,
                "question": "Regulation Winner",
                "conditionId": "cond1",
                "outcomes": ["Predators", "Blackhawks"],
                "clobTokenIds": ["1", "2"],
            }],
        }
        markets = _extract_tradeable_markets(event, sport_tag="nhl")
        assert markets == []

    def test_skips_first_half_shorthand_moneyline_market(self):
        event = {
            "title": "Cavaliers vs Pistons",
            "markets": [{
                "active": True,
                "closed": False,
                "question": "Cavaliers vs Pistons: 1H Moneyline",
                "conditionId": "cond1",
                "outcomes": ["Cavaliers", "Pistons"],
                "clobTokenIds": ["1", "2"],
            }],
        }
        markets = _extract_tradeable_markets(event, sport_tag="nba")
        assert markets == []

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

    def test_uses_event_title_as_moneyline_fallback_when_market_text_missing(self):
        event = {
            "title": "Flyers vs Capitals",
            "markets": [{
                "active": True,
                "closed": False,
                "question": "",
                "title": "",
                "conditionId": "cond1",
                "outcomes": ["PHI", "WSH"],
                "clobTokenIds": ["1", "2"],
            }],
        }
        markets = _extract_tradeable_markets(event, sport_tag="nhl")
        assert len(markets) == 1
        assert markets[0].market_type == "moneyline"


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


class TestSportSlugResolution:
    def test_exact_supported_sport_slug(self):
        assert sport_to_tag_slug("basketball_nba") == "nba"
        assert sport_to_tag_slug("baseball_mlb") == "mlb"

    def test_soccer_prefix_maps_to_soccer_tag(self):
        assert sport_to_tag_slug("soccer_epl") == "soccer"

    def test_tennis_prefix_maps_to_tennis_tag(self):
        assert sport_to_tag_slug("tennis_atp") == "tennis"

    def test_cricket_prefix_maps_to_cricket_tag(self):
        assert sport_to_tag_slug("cricket_odi") == "cricket"

    def test_rugby_prefix_maps_to_rugby_tag(self):
        assert sport_to_tag_slug("rugby_union_six_nations") == "rugby"
        assert sport_to_tag_slug("rugbyleague_nrl") == "rugby"
        assert sport_to_tag_slug("rugby") == "rugby"

    def test_table_tennis_prefix_maps_to_table_tennis_tag(self):
        assert sport_to_tag_slug("table_tennis_world_championship") == "table-tennis"
