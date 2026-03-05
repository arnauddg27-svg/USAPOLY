# PolyEdge: Automated EV Trading Bot for Polymarket Sports Markets

**Date:** 2026-02-20
**Status:** Approved
**Author:** Design session with Claude

---

## 1. Overview & Goal

Build an automated trading bot for Polymarket's CLOB that scans sports markets every ~10 seconds and places trades when aggregated, vig-removed sportsbook odds imply a meaningfully different probability than Polymarket prices.

**Core assumption:** A devigged aggregate of 6+ major sportsbook odds is the best available estimate of true probability for sports outcomes.

**Strategy:** EV-only on Polymarket (no hedged arbitrage). Use sportsbook odds purely as a truth signal. All trades happen on Polymarket.

**Execution:** Fully autonomous with kill switch. Bot places limit orders when safe-edge criteria are met. Telegram alerts on every action. Dashboard toggle to pause instantly.

**Target:** High-frequency scanning (10s cycles) with selective trading (few high-confidence trades per day).

---

## 2. Existing Assets Being Reused

### From PolyTrader v0.1.6
- Polymarket CLOB API client (`py_clob_client` integration, L2 auth, order signing)
- Trade execution patterns (limit orders, cancel logic, partial fill handling)
- Position tracking (paginated API, up to 2500 positions)
- Kelly-inspired position sizing (equity-tiered)
- Portfolio management (exit logic, cashout, CTF redemption)
- Telegram notification system (10+ event types)
- FastAPI + HTMX dashboard
- Docker deployment (docker-compose, health checks)
- Configuration system (env vars + runtime overrides)
- Budget tracking and daily loss limits

### From Odds Arb Scanner
- The Odds API client (multi-sport odds fetching)
- Polymarket Gamma API integration (sports market discovery)
- Order book fill simulation (`compute_avg_fill_price`)
- Fuzzy team name matching with 100+ aliases
- Event mapping (sportsbook ↔ Polymarket)
- Data models (SportsGame, SportsOutcome, PolyMarket, OrderBook)

---

## 3. System Architecture

### Architecture Style
Single async Python process with modular components (monolithic). Matches PolyTrader's battle-tested loop pattern. Docker-deployed.

### Project Structure
```
PolyEdge/
├── polyedge/                    # Main package
│   ├── __init__.py
│   ├── main.py                  # Entry point, async main loop
│   ├── config.py                # Configuration schema (env + runtime overrides)
│   ├── models.py                # Shared data classes
│   │
│   ├── data/                    # Data ingestion layer
│   │   ├── odds_api.py          # The Odds API client
│   │   ├── polymarket.py        # Polymarket CLOB + Gamma client
│   │   └── cache.py             # TTL cache for odds snapshots
│   │
│   ├── pipeline/                # Signal generation
│   │   ├── devig.py             # Devigging: multiplicative + power method
│   │   ├── aggregator.py        # Cross-book aggregation (median, outlier removal)
│   │   ├── matcher.py           # Sportsbook ↔ Polymarket event mapping
│   │   └── edge_detector.py     # Safe-edge filter (core decision engine)
│   │
│   ├── execution/               # Trade execution
│   │   ├── executor.py          # Order placement, cancel, partial fill handling
│   │   ├── sizing.py            # Fractional Kelly + position caps
│   │   └── order_manager.py     # Open order tracking, expiry timers
│   │
│   ├── risk/                    # Risk management
│   │   ├── portfolio.py         # Position tracking, exposure calculation
│   │   ├── limits.py            # Per-event, per-sport, daily loss limits
│   │   └── circuit_breaker.py   # Halt conditions
│   │
│   └── monitoring/              # Observability
│       ├── audit_log.py         # Full decision audit trail
│       ├── telegram.py          # Telegram notifications
│       ├── dashboard.py         # FastAPI dashboard
│       └── health.py            # Health check endpoint
│
├── tests/
│   ├── test_devig.py
│   ├── test_aggregator.py
│   ├── test_matcher.py
│   ├── test_edge_detector.py
│   ├── test_sizing.py
│   ├── test_executor.py
│   └── test_integration.py
│
├── backtest/
│   ├── harness.py               # Replay historical data
│   ├── replay.py                # Data replay engine
│   └── report.py                # Brier score, ROI, drawdown reports
│
├── config/
│   ├── .env.example
│   └── markets.yaml             # Allowed sports/leagues
│
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

### Main Loop (Tiered Polling)

```
FAST cycle (every 10 seconds):
  ├── Refresh Polymarket order books for all matched markets
  ├── Run edge detection against cached devigged odds
  ├── If safe edge found → size and execute
  ├── Manage open orders (check fills, enforce expiry, cancel stale)
  └── Check existing positions for exit signals

