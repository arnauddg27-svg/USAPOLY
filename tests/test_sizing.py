import pytest
from polyedge.execution.sizing import compute_bet_size, compute_event_cap_pct

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

    def test_event_pct_no_longer_hard_clamped_to_five_percent(self):
        size = compute_bet_size(
            adjusted_edge=0.40, fill_price=0.20, bankroll=1000,
            fraction_kelly=1.0, max_per_event_pct=0.50,
            total_exposure=0, max_total_pct=1.0, cash_buffer_pct=0.0,
            book_depth_usd=100000, min_bet=1.0, min_edge=0.03,
            max_per_sport_pct=1.0,
        )
        assert size == 150.0

    def test_event_cap_pct_allows_multiple_kelly_units(self):
        cap_pct = compute_event_cap_pct(
            adjusted_edge=0.04,
            fill_price=0.50,
            fraction_kelly=0.15,
            max_per_event_pct=0.02,
            min_edge=0.03,
        )
        single_kelly_cap = compute_event_cap_pct(
            adjusted_edge=0.04,
            fill_price=0.50,
            fraction_kelly=0.15,
            max_per_event_pct=0.02,
            event_cap_kelly_multiplier=1.0,
            min_edge=0.03,
        )
        assert cap_pct < 0.02
        assert cap_pct > single_kelly_cap > 0.0

        size = compute_bet_size(
            adjusted_edge=0.04,
            fill_price=0.50,
            bankroll=1000,
            fraction_kelly=0.15,
            max_per_event_pct=0.02,
            total_exposure=0,
            max_total_pct=0.30,
            cash_buffer_pct=0.20,
            book_depth_usd=1000,
            min_bet=1.0,
            min_edge=0.03,
        )
        assert size == round(1000 * single_kelly_cap, 2)

    def test_event_headroom_allows_multiple_entries_per_event(self):
        size = compute_bet_size(
            adjusted_edge=0.04, fill_price=0.50, bankroll=1000,
            fraction_kelly=0.15, max_per_event_pct=0.02,
            total_exposure=0, max_total_pct=0.30, cash_buffer_pct=0.20,
            book_depth_usd=100000, min_bet=1.0, min_edge=0.03,
            event_exposure=8.0,
        )
        single_kelly_cap = compute_event_cap_pct(
            adjusted_edge=0.04,
            fill_price=0.50,
            fraction_kelly=0.15,
            max_per_event_pct=0.02,
            event_cap_kelly_multiplier=1.0,
            min_edge=0.03,
        )
        assert 1000 * single_kelly_cap < 8.0
        assert size > 0

    def test_event_headroom_caps_to_remaining(self):
        size = compute_bet_size(
            adjusted_edge=0.20, fill_price=0.50, bankroll=1000,
            fraction_kelly=0.50, max_per_event_pct=0.02,
            total_exposure=0, max_total_pct=0.30, cash_buffer_pct=0.20,
            book_depth_usd=100000, min_bet=1.0, min_edge=0.03,
            event_exposure=15.0,
        )
        assert size == 5.0

    def test_event_headroom_below_min_bet_returns_zero(self):
        size = compute_bet_size(
            adjusted_edge=0.20, fill_price=0.50, bankroll=1000,
            fraction_kelly=0.50, max_per_event_pct=0.02,
            total_exposure=0, max_total_pct=0.30, cash_buffer_pct=0.20,
            book_depth_usd=100000, min_bet=5.0, min_edge=0.03,
            event_exposure=16.0,
        )
        assert size == 0

    def test_total_exposure_no_longer_hard_clamped_to_thirty_percent(self):
        size = compute_bet_size(
            adjusted_edge=0.40, fill_price=0.20, bankroll=1000,
            fraction_kelly=1.0, max_per_event_pct=0.50,
            total_exposure=290.0, max_total_pct=1.0, cash_buffer_pct=0.0,
            book_depth_usd=100000, min_bet=1.0, min_edge=0.03,
            max_per_sport_pct=1.0,
        )
        assert size == 150.0
