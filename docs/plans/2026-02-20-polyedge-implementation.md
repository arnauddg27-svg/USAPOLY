# PolyEdge EV Bot Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build an automated Polymarket trading bot that uses devigged aggregate sportsbook odds as "true probability" and trades when there's a safe edge.

**Architecture:** Single async Python process with tiered polling (10s fast / 2min slow cycles). Reuses Polymarket CLOB client from PolyTrader and odds fetching + matching from the arb scanner. New modules: devig, aggregator, edge detector, fractional Kelly sizing, circuit breakers.

**Tech Stack:** Python 3.11+, py-clob-client, aiohttp, scipy, numpy, FastAPI, python-telegram-bot, pytest

**Design doc:** `docs/plans/2026-02-20-polyedge-ev-bot-design.md`

**Existing code to reference:**
- PolyTrader: `PolyTrader_v0.1.6/src/` (polymarket.py, executor.py, config.py, autonomous_vps.py, telegram_notify.py, portfolio.py, budget.py, guards.py)
- Arb Scanner: `Odds copy/odds-arb-scanner/` (models.py, sources/odds_api.py, sources/polymarket.py, matching.py, engine.py, config.py)

---

## Task 1: Project Scaffold & Configuration

**Files:**
- Create: `polyedge/__init__.py`
- Create: `polyedge/config.py`
- Create: `polyedge/models.py`
- Create: `tests/__init__.py`
- Create: `tests/test_config.py`
- Create: `requirements.txt`
- Create: `config/.env.example`
- Create: `.gitignore`

**Step 1: Create project directories**

```bash
mkdir -p polyedge/data polyedge/pipeline polyedge/execution polyedge/risk polyedge/monitoring tests backtest config logs
touch polyedge/__init__.py polyedge/data/__init__.py polyedge/pipeline/__init__.py polyedge/execution/__init__.py polyedge/risk/__init__.py polyedge/monitoring/__init__.py tests/__init__.py
```

**Step 2: Write requirements.txt**

```
py-clob-client>=0.1.0
aiohttp>=3.9.0
fastapi>=0.104.0
uvicorn>=0.24.0
jinja2>=3.1.0
python-telegram-bot>=20.0
scipy>=1.11.0
numpy>=1.24.0
pydantic>=2.0.0
python-dotenv>=1.0.0
web3>=6.0.0
pytest>=7.4.0
pytest-asyncio>=0.21.0
```

**Step 3: Write .gitignore**

```
.env
__pycache__/
*.pyc
logs/
.pytest_cache/
*.egg-info/
dist/
build/
.DS_Store
```

**Step 4: Write config/.env.example**

Copy the full env vars block from design doc Section 12 into `config/.env.example`.

**Step 5: Write the failing config test**

```python
# tests/test_config.py
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
```

**Step 6: Run test to verify it fails**

```bash
cd "/Users/arnauddurand/Documents/Documents - Arnaud's MacBook Pro/PolyEdge"
python -m pytest tests/test_config.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'polyedge.config'`

**Step 7: Implement config.py**

Reference `PolyTrader_v0.1.6/src/config.py` for the declarative CONFIG_FIELDS pattern. Adapt to the design doc Section 12 env vars.

```python
# polyedge/config.py
import os
import json
from dataclasses import dataclass, field
from typing import List

def _cast_bool(v: str) -> bool:
    return v.lower() in ("true", "1", "yes")

CONFIG_FIELDS = [
    # (env_name, attr_name, type, default)
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
```

**Step 8: Run test to verify it passes**

```bash
python -m pytest tests/test_config.py -v
```
Expected: 3 PASSED

**Step 9: Implement models.py**

Adapt from arb scanner `models.py`. Add new fields for EV trading.

```python
# polyedge/models.py
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

class ConfidenceTier(Enum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3

class EdgeSource(Enum):
    POLY_STALE = "poly_stale"
    POLY_THIN_BOOK = "poly_thin_book"
    BOOK_OUTLIER = "book_outlier"
    CONSENSUS = "consensus"
    UNKNOWN = "unknown"

@dataclass
class BookLevel:
    price: float
    size: float

@dataclass
class OrderBook:
    token_id: str
    outcome_name: str
    asks: list[BookLevel] = field(default_factory=list)
    bids: list[BookLevel] = field(default_factory=list)

    @property
    def best_ask(self) -> float:
        return self.asks[0].price if self.asks else 1.0

    @property
    def best_bid(self) -> float:
        return self.bids[0].price if self.bids else 0.0

    @property
    def mid(self) -> float:
        return (self.best_ask + self.best_bid) / 2

    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid

    def depth_shares(self, max_price: float = 1.0) -> float:
        return sum(a.size for a in self.asks if a.price <= max_price)

@dataclass
class SportsOutcome:
    name: str
    american_odds: int
    bookmaker: str

    @property
    def decimal_odds(self) -> float:
        if self.american_odds >= 0:
            return 1 + self.american_odds / 100
        return 1 + 100 / abs(self.american_odds)

    @property
    def implied_prob(self) -> float:
        return 1 / self.decimal_odds

@dataclass
class BookLine:
    """One bookmaker's devigged probabilities for a binary event."""
    bookmaker: str
    prob_a: float
    prob_b: float
    method: str  # "multiplicative" or "power"

@dataclass
class SportsGame:
    sport: str
    home: str
    away: str
    commence_time: str
    outcomes: list[SportsOutcome] = field(default_factory=list)

@dataclass
class AllBookOdds:
    """All bookmakers' odds for a single event (not just best)."""
    sport: str
    home: str
    away: str
    commence_time: str
    books: dict[str, tuple[SportsOutcome, SportsOutcome]] = field(default_factory=dict)
    # books = {"DraftKings": (outcome_a, outcome_b), ...}

@dataclass
class PolyMarket:
    event_title: str
    condition_id: str
    outcome_a: str
    outcome_b: str
    token_id_a: str
    token_id_b: str

@dataclass
class MatchedEvent:
    """A sportsbook event matched to a Polymarket market."""
    sport: str
    all_odds: AllBookOdds
    poly_market: PolyMarket
    team_a: str  # maps to poly outcome_a / token_a
    team_b: str  # maps to poly outcome_b / token_b

@dataclass
class AggregatedProb:
    """Devigged, aggregated true probability for an event."""
    prob_a: float
    prob_b: float
    books_used: int
    outliers_dropped: int
    method: str
    per_book: list[BookLine] = field(default_factory=list)

@dataclass
class EdgeOpportunity:
    """A detected +EV opportunity on Polymarket."""
    matched_event: MatchedEvent
    aggregated: AggregatedProb
    # Which side to buy
    buy_outcome: str  # "a" or "b"
    buy_token_id: str
    true_prob: float
    poly_mid: float
    poly_fill_price: float
    poly_depth_shares: float
    poly_spread: float
    raw_edge: float
    adjusted_edge: float
    # Sizing (filled in by sizing module)
    kelly_raw: float = 0.0
    kelly_adjusted: float = 0.0
    bet_usd: float = 0.0
    shares: int = 0
    # Meta
    confidence: ConfidenceTier = ConfidenceTier.LOW
    edge_source: EdgeSource = EdgeSource.UNKNOWN
    gate_results: dict = field(default_factory=dict)

@dataclass
class OpenOrder:
    """Tracks an open limit order."""
    order_id: str
    token_id: str
    condition_id: str
    side: str
    price: float
    size: float
    placed_at: float  # timestamp
    ttl_sec: int
    original_edge: float
    filled_size: float = 0.0
```

**Step 10: Commit**

```bash
git init
git add polyedge/ tests/ requirements.txt .gitignore config/ docs/
git commit -m "feat: project scaffold with config and data models"
```

---

## Task 2: Devigging Module (TDD)

**Files:**
- Create: `polyedge/pipeline/devig.py`
- Create: `tests/test_devig.py`

**Step 1: Write failing tests**

```python
# tests/test_devig.py
import pytest
from polyedge.pipeline.devig import multiplicative_devig, power_devig

class TestMultiplicativeDevig:
    def test_even_odds(self):
        """Even odds with vig should devig to 50/50."""
        # -110 / -110 = 1.909 / 1.909 => implied 0.5238 each => overround 1.0476
        p_a, p_b = multiplicative_devig(1.909, 1.909)
        assert abs(p_a - 0.5) < 0.001
        assert abs(p_b - 0.5) < 0.001
        assert abs(p_a + p_b - 1.0) < 0.0001

    def test_favorite_underdog(self):
        """-200 / +170 favorite/underdog."""
        # -200 = 1.50 decimal, +170 = 2.70 decimal
        # implied: 0.6667 + 0.3704 = 1.0371
        p_a, p_b = multiplicative_devig(1.50, 2.70)
        assert abs(p_a + p_b - 1.0) < 0.0001
        assert p_a > p_b  # favorite has higher prob
        assert abs(p_a - 0.6429) < 0.01

    def test_no_vig_passthrough(self):
        """If odds already sum to 1.0, should pass through."""
        p_a, p_b = multiplicative_devig(2.0, 2.0)
        assert abs(p_a - 0.5) < 0.0001
        assert abs(p_b - 0.5) < 0.0001

class TestPowerDevig:
    def test_even_odds(self):
        p_a, p_b = power_devig(1.909, 1.909)
        assert abs(p_a - 0.5) < 0.001
        assert abs(p_b - 0.5) < 0.001
        assert abs(p_a + p_b - 1.0) < 0.0001

    def test_favorite_underdog(self):
        p_a, p_b = power_devig(1.50, 2.70)
        assert abs(p_a + p_b - 1.0) < 0.0001
        assert p_a > p_b

    def test_heavy_favorite_differs_from_multiplicative(self):
        """Power devig should differ from multiplicative for heavy favorites."""
        # -500 = 1.20, +400 = 5.0
        m_a, m_b = multiplicative_devig(1.20, 5.0)
        p_a, p_b = power_devig(1.20, 5.0)
        # Both sum to 1
        assert abs(m_a + m_b - 1.0) < 0.0001
        assert abs(p_a + p_b - 1.0) < 0.0001
        # Power should give the longshot HIGHER prob (less vig extracted from longshot)
        assert p_b > m_b

    def test_convergence(self):
        """Should converge for extreme odds."""
        p_a, p_b = power_devig(1.05, 20.0)
        assert abs(p_a + p_b - 1.0) < 0.001
```

