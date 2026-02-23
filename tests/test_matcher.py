import pytest

from polyedge.models import AllBookOdds, PolyMarket, SportsOutcome
from polyedge.pipeline.matcher import match_events, orient_book_outcomes

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

    def test_no_match(self):
        matches = match_events([_game()], [_poly("Miami Heat", "Chicago Bulls")])
        assert len(matches) == 0

    def test_title_fallback(self):
        poly = _poly("Yes", "No", "Will Lakers beat Celtics?")
        matches = match_events([_game()], [poly])
        assert len(matches) == 0

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
        poly = _poly(start_iso="2026-02-26T00:00:00Z")
        matches = match_events([game], [poly])
        assert len(matches) == 1

    def test_selects_closest_start_time_when_multiple_markets_match(self):
        game = _game(commence_time="2026-02-21T00:00:00Z")
        far = _poly(start_iso="2026-02-25T00:00:00Z", title="Far")
        close = _poly(start_iso="2026-02-21T01:00:00Z", title="Close")
        matches = match_events([game], [far, close])
        assert len(matches) == 1
        assert matches[0].poly_market.event_title == "Close"

    def test_orient_book_outcomes_handles_reversed_book_order(self):
        first = SportsOutcome("Los Angeles Lakers", 170, "DK")
        second = SportsOutcome("Boston Celtics", -200, "DK")
        oriented = orient_book_outcomes("Boston Celtics", "Los Angeles Lakers", first, second)
        assert oriented is not None
        a, b = oriented
        assert a.name == "Boston Celtics"
        assert b.name == "Los Angeles Lakers"
