import os
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from polyedge.paths import RUNTIME_CONFIG_PATH

logger = logging.getLogger(__name__)

_SECRET_FIELDS = {"poly_api_key", "poly_api_secret", "poly_api_passphrase",
                  "poly_private_key", "odds_api_key", "dashboard_password"}

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
    ("SOCCER_MIN_BOOKS",       "soccer_min_books",       int,   4),
    ("DEVIG_METHOD",           "devig_method",           str,   "power"),
    ("SAFETY_HAIRCUT",         "safety_haircut",         float, 0.0),
    ("MAX_SLIPPAGE",           "max_slippage",           float, 0.02),
    ("MAX_SPREAD",             "max_spread",             float, 0.02),
    ("MIN_HOURS_BEFORE_EVENT", "min_hours_before_event", float, 1.0),
    ("MONEYLINE_FAVORITES_ONLY", "moneyline_favorites_only", bool, False),
    ("TENNIS_MAJOR_ONLY",      "tennis_major_only",      bool,  False),
    ("FRACTION_KELLY",         "fraction_kelly",         float, 0.15),
    ("EVENT_CAP_KELLY_MULTIPLIER", "event_cap_kelly_multiplier", float, 3.0),
    ("MAX_PER_EVENT_PCT",      "max_per_event_pct",      float, 0.02),
    ("MAX_PER_SPORT_PCT",      "max_per_sport_pct",      float, 0.10),
    ("MAX_TOTAL_EXPOSURE_PCT", "max_total_exposure_pct", float, 0.30),
    ("CASH_BUFFER_PCT",        "cash_buffer_pct",        float, 0.20),
    ("MIN_BET_USD",            "min_bet_usd",            float, 5.0),
    ("DAILY_LOSS_LIMIT_PCT",   "daily_loss_limit_pct",   float, -0.05),
    ("ORDER_OFFSET",           "order_offset",           float, 0.005),
    ("ORDER_TTL_SEC",          "order_ttl_sec",          int,   90),
    ("NO_RESTING_ORDERS",      "no_resting_orders",      bool,  True),
    ("CLOSE_ORDERS_BEFORE_EVENT_SEC", "close_orders_before_event_sec", int, 300),
    ("AUTO_CLAIM_ENABLED",     "auto_claim_enabled",     bool,  True),
    ("CLAIM_COOLDOWN_SEC",     "claim_cooldown_sec",     int,   14400),
    ("CLAIM_MAX_PER_CYCLE",    "claim_max_per_cycle",    int,   1),
    ("AUTO_CASHOUT_ENABLED",   "auto_cashout_enabled",   bool,  True),
    ("CASHOUT_COOLDOWN_SEC",   "cashout_cooldown_sec",   int,   3600),
    ("CASHOUT_MAX_PER_CYCLE",  "cashout_max_per_cycle",  int,   1),
    ("CASHOUT_MIN_PRICE",      "cashout_min_price",      float, 0.99),
    ("CASHOUT_MIN_LIMIT_PRICE","cashout_min_limit_price",float, 0.98),
    ("CASHOUT_MIN_SIZE",       "cashout_min_size",       float, 1.0),
    ("CASHOUT_MIN_NOTIONAL_USD","cashout_min_notional_usd",float, 100.0),
    ("CHASE_TOLERANCE",        "chase_tolerance",        float, 0.01),
    ("MAX_RETRIES",            "max_retries",            int,   3),
    ("SPORTS",                 "sports",                 list,  ["basketball_nba","americanfootball_nfl","baseball_mlb","icehockey_nhl","soccer_epl","tennis_atp","tennis_wta","cricket","rugby","table_tennis"]),
    ("POLY_API_KEY",           "poly_api_key",           str,   ""),
    ("POLY_API_SECRET",        "poly_api_secret",        str,   ""),
    ("POLY_API_PASSPHRASE",    "poly_api_passphrase",    str,   ""),
    ("POLY_PRIVATE_KEY",       "poly_private_key",       str,   ""),
    ("POLY_SIGNATURE_TYPE",    "poly_signature_type",    int,   2),
    ("POLY_FUNDER_ADDRESS",    "poly_funder_address",    str,   ""),
    ("POLY_CLAIM_HOLDER_ADDRESS", "poly_claim_holder_address", str, ""),
    ("POLY_CLAIM_USER_ADDRESS",   "poly_claim_user_address",   str, ""),
    ("POLYGON_RPC",            "polygon_rpc",            str,   "https://polygon-bor-rpc.publicnode.com"),
    ("USDC_ADDRESS",           "usdc_address",           str,   "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"),
    ("ODDS_API_KEY",           "odds_api_key",           str,   ""),
    ("ODDS_API_REGIONS",       "odds_api_regions",       str,   "us,fr,uk"),
    ("ODDS_API_CRICKET_REGIONS", "odds_api_cricket_regions", str, "us,uk,eu,au"),
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
    moneyline_favorites_only: bool = False
    tennis_major_only: bool = False
    fraction_kelly: float = 0.15
    event_cap_kelly_multiplier: float = 3.0
    max_per_event_pct: float = 0.02
    max_per_sport_pct: float = 0.10
    max_total_exposure_pct: float = 0.30
    cash_buffer_pct: float = 0.20
    min_bet_usd: float = 5.0
    daily_loss_limit_pct: float = -0.05
    order_offset: float = 0.005
    order_ttl_sec: int = 90
    no_resting_orders: bool = True
    close_orders_before_event_sec: int = 300
    auto_claim_enabled: bool = True
    claim_cooldown_sec: int = 14400
    claim_max_per_cycle: int = 1
    auto_cashout_enabled: bool = True
    cashout_cooldown_sec: int = 3600
    cashout_max_per_cycle: int = 1
    cashout_min_price: float = 0.99
    cashout_min_limit_price: float = 0.98
    cashout_min_size: float = 1.0
    cashout_min_notional_usd: float = 100.0
    chase_tolerance: float = 0.01
    max_retries: int = 3
    sports: list = field(default_factory=lambda: ["basketball_nba","americanfootball_nfl","baseball_mlb","icehockey_nhl","soccer_epl","tennis_atp","tennis_wta","cricket","rugby","table_tennis"])
    poly_api_key: str = ""
    poly_api_secret: str = ""
    poly_api_passphrase: str = ""
    poly_private_key: str = ""
    poly_signature_type: int = 2
    poly_funder_address: str = ""
    poly_claim_holder_address: str = ""
    poly_claim_user_address: str = ""
    polygon_rpc: str = "https://polygon-bor-rpc.publicnode.com"
    usdc_address: str = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
    odds_api_key: str = ""
    odds_api_regions: str = "us,fr,uk"
    odds_api_cricket_regions: str = "us,uk,eu,au"
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
        # Fee and haircut adjustments are disabled by default for live tuning.
        # Keep these hard-zeroed so env/runtime overrides cannot silently reduce edge.
        cfg.fee_rate = 0.0
        cfg.safety_haircut = 0.0
        return cfg

    def _apply_runtime_overrides(self):
        # Only allow safe tuning fields via runtime overrides
        _SAFE_OVERRIDES = {
            "MIN_EDGE_PP", "MAX_EDGE_PP", "MIN_BOOKS", "SOCCER_MIN_BOOKS", "SAFETY_HAIRCUT", "MAX_SLIPPAGE", "MAX_SPREAD",
            "MIN_HOURS_BEFORE_EVENT", "FRACTION_KELLY", "MAX_PER_EVENT_PCT",
            "EVENT_CAP_KELLY_MULTIPLIER",
            "MONEYLINE_FAVORITES_ONLY",
            "TENNIS_MAJOR_ONLY",
            "MAX_PER_SPORT_PCT", "MAX_TOTAL_EXPOSURE_PCT", "CASH_BUFFER_PCT",
            "MIN_BET_USD", "DAILY_LOSS_LIMIT_PCT", "ORDER_OFFSET", "ORDER_TTL_SEC",
            "NO_RESTING_ORDERS", "CLOSE_ORDERS_BEFORE_EVENT_SEC",
            "AUTO_CLAIM_ENABLED", "CLAIM_COOLDOWN_SEC", "CLAIM_MAX_PER_CYCLE",
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
        if self.simulation_start_bankroll <= 0:
            warnings.append(
                f"simulation_start_bankroll={self.simulation_start_bankroll} must be > 0"
            )
        if self.claim_cooldown_sec < 30:
            warnings.append(f"claim_cooldown_sec={self.claim_cooldown_sec} < 30")
        if self.claim_max_per_cycle < 1:
            warnings.append(f"claim_max_per_cycle={self.claim_max_per_cycle} < 1")
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
        return warnings

    def __repr__(self) -> str:
        fields = []
        for f_name in self.__dataclass_fields__:
            val = getattr(self, f_name)
            if f_name in _SECRET_FIELDS and val:
                fields.append(f"{f_name}='***'")
            else:
                fields.append(f"{f_name}={val!r}")
        return f"EdgeConfig({', '.join(fields)})"
