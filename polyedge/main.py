import asyncio
import json
import logging
import signal
from collections import Counter
import re
from dotenv import load_dotenv
from polyedge.config import EdgeConfig
from polyedge.data.odds_api import fetch_all_odds
from polyedge.data.polymarket import fetch_sports_markets, fetch_order_book
from polyedge.data.cache import TTLCache
from polyedge.pipeline.devig import devig, devig_three_way
from polyedge.pipeline.aggregator import aggregate_probs
from polyedge.pipeline.matcher import (
    match_events,
    orient_book_outcomes,
    spread_points_compatible,
)
from polyedge.pipeline.edge_detector import detect_edge
from polyedge.execution.sizing import compute_bet_size, compute_event_cap_pct
from polyedge.execution.executor import EdgeExecutor
from polyedge.execution.order_manager import OrderManager
from polyedge.risk.limits import ExposureTracker
from polyedge.risk.circuit_breaker import CircuitBreaker
from polyedge.monitoring import audit_log
from polyedge.models import BookLine
from polyedge.simulation import PaperSimulator
from polyedge.paths import KILLSWITCH_PATH, CONFIG_ENV_PATH, HEALTH_PATH, EXPOSURE_STATE_PATH
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("polyedge")

DRY_RUN_BANKROLL_USD = 1000.0


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_risk_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _event_risk_id(matched) -> str:
    """Build a unique risk ID per (game, market_type).

    Including market_type means moneyline and spread for the same game
    get separate exposure caps — they are different Polymarket contracts
    even though the underlying event is the same.
    """
    sport = _normalize_risk_token(getattr(matched, "sport", ""))
    mtype = getattr(matched.poly_market, "market_type", "moneyline")
    kickoff = str(
        getattr(matched.all_odds, "commence_time", "")
        or getattr(matched.poly_market, "start_iso", "")
    ).strip().lower()
    team_tokens = sorted(
        token
        for token in (
            _normalize_risk_token(getattr(matched, "team_a", "")),
            _normalize_risk_token(getattr(matched, "team_b", "")),
        )
        if token
    )
    if sport and kickoff and len(team_tokens) == 2:
        return f"{sport}|{mtype}|{kickoff}|{team_tokens[0]}|{team_tokens[1]}"

    fallback = str(getattr(matched.poly_market, "condition_id", "")).strip()
    if fallback:
        return fallback
    event_title = _normalize_risk_token(getattr(matched.poly_market, "event_title", ""))
    return f"{sport}|{event_title}" if sport or event_title else "unknown_event"


_INTRA_MARKET_HINTS = (
    "first half",
    "1st half",
    "second half",
    "2nd half",
    "first quarter",
    "1st quarter",
    "second quarter",
    "2nd quarter",
    "third quarter",
    "3rd quarter",
    "fourth quarter",
    "4th quarter",
    "first period",
    "1st period",
    "second period",
    "2nd period",
    "third period",
    "3rd period",
    "regulation winner",
    "in regulation",
    "after 60",
    "60-minute",
    "60 minute",
    "60 min",
    "period winner",
    "set winner",
)
_INTRA_SHORT_RE = re.compile(
    r"\b(?:1h|2h|h1|h2|"
    r"1q|2q|3q|4q|q1|q2|q3|q4|"
    r"1p|2p|3p|p1|p2|p3)\b"
)
_INTRA_SEGMENT_RE = re.compile(
    r"\b(?:\d+(?:st|nd|rd|th)|first|second|third|fourth|fifth)\s+"
    r"(?:set|period|quarter|half|inning|map|game)\b"
)
_TENNIS_MAJOR_KEYWORDS = (
    "australian open",
    "french open",
    "roland garros",
    "wimbledon",
    "us open",
    "indian wells",
    "miami open",
    "madrid open",
    "italian open",
    "rome masters",
    "canadian open",
    "cincinnati open",
    "shanghai masters",
    "paris masters",
    "atp finals",
    "wta finals",
    "davis cup",
    "billie jean king cup",
    "united cup",
    "laver cup",
)
_TENNIS_MAJOR_SPORT_TOKENS = (
    "australian_open",
    "french_open",
    "wimbledon",
    "us_open",
    "indian_wells",
    "miami_open",
    "madrid_open",
    "italian_open",
    "canadian_open",
    "cincinnati_open",
    "shanghai",
    "paris_masters",
    "atp_finals",
    "wta_finals",
    "davis_cup",
    "billie_jean_king_cup",
    "united_cup",
    "laver_cup",
)
_TENNIS_EXCLUDED_KEYWORDS = (
    "qualification",
    "qualifying",
    "challenger",
    "itf",
    "futures",
    "atp 125",
    "wta 125",
)


def _is_intra_game_market(matched) -> bool:
    market = getattr(matched, "poly_market", None)
    if market is None:
        return False
    texts = [
        str(getattr(market, "event_title", "") or ""),
        str(getattr(market, "question", "") or ""),
        str(getattr(market, "outcome_a", "") or ""),
        str(getattr(market, "outcome_b", "") or ""),
    ]
    for text in texts:
        t = text.strip().lower()
        if not t:
            continue
        if any(hint in t for hint in _INTRA_MARKET_HINTS):
            return True
        if _INTRA_SHORT_RE.search(t):
            return True
        if _INTRA_SEGMENT_RE.search(t):
            return True
    return False


