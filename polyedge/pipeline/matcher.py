import re
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from math import isfinite

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
    "Los Angeles Kings": ["Kings", "LA Kings"],
    "Montreal Canadiens": ["Canadiens", "Habs"],
    "Nashville Predators": ["Predators", "Preds"],
    "New Jersey Devils": ["Devils"], "New York Islanders": ["Islanders"],
    "New York Rangers": ["Rangers"], "Ottawa Senators": ["Senators", "Sens"],
    "Philadelphia Flyers": ["Flyers"], "Pittsburgh Penguins": ["Penguins", "Pens"],
    "San Jose Sharks": ["Sharks"], "Seattle Kraken": ["Kraken"],
    "St. Louis Blues": ["Blues"], "Tampa Bay Lightning": ["Lightning", "Bolts"],
    "Toronto Maple Leafs": ["Maple Leafs", "Leafs"],
    "Utah Hockey Club": ["Utah HC", "Utah"],
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

TEAM_CODE_ALIASES: dict[str, list[str]] = {
    # NHL 3-letter codes frequently used as outcome labels.
    "Anaheim Ducks": ["ANA"],
    "Boston Bruins": ["BOS"],
    "Buffalo Sabres": ["BUF"],
    "Calgary Flames": ["CGY"],
    "Carolina Hurricanes": ["CAR"],
    "Chicago Blackhawks": ["CHI"],
    "Colorado Avalanche": ["COL"],
    "Columbus Blue Jackets": ["CBJ"],
    "Dallas Stars": ["DAL"],
    "Detroit Red Wings": ["DET"],
    "Edmonton Oilers": ["EDM"],
    "Florida Panthers": ["FLA"],
    "Minnesota Wild": ["MIN"],
    "Montreal Canadiens": ["MTL"],
    "Nashville Predators": ["NSH"],
    "New Jersey Devils": ["NJD"],
    "New York Islanders": ["NYI"],
    "New York Rangers": ["NYR"],
    "Ottawa Senators": ["OTT"],
    "Philadelphia Flyers": ["PHI"],
    "Pittsburgh Penguins": ["PIT"],
    "San Jose Sharks": ["SJS"],
    "Seattle Kraken": ["SEA"],
    "St. Louis Blues": ["STL"],
    "Tampa Bay Lightning": ["TBL"],
    "Toronto Maple Leafs": ["TOR"],
    "Utah Hockey Club": ["UTA", "UTH"],
    "Vancouver Canucks": ["VAN"],
    "Vegas Golden Knights": ["VGK"],
    "Washington Capitals": ["WSH"],
    "Winnipeg Jets": ["WPG"],
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_NUMERIC_TS_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_SPREAD_POINT_PARENS_RE = re.compile(r"\(\s*([+-]?\d+(?:\.\d+)?)\s*\)\s*$")
_SPREAD_POINT_TRAILING_RE = re.compile(r"(?:^|\s)([+-]\d+(?:\.\d+)?)\s*$")
_YES_NO_TARGET_PATTERNS = (
    re.compile(r"\bwill\s+(.+?)\s+win\b", re.IGNORECASE),
    re.compile(r"\bwill\s+(.+?)\s+cover\b", re.IGNORECASE),
    re.compile(
        r"\bwill\s+(.+?)\s*(?:\(\s*[+-]?\d+(?:\.\d+)?\s*\)|[+-]\d+(?:\.\d+)?)",
        re.IGNORECASE,
    ),
)
# Safety-first: keep clearly wrong cross-date mismatches out while allowing
# moderate feed/provider timestamp drift around game-day windows.
_MAX_START_TIME_DRIFT_SEC = 36 * 3600
_MAX_CROSS_DATE_DRIFT_SEC = 18 * 3600
_MISSING_TIME_PENALTY = 200_000_000
_MONEYLINE_SCORE_BONUS = 15_000
_SPREAD_SCORE_WITH_BOOKS_BONUS = 8_000
_SPREAD_SCORE_NO_BOOKS_PENALTY = 12_000


@dataclass(frozen=True)
class _MatchCandidate:
    game_idx: int
    poly_idx: int
    matched: MatchedEvent
    name_score: int
    drift_sec: float
    total_score: float


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(str(text).lower())


def _normalized_key(text: str) -> str:
    return " ".join(_tokenize(text))


def _compact_key(text: str) -> str:
    return "".join(_tokenize(text))


def _build_alias_lookup(source: dict[str, list[str]]) -> dict[str, list[str]]:
    lookup: dict[str, list[str]] = {}
    for team_name, aliases in source.items():
        key = _normalized_key(team_name)
        if not key:
            continue
        existing = lookup.setdefault(key, [])
        for alias in aliases:
            alias_text = str(alias).strip()
            if alias_text and alias_text not in existing:
                existing.append(alias_text)
    return lookup


_TEAM_ALIAS_LOOKUP = _build_alias_lookup(TEAM_ALIASES)
_TEAM_CODE_ALIAS_LOOKUP = _build_alias_lookup(TEAM_CODE_ALIASES)

_TEAM_KEYS: tuple[str, ...] = tuple(
    sorted(set(_TEAM_ALIAS_LOOKUP) | set(_TEAM_CODE_ALIAS_LOOKUP))
)
_TEAM_KEYS_BY_TAIL1: dict[str, list[str]] = {}
_TEAM_KEYS_BY_TAIL2: dict[str, list[str]] = {}
for _team_key in _TEAM_KEYS:
    _tokens = _tokenize(_team_key)
    if not _tokens:
        continue
    _TEAM_KEYS_BY_TAIL1.setdefault(_tokens[-1], []).append(_team_key)
    if len(_tokens) >= 2:
        _TEAM_KEYS_BY_TAIL2.setdefault(" ".join(_tokens[-2:]), []).append(_team_key)


@lru_cache(maxsize=1024)
def _candidate_team_keys_for_name(full_name: str) -> tuple[str, ...]:
    tokens = _tokenize(full_name)
    if not tokens:
        return ()
    key = " ".join(tokens)
    candidates: set[str] = set()
    if key in _TEAM_ALIAS_LOOKUP or key in _TEAM_CODE_ALIAS_LOOKUP:
        candidates.add(key)
    candidates.update(_TEAM_KEYS_BY_TAIL1.get(tokens[-1], []))
    if len(tokens) >= 2:
        candidates.update(_TEAM_KEYS_BY_TAIL2.get(" ".join(tokens[-2:]), []))
    if not candidates:
        return ()

    token_set = set(tokens)
    scored: list[tuple[int, str]] = []
    for candidate_key in candidates:
        candidate_tokens = _tokenize(candidate_key)
        if not candidate_tokens:
            continue
        score = 0
        if candidate_tokens == tokens:
            score += 200
        if len(candidate_tokens) >= 2 and len(tokens) >= 2:
            if candidate_tokens[-2:] == tokens[-2:]:
                score += 80
        if candidate_tokens[-1] == tokens[-1]:
            score += 50
        score += len(token_set & set(candidate_tokens)) * 10
        if candidate_tokens and tokens and candidate_tokens[0] == tokens[0]:
            score += 8
        if score > 0:
            scored.append((score, candidate_key))
    if not scored:
        return ()
    scored.sort(reverse=True)
    best = scored[0][0]
    keep = [team_key for score, team_key in scored if score >= max(30, best - 25)]
    return tuple(keep)


@lru_cache(maxsize=1024)
def _team_form_keys(team_key: str) -> tuple[str, ...]:
    forms: set[str] = {team_key}
    for alias in _TEAM_ALIAS_LOOKUP.get(team_key, []):
        key = _normalized_key(alias)
        if key:
            forms.add(key)
    for alias in _TEAM_CODE_ALIAS_LOOKUP.get(team_key, []):
        key = _normalized_key(alias)
        if key:
            forms.add(key)
    return tuple(sorted(forms))


@lru_cache(maxsize=1024)
def _team_form_compacts(team_key: str) -> tuple[str, ...]:
    compacts: set[str] = set()
    for form in _team_form_keys(team_key):
        compact = _compact_key(form)
        if compact:
            compacts.add(compact)
    return tuple(sorted(compacts))


def _contains_token_sequence(needle: list[str], haystack: list[str]) -> bool:
    if not needle or not haystack or len(needle) > len(haystack):
        return False
    n = len(needle)
    return any(haystack[i:i + n] == needle for i in range(len(haystack) - n + 1))


def _name_match_strength(full_name: str, candidate: str) -> int:
    """Return a match strength score for team name vs candidate label."""
    full_tokens = _tokenize(full_name)
    candidate_tokens = _tokenize(candidate)
    if not full_tokens or not candidate_tokens:
        return 0

    if _contains_token_sequence(full_tokens, candidate_tokens):
        return 130

    candidate_key = " ".join(candidate_tokens)
    candidate_compact = "".join(candidate_tokens)
    best = 0

    for team_key in _candidate_team_keys_for_name(full_name):
        key_tokens = _tokenize(team_key)
        if not key_tokens:
            continue

        for form_key in _team_form_keys(team_key):
            form_tokens = _tokenize(form_key)
            if not form_tokens:
                continue
            if candidate_key == form_key:
                if form_key == team_key:
                    best = max(best, 120)
                elif len(form_tokens) == 1:
                    best = max(best, 100)
                else:
                    best = max(best, 110)
                continue
            if _contains_token_sequence(form_tokens, candidate_tokens):
                if form_key == team_key:
                    best = max(best, 115)
                elif len(form_tokens) == 1:
                    best = max(best, 96)
                else:
                    best = max(best, 106)
                continue
            if len(candidate_tokens) >= 2 and _contains_token_sequence(candidate_tokens, form_tokens):
                best = max(best, 90)

        if candidate_compact in _team_form_compacts(team_key):
            best = max(best, 104)

        # Fallback when markets use only nickname (e.g. "Predators").
        if len(candidate_tokens) == 1 and candidate_tokens[0] == key_tokens[-1]:
            ambiguity = len(_TEAM_KEYS_BY_TAIL1.get(candidate_tokens[0], []))
            best = max(best, 78 if ambiguity <= 2 else 68)
        if len(candidate_tokens) >= 2 and len(key_tokens) >= 2:
            if candidate_tokens[-2:] == key_tokens[-2:]:
                best = max(best, 88)

    if best > 0:
        return best

    # Last-resort fallback to preserve coverage for previously unseen teams.
    if len(candidate_tokens) == 1 and candidate_tokens[0] == full_tokens[-1]:
        return 64
    return 0


def _names_match(full_name: str, candidate: str) -> bool:
    """Boolean wrapper used by spread orientation helper."""
    return _name_match_strength(full_name, candidate) > 0


def _extract_yes_no_target_team(question: str) -> str:
    q = str(question or "").strip()
    if not q:
        return ""
    for pattern in _YES_NO_TARGET_PATTERNS:
        m = pattern.search(q)
        if m:
            return str(m.group(1) or "").strip(" ?:-")
    return ""


def _parse_iso_utc(raw: str) -> datetime | None:
    if isinstance(raw, (int, float)):
        val = float(raw)
        if not isfinite(val):
            return None
        # Handle millisecond epochs.
        if abs(val) > 1e12:
            val /= 1000.0
        return datetime.fromtimestamp(val, tz=timezone.utc)

    text = str(raw or "").strip()
    if not text:
        return None
    if _NUMERIC_TS_RE.match(text):
        try:
            val = float(text)
        except ValueError:
            val = float("nan")
        if isfinite(val):
            if abs(val) > 1e12:
                val /= 1000.0
            try:
                return datetime.fromtimestamp(val, tz=timezone.utc)
            except Exception:
                pass
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
    if drift_sec > _MAX_START_TIME_DRIFT_SEC:
        return False
    if odds_start.date() != poly_start.date() and drift_sec > _MAX_CROSS_DATE_DRIFT_SEC:
        return False
    return True


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
    normalized = (
        str(text or "")
        .strip()
        .replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
    )
    m = _SPREAD_POINT_PARENS_RE.search(normalized)
    if not m:
        m = _SPREAD_POINT_TRAILING_RE.search(normalized)
    if not m:
        return None
    try:
        return float(m.group(1))
    except (TypeError, ValueError):
        return None


def _extract_named_point(question: str, team_name: str) -> float | None:
    team = str(team_name or "").strip()
    q = (
        str(question or "")
        .replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
    )
    if not team or not q:
        return None
    pattern = re.compile(
        rf"{re.escape(team)}\s*(?:\(\s*([+-]?\d+(?:\.\d+)?)\s*\)|([+-]\d+(?:\.\d+)?))",
        re.IGNORECASE,
    )
    m = pattern.search(q)
    if not m:
        return None
    try:
        point_token = m.group(1) if m.group(1) is not None else m.group(2)
        return float(point_token)
    except (TypeError, ValueError):
        return None


def poly_spread_points(
    poly: PolyMarket,
    team_a_name: str = "",
    team_b_name: str = "",
) -> tuple[float | None, float | None]:
    """Return expected spread points for (outcome_a, outcome_b), if derivable."""
    point_a = _extract_spread_point(poly.outcome_a)
    point_b = _extract_spread_point(poly.outcome_b)

    if point_a is None:
        point_a = _extract_named_point(poly.question, poly.outcome_a)
    if point_b is None:
        point_b = _extract_named_point(poly.question, poly.outcome_b)
    if point_a is None and team_a_name:
        point_a = _extract_named_point(poly.question, team_a_name)
    if point_b is None and team_b_name:
        point_b = _extract_named_point(poly.question, team_b_name)

    if point_a is None and point_b is not None:
        point_a = -point_b
    elif point_b is None and point_a is not None:
        point_b = -point_a
    return point_a, point_b


def spread_points_compatible(
    poly: PolyMarket,
    team_a_outcome: SportsOutcome,
    team_b_outcome: SportsOutcome,
    team_a_name: str = "",
    team_b_name: str = "",
    tol: float = 0.01,
) -> bool:
    """Ensure sportsbook spread line exactly matches the Polymarket spread line."""
    expected_a, expected_b = poly_spread_points(
        poly,
        team_a_name=team_a_name,
        team_b_name=team_b_name,
    )
    if expected_a is None or expected_b is None:
        return False
    book_a = _extract_spread_point(team_a_outcome.name)
    book_b = _extract_spread_point(team_b_outcome.name)
    if book_a is None or book_b is None:
        return False
    return abs(book_a - expected_a) <= tol and abs(book_b - expected_b) <= tol


def _compatible_spread_book_count(
    game: AllBookOdds,
    poly: PolyMarket,
    team_a: str,
    team_b: str,
) -> int:
    count = 0
    for team_a_outcome, team_b_outcome in getattr(game, "spread_books", {}).values():
        oriented = orient_book_outcomes(team_a, team_b, team_a_outcome, team_b_outcome)
        if oriented is None:
            continue
        if spread_points_compatible(
            poly,
            oriented[0],
            oriented[1],
            team_a_name=team_a,
            team_b_name=team_b,
        ):
            count += 1
    return count


def match_events(
    games: list[AllBookOdds],
    polys: list[PolyMarket],
    min_books_for_spread: int = 1,
) -> list[MatchedEvent]:
    """Match sportsbook games to Polymarket markets by team names.

    Each Polymarket market is used at most once (first-come, first-served).
    """
    results: list[MatchedEvent] = []
    game_candidates: dict[int, list[_MatchCandidate]] = {}

    for game_idx, game in enumerate(games):
        expected_slug = sport_to_tag_slug(game.sport)
        candidates: list[_MatchCandidate] = []
        # Set to 0 to disable pre-filtering spread markets at match-time.
        required_spread_books = max(0, int(min_books_for_spread))
        for poly_idx, poly in enumerate(polys):
            # Cross-sport guard: only match within same sport
            if expected_slug and poly.sport_tag and poly.sport_tag != expected_slug:
                continue

            home_a = _name_match_strength(game.home, poly.outcome_a)
            away_b = _name_match_strength(game.away, poly.outcome_b)
            home_b = _name_match_strength(game.home, poly.outcome_b)
            away_a = _name_match_strength(game.away, poly.outcome_a)
            if home_a > 0 and away_b > 0:
                name_score = home_a + away_b
                candidate = MatchedEvent(
                    sport=game.sport,
                    all_odds=game,
                    poly_market=poly,
                    team_a=game.home,
                    team_b=game.away,
                )
            elif home_b > 0 and away_a > 0:
                name_score = home_b + away_a
                candidate = MatchedEvent(
                    sport=game.sport,
                    all_odds=game,
                    poly_market=poly,
                    team_a=game.away,
                    team_b=game.home,
                )
            else:
                # Fallback: some exchanges label outcomes generically ("Home/Away")
                # while event_title still contains team names.
                out_a = str(poly.outcome_a or "").strip().lower()
                out_b = str(poly.outcome_b or "").strip().lower()
                generic_labels = {
                    "home", "away", "team a", "team b", "teama", "teamb", "a", "b", "1", "2"
                }
                yes_no = {out_a, out_b} == {"yes", "no"}
                generic_outcomes = out_a in generic_labels and out_b in generic_labels
                title_home = _name_match_strength(game.home, poly.event_title)
                title_away = _name_match_strength(game.away, poly.event_title)
                if yes_no:
                    # Some sports (notably rugby and soccer spreads) can use
                    # Yes/No markets phrased as "Will Team X win/cover?".
                    if expected_slug not in {"rugby", "soccer"}:
                        continue
                    question_text = str(getattr(poly, "question", "") or "")
                    target_team = _extract_yes_no_target_team(question_text)
                    if not (title_home and title_away):
                        continue
                    target_home = _name_match_strength(game.home, target_team) if target_team else 0
                    target_away = _name_match_strength(game.away, target_team) if target_team else 0

                    # Fallback when parser can't isolate target team cleanly.
                    if target_home <= 0 and target_away <= 0:
                        q_home = _name_match_strength(game.home, question_text)
                        q_away = _name_match_strength(game.away, question_text)
                        if q_home > q_away:
                            target_home, target_away = q_home, 0
                        elif q_away > q_home:
                            target_home, target_away = 0, q_away
                        else:
                            continue

                    if target_home > 0 and target_away > 0:
                        if abs(target_home - target_away) < 20:
                            continue
                        if target_home > target_away:
                            target_away = 0
                        else:
                            target_home = 0

                    if target_home > 0 and target_away == 0:
                        name_score = target_home + title_home + title_away
                        candidate = MatchedEvent(
                            sport=game.sport,
                            all_odds=game,
                            poly_market=poly,
                            team_a=game.home,
                            team_b=game.away,
                        )
                    elif target_away > 0 and target_home == 0:
                        name_score = target_away + title_home + title_away
                        candidate = MatchedEvent(
                            sport=game.sport,
                            all_odds=game,
                            poly_market=poly,
                            team_a=game.away,
                            team_b=game.home,
                        )
                    else:
                        continue
                elif not generic_outcomes or not (title_home and title_away):
                    continue
                else:
                    name_score = title_home + title_away
                    candidate = MatchedEvent(
                        sport=game.sport,
                        all_odds=game,
                        poly_market=poly,
                        team_a=game.home,
                        team_b=game.away,
                    )

            drift = start_time_drift_seconds(game.commence_time, poly.start_iso)
            if isfinite(drift) and not _start_times_compatible(game.commence_time, poly.start_iso):
                continue

            market_type = getattr(poly, "market_type", "moneyline")
            if market_type == "spread" and required_spread_books > 0:
                compatible_spread = _compatible_spread_book_count(
                    game,
                    poly,
                    candidate.team_a,
                    candidate.team_b,
                )
                if compatible_spread < required_spread_books:
                    continue

            market_bonus = 0.0
            if market_type == "moneyline":
                market_bonus += _MONEYLINE_SCORE_BONUS
            elif market_type == "spread":
                market_bonus += (
                    _SPREAD_SCORE_WITH_BOOKS_BONUS
                    if bool(getattr(game, "spread_books", {}))
                    else -_SPREAD_SCORE_NO_BOOKS_PENALTY
                )

            total_score = float(name_score * 100_000 + market_bonus)
            if isfinite(drift):
                total_score -= drift
                odds_start = _parse_iso_utc(game.commence_time)
                poly_start = _parse_iso_utc(poly.start_iso)
                if odds_start and poly_start and odds_start.date() == poly_start.date():
                    total_score += 10_000
            else:
                total_score -= _MISSING_TIME_PENALTY

            candidates.append(
                _MatchCandidate(
                    game_idx=game_idx,
                    poly_idx=poly_idx,
                    matched=candidate,
                    name_score=name_score,
                    drift_sec=drift,
                    total_score=total_score,
                )
            )

        if not candidates:
            continue

        # Prefer parseable start times.
        finite_candidates = [c for c in candidates if isfinite(c.drift_sec)]
        if finite_candidates:
            game_candidates[game_idx] = finite_candidates
            continue

        # No parseable start times at all: require clear winner by name score.
        sorted_missing = sorted(candidates, key=lambda c: c.total_score, reverse=True)
        if len(sorted_missing) == 1:
            game_candidates[game_idx] = sorted_missing
            continue
        score_gap = sorted_missing[0].total_score - sorted_missing[1].total_score
        if (
            sorted_missing[0].name_score >= sorted_missing[1].name_score + 15
            or score_gap >= 12_000
        ):
            game_candidates[game_idx] = [sorted_missing[0]]

    # Global greedy assignment by candidate quality to avoid local first-match traps.
    all_candidates: list[_MatchCandidate] = []
    for game_idx, cands in game_candidates.items():
        _ = game_idx  # satisfy linters for explicit intent
        all_candidates.extend(sorted(cands, key=lambda c: c.total_score, reverse=True))

    used_games: set[int] = set()
    used_polys: set[int] = set()
    for cand in sorted(all_candidates, key=lambda c: c.total_score, reverse=True):
        if cand.game_idx in used_games or cand.poly_idx in used_polys:
            continue
        results.append(cand.matched)
        used_games.add(cand.game_idx)
        used_polys.add(cand.poly_idx)
    return results
