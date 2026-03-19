from polyedge.models import (
    MatchedEvent, AggregatedProb, OrderBook, EdgeOpportunity,
    ConfidenceTier, EdgeSource,
)
from polyedge.config import EdgeConfig
from polyedge.data.polymarket import compute_avg_fill_price


def check_gates(
    adjusted_edge: float, books_used: int, depth: float,
    fill_price: float, book: OrderBook, hours_until: float, cfg: EdgeConfig,
) -> dict:
    """Evaluate all safety gates and return per-gate pass/fail details."""
    mid = book.mid
    slippage = abs(fill_price - mid) if mid > 0 else 0.0
    return {
        "edge": {"passed": adjusted_edge >= cfg.min_edge, "value": adjusted_edge, "threshold": cfg.min_edge},
        "edge_ceiling": {"passed": adjusted_edge <= cfg.max_edge, "value": adjusted_edge, "threshold": cfg.max_edge},
        "books": {"passed": books_used >= cfg.min_books, "value": books_used, "threshold": cfg.min_books},
        "liquidity": {"passed": depth >= cfg.target_shares, "value": depth, "threshold": cfg.target_shares},
        "slippage": {"passed": slippage <= cfg.max_slippage, "value": slippage, "threshold": cfg.max_slippage},
        "spread": {"passed": book.spread <= cfg.max_spread, "value": book.spread, "threshold": cfg.max_spread},
        "time": {"passed": hours_until >= cfg.min_hours_before_event, "value": hours_until, "threshold": cfg.min_hours_before_event},
        "time_ceiling": {"passed": cfg.max_hours_before_event <= 0 or hours_until <= cfg.max_hours_before_event, "value": hours_until, "threshold": cfg.max_hours_before_event},
    }


def _assess_confidence(edge: float, depth: float, target: float) -> ConfidenceTier:
    """Map edge magnitude and liquidity depth to a confidence tier."""
    if depth >= target and edge >= 0.10:
        return ConfidenceTier.HIGH
    if depth >= target * 0.5 and edge >= 0.05:
        return ConfidenceTier.MEDIUM
    return ConfidenceTier.LOW


def _assess_source(fill: float, true_prob: float, depth: float, target: float) -> EdgeSource:
    """Classify where the edge is likely coming from."""
    if depth < target * 0.5:
        return EdgeSource.POLY_THIN_BOOK
    if fill < true_prob * 0.75:
        return EdgeSource.POLY_STALE
    return EdgeSource.CONSENSUS


