import time
import logging

logger = logging.getLogger(__name__)

class CircuitBreaker:
    def __init__(self, stale_timeout_sec: float = 600, max_consecutive_errors: int = 3):
        self._stale_timeout = stale_timeout_sec
        self._max_errors = max_consecutive_errors
        self._last_odds_fetch: float = time.time()
        self._consecutive_errors: int = 0
        self._manually_tripped: bool = False
        self.trip_reason: str = ""

    def record_odds_fetch(self) -> None:
        self._last_odds_fetch = time.time()

    def record_api_error(self) -> None:
        self._consecutive_errors += 1

    def record_api_success(self) -> None:
        self._consecutive_errors = 0

    def trip(self, reason: str) -> None:
        self._manually_tripped = True
        self.trip_reason = reason
        logger.warning("Circuit breaker tripped: %s", reason)

    def reset(self) -> None:
        self._manually_tripped = False
        self._consecutive_errors = 0
        self.trip_reason = ""

    def is_tripped(self) -> bool:
        if self._manually_tripped:
            return True
        if time.time() - self._last_odds_fetch > self._stale_timeout:
            self.trip_reason = "stale_odds"
            return True
        if self._consecutive_errors >= self._max_errors:
            self.trip_reason = "api_errors"
            return True
        self.trip_reason = ""
        return False