SLOW cycle (every 12th fast cycle = ~2 minutes):
  ├── Refresh sportsbook odds from The Odds API
  ├── Devig and aggregate across books → update cached true_prob
  ├── Refresh Polymarket sports market list from Gamma API
  ├── Re-run event matching (sportsbook ↔ Polymarket)
  └── Update dashboard data, send periodic alerts
```

### Data Flow
```
The Odds API ──┐                        ┌── Telegram
(6+ books)     │                        │
               ▼                        │
         ┌───────────┐                  │
         │  Devig +  │    ┌─────────┐   │
         │ Aggregate │───▶│  Edge   │───┼── Dashboard
         │ (true p)  │    │Detector │   │
         └───────────┘    └────┬────┘   │
                               │        │
Polymarket ──┐                 ▼        │
(CLOB books) │           ┌─────────┐   │
             ▼           │Executor │───┼── Audit Log
         ┌───────────┐   │+ Sizing │   │
         │  Market   │──▶│         │   │
         │  Scanner  │   └────┬────┘   │
         └───────────┘        │        │
                              ▼        │
                        ┌──────────┐   │
              Matcher──▶│Portfolio │───┘
                        │+ Risk   │
                        └──────────┘
```

---

## 4. Data Sources & APIs

### The Odds API (Sportsbook Odds)
- **Endpoint:** `https://api.the-odds-api.com/v4/sports/{sport}/odds/`
- **Format:** American odds → converted to decimal
- **Markets:** Head-to-head moneylines (h2h)
- **Sports:** NBA, NFL, MLB, NHL, MMA, Tennis, Cricket (configurable)
- **Rate limiting:** Quota-based. Cache results for 2 minutes between fetches.
- **Books used:** DraftKings, FanDuel, BetMGM, Caesars, PointsBet, BetRivers, Bovada, BetOnline, etc.
- **Minimum books required per event:** 6

### Polymarket CLOB API (Prices + Order Placement)
- **Order book:** `https://clob.polymarket.com/book?token_id={token_id}` (public, no rate limit)
- **Order placement:** Authenticated endpoints (L2 auth: apiKey, secret, passphrase, HMAC signing)
- **Order types:** Limit orders (treated as marketable limit if price crosses book)
- **Library:** `py_clob_client`

### Polymarket Gamma API (Market Discovery)
- **Endpoint:** `https://gamma-api.polymarket.com/events`
- **Query:** Filter by sport tag_slug (nba, nfl, etc.)
- **Use:** Discover active sports markets, get condition_ids, token_ids, event metadata

---

## 5. Market Mapping: Sportsbooks ↔ Polymarket

### Matching Strategy
1. Fetch all active sports markets from Polymarket Gamma API
2. Fetch all upcoming games from The Odds API for configured sports
3. Match by:
   - Sport/league alignment (Odds API sport key → Polymarket tag_slug)
   - Team name fuzzy matching (using alias table with 100+ entries)
   - Start time proximity (within 24-hour window)
4. For each match, determine outcome mapping:
   - Which Polymarket token corresponds to which sportsbook team
   - Handle "Will X beat Y?" title patterns
   - Handle direct outcome_a/outcome_b team names

### Mapping Table
Maintained in memory, rebuilt every slow cycle. Deterministic:
```python
{
    event_key: {
        "sport": "basketball_nba",
        "sportsbook_game": SportsGame(...),
        "poly_market": PolyMarket(...),
        "team_a": "Los Angeles Lakers",  # maps to poly token A
        "team_b": "Boston Celtics",       # maps to poly token B
        "last_updated": datetime
    }
}
```

