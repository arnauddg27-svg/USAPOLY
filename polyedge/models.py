from dataclasses import dataclass, field
from enum import Enum

class ConfidenceTier(Enum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3

class EdgeSource(Enum):
    POLY_STALE = "poly_stale"
    POLY_THIN_BOOK = "poly_thin_book"
    BOOK_OUTLIER = "book_outlier"
    CONSENSUS = "consensus"
    UNKNOWN = "unknown"

@dataclass
class BookLevel:
    price: float
    size: float

@dataclass
class OrderBook:
    token_id: str
    outcome_name: str
    asks: list[BookLevel] = field(default_factory=list)
    bids: list[BookLevel] = field(default_factory=list)

    @property
    def best_ask(self) -> float:
        return self.asks[0].price if self.asks else 1.0

    @property
    def best_bid(self) -> float:
        return self.bids[0].price if self.bids else 0.0

    @property
    def mid(self) -> float:
        return (self.best_ask + self.best_bid) / 2

    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid

    def depth_shares(self, max_price: float = 1.0) -> float:
        return sum(a.size for a in self.asks if a.price <= max_price)

@dataclass
class SportsOutcome:
    name: str
    american_odds: int
    bookmaker: str

    @property
    def decimal_odds(self) -> float:
        if self.american_odds >= 0:
            return 1 + self.american_odds / 100
        return 1 + 100 / abs(self.american_odds)

    @property
    def implied_prob(self) -> float:
        return 1 / self.decimal_odds

@dataclass
class BookLine:
    bookmaker: str
    prob_a: float
    prob_b: float
    method: str

@dataclass
class SportsGame:
    sport: str
    home: str
    away: str
    commence_time: str
    outcomes: list[SportsOutcome] = field(default_factory=list)

@dataclass
class AllBookOdds:
    sport: str
    home: str
    away: str
    commence_time: str
    books: dict[str, tuple[SportsOutcome, SportsOutcome]] = field(default_factory=dict)
    spread_books: dict[str, tuple[SportsOutcome, SportsOutcome]] = field(default_factory=dict)

@dataclass
class PolyMarket:
    event_title: str
    condition_id: str
    outcome_a: str
    outcome_b: str
    token_id_a: str
    token_id_b: str
    market_type: str = "moneyline"
    sport_tag: str = ""
    question: str = ""
    start_iso: str = ""

@dataclass
class MatchedEvent:
    sport: str
    all_odds: AllBookOdds
    poly_market: PolyMarket
    team_a: str
    team_b: str

@dataclass
class AggregatedProb:
    prob_a: float
    prob_b: float
    books_used: int
    outliers_dropped: int
    method: str
    per_book: list[BookLine] = field(default_factory=list)

@dataclass
class EdgeOpportunity:
    matched_event: MatchedEvent
    aggregated: AggregatedProb
    buy_outcome: str
    buy_token_id: str
    true_prob: float
    poly_mid: float
    poly_fill_price: float
    poly_depth_shares: float
    poly_spread: float
    raw_edge: float
    adjusted_edge: float
    kelly_raw: float = 0.0
    kelly_adjusted: float = 0.0
    bet_usd: float = 0.0
    shares: int = 0
    confidence: ConfidenceTier = ConfidenceTier.LOW
    edge_source: EdgeSource = EdgeSource.UNKNOWN
    gate_results: dict = field(default_factory=dict)

@dataclass
class OpenOrder:
    order_id: str
    token_id: str
    condition_id: str
    risk_event_id: str
    sport: str
    side: str
    price: float
    size: float
    placed_at: float
    ttl_sec: int
    original_edge: float
    amount_usd: float = 0.0
    filled_size: float = 0.0
    event_title: str = ""
    event_start_ts: float | None = None
