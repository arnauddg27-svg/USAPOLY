"""Odds API client that preserves ALL bookmakers' odds per event.

Unlike the arb-scanner approach (which only keeps the best odds per outcome),
this client retains every bookmaker's line for each event. This is needed for
per-book devigging and cross-book aggregation.
"""

import logging
import re
import unicodedata

import aiohttp

from polyedge.models import AllBookOdds, SportsOutcome

logger = logging.getLogger(__name__)

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
_SPORT_WILDCARDS = {
    "soccer_all": "soccer_",
    "soccer_*": "soccer_",
    "tennis_all": "tennis_",
    "tennis_*": "tennis_",
    "cricket_all": "cricket_",
    "cricket_*": "cricket_",
    "rugby_all": ("rugby_", "rugbyunion_", "rugbyleague_"),
    "rugby_*": ("rugby_", "rugbyunion_", "rugbyleague_"),
    "rugbyleague_all": "rugbyleague_",
    "rugbyleague_*": "rugbyleague_",
    "rugby_league_all": "rugbyleague_",
    "rugby_league_*": "rugbyleague_",
    "table_tennis_all": "table_tennis_",
    "table_tennis_*": "table_tennis_",
}
_SPORT_FAMILY_ALIASES = {
    # Keep explicit high-signal config keys stable year-round by resolving to
    # currently active tournament/league keys from /sports.
    "tennis_atp": "tennis_atp_",
    "tennis_wta": "tennis_wta_",
    "cricket": "cricket_",
    "rugby": ("rugby_", "rugbyunion_", "rugbyleague_"),
    "rugbyleague": "rugbyleague_",
    "rugby_league": "rugbyleague_",
    "table_tennis": "table_tennis_",
}
_CRICKET_FALLBACK_KEYS = (
    "cricket_asia_cup",
    "cricket_big_bash",
    "cricket_caribbean_premier_league",
    "cricket_icc_trophy",
    "cricket_icc_world_cup",
    "cricket_icc_world_cup_womens",
    "cricket_international_t20",
    "cricket_ipl",
    "cricket_odi",
    "cricket_psl",
    "cricket_t20_blast",
    "cricket_t20_world_cup",
    "cricket_test_match",
    "cricket_the_hundred",
)
_NAME_TOKEN_RE = re.compile(r"[a-z0-9]+")
_TEAM_NOISE_TOKENS = {"fc", "cf", "sc", "ac", "afc", "club", "w", "women", "de", "la"}
_TEAM_TOKEN_ALIASES = {
    # Common soccer abbreviations/transliterations seen in odds feeds.
    "man": "manchester",
    "utd": "united",
    "st": "saint",
    "atl": "atletico",
    "munchen": "munich",
    "muenchen": "munich",
    "mgladbach": "monchengladbach",
    "gladbach": "monchengladbach",
    "lisbon": "cp",
}
_MIN_ORIENTATION_TEAM_SCORE = 45


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


def _normalize_name(name: str) -> str:
    raw = str(name or "").strip().lower()
    ascii_text = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    return " ".join(ascii_text.split())


def _name_tokens(name: str) -> list[str]:
    return _NAME_TOKEN_RE.findall(_normalize_name(name))


def _clean_team_tokens(name: str) -> list[str]:
    cleaned = [t for t in _name_tokens(name) if t not in _TEAM_NOISE_TOKENS]
    return [_TEAM_TOKEN_ALIASES.get(t, t) for t in cleaned]


def _name_compact(name: str) -> str:
    return "".join(_clean_team_tokens(name))


def _acronym(tokens: list[str]) -> str:
    return "".join(t[0] for t in tokens if t)


def _is_draw_label(name: str) -> bool:
    tokens = _clean_team_tokens(name)
    if not tokens:
        return False
    compact = "".join(tokens)
    return compact in {"draw", "tie", "x"}


def _team_name_score(team_name: str, outcome_name: str) -> int:
    team_tokens = _clean_team_tokens(team_name)
    out_tokens = _clean_team_tokens(outcome_name)
    if not team_tokens or not out_tokens:
        return 0
    team_key = " ".join(team_tokens)
    out_key = " ".join(out_tokens)
    if team_key == out_key:
        return 100
    if _name_compact(team_name) == _name_compact(outcome_name):
        return 95
    team_acr = _acronym(team_tokens)
    out_acr = _acronym(out_tokens)
    out_compact = "".join(out_tokens)
    team_compact = "".join(team_tokens)
    if team_acr and team_acr == out_compact:
        return 90
    if out_acr and out_acr == team_compact:
        return 90
    shared = len(set(team_tokens) & set(out_tokens))
    score = shared * 20
    if out_tokens and team_tokens and out_tokens[-1] == team_tokens[-1]:
        score = max(score, 45)
    if len(out_tokens) == 1 and out_tokens[0] in team_tokens:
        score = max(score, 55)
    return score