**Step 2: Run to verify failure**

```bash
python -m pytest tests/test_devig.py -v
```
Expected: FAIL

**Step 3: Implement devig.py**

```python
# polyedge/pipeline/devig.py
from scipy.optimize import brentq

def multiplicative_devig(decimal_a: float, decimal_b: float) -> tuple[float, float]:
    """Remove vig using multiplicative normalization.
    Returns (true_prob_a, true_prob_b) summing to 1.0.
    """
    imp_a = 1.0 / decimal_a
    imp_b = 1.0 / decimal_b
    overround = imp_a + imp_b
    return imp_a / overround, imp_b / overround

def power_devig(decimal_a: float, decimal_b: float) -> tuple[float, float]:
    """Remove vig using power method (accounts for favorite-longshot bias).
    Finds k such that implied_a^k + implied_b^k = 1.
    Returns (true_prob_a, true_prob_b) summing to 1.0.
    """
    imp_a = 1.0 / decimal_a
    imp_b = 1.0 / decimal_b

    # If already fair, no adjustment needed
    total = imp_a + imp_b
    if abs(total - 1.0) < 1e-9:
        return imp_a, imp_b

    def objective(k: float) -> float:
        return imp_a ** k + imp_b ** k - 1.0

    # k < 1 when overround > 0 (sum > 1); search in reasonable range
    try:
        k = brentq(objective, 0.01, 5.0, xtol=1e-12, maxiter=100)
    except ValueError:
        # Fallback to multiplicative if solver fails
        return multiplicative_devig(decimal_a, decimal_b)

    p_a = imp_a ** k
    p_b = imp_b ** k
    return p_a, p_b

def devig(decimal_a: float, decimal_b: float, method: str = "power") -> tuple[float, float]:
    """Devig wrapper. method = 'power' or 'multiplicative'."""
    if method == "power":
        return power_devig(decimal_a, decimal_b)
    return multiplicative_devig(decimal_a, decimal_b)
```

**Step 4: Run tests**

```bash
python -m pytest tests/test_devig.py -v
```
Expected: ALL PASSED

**Step 5: Commit**

```bash
git add polyedge/pipeline/devig.py tests/test_devig.py
git commit -m "feat: devigging module with multiplicative and power methods"
```

---

## Task 3: Aggregator Module (TDD)

**Files:**
- Create: `polyedge/pipeline/aggregator.py`
- Create: `tests/test_aggregator.py`

**Step 1: Write failing tests**

```python
# tests/test_aggregator.py
import pytest
from polyedge.pipeline.aggregator import aggregate_probs
from polyedge.models import BookLine

def _make_lines(probs_a: list[float], bookmakers: list[str] = None) -> list[BookLine]:
    if bookmakers is None:
        bookmakers = [f"Book{i}" for i in range(len(probs_a))]
    return [BookLine(bookmaker=b, prob_a=p, prob_b=1-p, method="power")
            for b, p in zip(bookmakers, probs_a)]

class TestAggregation:
    def test_basic_median(self):
        lines = _make_lines([0.60, 0.62, 0.61, 0.63, 0.59, 0.61])
        result = aggregate_probs(lines, min_books=6)
        assert result is not None
        assert abs(result.prob_a - 0.61) < 0.01  # median of [0.59,0.60,0.61,0.61,0.62,0.63]
        assert result.books_used == 6
        assert result.outliers_dropped == 0

    def test_outlier_removal(self):
        # 7 books, one extreme outlier
        lines = _make_lines([0.60, 0.61, 0.60, 0.62, 0.61, 0.60, 0.90])
        result = aggregate_probs(lines, min_books=6)
        assert result is not None
        assert result.outliers_dropped >= 1
        assert result.prob_a < 0.65  # outlier removed

    def test_insufficient_books(self):
        lines = _make_lines([0.60, 0.61, 0.62])
        result = aggregate_probs(lines, min_books=6)
        assert result is None  # not enough books

    def test_probs_sum_to_one(self):
        lines = _make_lines([0.55, 0.57, 0.56, 0.58, 0.55, 0.56])
        result = aggregate_probs(lines, min_books=6)
        assert result is not None
        assert abs(result.prob_a + result.prob_b - 1.0) < 0.0001
```

**Step 2: Run to verify failure**

```bash
python -m pytest tests/test_aggregator.py -v
```

**Step 3: Implement aggregator.py**

```python
# polyedge/pipeline/aggregator.py
import statistics
from polyedge.models import BookLine, AggregatedProb

def aggregate_probs(
    lines: list[BookLine],
    min_books: int = 6,
    outlier_sigma: float = 2.5,
) -> AggregatedProb | None:
    """Aggregate devigged probabilities across books using median with outlier removal.

    Returns None if fewer than min_books remain after outlier removal.
    """
    if len(lines) < min_books:
        return None

    probs_a = [l.prob_a for l in lines]
    median_a = statistics.median(probs_a)

    # Need at least 2 for stdev
    if len(probs_a) >= 2:
        stdev_a = statistics.stdev(probs_a)
    else:
        stdev_a = 0.0

    # Drop outliers
    kept = []
    dropped = 0
    for line in lines:
        if stdev_a > 0 and abs(line.prob_a - median_a) > outlier_sigma * stdev_a:
            dropped += 1
        else:
            kept.append(line)

    if len(kept) < min_books:
        return None

    # Final median from kept books
    final_a = statistics.median([l.prob_a for l in kept])
    final_b = 1.0 - final_a  # Force sum to 1

    return AggregatedProb(
        prob_a=final_a,
        prob_b=final_b,
        books_used=len(kept),
        outliers_dropped=dropped,
        method=kept[0].method if kept else "unknown",
        per_book=kept,
    )
```

**Step 4: Run tests**

```bash
python -m pytest tests/test_aggregator.py -v
```

**Step 5: Commit**

```bash
git add polyedge/pipeline/aggregator.py tests/test_aggregator.py
git commit -m "feat: odds aggregator with median and outlier removal"
```

---

## Task 4: Odds API Client (All Books)

**Files:**
- Create: `polyedge/data/odds_api.py`
- Create: `tests/test_odds_api.py`

**Step 1: Write failing tests**

```python
# tests/test_odds_api.py
import pytest
from polyedge.data.odds_api import parse_all_books_response
from polyedge.models import AllBookOdds

SAMPLE_RESPONSE = [
    {
        "sport_key": "basketball_nba",
        "home_team": "Boston Celtics",
        "away_team": "Los Angeles Lakers",
        "commence_time": "2026-02-21T00:00:00Z",
        "bookmakers": [
            {
                "key": "draftkings",
                "title": "DraftKings",
                "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Boston Celtics", "price": -200},
                    {"name": "Los Angeles Lakers", "price": 170},
                ]}],
            },
            {
                "key": "fanduel",
                "title": "FanDuel",
                "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Boston Celtics", "price": -190},
                    {"name": "Los Angeles Lakers", "price": 160},
                ]}],
            },
        ],
    }
]

class TestParseAllBooks:
    def test_parses_multiple_books(self):
        result = parse_all_books_response(SAMPLE_RESPONSE)
        assert len(result) == 1
        game = result[0]
        assert isinstance(game, AllBookOdds)
        assert len(game.books) == 2
        assert "DraftKings" in game.books
        assert "FanDuel" in game.books

    def test_outcome_odds_correct(self):
        game = parse_all_books_response(SAMPLE_RESPONSE)[0]
        dk_a, dk_b = game.books["DraftKings"]
        assert dk_a.name == "Boston Celtics"
        assert dk_a.american_odds == -200
        assert abs(dk_a.decimal_odds - 1.50) < 0.01

    def test_skips_non_h2h(self):
        data = [{"sport_key": "nba", "home_team": "A", "away_team": "B",
                 "commence_time": "2026-01-01T00:00:00Z",
                 "bookmakers": [{"key": "x", "title": "X",
                    "markets": [{"key": "spreads", "outcomes": []}]}]}]
        result = parse_all_books_response(data)
        assert len(result) == 0 or len(result[0].books) == 0
```

**Step 2: Run to verify failure**

**Step 3: Implement odds_api.py**

Key difference from arb scanner: we keep ALL books' odds (not just best), because we need per-book devigging and aggregation.

```python
# polyedge/data/odds_api.py
import os
import aiohttp
from polyedge.models import AllBookOdds, SportsOutcome

ODDS_API_BASE = "https://api.the-odds-api.com/v4"

def parse_all_books_response(data: list[dict]) -> list[AllBookOdds]:
    """Parse The Odds API response keeping ALL bookmakers' odds."""
    results = []
    for event in data:
        books = {}
        home = event.get("home_team", "")
        away = event.get("away_team", "")
        for bm in event.get("bookmakers", []):
            title = bm.get("title", bm.get("key", ""))
            for market in bm.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                outcomes = market.get("outcomes", [])
                if len(outcomes) != 2:
                    continue
                o_a = SportsOutcome(
                    name=outcomes[0]["name"],
                    american_odds=int(outcomes[0]["price"]),
                    bookmaker=title,
                )
                o_b = SportsOutcome(
                    name=outcomes[1]["name"],
                    american_odds=int(outcomes[1]["price"]),
                    bookmaker=title,
                )
                books[title] = (o_a, o_b)
        if books:
            results.append(AllBookOdds(
                sport=event.get("sport_key", ""),
                home=home,
                away=away,
                commence_time=event.get("commence_time", ""),
                books=books,
            ))
    return results

async def fetch_all_odds(sports: list[str], api_key: str) -> list[AllBookOdds]:
    """Fetch odds for all configured sports, keeping all bookmakers."""
    all_games = []
    async with aiohttp.ClientSession() as session:
        for sport in sports:
            url = f"{ODDS_API_BASE}/sports/{sport}/odds/"
            params = {
                "apiKey": api_key,
                "regions": "us",
                "markets": "h2h",
                "oddsFormat": "american",
            }
            try:
                async with session.get(url, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        games = parse_all_books_response(data)
                        for g in games:
                            g.sport = sport
                        all_games.extend(games)
            except Exception:
                continue
    return all_games
```

