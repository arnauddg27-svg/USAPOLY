import asyncio
import sys
import types
from unittest.mock import MagicMock

import pytest

from polyedge.main import PolyEdgeBot, _event_risk_id, summarize_exchange_open_orders
from polyedge.config import EdgeConfig
from polyedge.models import (
    AggregatedProb,
    AllBookOdds,
    EdgeOpportunity,
    MatchedEvent,
    PolyMarket,
    SportsOutcome,
)


def test_event_risk_id_groups_markets_for_same_game():
    game = AllBookOdds("basketball_nba", "Team A", "Team B", "2099-02-02T00:00:00Z", {})
    market_ml = PolyMarket("Team A vs Team B", "cond-ml", "Team A", "Team B", "tok-a", "tok-b")
    market_spread = PolyMarket("Team A vs Team B", "cond-spread", "Team A", "Team B", "tok-a2", "tok-b2")

    matched_ml = MatchedEvent("basketball_nba", game, market_ml, "Team A", "Team B")
    matched_spread = MatchedEvent("basketball_nba", game, market_spread, "Team A", "Team B")

    assert _event_risk_id(matched_ml) == _event_risk_id(matched_spread)


def test_fast_cycle_checks_expiry_when_bankroll_unavailable(monkeypatch):
    bot = PolyEdgeBot()
    bot.match_cache.set("matches", [])
    bot.order_mgr = MagicMock()
    bot.executor = MagicMock()
    bot.poly_client = object()
    monkeypatch.setattr(bot, "_get_bankroll", lambda: None)

    asyncio.run(bot._fast_cycle())

    bot.order_mgr.check_expiry.assert_called_once_with(
        close_before_event_sec=bot.cfg.close_orders_before_event_sec
    )


def test_fast_cycle_dry_run_does_not_require_bankroll(monkeypatch):
    bot = PolyEdgeBot()
    bot.match_cache.set("matches", [])
    bot.executor = None
    bot.poly_client = None

    def _unexpected_bankroll_call():
        raise AssertionError("_get_bankroll should not be called in dry-run mode")

    monkeypatch.setattr(bot, "_get_bankroll", _unexpected_bankroll_call)

    asyncio.run(bot._fast_cycle())


def test_init_poly_client_falls_back_to_dry_run_on_init_error(monkeypatch):
    fake_pm_mod = types.ModuleType("polymarket_us")

    class BrokenClient:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("boom")

    fake_pm_mod.PolymarketUS = BrokenClient
    monkeypatch.setitem(sys.modules, "polymarket_us", fake_pm_mod)

    bot = PolyEdgeBot()
    bot.cfg.simulation_mode = False
    bot.cfg.polymarket_key_id = "test-key"
    bot.cfg.polymarket_secret_key = "test-secret"

    bot._init_poly_client()

    assert bot.poly_client is None
    assert bot.executor is None
    assert bot.order_mgr is None


def test_init_poly_client_skips_in_simulation_mode():
    bot = PolyEdgeBot()
    bot.cfg.simulation_mode = True
    bot.cfg.polymarket_key_id = "test-key"
    bot.cfg.polymarket_secret_key = "test-secret"

    bot._init_poly_client()

    assert bot.poly_client is None
    assert bot.executor is None
    assert bot.order_mgr is None


def test_run_clamps_invalid_timing_config(monkeypatch, tmp_path):
    bot = PolyEdgeBot()
    bot._init_poly_client = lambda: None

    cfg = EdgeConfig()
    cfg.slow_cycle_multiplier = 0
    cfg.poll_interval_sec = 0
    cfg.polymarket_key_id = ""

    calls = {"slow": 0, "fast": 0, "sleep": []}

    async def _slow_cycle():
        calls["slow"] += 1

    async def _fast_cycle():
        calls["fast"] += 1

    class StopLoop(Exception):
        pass

    async def _fake_wait_for(awaitable, timeout):
        if hasattr(awaitable, "close"):
            awaitable.close()
        calls["sleep"].append(timeout)
        raise StopLoop()

    bot._slow_cycle = _slow_cycle
    bot._fast_cycle = _fast_cycle
    monkeypatch.setattr("polyedge.main.EdgeConfig.from_env", lambda: cfg)
    monkeypatch.setattr("polyedge.main.KILLSWITCH_PATH", tmp_path / "killswitch.json")
    monkeypatch.setattr("polyedge.main.asyncio.wait_for", _fake_wait_for)

    with pytest.raises(StopLoop):
        asyncio.run(bot.run())

    assert calls["slow"] == 1
    assert calls["fast"] == 1
    assert calls["sleep"][-1] == 1


