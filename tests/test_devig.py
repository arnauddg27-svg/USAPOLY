import pytest
from polyedge.pipeline.devig import multiplicative_devig, power_devig


class TestMultiplicativeDevig:
    def test_even_odds(self):
        # -110 / -110 = 1.909 / 1.909 => implied 0.5238 each => overround 1.0476
        p_a, p_b = multiplicative_devig(1.909, 1.909)
        assert abs(p_a - 0.5) < 0.001
        assert abs(p_b - 0.5) < 0.001
        assert abs(p_a + p_b - 1.0) < 0.0001

    def test_favorite_underdog(self):
        # -200 = 1.50 decimal, +170 = 2.70 decimal
        p_a, p_b = multiplicative_devig(1.50, 2.70)
        assert abs(p_a + p_b - 1.0) < 0.0001
        assert p_a > p_b
        assert abs(p_a - 0.6429) < 0.01

    def test_no_vig_passthrough(self):
        p_a, p_b = multiplicative_devig(2.0, 2.0)
        assert abs(p_a - 0.5) < 0.0001
        assert abs(p_b - 0.5) < 0.0001


class TestPowerDevig:
    def test_even_odds(self):
        p_a, p_b = power_devig(1.909, 1.909)
        assert abs(p_a - 0.5) < 0.001
        assert abs(p_b - 0.5) < 0.001
        assert abs(p_a + p_b - 1.0) < 0.0001

    def test_favorite_underdog(self):
        p_a, p_b = power_devig(1.50, 2.70)
        assert abs(p_a + p_b - 1.0) < 0.0001
        assert p_a > p_b

    def test_heavy_favorite_differs_from_multiplicative(self):
        # -500 = 1.20, +400 = 5.0
        m_a, m_b = multiplicative_devig(1.20, 5.0)
        p_a, p_b = power_devig(1.20, 5.0)
        assert abs(m_a + m_b - 1.0) < 0.0001
        assert abs(p_a + p_b - 1.0) < 0.0001
        # Power method with k>1 gives longshot LOWER prob than multiplicative
        assert p_b < m_b

    def test_convergence(self):
        p_a, p_b = power_devig(1.05, 20.0)
        assert abs(p_a + p_b - 1.0) < 0.001
