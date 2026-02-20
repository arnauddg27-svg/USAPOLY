from polyedge.models import AllBookOdds, PolyMarket, MatchedEvent

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


def _normalize(text: str) -> str:
    """Lowercase and strip whitespace from text."""
    return text.lower().strip()


def _names_match(full_name: str, candidate: str) -> bool:
    """Check if a full team name matches a candidate string.

    Matching strategies (in order):
    1. Exact match (case-insensitive)
    2. Substring match (either direction)
    3. Alias match (check known aliases for the full name)
    """
    nf = _normalize(full_name)
    nc = _normalize(candidate)
    if nf == nc or nf in nc or nc in nf:
        return True
    aliases = TEAM_ALIASES.get(full_name, [])
    for alias in aliases:
        na = _normalize(alias)
        if na == nc or na in nc or nc in na:
            return True
    return False


def match_events(
    games: list[AllBookOdds],
    polys: list[PolyMarket],
) -> list[MatchedEvent]:
    """Match sportsbook games to Polymarket markets by team names.

    For each game, attempts to find a Polymarket market where both team names
    (home and away) match the market's outcome_a and outcome_b (in either order).
    Uses exact matching, substring matching, and alias-based matching.

    Each Polymarket market is used at most once (first-come, first-served).
    """
    results: list[MatchedEvent] = []
    used_polys: set[int] = set()
    for game in games:
        for i, poly in enumerate(polys):
            if i in used_polys:
                continue
            home_a = _names_match(game.home, poly.outcome_a)
            away_b = _names_match(game.away, poly.outcome_b)
            home_b = _names_match(game.home, poly.outcome_b)
            away_a = _names_match(game.away, poly.outcome_a)
            if home_a and away_b:
                results.append(MatchedEvent(
                    sport=game.sport, all_odds=game, poly_market=poly,
                    team_a=game.home, team_b=game.away,
                ))
                used_polys.add(i)
                break
            elif home_b and away_a:
                results.append(MatchedEvent(
                    sport=game.sport, all_odds=game, poly_market=poly,
                    team_a=game.away, team_b=game.home,
                ))
                used_polys.add(i)
                break
    return results