### Safety Rules
- Only trade markets with clean binary outcomes (moneyline/winner)
- Skip events where matching confidence is low (no alias hit, time mismatch)
- Skip events with <6 sportsbook quotes

---

## 6. Probability Pipeline

### Step 1: Convert Odds to Implied Probabilities
For each book's decimal odds `d_A`, `d_B`:
```
implied_A = 1 / d_A
implied_B = 1 / d_B
```

### Step 2: Remove Vig (Devig)

**Method 1 — Multiplicative (default):**
```
overround = implied_A + implied_B    # typically 1.03-1.08
true_A = implied_A / overround
true_B = implied_B / overround
```

**Method 2 — Power devig (more accurate for heavy favorites):**
```
Find exponent k such that: (implied_A)^k + (implied_B)^k = 1
true_A = (implied_A)^k
true_B = (implied_B)^k
Solved via bisection search (k typically 0.9-1.0, converges in ~20 iterations)
```

Power devig accounts for the favorite-longshot bias — books shade longshot odds more heavily. For events with a heavy favorite (<-200), power devig produces materially different (more accurate) probabilities.

Config: `DEVIG_METHOD = "power"` (default) or `"multiplicative"`.

### Step 3: Aggregate Across Books
For each outcome in a matched event:
```
1. Collect devigged probabilities from all N books
2. Compute median and standard deviation
3. Drop outliers: remove books with prob > 2.5σ from median
4. Require N_remaining >= min_books (default: 6)
5. true_prob = median of remaining devigged probs
```

Median chosen over mean for robustness to outlier manipulation.

### Step 4: Polymarket Effective Probability
For the target trade size (e.g., 500 shares):
```
1. Fetch order book asks for the relevant token
2. Walk the book: compute volume-weighted avg fill price for target size
3. effective_poly_prob = avg_fill_price + fee_rate
   (fee_rate ≈ 0% for Polymarket sports taker, but configurable)
```

### Step 5: Edge Calculation
```
raw_edge = true_prob - effective_poly_prob
safety_haircut = 0.005 to 0.01  (configurable, default 0.01)
adjusted_edge = raw_edge - safety_haircut
```

---

## 7. Safe Edge Gate

A trade is executed ONLY if ALL of these conditions are simultaneously true:

| # | Gate | Condition | Default |
|---|------|-----------|---------|
| 1 | Edge threshold | `adjusted_edge >= min_edge` | 0.05 (5.0 pp) |
| 2 | Books required | `N_books_used >= min_books` | 6 |
| 3 | Liquidity depth | Order book depth at target size >= min_depth | 500 shares |
| 4 | Max slippage | Fill price - mid price <= max_slippage | 0.01 (1.0 pp) |
| 5 | Spread | Bid-ask spread <= max_spread | 0.01 (1.0 pp) |
| 6 | Time gate | Event starts in > min_hours_before | 1 hour |
| 7 | No existing position | Not already positioned on this event | - |
| 8 | Per-event limit | New exposure + existing <= max_per_event | 2% of bankroll |
| 9 | Per-sport limit | Sport exposure + new <= max_per_sport | 10% of bankroll |
| 10 | Daily loss limit | Daily PnL > daily_loss_limit | -5% of bankroll |
| 11 | Circuit breakers clear | No active circuit breakers | - |

If any gate fails, the opportunity is logged with the failing gate(s) for analysis.

---

## 8. Position Sizing

### Fractional Kelly
```
edge = adjusted_edge
decimal_payout_odds = 1 / effective_poly_prob
kelly_fraction = edge / (decimal_payout_odds - 1)
conservative_fraction = kelly_fraction * fraction_kelly_mult  # default 0.15

bet_size_usd = bankroll * conservative_fraction
```

### Caps (applied in order)
```
1. Per-event cap:        min(bet_size, bankroll * 0.02)
2. Per-sport cap:        min(bet_size, max_sport_exposure - current_sport_exposure)
3. Daily deployment cap: min(bet_size, max_daily_deployed - current_daily_deployed)
4. Liquidity cap:        min(bet_size, 0.8 * book_depth_usd_within_tolerance)
5. Min floor:            if bet_size < $5 → skip trade
6. Cash buffer:          ensure bankroll - total_exposure - bet_size >= 0.20 * bankroll
```