**Step 4: Run tests, then commit**

```bash
python -m pytest tests/test_odds_api.py -v
git add polyedge/data/odds_api.py tests/test_odds_api.py
git commit -m "feat: odds API client preserving all bookmakers"
```

---

## Task 5: Polymarket Data Client

**Files:**
- Create: `polyedge/data/polymarket.py`
- Create: `tests/test_polymarket.py`

**Step 1: Write failing tests**

```python
# tests/test_polymarket.py
import pytest
from polyedge.models import BookLevel, OrderBook
from polyedge.data.polymarket import compute_avg_fill_price

class TestFillSimulation:
    def test_single_level_exact(self):
        asks = [BookLevel(price=0.55, size=500)]
        avg, filled = compute_avg_fill_price(asks, 500)
        assert abs(avg - 0.55) < 0.001
        assert abs(filled - 500) < 0.01

    def test_multi_level_walk(self):
        asks = [BookLevel(price=0.55, size=200), BookLevel(price=0.56, size=300)]
        avg, filled = compute_avg_fill_price(asks, 400)
        # 200*0.55 + 200*0.56 = 110 + 112 = 222, avg = 222/400 = 0.555
        assert abs(avg - 0.555) < 0.001
        assert abs(filled - 400) < 0.01

    def test_insufficient_depth(self):
        asks = [BookLevel(price=0.55, size=100)]
        avg, filled = compute_avg_fill_price(asks, 500)
        assert abs(filled - 100) < 0.01
        assert abs(avg - 0.55) < 0.001

    def test_empty_book(self):
        avg, filled = compute_avg_fill_price([], 500)
        assert filled == 0
        assert avg == 0
```

**Step 2: Run to verify failure**

**Step 3: Implement polymarket.py**

Adapt from arb scanner's `sources/polymarket.py` (Gamma + CLOB) and PolyTrader's `src/polymarket.py` (CLOB client with auth for orders).

```python
# polyedge/data/polymarket.py
import aiohttp
from polyedge.models import BookLevel, OrderBook, PolyMarket

POLY_CLOB_BASE = "https://clob.polymarket.com"
POLY_GAMMA_BASE = "https://gamma-api.polymarket.com"

SPORT_TAG_SLUGS = {
    "basketball_nba": "nba",
    "americanfootball_nfl": "nfl",
    "baseball_mlb": "mlb",
    "icehockey_nhl": "nhl",
    "mma_mixed_martial_arts": "ufc",
}

def compute_avg_fill_price(asks: list[BookLevel], target_shares: float) -> tuple[float, float]:
    """Walk the order book to compute volume-weighted avg fill price.
    Returns (avg_price, shares_filled).
    """
    if not asks or target_shares <= 0:
        return 0.0, 0.0
    filled = 0.0
    total_cost = 0.0
    for level in asks:
        take = min(level.size, target_shares - filled)
        total_cost += take * level.price
        filled += take
        if filled >= target_shares:
            break
    return (total_cost / filled if filled > 0 else 0.0), filled

def _extract_moneyline_market(event: dict) -> PolyMarket | None:
    """Extract moneyline market from Gamma event. Filters out props/totals."""
    for market in event.get("markets", []):
        if market.get("closed") or not market.get("active"):
            continue
        outcomes = market.get("outcomes", "")
        if isinstance(outcomes, str):
            import json as _json
            try:
                outcomes = _json.loads(outcomes)
            except Exception:
                continue
        tokens = market.get("clobTokenIds", "")
        if isinstance(tokens, str):
            import json as _json
            try:
                tokens = _json.loads(tokens)
            except Exception:
                continue
        if len(outcomes) != 2 or len(tokens) != 2:
            continue
        skip_words = {"Yes", "No", "Over", "Under"}
        if any(o in skip_words for o in outcomes):
            continue
        q = (market.get("question") or "").lower()
        if any(kw in q for kw in ("spread:", "line:", "favorite(")):
            continue
        return PolyMarket(
            event_title=event.get("title", ""),
            condition_id=market.get("conditionId", ""),
            outcome_a=outcomes[0],
            outcome_b=outcomes[1],
            token_id_a=tokens[0],
            token_id_b=tokens[1],
        )
    return None

async def fetch_sports_markets(sports: list[str]) -> list[PolyMarket]:
    """Fetch moneyline markets from Polymarket Gamma API."""
    seen_slugs = set()
    slugs = []
    for s in sports:
        slug = SPORT_TAG_SLUGS.get(s)
        if slug and slug not in seen_slugs:
            slugs.append(slug)
            seen_slugs.add(slug)

    markets = []
    async with aiohttp.ClientSession() as session:
        for slug in slugs:
            offset = 0
            while offset < 1000:
                params = {"tag_slug": slug, "active": "true", "closed": "false",
                          "limit": 50, "offset": offset}
                try:
                    async with session.get(f"{POLY_GAMMA_BASE}/events", params=params) as resp:
                        if resp.status != 200:
                            break
                        events = await resp.json()
                        if not events:
                            break
                        for ev in events:
                            pm = _extract_moneyline_market(ev)
                            if pm:
                                markets.append(pm)
                        offset += 50
                except Exception:
                    break
    return markets

async def fetch_order_book(token_id: str) -> OrderBook:
    """Fetch order book from Polymarket CLOB API."""
    async with aiohttp.ClientSession() as session:
        url = f"{POLY_CLOB_BASE}/book"
        async with session.get(url, params={"token_id": token_id}) as resp:
            data = await resp.json()
            asks = [BookLevel(price=float(a["price"]), size=float(a["size"]))
                    for a in data.get("asks", [])]
            bids = [BookLevel(price=float(b["price"]), size=float(b["size"]))
                    for b in data.get("bids", [])]
            asks.sort(key=lambda x: x.price)
            bids.sort(key=lambda x: -x.price)
            return OrderBook(token_id=token_id, outcome_name="", asks=asks, bids=bids)
```

**Step 4: Run tests, commit**

```bash
python -m pytest tests/test_polymarket.py -v
git add polyedge/data/polymarket.py tests/test_polymarket.py
git commit -m "feat: polymarket data client with fill simulation"
```

---

## Task 6: TTL Cache

**Files:**
- Create: `polyedge/data/cache.py`
- Create: `tests/test_cache.py`

**Step 1: Write failing test**

```python
# tests/test_cache.py
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
```

**Step 2: Implement cache.py**

```python
# polyedge/data/cache.py
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
```

**Step 3: Run tests, commit**

```bash
python -m pytest tests/test_cache.py -v
git add polyedge/data/cache.py tests/test_cache.py
git commit -m "feat: TTL cache for odds snapshots"
```

---

## Task 7: Matcher Module

**Files:**
- Create: `polyedge/pipeline/matcher.py`
- Create: `tests/test_matcher.py`

**Step 1: Write failing tests**

```python
# tests/test_matcher.py
import pytest
from polyedge.models import AllBookOdds, SportsOutcome, PolyMarket
from polyedge.pipeline.matcher import match_events

def _game(home="Boston Celtics", away="Los Angeles Lakers", sport="basketball_nba"):
    return AllBookOdds(sport=sport, home=home, away=away, commence_time="2026-02-21T00:00:00Z",
                       books={"DK": (SportsOutcome(home, -200, "DK"), SportsOutcome(away, 170, "DK"))})

def _poly(outcome_a="Boston Celtics", outcome_b="Los Angeles Lakers", title="NBA: Celtics vs Lakers"):
    return PolyMarket(event_title=title, condition_id="cond1",
                      outcome_a=outcome_a, outcome_b=outcome_b,
                      token_id_a="tok_a", token_id_b="tok_b")

class TestMatching:
    def test_exact_match(self):
        matches = match_events([_game()], [_poly()])
        assert len(matches) == 1
        assert matches[0].team_a == "Boston Celtics"

    def test_alias_match(self):
        matches = match_events([_game()], [_poly("Celtics", "Lakers")])
        assert len(matches) == 1

    def test_no_match(self):
        matches = match_events([_game()], [_poly("Miami Heat", "Chicago Bulls")])
        assert len(matches) == 0

    def test_title_fallback(self):
        poly = _poly("Yes", "No", "Will Lakers beat Celtics?")
        # This should NOT match since outcomes are Yes/No — it's a prop
        # (our Gamma extraction already filters these out, but matcher should handle gracefully)
        matches = match_events([_game()], [poly])
        # Title-based matching attempts, but Yes/No outcomes aren't team names
        assert len(matches) == 0
```

**Step 2: Implement matcher.py**

Adapt directly from arb scanner's `matching.py`. Copy the TEAM_ALIASES dict and matching functions. Change input types from `SportsGame` → `AllBookOdds`.

