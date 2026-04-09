from __future__ import annotations

import logging
import re

import aiohttp

from polyedge.models import BookLevel, OrderBook, PolyMarket


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Polymarket US API
# ---------------------------------------------------------------------------

POLY_US_BASE = "https://gateway.polymarket.us"
_HEADERS = {"User-Agent": "PolyEdge/1.0"}

# Map Odds API sport keys → Polymarket US league slugs.
# These slugs are used with GET /v2/leagues/{slug}/events.
US_LEAGUE_SLUGS: dict[str, str] = {
    "basketball_nba": "nba",
    "icehockey_nhl": "nhl",
    "baseball_mlb": "mlb",
    "americanfootball_nfl": "nfl",
    "basketball_ncaab": "cbb",
    "mma_mixed_martial_arts": "ufc",
    "boxing_boxing": "boxing",
    # Soccer
    "soccer_epl": "epl",
    "soccer_uefa_champs_league": "ucl",
    "soccer_usa_mls": "mls",
    "soccer_germany_bundesliga": "bun",
    "soccer_spain_la_liga": "lal",
    "soccer_italy_serie_a": "sea",
    # Tennis (specific tournaments map to same league)
    "tennis_atp_miami_open": "atp",
    "tennis_wta_miami_open": "wta",
}

# Fallback prefix matching for keys not listed explicitly above.
_SPORT_PREFIX_TO_LEAGUE: list[tuple[str, str]] = [
    ("tennis_atp", "atp"),
    ("tennis_wta", "wta"),
]


def _sport_to_us_league(sport_key: str) -> str:
    """Resolve an Odds API sport key to a Polymarket US league slug."""
    key = str(sport_key or "").strip().lower()
    if not key:
        return ""
    if key in US_LEAGUE_SLUGS:
        return US_LEAGUE_SLUGS[key]
    for prefix, league in _SPORT_PREFIX_TO_LEAGUE:
        if key.startswith(prefix):
            return league
    return ""


# Backward-compatible alias used by the matcher for cross-sport filtering.
sport_to_tag_slug = _sport_to_us_league


# ---------------------------------------------------------------------------
# Utility kept from original code (used by edge_detector / sizing)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Parse Polymarket US book levels
# ---------------------------------------------------------------------------

def _parse_us_book_levels(rows: list[dict]) -> list[BookLevel]:
    """Parse Polymarket US order book entries.

    US format: {"px": {"value": "0.680", "currency": "USD"}, "qty": "108633.000"}
    """
    levels: list[BookLevel] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            px = row.get("px", {})
            price = float(px.get("value")) if isinstance(px, dict) else float(px)
            size = float(row.get("qty", 0))
        except (TypeError, ValueError):
            continue
        if price <= 0 or size <= 0:
            continue
        levels.append(BookLevel(price=price, size=size))
    return levels


# ---------------------------------------------------------------------------
# Market type classification (kept for safety filtering)
# ---------------------------------------------------------------------------

_ALLOWED_SPORTS_MARKET_TYPES = {"moneyline", "spreads"}
_DRAWABLE_SPORTS_MARKET_TYPE = "drawable_outcome"

_NON_MATCH_PROP_HINTS = (
    "set winner", "total sets", "set handicap", "set spread", "set total",
    "period winner", "total periods", "halftime", "half-time",
    "first-half", "second-half", "regulation winner", "in regulation",
    "after 60", "60-minute", "60 minute", "60 min", "series winner",
)
_SEGMENT_MARKET_RE = re.compile(
    r"\b(?:\d+(?:st|nd|rd|th)|first|second|third|fourth|fifth)\s+"
    r"(?:set|period|quarter|half|inning|map|game)\b"
)
_SEGMENT_SHORT_RE = re.compile(
    r"\b(?:1h|2h|h1|h2|1q|2q|3q|4q|q1|q2|q3|q4|1p|2p|3p|p1|p2|p3)\b"
)


def _is_segment_or_prop(text: str) -> bool:
    """Return True if text looks like a segment/prop market we should skip."""
    t = str(text).strip().lower()
    if not t:
        return False
    if _SEGMENT_MARKET_RE.search(t):
        return True
    if _SEGMENT_SHORT_RE.search(t):
        return True
    return any(kw in t for kw in _NON_MATCH_PROP_HINTS)