### Shares Calculation
```
shares = bet_size_usd / avg_fill_price
# Round down to nearest integer
```

---

## 9. Execution Strategy

### Order Placement
1. Compute fair value from true_prob
2. Place **passive limit order** at: `mid_price - offset` (default offset: 0.005)
   - Prefer post-only to avoid taker fees where possible
3. Set order expiry: 90 seconds (configurable)
4. Monitor for fill

### Never-Chase Rule
```
If best_ask moves > chase_tolerance (default 0.01) above our limit price
before fill → cancel immediately. Do not re-place at worse price.
```

### Partial Fill Handling
```
If partially filled:
  - Re-check edge with remaining size
  - If edge still passes all gates → keep order live
  - If edge evaporated or liquidity dried up → cancel remainder
  - Keep the partial position (it still has +EV)
```

### Order Management
- Track all open orders by order_id
- Enforce expiry timers (cancel unfilled orders after TTL)
- Handle API errors with exponential backoff (max 3 retries)
- Log every order state transition

---

## 10. Risk Management

### Exposure Limits
| Control | Default | Description |
|---------|---------|-------------|
| Max per event | 2% of bankroll | No single event dominates |
| Max per sport per day | 10% of bankroll | Limits sport concentration |
| Max total exposure | 30% of bankroll | Always maintain cash buffer |
| Daily loss limit | -5% of bankroll | Stop trading if hit |
| Fraction Kelly | 0.15 | Conservative sizing multiplier |
| Correlated positions | Max 2 per team | Same team across markets |
| Cash buffer | 20% of bankroll | Reserved for fees/settlement |

### Circuit Breakers (auto-halt trading)
- **Stale odds:** No successful Odds API fetch in > 10 minutes
- **API errors:** > 3 consecutive Polymarket API failures
- **Position blowup:** Any single position loss > 50% of initial value
- **Daily loss:** Daily realized + unrealized PnL < daily_loss_limit
- **Abnormal spread:** Average spread across tracked markets jumps > 3x normal
- **Book thin:** Average depth drops below 50% of historical average

When a circuit breaker triggers:
1. Cancel all open orders
2. Send Telegram alert with reason
3. Halt new trades until breaker clears
4. Existing positions remain (no panic sell)
5. Log event with full state snapshot

### Position Exits
- **Resolution:** When Polymarket resolves the market → cash out or redeem
- **Take profit:** If position value > entry + take_profit_pct (default 30%)
- **Time-based:** If event < 1 hour away and in profit → sell
- **Edge reversal:** If true_prob flips (sportsbook odds shift significantly against us) → sell
- **Sports protection:** During live games, do NOT stop-loss. Only exit on take-profit or resolution.

---

## 11. Monitoring, Logging & Safety

### Audit Log
Every trade decision is logged as a JSON record:
```json
{
  "timestamp": "2026-02-20T15:30:45Z",
  "event": "Lakers vs Celtics",
  "sport": "basketball_nba",
  "cycle": 1234,
  "odds_snapshot": {
    "DraftKings": {"team_a": 1.50, "team_b": 2.60},
    "FanDuel": {"team_a": 1.48, "team_b": 2.65}
  },
  "devig_results": {
    "per_book": [{"book": "DraftKings", "method": "power", "prob_a": 0.62, "prob_b": 0.38}],
    "aggregated": {"prob_a": 0.618, "prob_b": 0.382, "books_used": 8, "outliers_dropped": 1}
  },
  "poly_snapshot": {
    "token_id": "0x...",
    "mid": 0.55,
    "best_ask": 0.56,
    "depth_shares": 800,
    "spread": 0.008
  },
  "edge_calc": {
    "true_prob": 0.618,
    "effective_fill": 0.557,
    "raw_edge": 0.061,
    "haircut": 0.01,
    "adjusted_edge": 0.051
  },
  "gates": {
    "edge": {"passed": true, "value": 0.051, "threshold": 0.05},
    "books": {"passed": true, "value": 8, "threshold": 6},
    "liquidity": {"passed": true, "depth": 800, "target": 500},
    "slippage": {"passed": true, "value": 0.007, "threshold": 0.01},
    "spread": {"passed": true, "value": 0.008, "threshold": 0.01},
    "time": {"passed": true, "hours_until": 4.5, "threshold": 1.0}
  },
  "sizing": {
    "kelly_raw": 0.093,
    "kelly_adjusted": 0.014,
    "bet_usd": 42.0,
    "shares": 75
  },
  "action": "PLACE_LIMIT_BUY",
  "order": {
    "order_id": "0x...",
    "price": 0.555,
    "size": 75,
    "expiry_sec": 90
  },
  "outcome": null
}
```

