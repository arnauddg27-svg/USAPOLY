def compute_bet_size(
    adjusted_edge: float, fill_price: float, bankroll: float,
    fraction_kelly: float, max_per_event_pct: float,
    total_exposure: float, max_total_pct: float,
    cash_buffer_pct: float, book_depth_usd: float, min_bet: float,
    sport_exposure: float = 0.0, max_per_sport_pct: float = 0.10,
) -> float:
    if fill_price <= 0 or fill_price >= 1:
        return 0.0
    decimal_odds = 1.0 / fill_price
    kelly_raw = adjusted_edge / (decimal_odds - 1) if decimal_odds > 1 else 0.0
    kelly_adj = kelly_raw * fraction_kelly
    bet = bankroll * kelly_adj
    bet = min(bet, bankroll * max_per_event_pct)
    bet = min(bet, bankroll * max_per_sport_pct - sport_exposure)
    bet = min(bet, bankroll * max_total_pct - total_exposure)
    bet = min(bet, book_depth_usd * 0.8)
    max_deployable = bankroll * (1 - cash_buffer_pct) - total_exposure
    bet = min(bet, max_deployable)
    bet = max(bet, 0)
    if bet < min_bet:
        return 0.0
    return round(bet, 2)