_VS_RE = re.compile(r"\s+(?:vs\.?|v\.?|@)\s+", re.IGNORECASE)
_SPREAD_NUM_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")


def _split_vs_title(title: str) -> tuple[str, str] | None:
    """Split 'Team A vs. Team B' into (team_a, team_b)."""
    parts = _VS_RE.split(str(title or "").strip(), maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        # Strip trailing date fragments like "2026-03-28"
        b = re.sub(r"\s+\d{4}-\d{2}-\d{2}.*$", "", parts[1].strip())
        return parts[0].strip(), b.strip() if b.strip() else parts[1].strip()
    return None


def _looks_like_spread_number(text: str) -> bool:
    return bool(_SPREAD_NUM_RE.match(str(text or "").strip()))


# ---------------------------------------------------------------------------
# Extract tradeable markets from Polymarket US event data
# ---------------------------------------------------------------------------

def _extract_drawable_markets(event: dict, sport_tag: str = "") -> list[PolyMarket]:
    """Extract tradeable drawable_outcome markets from a Polymarket US event.

    Soccer events have three-way markets (Team A / Draw / Team B) as separate
    binary Yes/No markets.  We create a PolyMarket entry for EACH team so the
    edge detector can evaluate both sides independently.
    """
    results = []
    event_title = str(event.get("title") or "").strip()
    event_start = str(event.get("startTime") or event.get("startDate") or "").strip()

    # Build teamId → team name lookup from event participants.
    team_names: dict[int, str] = {}
    for p in event.get("participants", []):
        team = p.get("team") or {}
        tid = team.get("id")
        name = str(team.get("name") or team.get("safeName") or "").strip()
        if tid and name:
            team_names[tid] = name

    # Collect non-draw team-win markets with their team identity.
    team_markets: list[tuple[dict, str]] = []  # (market_dict, team_name)
    for market in event.get("markets", []):
        smt = str(market.get("sportsMarketType") or "").strip().lower()
        if smt != _DRAWABLE_SPORTS_MARKET_TYPE:
            continue
        if market.get("closed"):
            continue
        question = str(market.get("question") or "").strip().lower()
        if "draw" in question:
            continue
        # Identify which team this market belongs to via marketSides teamId.
        sides = market.get("marketSides", [])
        team_id = None
        for s in sides:
            tid = s.get("teamId")
            if tid:
                team_id = tid
                break
        if team_id is None or team_id not in team_names:
            continue
        team_markets.append((market, team_names[team_id]))

    if len(team_markets) != 2:
        return results

    # Create a PolyMarket entry for each team's win market.
    for i in range(2):
        mkt, team_name = team_markets[i]
        _, other_name = team_markets[1 - i]

        slug = str(mkt.get("slug") or "").strip()
        if not slug:
            continue

        # Safety: skip segment/prop markets.
        question = str(mkt.get("question") or "").strip()
        if _is_segment_or_prop(question) or _is_segment_or_prop(event_title):
            continue

        condition_id = str(mkt.get("id") or slug).strip()

        results.append(
            PolyMarket(
                event_title=event_title,
                condition_id=condition_id,
                outcome_a=team_name,
                outcome_b=other_name,
                token_id_a=slug,
                token_id_b=slug,
                market_type="drawable",
                sport_tag=sport_tag,
                question=question,
            start_iso=str(fav_market.get("gameStartTime") or event_start),
            market_slug=slug,
        )
    )
    return results


def _extract_us_tradeable_markets(event: dict, sport_tag: str = "") -> list[PolyMarket]:
    """Extract tradeable game markets (moneyline + spread) from a Polymarket US event."""
    results = []
    event_title = str(event.get("title") or "").strip()
    event_start = str(event.get("startTime") or event.get("startDate") or "").strip()

    # Pre-scan: grab team names from the moneyline market so spread markets
    # can reuse them (moneyline sides have proper names like "Spurs" / "Bucks").
    _ml_long_name = ""
    _ml_short_name = ""
    for m in event.get("markets", []):
        if str(m.get("sportsMarketType") or "").strip().lower() == "moneyline":
            for side in m.get("marketSides", []):
                desc = str(side.get("description") or "").strip()
                if side.get("long") is True:
                    _ml_long_name = desc
                elif side.get("long") is False:
                    _ml_short_name = desc
            break

    for market in event.get("markets", []):
        if market.get("closed"):
            continue

        slug = str(market.get("slug") or "").strip()
        if not slug:
            continue

        # Classify using explicit sportsMarketType from US API.
        smt = str(market.get("sportsMarketType") or "").strip().lower()
        if smt not in _ALLOWED_SPORTS_MARKET_TYPES:
            continue

        market_type = "moneyline" if smt == "moneyline" else "spread"

        question = str(market.get("question") or "").strip()

        # Safety: skip segment/prop markets that might slip through.
        if _is_segment_or_prop(question) or _is_segment_or_prop(event_title):
            continue

        # Skip draw/tie moneylines.
        q_lower = question.lower()
        if market_type == "moneyline" and ("draw" in q_lower or "tie" in q_lower):
            continue

        # Extract outcomes from marketSides.
        sides = market.get("marketSides", [])
        if len(sides) != 2:
            continue

        long_side = None
        short_side = None
        for side in sides:
            if side.get("long") is True:
                long_side = side
            elif side.get("long") is False:
                short_side = side

        if not long_side or not short_side:
            continue

        # Use the market slug as the instrument identifier (Polymarket US convention).
        identifier = slug

        outcome_a = str(long_side.get("description") or "").strip()
        outcome_b = str(short_side.get("description") or "").strip()

        if not outcome_a or not outcome_b:
            continue

        # For spread markets the side descriptions are numeric ("+1.50" / "-1.50").
        # The matcher needs team names to match against odds data.  Prefer the
        # moneyline market's team names (e.g. "Spurs" / "Bucks") which the
        # matcher already handles well; fall back to event title city names.
        if market_type == "spread" and _looks_like_spread_number(outcome_a):
            if _ml_long_name and _ml_short_name:
                outcome_a = _ml_long_name
                outcome_b = _ml_short_name
            else:
                teams = _split_vs_title(event_title)
                if not teams:
                    continue
                outcome_a = teams[0]
                outcome_b = teams[1]

        # Use market ID as condition_id (unique within Polymarket US).
        condition_id = str(market.get("id") or slug).strip()

        results.append(
            PolyMarket(
                event_title=event_title,
                condition_id=condition_id,
                outcome_a=outcome_a,
                outcome_b=outcome_b,
                token_id_a=identifier,   # Long-side identifier
                token_id_b=identifier,   # Same slug; side determined by intent
                market_type=market_type,
                sport_tag=sport_tag,
                question=question,
                start_iso=str(market.get("gameStartTime") or event_start),
                market_slug=slug,
            )
        )

    # Also extract drawable_outcome markets (soccer three-way).
    results.extend(_extract_drawable_markets(event, sport_tag=sport_tag))
    return results


# ---------------------------------------------------------------------------
# Fetch sports markets from Polymarket US
# ---------------------------------------------------------------------------

async def fetch_sports_markets(sports: list[str]) -> list[PolyMarket]:
    """Fetch game markets (moneyline + spread) from Polymarket US API.

    Queries ``/v2/leagues/{league}/events`` for each sport, extracting
    tradeable moneyline and spread markets with correct US slugs.
    """
    seen_leagues: set[str] = set()
    league_entries: list[tuple[str, str]] = []   # (us_league_slug, odds_api_sport_key)
    for s in sports:
        league = _sport_to_us_league(s)
        if league and league not in seen_leagues:
            league_entries.append((league, s))
            seen_leagues.add(league)

    markets: list[PolyMarket] = []
    seen_slugs: set[str] = set()

    async with aiohttp.ClientSession(headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=20)) as session:
        for league_slug, sport_key in league_entries:
            url = f"{POLY_US_BASE}/v2/leagues/{league_slug}/events"
            offset = 0
            while offset < 500:
                params: dict = {"type": "sport", "limit": 50, "offset": offset}
                try:
                    async with session.get(url, params=params) as resp:
                        if resp.status != 200:
                            logger.warning("Polymarket US returned %d for league=%s", resp.status, league_slug)
                            break
                        data = await resp.json()
                        events = data.get("events", [])
                        if not events:
                            break
                        for ev in events:
                            for pm in _extract_us_tradeable_markets(ev, sport_tag=league_slug):
                                if pm.market_slug in seen_slugs:
                                    continue
                                seen_slugs.add(pm.market_slug)
                                markets.append(pm)
                        offset += 50
                except Exception as e:
                    logger.warning("Polymarket US fetch failed for league=%s: %s", league_slug, e)
                    break

            slug_type_counts: dict[str, int] = {}
            for pm in markets:
                if pm.sport_tag == league_slug:
                    slug_type_counts[pm.market_type] = slug_type_counts.get(pm.market_type, 0) + 1
            if slug_type_counts:
                logger.info("US league=%s: %s", league_slug, slug_type_counts)

    return markets