### Telegram Notifications
- Trade placed (with edge %, size, event)
- Trade filled (with fill price, slippage vs expected)
- Trade cancelled (with reason)
- Position resolved (with P&L)
- Daily P&L summary (end of day)
- Circuit breaker triggered (with reason)
- Stale data warning
- Weekly performance report

### Health Checks
- HTTP endpoint (port 8501) reporting: uptime, last cycle time, open positions, daily PnL, API status
- Docker healthcheck integration

### API Rate Limits
- The Odds API: respect monthly quota, cache for 2+ minutes
- Polymarket CLOB: no documented rate limit, but implement backoff on 429s
- Exponential backoff on all API errors (base 1s, max 30s, 3 retries)

---

## 12. Configuration Schema

### Environment Variables (.env)
```bash
# === Polymarket Credentials ===
POLY_API_KEY=
POLY_API_SECRET=
POLY_API_PASSPHRASE=
POLY_PRIVATE_KEY=
POLY_SIGNATURE_TYPE=2          # 0=EOA, 1=MagicLink, 2=Gnosis
POLY_FUNDER_ADDRESS=           # Required for sig type 1 or 2

# === The Odds API ===
ODDS_API_KEY=

# === Telegram ===
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# === Trading ===
TRADING_ENABLED=false          # Kill switch (default: off)
POLL_INTERVAL_SEC=10           # Fast cycle interval
SLOW_CYCLE_MULTIPLIER=12      # Slow cycle = 12 * fast = ~2 min

# === Edge Detection ===
MIN_EDGE_PP=0.05              # Minimum adjusted edge (5 pp)
MIN_BOOKS=6                   # Minimum sportsbooks for signal
DEVIG_METHOD=power            # "power" or "multiplicative"
SAFETY_HAIRCUT=0.01           # Subtract from true_prob (1 pp)
MAX_SLIPPAGE=0.01             # Max fill-vs-mid deviation
MAX_SPREAD=0.01               # Max bid-ask spread
MIN_HOURS_BEFORE_EVENT=1.0    # Ignore events starting within N hours

# === Sizing ===
FRACTION_KELLY=0.15           # Kelly multiplier (0.10-0.25)
MAX_PER_EVENT_PCT=0.02        # Max 2% of bankroll per event
MAX_PER_SPORT_PCT=0.10        # Max 10% per sport per day
MAX_TOTAL_EXPOSURE_PCT=0.30   # Max 30% total deployed
CASH_BUFFER_PCT=0.20          # Always reserve 20%
MIN_BET_USD=5.0               # Skip trades below this
DAILY_LOSS_LIMIT_PCT=-0.05    # Stop at -5% daily

# === Execution ===
ORDER_OFFSET=0.005            # Place limit this far below mid
ORDER_TTL_SEC=90              # Cancel unfilled after N seconds
CHASE_TOLERANCE=0.01          # Cancel if price moves against > this
MAX_RETRIES=3                 # API retry attempts

# === Sports ===
SPORTS=basketball_nba,americanfootball_nfl,baseball_mlb,icehockey_nhl

# === Dashboard ===
DASHBOARD_PORT=8502
DASHBOARD_PASSWORD=            # Optional basic auth
```

### Markets Config (markets.yaml)
```yaml
sports:
  basketball_nba:
    odds_api_key: basketball_nba
    poly_tag: nba
    enabled: true
  americanfootball_nfl:
    odds_api_key: americanfootball_nfl
    poly_tag: nfl
    enabled: true
  baseball_mlb:
    odds_api_key: baseball_mlb
    poly_tag: mlb
    enabled: true
  icehockey_nhl:
    odds_api_key: icehockey_nhl
    poly_tag: nhl
    enabled: true
```

---

## 13. Backtesting & Evaluation

