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
