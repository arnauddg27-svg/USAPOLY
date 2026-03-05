import json
import math
import time
from collections import defaultdict
from pathlib import Path


class ExposureTracker:
    def __init__(
        self,
        state_path: str | Path | None = None,
        event_retention_sec: int = 6 * 3600,
    ):
        self._by_event: dict[str, float] = defaultdict(float)
        self._by_sport: dict[str, float] = defaultdict(float)
        self._event_start_ts: dict[str, float] = {}
        self._event_sport: dict[str, str] = {}
        self.daily_pnl: float = 0.0
        self._event_retention_sec = max(0, int(event_retention_sec))
        self._state_path: Path | None = None
        if state_path is not None:
            self._state_path = Path(state_path).expanduser()
        self._load_state()

    @staticmethod
    def _to_float(value) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(parsed):
            return None
        return parsed

    @staticmethod
    def _normalize_key(value: str) -> str:
        return str(value or "").strip()

    def _rebuild_sport_totals(self) -> None:
        totals: dict[str, float] = defaultdict(float)
        for event_id, amount in self._by_event.items():
            sport = self._event_sport.get(event_id, "")
            if sport:
                totals[sport] += max(0.0, float(amount))
        self._by_sport = defaultdict(float, totals)

    def _load_state(self) -> None:
        if self._state_path is None:
            return
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return

        raw_events = payload.get("by_event")
        if isinstance(raw_events, dict):
            for event_id, amount in raw_events.items():
                key = self._normalize_key(event_id)
                val = self._to_float(amount)
                if key and val is not None and val > 0:
                    self._by_event[key] = float(val)

        raw_event_starts = payload.get("event_start_ts")
        if isinstance(raw_event_starts, dict):
            for event_id, start_ts in raw_event_starts.items():
                key = self._normalize_key(event_id)
                val = self._to_float(start_ts)
                if key and val is not None and val > 0:
                    self._event_start_ts[key] = float(val)

        raw_event_sports = payload.get("event_sport")
        if isinstance(raw_event_sports, dict):
            for event_id, sport in raw_event_sports.items():
                key = self._normalize_key(event_id)
                sport_name = self._normalize_key(sport)
                if key and sport_name:
                    self._event_sport[key] = sport_name

        pnl = self._to_float(payload.get("daily_pnl"))
        if pnl is not None:
            self.daily_pnl = pnl

        changed = self._prune_stale()
        self._rebuild_sport_totals()
        if changed:
            self._persist_state()

    def _prune_stale(self, now_ts: float | None = None) -> bool:
        if not self._event_start_ts:
            return False
        now = time.time() if now_ts is None else float(now_ts)
        stale_ids = [
            event_id
            for event_id, start_ts in self._event_start_ts.items()
            if start_ts > 0 and now >= start_ts + self._event_retention_sec
        ]
        if not stale_ids:
            return False

        for event_id in stale_ids:
            self._event_start_ts.pop(event_id, None)
            self._event_sport.pop(event_id, None)
            self._by_event.pop(event_id, None)
        self._rebuild_sport_totals()
        return True

    def _persist_state(self) -> None:
        if self._state_path is None:
            return
        self._prune_stale()
        self._rebuild_sport_totals()
        payload = {
            "updated_at": time.time(),
            "daily_pnl": float(self.daily_pnl),
            "by_event": {k: round(float(v), 8) for k, v in self._by_event.items() if float(v) > 0},
            "by_sport": {k: round(float(v), 8) for k, v in self._by_sport.items() if float(v) > 0},
            "event_start_ts": {k: float(v) for k, v in self._event_start_ts.items() if float(v) > 0},
            "event_sport": {k: v for k, v in self._event_sport.items() if v},
        }
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._state_path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp_path.replace(self._state_path)
        except OSError:
            # Persistence should not block the trading loop.
            pass

    def record_trade(
        self,
        sport: str,
        event_id: str,
        amount_usd: float,
        event_start_ts: float | None = None,
    ) -> None:
        amount = self._to_float(amount_usd)
        if amount is None or amount <= 0:
            return
        key = self._normalize_key(event_id)
        if not key:
            return

        changed = self._prune_stale()
        self._by_event[key] = float(self._by_event.get(key, 0.0)) + float(amount)

        sport_name = self._normalize_key(sport)
        if sport_name:
            self._event_sport[key] = sport_name

        if event_start_ts is not None:
            start_val = self._to_float(event_start_ts)
            if start_val is not None and start_val > 0:
                prev = self._event_start_ts.get(key, 0.0)
                if start_val > prev:
                    self._event_start_ts[key] = start_val
                    changed = True

        self._rebuild_sport_totals()
        if changed or self._state_path is not None:
            self._persist_state()

    def record_exit(self, sport: str, event_id: str, amount_usd: float) -> None:
        amount = self._to_float(amount_usd)
        if amount is None or amount <= 0:
            return
        key = self._normalize_key(event_id)
        if not key:
            return

        changed = self._prune_stale()
        remaining = max(0.0, float(self._by_event.get(key, 0.0)) - float(amount))
        if remaining > 0:
            self._by_event[key] = remaining
        else:
            self._by_event.pop(key, None)
            self._event_start_ts.pop(key, None)
            self._event_sport.pop(key, None)
            changed = True

        self._rebuild_sport_totals()
        if changed or self._state_path is not None:
            self._persist_state()

    def record_pnl(self, pnl: float) -> None:
        amount = self._to_float(pnl)
        if amount is None:
            return
        self.daily_pnl += float(amount)
        self._persist_state()

    def event_exposure(self, event_id: str) -> float:
        changed = self._prune_stale()
        if changed:
            self._persist_state()
        key = self._normalize_key(event_id)
        return float(self._by_event.get(key, 0.0))

    def sport_exposure(self, sport: str) -> float:
        changed = self._prune_stale()
        if changed:
            self._persist_state()
        key = self._normalize_key(sport)
        return float(self._by_sport.get(key, 0.0))

    def total_exposure(self) -> float:
        changed = self._prune_stale()
        if changed:
            self._persist_state()
        return float(sum(self._by_event.values()))

    def can_trade(
        self,
        sport: str,
        event_id: str,
        amount: float,
        bankroll: float,
        max_per_event: float = 0.02,
        max_per_sport: float = 0.10,
        max_total: float = 0.30,
        daily_loss_limit: float = -0.05,
    ) -> bool:
        stake = self._to_float(amount)
        bank = self._to_float(bankroll)
        if stake is None or bank is None or stake <= 0 or bank <= 0:
            return False

        changed = self._prune_stale()
        if changed:
            self._persist_state()

        if self.event_exposure(event_id) + stake > bank * max_per_event:
            return False
        if self.sport_exposure(sport) + stake > bank * max_per_sport:
            return False
        if self.total_exposure() + stake > bank * max_total:
            return False
        if self.daily_pnl < bank * daily_loss_limit:
            return False
        return True

    def reset_daily(self) -> None:
        self.daily_pnl = 0.0
        self._persist_state()
