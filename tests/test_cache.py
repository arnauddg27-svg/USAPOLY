import time
import pytest
from polyedge.data.cache import TTLCache

class TestTTLCache:
    def test_set_get(self):
        c = TTLCache(ttl_sec=60)
        c.set("key", "value")
        assert c.get("key") == "value"

    def test_expired(self):
        c = TTLCache(ttl_sec=0.1)
        c.set("key", "value")
        time.sleep(0.15)
        assert c.get("key") is None

    def test_is_stale(self):
        c = TTLCache(ttl_sec=60)
        assert c.is_stale("key") is True
        c.set("key", "value")
        assert c.is_stale("key") is False