### Backtest Harness
- Replay historical sportsbook odds snapshots (timestamped JSON files)
- Replay historical Polymarket prices/order books
- Run the full pipeline: devig → aggregate → edge detect → size → simulate execution
- Score using:
  - **Brier score** (calibration of true_prob estimates)
  - **Realized ROI** (actual P&L / capital deployed)
  - **Max drawdown** (worst peak-to-trough)
  - **Sharpe ratio** (risk-adjusted returns)
  - **Win rate** (% of trades that were profitable)

### Stress Tests
- Wider spreads (2x, 3x normal)
- Worse slippage (simulate partial fills, adverse selection)
- Delayed odds updates (stale by 1, 5, 10 minutes)
- Reduced book count (what if only 4 books available?)

### Reports
Compare:
- "Trade all edges" (min_edge=1pp) vs "safe edge only" (min_edge=5pp)
- Passive limit vs aggressive marketable limit
- Multiplicative vs power devig
- Different Kelly fractions (0.10, 0.15, 0.20, 0.25)

---

## 14. Security Notes

### Key Management
- All API keys in `.env` file, never committed to git
- `.env` in `.gitignore`
- Private key (POLY_PRIVATE_KEY) for signing orders — never logged, never transmitted except for HMAC
- Separate read-only API keys for dashboard (if supported)

### Operational Security
- Docker containers run as non-root user
- No shell access exposed via dashboard
- Telegram bot token scoped to single chat
- VPS deployed outside restricted jurisdictions (per Polymarket TOS)
- Audit logs stored locally (not transmitted)

### Separation of Concerns
- Signer (private key) only used in executor module
- Data ingestion has no access to signing keys
- Dashboard is read-only (config toggles stored in JSON, not executed)

---

## 15. Test Plan

### Unit Tests
| Module | Tests |
|--------|-------|
| `devig.py` | Multiplicative devig produces probs summing to 1.0; power devig converges; heavy favorite accuracy; edge cases (even odds, extreme favorites) |
| `aggregator.py` | Median aggregation; outlier removal at 2.5σ; minimum book count enforcement; single-book edge case |
| `matcher.py` | Exact name match; alias match; title parsing ("Will X beat Y?"); time window filtering; no-match cases |
| `edge_detector.py` | Edge calculation accuracy; all 11 gates individually; combined gate logic; edge with fees/slippage |
| `sizing.py` | Kelly formula correctness; all caps applied in order; minimum bet floor; cash buffer enforcement |
| `executor.py` | Order placement mock; cancel on chase; partial fill handling; expiry enforcement |

### Integration Tests
- Full pipeline with mocked API responses: odds → devig → match → edge → size → execute
- Circuit breaker triggers correctly
- Portfolio exit logic fires on resolution

### Execution Simulation
- Simulate placing orders against recorded order book snapshots
- Verify slippage estimates match simulated fills
- Test partial fill scenarios

---

## 16. Dependencies

### Python Packages
```
py-clob-client          # Polymarket CLOB SDK
aiohttp                 # Async HTTP for API calls
fastapi                 # Dashboard
uvicorn                 # ASGI server
jinja2                  # Dashboard templates
python-telegram-bot     # Telegram notifications
scipy                   # Bisection for power devig
numpy                   # Statistical operations
pydantic                # Config validation
python-dotenv           # Environment loading
web3                    # On-chain interactions (CTF redemption)
pytest / pytest-asyncio # Testing
```

---

## 17. Decisions Log

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Strategy | EV-only on Polymarket | Simpler execution, single platform, fully automatable |
| Architecture | Monolithic async | Right complexity for few trades/day, proven pattern |
| New project vs extend | New project | Clean separation, no risk of breaking existing bots |
| Devig method | Power + multiplicative | Power is industry standard, multiplicative as fallback |
| Aggregation | Median with outlier removal | Robust to manipulation vs mean |
| Execution | Full auto with kill switch | Capture time-sensitive edges, dashboard toggle for safety |
| Polling frequency | 10s fast / 2min slow | Fast enough for edge capture, respects API quotas |
| Kelly fraction | 0.15 (conservative) | Standard for sports betting quant approaches |
| Edge threshold | 5 pp minimum | Persists after fees/slippage/haircut with margin |
