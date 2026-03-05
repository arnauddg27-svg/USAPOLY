import json
import logging
from datetime import datetime, timezone
from polyedge.models import EdgeOpportunity
from polyedge.paths import AUDIT_DIR

logger = logging.getLogger(__name__)

def log_decision(
    opp: EdgeOpportunity,
    action: str,
    order_result: dict | None = None,
    cycle: int = 0,
    meta: dict | None = None,
) -> None:
    try:
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cycle": cycle,
            "event": opp.matched_event.poly_market.event_title,
            "event_start": opp.matched_event.all_odds.commence_time,
            "poly_event_start": opp.matched_event.poly_market.start_iso,
            "sport": opp.matched_event.sport,
            "condition_id": opp.matched_event.poly_market.condition_id,
            "market_question": opp.matched_event.poly_market.question,
            "market_type": opp.matched_event.poly_market.market_type,
            "outcome_a": opp.matched_event.poly_market.outcome_a,
            "outcome_b": opp.matched_event.poly_market.outcome_b,
            "buy_outcome": opp.buy_outcome,
            "agg_prob_a": round(opp.aggregated.prob_a, 4),
            "agg_prob_b": round(opp.aggregated.prob_b, 4),
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
        if meta:
            record.update(meta)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = AUDIT_DIR / f"decisions_{date_str}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:
        logger.error("Audit log write failed: %s", e)
