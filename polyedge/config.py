import os
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from polyedge.paths import RUNTIME_CONFIG_PATH

logger = logging.getLogger(__name__)

_SECRET_FIELDS = {"polymarket_key_id", "polymarket_secret_key",
                  "odds_api_key", "dashboard_password"}

def _cast_bool(v: str) -> bool:
    return v.lower() in ("true", "1", "yes")


def _cast_value(raw: str, typ: type):
    if typ is bool:
        return _cast_bool(raw)
    if typ is list:
        return [s.strip() for s in raw.split(",") if s.strip()]
    if typ is int:
        return int(raw)
    if typ is float:
        return float(raw)
    return raw

CONFIG_FIELDS = [
    ("TRADING_ENABLED",        "trading_enabled",        bool,  False),
    ("SIMULATION_MODE",        "simulation_mode",        bool,  True),
    ("SIMULATION_START_BANKROLL", "simulation_start_bankroll", float, 1000.0),
    ("POLL_INTERVAL_SEC",      "poll_interval_sec",      int,   10),
    ("SLOW_CYCLE_MULTIPLIER",  "slow_cycle_multiplier",  int,   12),
    ("MIN_EDGE_PP",            "min_edge",               float, 0.03),
    ("MAX_EDGE_PP",            "max_edge",               float, 0.05),
    ("MIN_BOOKS",              "min_books",              int,   4),
    ("SOCCER_MIN_BOOKS",       "soccer_min_books",       int,   2),
    ("DEVIG_METHOD",           "devig_method",           str,   "power"),
    ("SAFETY_HAIRCUT",         "safety_haircut",         float, 0.0),
    ("MAX_SLIPPAGE",           "max_slippage",           float, 0.02),
    ("MAX_SPREAD",             "max_spread",             float, 0.02),
    ("MIN_HOURS_BEFORE_EVENT", "min_hours_before_event", float, 1.0),
    ("MAX_HOURS_BEFORE_EVENT", "max_hours_before_event", float, 48.0),
    ("MAX_FILL_PRICE",         "max_fill_price",         float, 0.91),
    ("MONEYLINE_FAVORITES_ONLY", "moneyline_favorites_only", bool, False),
    ("TENNIS_MAJOR_ONLY",      "tennis_major_only",      bool,  False),
    ("FRACTION_KELLY",         "fraction_kelly",         float, 0.15),
    ("EVENT_CAP_KELLY_MULTIPLIER", "event_cap_kelly_multiplier", float, 1.0),
    ("MAX_PER_EVENT_PCT",      "max_per_event_pct",      float, 0.05),
    ("MAX_PER_SPORT_PCT",      "max_per_sport_pct",      float, 0.10),
    ("MAX_TOTAL_EXPOSURE_PCT", "max_total_exposure_pct", float, 0.30),
    ("CASH_BUFFER_PCT",        "cash_buffer_pct",        float, 0.20),
    ("MIN_BET_USD",            "min_bet_usd",            float, 5.0),
    ("DAILY_LOSS_LIMIT_PCT",   "daily_loss_limit_pct",   float, -0.05),
    ("ORDER_OFFSET",           "order_offset",           float, 0.005),
    ("ORDER_TTL_SEC",          "order_ttl_sec",          int,   90),
    ("NO_RESTING_ORDERS",      "no_resting_orders",      bool,  True),
    ("CLOSE_ORDERS_BEFORE_EVENT_SEC", "close_orders_before_event_sec", int, 300),
    ("AUTO_CASHOUT_ENABLED",   "auto_cashout_enabled",   bool,  False),
    ("CASHOUT_COOLDOWN_SEC",   "cashout_cooldown_sec",   int,   3600),
    ("CASHOUT_MAX_PER_CYCLE",  "cashout_max_per_cycle",  int,   1),
    ("CASHOUT_MIN_PRICE",      "cashout_min_price",      float, 0.99),
    ("CASHOUT_MIN_LIMIT_PRICE","cashout_min_limit_price",float, 0.98),
    ("CASHOUT_MIN_SIZE",       "cashout_min_size",       float, 1.0),
    ("CASHOUT_MIN_NOTIONAL_USD","cashout_min_notional_usd",float, 100.0),
    ("CHASE_TOLERANCE",        "chase_tolerance",        float, 0.01),
    ("MAX_RETRIES",            "max_retries",            int,   3),
    ("SPORTS",                 "sports",                 list,  ["basketball_nba","americanfootball_nfl","baseball_mlb","icehockey_nhl","soccer_epl","tennis_atp","tennis_wta","cricket","rugby","table_tennis"]),
    ("POLYMARKET_KEY_ID",      "polymarket_key_id",      str,   ""),
    ("POLYMARKET_SECRET_KEY",  "polymarket_secret_key",  str,   ""),
    ("ODDS_API_KEY",           "odds_api_key",           str,   ""),
    ("ODDS_API_REGIONS",       "odds_api_regions",       str,   "us,fr,uk"),
    ("ODDS_API_CRICKET_REGIONS", "odds_api_cricket_regions", str, "us,uk,eu,au"),
    ("ODDS_API_SOCCER_REGIONS", "odds_api_soccer_regions", str, "us,us2,uk,eu,au,fr,se"),
    ("ODDS_API_NHL_REGIONS",   "odds_api_nhl_regions",   str, "us,us2"),
    ("DASHBOARD_PORT",         "dashboard_port",         int,   8502),
    ("DASHBOARD_PASSWORD",     "dashboard_password",     str,   ""),
    ("FEE_RATE",               "fee_rate",               float, 0.0),
    ("TARGET_SHARES",          "target_shares",          float, 150.0),
]