def test_summarize_exchange_open_orders_with_list_payload():
    raw = [
        {"status": "LIVE", "price": "0.5", "original_size": "10", "size_matched": "2"},
        {"status": "CANCELLED", "price": "0.4", "original_size": "20"},
        {"status": "OPEN", "price": "0.3", "remaining_size": "5"},
    ]
    count, notional = summarize_exchange_open_orders(raw)
    assert count == 2
    assert notional == pytest.approx((0.5 * 8) + (0.3 * 5))


def test_summarize_exchange_open_orders_with_dict_payload():
    raw = {
        "orders": [
            {"status": "ACTIVE", "price": "0.42", "size": "11"},
            {"status": "FILLED", "price": "0.25", "size": "8"},
        ]
    }
    count, notional = summarize_exchange_open_orders(raw)
    assert count == 1
    assert notional == pytest.approx(0.42 * 11)


def test_fast_cycle_no_resting_orders_skips_tracking_but_records_exposure(monkeypatch):
    bot = PolyEdgeBot()
    bot.cfg.simulation_mode = False
    bot.cfg.trading_enabled = True
    bot.cfg.no_resting_orders = True
    bot.cfg.min_hours_before_event = 0.0
    bot.cfg.min_bet_usd = 1.0

    game = AllBookOdds("basketball_nba", "A", "B", "2099-01-01T00:00:00Z", {})
    market = PolyMarket("A vs B", "cond-x", "A", "B", "tok-a", "tok-b")
    matched = MatchedEvent("basketball_nba", game, market, "A", "B")
    agg = AggregatedProb(0.60, 0.40, 8, 0, "power", [])
    opp = EdgeOpportunity(
        matched_event=matched,
        aggregated=agg,
        buy_outcome="a",
        buy_token_id="tok-a",
        true_prob=0.60,
        poly_mid=0.50,
        poly_fill_price=0.51,
        poly_depth_shares=500.0,
        poly_spread=0.01,
        raw_edge=0.09,
        adjusted_edge=0.08,
        bet_usd=12.0,
        shares=23,
    )

    bot.match_cache.set("matches", [matched])
    bot.odds_cache.set("aggregated", {market.condition_id: agg})
    bot.order_mgr = MagicMock()
    bot.executor = MagicMock()
    bot.executor.place_order.return_value = MagicMock(order_id="ord-1", event_title="", event_start_ts=None)
    bot.poly_client = object()
    bot.exposure = MagicMock()
    bot.exposure.can_trade.return_value = True
    bot.exposure.total_exposure.return_value = 0.0
    bot.exposure.event_exposure.return_value = 0.0
    bot.exposure.sport_exposure.return_value = 0.0
    monkeypatch.setattr(bot, "_get_bankroll", lambda: 1000.0)

    async def _fake_fetch_order_book(_token_id):
        return object()

    monkeypatch.setattr("polyedge.main.fetch_order_book", _fake_fetch_order_book)
    monkeypatch.setattr("polyedge.main.detect_edge", lambda *_args, **_kwargs: [opp])

    asyncio.run(bot._fast_cycle())

    bot.executor.place_order.assert_called_once()
    bot.order_mgr.track.assert_not_called()
    bot.exposure.record_trade.assert_called_once()
    args = bot.exposure.record_trade.call_args.args
    assert args[0] == "basketball_nba"
    assert args[1] == _event_risk_id(matched)
    assert args[2] > 0
    assert bot.trades_today == 1


