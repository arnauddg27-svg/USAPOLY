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
    assert cfg.max_edge == 0.05
    assert cfg.min_books == 4
    assert cfg.soccer_min_books == 2
    assert cfg.moneyline_favorites_only is False
    assert cfg.fraction_kelly == 0.15
    assert cfg.event_cap_kelly_multiplier == 1.0
    assert cfg.no_resting_orders is True
    assert cfg.close_orders_before_event_sec == 300
    assert cfg.max_fill_price == 0.91
    assert cfg.max_per_event_pct == 0.05
    assert cfg.auto_cashout_enabled is False
    assert cfg.cashout_cooldown_sec == 3600
    assert cfg.cashout_max_per_cycle == 1
    assert cfg.cashout_min_price == 0.99
    assert cfg.cashout_min_limit_price == 0.98
    assert cfg.cashout_min_size == 1.0
    assert cfg.cashout_min_notional_usd == 100.0
    assert cfg.poly_claim_holder_address == ""
    assert cfg.poly_claim_user_address == ""
    assert cfg.devig_method == "power"
    assert "baseball_mlb" in cfg.sports
    assert "soccer_epl" in cfg.sports
    assert "tennis_atp" in cfg.sports
    assert "tennis_wta" in cfg.sports
    assert "cricket" in cfg.sports
    assert "rugby" in cfg.sports
    assert "table_tennis" in cfg.sports

def test_config_loads_from_env(monkeypatch):
    monkeypatch.setenv("TRADING_ENABLED", "true")
    monkeypatch.setenv("SIMULATION_MODE", "false")
    monkeypatch.setenv("SIMULATION_START_BANKROLL", "2500")
    monkeypatch.setenv("MIN_EDGE_PP", "0.08")
    monkeypatch.setenv("MAX_EDGE_PP", "0.12")
    monkeypatch.setenv("MONEYLINE_FAVORITES_ONLY", "false")
    monkeypatch.setenv("NO_RESTING_ORDERS", "false")
    monkeypatch.setenv("FRACTION_KELLY", "0.20")
    monkeypatch.setenv("EVENT_CAP_KELLY_MULTIPLIER", "2.5")
    monkeypatch.setenv("CLOSE_ORDERS_BEFORE_EVENT_SEC", "180")
    monkeypatch.setenv("AUTO_CASHOUT_ENABLED", "false")
    monkeypatch.setenv("CASHOUT_COOLDOWN_SEC", "900")
    monkeypatch.setenv("CASHOUT_MAX_PER_CYCLE", "2")
    monkeypatch.setenv("CASHOUT_MIN_PRICE", "0.97")
    monkeypatch.setenv("CASHOUT_MIN_LIMIT_PRICE", "0.96")
    monkeypatch.setenv("CASHOUT_MIN_SIZE", "2.0")
    monkeypatch.setenv("CASHOUT_MIN_NOTIONAL_USD", "250")
    monkeypatch.setenv("SOCCER_MIN_BOOKS", "3")
    monkeypatch.setenv("POLY_CLAIM_HOLDER_ADDRESS", "0xholder")
    monkeypatch.setenv("POLY_CLAIM_USER_ADDRESS", "0xuser")
    cfg = EdgeConfig.from_env()
    assert cfg.trading_enabled is True
    assert cfg.simulation_mode is False
    assert cfg.simulation_start_bankroll == 2500.0
    assert cfg.min_edge == 0.08
    assert cfg.max_edge == 0.12
    assert cfg.moneyline_favorites_only is False
    assert cfg.no_resting_orders is False
    assert cfg.fraction_kelly == 0.20
    assert cfg.event_cap_kelly_multiplier == 2.5
    assert cfg.close_orders_before_event_sec == 180
    assert cfg.auto_cashout_enabled is False
    assert cfg.cashout_cooldown_sec == 900
    assert cfg.cashout_max_per_cycle == 2
    assert cfg.cashout_min_price == 0.97
    assert cfg.cashout_min_limit_price == 0.96
    assert cfg.cashout_min_size == 2.0
    assert cfg.cashout_min_notional_usd == 250.0
    assert cfg.soccer_min_books == 3
    assert cfg.poly_claim_holder_address == "0xholder"
    assert cfg.poly_claim_user_address == "0xuser"

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
