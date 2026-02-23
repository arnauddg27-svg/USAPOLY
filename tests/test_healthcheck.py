import json
from datetime import datetime, timedelta, timezone

from polyedge import healthcheck


def test_healthcheck_passes_for_fresh_running_status(monkeypatch, tmp_path):
    health_path = tmp_path / "health.json"
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "running",
    }
    health_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(healthcheck, "HEALTH_PATH", health_path)
    monkeypatch.setenv("HEALTH_MAX_STALE_SEC", "180")
    assert healthcheck.main() == 0


def test_healthcheck_fails_when_stale(monkeypatch, tmp_path):
    health_path = tmp_path / "health.json"
    stale_ts = datetime.now(timezone.utc) - timedelta(seconds=600)
    payload = {
        "timestamp": stale_ts.isoformat(),
        "status": "running",
    }
    health_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(healthcheck, "HEALTH_PATH", health_path)
    monkeypatch.setenv("HEALTH_MAX_STALE_SEC", "180")
    assert healthcheck.main() == 1


def test_healthcheck_fails_when_stopped(monkeypatch, tmp_path):
    health_path = tmp_path / "health.json"
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "stopped",
    }
    health_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(healthcheck, "HEALTH_PATH", health_path)
    assert healthcheck.main() == 1


def test_healthcheck_invalid_stale_env_falls_back(monkeypatch, tmp_path):
    health_path = tmp_path / "health.json"
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "running",
    }
    health_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(healthcheck, "HEALTH_PATH", health_path)
    monkeypatch.setenv("HEALTH_MAX_STALE_SEC", "not-a-number")
    assert healthcheck.main() == 0