def test_fast_cycle_skips_halftime_markets(monkeypatch):
    bot = PolyEdgeBot()
    bot.cfg.simulation_mode = False
    bot.cfg.trading_enabled = True
    bot.cfg.no_resting_orders = True
    bot.cfg.min_hours_before_event = 0.0

    game = AllBookOdds("basketball_nba", "Raptors", "Wizards", "2099-01-01T00:00:00Z", {})
    market = PolyMarket(
        "Raptors vs. Wizards: 1H Moneyline",
        "cond-1h",
        "Raptors",
        "Wizards",
        "tok-a",
        "tok-b",
        question="Raptors vs. Wizards: 1H Moneyline",
    )
    matched = MatchedEvent("basketball_nba", game, market, "Raptors", "Wizards")
    agg = AggregatedProb(0.55, 0.45, 8, 0, "power", [])

    bot.match_cache.set("matches", [matched])
    bot.odds_cache.set("aggregated", {market.condition_id: agg})
    bot.order_mgr = MagicMock()
    bot.executor = MagicMock()
    bot.poly_client = object()
    bot.exposure = MagicMock()
    bot.exposure.total_exposure.return_value = 0.0
    bot.exposure.event_exposure.return_value = 0.0
    bot.exposure.sport_exposure.return_value = 0.0
    monkeypatch.setattr(bot, "_get_bankroll", lambda: 1000.0)

    async def _unexpected_fetch_order_book(_token_id):
        raise AssertionError("fetch_order_book should not be called for halftime markets")

    monkeypatch.setattr("polyedge.main.fetch_order_book", _unexpected_fetch_order_book)
    monkeypatch.setattr(
        "polyedge.main.detect_edge",
        lambda *_args, **_kwargs: pytest.fail("detect_edge should not be called for halftime markets"),
    )

    asyncio.run(bot._fast_cycle())

    bot.executor.place_order.assert_not_called()
    stats = bot.last_fast_cycle
    assert stats["skipped_segment_market"] == 1


def test_slow_cycle_orients_book_outcomes_before_aggregation(monkeypatch):
    bot = PolyEdgeBot()
    bot.cfg.min_books = 1
    bot.cfg.devig_method = "power"

    game = AllBookOdds(
        sport="basketball_nba",
        home="Chicago Bulls",
        away="Miami Heat",
        commence_time="2099-01-01T00:00:00Z",
        books={
            # Reversed order from team_a/team_b orientation.
            "BookA": (
                SportsOutcome("Miami Heat", -180, "BookA"),
                SportsOutcome("Chicago Bulls", 150, "BookA"),
            )
        },
    )
    market = PolyMarket(
        event_title="Bulls vs Heat",
        condition_id="cond-orient",
        outcome_a="Chicago Bulls",
        outcome_b="Miami Heat",
        token_id_a="tok-a",
        token_id_b="tok-b",
        market_type="moneyline",
        sport_tag="nba",
    )

    async def _fake_all_odds(_sports, _api_key, *_args, **_kwargs):
        return [game]

    async def _fake_polys(_sports):
        return [market]

    monkeypatch.setattr("polyedge.main.fetch_all_odds", _fake_all_odds)
    monkeypatch.setattr("polyedge.main.fetch_sports_markets", _fake_polys)

    asyncio.run(bot._slow_cycle())

    agg_cache = bot.odds_cache.get("aggregated") or {}
    agg = agg_cache.get("cond-orient")
    assert agg is not None
    # Bulls are +150 in the single bookmaker line, so consensus for side A
    # must remain under 50% after orientation.
    assert agg.prob_a < 0.5


def test_slow_cycle_uses_spread_books_for_spread_markets(monkeypatch):
    bot = PolyEdgeBot()
    bot.cfg.min_books = 1
    bot.cfg.devig_method = "power"

    game = AllBookOdds(
        sport="basketball_nba",
        home="Chicago Bulls",
        away="Miami Heat",
        commence_time="2099-01-01T00:00:00Z",
        books={},
        spread_books={
            "BookA": (
                SportsOutcome("Miami Heat (+2.5)", -110, "BookA"),
                SportsOutcome("Chicago Bulls (-2.5)", -110, "BookA"),
            )
        },
    )
    market = PolyMarket(
        event_title="Bulls vs Heat Spread",
        condition_id="cond-spread",
        outcome_a="Chicago Bulls (-2.5)",
        outcome_b="Miami Heat (+2.5)",
        token_id_a="tok-a",
        token_id_b="tok-b",
        market_type="spread",
        sport_tag="nba",
    )

    async def _fake_all_odds(_sports, _api_key, *_args, **_kwargs):
        return [game]

    async def _fake_polys(_sports):
        return [market]

    monkeypatch.setattr("polyedge.main.fetch_all_odds", _fake_all_odds)
    monkeypatch.setattr("polyedge.main.fetch_sports_markets", _fake_polys)

    asyncio.run(bot._slow_cycle())

    agg_cache = bot.odds_cache.get("aggregated") or {}
    agg = agg_cache.get("cond-spread")
    assert agg is not None
    assert agg.books_used == 1


