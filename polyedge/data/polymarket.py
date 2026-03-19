import logging
import re

import aiohttp

from polyedge.models import BookLevel, OrderBook, PolyMarket



def _us_slug(gamma_slug: str) -> str:
    """Prepend aec- prefix for Polymarket US market slugs."""
    if not gamma_slug or gamma_slug.startswith("aec-") or gamma_slug.startswith("tec-"):
        return gamma_slug
    return "aec-" + gamma_slug

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

# League-specific tag slugs for discovering individual game events.
# The Gamma API events endpoint returns only futures/outrights for broad
# sport tags (e.g. "soccer").  Individual match events (moneyline, spreads)
# are indexed under league-specific tags like "premier-league".
GAME_EVENT_TAG_SLUGS: dict[str, list[str]] = {
    "soccer_epl": ["premier-league"],
    "soccer_spain_la_liga": ["la-liga"],
    "soccer_germany_bundesliga": ["bundesliga"],
    "soccer_italy_serie_a": ["serie-a"],
    "soccer_france_ligue_one": ["ligue-1"],
    "soccer_uefa_champs_league": ["champions-league"],
    "soccer_uefa_europa_league": ["europa-league"],
    "soccer_mexico_ligamx": ["liga-mx"],
    "soccer_usa_mls": ["mls"],
    "soccer_brazil_serie_a": ["brasileirao"],
    "soccer_fa_cup": ["fa-cup"],
    "soccer_efl_champ": ["efl-championship"],
    "soccer_portugal_primeira_liga": ["primeira-liga"],
    "soccer_netherlands_eredivisie": ["eredivisie"],
    "soccer_turkey_super_league": ["super-lig"],
    "soccer_belgium_first_div": ["belgian-pro-league"],
}


def sport_to_tag_slug(sport_key: str) -> str:
    """Resolve an Odds API sport key to a Polymarket Gamma tag slug."""
    key = str(sport_key or "").strip().lower()
    if not key:
        return ""
    if key in SPORT_TAG_SLUGS:
        return SPORT_TAG_SLUGS[key]
    if key.startswith("soccer_"):
        return "soccer"
    if key.startswith("tennis_"):
        return "tennis"
    if key.startswith("cricket_"):
        return "cricket"
    if key in {"rugby", "rugby_all", "rugby_*"}:
        return "rugby"
    if key.startswith("rugby_") or key.startswith("rugbyunion_") or key.startswith("rugbyleague_"):
        return "rugby"
    if key.startswith("table_tennis_"):
        return "table-tennis"
    return ""

_SPREAD_PATTERN = re.compile(r"(?<!\d)[+-]\s*\d+(?:\.\d+)?")
_NUMERIC_PARENS_PATTERN = re.compile(r"\(\s*[+-]?\s*\d+(?:\.\d+)?\s*\)")
_OVER_UNDER_RE = re.compile(r"\b(?:over|under)\b")
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
_NON_MATCH_PROP_HINTS = (
    "set winner",
    "total sets",
    "set handicap",
    "set spread",
    "set total",
    "period winner",
    "total periods",
    "halftime",
    "half-time",
    "first-half",
    "second-half",
    "regulation winner",
    "in regulation",
    "after 60",
    "60-minute",
    "60 minute",
    "60 min",
    "series winner",
)
_SEGMENT_MARKET_RE = re.compile(
    r"\b(?:\d+(?:st|nd|rd|th)|first|second|third|fourth|fifth)\s+"
    r"(?:set|period|quarter|half|inning|map|game)\b"
)
_SEGMENT_SHORT_RE = re.compile(
    r"\b(?:1h|2h|h1|h2|"
    r"1q|2q|3q|4q|q1|q2|q3|q4|"
    r"1p|2p|3p|p1|p2|p3)\b"
)
_SET_N_WINNER_RE = re.compile(
    r"\bset\s*(?:\d+|first|second|third|fourth|fifth)\s+winner\b"
)
_MATCHUP_HINT_RE = re.compile(r"\bvs\.?\b|\bv\b|@")
_YES_NO_MONEYLINE_RE = re.compile(
    r"\bwill\s+.+?\s+(?:win|beat|defeat)\b",
    re.IGNORECASE,
)


