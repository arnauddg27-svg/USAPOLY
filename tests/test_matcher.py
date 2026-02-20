import pytest
from polyedge.models import AllBookOdds, SportsOutcome, PolyMarket
from polyedge.pipeline.matcher import match_events

def _game(home="Boston Celtics", away="Los Angeles Lakers", sport="basketball_nba"):
    return AllBookOdds(sport=sport, home=home, away=away, commence_time="2026-02-21T00:00:00Z",
                       books={"DK": (SportsOutcome(home, -200, "DK"), SportsOutcome(away, 170, "DK"))})

def _poly(outcome_a="Boston Celtics", outcome_b="Los Angeles Lakers", title="NBA: Celtics vs Lakers"):
    return PolyMarket(event_title=title, condition_id="cond1",
                      outcome_a=outcome_a, outcome_b=outcome_b,
                      token_id_a="tok_a", token_id_b="tok_b")

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