def _orient_selected_rows(
    selected: list[dict],
    team_a: str,
    team_b: str,
) -> tuple[dict, dict] | None:
    if len(selected) != 2:
        return None
    first, second = selected[0], selected[1]
    name_first = str(first.get("name") or "")
    name_second = str(second.get("name") or "")

    a_first = _team_name_score(team_a, name_first)
    b_second = _team_name_score(team_b, name_second)
    a_second = _team_name_score(team_a, name_second)
    b_first = _team_name_score(team_b, name_first)

    opt_direct = a_first + b_second
    opt_swapped = a_second + b_first
    if max(opt_direct, opt_swapped) <= 0:
        return None
    if opt_direct >= opt_swapped:
        if a_first >= _MIN_ORIENTATION_TEAM_SCORE and b_second >= _MIN_ORIENTATION_TEAM_SCORE:
            return first, second
        return None
    if a_second >= _MIN_ORIENTATION_TEAM_SCORE and b_first >= _MIN_ORIENTATION_TEAM_SCORE:
        return second, first
    return None


def _select_two_way_outcomes(outcomes, team_a: str = "", team_b: str = "") -> list[dict] | None:
    """Return a 2-outcome view from raw market outcomes.

    For standard two-way markets, this is the original list.
    For soccer-style three-way h2h (home/away/draw), this selects only home/away.
    """
    if not isinstance(outcomes, list) or len(outcomes) < 2:
        return None
    if len(outcomes) == 2:
        return outcomes
    name_a = _normalize_name(team_a)
    name_b = _normalize_name(team_b)
    if not name_a or not name_b:
        return None
    out_a = None
    out_b = None
    for row in outcomes:
        if not isinstance(row, dict):
            continue
        row_name = _normalize_name(row.get("name"))
        if row_name == name_a and out_a is None:
            out_a = row
        elif row_name == name_b and out_b is None:
            out_b = row
    if out_a is None or out_b is None:
        # Fallback for 3-way soccer books: keep non-draw outcomes.
        non_draw = [
            row
            for row in outcomes
            if isinstance(row, dict) and not _is_draw_label(row.get("name"))
        ]
        if len(non_draw) == 2:
            return non_draw
        return None
    return [out_a, out_b]


def _parse_outcome_pair(
    outcomes,
    title: str,
    include_point: bool = False,
    team_a: str = "",
    team_b: str = "",
) -> tuple[SportsOutcome, SportsOutcome] | None:
    selected = _select_two_way_outcomes(outcomes, team_a=team_a, team_b=team_b)
    if selected is None:
        return None
    canonicalize_to_teams = False
    if team_a and team_b:
        oriented = _orient_selected_rows(selected, team_a=team_a, team_b=team_b)
        if oriented is None:
            # For 3-way outcomes, skip ambiguous mappings.
            if isinstance(outcomes, list) and len(outcomes) > 2:
                return None
        else:
            selected = [oriented[0], oriented[1]]
            canonicalize_to_teams = True
    if not isinstance(selected[0], dict) or not isinstance(selected[1], dict):
        return None

    name_a = str(selected[0].get("name") or "").strip()
    name_b = str(selected[1].get("name") or "").strip()
    price_a = selected[0].get("price")
    price_b = selected[1].get("price")
    if not name_a or not name_b:
        return None

    if canonicalize_to_teams:
        name_a = str(team_a).strip() or name_a
        name_b = str(team_b).strip() or name_b

    if include_point:
        name_a = _format_name_with_point(name_a, selected[0].get("point"))
        name_b = _format_name_with_point(name_b, selected[1].get("point"))

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
        draw_odds: dict[str, float] = {}
        home = event.get("home_team", "")
        away = event.get("away_team", "")
        for bm in event.get("bookmakers", []):
            title = bm.get("title", bm.get("key", ""))
            for market in bm.get("markets", []):
                key = str(market.get("key") or "").strip().lower()
                outcomes = market.get("outcomes", [])
                if key == "h2h":
                    pair = _parse_outcome_pair(
                        outcomes,
                        title,
                        include_point=False,
                        team_a=home,
                        team_b=away,
                    )
                    if pair is not None:
                        books[title] = pair
                    # Extract draw odds for 3-way soccer markets.
                    for row in outcomes:
                        if isinstance(row, dict) and _is_draw_label(row.get("name")):
                            price = row.get("price")
                            if price is not None:
                                # Convert American odds to decimal.
                                if price >= 0:
                                    draw_odds[title] = 1 + price / 100
                                else:
                                    draw_odds[title] = 1 + 100 / abs(price)
                            break
                elif key == "spreads":
                    pair = _parse_outcome_pair(
                        outcomes,
                        title,
                        include_point=True,
                        team_a=home,
                        team_b=away,
                    )
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
                    draw_odds=draw_odds,
                )
            )
    return results


