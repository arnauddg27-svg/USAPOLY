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

    all_games: list[AllBookOdds] = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        for sport in sports:
            url = f"{ODDS_API_BASE}/sports/{sport}/odds/"
            params = {
                "apiKey": api_key,
                "regions": "us",
                "markets": "h2h,spreads",
                "oddsFormat": "american",
            }
            try:
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        logger.warning("Odds API returned %d for %s", resp.status, sport)
                        continue
                    data = await resp.json()
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
