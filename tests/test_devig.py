import pytest
from polyedge.pipeline.devig import multiplicative_devig, power_devig, devig_three_way


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


class TestThreeWayDevig:
    def test_soccer_three_way_sums_to_one(self):
        """3-way devig: home + draw + away probs should sum to ~1.0."""
        # Home -182 (1.549), Draw +280 (3.80), Away +350 (4.50)
        p_a, p_b = devig_three_way(1.549, 3.80, 4.50, method="multiplicative")
        # Estimate draw prob from total
        imp_d = 1.0 / 3.80
        total = 1.0 / 1.549 + imp_d + 1.0 / 4.50
        p_d = imp_d / total
        assert abs(p_a + p_d + p_b - 1.0) < 0.01

    def test_soccer_home_prob_lower_than_two_way(self):
        """3-way devig should give lower home prob than 2-way (draw eats into it)."""
        two_a, _ = multiplicative_devig(1.549, 4.50)
        three_a, _ = devig_three_way(1.549, 3.80, 4.50, method="multiplicative")
        # 3-way home prob should be significantly lower
        assert three_a < two_a
        assert two_a - three_a > 0.10  # at least 10pp difference

    def test_soccer_realistic_values(self):
        """Home prob should be ~57% not ~74% for typical -182 favorite."""
        p_a, p_b = devig_three_way(1.549, 3.80, 4.50, method="multiplicative")
        assert 0.50 < p_a < 0.65  # ~57%, not 74%
        assert 0.15 < p_b < 0.25  # ~20%, not 26%

    def test_power_method_three_way(self):
        """Power method should also work for 3-way."""
        p_a, p_b = devig_three_way(1.549, 3.80, 4.50, method="power")
        assert p_a + p_b < 1.0
        assert 0.50 < p_a < 0.65