```python
# polyedge/pipeline/matcher.py
from polyedge.models import AllBookOdds, PolyMarket, MatchedEvent

TEAM_ALIASES: dict[str, list[str]] = {
    # NBA
    "Atlanta Hawks": ["Hawks"], "Boston Celtics": ["Celtics"],
    "Brooklyn Nets": ["Nets"], "Charlotte Hornets": ["Hornets"],
    "Chicago Bulls": ["Bulls"], "Cleveland Cavaliers": ["Cavaliers", "Cavs"],
    "Dallas Mavericks": ["Mavericks", "Mavs"], "Denver Nuggets": ["Nuggets"],
    "Detroit Pistons": ["Pistons"], "Golden State Warriors": ["Warriors", "GSW"],
    "Houston Rockets": ["Rockets"], "Indiana Pacers": ["Pacers"],
    "Los Angeles Clippers": ["Clippers", "LA Clippers"],
    "Los Angeles Lakers": ["Lakers", "LA Lakers"],
    "Memphis Grizzlies": ["Grizzlies"], "Miami Heat": ["Heat"],
    "Milwaukee Bucks": ["Bucks"], "Minnesota Timberwolves": ["Timberwolves", "Wolves"],
    "New Orleans Pelicans": ["Pelicans"], "New York Knicks": ["Knicks"],
    "Oklahoma City Thunder": ["Thunder", "OKC"],
    "Orlando Magic": ["Magic"], "Philadelphia 76ers": ["76ers", "Sixers"],
    "Phoenix Suns": ["Suns"], "Portland Trail Blazers": ["Trail Blazers", "Blazers"],
    "Sacramento Kings": ["Kings"], "San Antonio Spurs": ["Spurs"],
    "Toronto Raptors": ["Raptors"], "Utah Jazz": ["Jazz"],
    "Washington Wizards": ["Wizards"],
    # NFL
    "Arizona Cardinals": ["Cardinals"], "Atlanta Falcons": ["Falcons"],
    "Baltimore Ravens": ["Ravens"], "Buffalo Bills": ["Bills"],
    "Carolina Panthers": ["Panthers"], "Chicago Bears": ["Bears"],
    "Cincinnati Bengals": ["Bengals"], "Cleveland Browns": ["Browns"],
    "Dallas Cowboys": ["Cowboys"], "Denver Broncos": ["Broncos"],
    "Detroit Lions": ["Lions"], "Green Bay Packers": ["Packers"],
    "Houston Texans": ["Texans"], "Indianapolis Colts": ["Colts"],
    "Jacksonville Jaguars": ["Jaguars"], "Kansas City Chiefs": ["Chiefs"],
    "Las Vegas Raiders": ["Raiders"], "Los Angeles Chargers": ["Chargers"],
    "Los Angeles Rams": ["Rams"], "Miami Dolphins": ["Dolphins"],
    "Minnesota Vikings": ["Vikings"], "New England Patriots": ["Patriots", "Pats"],
    "New Orleans Saints": ["Saints"], "New York Giants": ["Giants"],
    "New York Jets": ["Jets"], "Philadelphia Eagles": ["Eagles"],
    "Pittsburgh Steelers": ["Steelers"], "San Francisco 49ers": ["49ers", "Niners"],
    "Seattle Seahawks": ["Seahawks"], "Tampa Bay Buccaneers": ["Buccaneers", "Bucs"],
    "Tennessee Titans": ["Titans"], "Washington Commanders": ["Commanders"],
    # NHL
    "Anaheim Ducks": ["Ducks"], "Boston Bruins": ["Bruins"],
    "Buffalo Sabres": ["Sabres"], "Calgary Flames": ["Flames"],
    "Carolina Hurricanes": ["Hurricanes", "Canes"],
    "Chicago Blackhawks": ["Blackhawks"], "Colorado Avalanche": ["Avalanche", "Avs"],
    "Columbus Blue Jackets": ["Blue Jackets"], "Dallas Stars": ["Stars"],
    "Detroit Red Wings": ["Red Wings"], "Edmonton Oilers": ["Oilers"],
    "Florida Panthers": ["Panthers"], "Minnesota Wild": ["Wild"],
    "Montreal Canadiens": ["Canadiens", "Habs"],
    "Nashville Predators": ["Predators", "Preds"],
    "New Jersey Devils": ["Devils"], "New York Islanders": ["Islanders"],
    "New York Rangers": ["Rangers"], "Ottawa Senators": ["Senators", "Sens"],
    "Philadelphia Flyers": ["Flyers"], "Pittsburgh Penguins": ["Penguins", "Pens"],
    "San Jose Sharks": ["Sharks"], "Seattle Kraken": ["Kraken"],
    "St. Louis Blues": ["Blues"], "Tampa Bay Lightning": ["Lightning", "Bolts"],
    "Toronto Maple Leafs": ["Maple Leafs", "Leafs"],
    "Vancouver Canucks": ["Canucks"], "Vegas Golden Knights": ["Golden Knights", "VGK"],
    "Washington Capitals": ["Capitals", "Caps"], "Winnipeg Jets": ["Jets"],
    # MLB
    "Arizona Diamondbacks": ["Diamondbacks", "D-backs"],
    "Atlanta Braves": ["Braves"], "Baltimore Orioles": ["Orioles", "O's"],
    "Boston Red Sox": ["Red Sox"], "Chicago Cubs": ["Cubs"],
    "Chicago White Sox": ["White Sox"], "Cincinnati Reds": ["Reds"],
    "Cleveland Guardians": ["Guardians"], "Colorado Rockies": ["Rockies"],
    "Detroit Tigers": ["Tigers"], "Houston Astros": ["Astros"],
    "Kansas City Royals": ["Royals"], "Los Angeles Angels": ["Angels"],
    "Los Angeles Dodgers": ["Dodgers"], "Miami Marlins": ["Marlins"],
    "Milwaukee Brewers": ["Brewers"], "Minnesota Twins": ["Twins"],
    "New York Mets": ["Mets"], "New York Yankees": ["Yankees"],
    "Oakland Athletics": ["Athletics", "A's"],
    "Philadelphia Phillies": ["Phillies"], "Pittsburgh Pirates": ["Pirates"],
    "San Diego Padres": ["Padres"], "San Francisco Giants": ["Giants"],
    "Seattle Mariners": ["Mariners"], "St. Louis Cardinals": ["Cardinals"],
    "Tampa Bay Rays": ["Rays"], "Texas Rangers": ["Rangers"],
    "Toronto Blue Jays": ["Blue Jays"], "Washington Nationals": ["Nationals", "Nats"],
}

def _normalize(text: str) -> str:
    return text.lower().strip()

def _names_match(full_name: str, candidate: str) -> bool:
    nf = _normalize(full_name)
    nc = _normalize(candidate)
    if nf == nc or nf in nc or nc in nf:
        return True
    aliases = TEAM_ALIASES.get(full_name, [])
    for alias in aliases:
        na = _normalize(alias)
        if na == nc or na in nc or nc in na:
            return True
    return False

def match_events(
    games: list[AllBookOdds],
    polys: list[PolyMarket],
) -> list[MatchedEvent]:
    """Match sportsbook games to Polymarket markets by team names."""
    results = []
    used_polys = set()

    for game in games:
        for i, poly in enumerate(polys):
            if i in used_polys:
                continue
            # Try direct outcome matching
            home_a = _names_match(game.home, poly.outcome_a)
            away_b = _names_match(game.away, poly.outcome_b)
            home_b = _names_match(game.home, poly.outcome_b)
            away_a = _names_match(game.away, poly.outcome_a)

            if home_a and away_b:
                results.append(MatchedEvent(
                    sport=game.sport, all_odds=game, poly_market=poly,
                    team_a=game.home, team_b=game.away,
                ))
                used_polys.add(i)
                break
            elif home_b and away_a:
                results.append(MatchedEvent(
                    sport=game.sport, all_odds=game, poly_market=poly,
                    team_a=game.away, team_b=game.home,
                ))
                used_polys.add(i)
                break
    return results
```

**Step 3: Run tests, commit**

```bash
python -m pytest tests/test_matcher.py -v
git add polyedge/pipeline/matcher.py tests/test_matcher.py
git commit -m "feat: event matcher with team aliases"
```

---

## Task 8: Edge Detector (Core Decision Engine)

**Files:**
- Create: `polyedge/pipeline/edge_detector.py`
- Create: `tests/test_edge_detector.py`

**Step 1: Write failing tests**

```python
# tests/test_edge_detector.py
import pytest
from polyedge.models import (
    MatchedEvent, AllBookOdds, SportsOutcome, PolyMarket,
    AggregatedProb, BookLine, OrderBook, BookLevel, EdgeOpportunity,
)
from polyedge.pipeline.edge_detector import detect_edge, check_gates
from polyedge.config import EdgeConfig

def _matched(prob_a=0.62):
    game = AllBookOdds("basketball_nba", "TeamA", "TeamB", "2026-02-21T12:00:00Z", {})
    poly = PolyMarket("Game", "cond1", "TeamA", "TeamB", "tok_a", "tok_b")
    agg = AggregatedProb(prob_a=prob_a, prob_b=1-prob_a, books_used=8,
                         outliers_dropped=0, method="power", per_book=[])
    return MatchedEvent("basketball_nba", game, poly, "TeamA", "TeamB"), agg

def _book(best_ask=0.55, depth=800):
    asks = [BookLevel(price=best_ask, size=depth)]
    bids = [BookLevel(price=best_ask - 0.008, size=500)]
    return OrderBook(token_id="tok_a", outcome_name="TeamA", asks=asks, bids=bids)

class TestEdgeDetection:
    def test_positive_edge_detected(self):
        matched, agg = _matched(0.62)
        book_a = _book(0.55, 800)
        cfg = EdgeConfig()
        opps = detect_edge(matched, agg, book_a, _book(0.40, 800), cfg)
        # true_prob=0.62, fill=0.55, raw_edge=0.07, adj=0.06 -> passes 0.05 threshold
        assert len(opps) >= 1
        opp = opps[0]
        assert opp.buy_outcome == "a"
        assert opp.adjusted_edge > 0.05

    def test_no_edge(self):
        matched, agg = _matched(0.56)  # true_prob close to market
        book_a = _book(0.55, 800)
        cfg = EdgeConfig()
        opps = detect_edge(matched, agg, book_a, _book(0.44, 800), cfg)
        # raw_edge=0.01, adj=0.00 -> below threshold
        assert len(opps) == 0

    def test_both_sides_checked(self):
        matched, agg = _matched(0.40)  # team_a is underdog
        book_a = _book(0.55, 800)
        book_b = _book(0.30, 800)  # team_b cheap on poly
        cfg = EdgeConfig()
        opps = detect_edge(matched, agg, book_a, book_b, cfg)
        # team_b: true_prob=0.60, fill=0.30, adj_edge=0.29 -> big edge on side b
        assert any(o.buy_outcome == "b" for o in opps)

class TestGates:
    def test_spread_gate_fails(self):
        cfg = EdgeConfig()
        book = OrderBook("tok", "A",
                         asks=[BookLevel(0.60, 500)],
                         bids=[BookLevel(0.50, 500)])  # spread = 0.10
        gates = check_gates(
            adjusted_edge=0.06, books_used=8, depth=800,
            fill_price=0.60, book=book,
            hours_until=5.0, cfg=cfg,
        )
        assert gates["spread"]["passed"] is False

    def test_all_gates_pass(self):
        cfg = EdgeConfig()
        book = OrderBook("tok", "A",
                         asks=[BookLevel(0.55, 800)],
                         bids=[BookLevel(0.545, 500)])
        gates = check_gates(
            adjusted_edge=0.06, books_used=8, depth=800,
            fill_price=0.553, book=book,
            hours_until=5.0, cfg=cfg,
        )
        assert all(g["passed"] for g in gates.values())
```

