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
