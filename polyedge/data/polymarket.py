import logging
import re

import aiohttp

from polyedge.models import BookLevel, OrderBook, PolyMarket

logger = logging.getLogger(__name__)

POLY_CLOB_BASE = "https://clob.polymarket.com"
POLY_GAMMA_BASE = "https://gamma-api.polymarket.com"
_HEADERS = {"User-Agent": "PolyEdge/1.0"}

SPORT_TAG_SLUGS = {
    "basketball_nba": "nba",
    "americanfootball_nfl": "nfl",
    "baseball_mlb": "mlb",
    "icehockey_nhl": "nhl",
    "mma_mixed_martial_arts": "ufc",
}

_SPREAD_PATTERN = re.compile(r"[+-]\s*\d+(?:\.\d+)?")
_NUMERIC_PARENS_PATTERN = re.compile(r"\(\s*[+-]?\s*\d+(?:\.\d+)?\s*\)")
_MONEYLINE_HINTS = (
    "moneyline",
    "match winner",
    "to win",
    "winner",
)
_SPREAD_HINTS = (
    "spread",
    "handicap",
    "run line",
    "puck line",
)
_TOTAL_HINTS = (
    "total",
    "o/u",
    "over",
    "under",
)

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


def _parse_book_levels(rows: list[dict]) -> list[BookLevel]:
    levels: list[BookLevel] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            price = float(row.get("price"))
            size = float(row.get("size"))
        except (TypeError, ValueError):
            continue
        if price <= 0 or size <= 0:
            continue
        levels.append(BookLevel(price=price, size=size))
    return levels


def _parse_outcomes_tokens(market: dict) -> tuple[list, list] | None:
    """Parse outcomes and tokens from a market dict, handling JSON strings."""
    import json as _json

    outcomes = market.get("outcomes", "")
    if isinstance(outcomes, str):
        try:
            outcomes = _json.loads(outcomes)
        except Exception:
            return None

    tokens = market.get("clobTokenIds", "")
    if isinstance(tokens, str):
        try:
            tokens = _json.loads(tokens)
        except Exception:
            return None

    if not isinstance(outcomes, (list, tuple)) or not isinstance(tokens, (list, tuple)):
        return None
    if len(outcomes) != 2 or len(tokens) != 2:
        return None
    if any(not str(t).strip() for t in tokens):
        return None
    return outcomes, tokens


def _looks_like_total(text: str) -> bool:
    t = str(text).strip().lower()
    if not t:
        return False
    if " o/u" in t or "o/u " in t:
        return True
    return any(kw in t for kw in _TOTAL_HINTS)


def _looks_like_spread(text: str) -> bool:
    t = str(text).strip().lower()
    if not t:
        return False
    if _SPREAD_PATTERN.search(t):
        return True
    if _NUMERIC_PARENS_PATTERN.search(t):
        return True
    return any(kw in t for kw in _SPREAD_HINTS)


def _classify_market_type(market: dict, outcomes: list[str]) -> str | None:
    outcomes_lower = [str(o).strip().lower() for o in outcomes]
    if any(o in {"yes", "no"} for o in outcomes_lower):
        return None

    question = str(market.get("question") or "").strip().lower()
    market_title = str(market.get("title") or "").strip().lower()
    all_text = [question, market_title, *outcomes_lower]

    if any(_looks_like_total(text) for text in all_text):
        return None
    if any(_looks_like_spread(text) for text in all_text):
        return "spread"
    if question and any(hint in question for hint in _MONEYLINE_HINTS):
        return "moneyline"
    if market_title and any(hint in market_title for hint in _MONEYLINE_HINTS):
        return "moneyline"
    # If both outcomes look like team names and no spread/total signal was found,
    # treat as moneyline-style market.
    return "moneyline"


def _extract_tradeable_markets(event: dict, sport_tag: str = "") -> list[PolyMarket]:
    """Extract tradeable game markets (moneyline + spread) from a Gamma event."""
    results = []
    for market in event.get("markets", []):
        if market.get("closed") or not market.get("active"):
            continue

        parsed = _parse_outcomes_tokens(market)
        if not parsed:
            continue
        outcomes, tokens = parsed

        condition_id = str(market.get("conditionId", "")).strip()
        if not condition_id:
            continue
        market_type = _classify_market_type(market, outcomes)
        if market_type is None:
            continue

        results.append(
            PolyMarket(
                event_title=event.get("title", ""),
                condition_id=condition_id,
                outcome_a=str(outcomes[0]).strip(),
                outcome_b=str(outcomes[1]).strip(),
                token_id_a=str(tokens[0]).strip(),
                token_id_b=str(tokens[1]).strip(),
                market_type=market_type,
                sport_tag=sport_tag,
                question=str(market.get("question") or ""),
                start_iso=str(event.get("startDate") or event.get("startTime") or ""),
            )
        )
    return results

async def fetch_sports_markets(sports: list[str]) -> list[PolyMarket]:
    """Fetch game markets (moneyline + spread) from Polymarket Gamma API."""
    seen_slugs = set()
    slugs = []
    for s in sports:
        slug = SPORT_TAG_SLUGS.get(s)
        if slug and slug not in seen_slugs:
            slugs.append(slug)
            seen_slugs.add(slug)
    markets = []
    seen_conditions: set[str] = set()
    async with aiohttp.ClientSession(headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=15)) as session:
        for slug in slugs:
            offset = 0
            while offset < 1000:
                params = {"tag_slug": slug, "active": "true", "closed": "false",
                          "limit": 50, "offset": offset}
                try:
                    async with session.get(f"{POLY_GAMMA_BASE}/events", params=params) as resp:
                        if resp.status != 200:
                            logger.warning("Gamma API returned %d for slug=%s", resp.status, slug)
                            break
                        events = await resp.json()
                        if not isinstance(events, list):
                            logger.warning("Gamma API returned non-list payload for slug=%s", slug)
                            break
                        if not events:
                            break
                        for ev in events:
                            for pm in _extract_tradeable_markets(ev, sport_tag=slug):
                                if pm.condition_id in seen_conditions:
                                    continue
                                seen_conditions.add(pm.condition_id)
                                markets.append(pm)
                        offset += 50
                except Exception as e:
                    logger.warning("Gamma API fetch failed for slug=%s: %s", slug, e)
                    break
    return markets

async def fetch_order_book(token_id: str) -> OrderBook:
    """Fetch order book from Polymarket CLOB API."""
    async with aiohttp.ClientSession(headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=10)) as session:
        url = f"{POLY_CLOB_BASE}/book"
        async with session.get(url, params={"token_id": token_id}) as resp:
            if resp.status != 200:
                raise RuntimeError(f"CLOB book API returned {resp.status} for {token_id}")
            data = await resp.json()
            if not isinstance(data, dict):
                raise RuntimeError(f"CLOB book API returned non-object payload for {token_id}")
            asks = _parse_book_levels(data.get("asks", []))
            bids = _parse_book_levels(data.get("bids", []))
            asks.sort(key=lambda x: x.price)
            bids.sort(key=lambda x: -x.price)
            return OrderBook(token_id=token_id, outcome_name="", asks=asks, bids=bids)
