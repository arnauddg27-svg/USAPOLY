import os
from pathlib import Path
import pytest
from polyedge.config import EdgeConfig

def test_config_loads_defaults():
    cfg = EdgeConfig.from_env()
    assert cfg.trading_enabled is False
    assert cfg.simulation_mode is True
    assert cfg.simulation_start_bankroll == 1000.0
    assert cfg.poll_interval_sec == 10
    assert cfg.min_edge == 0.03
    assert cfg.min_books == 4
    assert cfg.moneyline_favorites_only is False
    assert cfg.fraction_kelly == 0.15
    assert cfg.no_resting_orders is True
    assert cfg.close_orders_before_event_sec == 300
    assert cfg.devig_method == "power"

def test_config_loads_from_env(monkeypatch):
    monkeypatch.setenv("TRADING_ENABLED", "true")
    monkeypatch.setenv("SIMULATION_MODE", "false")
    monkeypatch.setenv("SIMULATION_START_BANKROLL", "2500")
    monkeypatch.setenv("MIN_EDGE_PP", "0.08")
    monkeypatch.setenv("MONEYLINE_FAVORITES_ONLY", "false")
    monkeypatch.setenv("NO_RESTING_ORDERS", "false")
    monkeypatch.setenv("FRACTION_KELLY", "0.20")
    monkeypatch.setenv("CLOSE_ORDERS_BEFORE_EVENT_SEC", "180")
    cfg = EdgeConfig.from_env()
    assert cfg.trading_enabled is True
    assert cfg.simulation_mode is False
    assert cfg.simulation_start_bankroll == 2500.0
    assert cfg.min_edge == 0.08
    assert cfg.moneyline_favorites_only is False
    assert cfg.no_resting_orders is False
    assert cfg.fraction_kelly == 0.20
    assert cfg.close_orders_before_event_sec == 180

def test_config_sports_parsing(monkeypatch):
    monkeypatch.setenv("SPORTS", "basketball_nba,icehockey_nhl")
    cfg = EdgeConfig.from_env()
    assert cfg.sports == ["basketball_nba", "icehockey_nhl"]


def test_config_invalid_numeric_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("MIN_EDGE_PP", "not-a-number")
    cfg = EdgeConfig.from_env()
    assert cfg.min_edge == 0.03


def test_runtime_config_path_is_absolute():
    cfg = EdgeConfig.from_env()
    assert Path(cfg.runtime_config_path).is_absolute()
