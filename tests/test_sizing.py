import pytest
from polyedge.execution.sizing import compute_bet_size

class TestSizing:
    def test_basic_kelly(self):
        size = compute_bet_size(
            adjusted_edge=0.06, fill_price=0.55, bankroll=1000,
            fraction_kelly=0.15, max_per_event_pct=0.02,
            total_exposure=0, max_total_pct=0.30, cash_buffer_pct=0.20,
            book_depth_usd=500, min_bet=5.0,
        )
        assert size > 5.0
        assert size <= 20.0

    def test_min_bet_floor(self):
        size = compute_bet_size(
            adjusted_edge=0.06, fill_price=0.55, bankroll=50,
            fraction_kelly=0.15, max_per_event_pct=0.02,
            total_exposure=0, max_total_pct=0.30, cash_buffer_pct=0.20,
            book_depth_usd=500, min_bet=5.0,
        )
        assert size == 0

    def test_exposure_cap(self):
        size = compute_bet_size(
            adjusted_edge=0.10, fill_price=0.40, bankroll=1000,
            fraction_kelly=0.25, max_per_event_pct=0.02,
            total_exposure=290, max_total_pct=0.30, cash_buffer_pct=0.20,
            book_depth_usd=500, min_bet=5.0,
        )
        assert size <= 10.0

    def test_liquidity_cap(self):
        size = compute_bet_size(
            adjusted_edge=0.10, fill_price=0.40, bankroll=10000,
            fraction_kelly=0.25, max_per_event_pct=0.05,
            total_exposure=0, max_total_pct=0.30, cash_buffer_pct=0.20,
            book_depth_usd=20, min_bet=5.0,
        )
        assert size <= 16.0

    def test_higher_edge_produces_larger_size(self):
        low = compute_bet_size(
            adjusted_edge=0.04, fill_price=0.50, bankroll=1000,
            fraction_kelly=0.15, max_per_event_pct=0.05,
            total_exposure=0, max_total_pct=0.30, cash_buffer_pct=0.20,
            book_depth_usd=1000, min_bet=1.0, min_edge=0.03,
        )
        high = compute_bet_size(
            adjusted_edge=0.12, fill_price=0.50, bankroll=1000,
            fraction_kelly=0.15, max_per_event_pct=0.05,
            total_exposure=0, max_total_pct=0.30, cash_buffer_pct=0.20,
            book_depth_usd=1000, min_bet=1.0, min_edge=0.03,
        )
        assert high > low

    def test_event_pct_hard_cap_prevents_oversized_bet(self):
        size = compute_bet_size(
            adjusted_edge=0.40, fill_price=0.20, bankroll=1000,
            fraction_kelly=1.0, max_per_event_pct=0.50,
            total_exposure=0, max_total_pct=1.0, cash_buffer_pct=0.0,
            book_depth_usd=100000, min_bet=1.0, min_edge=0.03,
        )
        assert size <= 50.0

    def test_total_exposure_hard_cap_stops_over_30_percent(self):
        size = compute_bet_size(
            adjusted_edge=0.40, fill_price=0.20, bankroll=1000,
            fraction_kelly=1.0, max_per_event_pct=0.50,
            total_exposure=290.0, max_total_pct=1.0, cash_buffer_pct=0.0,
            book_depth_usd=100000, min_bet=1.0, min_edge=0.03,
        )
        # With a hard 30% total cap on $1,000 bankroll, only $10 headroom remains.
        assert size <= 10.0
