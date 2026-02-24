import asyncio
import sys
import types
from unittest.mock import MagicMock

import pytest

from polyedge.main import PolyEdgeBot, summarize_exchange_open_orders
from polyedge.config import EdgeConfig
from polyedge.models import (
    AggregatedProb,
    AllBookOdds,
    EdgeOpportunity,
    MatchedEvent,
    PolyMarket,
    SportsOutcome,
)


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
    fake_client_mod = types.ModuleType("py_clob_client.client")

    class BrokenClient:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("boom")

    fake_client_mod.ClobClient = BrokenClient
    fake_constants_mod = types.ModuleType("py_clob_client.constants")
    fake_constants_mod.POLYGON = 137

    monkeypatch.setitem(sys.modules, "py_clob_client.client", fake_client_mod)
    monkeypatch.setitem(sys.modules, "py_clob_client.constants", fake_constants_mod)

    bot = PolyEdgeBot()
    bot.cfg.simulation_mode = False
    bot.cfg.poly_private_key = "0xabc"

    bot._init_poly_client()

    assert bot.poly_client is None
    assert bot.executor is None
    assert bot.order_mgr is None


def test_init_poly_client_skips_in_simulation_mode():
    bot = PolyEdgeBot()
    bot.cfg.simulation_mode = True
    bot.cfg.poly_private_key = "0xabc"

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
    cfg.poly_private_key = ""

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
    assert args[1] == market.condition_id
    assert args[2] > 0
    assert bot.trades_today == 1


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

    async def _fake_all_odds(_sports, _api_key):
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

    async def _fake_all_odds(_sports, _api_key):
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


def test_slow_cycle_falls_back_to_two_books_when_strict_threshold_empty(monkeypatch):
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

    async def _fake_all_odds(_sports, _api_key):
        return [game]

    async def _fake_polys(_sports):
        return [market]

    monkeypatch.setattr("polyedge.main.fetch_all_odds", _fake_all_odds)
    monkeypatch.setattr("polyedge.main.fetch_sports_markets", _fake_polys)

    asyncio.run(bot._slow_cycle())

    agg_cache = bot.odds_cache.get("aggregated") or {}
    agg = agg_cache.get("cond-fallback")
    assert agg is not None
    assert agg.books_used == 2
    assert bot.cfg.min_books == 2


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


def test_get_bankroll_probes_and_switches_identity(monkeypatch):
    fake_clob_types_mod = types.ModuleType("py_clob_client.clob_types")

    class BalanceAllowanceParams:
        def __init__(self, asset_type=None, token_id=None, signature_type=-1):
            self.asset_type = asset_type
            self.token_id = token_id
            self.signature_type = signature_type

    class AssetType:
        COLLATERAL = "COLLATERAL"

    fake_clob_types_mod.BalanceAllowanceParams = BalanceAllowanceParams
    fake_clob_types_mod.AssetType = AssetType

    fake_constants_mod = types.ModuleType("py_clob_client.constants")
    fake_constants_mod.POLYGON = 137

    class FakeClobClient:
        def __init__(self, _host, key=None, chain_id=None, signature_type=None, funder=None):
            self.key = key
            self.chain_id = chain_id
            self.builder = types.SimpleNamespace(sig_type=signature_type, funder=funder)

        def set_api_creds(self, _creds):
            return None

        def create_or_derive_api_creds(self):
            return {}

        def get_balance_allowance(self, params):
            sig = self.builder.sig_type if getattr(params, "signature_type", -1) == -1 else params.signature_type
            funder = self.builder.funder
            if sig == 2 and funder == "0xfund":
                return {"balance": 250_000_000}
            return {"balance": 0}

    fake_client_mod = types.ModuleType("py_clob_client.client")
    fake_client_mod.ClobClient = FakeClobClient

    class DummyRedeemer:
        def __init__(self, *args, **kwargs):
            self.enabled = False
            self.disable_reason = "test"
            self.holder_address = kwargs.get("holder_address", "")

    monkeypatch.setitem(sys.modules, "py_clob_client.clob_types", fake_clob_types_mod)
    monkeypatch.setitem(sys.modules, "py_clob_client.constants", fake_constants_mod)
    monkeypatch.setitem(sys.modules, "py_clob_client.client", fake_client_mod)
    monkeypatch.setattr("polyedge.main.AutoRedeemer", DummyRedeemer)

    bot = PolyEdgeBot()
    bot.cfg.poly_private_key = "0xabc"
    bot.cfg.poly_signature_type = 0
    bot.cfg.poly_funder_address = "0xfund"
    bot.poly_client = FakeClobClient("https://clob.polymarket.com", key="0xabc", chain_id=137, signature_type=0, funder=None)
    bot._active_sig_type = 0
    bot._active_funder = None

    balance = bot._get_bankroll()

    assert balance == pytest.approx(250.0)
    assert bot._active_sig_type == 2
    assert bot._active_funder == "0xfund"