@dataclass
class EdgeConfig:
    trading_enabled: bool = False
    simulation_mode: bool = True
    simulation_start_bankroll: float = 1000.0
    poll_interval_sec: int = 10
    slow_cycle_multiplier: int = 12
    min_edge: float = 0.03
    max_edge: float = 0.05
    min_books: int = 4
    soccer_min_books: int = 4
    devig_method: str = "power"
    safety_haircut: float = 0.0
    max_slippage: float = 0.02
    max_spread: float = 0.02
    min_hours_before_event: float = 1.0
    max_hours_before_event: float = 48.0
    max_fill_price: float = 0.91
    moneyline_favorites_only: bool = False
    tennis_major_only: bool = False
    fraction_kelly: float = 0.15
    event_cap_kelly_multiplier: float = 1.0
    max_per_event_pct: float = 0.05
    max_per_sport_pct: float = 0.10
    max_total_exposure_pct: float = 0.30
    cash_buffer_pct: float = 0.20
    min_bet_usd: float = 5.0
    daily_loss_limit_pct: float = -0.05
    order_offset: float = 0.005
    order_ttl_sec: int = 90
    no_resting_orders: bool = True
    close_orders_before_event_sec: int = 300
    auto_cashout_enabled: bool = False
    cashout_cooldown_sec: int = 3600
    cashout_max_per_cycle: int = 1
    cashout_min_price: float = 0.99
    cashout_min_limit_price: float = 0.98
    cashout_min_size: float = 1.0
    cashout_min_notional_usd: float = 100.0
    chase_tolerance: float = 0.01
    max_retries: int = 3
    sports: list = field(default_factory=lambda: ["basketball_nba","americanfootball_nfl","baseball_mlb","icehockey_nhl","soccer_epl","tennis_atp","tennis_wta","cricket","rugby","table_tennis"])
    polymarket_key_id: str = ""
    polymarket_secret_key: str = ""
    odds_api_key: str = ""
    odds_api_regions: str = "us,fr,uk"
    odds_api_cricket_regions: str = "us,uk,eu,au"
    odds_api_soccer_regions: str = "us,us2,uk,eu,au,fr,se"
    odds_api_nhl_regions: str = "us,us2"
    dashboard_port: int = 8502
    dashboard_password: str = ""
    fee_rate: float = 0.0
    target_shares: float = 150.0
    runtime_config_path: str = str(RUNTIME_CONFIG_PATH)

    @classmethod
    def from_env(cls) -> "EdgeConfig":
        kwargs = {}
        for env_name, attr, typ, default in CONFIG_FIELDS:
            raw = os.getenv(env_name)
            if raw is None:
                kwargs[attr] = default
                continue
            try:
                kwargs[attr] = _cast_value(raw, typ)
            except (ValueError, TypeError):
                logger.warning("Invalid %s=%r; using default %r", env_name, raw, default)
                kwargs[attr] = default
        cfg = cls(**kwargs)
        cfg._apply_runtime_overrides()
        cfg.fee_rate = 0.0
        cfg.safety_haircut = 0.0
        return cfg

    def _apply_runtime_overrides(self):
        _SAFE_OVERRIDES = {
            "MIN_EDGE_PP", "MAX_EDGE_PP", "MIN_BOOKS", "SOCCER_MIN_BOOKS", "SAFETY_HAIRCUT", "MAX_SLIPPAGE", "MAX_SPREAD",
            "MIN_HOURS_BEFORE_EVENT", "MAX_HOURS_BEFORE_EVENT", "MAX_FILL_PRICE", "FRACTION_KELLY", "MAX_PER_EVENT_PCT",
            "EVENT_CAP_KELLY_MULTIPLIER",
            "MONEYLINE_FAVORITES_ONLY",
            "TENNIS_MAJOR_ONLY",
            "MAX_PER_SPORT_PCT", "MAX_TOTAL_EXPOSURE_PCT", "CASH_BUFFER_PCT",
            "MIN_BET_USD", "DAILY_LOSS_LIMIT_PCT", "ORDER_OFFSET", "ORDER_TTL_SEC",
            "NO_RESTING_ORDERS", "CLOSE_ORDERS_BEFORE_EVENT_SEC",
            "AUTO_CASHOUT_ENABLED", "CASHOUT_COOLDOWN_SEC", "CASHOUT_MAX_PER_CYCLE",
            "CASHOUT_MIN_PRICE", "CASHOUT_MIN_LIMIT_PRICE", "CASHOUT_MIN_SIZE",
            "CASHOUT_MIN_NOTIONAL_USD",
            "CHASE_TOLERANCE", "MAX_RETRIES",
            "TRADING_ENABLED", "SIMULATION_MODE",
            "SIMULATION_START_BANKROLL", "TARGET_SHARES",
        }
        try:
            cfg_path = Path(self.runtime_config_path).expanduser()
            with open(cfg_path, encoding="utf-8") as f:
                overrides = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return
        if not isinstance(overrides, dict):
            logger.warning("Runtime override file ignored (expected object): %s", self.runtime_config_path)
            return
        lookup = {env: (attr, typ) for env, attr, typ, _ in CONFIG_FIELDS}
        for key, val in overrides.items():
            if key not in _SAFE_OVERRIDES:
                logger.warning("Runtime override blocked for %s (not in safe list)", key)
                continue
            if key in lookup:
                attr, typ = lookup[key]
                try:
                    if typ is list and isinstance(val, list):
                        setattr(self, attr, val)
                    elif typ is list:
                        setattr(self, attr, [s.strip() for s in str(val).split(",") if s.strip()])
                    else:
                        setattr(self, attr, _cast_value(str(val), typ))
                except (ValueError, TypeError):
                    pass

    def validate(self) -> list[str]:
        """Check config values are in sane ranges. Returns list of warnings."""
        warnings = []
        if not (0 < self.min_edge < 0.5):
            warnings.append(f"min_edge={self.min_edge} outside (0, 0.5)")
        if not (0 < self.max_edge <= 1.0):
            warnings.append(f"max_edge={self.max_edge} outside (0, 1.0]")
        if self.min_edge >= self.max_edge:
            warnings.append("min_edge >= max_edge")
        if not (0 < self.fraction_kelly <= 1.0):
            warnings.append(f"fraction_kelly={self.fraction_kelly} outside (0, 1.0]")
        if not (0 < self.max_per_event_pct <= 0.5):
            warnings.append(f"max_per_event_pct={self.max_per_event_pct} outside (0, 0.5]")
        if not (1.0 <= self.event_cap_kelly_multiplier <= 5.0):
            warnings.append(
                f"event_cap_kelly_multiplier={self.event_cap_kelly_multiplier} outside [1.0, 5.0]"
            )
        if not (0 < self.max_total_exposure_pct <= 1.0):
            warnings.append(f"max_total_exposure_pct={self.max_total_exposure_pct} outside (0, 1.0]")
        if self.max_per_event_pct > self.max_per_sport_pct:
            warnings.append("max_per_event_pct > max_per_sport_pct")
        if self.max_per_sport_pct > self.max_total_exposure_pct:
            warnings.append("max_per_sport_pct > max_total_exposure_pct")
        if not (0 <= self.cash_buffer_pct < 1.0):
            warnings.append(f"cash_buffer_pct={self.cash_buffer_pct} outside [0, 1.0)")
        if self.poll_interval_sec < 1:
            warnings.append(f"poll_interval_sec={self.poll_interval_sec} < 1")
        if self.close_orders_before_event_sec < 0:
            warnings.append(
                f"close_orders_before_event_sec={self.close_orders_before_event_sec} < 0"
            )
        if not (0.01 <= self.max_fill_price < 1.0):
            warnings.append(f"max_fill_price={self.max_fill_price} outside [0.01, 1.0)")
        if self.simulation_start_bankroll <= 0:
            warnings.append(
                f"simulation_start_bankroll={self.simulation_start_bankroll} must be > 0"
            )
        if self.cashout_cooldown_sec < 30:
            warnings.append(f"cashout_cooldown_sec={self.cashout_cooldown_sec} < 30")
        if self.cashout_max_per_cycle < 1:
            warnings.append(f"cashout_max_per_cycle={self.cashout_max_per_cycle} < 1")
        if not (0.5 <= self.cashout_min_price < 1.0):
            warnings.append(
                f"cashout_min_price={self.cashout_min_price} outside [0.5, 1.0)"
            )
        if not (0.5 <= self.cashout_min_limit_price < 1.0):
            warnings.append(
                f"cashout_min_limit_price={self.cashout_min_limit_price} outside [0.5, 1.0)"
            )
        if self.cashout_min_limit_price > self.cashout_min_price:
            warnings.append("cashout_min_limit_price > cashout_min_price")
        if self.cashout_min_size <= 0:
            warnings.append(f"cashout_min_size={self.cashout_min_size} must be > 0")
        if self.cashout_min_notional_usd < 0:
            warnings.append(
                f"cashout_min_notional_usd={self.cashout_min_notional_usd} must be >= 0"
            )
        if not self.odds_api_key:
            warnings.append("ODDS_API_KEY not set")
        if not self.polymarket_key_id:
            warnings.append("POLYMARKET_KEY_ID not set")
        return warnings

    def __repr__(self) -> str:
        fields = []
        for f_name in self.__dataclass_fields__:
            val = getattr(self, f_name)
            if f_name in _SECRET_FIELDS and val:
                fields.append(f"{f_name}='***'")
            else:
                fields.append(f"{f_name}={val!r}")
        return "EdgeConfig(" + ", ".join(fields) + ")"