# ---------------------------------------------------------------------------
# Fetch order book from Polymarket US
# ---------------------------------------------------------------------------

async def fetch_order_book(market_slug: str) -> OrderBook:
    """Fetch the LONG-side order book from Polymarket US.

    Returns an OrderBook where:
      - asks = prices you can BUY LONG at (ascending)
      - bids = prices you can SELL LONG at (descending)

    The ``token_id`` field is set to the market slug for tracking.
    """
    async with aiohttp.ClientSession(headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=10)) as session:
        url = f"{POLY_US_BASE}/v1/markets/{market_slug}/book"
        async with session.get(url) as resp:
            if resp.status != 200:
                raise RuntimeError(f"US book API returned {resp.status} for {market_slug}")
            data = await resp.json()
            md = data.get("marketData", data)
            if not isinstance(md, dict):
                raise RuntimeError(f"US book API returned non-object for {market_slug}")

            # US API: offers = sell-long (you can buy long from these)
            #         bids   = buy-long  (you can sell long to these)
            asks = _parse_us_book_levels(md.get("offers", []))
            bids = _parse_us_book_levels(md.get("bids", []))
            asks.sort(key=lambda x: x.price)
            bids.sort(key=lambda x: -x.price)
            return OrderBook(token_id=market_slug, outcome_name="", asks=asks, bids=bids)


