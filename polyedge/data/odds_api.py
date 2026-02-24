"""Odds API client that preserves ALL bookmakers' odds per event.

Unlike the arb-scanner approach (which only keeps the best odds per outcome),
this client retains every bookmaker's line for each event. This is needed for
per-book devigging and cross-book aggregation.
"""

import logging

import aiohttp

from polyedge.models import AllBookOdds, SportsOutcome

logger = logging.getLogger(__name__)

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
_SPORT_WILDCARDS = {
    "soccer_all": "soccer_",
    "soccer_*": "soccer_",
    "tennis_all": "tennis_",
    "tennis_*": "tennis_",
}


def _looks_like_outright_key(key: str) -> bool:
    k = str(key or "").strip().lower()
    return k.endswith("_winner") or "_winner_" in k


def _format_name_with_point(name: str, point) -> str:
    try:
        point_val = float(point)
    except (TypeError, ValueError):
        return name
    if point_val.is_integer():
        point_text = f"{int(point_val):+d}"
    else:
        point_text = f"{point_val:+.1f}".rstrip("0").rstrip(".")
    return f"{name} ({point_text})"


def _parse_outcome_pair(outcomes, title: str, include_point: bool = False) -> tuple[SportsOutcome, SportsOutcome] | None:
    if not isinstance(outcomes, list) or len(outcomes) != 2:
        return None
    if not isinstance(outcomes[0], dict) or not isinstance(outcomes[1], dict):
        return None

    name_a = str(outcomes[0].get("name") or "").strip()
    name_b = str(outcomes[1].get("name") or "").strip()
    price_a = outcomes[0].get("price")
    price_b = outcomes[1].get("price")
    if not name_a or not name_b:
        return None

    if include_point:
        name_a = _format_name_with_point(name_a, outcomes[0].get("point"))
        name_b = _format_name_with_point(name_b, outcomes[1].get("point"))

    try:
        o_a = SportsOutcome(
            name=name_a,
            american_odds=int(price_a),
            bookmaker=title,
        )
        o_b = SportsOutcome(
            name=name_b,
            american_odds=int(price_b),
            bookmaker=title,
        )
    except (ValueError, TypeError):
        return None
    return o_a, o_b


def parse_all_books_response(data: list[dict]) -> list[AllBookOdds]:
    """Parse The Odds API response keeping h2h and spread lines per event."""
    results: list[AllBookOdds] = []
    for event in data:
        books: dict[str, tuple[SportsOutcome, SportsOutcome]] = {}
        spread_books: dict[str, tuple[SportsOutcome, SportsOutcome]] = {}
        home = event.get("home_team", "")
        away = event.get("away_team", "")
        for bm in event.get("bookmakers", []):
            title = bm.get("title", bm.get("key", ""))
            for market in bm.get("markets", []):
                key = str(market.get("key") or "").strip().lower()
                outcomes = market.get("outcomes", [])
                if key == "h2h":
                    pair = _parse_outcome_pair(outcomes, title, include_point=False)
                    if pair is not None:
                        books[title] = pair
                elif key == "spreads":
                    pair = _parse_outcome_pair(outcomes, title, include_point=True)
                    if pair is not None:
                        spread_books[title] = pair
        if books or spread_books:
            results.append(
                AllBookOdds(
                    sport=event.get("sport_key", ""),
                    home=home,
                    away=away,
                    commence_time=event.get("commence_time", ""),
                    books=books,
                    spread_books=spread_books,
                )
            )
    return results


def expand_sport_keys(requested_sports: list[str], available_sports: list[str]) -> list[str]:
    """Expand wildcard sport tokens into concrete The Odds API sport keys."""
    available: list[str] = []
    seen_available: set[str] = set()
    for raw in available_sports:
        key = str(raw or "").strip().lower()
        if not key or key in seen_available:
            continue
        seen_available.add(key)
        available.append(key)

    resolved: list[str] = []
    seen_resolved: set[str] = set()
    for raw in requested_sports:
        token = str(raw or "").strip().lower()
        if not token:
            continue
        wildcard_prefix = _SPORT_WILDCARDS.get(token)
        if wildcard_prefix is None:
            if token not in seen_resolved:
                seen_resolved.add(token)
                resolved.append(token)
            continue
        for key in available:
            if key.startswith(wildcard_prefix) and key not in seen_resolved:
                seen_resolved.add(key)
                resolved.append(key)
    return resolved


