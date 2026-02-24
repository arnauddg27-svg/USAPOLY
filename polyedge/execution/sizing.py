def compute_bet_size(
    adjusted_edge: float, fill_price: float, bankroll: float,
    fraction_kelly: float, max_per_event_pct: float,
    total_exposure: float, max_total_pct: float,
    cash_buffer_pct: float, book_depth_usd: float, min_bet: float,
    sport_exposure: float = 0.0, max_per_sport_pct: float = 0.10,
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
    bet = bankroll * kelly_adj * edge_multiplier

    # Hard safety ceilings prevent accidental oversized single bets.
    safe_event_pct = min(max(max_per_event_pct, 0.0), 0.05)
    safe_sport_pct = min(max(max_per_sport_pct, 0.0), 0.20)
    safe_total_pct = min(max(max_total_pct, 0.0), 0.40)

    bet = min(bet, bankroll * safe_event_pct)
    bet = min(bet, bankroll * safe_sport_pct - sport_exposure)
    bet = min(bet, bankroll * safe_total_pct - total_exposure)
    bet = min(bet, book_depth_usd * 0.8)
    max_deployable = bankroll * (1 - cash_buffer_pct) - total_exposure
    bet = min(bet, max_deployable)
    bet = max(bet, 0)
    if bet < min_bet:
        return 0.0
    return round(bet, 2)
