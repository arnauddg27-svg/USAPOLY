import time
import logging

logger = logging.getLogger(__name__)


class PositionTracker:
    """Lightweight position tracker for Polymarket US.
    
    Polymarket US handles settlement automatically (fiat exchange).
    This class only tracks positions for the auto-cashout feature
    (selling near-$1.00 positions to lock in profits).
    """

    def __init__(self, poly_client):
        self.poly = poly_client
        self._cooldowns: dict[str, float] = {}

    def fetch_positions(self, *, limit: int = 500) -> list[dict]:
        """Fetch current positions from Polymarket US."""
        try:
            positions = self.poly.portfolio.positions({"limit": limit})
            if isinstance(positions, list):
                return positions
            if hasattr(positions, "data"):
                return list(positions.data)
            if isinstance(positions, dict) and "data" in positions:
                return positions["data"]
            return []
        except Exception as e:
            logger.warning("Failed to fetch positions: %s", e)
            return []

    def in_cooldown(self, token_id: str, cooldown_sec: int = 3600) -> bool:
        last = self._cooldowns.get(token_id)
        if last is None:
            return False
        return time.time() - last < cooldown_sec

    def set_cooldown(self, token_id: str) -> None:
        self._cooldowns[token_id] = time.time()