**Step 2: Implement edge_detector.py**

```python
# polyedge/pipeline/edge_detector.py
from polyedge.models import (
    MatchedEvent, AggregatedProb, OrderBook, EdgeOpportunity,
    ConfidenceTier, EdgeSource,
)
from polyedge.config import EdgeConfig
from polyedge.data.polymarket import compute_avg_fill_price

def check_gates(
    adjusted_edge: float,
    books_used: int,
    depth: float,
    fill_price: float,
    book: OrderBook,
    hours_until: float,
    cfg: EdgeConfig,
) -> dict:
    """Check all safe-edge gates. Returns dict of {gate_name: {passed, value, threshold}}."""
    mid = book.mid
    slippage = abs(fill_price - mid) if mid > 0 else 0.0
    return {
        "edge": {"passed": adjusted_edge >= cfg.min_edge,
                 "value": adjusted_edge, "threshold": cfg.min_edge},
        "books": {"passed": books_used >= cfg.min_books,
                  "value": books_used, "threshold": cfg.min_books},
        "liquidity": {"passed": depth >= cfg.target_shares,
                      "value": depth, "threshold": cfg.target_shares},
        "slippage": {"passed": slippage <= cfg.max_slippage,
                     "value": slippage, "threshold": cfg.max_slippage},
        "spread": {"passed": book.spread <= cfg.max_spread,
                   "value": book.spread, "threshold": cfg.max_spread},
        "time": {"passed": hours_until >= cfg.min_hours_before_event,
                 "value": hours_until, "threshold": cfg.min_hours_before_event},
    }

def _assess_confidence(edge: float, depth: float, target: float) -> ConfidenceTier:
    if depth >= target and edge >= 0.10:
        return ConfidenceTier.HIGH
    if depth >= target * 0.5 and edge >= 0.05:
        return ConfidenceTier.MEDIUM
    return ConfidenceTier.LOW

def _assess_source(fill: float, true_prob: float, depth: float, target: float) -> EdgeSource:
    if depth < target * 0.5:
        return EdgeSource.POLY_THIN_BOOK
    if fill < true_prob * 0.75:
        return EdgeSource.POLY_STALE
    return EdgeSource.CONSENSUS

def detect_edge(
    matched: MatchedEvent,
    agg: AggregatedProb,
    book_a: OrderBook,
    book_b: OrderBook,
    cfg: EdgeConfig,
    hours_until: float = 24.0,
) -> list[EdgeOpportunity]:
    """Detect EV opportunities on both sides of a matched event."""
    opportunities = []
    target = cfg.target_shares

    for side, true_prob, book, token_id in [
        ("a", agg.prob_a, book_a, matched.poly_market.token_id_a),
        ("b", agg.prob_b, book_b, matched.poly_market.token_id_b),
    ]:
        fill_price, filled = compute_avg_fill_price(book.asks, target)
        if filled <= 0:
            continue

        effective_prob = fill_price + cfg.fee_rate
        raw_edge = true_prob - effective_prob
        adjusted_edge = raw_edge - cfg.safety_haircut

        if adjusted_edge < cfg.min_edge:
            continue

        gates = check_gates(adjusted_edge, agg.books_used, filled,
                            fill_price, book, hours_until, cfg)
        if not all(g["passed"] for g in gates.values()):
            continue

        opp = EdgeOpportunity(
            matched_event=matched,
            aggregated=agg,
            buy_outcome=side,
            buy_token_id=token_id,
            true_prob=true_prob,
            poly_mid=book.mid,
            poly_fill_price=fill_price,
            poly_depth_shares=filled,
            poly_spread=book.spread,
            raw_edge=raw_edge,
            adjusted_edge=adjusted_edge,
            confidence=_assess_confidence(adjusted_edge, filled, target),
            edge_source=_assess_source(fill_price, true_prob, filled, target),
            gate_results=gates,
        )
        opportunities.append(opp)
    return opportunities
```

**Step 3: Run tests, commit**

```bash
python -m pytest tests/test_edge_detector.py -v
git add polyedge/pipeline/edge_detector.py tests/test_edge_detector.py
git commit -m "feat: edge detector with safe-edge gates"
```

---

## Task 9: Position Sizing (Fractional Kelly)

**Files:**
- Create: `polyedge/execution/sizing.py`
- Create: `tests/test_sizing.py`

**Step 1: Write failing tests**

```python
# tests/test_sizing.py
import pytest
from polyedge.execution.sizing import compute_bet_size

class TestSizing:
    def test_basic_kelly(self):
        size = compute_bet_size(
            adjusted_edge=0.06, fill_price=0.55, bankroll=1000,
            fraction_kelly=0.15, max_per_event_pct=0.02,
            total_exposure=0, max_total_pct=0.30, cash_buffer_pct=0.20,
            book_depth_usd=500, min_bet=5.0,
        )
        assert size > 5.0
        assert size <= 20.0  # 2% of 1000

    def test_min_bet_floor(self):
        size = compute_bet_size(
            adjusted_edge=0.06, fill_price=0.55, bankroll=50,
            fraction_kelly=0.15, max_per_event_pct=0.02,
            total_exposure=0, max_total_pct=0.30, cash_buffer_pct=0.20,
            book_depth_usd=500, min_bet=5.0,
        )
        # Kelly on $50 bankroll → tiny bet → below $5 floor
        assert size == 0  # skipped

    def test_exposure_cap(self):
        size = compute_bet_size(
            adjusted_edge=0.10, fill_price=0.40, bankroll=1000,
            fraction_kelly=0.25, max_per_event_pct=0.02,
            total_exposure=290, max_total_pct=0.30, cash_buffer_pct=0.20,
            book_depth_usd=500, min_bet=5.0,
        )
        # Max total = 300, already at 290 → only 10 left
        assert size <= 10.0

    def test_liquidity_cap(self):
        size = compute_bet_size(
            adjusted_edge=0.10, fill_price=0.40, bankroll=10000,
            fraction_kelly=0.25, max_per_event_pct=0.05,
            total_exposure=0, max_total_pct=0.30, cash_buffer_pct=0.20,
            book_depth_usd=20, min_bet=5.0,
        )
        # Only $20 in book depth → cap to 80% = $16
        assert size <= 16.0
```

**Step 2: Implement sizing.py**

```python
# polyedge/execution/sizing.py

def compute_bet_size(
    adjusted_edge: float,
    fill_price: float,
    bankroll: float,
    fraction_kelly: float,
    max_per_event_pct: float,
    total_exposure: float,
    max_total_pct: float,
    cash_buffer_pct: float,
    book_depth_usd: float,
    min_bet: float,
    sport_exposure: float = 0.0,
    max_per_sport_pct: float = 0.10,
) -> float:
    """Compute bet size using fractional Kelly with cascading caps.
    Returns 0 if bet is below minimum or violates constraints.
    """
    if fill_price <= 0 or fill_price >= 1:
        return 0.0

    decimal_odds = 1.0 / fill_price
    kelly_raw = adjusted_edge / (decimal_odds - 1) if decimal_odds > 1 else 0.0
    kelly_adj = kelly_raw * fraction_kelly
    bet = bankroll * kelly_adj

    # Caps in order
    bet = min(bet, bankroll * max_per_event_pct)
    bet = min(bet, bankroll * max_per_sport_pct - sport_exposure)
    bet = min(bet, bankroll * max_total_pct - total_exposure)
    bet = min(bet, book_depth_usd * 0.8)

    # Cash buffer: ensure we keep cash_buffer_pct of bankroll undeployed
    max_deployable = bankroll * (1 - cash_buffer_pct) - total_exposure
    bet = min(bet, max_deployable)

    bet = max(bet, 0)
    if bet < min_bet:
        return 0.0

    return round(bet, 2)
```

**Step 3: Run tests, commit**

```bash
python -m pytest tests/test_sizing.py -v
git add polyedge/execution/sizing.py tests/test_sizing.py
git commit -m "feat: fractional Kelly sizing with cascading caps"
```

---

## Task 10: Executor (Order Placement)

**Files:**
- Create: `polyedge/execution/executor.py`
- Create: `polyedge/execution/order_manager.py`
- Create: `tests/test_executor.py`

**Step 1: Write failing tests** (mock-based, no real API calls)

