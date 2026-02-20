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
