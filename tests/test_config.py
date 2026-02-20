import os
import pytest
from polyedge.config import EdgeConfig

def test_config_loads_defaults():
    cfg = EdgeConfig.from_env()
    assert cfg.trading_enabled is False
    assert cfg.poll_interval_sec == 10
    assert cfg.min_edge == 0.05
    assert cfg.min_books == 6
    assert cfg.fraction_kelly == 0.15
    assert cfg.devig_method == "power"

def test_config_loads_from_env(monkeypatch):
    monkeypatch.setenv("TRADING_ENABLED", "true")
    monkeypatch.setenv("MIN_EDGE_PP", "0.08")
    monkeypatch.setenv("FRACTION_KELLY", "0.20")
    cfg = EdgeConfig.from_env()
    assert cfg.trading_enabled is True
    assert cfg.min_edge == 0.08
    assert cfg.fraction_kelly == 0.20

def test_config_sports_parsing(monkeypatch):
    monkeypatch.setenv("SPORTS", "basketball_nba,icehockey_nhl")
    cfg = EdgeConfig.from_env()
    assert cfg.sports == ["basketball_nba", "icehockey_nhl"]