```python
# tests/test_executor.py
import pytest
from unittest.mock import MagicMock, patch
from polyedge.execution.executor import EdgeExecutor
from polyedge.models import EdgeOpportunity, MatchedEvent, AllBookOdds, PolyMarket, AggregatedProb, OrderBook, BookLevel
from polyedge.config import EdgeConfig

def _make_opportunity(edge=0.06, fill=0.55, bet_usd=20.0, shares=36):
    game = AllBookOdds("nba", "A", "B", "2026-02-21T12:00:00Z", {})
    poly = PolyMarket("Game", "cond1", "A", "B", "tok_a", "tok_b")
    matched = MatchedEvent("nba", game, poly, "A", "B")
    agg = AggregatedProb(0.62, 0.38, 8, 0, "power", [])
    opp = EdgeOpportunity(
        matched_event=matched, aggregated=agg,
        buy_outcome="a", buy_token_id="tok_a",
        true_prob=0.62, poly_mid=0.55, poly_fill_price=fill,
        poly_depth_shares=800, poly_spread=0.005,
        raw_edge=0.07, adjusted_edge=edge,
        bet_usd=bet_usd, shares=shares,
    )
    return opp

class TestExecutor:
    def test_places_limit_order(self):
        mock_poly = MagicMock()
        mock_poly.post_order.return_value = {"ok": True, "orderID": "order123"}
        executor = EdgeExecutor(mock_poly)
        cfg = EdgeConfig()
        opp = _make_opportunity()
        result = executor.place_order(opp, cfg)
        assert result is not None
        mock_poly.post_order.assert_called_once()

    def test_skips_when_trading_disabled(self):
        mock_poly = MagicMock()
        executor = EdgeExecutor(mock_poly)
        cfg = EdgeConfig()
        cfg.trading_enabled = False
        opp = _make_opportunity()
        result = executor.place_order(opp, cfg)
        assert result is None
        mock_poly.post_order.assert_not_called()
```

**Step 2: Implement executor.py**

Adapt from PolyTrader's `src/executor.py` pattern (spread check, depth cap, retry).

```python
# polyedge/execution/executor.py
import time
import logging
from polyedge.models import EdgeOpportunity, OpenOrder
from polyedge.config import EdgeConfig

logger = logging.getLogger(__name__)

class EdgeExecutor:
    def __init__(self, poly_client):
        """poly_client: object with post_order(token_id, side, price, size, post_only) method."""
        self.poly = poly_client

    def place_order(self, opp: EdgeOpportunity, cfg: EdgeConfig) -> OpenOrder | None:
        if not cfg.trading_enabled:
            logger.info("Trading disabled — skipping %s", opp.buy_token_id)
            return None

        if opp.bet_usd <= 0 or opp.shares <= 0:
            return None

        limit_price = round(opp.poly_mid - cfg.order_offset, 4)
        limit_price = max(0.01, min(0.99, limit_price))

        try:
            result = self.poly.post_order(
                token_id=opp.buy_token_id,
                side="BUY",
                price=limit_price,
                size=opp.shares,
                post_only=True,
            )
        except Exception as e:
            logger.error("Order failed: %s", e)
            return None

        if not result or not result.get("ok"):
            logger.warning("Order rejected: %s", result)
            return None

        order_id = result.get("orderID", result.get("order_id", ""))
        return OpenOrder(
            order_id=order_id,
            token_id=opp.buy_token_id,
            condition_id=opp.matched_event.poly_market.condition_id,
            side="BUY",
            price=limit_price,
            size=opp.shares,
            placed_at=time.time(),
            ttl_sec=cfg.order_ttl_sec,
            original_edge=opp.adjusted_edge,
        )
```

```python
# polyedge/execution/order_manager.py
import time
import logging
from polyedge.models import OpenOrder

logger = logging.getLogger(__name__)

class OrderManager:
    def __init__(self, poly_client):
        self.poly = poly_client
        self.open_orders: dict[str, OpenOrder] = {}

    def track(self, order: OpenOrder) -> None:
        self.open_orders[order.order_id] = order

    def check_expiry(self) -> list[str]:
        """Cancel expired orders. Returns list of cancelled order_ids."""
        now = time.time()
        cancelled = []
        for oid, order in list(self.open_orders.items()):
            if now - order.placed_at > order.ttl_sec:
                try:
                    self.poly.cancel_order(oid)
                    logger.info("Cancelled expired order %s", oid)
                except Exception as e:
                    logger.warning("Cancel failed for %s: %s", oid, e)
                del self.open_orders[oid]
                cancelled.append(oid)
        return cancelled

    def remove(self, order_id: str) -> None:
        self.open_orders.pop(order_id, None)

    def has_position(self, condition_id: str) -> bool:
        return any(o.condition_id == condition_id for o in self.open_orders.values())
```

**Step 3: Run tests, commit**

```bash
python -m pytest tests/test_executor.py -v
git add polyedge/execution/ tests/test_executor.py
git commit -m "feat: executor and order manager"
```

---

## Task 11: Risk Module (Limits + Circuit Breakers)

**Files:**
- Create: `polyedge/risk/limits.py`
- Create: `polyedge/risk/circuit_breaker.py`
- Create: `tests/test_risk.py`

**Step 1: Write failing tests**

```python
# tests/test_risk.py
import time
import pytest
from polyedge.risk.limits import ExposureTracker
from polyedge.risk.circuit_breaker import CircuitBreaker

class TestExposureTracker:
    def test_record_and_check(self):
        t = ExposureTracker()
        t.record_trade("basketball_nba", "cond1", 50.0)
        assert t.event_exposure("cond1") == 50.0
        assert t.sport_exposure("basketball_nba") == 50.0
        assert t.total_exposure() == 50.0

    def test_per_event_limit(self):
        t = ExposureTracker()
        t.record_trade("nba", "cond1", 50.0)
        assert t.can_trade("nba", "cond1", 10.0, bankroll=1000,
                           max_per_event=0.02) is False  # 60 > 20

    def test_daily_reset(self):
        t = ExposureTracker()
        t.record_trade("nba", "cond1", 50.0)
        t.record_pnl(-100.0)
        assert t.daily_pnl == -100.0
        t.reset_daily()
        assert t.daily_pnl == 0.0

class TestCircuitBreaker:
    def test_stale_odds(self):
        cb = CircuitBreaker(stale_timeout_sec=10)
        cb.record_odds_fetch()
        assert cb.is_tripped() is False
        cb._last_odds_fetch = time.time() - 15
        assert cb.is_tripped() is True

    def test_api_errors(self):
        cb = CircuitBreaker(max_consecutive_errors=3)
        cb.record_api_error()
        cb.record_api_error()
        assert cb.is_tripped() is False
        cb.record_api_error()
        assert cb.is_tripped() is True

    def test_clear_on_success(self):
        cb = CircuitBreaker(max_consecutive_errors=3)
        cb.record_api_error()
        cb.record_api_error()
        cb.record_api_error()
        assert cb.is_tripped() is True
        cb.record_api_success()
        assert cb.is_tripped() is False
```

**Step 2: Implement limits.py and circuit_breaker.py**

```python
# polyedge/risk/limits.py
from collections import defaultdict

class ExposureTracker:
    def __init__(self):
        self._by_event: dict[str, float] = defaultdict(float)
        self._by_sport: dict[str, float] = defaultdict(float)
        self.daily_pnl: float = 0.0

    def record_trade(self, sport: str, condition_id: str, amount_usd: float) -> None:
        self._by_event[condition_id] += amount_usd
        self._by_sport[sport] += amount_usd

    def record_exit(self, sport: str, condition_id: str, amount_usd: float) -> None:
        self._by_event[condition_id] = max(0, self._by_event[condition_id] - amount_usd)
        self._by_sport[sport] = max(0, self._by_sport[sport] - amount_usd)

    def record_pnl(self, pnl: float) -> None:
        self.daily_pnl += pnl

    def event_exposure(self, condition_id: str) -> float:
        return self._by_event.get(condition_id, 0.0)

    def sport_exposure(self, sport: str) -> float:
        return self._by_sport.get(sport, 0.0)

    def total_exposure(self) -> float:
        return sum(self._by_event.values())

    def can_trade(self, sport: str, condition_id: str, amount: float,
                  bankroll: float, max_per_event: float = 0.02,
                  max_per_sport: float = 0.10, max_total: float = 0.30,
                  daily_loss_limit: float = -0.05) -> bool:
        if self.event_exposure(condition_id) + amount > bankroll * max_per_event:
            return False
        if self.sport_exposure(sport) + amount > bankroll * max_per_sport:
            return False
        if self.total_exposure() + amount > bankroll * max_total:
            return False
        if self.daily_pnl < bankroll * daily_loss_limit:
            return False
        return True

    def reset_daily(self) -> None:
        self.daily_pnl = 0.0
```

```python
# polyedge/risk/circuit_breaker.py
import time
import logging

logger = logging.getLogger(__name__)

class CircuitBreaker:
    def __init__(self, stale_timeout_sec: float = 600, max_consecutive_errors: int = 3):
        self._stale_timeout = stale_timeout_sec
        self._max_errors = max_consecutive_errors
        self._last_odds_fetch: float = time.time()
        self._consecutive_errors: int = 0
        self._manually_tripped: bool = False
        self.trip_reason: str = ""

    def record_odds_fetch(self) -> None:
        self._last_odds_fetch = time.time()

    def record_api_error(self) -> None:
        self._consecutive_errors += 1

    def record_api_success(self) -> None:
        self._consecutive_errors = 0

    def trip(self, reason: str) -> None:
        self._manually_tripped = True
        self.trip_reason = reason
        logger.warning("Circuit breaker tripped: %s", reason)

    def reset(self) -> None:
        self._manually_tripped = False
        self._consecutive_errors = 0
        self.trip_reason = ""

    def is_tripped(self) -> bool:
        if self._manually_tripped:
            return True
        if time.time() - self._last_odds_fetch > self._stale_timeout:
            self.trip_reason = "stale_odds"
            return True
        if self._consecutive_errors >= self._max_errors:
            self.trip_reason = "api_errors"
            return True
        self.trip_reason = ""
        return False
```

**Step 3: Run tests, commit**

```bash
python -m pytest tests/test_risk.py -v
git add polyedge/risk/ tests/test_risk.py
git commit -m "feat: risk limits and circuit breakers"
```

---

## Task 12: Telegram Notifications

**Files:**
- Create: `polyedge/monitoring/telegram.py`

