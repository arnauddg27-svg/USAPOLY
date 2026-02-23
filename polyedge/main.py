import asyncio
import json
import logging
import signal
from collections import Counter
from dotenv import load_dotenv
from polyedge.config import EdgeConfig
from polyedge.data.odds_api import fetch_all_odds
from polyedge.data.polymarket import fetch_sports_markets, fetch_order_book
from polyedge.data.cache import TTLCache
from polyedge.pipeline.devig import devig
from polyedge.pipeline.aggregator import aggregate_probs
from polyedge.pipeline.matcher import match_events, orient_book_outcomes
from polyedge.pipeline.edge_detector import detect_edge
from polyedge.execution.sizing import compute_bet_size
from polyedge.execution.executor import EdgeExecutor
from polyedge.execution.redeemer import AutoRedeemer
from polyedge.execution.order_manager import OrderManager
from polyedge.risk.limits import ExposureTracker
from polyedge.risk.circuit_breaker import CircuitBreaker
from polyedge.monitoring import audit_log
from polyedge.models import BookLine
from polyedge.simulation import PaperSimulator
from polyedge.paths import KILLSWITCH_PATH, CONFIG_ENV_PATH, HEALTH_PATH
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("polyedge")

DRY_RUN_BANKROLL_USD = 1000.0


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def summarize_exchange_open_orders(raw_orders) -> tuple[int, float]:
    """Return (open_order_count, open_order_notional_usd) from CLOB get_orders payload."""
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
        self.exposure = ExposureTracker()
        self.breaker = CircuitBreaker()
        self.poly_client = None
        self.executor = None
        self.order_mgr = None
        self.redeemer = None
        self.cycle = 0
        self.trades_today = 0
        self.claims_today = 0
        self.claimed_usdc_today = 0.0
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
        self.coverage: dict[str, dict[str, int]] = {
            "odds_games_by_sport": {},
            "matches_by_sport": {},
            "aggregated_by_sport": {},
            "aggregated_by_market_type": {},
        }

    def _is_live_mode(self) -> bool:
        return (
            self.executor is not None
            and self.cfg.trading_enabled
            and not self.cfg.simulation_mode
        )

    def _init_poly_client(self):
        """Initialize authenticated Polymarket CLOB client."""
        if self.cfg.simulation_mode:
            logger.info("Simulation mode enabled — live client disabled")
            self.poly_client = None
            self.executor = None
            self.order_mgr = None
            self.redeemer = None
            return
        if not self.cfg.poly_private_key:
            logger.warning("No POLY_PRIVATE_KEY set — running in dry-run mode")
            return

        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.constants import POLYGON

            funder = self.cfg.poly_funder_address or None
            self.poly_client = ClobClient(
                "https://clob.polymarket.com",
                key=self.cfg.poly_private_key,
                chain_id=POLYGON,
                signature_type=self.cfg.poly_signature_type,
                funder=funder,
            )
            self.poly_client.set_api_creds(
                self.poly_client.create_or_derive_api_creds()
            )
            self.executor = EdgeExecutor(self.poly_client)
            self.order_mgr = OrderManager(self.poly_client)
            holder = self.cfg.poly_funder_address or ""
            self.redeemer = AutoRedeemer(
                private_key=self.cfg.poly_private_key,
                holder_address=holder,
                rpc_url=self.cfg.polygon_rpc,
                usdc_address=self.cfg.usdc_address,
                claim_cooldown_sec=self.cfg.claim_cooldown_sec,
            )
            if self.cfg.auto_claim_enabled:
                if self.redeemer.enabled:
                    logger.info("Auto-claim enabled (holder=%s)", self.redeemer.holder_address)
                else:
                    logger.warning("Auto-claim disabled: %s", self.redeemer.disable_reason)
            else:
                logger.info("Auto-claim disabled by config")
            logger.info("CLOB client initialized (funder=%s)", funder)
        except Exception as e:
            logger.error("CLOB client init failed: %s — continuing in dry-run mode", e)
            self.poly_client = None
            self.executor = None
            self.order_mgr = None
            self.redeemer = None

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
                    raw_orders = self.poly_client.get_orders()
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
                "claims_today": int(self.claims_today),
                "claimed_usdc_today": round(float(self.claimed_usdc_today), 2),
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
                payload["claims_today"] = 0
                payload["claimed_usdc_today"] = 0.0
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

    def _claim_winning_positions(self):
        if not self._is_live_mode():
            return
        if not self.cfg.auto_claim_enabled:
            return
        if self.redeemer is None:
            return
        if not self.redeemer.enabled:
            return

        max_claims = max(1, int(self.cfg.claim_max_per_cycle))
        attempts = 0
        positions = self.redeemer.fetch_redeemable_positions(limit=500, max_pages=3)
        for pos in positions:
            if attempts >= max_claims:
                break
            token_id = str(pos.get("asset") or "")
            if not token_id:
                continue
            if self.redeemer.in_cooldown(token_id):
                continue
            attempts += 1
            result = self.redeemer.redeem_position(pos)
            if result.get("ok"):
                payout = _to_float(result.get("payout_usdc")) or 0.0
                self.claimed_usdc_today += payout
                self.claims_today += 1
                self.redeemer.set_cooldown(token_id, max(3600, int(self.cfg.claim_cooldown_sec)))
                logger.info(
                    "AUTO-CLAIM SUCCESS token=%s condition=%s payout=$%.2f tx=%s",
                    token_id,
                    str(pos.get("conditionId") or "")[:18],
                    payout,
                    result.get("tx_hash", ""),
                )
                break

            err = str(result.get("error") or "")
            lower_err = err.lower()
            cooldown = int(self.cfg.claim_cooldown_sec)
            if "rate limit" in lower_err or "429" in lower_err or "too many" in lower_err:
                cooldown = 120
            elif "condition_not_resolved" in lower_err:
                cooldown = max(cooldown, 3600)
            elif "outcome_lost" in lower_err:
                cooldown = 86400 * 7
            elif "zero_onchain_balance" in lower_err:
                cooldown = 86400
            self.redeemer.set_cooldown(token_id, cooldown)
            logger.warning("AUTO-CLAIM FAILED token=%s error=%s", token_id, err)

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

    def _get_bankroll(self) -> float | None:
        """Get USDC balance from CLOB client. Returns None on failure."""
        if not self.poly_client:
            return None
        try:
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
            resp = self.poly_client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            raw = float(resp.get("balance", 0)) if isinstance(resp, dict) else float(resp)
            return raw / 1_000_000.0  # USDC has 6 decimals on Polygon
        except Exception as e:
            logger.error("Balance fetch failed: %s", e)
            return None

    async def _slow_cycle(self):
        """Refresh odds, markets, and matching (every ~2 min)."""
        logger.info("SLOW CYCLE: refreshing odds & markets")

        all_odds = await fetch_all_odds(self.cfg.sports, self.cfg.odds_api_key)
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
            logger.info("Fetched %d Polymarket markets", len(poly_markets))
        else:
            logger.warning("Polymarket fetch returned empty")
            self.breaker.record_api_error()

        all_odds = self.odds_cache.get("all_odds") or []
        poly_markets = self.market_cache.get("poly_markets") or []
        matches = match_events(all_odds, poly_markets)
        self.match_cache.set("matches", matches)
        logger.info("Matched %d events", len(matches))
        odds_games_by_sport = Counter(g.sport for g in all_odds)
        matches_by_sport = Counter(m.sport for m in matches)

        def _build_aggregates(min_books: int):
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
                agg = aggregate_probs(lines, min_books=min_books)
                if agg:
                    cache[m.poly_market.condition_id] = agg
                    by_sport[m.sport] += 1
                    by_market_type[market_type] += 1
            return cache, by_sport, by_market_type

        required_books = max(1, int(self.cfg.min_books))
        agg_cache, aggregated_by_sport, aggregated_by_market_type = _build_aggregates(
            required_books
        )
        # Adaptive fallback: if strict min-book settings yield no markets,
        # temporarily relax to keep the engine producing decisions.
        if not agg_cache and matches and required_books > 2:
            fallback_books = 2
            fallback_cache, fallback_by_sport, fallback_by_market_type = _build_aggregates(
                fallback_books
            )
            if fallback_cache:
                logger.warning(
                    "No events met MIN_BOOKS=%d; temporarily using MIN_BOOKS=%d for this cycle",
                    required_books,
                    fallback_books,
                )
                self.cfg.min_books = fallback_books
                agg_cache = fallback_cache
                aggregated_by_sport = fallback_by_sport
                aggregated_by_market_type = fallback_by_market_type
        self.odds_cache.set("aggregated", agg_cache)
        self.coverage = {
            "odds_games_by_sport": dict(odds_games_by_sport),
            "matches_by_sport": dict(matches_by_sport),
            "aggregated_by_sport": dict(aggregated_by_sport),
            "aggregated_by_market_type": dict(aggregated_by_market_type),
        }
        logger.info("Aggregated probs for %d events", len(agg_cache))

    async def _fast_cycle(self):
        """Check edges and execute (every 10s)."""
        close_before_event_sec = max(0, int(self.cfg.close_orders_before_event_sec))
        # Always process TTL expiry, even if trading is temporarily blocked.
        if self.order_mgr:
            cancelled = self.order_mgr.check_expiry(
                close_before_event_sec=close_before_event_sec
            )
            for order in cancelled:
                if order.amount_usd > 0 and order.sport:
                    self.exposure.record_exit(order.sport, order.condition_id, order.amount_usd)

        matches = self.match_cache.get("matches")
        if matches is None:
            return
        agg_cache = self.odds_cache.get("aggregated") or {}

        if self.breaker.is_tripped():
            logger.warning("Circuit breaker active: %s", self.breaker.trip_reason)
            return

        # Fetch bankroll once per cycle (not per opportunity).
        if self.cfg.simulation_mode:
            bankroll = self.simulator.current_bankroll
            if bankroll <= 0:
                logger.warning("Simulation bankroll is depleted — skipping fast cycle")
                return
        elif self._is_live_mode():
            bankroll = self._get_bankroll()
            if bankroll is None:
                logger.warning("Bankroll unavailable — skipping fast cycle")
                return
            self.live_wallet_balance_usd = float(bankroll)
            if self.live_wallet_start_usd is None:
                self.live_wallet_start_usd = float(bankroll)
            if bankroll <= 0:
                logger.debug("Bankroll is $0 — no funds to trade")
                return
        else:
            bankroll = DRY_RUN_BANKROLL_USD

        for matched in matches:
            cid = matched.poly_market.condition_id
            agg = agg_cache.get(cid)
            if not agg:
                continue

            try:
                book_a = await fetch_order_book(matched.poly_market.token_id_a)
                book_b = await fetch_order_book(matched.poly_market.token_id_b)
            except Exception as e:
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
                continue
            if event_start_ts is not None and close_before_event_sec > 0:
                if now_ts >= event_start_ts - close_before_event_sec:
                    continue

            opportunities = detect_edge(matched, agg, book_a, book_b, self.cfg, hours_until)

            for opp in opportunities:
                locked_side = self.condition_side_lock.get(cid)
                if locked_side and locked_side != opp.buy_outcome:
                    audit_log.log_decision(
                        opp,
                        "REJECTED",
                        cycle=self.cycle,
                        meta={"reject_reason": f"opposite_side_locked:{locked_side}"},
                    )
                    continue

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

                # Check exposure with actual bet size (not placeholder)
                if not self.exposure.can_trade(
                    opp.matched_event.sport, cid, opp.bet_usd,
                    bankroll=bankroll,
                    max_per_event=self.cfg.max_per_event_pct,
                    max_per_sport=self.cfg.max_per_sport_pct,
                    max_total=self.cfg.max_total_exposure_pct,
                    daily_loss_limit=self.cfg.daily_loss_limit_pct,
                ):
                    continue

                if self.cfg.simulation_mode:
                    sim = self.simulator.record_bet(opp, cycle=self.cycle)
                    self.exposure.record_trade(opp.matched_event.sport, cid, opp.bet_usd)
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
                        if not self.cfg.no_resting_orders and self.order_mgr is not None:
                            self.order_mgr.track(order)
                        # Exposure limits should apply to any successfully submitted live trade,
                        # including no-resting (FOK/IOC-like) executions.
                        self.exposure.record_trade(opp.matched_event.sport, cid, opp.bet_usd)
                        self.condition_side_lock[cid] = opp.buy_outcome
                        self.trades_today += 1
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
                        audit_log.log_decision(
                            opp,
                            "REJECTED",
                            cycle=self.cycle,
                            meta=reject_meta or None,
                        )
                else:
                    logger.info("DRY RUN — would trade: %s edge=%.1f%% $%.2f",
                                matched.poly_market.event_title, opp.adjusted_edge * 100, opp.bet_usd)
                    audit_log.log_decision(opp, "DRY_RUN", cycle=self.cycle)

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
                    self.redeemer = None

                # Retry CLOB init periodically so transient startup failures can recover.
                if (not self.cfg.simulation_mode and self.executor is None and self.cfg.poly_private_key and
                        slow_cycle_due):
                    self._init_poly_client()

                try:
                    if slow_cycle_due:
                        await self._slow_cycle()
                        self._claim_winning_positions()
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