def _first_non_empty(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


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
    if "total" in t:
        return True
    return _OVER_UNDER_RE.search(t) is not None


def _looks_like_spread(text: str) -> bool:
    t = str(text).strip().lower()
    if not t:
        return False
    if _SPREAD_PATTERN.search(t):
        return True
    if _NUMERIC_PARENS_PATTERN.search(t):
        return True
    return any(kw in t for kw in _SPREAD_HINTS)


def _looks_like_non_match_prop(text: str) -> bool:
    t = str(text).strip().lower()
    if not t:
        return False
    if _SEGMENT_MARKET_RE.search(t):
        return True
    if _SEGMENT_SHORT_RE.search(t):
        return True
    if _SET_N_WINNER_RE.search(t):
        return True
    return any(kw in t for kw in _NON_MATCH_PROP_HINTS)


def _classify_market_type(
    market: dict,
    outcomes: list[str],
    event_title: str = "",
    sport_tag: str = "",
) -> str | None:
    # Shortcut: game events carry an explicit sportsMarketType field.
    smt = str(market.get("sportsMarketType") or "").strip().lower()
    if smt:
        if smt == "moneyline":
            question = str(market.get("question") or "").strip().lower()
            if "draw" in question or "tie" in question:
                return None
            return "moneyline"
        if smt == "spreads":
            return "spread"
        # totals, both_teams_to_score, etc. — we don't trade these.
        return None

    outcomes_lower = [str(o).strip().lower() for o in outcomes]
    yes_no_market = set(outcomes_lower) == {"yes", "no"}
    sport_tag_l = str(sport_tag or "").strip().lower()

    question = str(market.get("question") or "").strip().lower()
    market_title = str(market.get("title") or "").strip().lower()
    event_title_l = str(event_title or "").strip().lower()
    all_text = [question, market_title, event_title_l, *outcomes_lower]

    # Guardrail: skip intra-match and prop markets (1st set/period, regulation, etc.).
    if any(_looks_like_non_match_prop(text) for text in all_text):
        return None
    if any(_looks_like_total(text) for text in all_text):
        return None
    if yes_no_market:
        # Some sports expose full-match outcomes as "Will Team X win/cover?"
        # with Yes/No outcomes. Keep only clear matchup markets and still
        # block draw/tie and segment props via guardrails above.
        if "draw" in question or "tie" in question:
            return None
        has_matchup = any(_MATCHUP_HINT_RE.search(text) for text in (question, market_title, event_title_l) if text)
        if not has_matchup:
            return None
        if any(_looks_like_spread(text) for text in (question, market_title, event_title_l) if text):
            return "spread"
        if sport_tag_l == "soccer" and _YES_NO_MONEYLINE_RE.search(question):
            return "moneyline"
        if sport_tag_l == "rugby" and "win" in question:
            return "moneyline"
        return None
    if any(_looks_like_spread(text) for text in all_text):
        return "spread"
    if question and any(hint in question for hint in _MONEYLINE_HINTS):
        return "moneyline"
    if market_title and any(hint in market_title for hint in _MONEYLINE_HINTS):
        return "moneyline"
    # Fallback only when text looks like an explicit head-to-head matchup.
    if any(_MATCHUP_HINT_RE.search(text) for text in (question, market_title, event_title_l) if text):
        return "moneyline"
    return None


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
        market_type = _classify_market_type(
            market,
            outcomes,
            event_title=str(event.get("title") or ""),
            sport_tag=sport_tag,
        )
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
                start_iso=_first_non_empty(
                    market.get("gameStartTime"),
                    market.get("eventStartTime"),
                    market.get("startTime"),
                    market.get("eventStartDate"),
                    market.get("endDate"),
                    market.get("start"),
                    market.get("startDate"),
                    event.get("gameStartTime"),
                    event.get("eventStartTime"),
                    event.get("startTime"),
                    event.get("eventStartDate"),
                    event.get("endDate"),
                    event.get("start"),
                    event.get("startDate"),
                ),
                market_slug=_us_slug(str(market.get("slug") or event.get("slug") or "").strip()),
            )
        )
    return results

async def fetch_sports_markets(sports: list[str]) -> list[PolyMarket]:
    """Fetch game markets (moneyline + spread) from Polymarket Gamma API.

    For each sport we query:
      1. The broad sport-level tag (e.g. ``soccer``, ``nba``) — catches
         standard event-style markets.
      2. League-specific game-event tags (e.g. ``premier-league``) — catches
         individual match events with moneyline / spread lines that the Gamma
         API does **not** surface under the broad sport tag.
    """
    seen_slugs: set[str] = set()
    # Each entry is (tag_slug_to_query, sport_tag_for_matching).
    slug_entries: list[tuple[str, str]] = []
    for s in sports:
        sport_slug = sport_to_tag_slug(s)
        if sport_slug and sport_slug not in seen_slugs:
            slug_entries.append((sport_slug, sport_slug))
            seen_slugs.add(sport_slug)
        # League-specific tags for game event discovery.
        for league_slug in GAME_EVENT_TAG_SLUGS.get(s.strip().lower(), []):
            if league_slug not in seen_slugs:
                slug_entries.append((league_slug, sport_slug or league_slug))
                seen_slugs.add(league_slug)
    markets: list[PolyMarket] = []
    seen_conditions: set[str] = set()
    async with aiohttp.ClientSession(headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=15)) as session:
        for query_slug, sport_tag in slug_entries:
            offset = 0
            while offset < 1000:
                params = {"tag_slug": query_slug, "active": "true", "closed": "false",
                          "limit": 50, "offset": offset}
                try:
                    async with session.get(f"{POLY_GAMMA_BASE}/events", params=params) as resp:
                        if resp.status != 200:
                            logger.warning("Gamma API returned %d for slug=%s", resp.status, query_slug)
                            break
                        events = await resp.json()
                        if not isinstance(events, list):
                            logger.warning("Gamma API returned non-list payload for slug=%s", query_slug)
                            break
                        if not events:
                            break
                        for ev in events:
                            for pm in _extract_tradeable_markets(ev, sport_tag=sport_tag):
                                if pm.condition_id in seen_conditions:
                                    continue
                                seen_conditions.add(pm.condition_id)
                                markets.append(pm)
                        offset += 50
                except Exception as e:
                    logger.warning("Gamma API fetch failed for slug=%s: %s", query_slug, e)
                    break
            slug_type_counts = {}
            for pm in markets:
                if pm.sport_tag == sport_tag:
                    slug_type_counts[pm.market_type] = slug_type_counts.get(pm.market_type, 0) + 1
            if slug_type_counts:
                logger.info("Gamma slug=%s: %s", query_slug, slug_type_counts)
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