Adapt directly from PolyTrader's `src/telegram_notify.py`. Change message templates for EV trading context.

```python
# polyedge/monitoring/telegram.py
import os
import time
import logging
import requests

logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
_last_sent = 0

def _esc(t: str) -> str:
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def _send(text: str) -> bool:
    global _last_sent
    if not BOT_TOKEN or not CHAT_ID:
        return False
    now = time.time()
    if now - _last_sent < 1.0:
        time.sleep(1.0 - (now - _last_sent))
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        _last_sent = time.time()
        return r.ok
    except Exception as e:
        logger.warning("Telegram send failed: %s", e)
        return False

def edge_trade_placed(event: str, side: str, edge_pct: float, bet_usd: float, price: float):
    _send(f"<b>EDGE TRADE</b>\n{_esc(event)}\nSide: {side}\nEdge: {edge_pct:.1%}\nBet: ${bet_usd:.2f} @ {price:.4f}")

def trade_filled(event: str, order_id: str, fill_price: float, size: float):
    _send(f"<b>FILLED</b>\n{_esc(event)}\nOrder: {order_id[:12]}...\nFill: {fill_price:.4f} x {size:.0f}")

def trade_cancelled(event: str, reason: str):
    _send(f"<b>CANCELLED</b>\n{_esc(event)}\nReason: {_esc(reason)}")

def circuit_breaker(reason: str):
    _send(f"<b>CIRCUIT BREAKER</b>\n{_esc(reason)}")

def daily_summary(equity: float, pnl: float, trades: int, positions: int):
    _send(f"<b>DAILY SUMMARY</b>\nEquity: ${equity:.2f}\nP&L: ${pnl:+.2f}\nTrades: {trades}\nOpen: {positions}")

def bot_started():
    _send("<b>PolyEdge Bot Started</b>")

def bot_error(error: str):
    _send(f"<b>ERROR</b>\n{_esc(error)}")
```

**Commit:**

```bash
git add polyedge/monitoring/telegram.py
git commit -m "feat: telegram notifications for EV trades"
```

---

## Task 13: Audit Logger

**Files:**
- Create: `polyedge/monitoring/audit_log.py`

```python
# polyedge/monitoring/audit_log.py
import json
import os
import time
import logging
from datetime import datetime, timezone
from polyedge.models import EdgeOpportunity

logger = logging.getLogger(__name__)
AUDIT_DIR = "logs/audit"

def log_decision(opp: EdgeOpportunity, action: str, order_result: dict = None, cycle: int = 0) -> None:
    os.makedirs(AUDIT_DIR, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cycle": cycle,
        "event": opp.matched_event.poly_market.event_title,
        "sport": opp.matched_event.sport,
        "buy_outcome": opp.buy_outcome,
        "true_prob": round(opp.true_prob, 4),
        "poly_fill": round(opp.poly_fill_price, 4),
        "poly_mid": round(opp.poly_mid, 4),
        "poly_spread": round(opp.poly_spread, 4),
        "poly_depth": round(opp.poly_depth_shares, 1),
        "raw_edge": round(opp.raw_edge, 4),
        "adjusted_edge": round(opp.adjusted_edge, 4),
        "books_used": opp.aggregated.books_used,
        "confidence": opp.confidence.name,
        "edge_source": opp.edge_source.value,
        "bet_usd": round(opp.bet_usd, 2),
        "shares": opp.shares,
        "action": action,
        "gates": opp.gate_results,
        "order_result": order_result,
    }
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = os.path.join(AUDIT_DIR, f"decisions_{date_str}.jsonl")
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")
```

**Commit:**

```bash
git add polyedge/monitoring/audit_log.py
git commit -m "feat: audit logger for trade decisions"
```

---

## Task 14: Main Loop

**Files:**
- Create: `polyedge/main.py`

This is the orchestrator. Adapt from PolyTrader's `autonomous_vps.py` fast/slow pattern.

```python
# polyedge/main.py
import asyncio
import time
import logging
import os
from polyedge.config import EdgeConfig
from polyedge.data.odds_api import fetch_all_odds, parse_all_books_response
from polyedge.data.polymarket import fetch_sports_markets, fetch_order_book, compute_avg_fill_price
from polyedge.data.cache import TTLCache
from polyedge.pipeline.devig import devig
from polyedge.pipeline.aggregator import aggregate_probs
from polyedge.pipeline.matcher import match_events
from polyedge.pipeline.edge_detector import detect_edge
from polyedge.execution.sizing import compute_bet_size
from polyedge.execution.executor import EdgeExecutor
from polyedge.execution.order_manager import OrderManager
from polyedge.risk.limits import ExposureTracker
from polyedge.risk.circuit_breaker import CircuitBreaker
from polyedge.monitoring import telegram, audit_log
from polyedge.models import BookLine, MatchedEvent
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("polyedge")

KILLSWITCH_PATH = "logs/killswitch.json"

class PolyEdgeBot:
    def __init__(self):
        self.cfg = EdgeConfig.from_env()
        self.odds_cache = TTLCache(ttl_sec=self.cfg.poll_interval_sec * self.cfg.slow_cycle_multiplier)
        self.market_cache = TTLCache(ttl_sec=self.cfg.poll_interval_sec * self.cfg.slow_cycle_multiplier)
        self.match_cache = TTLCache(ttl_sec=self.cfg.poll_interval_sec * self.cfg.slow_cycle_multiplier)
        self.exposure = ExposureTracker()
        self.breaker = CircuitBreaker()
        # poly_client must be initialized with real CLOB credentials for live trading
        self.poly_client = None  # Set up in _init_poly_client()
        self.executor = None
        self.order_mgr = None
        self.cycle = 0
        self.trades_today = 0

    def _init_poly_client(self):
        """Initialize authenticated Polymarket CLOB client.
        Adapt from PolyTrader's PolyInterface.__init__() pattern.
        """
        # TODO: Wire up py_clob_client with self.cfg credentials
        # For now, this is a placeholder that must be filled with actual CLOB client init
        pass

    async def _slow_cycle(self):
        """Refresh odds, markets, and matching (every ~2 min)."""
        logger.info("SLOW CYCLE: refreshing odds & markets")

        # 1. Fetch all sportsbook odds
        all_odds = await fetch_all_odds(self.cfg.sports, self.cfg.odds_api_key)
        if all_odds:
            self.odds_cache.set("all_odds", all_odds)
            self.breaker.record_odds_fetch()
            logger.info("Fetched odds for %d games", len(all_odds))
        else:
            self.breaker.record_api_error()

        # 2. Fetch Polymarket sports markets
        poly_markets = await fetch_sports_markets(self.cfg.sports)
        if poly_markets:
            self.market_cache.set("poly_markets", poly_markets)
            logger.info("Fetched %d Polymarket markets", len(poly_markets))

        # 3. Match events
        all_odds = self.odds_cache.get("all_odds") or []
        poly_markets = self.market_cache.get("poly_markets") or []
        matches = match_events(all_odds, poly_markets)
        self.match_cache.set("matches", matches)
        logger.info("Matched %d events", len(matches))

        # 4. Devig and aggregate for each match
        agg_cache = {}
        for m in matches:
            lines = []
            for bk_name, (out_a, out_b) in m.all_odds.books.items():
                p_a, p_b = devig(out_a.decimal_odds, out_b.decimal_odds, self.cfg.devig_method)
                lines.append(BookLine(bookmaker=bk_name, prob_a=p_a, prob_b=p_b, method=self.cfg.devig_method))
            agg = aggregate_probs(lines, min_books=self.cfg.min_books)
            if agg:
                agg_cache[m.poly_market.condition_id] = agg
        self.odds_cache.set("aggregated", agg_cache)
        logger.info("Aggregated probs for %d events", len(agg_cache))

    async def _fast_cycle(self):
        """Check edges and execute (every 10s)."""
        matches = self.match_cache.get("matches") or []
        agg_cache = self.odds_cache.get("aggregated") or {}

        if self.breaker.is_tripped():
            logger.warning("Circuit breaker active: %s", self.breaker.trip_reason)
            return

        for matched in matches:
            cid = matched.poly_market.condition_id
            agg = agg_cache.get(cid)
            if not agg:
                continue

            # Fetch fresh order books
            try:
                book_a = await fetch_order_book(matched.poly_market.token_id_a)
                book_b = await fetch_order_book(matched.poly_market.token_id_b)
            except Exception as e:
                self.breaker.record_api_error()
                continue
            self.breaker.record_api_success()

            # Compute hours until event
            try:
                commence = datetime.fromisoformat(matched.all_odds.commence_time.replace("Z", "+00:00"))
                hours_until = (commence - datetime.now(timezone.utc)).total_seconds() / 3600
            except Exception:
                hours_until = 24.0

            # Detect edges
            opportunities = detect_edge(matched, agg, book_a, book_b, self.cfg, hours_until)

            for opp in opportunities:
                # Check risk limits
                if not self.exposure.can_trade(
                    opp.matched_event.sport, cid, 10.0,  # placeholder
                    bankroll=1000,  # TODO: get from poly_client
                    max_per_event=self.cfg.max_per_event_pct,
                    max_per_sport=self.cfg.max_per_sport_pct,
                    max_total=self.cfg.max_total_exposure_pct,
                    daily_loss_limit=self.cfg.daily_loss_limit_pct,
                ):
                    continue

                # Size the trade
                bankroll = 1000  # TODO: get from poly_client
                opp.bet_usd = compute_bet_size(
                    adjusted_edge=opp.adjusted_edge,
                    fill_price=opp.poly_fill_price,
                    bankroll=bankroll,
                    fraction_kelly=self.cfg.fraction_kelly,
                    max_per_event_pct=self.cfg.max_per_event_pct,
                    total_exposure=self.exposure.total_exposure(),
                    max_total_pct=self.cfg.max_total_exposure_pct,
                    cash_buffer_pct=self.cfg.cash_buffer_pct,
                    book_depth_usd=opp.poly_depth_shares * opp.poly_fill_price,
                    min_bet=self.cfg.min_bet_usd,
                    sport_exposure=self.exposure.sport_exposure(opp.matched_event.sport),
                    max_per_sport_pct=self.cfg.max_per_sport_pct,
                )
                if opp.bet_usd <= 0:
                    continue
                opp.shares = int(opp.bet_usd / opp.poly_fill_price)

                # Execute
                if self.executor:
                    order = self.executor.place_order(opp, self.cfg)
                    if order:
                        self.order_mgr.track(order)
                        self.exposure.record_trade(opp.matched_event.sport, cid, opp.bet_usd)
                        self.trades_today += 1
                        telegram.edge_trade_placed(
                            matched.poly_market.event_title,
                            f"{'YES' if opp.buy_outcome == 'a' else 'NO'}",
                            opp.adjusted_edge, opp.bet_usd, opp.poly_fill_price,
                        )
                        audit_log.log_decision(opp, "PLACED", cycle=self.cycle)
                    else:
                        audit_log.log_decision(opp, "REJECTED", cycle=self.cycle)
                else:
                    # Dry run mode
                    logger.info("DRY RUN — would trade: %s edge=%.1f%% $%.2f",
                                matched.poly_market.event_title, opp.adjusted_edge*100, opp.bet_usd)
                    audit_log.log_decision(opp, "DRY_RUN", cycle=self.cycle)

        # Manage open orders
        if self.order_mgr:
            self.order_mgr.check_expiry()

    async def run(self):
        """Main loop with fast/slow cycle separation."""
        logger.info("PolyEdge Bot starting")
        telegram.bot_started()

        while True:
            self.cycle += 1

            # Killswitch check
            if os.path.exists(KILLSWITCH_PATH):
                logger.warning("KILLSWITCH active — paused")
                await asyncio.sleep(10)
                continue

            # Reload config
            self.cfg = EdgeConfig.from_env()

            try:
                # Slow cycle
                if self.cycle % self.cfg.slow_cycle_multiplier == 1:
                    await self._slow_cycle()

                # Fast cycle
                await self._fast_cycle()
            except Exception as e:
                logger.error("Cycle error: %s", e, exc_info=True)
                telegram.bot_error(str(e))
                self.breaker.record_api_error()

            await asyncio.sleep(self.cfg.poll_interval_sec)

def main():
    bot = PolyEdgeBot()
    asyncio.run(bot.run())

if __name__ == "__main__":
    main()
```

