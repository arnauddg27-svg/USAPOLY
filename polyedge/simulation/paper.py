import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

from polyedge.models import EdgeOpportunity
from polyedge.paths import SIM_STATE_PATH


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PaperState:
    start_bankroll: float
    current_bankroll: float
    total_staked: float
    expected_pnl: float
    bet_count: int
    updated_at: str

    @classmethod
    def new(cls, start_bankroll: float) -> "PaperState":
        start = float(start_bankroll)
        return cls(
            start_bankroll=start,
            current_bankroll=start,
            total_staked=0.0,
            expected_pnl=0.0,
            bet_count=0,
            updated_at=_utc_now_iso(),
        )


class PaperSimulator:
    """Simple expected-value paper account for opportunity-level simulation."""

    def __init__(self, start_bankroll: float, state_path: Path = SIM_STATE_PATH):
        self.state_path = Path(state_path)
        self.state = self._load_or_init(start_bankroll)

    @property
    def current_bankroll(self) -> float:
        return float(self.state.current_bankroll)

    def reset(self, start_bankroll: float) -> None:
        self.state = PaperState.new(start_bankroll)
        self._persist()

    def record_bet(self, opp: EdgeOpportunity, cycle: int = 0) -> dict:
        stake = max(0.0, float(opp.bet_usd))
        fill = max(1e-6, float(opp.poly_fill_price))
        true_prob = min(1.0, max(0.0, float(opp.true_prob)))
        bankroll_before = float(self.state.current_bankroll)

        # Expected PnL for YES shares bought at fill price.
        expected_pnl = stake * ((true_prob / fill) - 1.0)

        self.state.total_staked += stake
        self.state.expected_pnl += expected_pnl
        self.state.current_bankroll += expected_pnl
        self.state.bet_count += 1
        self.state.updated_at = _utc_now_iso()
        self._persist()

        return {
            "cycle": cycle,
            "stake_usd": round(stake, 2),
            "expected_pnl_usd": round(expected_pnl, 4),
            "bankroll_before_usd": round(bankroll_before, 2),
            "bankroll_after_usd": round(self.state.current_bankroll, 2),
            "bet_count": self.state.bet_count,
        }

    def snapshot(self) -> dict:
        return asdict(self.state)

    def _load_or_init(self, start_bankroll: float) -> PaperState:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("invalid simulation state")
            return PaperState(
                start_bankroll=float(raw.get("start_bankroll", start_bankroll)),
                current_bankroll=float(raw.get("current_bankroll", start_bankroll)),
                total_staked=float(raw.get("total_staked", 0.0)),
                expected_pnl=float(raw.get("expected_pnl", 0.0)),
                bet_count=int(raw.get("bet_count", 0)),
                updated_at=str(raw.get("updated_at", _utc_now_iso())),
            )
        except Exception:
            state = PaperState.new(start_bankroll)
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")
            return state

    def _persist(self) -> None:
        payload = asdict(self.state)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)
