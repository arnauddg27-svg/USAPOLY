def _kelly_bet_pct(
    adjusted_edge: float,
    fill_price: float,
    fraction_kelly: float,
    min_edge: float = 0.03,
) -> float:
    if fill_price <= 0 or fill_price >= 1:
        return 0.0
    edge = max(adjusted_edge, 0.0)
    if edge <= 0:
        return 0.0
    decimal_odds = 1.0 / fill_price
    kelly_raw = edge / (decimal_odds - 1) if decimal_odds > 1 else 0.0
    kelly_adj = kelly_raw * fraction_kelly

    # Stronger edges size up modestly vs. threshold edges.
    edge_span = max(0.20 - min_edge, 1e-6)
    edge_pos = max(0.0, min(1.0, (edge - min_edge) / edge_span))
    edge_multiplier = 1.0 + 0.5 * edge_pos
    return max(0.0, kelly_adj * edge_multiplier)


def compute_event_cap_pct(
    adjusted_edge: float,
    fill_price: float,
    fraction_kelly: float,
    max_per_event_pct: float,
    event_cap_kelly_multiplier: float = 3.0,
    min_edge: float = 0.03,
) -> float:
    # Respect caller-provided limits directly.
    safe_event_pct = max(max_per_event_pct, 0.0)
    safe_kelly_mult = max(event_cap_kelly_multiplier, 0.0)
    kelly_pct = _kelly_bet_pct(
        adjusted_edge=adjusted_edge,
        fill_price=fill_price,
        fraction_kelly=fraction_kelly,
        min_edge=min_edge,
    )
    if kelly_pct <= 0:
        return 0.0
    return min(safe_event_pct, kelly_pct * safe_kelly_mult)


import logging

_sizing_logger = logging.getLogger(__name__)


def compute_bet_size(
    adjusted_edge: float,
    fill_price: float,
    bankroll: float,
    fraction_kelly: float,
    max_per_event_pct: float,
    total_exposure: float,
    max_total_pct: float,
    cash_buffer_pct: float,
    book_depth_usd: float,
    min_bet: float,
    event_exposure: float = 0.0,
    sport_exposure: float = 0.0,
    max_per_sport_pct: float = 0.10,
    event_cap_kelly_multiplier: float = 3.0,
    min_edge: float = 0.03,
) -> float:
    kelly_pct = _kelly_bet_pct(
        adjusted_edge=adjusted_edge,
        fill_price=fill_price,
        fraction_kelly=fraction_kelly,
        min_edge=min_edge,
    )
    event_cap_pct = compute_event_cap_pct(
        adjusted_edge=adjusted_edge,
        fill_price=fill_price,
        fraction_kelly=fraction_kelly,
        max_per_event_pct=max_per_event_pct,
        event_cap_kelly_multiplier=event_cap_kelly_multiplier,
        min_edge=min_edge,
    )
    bet = bankroll * kelly_pct

    # Respect caller-provided limits directly.
    safe_sport_pct = max(max_per_sport_pct, 0.0)
    safe_total_pct = max(max_total_pct, 0.0)

    caps = [
        ("kelly", bet),
        ("event_cap", bankroll * event_cap_pct - event_exposure),
        ("sport_cap", bankroll * safe_sport_pct - sport_exposure),
        ("total_cap", bankroll * safe_total_pct - total_exposure),
        ("book_depth", book_depth_usd * 0.8),
        ("deployable", bankroll * (1 - cash_buffer_pct) - total_exposure),
    ]

    bet = min(bet, bankroll * event_cap_pct - event_exposure)
    bet = min(bet, bankroll * safe_sport_pct - sport_exposure)
    bet = min(bet, bankroll * safe_total_pct - total_exposure)
    bet = min(bet, book_depth_usd * 0.8)
    max_deployable = bankroll * (1 - cash_buffer_pct) - total_exposure
    bet = min(bet, max_deployable)
    bet = max(bet, 0)
    if bet < min_bet:
        binding = min(caps, key=lambda x: x[1])
        _sizing_logger.info(
            "bet_too_small: binding_cap=%s (%.2f), kelly_pct=%.6f, "
            "bankroll=%.0f, edge=%.4f, fill=%.4f, "
            "event_exp=%.2f, sport_exp=%.2f, total_exp=%.2f, "
            "book_depth=%.2f, min_bet=%.2f",
            binding[0], binding[1], kelly_pct,
            bankroll, adjusted_edge, fill_price,
            event_exposure, sport_exposure, total_exposure,
            book_depth_usd, min_bet,
        )
        return 0.0
    return round(bet, 2)