**Commit:**

```bash
git add polyedge/main.py
git commit -m "feat: main loop with fast/slow cycle orchestration"
```

---

## Task 15: Integration Test

**Files:**
- Create: `tests/test_integration.py`

```python
# tests/test_integration.py
import pytest
from polyedge.data.odds_api import parse_all_books_response
from polyedge.data.polymarket import compute_avg_fill_price
from polyedge.pipeline.devig import devig
from polyedge.pipeline.aggregator import aggregate_probs
from polyedge.pipeline.matcher import match_events
from polyedge.pipeline.edge_detector import detect_edge
from polyedge.execution.sizing import compute_bet_size
from polyedge.models import *
from polyedge.config import EdgeConfig

def test_full_pipeline_finds_edge():
    """End-to-end: odds → devig → aggregate → match → detect → size."""
    # 1. Simulate 8 books with Celtics as ~62% favorite
    books = {}
    for i, (odds_a, odds_b) in enumerate([
        (-160, 140), (-155, 135), (-165, 145), (-150, 130),
        (-160, 140), (-158, 138), (-162, 142), (-155, 135),
    ]):
        name = f"Book{i}"
        books[name] = (
            SportsOutcome("Boston Celtics", odds_a, name),
            SportsOutcome("Los Angeles Lakers", odds_b, name),
        )
    game = AllBookOdds("basketball_nba", "Boston Celtics", "Los Angeles Lakers",
                       "2026-02-21T20:00:00Z", books)

    # 2. Polymarket has Celtics at 0.55 (underpriced vs true ~0.61)
    poly = PolyMarket("NBA: Celtics vs Lakers", "cond1", "Boston Celtics",
                      "Los Angeles Lakers", "tok_celtics", "tok_lakers")

    # 3. Match
    matches = match_events([game], [poly])
    assert len(matches) == 1

    # 4. Devig and aggregate
    lines = []
    for bk, (oa, ob) in game.books.items():
        pa, pb = devig(oa.decimal_odds, ob.decimal_odds, "power")
        lines.append(BookLine(bk, pa, pb, "power"))
    agg = aggregate_probs(lines, min_books=6)
    assert agg is not None
    assert agg.prob_a > 0.58  # Celtics should be ~60-62%

    # 5. Simulate order book
    book_a = OrderBook("tok_celtics", "Boston Celtics",
                       asks=[BookLevel(0.55, 300), BookLevel(0.56, 500)],
                       bids=[BookLevel(0.545, 400)])
    book_b = OrderBook("tok_lakers", "Los Angeles Lakers",
                       asks=[BookLevel(0.44, 500)],
                       bids=[BookLevel(0.435, 400)])

    # 6. Detect edge
    cfg = EdgeConfig()
    opps = detect_edge(matches[0], agg, book_a, book_b, cfg, hours_until=5.0)
    assert len(opps) >= 1
    opp = opps[0]
    assert opp.buy_outcome == "a"  # buy Celtics
    assert opp.adjusted_edge > 0.04

    # 7. Size
    bet = compute_bet_size(
        opp.adjusted_edge, opp.poly_fill_price, bankroll=1000,
        fraction_kelly=0.15, max_per_event_pct=0.02,
        total_exposure=0, max_total_pct=0.30, cash_buffer_pct=0.20,
        book_depth_usd=800*0.55, min_bet=5.0,
    )
    assert bet > 0

def test_full_pipeline_no_edge():
    """Efficient market — no edge detected."""
    books = {}
    for i in range(8):
        books[f"Book{i}"] = (
            SportsOutcome("TeamA", -110, f"Book{i}"),
            SportsOutcome("TeamB", -110, f"Book{i}"),
        )
    game = AllBookOdds("basketball_nba", "TeamA", "TeamB", "2026-02-21T20:00:00Z", books)
    poly = PolyMarket("Game", "cond2", "TeamA", "TeamB", "tok_a", "tok_b")
    matches = match_events([game], [poly])
    assert len(matches) == 1

    lines = []
    for bk, (oa, ob) in game.books.items():
        pa, pb = devig(oa.decimal_odds, ob.decimal_odds, "power")
        lines.append(BookLine(bk, pa, pb, "power"))
    agg = aggregate_probs(lines, min_books=6)

    # Polymarket at fair value (0.50)
    book_a = OrderBook("tok_a", "TeamA", asks=[BookLevel(0.50, 1000)], bids=[BookLevel(0.495, 800)])
    book_b = OrderBook("tok_b", "TeamB", asks=[BookLevel(0.50, 1000)], bids=[BookLevel(0.495, 800)])

    cfg = EdgeConfig()
    opps = detect_edge(matches[0], agg, book_a, book_b, cfg, hours_until=5.0)
    assert len(opps) == 0  # no edge
```

**Commit:**

```bash
python -m pytest tests/test_integration.py -v
git add tests/test_integration.py
git commit -m "feat: end-to-end integration tests"
```

---

## Task 16: Docker & Deployment

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`

```dockerfile
# Dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY polyedge/ polyedge/
COPY config/ config/
RUN mkdir -p logs/audit
CMD ["python", "-m", "polyedge.main"]
```

```yaml
# docker-compose.yml
version: "3.8"
services:
  bot:
    build: .
    container_name: polyedge_bot
    restart: unless-stopped
    env_file: .env
    volumes:
      - ./logs:/app/logs
      - ./.env:/app/.env:ro
    healthcheck:
      test: ["CMD", "python", "-c", "import requests; requests.get('http://localhost:8501/health')"]
      interval: 60s
      timeout: 10s
      retries: 3
```

**Commit:**

```bash
git add Dockerfile docker-compose.yml
git commit -m "feat: Docker deployment setup"
```

---

## Task 17: Wire Up CLOB Client (USER CONTRIBUTION)

This is where the user needs to provide their Polymarket credentials and wire the real `py_clob_client` into `polyedge/main.py`. The `_init_poly_client()` method must be filled in using the pattern from `PolyTrader_v0.1.6/src/polymarket.py`:

- Initialize `ClobClient` with API key, secret, passphrase, private key
- Set signature type and funder address
- Create/derive API creds
- Wire `self.executor = EdgeExecutor(poly_client)` and `self.order_mgr = OrderManager(poly_client)`

The poly_client needs methods: `post_order()`, `cancel_order()`, `get_balance_allowance()`.

---

## Summary

| Task | Module | Tests | Description |
|------|--------|-------|-------------|
| 1 | config, models | test_config | Project scaffold, config system, data models |
| 2 | pipeline/devig | test_devig | Multiplicative + power devigging |
| 3 | pipeline/aggregator | test_aggregator | Cross-book median aggregation |
| 4 | data/odds_api | test_odds_api | All-books odds fetching |
| 5 | data/polymarket | test_polymarket | Market discovery + fill simulation |
| 6 | data/cache | test_cache | TTL cache for odds |
| 7 | pipeline/matcher | test_matcher | Sportsbook ↔ Polymarket matching |
| 8 | pipeline/edge_detector | test_edge_detector | Safe-edge gates + detection |
| 9 | execution/sizing | test_sizing | Fractional Kelly with caps |
| 10 | execution/executor | test_executor | Order placement + management |
| 11 | risk/* | test_risk | Exposure limits + circuit breakers |
| 12 | monitoring/telegram | — | Telegram notifications |
| 13 | monitoring/audit_log | — | Decision audit trail |
| 14 | main.py | — | Main loop orchestrator |
| 15 | — | test_integration | End-to-end pipeline test |
| 16 | Docker | — | Deployment config |
| 17 | CLOB wiring | — | User: wire real Polymarket credentials |
