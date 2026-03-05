import pytest

from polyedge.models import AllBookOdds, PolyMarket, SportsOutcome
from polyedge.pipeline.matcher import (
    match_events,
    orient_book_outcomes,
    poly_spread_points,
    spread_points_compatible,
)

def _game(home="Boston Celtics", away="Los Angeles Lakers", sport="basketball_nba", commence_time="2026-02-21T00:00:00Z"):
    return AllBookOdds(sport=sport, home=home, away=away, commence_time=commence_time,
                       books={"DK": (SportsOutcome(home, -200, "DK"), SportsOutcome(away, 170, "DK"))})

def _poly(
    outcome_a="Boston Celtics",
    outcome_b="Los Angeles Lakers",
    title="NBA: Celtics vs Lakers",
    start_iso="",
):
    return PolyMarket(event_title=title, condition_id="cond1",
                      outcome_a=outcome_a, outcome_b=outcome_b,
                      token_id_a="tok_a", token_id_b="tok_b",
                      sport_tag="nba", start_iso=start_iso)

class TestMatching:
    def test_exact_match(self):
        matches = match_events([_game()], [_poly()])
        assert len(matches) == 1
        assert matches[0].team_a == "Boston Celtics"

    def test_alias_match(self):
        matches = match_events([_game()], [_poly("Celtics", "Lakers")])
        assert len(matches) == 1

    def test_nhl_code_alias_match(self):
        game = _game(
            home="Philadelphia Flyers",
            away="Washington Capitals",
            sport="icehockey_nhl",
            commence_time="2026-02-26T00:00:00Z",
        )
        poly = _poly(
            outcome_a="PHI",
            outcome_b="WSH",
            title="Flyers vs Capitals",
            start_iso="2026-02-26T01:00:00Z",
        )
        poly.sport_tag = "nhl"
        matches = match_events([game], [poly])
        assert len(matches) == 1
        assert matches[0].team_a == "Philadelphia Flyers"
        assert matches[0].team_b == "Washington Capitals"

    def test_nhl_variant_name_matches_code_alias(self):
        game = _game(
            home="N.Y. Islanders",
            away="Washington Capitals",
            sport="icehockey_nhl",
            commence_time="2026-02-26T00:00:00Z",
        )
        poly = _poly(
            outcome_a="NYI",
            outcome_b="WSH",
            title="Islanders vs Capitals",
            start_iso="2026-02-26T01:00:00Z",
        )
        poly.sport_tag = "nhl"
        matches = match_events([game], [poly])
        assert len(matches) == 1
        assert matches[0].team_a == "N.Y. Islanders"
        assert matches[0].team_b == "Washington Capitals"

    def test_nhl_los_angeles_kings_alias_match(self):
        game = _game(
            home="Los Angeles Kings",
            away="Anaheim Ducks",
            sport="icehockey_nhl",
            commence_time="2026-02-26T00:00:00Z",
        )
        poly = _poly(
            outcome_a="LA Kings",
            outcome_b="Ducks",
            title="Kings vs Ducks",
            start_iso="2026-02-26T01:00:00Z",
        )
        poly.sport_tag = "nhl"
        matches = match_events([game], [poly])
        assert len(matches) == 1

    def test_no_match(self):
        matches = match_events([_game()], [_poly("Miami Heat", "Chicago Bulls")])
        assert len(matches) == 0

    def test_title_fallback(self):
        poly = _poly("Yes", "No", "Will Lakers beat Celtics?")
        matches = match_events([_game()], [poly])
        assert len(matches) == 0

    def test_rugby_yes_no_market_matches_target_team_question(self):
        game = _game(
            home="Brisbane Broncos",
            away="Penrith Panthers",
            sport="rugbyleague_nrl",
            commence_time="2026-03-06T09:00:00Z",
        )
        poly = _poly(
            outcome_a="Yes",
            outcome_b="No",
            title="NRL: Brisbane Broncos vs Penrith Panthers",
            start_iso="2026-03-06T09:00:00Z",
        )
        poly.question = "Will Brisbane Broncos win?"
        poly.sport_tag = "rugby"
        matches = match_events([game], [poly])
        assert len(matches) == 1
        assert matches[0].team_a == "Brisbane Broncos"
        assert matches[0].team_b == "Penrith Panthers"

    def test_soccer_yes_no_spread_market_matches_target_team_question(self):
        game = AllBookOdds(
            sport="soccer_epl",
            home="Tottenham Hotspur",
            away="Arsenal",
            commence_time="2026-03-08T15:00:00Z",
            books={},
            spread_books={
                "BookA": (
                    SportsOutcome("Tottenham Hotspur (+1.5)", -110, "BookA"),
                    SportsOutcome("Arsenal (-1.5)", -110, "BookA"),
                )
            },
        )
        poly = _poly(
            outcome_a="Yes",
            outcome_b="No",
            title="Tottenham Hotspur vs Arsenal",
            start_iso="2026-03-08T15:00:00Z",
        )
        poly.question = "Will Tottenham Hotspur (+1.5) cover the spread?"
        poly.sport_tag = "soccer"
        poly.market_type = "spread"
        matches = match_events([game], [poly], min_books_for_spread=1)
        assert len(matches) == 1
        assert matches[0].team_a == "Tottenham Hotspur"
        assert matches[0].team_b == "Arsenal"

    def test_title_team_name_fallback_when_outcomes_are_generic(self):
        game = _game(
            home="Philadelphia Flyers",
            away="Washington Capitals",
            sport="icehockey_nhl",
            commence_time="2026-02-26T00:00:00Z",
        )
        poly = _poly(
            outcome_a="Home",
            outcome_b="Away",
            title="Flyers vs Capitals",
            start_iso="2026-02-26T01:00:00Z",
        )
        poly.sport_tag = "nhl"
        matches = match_events([game], [poly])
        assert len(matches) == 1

    def test_token_boundary_prevents_false_alias_match(self):
        game = _game(home="Sacramento Kings", away="Chicago Bulls")
        poly = _poly(outcome_a="Minnesota Vikings", outcome_b="Chicago Bulls")
        matches = match_events([game], [poly])
        assert len(matches) == 0

    def test_start_time_guard_blocks_far_apart_events(self):
        game = _game(commence_time="2026-02-21T00:00:00Z")
        poly = _poly(start_iso="2026-03-10T00:00:00Z")
        matches = match_events([game], [poly])
        assert len(matches) == 0

    def test_start_time_guard_allows_mid_range_drift(self):
        game = _game(commence_time="2026-02-21T00:00:00Z")
        poly = _poly(start_iso="2026-02-21T06:00:00Z")
        matches = match_events([game], [poly])
        assert len(matches) == 1

    def test_start_time_guard_blocks_31h_drift(self):
        game = _game(commence_time="2026-02-21T00:00:00Z")
        poly = _poly(start_iso="2026-02-22T07:00:00Z")
        matches = match_events([game], [poly])
        assert len(matches) == 0

    def test_cross_date_guard_blocks_large_cross_day_drift(self):
        game = _game(commence_time="2026-02-21T23:30:00Z")
        poly = _poly(start_iso="2026-02-22T20:00:00Z")
        matches = match_events([game], [poly])
        assert len(matches) == 0

    def test_start_time_guard_blocks_multi_day_drift(self):
        game = _game(commence_time="2026-02-21T00:00:00Z")
        poly = _poly(start_iso="2026-02-24T00:00:00Z")
        matches = match_events([game], [poly])
        assert len(matches) == 0

    def test_selects_closest_start_time_when_multiple_markets_match(self):
        game = _game(commence_time="2026-02-21T00:00:00Z")
        far = _poly(start_iso="2026-02-25T00:00:00Z", title="Far")
        close = _poly(start_iso="2026-02-21T01:00:00Z", title="Close")
        matches = match_events([game], [far, close])
        assert len(matches) == 1
        assert matches[0].poly_market.event_title == "Close"

    def test_skips_ambiguous_missing_start_times(self):
        game = _game(commence_time="2026-02-21T00:00:00Z")
        p1 = _poly(start_iso="", title="A")
        p2 = _poly(start_iso="", title="B")
        p2.condition_id = "cond2"
        matches = match_events([game], [p1, p2])
        assert len(matches) == 0

    def test_prefers_moneyline_over_spread_without_spread_books(self):
        game = _game(commence_time="2026-02-21T00:00:00Z")
        spread = _poly(start_iso="2026-02-21T01:00:00Z", title="Spread")
        spread.condition_id = "cond_spread"
        spread.market_type = "spread"
        moneyline = _poly(start_iso="2026-02-21T01:00:00Z", title="Moneyline")
        moneyline.condition_id = "cond_moneyline"
        moneyline.market_type = "moneyline"
        matches = match_events([game], [spread, moneyline])
        assert len(matches) == 1
        assert matches[0].poly_market.condition_id == "cond_moneyline"

    def test_orient_book_outcomes_handles_reversed_book_order(self):
        first = SportsOutcome("Los Angeles Lakers", 170, "DK")
        second = SportsOutcome("Boston Celtics", -200, "DK")
        oriented = orient_book_outcomes("Boston Celtics", "Los Angeles Lakers", first, second)
        assert oriented is not None
        a, b = oriented
        assert a.name == "Boston Celtics"
        assert b.name == "Los Angeles Lakers"

    def test_poly_spread_points_inferred_from_question(self):
        poly = PolyMarket(
            event_title="KRC Genk vs Dinamo Zagreb",
            condition_id="cond-spread",
            outcome_a="KRC Genk",
            outcome_b="Dinamo Zagreb",
            token_id_a="tok_a",
            token_id_b="tok_b",
            market_type="spread",
            question="Spread: KRC Genk (-1.5)",
            sport_tag="soccer",
            start_iso="2026-02-25T20:00:00Z",
        )
        point_a, point_b = poly_spread_points(poly)
        assert point_a == -1.5
        assert point_b == 1.5

    def test_spread_points_compatible_requires_exact_line(self):
        poly = PolyMarket(
            event_title="KRC Genk vs Dinamo Zagreb",
            condition_id="cond-spread",
            outcome_a="KRC Genk",
            outcome_b="Dinamo Zagreb",
            token_id_a="tok_a",
            token_id_b="tok_b",
            market_type="spread",
            question="Spread: KRC Genk (-1.5)",
            sport_tag="soccer",
            start_iso="2026-02-25T20:00:00Z",
        )
        ok = spread_points_compatible(
            poly,
            SportsOutcome("KRC Genk (-1.5)", -110, "DK"),
            SportsOutcome("Dinamo Zagreb (+1.5)", -110, "DK"),
        )
        bad = spread_points_compatible(
            poly,
            SportsOutcome("KRC Genk (+1.5)", -110, "DK"),
            SportsOutcome("Dinamo Zagreb (-1.5)", -110, "DK"),
        )
        assert ok is True
        assert bad is False

    def test_spread_points_compatible_yes_no_uses_team_names_from_question(self):
        poly = PolyMarket(
            event_title="Tottenham Hotspur vs Arsenal",
            condition_id="cond-yes-no-spread",
            outcome_a="Yes",
            outcome_b="No",
            token_id_a="tok_yes",
            token_id_b="tok_no",
            market_type="spread",
            question="Will Tottenham Hotspur (+1.5) cover the spread?",
            sport_tag="soccer",
            start_iso="2026-03-08T15:00:00Z",
        )
        ok = spread_points_compatible(
            poly,
            SportsOutcome("Tottenham Hotspur (+1.5)", -110, "BookA"),
            SportsOutcome("Arsenal (-1.5)", -110, "BookA"),
            team_a_name="Tottenham Hotspur",
            team_b_name="Arsenal",
        )
        bad = spread_points_compatible(
            poly,
            SportsOutcome("Tottenham Hotspur (+0.5)", -110, "BookA"),
            SportsOutcome("Arsenal (-0.5)", -110, "BookA"),
            team_a_name="Tottenham Hotspur",
            team_b_name="Arsenal",
        )
        assert ok is True
        assert bad is False

    def test_spread_candidate_skipped_when_no_compatible_lines(self):
        game = AllBookOdds(
            sport="soccer_epl",
            home="Tottenham Hotspur",
            away="Crystal Palace",
            commence_time="2026-02-25T20:00:00Z",
            books={},
            spread_books={
                "BookA": (
                    SportsOutcome("Tottenham Hotspur (-1.5)", -110, "BookA"),
                    SportsOutcome("Crystal Palace (+1.5)", -110, "BookA"),
                )
            },
        )
        poly = PolyMarket(
            event_title="Tottenham vs Crystal Palace",
            condition_id="cond-spread-mismatch",
            outcome_a="Tottenham Hotspur",
            outcome_b="Crystal Palace",
            token_id_a="tok_a",
            token_id_b="tok_b",
            market_type="spread",
            question="Spread: Tottenham Hotspur (-2.5)",
            sport_tag="soccer",
            start_iso="2026-02-25T20:00:00Z",
        )
        matches = match_events([game], [poly], min_books_for_spread=1)
        assert matches == []

    def test_spread_candidate_allowed_when_prefilter_disabled(self):
        game = AllBookOdds(
            sport="soccer_epl",
            home="Tottenham Hotspur",
            away="Crystal Palace",
            commence_time="2026-02-25T20:00:00Z",
            books={},
            spread_books={
                "BookA": (
                    SportsOutcome("Tottenham Hotspur (-1.5)", -110, "BookA"),
                    SportsOutcome("Crystal Palace (+1.5)", -110, "BookA"),
                )
            },
        )
        poly = PolyMarket(
            event_title="Tottenham vs Crystal Palace",
            condition_id="cond-spread-mismatch-no-prefilter",
            outcome_a="Tottenham Hotspur",
            outcome_b="Crystal Palace",
            token_id_a="tok_a",
            token_id_b="tok_b",
            market_type="spread",
            question="Spread: Tottenham Hotspur (-2.5)",
            sport_tag="soccer",
            start_iso="2026-02-25T20:00:00Z",
        )
        matches = match_events([game], [poly], min_books_for_spread=0)
        assert len(matches) == 1

    def test_prefers_moneyline_when_spread_below_min_books_threshold(self):
        game = AllBookOdds(
            sport="soccer_epl",
            home="Tottenham Hotspur",
            away="Crystal Palace",
            commence_time="2026-02-25T20:00:00Z",
            books={
                "BookML": (
                    SportsOutcome("Tottenham Hotspur", -120, "BookML"),
                    SportsOutcome("Crystal Palace", 240, "BookML"),
                )
            },
            spread_books={
                "BookA": (
                    SportsOutcome("Tottenham Hotspur (-1.5)", -110, "BookA"),
                    SportsOutcome("Crystal Palace (+1.5)", -110, "BookA"),
                )
            },
        )
        spread = PolyMarket(
            event_title="Tottenham vs Crystal Palace",
            condition_id="cond-spread",
            outcome_a="Tottenham Hotspur",
            outcome_b="Crystal Palace",
            token_id_a="tok_sa",
            token_id_b="tok_sb",
            market_type="spread",
            question="Spread: Tottenham Hotspur (-1.5)",
            sport_tag="soccer",
            start_iso="2026-02-25T20:00:00Z",
        )
        moneyline = PolyMarket(
            event_title="Tottenham vs Crystal Palace",
            condition_id="cond-ml",
            outcome_a="Tottenham Hotspur",
            outcome_b="Crystal Palace",
            token_id_a="tok_ma",
            token_id_b="tok_mb",
            market_type="moneyline",
            sport_tag="soccer",
            start_iso="2026-02-25T20:00:00Z",
        )
        matches = match_events([game], [spread, moneyline], min_books_for_spread=2)
        assert len(matches) == 1
        assert matches[0].poly_market.condition_id == "cond-ml"

    def test_poly_spread_points_supports_trailing_signed_values(self):
        poly = PolyMarket(
            event_title="Tottenham vs Crystal Palace",
            condition_id="cond-spread-trailing",
            outcome_a="Tottenham Hotspur -1.5",
            outcome_b="Crystal Palace +1.5",
            token_id_a="tok_a",
            token_id_b="tok_b",
            market_type="spread",
            question="",
            sport_tag="soccer",
            start_iso="2026-02-25T20:00:00Z",
        )
        point_a, point_b = poly_spread_points(poly)
        assert point_a == -1.5
        assert point_b == 1.5

    def test_poly_spread_points_supports_named_question_without_parentheses(self):
        poly = PolyMarket(
            event_title="Tottenham vs Crystal Palace",
            condition_id="cond-spread-question",
            outcome_a="Tottenham Hotspur",
            outcome_b="Crystal Palace",
            token_id_a="tok_a",
            token_id_b="tok_b",
            market_type="spread",
            question="Spread: Tottenham Hotspur -1.5",
            sport_tag="soccer",
            start_iso="2026-02-25T20:00:00Z",
        )
        point_a, point_b = poly_spread_points(poly)
        assert point_a == -1.5
        assert point_b == 1.5