def _extract_available_sport_keys(payload: list[dict]) -> list[str]:
    """Normalize and filter available sport keys from /sports payload."""
    keys: list[str] = []
    seen: set[str] = set()
    for row in payload:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "").strip().lower()
        if not key:
            continue
        if key in seen:
            continue
        # Keep regular sport keys even if the league also offers outrights.
        # Only exclude explicit outright-only keys.
        if _looks_like_outright_key(key):
            continue
        active = row.get("active")
        if isinstance(active, bool) and not active:
            continue
        seen.add(key)
        keys.append(key)
    return keys


async def _fetch_available_sport_keys(session: aiohttp.ClientSession, api_key: str) -> list[str]:
    """Return all available sport keys from The Odds API."""
    try:
        async with session.get(f"{ODDS_API_BASE}/sports", params={"apiKey": api_key}) as resp:
            if resp.status != 200:
                logger.warning("Odds API sports list returned %d", resp.status)
                return []
            payload = await resp.json()
            if not isinstance(payload, list):
                logger.warning("Odds API sports list returned non-list payload")
                return []
            return _extract_available_sport_keys(payload)
    except Exception as exc:
        logger.warning("Odds API sports list fetch failed: %s", exc)
        return []


async def fetch_all_odds(sports: list[str], api_key: str) -> list[AllBookOdds]:
    """Fetch odds for all configured sports, keeping all bookmakers.

    Args:
        sports: List of sport keys (e.g. ["basketball_nba", "icehockey_nhl"]).
        api_key: The Odds API key.

    Returns:
        Combined list of AllBookOdds across all requested sports.
    """
    if not api_key:
        logger.warning("ODDS_API_KEY missing — skipping odds fetch")
        return []
    requested_sports = [str(s).strip() for s in sports if str(s).strip()]
    if not requested_sports:
        return []

    all_games: list[AllBookOdds] = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        resolved_sports = requested_sports
        if any(str(s).strip().lower() in _SPORT_WILDCARDS for s in requested_sports):
            available_keys = await _fetch_available_sport_keys(session, api_key)
            expanded = expand_sport_keys(requested_sports, available_keys)
            if expanded:
                resolved_sports = expanded
                logger.info(
                    "Expanded sports from %s to %s",
                    requested_sports,
                    resolved_sports,
                )
            else:
                resolved_sports = [
                    s for s in requested_sports if str(s).strip().lower() not in _SPORT_WILDCARDS
                ]
                if not resolved_sports:
                    logger.warning(
                        "No concrete sports resolved from wildcard config: %s",
                        requested_sports,
                    )
                    return []

        for sport in resolved_sports:
            url = f"{ODDS_API_BASE}/sports/{sport}/odds/"
            params = {
                "apiKey": api_key,
                # Include major soccer/tennis bookmaker regions by default.
                "regions": "us,uk,eu",
                "markets": "h2h,spreads",
                "oddsFormat": "american",
            }
            try:
                async with session.get(url, params=params) as resp:
                    status = resp.status
                    data = None
                    if status == 200:
                        data = await resp.json()
                    elif status == 422:
                        retry_params = dict(params)
                        retry_params["markets"] = "h2h"
                        logger.info("Odds API 422 for %s with spreads; retrying h2h only", sport)
                        async with session.get(url, params=retry_params) as retry_resp:
                            if retry_resp.status == 200:
                                data = await retry_resp.json()
                            elif retry_resp.status in {404, 422}:
                                logger.info(
                                    "Skipping unsupported odds sport=%s status=%d",
                                    sport,
                                    retry_resp.status,
                                )
                                continue
                            else:
                                logger.warning(
                                    "Odds API returned %d for %s (h2h retry)",
                                    retry_resp.status,
                                    sport,
                                )
                                continue
                    elif status in {404}:
                        logger.info("Skipping unsupported odds sport=%s status=%d", sport, status)
                        continue
                    else:
                        logger.warning("Odds API returned %d for %s", status, sport)
                        continue

                    if not isinstance(data, list):
                        logger.warning("Odds API returned non-list payload for %s", sport)
                        continue
                    games = parse_all_books_response(data)
                    for g in games:
                        g.sport = sport
                    all_games.extend(games)
            except Exception as e:
                logger.warning("Odds API fetch failed for %s: %s", sport, e)
                continue
    return all_games
