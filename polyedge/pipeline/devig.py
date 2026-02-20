"""Devigging module: remove bookmaker vig (margin) from sportsbook odds.

Converts raw decimal odds into true implied probabilities by removing
the bookmaker's overround using either multiplicative normalization
or the power method (which accounts for favorite-longshot bias).
"""

from scipy.optimize import brentq


def multiplicative_devig(
    decimal_a: float, decimal_b: float
) -> tuple[float, float]:
    """Remove vig using multiplicative normalization.

    Each implied probability is divided by the total overround so that
    the resulting probabilities sum to 1.0.  This method distributes
    the margin proportionally across both sides.

    Args:
        decimal_a: Decimal odds for side A (e.g. 1.909 for -110).
        decimal_b: Decimal odds for side B.

    Returns:
        (true_prob_a, true_prob_b) summing to 1.0.
    """
    imp_a = 1.0 / decimal_a
    imp_b = 1.0 / decimal_b
    overround = imp_a + imp_b
    return imp_a / overround, imp_b / overround


def power_devig(
    decimal_a: float, decimal_b: float
) -> tuple[float, float]:
    """Remove vig using the power method (accounts for favorite-longshot bias).

    Finds exponent *k* such that ``implied_a**k + implied_b**k == 1``.
    The power method extracts less vig from the longshot side, which
    better reflects the empirical favorite-longshot bias observed in
    real betting markets.

    Args:
        decimal_a: Decimal odds for side A.
        decimal_b: Decimal odds for side B.

    Returns:
        (true_prob_a, true_prob_b) summing to 1.0.
    """
    imp_a = 1.0 / decimal_a
    imp_b = 1.0 / decimal_b
    total = imp_a + imp_b

    # If there is essentially no vig, return implied probabilities directly.
    if abs(total - 1.0) < 1e-9:
        return imp_a, imp_b

    def objective(k: float) -> float:
        return imp_a ** k + imp_b ** k - 1.0

    try:
        k = brentq(objective, 0.01, 5.0, xtol=1e-12, maxiter=100)
    except ValueError:
        # Fall back to multiplicative if root-finding fails to bracket.
        return multiplicative_devig(decimal_a, decimal_b)

    p_a = imp_a ** k
    p_b = imp_b ** k
    return p_a, p_b


def devig(
    decimal_a: float, decimal_b: float, method: str = "power"
) -> tuple[float, float]:
    """Convenience wrapper that dispatches to the chosen devig method.

    Args:
        decimal_a: Decimal odds for side A.
        decimal_b: Decimal odds for side B.
        method: ``"power"`` (default) or ``"multiplicative"``.

    Returns:
        (true_prob_a, true_prob_b) summing to 1.0.
    """
    if method == "power":
        return power_devig(decimal_a, decimal_b)
    return multiplicative_devig(decimal_a, decimal_b)
