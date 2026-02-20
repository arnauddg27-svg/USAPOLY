import asyncio
import time
import logging
import os
from polyedge.config import EdgeConfig
from polyedge.data.odds_api import fetch_all_odds
from polyedge.data.polymarket import fetch_sports_markets, fetch_order_book
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
from polyedge.monitoring import audit_log
from polyedge.models import BookLine
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
        self.poly_client = None
        self.executor = None
        self.order_mgr = None
        self.cycle = 0
        self.trades_today = 0

    def _init_poly_client(self):
        """Initialize authenticated Polymarket CLOB client.
        TODO: Wire up py_clob_client with self.cfg credentials.
        """
        pass

    async def _slow_cycle(self):
        """Refresh odds, markets, and matching (every ~2 min)."""
        logger.info("SLOW CYCLE: refreshing odds & markets")

        all_odds = await fetch_all_odds(self.cfg.sports, self.cfg.odds_api_key)
        if all_odds:
            self.odds_cache.set("all_odds", all_odds)
            self.breaker.record_odds_fetch()
            logger.info("Fetched odds for %d games", len(all_odds))
        else:
            self.breaker.record_api_error()

        poly_markets = await fetch_sports_markets(self.cfg.sports)
        if poly_markets:
            self.market_cache.set("poly_markets", poly_markets)
            logger.info("Fetched %d Polymarket markets", len(poly_markets))

        all_odds = self.odds_cache.get("all_odds") or []
        poly_markets = self.market_cache.get("poly_markets") or []
        matches = match_events(all_odds, poly_markets)
        self.match_cache.set("matches", matches)
        logger.info("Matched %d events", len(matches))

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

            try:
                book_a = await fetch_order_book(matched.poly_market.token_id_a)
                book_b = await fetch_order_book(matched.poly_market.token_id_b)
            except Exception as e:
                self.breaker.record_api_error()
                continue
            self.breaker.record_api_success()

            try:
                commence = datetime.fromisoformat(matched.all_odds.commence_time.replace("Z", "+00:00"))
                hours_until = (commence - datetime.now(timezone.utc)).total_seconds() / 3600
            except Exception:
                hours_until = 24.0

            opportunities = detect_edge(matched, agg, book_a, book_b, self.cfg, hours_until)

            for opp in opportunities:
                if not self.exposure.can_trade(
                    opp.matched_event.sport, cid, 10.0,
                    bankroll=1000,  # TODO: get from poly_client
                    max_per_event=self.cfg.max_per_event_pct,
                    max_per_sport=self.cfg.max_per_sport_pct,
                    max_total=self.cfg.max_total_exposure_pct,
                    daily_loss_limit=self.cfg.daily_loss_limit_pct,
                ):
                    continue

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

                if self.executor:
                    order = self.executor.place_order(opp, self.cfg)
                    if order:
                        self.order_mgr.track(order)
                        self.exposure.record_trade(opp.matched_event.sport, cid, opp.bet_usd)
                        self.trades_today += 1
                        logger.info("TRADE PLACED: %s %s edge=%.1f%% $%.2f @ %.4f",
                                    matched.poly_market.event_title,
                                    "YES" if opp.buy_outcome == "a" else "NO",
                                    opp.adjusted_edge * 100, opp.bet_usd, opp.poly_fill_price)
                        audit_log.log_decision(opp, "PLACED", cycle=self.cycle)
                    else:
                        audit_log.log_decision(opp, "REJECTED", cycle=self.cycle)
                else:
                    logger.info("DRY RUN — would trade: %s edge=%.1f%% $%.2f",
                                matched.poly_market.event_title, opp.adjusted_edge * 100, opp.bet_usd)
                    audit_log.log_decision(opp, "DRY_RUN", cycle=self.cycle)

        if self.order_mgr:
            self.order_mgr.check_expiry()

    async def run(self):
        """Main loop with fast/slow cycle separation."""
        logger.info("PolyEdge Bot starting")

        while True:
            self.cycle += 1

            if os.path.exists(KILLSWITCH_PATH):
                logger.warning("KILLSWITCH active — paused")
                await asyncio.sleep(10)
                continue

            self.cfg = EdgeConfig.from_env()

            try:
                if self.cycle % self.cfg.slow_cycle_multiplier == 1:
                    await self._slow_cycle()
                await self._fast_cycle()
            except Exception as e:
                logger.error("Cycle error: %s", e, exc_info=True)
                self.breaker.record_api_error()

            await asyncio.sleep(self.cfg.poll_interval_sec)

def main():
    bot = PolyEdgeBot()
    asyncio.run(bot.run())

if __name__ == "__main__":
    main()