def _passes_tennis_scope(matched, tennis_major_only: bool) -> bool:
    if not tennis_major_only:
        return True

    sport_key = str(getattr(matched.all_odds, "sport", "") or "").strip().lower()
    if not sport_key.startswith("tennis_"):
        return True

    text_parts = [
        str(getattr(matched.poly_market, "event_title", "") or ""),
        str(getattr(matched.poly_market, "question", "") or ""),
        str(getattr(matched.poly_market, "outcome_a", "") or ""),
        str(getattr(matched.poly_market, "outcome_b", "") or ""),
    ]
    haystack = " ".join(t.strip().lower() for t in text_parts if t)

    if any(keyword in haystack for keyword in _TENNIS_EXCLUDED_KEYWORDS):
        return False
    if any(token in sport_key for token in _TENNIS_MAJOR_SPORT_TOKENS):
        return True
    if any(keyword in haystack for keyword in _TENNIS_MAJOR_KEYWORDS):
        return True
    return False


def summarize_exchange_open_orders(raw_orders) -> tuple[int, float]:
    """Return (open_order_count, open_order_notional_usd) from orders.list() payload."""
    rows = []
    if isinstance(raw_orders, list):
        rows = raw_orders
    elif isinstance(raw_orders, dict):
        for key in ("data", "orders"):
            value = raw_orders.get(key)
            if isinstance(value, list):
                rows = value
                break

    count = 0
    notional = 0.0
    for order in rows:
        if not isinstance(order, dict):
            continue
        status = str(order.get("status") or "").strip().upper()
        if status and status not in {"LIVE", "OPEN", "ACTIVE"}:
            continue

        price = _to_float(order.get("price"))
        if price is None or price <= 0:
            continue

        remaining = _to_float(order.get("remaining_size"))
        if remaining is None:
            base_size = _to_float(order.get("original_size"))
            if base_size is None:
                base_size = _to_float(order.get("size"))
            if base_size is None:
                continue
            matched = _to_float(order.get("size_matched")) or 0.0
            remaining = max(base_size - matched, 0.0)

        if remaining <= 0:
            continue
        count += 1
        notional += price * remaining

    return count, notional


