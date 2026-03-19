import time
import pytest
from polyedge.risk.limits import ExposureTracker
from polyedge.risk.circuit_breaker import CircuitBreaker

class TestExposureTracker:
    def test_record_and_check(self, tmp_path):
        t = ExposureTracker(state_path=tmp_path / "exposure_state.json")
        t.record_trade("basketball_nba", "cond1", 50.0)
        assert t.event_exposure("cond1") == 50.0
        assert t.sport_exposure("basketball_nba") == 50.0
        assert t.total_exposure() == 50.0

    def test_per_event_limit(self, tmp_path):
        t = ExposureTracker(state_path=tmp_path / "exposure_state.json")
        t.record_trade("nba", "cond1", 50.0)
        reason = t.can_trade("nba", "cond1", 10.0, bankroll=1000,
                             max_per_event=0.02)
        assert reason is not None  # 60 > 20, should be rejected
        assert "event_cap" in reason

    def test_daily_reset(self, tmp_path):
        t = ExposureTracker(state_path=tmp_path / "exposure_state.json")
        t.record_trade("nba", "cond1", 50.0)
        t.record_pnl(-100.0)
        assert t.daily_pnl == -100.0
        t.reset_daily()
        assert t.daily_pnl == 0.0

    def test_persists_event_exposure_across_instances(self, tmp_path):
        state_path = tmp_path / "exposure_state.json"
        start_ts = time.time() + 3600

        first = ExposureTracker(state_path=state_path)
        first.record_trade("nba", "event-key", 12.5, event_start_ts=start_ts)

        second = ExposureTracker(state_path=state_path)
        assert second.event_exposure("event-key") == pytest.approx(12.5)
        assert second.sport_exposure("nba") == pytest.approx(12.5)

    def test_prunes_stale_events_on_load(self, tmp_path):
        state_path = tmp_path / "exposure_state.json"
        first = ExposureTracker(state_path=state_path, event_retention_sec=0)
        first.record_trade("nba", "old-event", 20.0, event_start_ts=time.time() - 5)

        second = ExposureTracker(state_path=state_path, event_retention_sec=0)
        assert second.event_exposure("old-event") == 0.0
        assert second.total_exposure() == 0.0

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
