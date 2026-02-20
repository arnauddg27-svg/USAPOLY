import time


class TTLCache:
    def __init__(self, ttl_sec: float = 120.0):
        self._ttl = ttl_sec
        self._store: dict[str, tuple[float, object]] = {}

    def set(self, key: str, value: object) -> None:
        self._store[key] = (time.time(), value)

    def get(self, key: str) -> object | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, val = entry
        if time.time() - ts > self._ttl:
            del self._store[key]
            return None
        return val

    def is_stale(self, key: str) -> bool:
        return self.get(key) is None

    def clear(self) -> None:
        self._store.clear()
