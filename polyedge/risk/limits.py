from collections import defaultdict

class ExposureTracker:
    def __init__(self):
        self._by_event: dict[str, float] = defaultdict(float)
        self._by_sport: dict[str, float] = defaultdict(float)
        self.daily_pnl: float = 0.0

    def record_trade(self, sport: str, condition_id: str, amount_usd: float) -> None:
        self._by_event[condition_id] += amount_usd
        self._by_sport[sport] += amount_usd

    def record_exit(self, sport: str, condition_id: str, amount_usd: float) -> None:
        self._by_event[condition_id] = max(0, self._by_event[condition_id] - amount_usd)
        self._by_sport[sport] = max(0, self._by_sport[sport] - amount_usd)

    def record_pnl(self, pnl: float) -> None:
        self.daily_pnl += pnl

    def event_exposure(self, condition_id: str) -> float:
        return self._by_event.get(condition_id, 0.0)

    def sport_exposure(self, sport: str) -> float:
        return self._by_sport.get(sport, 0.0)

    def total_exposure(self) -> float:
        return sum(self._by_event.values())

    def can_trade(self, sport: str, condition_id: str, amount: float,
                  bankroll: float, max_per_event: float = 0.02,
                  max_per_sport: float = 0.10, max_total: float = 0.30,
                  daily_loss_limit: float = -0.05) -> bool:
        if self.event_exposure(condition_id) + amount > bankroll * max_per_event:
            return False
        if self.sport_exposure(sport) + amount > bankroll * max_per_sport:
            return False
        if self.total_exposure() + amount > bankroll * max_total:
            return False
        if self.daily_pnl < bankroll * daily_loss_limit:
            return False
        return True

    def reset_daily(self) -> None:
        self.daily_pnl = 0.0
