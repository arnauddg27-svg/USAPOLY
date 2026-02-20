import pytest
from polyedge.models import BookLevel, OrderBook
from polyedge.data.polymarket import compute_avg_fill_price

class TestFillSimulation:
    def test_single_level_exact(self):
        asks = [BookLevel(price=0.55, size=500)]
        avg, filled = compute_avg_fill_price(asks, 500)
        assert abs(avg - 0.55) < 0.001
        assert abs(filled - 500) < 0.01

    def test_multi_level_walk(self):
        asks = [BookLevel(price=0.55, size=200), BookLevel(price=0.56, size=300)]
        avg, filled = compute_avg_fill_price(asks, 400)
        # 200*0.55 + 200*0.56 = 110 + 112 = 222, avg = 222/400 = 0.555
        assert abs(avg - 0.555) < 0.001
        assert abs(filled - 400) < 0.01

    def test_insufficient_depth(self):
        asks = [BookLevel(price=0.55, size=100)]
        avg, filled = compute_avg_fill_price(asks, 500)
        assert abs(filled - 100) < 0.01
        assert abs(avg - 0.55) < 0.001

    def test_empty_book(self):
        avg, filled = compute_avg_fill_price([], 500)
        assert filled == 0
        assert avg == 0