def test_slow_cycle_keeps_strict_min_books_when_threshold_empty(monkeypatch):
    bot = PolyEdgeBot()
    bot.cfg.min_books = 6
    bot.cfg.devig_method = "power"

    game = AllBookOdds(
        sport="basketball_nba",
        home="Chicago Bulls",
        away="Miami Heat",
        commence_time="2099-01-01T00:00:00Z",
        books={
            "BookA": (
                SportsOutcome("Chicago Bulls", -110, "BookA"),
                SportsOutcome("Miami Heat", -110, "BookA"),
            ),
            "BookB": (
                SportsOutcome("Chicago Bulls", -112, "BookB"),
                SportsOutcome("Miami Heat", -108, "BookB"),
            ),
        },
    )
    market = PolyMarket(
        event_title="Bulls vs Heat",
        condition_id="cond-fallback",
        outcome_a="Chicago Bulls",
        outcome_b="Miami Heat",
        token_id_a="tok-a",
        token_id_b="tok-b",
        market_type="moneyline",
        sport_tag="nba",
    )

    async def _fake_all_odds(_sports, _api_key, *_args, **_kwargs):
        return [game]

    async def _fake_polys(_sports):
        return [market]

    monkeypatch.setattr("polyedge.main.fetch_all_odds", _fake_all_odds)
    monkeypatch.setattr("polyedge.main.fetch_sports_markets", _fake_polys)

    asyncio.run(bot._slow_cycle())

    agg_cache = bot.odds_cache.get("aggregated") or {}
    agg = agg_cache.get("cond-fallback")
    assert agg is None
    assert bot.cfg.min_books == 6


def test_slow_cycle_uses_soccer_min_books_override(monkeypatch):
    bot = PolyEdgeBot()
    bot.cfg.min_books = 4
    bot.cfg.soccer_min_books = 3
    bot.cfg.devig_method = "power"

    game = AllBookOdds(
        sport="soccer_epl",
        home="Tottenham Hotspur",
        away="Crystal Palace",
        commence_time="2099-01-01T00:00:00Z",
        books={
            "BookA": (
                SportsOutcome("Tottenham Hotspur", -110, "BookA"),
                SportsOutcome("Crystal Palace", 220, "BookA"),
            ),
            "BookB": (
                SportsOutcome("Tottenham Hotspur", -112, "BookB"),
                SportsOutcome("Crystal Palace", 225, "BookB"),
            ),
            "BookC": (
                SportsOutcome("Tottenham Hotspur", -108, "BookC"),
                SportsOutcome("Crystal Palace", 215, "BookC"),
            ),
        },
    )
    market = PolyMarket(
        event_title="Tottenham vs Crystal Palace",
        condition_id="cond-soccer-min-books",
        outcome_a="Tottenham Hotspur",
        outcome_b="Crystal Palace",
        token_id_a="tok-a",
        token_id_b="tok-b",
        market_type="moneyline",
        sport_tag="soccer",
    )

    async def _fake_all_odds(_sports, _api_key, *_args, **_kwargs):
        return [game]

    async def _fake_polys(_sports):
        return [market]

    monkeypatch.setattr("polyedge.main.fetch_all_odds", _fake_all_odds)
    monkeypatch.setattr("polyedge.main.fetch_sports_markets", _fake_polys)

    asyncio.run(bot._slow_cycle())

    agg_cache = bot.odds_cache.get("aggregated") or {}
    agg = agg_cache.get("cond-soccer-min-books")
    assert agg is not None
    assert agg.books_used == 3


def test_fast_cycle_blocks_opposite_side_for_same_condition(monkeypatch):
    bot = PolyEdgeBot()
    bot.cfg.simulation_mode = False
    bot.cfg.trading_enabled = True
    bot.cfg.no_resting_orders = True
    bot.cfg.min_hours_before_event = 0.0
    bot.cfg.min_bet_usd = 1.0

    game = AllBookOdds("basketball_nba", "A", "B", "2099-01-01T00:00:00Z", {})
    market = PolyMarket("A vs B", "cond-lock", "A", "B", "tok-a", "tok-b")
    matched = MatchedEvent("basketball_nba", game, market, "A", "B")
    agg = AggregatedProb(0.40, 0.60, 8, 0, "power", [])
    opp = EdgeOpportunity(
        matched_event=matched,
        aggregated=agg,
        buy_outcome="b",
        buy_token_id="tok-b",
        true_prob=0.60,
        poly_mid=0.50,
        poly_fill_price=0.45,
        poly_depth_shares=500.0,
        poly_spread=0.01,
        raw_edge=0.10,
        adjusted_edge=0.09,
        bet_usd=12.0,
        shares=26,
    )

    bot.match_cache.set("matches", [matched])
    bot.odds_cache.set("aggregated", {market.condition_id: agg})
    bot.condition_side_lock = {market.condition_id: "a"}
    bot.order_mgr = MagicMock()
    bot.executor = MagicMock()
    bot.poly_client = object()
    bot.exposure = MagicMock()
    bot.exposure.can_trade.return_value = True
    bot.exposure.total_exposure.return_value = 0.0
    bot.exposure.event_exposure.return_value = 0.0
    bot.exposure.sport_exposure.return_value = 0.0
    monkeypatch.setattr(bot, "_get_bankroll", lambda: 1000.0)

    async def _fake_fetch_order_book(_token_id):
        return object()

    monkeypatch.setattr("polyedge.main.fetch_order_book", _fake_fetch_order_book)
    monkeypatch.setattr("polyedge.main.detect_edge", lambda *_args, **_kwargs: [opp])

    asyncio.run(bot._fast_cycle())

    bot.executor.place_order.assert_not_called()