def _build_opportunity(
    matched: MatchedEvent,
    agg: AggregatedProb,
    side: str,
    token_id: str,
    true_prob: float,
    book: OrderBook,
    fill_price: float,
    filled: float,
    raw_edge: float,
    adjusted_edge: float,
    target: float,
    gates: dict,
) -> EdgeOpportunity:
    return EdgeOpportunity(
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


def detect_edge(
    matched: MatchedEvent, agg: AggregatedProb,
    book_a: OrderBook, book_b: OrderBook, cfg: EdgeConfig,
    hours_until: float = 24.0,
    include_rejected: bool = False,
) -> list[EdgeOpportunity] | tuple[list[EdgeOpportunity], list[EdgeOpportunity]]:
    """Core decision engine: detect profitable edges on both sides of a market.

    For each side (a and b), computes the average fill price by walking the
    order book, applies fees and safety haircut, checks all gates, and builds
    an EdgeOpportunity if everything passes.

    Args:
        matched: The matched event linking sportsbook odds to a Polymarket market.
        agg: Aggregated true probability from multiple sportsbooks.
        book_a: Polymarket order book for outcome A.
        book_b: Polymarket order book for outcome B.
        cfg: Edge configuration parameters.
        hours_until: Hours remaining before the event starts.

    Returns:
        - include_rejected=False (default): list of profitable opportunities.
        - include_rejected=True: (opportunities, rejected_out_of_edge_range) tuple.
    """
    opportunities = []
    rejected_out_of_edge_range = []
    target = cfg.target_shares
    market_type = getattr(matched.poly_market, "market_type", "moneyline")
    favorites_only = (
        market_type in ("moneyline", "spread")
        and cfg.moneyline_favorites_only
    )
    favorite_side = "a" if agg.prob_a >= agg.prob_b else "b"

    for side, true_prob, book, token_id in [
        ("a", agg.prob_a, book_a, matched.poly_market.token_id_a),
        ("b", agg.prob_b, book_b, matched.poly_market.token_id_b),
    ]:
        if favorites_only and side != favorite_side:
            continue

        fill_price, filled = compute_avg_fill_price(book.asks, target)
        if filled <= 0:
            # Empty order book — build a minimal rejected entry for visibility.
            if include_rejected:
                _gates = {"empty_book": {"passed": False, "value": 0, "threshold": 1}}
                rejected_out_of_edge_range.append(
                    _build_opportunity(
                        matched=matched, agg=agg, side=side, token_id=token_id,
                        true_prob=true_prob, book=book, fill_price=0.0, filled=0.0,
                        raw_edge=0.0, adjusted_edge=0.0, target=target, gates=_gates,
                    )
                )
            continue
        # In strict favorites-only mode, only allow buying the market-favorite side
        # at favorite pricing (>= 0.50).
        if favorites_only and fill_price < 0.50:
            continue
        # Cap filter: never buy at or above configured max entry price.
        hard_buy_cap = float(getattr(cfg, "max_fill_price", 0.91))
        if fill_price >= hard_buy_cap:
            if include_rejected:
                _gates = {"fill_price_cap": {"passed": False, "value": fill_price, "threshold": hard_buy_cap}}
                effective_prob = fill_price + cfg.fee_rate
                raw_edge = true_prob - effective_prob
                adjusted_edge = raw_edge - cfg.safety_haircut
                rejected_out_of_edge_range.append(
                    _build_opportunity(
                        matched=matched, agg=agg, side=side, token_id=token_id,
                        true_prob=true_prob, book=book, fill_price=fill_price,
                        filled=filled, raw_edge=raw_edge, adjusted_edge=adjusted_edge,
                        target=target, gates=_gates,
                    )
                )
            continue

        # Effective cost includes any fee on top of the fill price
        effective_prob = fill_price + cfg.fee_rate
        raw_edge = true_prob - effective_prob
        adjusted_edge = raw_edge - cfg.safety_haircut
        gates = check_gates(
            adjusted_edge, agg.books_used, filled,
            fill_price, book, hours_until, cfg,
        )

        # Rejected: outside the configured edge band or gates failed.
        if adjusted_edge < cfg.min_edge or adjusted_edge > cfg.max_edge:
            if include_rejected:
                rejected_out_of_edge_range.append(
                    _build_opportunity(
                        matched=matched, agg=agg, side=side, token_id=token_id,
                        true_prob=true_prob, book=book, fill_price=fill_price,
                        filled=filled, raw_edge=raw_edge, adjusted_edge=adjusted_edge,
                        target=target, gates=gates,
                    )
                )
            continue

        if not all(g["passed"] for g in gates.values()):
            if include_rejected:
                rejected_out_of_edge_range.append(
                    _build_opportunity(
                        matched=matched, agg=agg, side=side, token_id=token_id,
                        true_prob=true_prob, book=book, fill_price=fill_price,
                        filled=filled, raw_edge=raw_edge, adjusted_edge=adjusted_edge,
                        target=target, gates=gates,
                    )
                )
            continue

        opp = _build_opportunity(
            matched=matched,
            agg=agg,
            side=side,
            token_id=token_id,
            true_prob=true_prob,
            book=book,
            fill_price=fill_price,
            filled=filled,
            raw_edge=raw_edge,
            adjusted_edge=adjusted_edge,
            target=target,
            gates=gates,
        )
        opportunities.append(opp)

    if include_rejected:
        return opportunities, rejected_out_of_edge_range
    return opportunities
