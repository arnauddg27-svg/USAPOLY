import os
import json
from dataclasses import dataclass, field

def _cast_bool(v: str) -> bool:
    return v.lower() in ("true", "1", "yes")

CONFIG_FIELDS = [
    ("TRADING_ENABLED",        "trading_enabled",        bool,  False),
    ("POLL_INTERVAL_SEC",      "poll_interval_sec",      int,   10),
    ("SLOW_CYCLE_MULTIPLIER",  "slow_cycle_multiplier",  int,   12),
    ("MIN_EDGE_PP",            "min_edge",               float, 0.05),
    ("MIN_BOOKS",              "min_books",              int,   6),
    ("DEVIG_METHOD",           "devig_method",           str,   "power"),
    ("SAFETY_HAIRCUT",         "safety_haircut",         float, 0.01),
    ("MAX_SLIPPAGE",           "max_slippage",           float, 0.01),
    ("MAX_SPREAD",             "max_spread",             float, 0.01),
    ("MIN_HOURS_BEFORE_EVENT", "min_hours_before_event", float, 1.0),
    ("FRACTION_KELLY",         "fraction_kelly",         float, 0.15),
    ("MAX_PER_EVENT_PCT",      "max_per_event_pct",      float, 0.02),
    ("MAX_PER_SPORT_PCT",      "max_per_sport_pct",      float, 0.10),
    ("MAX_TOTAL_EXPOSURE_PCT", "max_total_exposure_pct", float, 0.30),
    ("CASH_BUFFER_PCT",        "cash_buffer_pct",        float, 0.20),
    ("MIN_BET_USD",            "min_bet_usd",            float, 5.0),
    ("DAILY_LOSS_LIMIT_PCT",   "daily_loss_limit_pct",   float, -0.05),
    ("ORDER_OFFSET",           "order_offset",           float, 0.005),
    ("ORDER_TTL_SEC",          "order_ttl_sec",          int,   90),
    ("CHASE_TOLERANCE",        "chase_tolerance",        float, 0.01),
    ("MAX_RETRIES",            "max_retries",            int,   3),
    ("SPORTS",                 "sports",                 list,  ["basketball_nba","americanfootball_nfl","baseball_mlb","icehockey_nhl"]),
    ("POLY_API_KEY",           "poly_api_key",           str,   ""),
    ("POLY_API_SECRET",        "poly_api_secret",        str,   ""),
    ("POLY_API_PASSPHRASE",    "poly_api_passphrase",    str,   ""),
    ("POLY_PRIVATE_KEY",       "poly_private_key",       str,   ""),
    ("POLY_SIGNATURE_TYPE",    "poly_signature_type",    int,   2),
    ("POLY_FUNDER_ADDRESS",    "poly_funder_address",    str,   ""),
    ("ODDS_API_KEY",           "odds_api_key",           str,   ""),
    ("TELEGRAM_BOT_TOKEN",     "telegram_bot_token",     str,   ""),
    ("TELEGRAM_CHAT_ID",       "telegram_chat_id",       str,   ""),
    ("DASHBOARD_PORT",         "dashboard_port",         int,   8502),
    ("DASHBOARD_PASSWORD",     "dashboard_password",     str,   ""),
    ("FEE_RATE",               "fee_rate",               float, 0.0),
    ("TARGET_SHARES",          "target_shares",          float, 500.0),
]

@dataclass
class EdgeConfig:
    trading_enabled: bool = False
    poll_interval_sec: int = 10
    slow_cycle_multiplier: int = 12
    min_edge: float = 0.05
    min_books: int = 6
    devig_method: str = "power"
    safety_haircut: float = 0.01
    max_slippage: float = 0.01
    max_spread: float = 0.01
    min_hours_before_event: float = 1.0
    fraction_kelly: float = 0.15
    max_per_event_pct: float = 0.02
    max_per_sport_pct: float = 0.10
    max_total_exposure_pct: float = 0.30
    cash_buffer_pct: float = 0.20
    min_bet_usd: float = 5.0
    daily_loss_limit_pct: float = -0.05
    order_offset: float = 0.005
    order_ttl_sec: int = 90
    chase_tolerance: float = 0.01
    max_retries: int = 3
    sports: list = field(default_factory=lambda: ["basketball_nba","americanfootball_nfl","baseball_mlb","icehockey_nhl"])
    poly_api_key: str = ""
    poly_api_secret: str = ""
    poly_api_passphrase: str = ""
    poly_private_key: str = ""
    poly_signature_type: int = 2
    poly_funder_address: str = ""
    odds_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    dashboard_port: int = 8502
    dashboard_password: str = ""
    fee_rate: float = 0.0
    target_shares: float = 500.0
    runtime_config_path: str = "logs/runtime_config.json"

    @classmethod
    def from_env(cls) -> "EdgeConfig":
        kwargs = {}
        for env_name, attr, typ, default in CONFIG_FIELDS:
            raw = os.getenv(env_name)
            if raw is None:
                kwargs[attr] = default
                continue
            if typ is bool:
                kwargs[attr] = _cast_bool(raw)
            elif typ is list:
                kwargs[attr] = [s.strip() for s in raw.split(",") if s.strip()]
            elif typ is int:
                kwargs[attr] = int(raw)
            elif typ is float:
                kwargs[attr] = float(raw)
            else:
                kwargs[attr] = raw
        cfg = cls(**kwargs)
        cfg._apply_runtime_overrides()
        return cfg

    def _apply_runtime_overrides(self):
        try:
            with open(self.runtime_config_path) as f:
                overrides = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return
        lookup = {env: (attr, typ) for env, attr, typ, _ in CONFIG_FIELDS}
        for key, val in overrides.items():
            if key in lookup:
                attr, typ = lookup[key]
                try:
                    if typ is bool:
                        setattr(self, attr, _cast_bool(str(val)))
                    elif typ is list:
                        setattr(self, attr, val if isinstance(val, list) else [s.strip() for s in str(val).split(",")])
                    else:
                        setattr(self, attr, typ(val))
                except (ValueError, TypeError):
                    pass