def test_fast_cycle_skips_started_events(monkeypatch):
    bot = PolyEdgeBot()
    bot.cfg.simulation_mode = False
    bot.cfg.trading_enabled = True
    bot.cfg.no_resting_orders = True
    bot.cfg.min_hours_before_event = 0.0

    game = AllBookOdds("basketball_nba", "A", "B", "2000-01-01T00:00:00Z", {})
    market = PolyMarket("A vs B", "cond-started", "A", "B", "tok-a", "tok-b")
    matched = MatchedEvent("basketball_nba", game, market, "A", "B")
    agg = AggregatedProb(0.55, 0.45, 8, 0, "power", [])

    bot.match_cache.set("matches", [matched])
    bot.odds_cache.set("aggregated", {market.condition_id: agg})
    bot.order_mgr = MagicMock()
    bot.executor = MagicMock()
    bot.poly_client = object()
    bot.exposure = MagicMock()
    bot.exposure.can_trade.return_value = True
    bot.exposure.total_exposure.return_value = 0.0
    bot.exposure.event_exposure.return_value = 0.0
    bot.exposure.sport_exposure.return_value = 0.0
    monkeypatch.setattr(bot, "_get_bankroll", lambda: 1000.0)

    async def _fake_fetch_order_book(_token_id):
        return object()

    monkeypatch.setattr("polyedge.main.fetch_order_book", _fake_fetch_order_book)
    monkeypatch.setattr("polyedge.main.detect_edge", lambda *_args, **_kwargs: pytest.fail("detect_edge should not be called for started events"))

    asyncio.run(bot._fast_cycle())

    bot.executor.place_order.assert_not_called()


def test_fast_cycle_records_skip_stats_when_no_aggregate_available():
    bot = PolyEdgeBot()
    game = AllBookOdds("basketball_nba", "A", "B", "2099-01-01T00:00:00Z", {})
    market = PolyMarket("A vs B", "cond-no-agg", "A", "B", "tok-a", "tok-b")
    matched = MatchedEvent("basketball_nba", game, market, "A", "B")

    bot.match_cache.set("matches", [matched])
    bot.odds_cache.set("aggregated", {})

    asyncio.run(bot._fast_cycle())

    stats = bot.last_fast_cycle
    assert stats["status"] == "completed"
    assert stats["matches_total"] == 1
    assert stats["with_agg"] == 0
    assert stats["skipped_no_agg"] == 1
    assert stats["submitted"] == 0


def test_fast_cycle_records_bankroll_blocker_for_live_mode(monkeypatch):
    bot = PolyEdgeBot()
    bot.cfg.simulation_mode = False
    bot.cfg.trading_enabled = True
    bot.executor = MagicMock()
    bot.poly_client = object()
    bot.match_cache.set("matches", [])
    monkeypatch.setattr(bot, "_get_bankroll", lambda: None)

    asyncio.run(bot._fast_cycle())

    stats = bot.last_fast_cycle
    assert stats["status"] == "blocked_bankroll_unavailable"
    assert stats["blocked_bankroll_unavailable"] == 1


def test_cashout_limit_price_never_exceeds_clob_max():
    # tick=0.02 => CLOB max is 0.98; min_limit is above max.
    # Helper should return clob_max so caller can skip safely.
    price = PolyEdgeBot._cashout_limit_price(cur_price=0.995, tick=0.02, min_limit=0.99)
    assert price == pytest.approx(0.98)