async def fetch_order_book_pair(market_slug: str) -> tuple[OrderBook, OrderBook]:
    """Fetch both long-side and short-side order books from a single US market.

    Returns (book_a, book_b) where:
      - book_a = long-side book (buy/sell the "Yes" / team-A outcome)
      - book_b = short-side book (buy/sell the "No" / team-B outcome),
                 derived by flipping prices: short_price = 1 - long_price
    """
    async with aiohttp.ClientSession(headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=10)) as session:
        url = f"{POLY_US_BASE}/v1/markets/{market_slug}/book"
        async with session.get(url) as resp:
            if resp.status != 200:
                raise RuntimeError(f"US book API returned {resp.status} for {market_slug}")
            data = await resp.json()
            md = data.get("marketData", data)
            if not isinstance(md, dict):
                raise RuntimeError(f"US book API returned non-object for {market_slug}")

            # Parse raw levels from US API.
            raw_offers = _parse_us_book_levels(md.get("offers", []))  # sell-long prices
            raw_bids = _parse_us_book_levels(md.get("bids", []))      # buy-long prices

            # Book A (long side): buy long from offers, sell long to bids.
            asks_a = sorted(raw_offers, key=lambda x: x.price)
            bids_a = sorted(raw_bids, key=lambda x: -x.price)
            book_a = OrderBook(token_id=market_slug, outcome_name="long", asks=asks_a, bids=bids_a)

            # Book B (short side): derived by flipping.
            # Buy short = other side of sell long → flip bids.
            # Sell short = other side of buy long → flip offers.
            asks_b = sorted(
                [BookLevel(price=round(1.0 - b.price, 4), size=b.size) for b in raw_bids],
                key=lambda x: x.price,
            )
            bids_b = sorted(
                [BookLevel(price=round(1.0 - o.price, 4), size=o.size) for o in raw_offers],
                key=lambda x: -x.price,
            )
            book_b = OrderBook(token_id=market_slug, outcome_name="short", asks=asks_b, bids=bids_b)

            return book_a, book_b