def expand_sport_keys(requested_sports: list[str], available_sports: list[str]) -> list[str]:
    """Expand wildcard and family tokens into concrete The Odds API sport keys."""
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

    def _matches_prefix(key: str, prefix_spec: str | tuple[str, ...]) -> bool:
        if isinstance(prefix_spec, tuple):
            return any(key.startswith(p) for p in prefix_spec)
        return key.startswith(prefix_spec)

    for raw in requested_sports:
        token = str(raw or "").strip().lower()
        if not token:
            continue
        wildcard_prefix = _SPORT_WILDCARDS.get(token)
        if wildcard_prefix is not None:
            for key in available:
                if _matches_prefix(key, wildcard_prefix) and key not in seen_resolved:
                    seen_resolved.add(key)
                    resolved.append(key)
            continue

        family_prefix = _SPORT_FAMILY_ALIASES.get(token)
        if family_prefix is not None:
            for key in available:
                if (key == token or _matches_prefix(key, family_prefix)) and key not in seen_resolved:
                    seen_resolved.add(key)
                    resolved.append(key)
            continue

        if token not in seen_resolved:
            seen_resolved.add(token)
            resolved.append(token)
    return resolved


def augment_sport_keys_with_fallbacks(
    requested_sports: list[str],
    resolved_sports: list[str],
) -> list[str]:
    """Append hard fallback keys for families that need year-round probing."""
    requested_tokens = {str(s or "").strip().lower() for s in requested_sports}
    want_cricket_fallbacks = bool(
        {"cricket", "cricket_all", "cricket_*"} & requested_tokens
    )

    augmented: list[str] = []
    seen: set[str] = set()
    for raw in resolved_sports:
        key = str(raw or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        augmented.append(key)

    if want_cricket_fallbacks:
        for key in _CRICKET_FALLBACK_KEYS:
            if key in seen:
                continue
            seen.add(key)
            augmented.append(key)
    return augmented


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


async def fetch_all_odds(
    sports: list[str],
    api_key: str,
    regions: str = "us,fr,uk",
    cricket_regions: str = "us,uk,eu,au",
    soccer_regions: str = "us,us2,uk,eu,au,fr,se",
    nhl_regions: str = "us",
) -> list[AllBookOdds]:
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
    configured_regions = str(regions or "").strip() or "us,fr,uk"
    configured_cricket_regions = str(cricket_regions or "").strip() or configured_regions
    configured_soccer_regions = str(soccer_regions or "").strip() or configured_regions
    configured_nhl_regions = str(nhl_regions or "").strip() or configured_regions
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        resolved_sports = requested_sports
        if any(
            str(s).strip().lower() in _SPORT_WILDCARDS
            or str(s).strip().lower() in _SPORT_FAMILY_ALIASES
            for s in requested_sports
        ):
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
                    s
                    for s in requested_sports
                    if str(s).strip().lower() not in _SPORT_WILDCARDS
                    and str(s).strip().lower() not in _SPORT_FAMILY_ALIASES
                ]
                if not resolved_sports:
                    logger.warning(
                        "No concrete sports resolved from wildcard config: %s",
                        requested_sports,
                    )
                    return []

        resolved_sports = augment_sport_keys_with_fallbacks(
            requested_sports,
            resolved_sports,
        )

        for sport in resolved_sports:
            _sk = str(sport)
            if _sk.startswith("icehockey_nhl"):
                sport_regions = configured_nhl_regions
            elif _sk.startswith("cricket_"):
                sport_regions = configured_cricket_regions
            elif _sk.startswith("soccer_") or _sk.startswith("rugby_") or _sk.startswith("rugbyunion_") or _sk.startswith("rugbyleague_"):
                sport_regions = configured_soccer_regions
            else:
                sport_regions = configured_regions
            url = f"{ODDS_API_BASE}/sports/{sport}/odds/"
            params = {
                "apiKey": api_key,
                "regions": sport_regions,
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
                    spread_count = sum(1 for g in games if g.spread_books)
                    if spread_count:
                        logger.info("Odds API %s: %d games, %d with spread lines", sport, len(games), spread_count)
                    for g in games:
                        g.sport = sport
                    all_games.extend(games)
            except Exception as e:
                logger.warning("Odds API fetch failed for %s: %s", sport, e)
                continue
    return all_games
