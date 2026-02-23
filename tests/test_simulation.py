from polyedge.models import (
    AggregatedProb,
    AllBookOdds,
    EdgeOpportunity,
    MatchedEvent,
    PolyMarket,
)
from polyedge.simulation.paper import PaperSimulator


def _make_opportunity(true_prob=0.62, fill=0.55, bet_usd=20.0):
    game = AllBookOdds("nba", "A", "B", "2026-02-21T12:00:00Z", {})
    poly = PolyMarket("Game", "cond1", "A", "B", "tok_a", "tok_b")
    matched = MatchedEvent("nba", game, poly, "A", "B")
    agg = AggregatedProb(0.62, 0.38, 8, 0, "power", [])
    return EdgeOpportunity(
        matched_event=matched,
        aggregated=agg,
        buy_outcome="a",
        buy_token_id="tok_a",
        true_prob=true_prob,
        poly_mid=0.55,
        poly_fill_price=fill,
        poly_depth_shares=800,
        poly_spread=0.005,
        raw_edge=0.07,
        adjusted_edge=0.06,
        bet_usd=bet_usd,
        shares=int(bet_usd / fill),
    )


def test_paper_simulator_records_expected_pnl(tmp_path):
    sim = PaperSimulator(start_bankroll=1000.0, state_path=tmp_path / "sim.json")
    opp = _make_opportunity(true_prob=0.62, fill=0.55, bet_usd=20.0)
    result = sim.record_bet(opp, cycle=3)

    # expected_pnl = 20 * ((0.62 / 0.55) - 1) = 2.5454...
    assert round(result["expected_pnl_usd"], 4) == 2.5455
    assert result["cycle"] == 3
    assert sim.state.bet_count == 1
    assert round(sim.current_bankroll, 4) == 1002.5455


def test_paper_simulator_persists_state(tmp_path):
    state_path = tmp_path / "sim.json"
    sim = PaperSimulator(start_bankroll=1000.0, state_path=state_path)
    sim.record_bet(_make_opportunity(), cycle=1)

    sim2 = PaperSimulator(start_bankroll=5000.0, state_path=state_path)
    assert sim2.state.bet_count == 1
    assert sim2.state.start_bankroll == 1000.0
