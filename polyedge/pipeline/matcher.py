import re
from datetime import datetime, timezone

from polyedge.data.polymarket import sport_to_tag_slug
from polyedge.models import AllBookOdds, MatchedEvent, PolyMarket, SportsOutcome

TEAM_ALIASES: dict[str, list[str]] = {
    # NBA
    "Atlanta Hawks": ["Hawks"], "Boston Celtics": ["Celtics"],
    "Brooklyn Nets": ["Nets"], "Charlotte Hornets": ["Hornets"],
    "Chicago Bulls": ["Bulls"], "Cleveland Cavaliers": ["Cavaliers", "Cavs"],
    "Dallas Mavericks": ["Mavericks", "Mavs"], "Denver Nuggets": ["Nuggets"],
    "Detroit Pistons": ["Pistons"], "Golden State Warriors": ["Warriors", "GSW"],
    "Houston Rockets": ["Rockets"], "Indiana Pacers": ["Pacers"],
    "Los Angeles Clippers": ["Clippers", "LA Clippers"],
    "Los Angeles Lakers": ["Lakers", "LA Lakers"],
    "Memphis Grizzlies": ["Grizzlies"], "Miami Heat": ["Heat"],
    "Milwaukee Bucks": ["Bucks"], "Minnesota Timberwolves": ["Timberwolves", "Wolves"],
    "New Orleans Pelicans": ["Pelicans"], "New York Knicks": ["Knicks"],
    "Oklahoma City Thunder": ["Thunder", "OKC"],
    "Orlando Magic": ["Magic"], "Philadelphia 76ers": ["76ers", "Sixers"],
    "Phoenix Suns": ["Suns"], "Portland Trail Blazers": ["Trail Blazers", "Blazers"],
    "Sacramento Kings": ["Kings"], "San Antonio Spurs": ["Spurs"],
    "Toronto Raptors": ["Raptors"], "Utah Jazz": ["Jazz"],
    "Washington Wizards": ["Wizards"],
    # NFL
    "Arizona Cardinals": ["Cardinals"], "Atlanta Falcons": ["Falcons"],
    "Baltimore Ravens": ["Ravens"], "Buffalo Bills": ["Bills"],
    "Carolina Panthers": ["Panthers"], "Chicago Bears": ["Bears"],
    "Cincinnati Bengals": ["Bengals"], "Cleveland Browns": ["Browns"],
    "Dallas Cowboys": ["Cowboys"], "Denver Broncos": ["Broncos"],
    "Detroit Lions": ["Lions"], "Green Bay Packers": ["Packers"],
    "Houston Texans": ["Texans"], "Indianapolis Colts": ["Colts"],
    "Jacksonville Jaguars": ["Jaguars"], "Kansas City Chiefs": ["Chiefs"],
    "Las Vegas Raiders": ["Raiders"], "Los Angeles Chargers": ["Chargers"],
    "Los Angeles Rams": ["Rams"], "Miami Dolphins": ["Dolphins"],
    "Minnesota Vikings": ["Vikings"], "New England Patriots": ["Patriots", "Pats"],
    "New Orleans Saints": ["Saints"], "New York Giants": ["Giants"],
    "New York Jets": ["Jets"], "Philadelphia Eagles": ["Eagles"],
    "Pittsburgh Steelers": ["Steelers"], "San Francisco 49ers": ["49ers", "Niners"],
    "Seattle Seahawks": ["Seahawks"], "Tampa Bay Buccaneers": ["Buccaneers", "Bucs"],
    "Tennessee Titans": ["Titans"], "Washington Commanders": ["Commanders"],
    # NHL
    "Anaheim Ducks": ["Ducks"], "Boston Bruins": ["Bruins"],
    "Buffalo Sabres": ["Sabres"], "Calgary Flames": ["Flames"],
    "Carolina Hurricanes": ["Hurricanes", "Canes"],
    "Chicago Blackhawks": ["Blackhawks"], "Colorado Avalanche": ["Avalanche", "Avs"],
    "Columbus Blue Jackets": ["Blue Jackets"], "Dallas Stars": ["Stars"],
    "Detroit Red Wings": ["Red Wings"], "Edmonton Oilers": ["Oilers"],
    "Florida Panthers": ["Panthers"], "Minnesota Wild": ["Wild"],
    "Montreal Canadiens": ["Canadiens", "Habs"],
    "Nashville Predators": ["Predators", "Preds"],
    "New Jersey Devils": ["Devils"], "New York Islanders": ["Islanders"],
    "New York Rangers": ["Rangers"], "Ottawa Senators": ["Senators", "Sens"],
    "Philadelphia Flyers": ["Flyers"], "Pittsburgh Penguins": ["Penguins", "Pens"],
    "San Jose Sharks": ["Sharks"], "Seattle Kraken": ["Kraken"],
    "St. Louis Blues": ["Blues"], "Tampa Bay Lightning": ["Lightning", "Bolts"],
    "Toronto Maple Leafs": ["Maple Leafs", "Leafs"],
    "Vancouver Canucks": ["Canucks"], "Vegas Golden Knights": ["Golden Knights", "VGK"],
    "Washington Capitals": ["Capitals", "Caps"], "Winnipeg Jets": ["Jets"],
    # MLB
    "Arizona Diamondbacks": ["Diamondbacks", "D-backs"],
    "Atlanta Braves": ["Braves"], "Baltimore Orioles": ["Orioles", "O's"],
    "Boston Red Sox": ["Red Sox"], "Chicago Cubs": ["Cubs"],
    "Chicago White Sox": ["White Sox"], "Cincinnati Reds": ["Reds"],
    "Cleveland Guardians": ["Guardians"], "Colorado Rockies": ["Rockies"],
    "Detroit Tigers": ["Tigers"], "Houston Astros": ["Astros"],
    "Kansas City Royals": ["Royals"], "Los Angeles Angels": ["Angels"],
    "Los Angeles Dodgers": ["Dodgers"], "Miami Marlins": ["Marlins"],
    "Milwaukee Brewers": ["Brewers"], "Minnesota Twins": ["Twins"],
    "New York Mets": ["Mets"], "New York Yankees": ["Yankees"],
    "Oakland Athletics": ["Athletics", "A's"],
    "Philadelphia Phillies": ["Phillies"], "Pittsburgh Pirates": ["Pirates"],
    "San Diego Padres": ["Padres"], "San Francisco Giants": ["Giants"],
    "Seattle Mariners": ["Mariners"], "St. Louis Cardinals": ["Cardinals"],
    "Tampa Bay Rays": ["Rays"], "Texas Rangers": ["Rangers"],
    "Toronto Blue Jays": ["Blue Jays"], "Washington Nationals": ["Nationals", "Nats"],
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_SPREAD_POINT_RE = re.compile(r"\(\s*([+-]?\d+(?:\.\d+)?)\s*\)\s*$")
# Gamma sports event timestamps are often stale by up to ~2 weeks.
# Keep a broad guard to block clearly unrelated historical markets,
# while still allowing current matchup markets with imperfect metadata.
_MAX_START_TIME_DRIFT_SEC = 14 * 24 * 3600


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(str(text).lower())


def _contains_token_sequence(needle: list[str], haystack: list[str]) -> bool:
    if not needle or not haystack or len(needle) > len(haystack):
        return False
    n = len(needle)
    return any(haystack[i:i + n] == needle for i in range(len(haystack) - n + 1))


def _names_match(full_name: str, candidate: str) -> bool:
    """Match a canonical team name against an outcome label using token boundaries."""
    full_tokens = _tokenize(full_name)
    candidate_tokens = _tokenize(candidate)
    if not full_tokens or not candidate_tokens:
        return False

    if _contains_token_sequence(full_tokens, candidate_tokens):
        return True

    aliases = TEAM_ALIASES.get(full_name, [])
    for alias in aliases:
        alias_tokens = _tokenize(alias)
        if _contains_token_sequence(alias_tokens, candidate_tokens):
            return True

    # Lightweight fallback for markets that use only the team nickname.
    return len(candidate_tokens) == 1 and candidate_tokens[0] == full_tokens[-1]


def _parse_iso_utc(raw: str) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _start_times_compatible(odds_commence_time: str, poly_start_iso: str) -> bool:
    odds_start = _parse_iso_utc(odds_commence_time)
    poly_start = _parse_iso_utc(poly_start_iso)
    if odds_start is None or poly_start is None:
        return True
    drift_sec = abs((odds_start - poly_start).total_seconds())
    return drift_sec <= _MAX_START_TIME_DRIFT_SEC


def start_time_drift_seconds(odds_commence_time: str, poly_start_iso: str) -> float:
    """Return absolute start-time drift in seconds, or inf if missing/unparseable."""
    odds_start = _parse_iso_utc(odds_commence_time)
    poly_start = _parse_iso_utc(poly_start_iso)
    if odds_start is None or poly_start is None:
        return float("inf")
    return abs((odds_start - poly_start).total_seconds())


def orient_book_outcomes(
    team_a: str,
    team_b: str,
    first: SportsOutcome,
    second: SportsOutcome,
) -> tuple[SportsOutcome, SportsOutcome] | None:
    """Map a bookmaker outcome pair to (team_a, team_b) orientation."""
    a_first = _names_match(team_a, first.name)
    b_second = _names_match(team_b, second.name)
    if a_first and b_second:
        return first, second

    a_second = _names_match(team_a, second.name)
    b_first = _names_match(team_b, first.name)
    if a_second and b_first:
        return second, first
    return None


def _extract_spread_point(text: str) -> float | None:
    m = _SPREAD_POINT_RE.search(str(text or "").strip())
    if not m:
        return None
    try:
        return float(m.group(1))
    except (TypeError, ValueError):
        return None


def _extract_named_point(question: str, team_name: str) -> float | None:
    team = str(team_name or "").strip()
    q = str(question or "")
    if not team or not q:
        return None
    pattern = re.compile(rf"{re.escape(team)}\s*\(\s*([+-]?\d+(?:\.\d+)?)\s*\)", re.IGNORECASE)
    m = pattern.search(q)
    if not m:
        return None
    try:
        return float(m.group(1))
    except (TypeError, ValueError):
        return None


def poly_spread_points(poly: PolyMarket) -> tuple[float | None, float | None]:
    """Return expected spread points for (outcome_a, outcome_b), if derivable."""
    point_a = _extract_spread_point(poly.outcome_a)
    point_b = _extract_spread_point(poly.outcome_b)

    if point_a is None:
        point_a = _extract_named_point(poly.question, poly.outcome_a)
    if point_b is None:
        point_b = _extract_named_point(poly.question, poly.outcome_b)

    if point_a is None and point_b is not None:
        point_a = -point_b
    elif point_b is None and point_a is not None:
        point_b = -point_a
    return point_a, point_b


def spread_points_compatible(
    poly: PolyMarket,
    team_a_outcome: SportsOutcome,
    team_b_outcome: SportsOutcome,
    tol: float = 0.01,
) -> bool:
    """Ensure sportsbook spread line exactly matches the Polymarket spread line."""
    expected_a, expected_b = poly_spread_points(poly)
    if expected_a is None or expected_b is None:
        return False
    book_a = _extract_spread_point(team_a_outcome.name)
    book_b = _extract_spread_point(team_b_outcome.name)
    if book_a is None or book_b is None:
        return False
    return abs(book_a - expected_a) <= tol and abs(book_b - expected_b) <= tol


def match_events(
    games: list[AllBookOdds],
    polys: list[PolyMarket],
) -> list[MatchedEvent]:
    """Match sportsbook games to Polymarket markets by team names.

    Each Polymarket market is used at most once (first-come, first-served).
    """
    results: list[MatchedEvent] = []
    used_polys: set[int] = set()
    for game in games:
        expected_slug = sport_to_tag_slug(game.sport)
        best_index = None
        best_match: MatchedEvent | None = None
        best_drift = float("inf")
        for i, poly in enumerate(polys):
            if i in used_polys:
                continue
            # Cross-sport guard: only match within same sport
            if expected_slug and poly.sport_tag and poly.sport_tag != expected_slug:
                continue
            if not _start_times_compatible(game.commence_time, poly.start_iso):
                continue

            home_a = _names_match(game.home, poly.outcome_a)
            away_b = _names_match(game.away, poly.outcome_b)
            home_b = _names_match(game.home, poly.outcome_b)
            away_a = _names_match(game.away, poly.outcome_a)
            if home_a and away_b:
                candidate = MatchedEvent(
                    sport=game.sport,
                    all_odds=game,
                    poly_market=poly,
                    team_a=game.home,
                    team_b=game.away,
                )
            elif home_b and away_a:
                candidate = MatchedEvent(
                    sport=game.sport,
                    all_odds=game,
                    poly_market=poly,
                    team_a=game.away,
                    team_b=game.home,
                )
            else:
                continue

            drift = start_time_drift_seconds(game.commence_time, poly.start_iso)
            if best_match is None or drift < best_drift:
                best_match = candidate
                best_index = i
                best_drift = drift

        if best_match is not None and best_index is not None:
            results.append(best_match)
            used_polys.add(best_index)
    return results
