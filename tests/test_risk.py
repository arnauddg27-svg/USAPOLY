import time
import pytest
from polyedge.risk.limits import ExposureTracker
from polyedge.risk.circuit_breaker import CircuitBreaker

class TestExposureTracker:
    def test_record_and_check(self):
        t = ExposureTracker()
        t.record_trade("basketball_nba", "cond1", 50.0)
        assert t.event_exposure("cond1") == 50.0
        assert t.sport_exposure("basketball_nba") == 50.0
        assert t.total_exposure() == 50.0

    def test_per_event_limit(self):
        t = ExposureTracker()
        t.record_trade("nba", "cond1", 50.0)
        assert t.can_trade("nba", "cond1", 10.0, bankroll=1000,
                           max_per_event=0.02) is False  # 60 > 20

    def test_daily_reset(self):
        t = ExposureTracker()
        t.record_trade("nba", "cond1", 50.0)
        t.record_pnl(-100.0)
        assert t.daily_pnl == -100.0
        t.reset_daily()
        assert t.daily_pnl == 0.0

class TestCircuitBreaker:
    def test_stale_odds(self):
        cb = CircuitBreaker(stale_timeout_sec=10)
        cb.record_odds_fetch()
        assert cb.is_tripped() is False
        cb._last_odds_fetch = time.time() - 15
        assert cb.is_tripped() is True

    def test_api_errors(self):
        cb = CircuitBreaker(max_consecutive_errors=3)
        cb.record_api_error()
        cb.record_api_error()
        assert cb.is_tripped() is False
        cb.record_api_error()
        assert cb.is_tripped() is True

    def test_clear_on_success(self):
        cb = CircuitBreaker(max_consecutive_errors=3)
        cb.record_api_error()
        cb.record_api_error()
        cb.record_api_error()
        assert cb.is_tripped() is True
        cb.record_api_success()
        assert cb.is_tripped() is False