class PolyEdgeBot:
    def __init__(self):
        self.cfg = EdgeConfig.from_env()
        for w in self.cfg.validate():
            logger.warning("Config: %s", w)
        self.odds_cache = TTLCache(ttl_sec=self.cfg.poll_interval_sec * self.cfg.slow_cycle_multiplier)
        self.market_cache = TTLCache(ttl_sec=self.cfg.poll_interval_sec * self.cfg.slow_cycle_multiplier)
        self.match_cache = TTLCache(ttl_sec=self.cfg.poll_interval_sec * self.cfg.slow_cycle_multiplier)
        self.exposure = ExposureTracker(
            state_path=EXPOSURE_STATE_PATH if not self.cfg.simulation_mode else None
        )
        self._last_positions_value = 0.0
        self._position_cost_by_condition: dict[str, float] = {}
        self._slug_to_condition: dict[str, str] = {}
        self.breaker = CircuitBreaker()
        self.poly_client = None
        self.executor = None
        self.order_mgr = None
        self.cycle = 0
        self.trades_today = 0
        self.started_at = datetime.now(timezone.utc)
        self.live_wallet_balance_usd = None
        self.live_wallet_start_usd = None
        start_bankroll = (
            self.cfg.simulation_start_bankroll
            if self.cfg.simulation_start_bankroll > 0
            else DRY_RUN_BANKROLL_USD
        )
        self.simulator = PaperSimulator(start_bankroll=start_bankroll)
        # Prevent entering both sides of the same condition within a bot session.
        self.condition_side_lock: dict[str, str] = {}
        self._cooldowns: dict[str, float] = {}  # key -> expiry timestamp
        self.coverage: dict[str, dict[str, int]] = {
            "odds_games_by_sport": {},
            "matches_by_sport": {},
            "aggregated_by_sport": {},
            "aggregated_by_market_type": {},
        }
        self.last_fast_cycle: dict[str, int | str] = {}

    def _in_cooldown(self, key: str) -> bool:
        """Check if a cooldown key is still active."""
        import time
        return time.time() < self._cooldowns.get(key, 0)

    def _set_cooldown(self, key: str, seconds: int) -> None:
        """Set a cooldown for the given key."""
        import time
        self._cooldowns[key] = time.time() + seconds

    def _is_live_mode(self) -> bool:
        return (
            self.executor is not None
            and self.cfg.trading_enabled
            and not self.cfg.simulation_mode
        )

    def _init_poly_client(self):
        """Initialize authenticated Polymarket US client."""
        if self.cfg.simulation_mode:
            logger.info("Simulation mode — skipping Polymarket US client init")
            return

        if not self.cfg.polymarket_key_id or not self.cfg.polymarket_secret_key:
            logger.warning("No POLYMARKET_KEY_ID/SECRET_KEY set — running in dry-run mode")
            return

        try:
            from polymarket_us import PolymarketUS
            self.poly_client = PolymarketUS(
                key_id=self.cfg.polymarket_key_id,
                secret_key=self.cfg.polymarket_secret_key,
            )
            self.executor = EdgeExecutor(self.poly_client)
            self.order_mgr = OrderManager(self.poly_client)
            logger.info("Polymarket US client initialized")
        except Exception as e:
            logger.error("Polymarket US client init failed: %s — continuing in dry-run mode", e)
            self.poly_client = None
            self.executor = None
            self.order_mgr = None

    def _write_health(self, status: str, last_error: str = "") -> None:
        """Persist lightweight bot health status for external checks."""
        try:
            exposure_usd = float(self.exposure.total_exposure())
            open_orders = list(self.order_mgr.open_orders.values()) if self.order_mgr else []
            tracked_open_orders_count = len(open_orders)
            tracked_open_orders_notional_usd = sum(float(o.size) * float(o.price) for o in open_orders)
            exchange_open_orders_count = None
            exchange_open_orders_notional_usd = None
            if self._is_live_mode() and self.poly_client is not None:
                try:
                    raw_orders = self.poly_client.orders.list()
                    exchange_open_orders_count, exchange_open_orders_notional_usd = summarize_exchange_open_orders(raw_orders)
                except Exception as exc:
                    logger.warning("Failed to fetch exchange open orders for health: %s", exc)
            payload = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": status,
                "cycle": self.cycle,
                "started_at": self.started_at.isoformat(),
                "sports": list(self.cfg.sports),
                "trading_enabled": self.cfg.trading_enabled,
                "simulation_mode": self.cfg.simulation_mode,
                "dry_run": not self._is_live_mode(),
                "circuit_breaker_tripped": self.breaker.is_tripped(),
                "trip_reason": self.breaker.trip_reason,
                "trades_today": self.trades_today,
                "invested_usd": round(exposure_usd, 2),
                "exposure_tracking_usd": round(exposure_usd, 2),
                "open_orders_count": (
                    int(exchange_open_orders_count)
                    if exchange_open_orders_count is not None
                    else int(tracked_open_orders_count)
                ),
                "open_orders_notional_usd": round(
                    exchange_open_orders_notional_usd
                    if exchange_open_orders_notional_usd is not None
                    else tracked_open_orders_notional_usd,
                    2,
                ),
                "tracked_open_orders_count": int(tracked_open_orders_count),
                "tracked_open_orders_notional_usd": round(tracked_open_orders_notional_usd, 2),
                "last_error": last_error,
                "odds_games_by_sport": self.coverage.get("odds_games_by_sport", {}),
                "matches_by_sport": self.coverage.get("matches_by_sport", {}),
                "aggregated_by_sport": self.coverage.get("aggregated_by_sport", {}),
                "aggregated_by_market_type": self.coverage.get("aggregated_by_market_type", {}),
                "last_fast_cycle": self.last_fast_cycle,
            }
            if exchange_open_orders_count is not None:
                payload["exchange_open_orders_count"] = int(exchange_open_orders_count)
            if exchange_open_orders_notional_usd is not None:
                payload["exchange_open_orders_notional_usd"] = round(exchange_open_orders_notional_usd, 2)
            if self.cfg.simulation_mode:
                sim = self.simulator.snapshot()
                payload["simulation"] = sim
                payload["invested_usd"] = round(float(sim.get("total_staked", 0.0)), 2)
                payload["wallet_balance_usd"] = round(float(sim.get("current_bankroll", 0.0)), 2)
                payload["wallet_start_usd"] = round(float(sim.get("start_bankroll", 0.0)), 2)
                payload["pnl_usd"] = round(float(sim.get("expected_pnl", 0.0)), 2)
                payload["open_orders_count"] = 0
                payload["open_orders_notional_usd"] = 0.0
            else:
                # Live-mode "invested" should reflect currently submitted/open notional.
                payload["invested_usd"] = round(
                    exchange_open_orders_notional_usd
                    if exchange_open_orders_notional_usd is not None
                    else tracked_open_orders_notional_usd,
                    2,
                )
                payload["wallet_balance_usd"] = (
                    round(float(self.live_wallet_balance_usd), 2)
                    if self.live_wallet_balance_usd is not None
                    else None
                )
                payload["wallet_start_usd"] = (
                    round(float(self.live_wallet_start_usd), 2)
                    if self.live_wallet_start_usd is not None
                    else None
                )
                payload["pnl_usd"] = (
                    round(float(self.live_wallet_balance_usd - self.live_wallet_start_usd), 2)
                    if self.live_wallet_balance_usd is not None and self.live_wallet_start_usd is not None
                    else None
                )
            HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = HEALTH_PATH.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            tmp_path.replace(HEALTH_PATH)
        except Exception as e:
            logger.warning("Failed to write health status: %s", e)

    @staticmethod
    def _cashout_limit_price(cur_price: float, tick: float, min_limit: float) -> float:
        safe_tick = tick if tick > 0 else 0.01
        clob_max = round(max(0.01, 1.0 - safe_tick), 4)
        candidate = round(cur_price - safe_tick, 4)
        if clob_max < min_limit:
            return clob_max
        return round(max(min_limit, min(candidate, clob_max)), 4)

    def _cashout_winning_positions(self):
        if not self._is_live_mode():
            return
        if not bool(getattr(self.cfg, "auto_cashout_enabled", True)):
            return
        if self.poly_client is None or self.executor is None:
            return

        max_cashouts = max(1, int(getattr(self.cfg, "cashout_max_per_cycle", 1)))
        min_price = float(getattr(self.cfg, "cashout_min_price", 0.99))
        min_limit = float(getattr(self.cfg, "cashout_min_limit_price", 0.98))
        min_size = float(getattr(self.cfg, "cashout_min_size", 1.0))
        min_notional_usd = float(getattr(self.cfg, "cashout_min_notional_usd", 100.0))
        base_cooldown = max(30, int(getattr(self.cfg, "cashout_cooldown_sec", 3600)))

        attempts = 0
        try:
            positions = self.poly_client.portfolio.positions()
        except Exception as exc:
            logger.warning("Failed to fetch positions for cashout: %s", exc)
            return
        if not isinstance(positions, list):
            positions = list(positions) if positions else []
        for pos in positions:
            if attempts >= max_cashouts:
                break

            token_id = str(pos.get("asset") or "").strip()
            if not token_id:
                continue
            cashout_key = f"cashout:{token_id}"
            if self._in_cooldown(cashout_key):
                continue

            size = _to_float(pos.get("size")) or 0.0
            cur_price = _to_float(pos.get("curPrice")) or 0.0
            if size <= 0 or cur_price < min_price:
                continue
            if bool(pos.get("resolved")) or bool(pos.get("redeemable")):
                continue
            if size < min_size:
                self._set_cooldown(cashout_key, max(base_cooldown, 6 * 3600))
                continue
            if (size * cur_price) < min_notional_usd:
                continue

            tick = 0.01
            if self.poly_client is not None:
                try:
                    tick = float(self.poly_client.get_tick_size(token_id) or 0.01)
                except Exception:
                    tick = 0.01
            limit_price = self._cashout_limit_price(cur_price, tick, min_limit)
            if limit_price < min_limit:
                continue

            attempts += 1
            result = self.executor.place_cashout_order(
                token_id=token_id,
                size=size,
                price=limit_price,
            )
            if result.get("ok"):
                est_payout = round(float(size) * float(limit_price), 2)
                self._set_cooldown(cashout_key, base_cooldown)
                logger.info(
                    "AUTO-CASHOUT SUBMITTED token=%s condition=%s size=%.4f price=%.4f est=$%.2f order=%s",
                    token_id,
                    str(pos.get("conditionId") or "")[:18],
                    size,
                    limit_price,
                    est_payout,
                    result.get("order_id", ""),
                )
                break

            err = str(result.get("error") or "")
            lower_err = err.lower()
            cooldown = base_cooldown
            if "orderbook does not exist" in lower_err:
                cooldown = 86400 * 7
            elif "crosses book" in lower_err:
                cooldown = 60
            elif ("min:" in lower_err and "max:" in lower_err) or "price" in lower_err:
                cooldown = 60
            elif "rate limit" in lower_err or "429" in lower_err or "too many" in lower_err:
                cooldown = 120
            self._set_cooldown(cashout_key, cooldown)
            logger.warning(
                "AUTO-CASHOUT FAILED token=%s condition=%s error=%s",
                token_id,
                str(pos.get("conditionId") or "")[:18],
                err,
            )
            break

    @staticmethod
    def _install_signal_handlers(stop_event: asyncio.Event) -> None:
        def _handle_signal(signum, _frame):
            if not stop_event.is_set():
                logger.info("Received signal %s; shutting down gracefully", signum)
                stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _handle_signal)
            except (ValueError, OSError):
                # Signal handlers can only be installed in the main thread/process.
                pass

    def _get_position_value(self) -> float:
        """Sum current market value of all open positions.

        Also builds ``_position_cost_by_condition`` — a map of
        condition_id → cost basis — used for per-event exposure
        checks so we measure *actual* position size rather than
        accumulated order submissions.

        Polymarket US returns positions as:
        {"positions": {"aec-slug": {"netPosition": "14", "cost": {"value": "10.22"}, ...}}}
        We key by condition_id when available, falling back to market slug.
        """
        if self.poly_client is None:
            return 0.0
        try:
            raw = self.poly_client.portfolio.positions()
            positions = raw.get("positions", {}) if isinstance(raw, dict) else {}
            total = 0.0
            by_condition: dict[str, float] = {}

            if isinstance(positions, dict):
                for slug, pos in positions.items():
                    if not isinstance(pos, dict):
                        continue
                    if pos.get("expired"):
                        continue
                    net = _to_float(pos.get("netPosition")) or 0.0
                    if net <= 0:
                        continue
                    cost_val = 0.0
                    cost_obj = pos.get("cost")
                    if isinstance(cost_obj, dict):
                        cost_val = _to_float(cost_obj.get("value")) or 0.0
                    cash_val = 0.0
                    cash_obj = pos.get("cashValue")
                    if isinstance(cash_obj, dict):
                        cash_val = _to_float(cash_obj.get("value")) or 0.0
                    total += cash_val if cash_val > 0 else cost_val

                    # Map to condition_id for per-event cap checks.
                    # Try to find matching condition_id from our market cache.
                    cid = self._slug_to_condition.get(slug, slug)
                    by_condition[cid] = by_condition.get(cid, 0.0) + cost_val

            self._position_cost_by_condition = by_condition
            self._last_positions_value = total
            return total
        except Exception as e:
            logger.warning("Position value fetch failed: %s — using cash only", e)
            return 0.0

    def _get_bankroll(self) -> float | None:
        """Fetch available USD balance from Polymarket US."""
        if self.cfg.simulation_mode:
            return None
        if not self.poly_client:
            return None
        try:
            balances = self.poly_client.account.balances()
            # Polymarket US returns {'balances': [{'currentBalance': 520, ...}]}
            if isinstance(balances, dict) and "balances" in balances:
                bal_list = balances["balances"]
                if bal_list and isinstance(bal_list, list):
                    entry = bal_list[0]
                    for key in ("currentBalance", "buyingPower", "available"):
                        if key in entry:
                            return float(entry[key])
            # Fallback: try flat dict keys
            if isinstance(balances, dict):
                for key in ("available", "balance", "cash", "usd", "available_balance", "currentBalance"):
                    if key in balances:
                        return float(balances[key])
            if hasattr(balances, "available"):
                return float(balances.available)
            if hasattr(balances, "balance"):
                return float(balances.balance)
            return float(balances)
        except Exception as e:
            logger.error("Balance fetch failed: %s", e)
            return None

    async def _slow_cycle(self):
        """Refresh odds, markets, and matching (every ~2 min)."""
        logger.info("SLOW CYCLE: refreshing odds & markets")

        all_odds = await fetch_all_odds(
            self.cfg.sports,
            self.cfg.odds_api_key,
            self.cfg.odds_api_regions,
            self.cfg.odds_api_cricket_regions,
            self.cfg.odds_api_soccer_regions,
            self.cfg.odds_api_nhl_regions,
        )
        if all_odds:
            self.odds_cache.set("all_odds", all_odds)
            self.breaker.record_odds_fetch()
            logger.info("Fetched odds for %d games", len(all_odds))
        else:
            logger.warning("Odds fetch returned empty")
            self.breaker.record_api_error()

        poly_markets = await fetch_sports_markets(self.cfg.sports)
        if poly_markets:
            self.market_cache.set("poly_markets", poly_markets)
            poly_by_type = Counter(getattr(pm, "market_type", "unknown") for pm in poly_markets)
            logger.info("Fetched %d Polymarket markets (%s)", len(poly_markets), dict(poly_by_type))
        else:
            logger.warning("Polymarket fetch returned empty")
            self.breaker.record_api_error()

        all_odds = self.odds_cache.get("all_odds") or []
        poly_markets = self.market_cache.get("poly_markets") or []
        matches = match_events(
            all_odds,
            poly_markets,
            # Keep strict MIN_BOOKS enforcement in aggregation only.
            # Spread pre-filtering at match-time can hide whole sports when
            # Polymarket is spread-heavy (e.g. soccer), so disable it here.
            min_books_for_spread=0,
        )
        if self.cfg.tennis_major_only:
            before = len(matches)
            matches = [m for m in matches if _passes_tennis_scope(m, True)]
            filtered = before - len(matches)
            if filtered > 0:
                logger.info("Filtered %d non-major tennis matches", filtered)
        self.match_cache.set("matches", matches)
        match_by_type = Counter(getattr(m.poly_market, "market_type", "unknown") for m in matches)
        logger.info("Matched %d events (%s)", len(matches), dict(match_by_type))
        # Log spread detail for soccer to diagnose matching issues.
        spread_matches = [m for m in matches if getattr(m.poly_market, "market_type", "") == "spread"]
        if spread_matches:
            for sm in spread_matches[:5]:
                logger.info("  spread match: %s | poly=%s | books=%d",
                            sm.poly_market.event_title,
                            f"{sm.poly_market.outcome_a} vs {sm.poly_market.outcome_b}",
                            len(sm.all_odds.spread_books))
        odds_games_by_sport = Counter(g.sport for g in all_odds)
        matches_by_sport = Counter(m.sport for m in matches)

        def _build_aggregates(min_books: int, soccer_min_books: int):
            cache = {}
            by_sport = Counter()
            by_market_type = Counter()
            for m in matches:
                lines = []
                market_type = getattr(m.poly_market, "market_type", "moneyline")
                source_books = (
                    m.all_odds.spread_books
                    if market_type == "spread"
                    else m.all_odds.books
                )
                for bk_name, (out_a, out_b) in source_books.items():
                    oriented = orient_book_outcomes(m.team_a, m.team_b, out_a, out_b)
                    if oriented is None:
                        continue
                    team_a_outcome, team_b_outcome = oriented
                    if market_type == "spread":
                        compat = spread_points_compatible(
                            m.poly_market,
                            team_a_outcome,
                            team_b_outcome,
                            team_a_name=m.team_a,
                            team_b_name=m.team_b,
                        )
                        if not compat:
                            continue
                    # Use 3-way devig for sports with draw outcomes (soccer, rugby).
                    _sport = str(m.sport)
                    has_draw = _sport.startswith("soccer_") or _sport.startswith("rugby_") or _sport.startswith("rugbyunion_") or _sport.startswith("rugbyleague_")
                    draw_decimal = m.all_odds.draw_odds.get(bk_name) if has_draw else None
                    if has_draw and market_type == "moneyline":
                        if not draw_decimal:
                            # Skip books without draw odds for soccer/rugby moneyline —
                            # 2-way devig inflates win probs by absorbing draw probability.
                            continue
                        p_a, p_b = devig_three_way(
                            team_a_outcome.decimal_odds,
                            draw_decimal,
                            team_b_outcome.decimal_odds,
                            self.cfg.devig_method,
                        )
                    else:
                        p_a, p_b = devig(
                            team_a_outcome.decimal_odds,
                            team_b_outcome.decimal_odds,
                            self.cfg.devig_method,
                        )
                    lines.append(
                        BookLine(
                            bookmaker=bk_name,
                            prob_a=p_a,
                            prob_b=p_b,
                            method=self.cfg.devig_method,
                        )
                    )
                _sp = str(m.sport)
                per_market_min_books = (
                    soccer_min_books
                    if _sp.startswith("soccer_") or _sp.startswith("rugby_") or _sp.startswith("rugbyunion_") or _sp.startswith("rugbyleague_") or _sp.startswith("cricket_")
                    else min_books
                )
                agg = aggregate_probs(lines, min_books=per_market_min_books)
                if agg:
                    cache[m.poly_market.condition_id] = agg
                    by_sport[m.sport] += 1
                    by_market_type[market_type] += 1
            return cache, by_sport, by_market_type

        required_books = max(1, int(self.cfg.min_books))
        required_soccer_books = max(1, int(self.cfg.soccer_min_books))
        agg_cache, aggregated_by_sport, aggregated_by_market_type = _build_aggregates(
            required_books,
            required_soccer_books,
        )
        if not agg_cache and matches:
            logger.warning(
                "No events met thresholds MIN_BOOKS=%d SOCCER_MIN_BOOKS=%d in this cycle",
                required_books,
                required_soccer_books,
            )
        self.odds_cache.set("aggregated", agg_cache)
        self.coverage = {
            "odds_games_by_sport": dict(odds_games_by_sport),
            "matches_by_sport": dict(matches_by_sport),
            "aggregated_by_sport": dict(aggregated_by_sport),
            "aggregated_by_market_type": dict(aggregated_by_market_type),
        }
        logger.info("Aggregated probs for %d events (by_type=%s)", len(agg_cache), dict(aggregated_by_market_type))

    async def _fast_cycle(self):
        """Check edges and execute (every 10s)."""
        cycle_stats: dict[str, int | str] = {
            "cycle": int(self.cycle),
            "status": "started",
            "matches_total": 0,
            "with_agg": 0,
            "opportunities": 0,
            "submitted": 0,
            "rejected": 0,
            "simulated": 0,
            "dry_run": 0,
            "skipped_no_agg": 0,
            "skipped_order_book_fetch": 0,
            "skipped_segment_market": 0,
            "skipped_event_started": 0,
            "skipped_pre_event_window": 0,
            "skipped_no_edge_or_gates": 0,
            "skipped_opposite_side_locked": 0,
            "skipped_bet_too_small": 0,
            "skipped_exposure": 0,
            "blocked_circuit_breaker": 0,
            "blocked_bankroll_unavailable": 0,
            "blocked_bankroll_zero": 0,
            "trip_reason": "",
        }
        close_before_event_sec = max(0, int(self.cfg.close_orders_before_event_sec))
        # Always process TTL expiry, even if trading is temporarily blocked.
        if self.order_mgr:
            cancelled = self.order_mgr.check_expiry(
                close_before_event_sec=close_before_event_sec
            )
            for order in cancelled:
                if order.amount_usd > 0 and order.sport:
                    self.exposure.record_exit(
                        order.sport,
                        order.risk_event_id or order.condition_id,
                        order.amount_usd,
                    )

        matches = self.match_cache.get("matches")
        if matches is None:
            cycle_stats["status"] = "no_matches_cache"
            self.last_fast_cycle = cycle_stats
            return
        cycle_stats["matches_total"] = len(matches)
        agg_cache = self.odds_cache.get("aggregated") or {}

        if self.breaker.is_tripped():
            cycle_stats["status"] = "blocked_circuit_breaker"
            cycle_stats["blocked_circuit_breaker"] = 1
            cycle_stats["trip_reason"] = str(self.breaker.trip_reason or "")
            logger.warning("Circuit breaker active: %s", self.breaker.trip_reason)
            self.last_fast_cycle = cycle_stats
            return

        # Fetch bankroll once per cycle (not per opportunity).
        if self.cfg.simulation_mode:
            bankroll = self.simulator.current_bankroll
            if bankroll <= 0:
                cycle_stats["status"] = "blocked_bankroll_zero"
                cycle_stats["blocked_bankroll_zero"] = 1
                logger.warning("Simulation bankroll is depleted — skipping fast cycle")
                self.last_fast_cycle = cycle_stats
                return
        elif self._is_live_mode():
            bankroll = self._get_bankroll()
            if bankroll is None:
                cycle_stats["status"] = "blocked_bankroll_unavailable"
                cycle_stats["blocked_bankroll_unavailable"] = 1
                logger.warning("Bankroll unavailable — skipping fast cycle")
                self.last_fast_cycle = cycle_stats
                return
            self.live_wallet_balance_usd = float(bankroll)
            if self.live_wallet_start_usd is None:
                self.live_wallet_start_usd = float(bankroll)
            if bankroll <= 0:
                cycle_stats["status"] = "blocked_bankroll_zero"
                cycle_stats["blocked_bankroll_zero"] = 1
                logger.debug("Bankroll is $0 — no funds to trade")
                self.last_fast_cycle = cycle_stats
                return
        else:
            bankroll = DRY_RUN_BANKROLL_USD

        # Refresh position data for exposure caps.
        if self._is_live_mode():
            self._slug_to_condition = {
                m.poly_market.market_slug: m.poly_market.condition_id
                for m in matches if m.poly_market.market_slug
            }
            self._get_position_value()

        for matched in matches:
            cid = matched.poly_market.condition_id
            risk_event_id = _event_risk_id(matched)
            agg = agg_cache.get(cid)
            if not agg:
                cycle_stats["skipped_no_agg"] += 1
                continue
            cycle_stats["with_agg"] += 1

            if _is_intra_game_market(matched):
                cycle_stats["skipped_segment_market"] += 1
                continue

            try:
                book_a = await fetch_order_book(matched.poly_market.token_id_a)
                book_b = await fetch_order_book(matched.poly_market.token_id_b)
            except Exception as e:
                cycle_stats["skipped_order_book_fetch"] += 1
                logger.warning("Order book fetch failed for %s: %s", cid, e)
                self.breaker.record_api_error()
                continue
            self.breaker.record_api_success()

            # Fail CLOSED: if time unparseable, treat as imminent (blocks trade)
            try:
                commence = datetime.fromisoformat(matched.all_odds.commence_time.replace("Z", "+00:00"))
                hours_until = (commence - datetime.now(timezone.utc)).total_seconds() / 3600
                event_start_ts = commence.timestamp()
            except Exception as e:
                logger.warning("Cannot parse commence_time '%s': %s — treating as imminent",
                               matched.all_odds.commence_time, e)
                hours_until = 0.0
                event_start_ts = None

            # Never keep orders alive into live play.
            now_ts = datetime.now(timezone.utc).timestamp()
            if event_start_ts is not None and now_ts >= event_start_ts:
                cycle_stats["skipped_event_started"] += 1
                continue
            if event_start_ts is not None and close_before_event_sec > 0:
                if now_ts >= event_start_ts - close_before_event_sec:
                    cycle_stats["skipped_pre_event_window"] += 1
                    continue

            edge_result = detect_edge(
                matched,
                agg,
                book_a,
                book_b,
                self.cfg,
                hours_until,
                include_rejected=True,
            )
            if isinstance(edge_result, tuple):
                opportunities, rejected_edge_candidates = edge_result
            else:
                opportunities = edge_result
                rejected_edge_candidates = []

            for opp in rejected_edge_candidates:
                # Classify the reject reason from gate results.
                failed_gates = [
                    g for g, v in (opp.gate_results or {}).items()
                    if not v.get("passed", True)
                ]
                if "empty_book" in failed_gates:
                    reject_reason = "empty_book"
                elif "fill_price_cap" in failed_gates:
                    cap_val = (opp.gate_results or {}).get("fill_price_cap", {}).get("value", "?")
                    reject_reason = f"fill_price_cap:{cap_val}"
                elif opp.adjusted_edge < self.cfg.min_edge:
                    reject_reason = (
                        f"below_min_edge:{opp.adjusted_edge:.4f}<{self.cfg.min_edge:.4f}"
                    )
                elif opp.adjusted_edge > self.cfg.max_edge:
                    reject_reason = (
                        f"above_max_edge:{opp.adjusted_edge:.4f}>{self.cfg.max_edge:.4f}"
                    )
                elif failed_gates:
                    reject_reason = f"gates_failed:{','.join(failed_gates)}"
                else:
                    reject_reason = f"outside_edge_band:{opp.adjusted_edge:.4f}"
                cycle_stats["rejected"] += 1
                audit_log.log_decision(
                    opp,
                    "REJECTED",
                    cycle=self.cycle,
                    meta={"reject_reason": reject_reason},
                )
            if not opportunities:
                cycle_stats["skipped_no_edge_or_gates"] += 1
                continue
            cycle_stats["opportunities"] += len(opportunities)

            for opp in opportunities:
                locked_side = self.condition_side_lock.get(cid)
                if locked_side and locked_side != opp.buy_outcome:
                    cycle_stats["skipped_opposite_side_locked"] += 1
                    cycle_stats["rejected"] += 1
                    audit_log.log_decision(
                        opp,
                        "REJECTED",
                        cycle=self.cycle,
                        meta={"reject_reason": f"opposite_side_locked:{locked_side}"},
                    )
                    continue

                event_cap_pct = compute_event_cap_pct(
                    adjusted_edge=opp.adjusted_edge,
                    fill_price=opp.poly_fill_price,
                    fraction_kelly=self.cfg.fraction_kelly,
                    max_per_event_pct=self.cfg.max_per_event_pct,
                    event_cap_kelly_multiplier=self.cfg.event_cap_kelly_multiplier,
                    min_edge=self.cfg.min_edge,
                )
                # Use actual position cost from Polymarket instead of the
                # accumulating tracker, which over-counts across cycles.
                event_exposure = self._position_cost_by_condition.get(cid, 0.0)
                opp.bet_usd = compute_bet_size(
                    adjusted_edge=opp.adjusted_edge,
                    fill_price=opp.poly_fill_price,
                    bankroll=bankroll,
                    fraction_kelly=self.cfg.fraction_kelly,
                    max_per_event_pct=self.cfg.max_per_event_pct,
                    total_exposure=self._last_positions_value,
                    max_total_pct=self.cfg.max_total_exposure_pct,
                    cash_buffer_pct=self.cfg.cash_buffer_pct,
                    book_depth_usd=opp.poly_depth_shares * opp.poly_fill_price,
                    min_bet=self.cfg.min_bet_usd,
                    event_exposure=event_exposure,
                    sport_exposure=0.0,  # tracker over-counts; rely on event + total caps
                    max_per_sport_pct=self.cfg.max_per_sport_pct,
                    event_cap_kelly_multiplier=self.cfg.event_cap_kelly_multiplier,
                    min_edge=self.cfg.min_edge,
                )
                if opp.bet_usd <= 0:
                    cycle_stats["skipped_bet_too_small"] += 1
                    audit_log.log_decision(
                        opp, "REJECTED", cycle=self.cycle,
                        meta={"reject_reason": "bet_too_small"},
                    )
                    continue
                opp.shares = int(opp.bet_usd / opp.poly_fill_price)

                # Hard cap: actual position + new bet must not exceed per-event limit.
                # Uses real Polymarket position data, not the accumulating tracker.
                hard_cap = bankroll * self.cfg.max_per_event_pct
                if event_exposure + opp.bet_usd > hard_cap:
                    cycle_stats["skipped_exposure"] += 1
                    audit_log.log_decision(
                        opp, "REJECTED", cycle=self.cycle,
                        meta={
                            "reject_reason": (
                                f"event_cap:{event_exposure:.2f}+{opp.bet_usd:.2f}="
                                f"{event_exposure+opp.bet_usd:.2f}>"
                                f"{hard_cap:.2f}"
                            ),
                        },
                    )
                    continue

                if self.cfg.simulation_mode:
                    cycle_stats["simulated"] += 1
                    sim = self.simulator.record_bet(opp, cycle=self.cycle)
                    self.exposure.record_trade(
                        opp.matched_event.sport,
                        risk_event_id,
                        opp.bet_usd,
                        event_start_ts=event_start_ts,
                    )
                    self.condition_side_lock[cid] = opp.buy_outcome
                    self.trades_today += 1
                    logger.info(
                        "SIMULATED — %s edge=%.1f%% $%.2f bankroll=$%.2f",
                        matched.poly_market.event_title,
                        opp.adjusted_edge * 100,
                        opp.bet_usd,
                        self.simulator.current_bankroll,
                    )
                    audit_log.log_decision(
                        opp,
                        "SIMULATED",
                        cycle=self.cycle,
                        meta={"simulation": sim},
                    )
                elif self._is_live_mode():
                    order = self.executor.place_order(opp, self.cfg)
                    if order:
                        order.event_title = matched.poly_market.event_title
                        order.event_start_ts = event_start_ts
                        order.risk_event_id = risk_event_id
                        if not self.cfg.no_resting_orders and self.order_mgr is not None:
                            self.order_mgr.track(order)
                        # Exposure limits should apply to any successfully submitted live trade,
                        # including no-resting (FOK/IOC-like) executions.
                        self.exposure.record_trade(
                            opp.matched_event.sport,
                            risk_event_id,
                            opp.bet_usd,
                            event_start_ts=event_start_ts,
                        )
                        self.condition_side_lock[cid] = opp.buy_outcome
                        self.trades_today += 1
                        cycle_stats["submitted"] += 1
                        logger.info("ORDER SUBMITTED: %s %s edge=%.1f%% $%.2f @ %.4f",
                                    matched.poly_market.event_title,
                                    "YES" if opp.buy_outcome == "a" else "NO",
                                    opp.adjusted_edge * 100, opp.bet_usd, opp.poly_fill_price)
                        audit_log.log_decision(
                            opp,
                            "SUBMITTED",
                            cycle=self.cycle,
                            meta={"order_id": order.order_id},
                        )
                    else:
                        reject_meta = {}
                        last_error = getattr(self.executor, "last_error", "")
                        if last_error:
                            reject_meta["reject_reason"] = last_error
                        cycle_stats["rejected"] += 1
                        audit_log.log_decision(
                            opp,
                            "REJECTED",
                            cycle=self.cycle,
                            meta=reject_meta or None,
                        )
                else:
                    cycle_stats["dry_run"] += 1
                    logger.info("DRY RUN — would trade: %s edge=%.1f%% $%.2f",
                                matched.poly_market.event_title, opp.adjusted_edge * 100, opp.bet_usd)
                    audit_log.log_decision(opp, "DRY_RUN", cycle=self.cycle)

        cycle_stats["status"] = "completed"
        self.last_fast_cycle = cycle_stats

    async def run(self):
        """Main loop with fast/slow cycle separation."""
        logger.info("PolyEdge Bot starting")
        stop_event = asyncio.Event()
        self._install_signal_handlers(stop_event)
        self._write_health("starting")
        self._init_poly_client()

        try:
            while not stop_event.is_set():
                self.cycle += 1

                if KILLSWITCH_PATH.exists():
                    logger.warning("KILLSWITCH active — paused")
                    self._write_health("paused")
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=10)
                    except asyncio.TimeoutError:
                        pass
                    continue

                self.cfg = EdgeConfig.from_env()
                slow_cycle = max(1, int(self.cfg.slow_cycle_multiplier))
                sleep_sec = max(1, int(self.cfg.poll_interval_sec))
                slow_cycle_due = (self.cycle - 1) % slow_cycle == 0

                if self.cfg.simulation_mode and self.executor is not None:
                    logger.info("Simulation mode toggled on — disabling live executor")
                    self.poly_client = None
                    self.executor = None
                    self.order_mgr = None

                # Retry client init periodically so transient startup failures can recover.
                if (not self.cfg.simulation_mode and self.executor is None and self.cfg.polymarket_key_id and
                        slow_cycle_due):
                    self._init_poly_client()

                try:
                    if slow_cycle_due:
                        await self._slow_cycle()
                        await self._fast_cycle()
                    self._write_health("running")
                except Exception as e:
                    logger.error("Cycle error: %s", e, exc_info=True)
                    self.breaker.record_api_error()
                    self._write_health("degraded", str(e))

                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=sleep_sec)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            self._write_health("stopped", "cancelled")
            raise

        self._write_health("stopped")
        logger.info("PolyEdge Bot stopped")

def main():
    load_dotenv(str(CONFIG_ENV_PATH))
    bot = PolyEdgeBot()
    asyncio.run(bot.run())

if __name__ == "__main__":
    main()
