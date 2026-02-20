"""Odds API client that preserves ALL bookmakers' odds per event.

Unlike the arb-scanner approach (which only keeps the best odds per outcome),
this client retains every bookmaker's line for each event. This is needed for
per-book devigging and cross-book aggregation.
"""

import aiohttp
from polyedge.models import AllBookOdds, SportsOutcome

ODDS_API_BASE = "https://api.the-odds-api.com/v4"


def parse_all_books_response(data: list[dict]) -> list[AllBookOdds]:
    """Parse The Odds API response keeping ALL bookmakers' odds per event.

    Args:
        data: Raw JSON response list from The Odds API /odds endpoint.

    Returns:
        A list of AllBookOdds, one per event that has at least one h2h market.
        Each AllBookOdds.books maps bookmaker title -> (outcome_a, outcome_b).
    """
    results = []
    for event in data:
        books: dict[str, tuple[SportsOutcome, SportsOutcome]] = {}
        home = event.get("home_team", "")
        away = event.get("away_team", "")
        for bm in event.get("bookmakers", []):
            title = bm.get("title", bm.get("key", ""))
            for market in bm.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                outcomes = market.get("outcomes", [])
                if len(outcomes) != 2:
                    continue
                o_a = SportsOutcome(
                    name=outcomes[0]["name"],
                    american_odds=int(outcomes[0]["price"]),
                    bookmaker=title,
                )
                o_b = SportsOutcome(
                    name=outcomes[1]["name"],
                    american_odds=int(outcomes[1]["price"]),
                    bookmaker=title,
                )
                books[title] = (o_a, o_b)
        if books:
            results.append(AllBookOdds(
                sport=event.get("sport_key", ""),
                home=home,
                away=away,
                commence_time=event.get("commence_time", ""),
                books=books,
            ))
    return results


async def fetch_all_odds(sports: list[str], api_key: str) -> list[AllBookOdds]:
    """Fetch odds for all configured sports, keeping all bookmakers.

    Args:
        sports: List of sport keys (e.g. ["basketball_nba", "icehockey_nhl"]).
        api_key: The Odds API key.

    Returns:
        Combined list of AllBookOdds across all requested sports.
    """
    all_games: list[AllBookOdds] = []
    async with aiohttp.ClientSession() as session:
        for sport in sports:
            url = f"{ODDS_API_BASE}/sports/{sport}/odds/"
            params = {
                "apiKey": api_key,
                "regions": "us",
                "markets": "h2h",
                "oddsFormat": "american",
            }
            try:
                async with session.get(url, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        games = parse_all_books_response(data)
                        for g in games:
                            g.sport = sport
                        all_games.extend(games)
            except Exception:
                continue
    return all_games
