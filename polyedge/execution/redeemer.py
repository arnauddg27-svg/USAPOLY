import json
import logging
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from web3 import Web3

logger = logging.getLogger(__name__)

DEFAULT_RPC = "https://polygon-bor-rpc.publicnode.com"
DEFAULT_USDC = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
DATA_API_POSITIONS = "https://data-api.polymarket.com/positions"

_CTF_ABI = [
    {
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "indexSets", "type": "uint256[]"},
        ],
        "name": "redeemPositions",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"name": "owner", "type": "address"}, {"name": "id", "type": "uint256"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"name": "conditionId", "type": "bytes32"}],
        "name": "payoutDenominator",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"name": "conditionId", "type": "bytes32"}, {"name": "index", "type": "uint256"}],
        "name": "payoutNumerators",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _condition_bytes(condition_id: str) -> bytes:
    cond_hex = str(condition_id or "").strip()
    if not cond_hex:
        raise ValueError("missing condition_id")
    if not cond_hex.startswith("0x"):
        cond_hex = "0x" + cond_hex
    return bytes.fromhex(cond_hex.replace("0x", "").zfill(64))


@dataclass
class AutoRedeemer:
    private_key: str
    holder_address: str = ""
    query_address: str = ""
    rpc_url: str = DEFAULT_RPC
    usdc_address: str = DEFAULT_USDC
    claim_cooldown_sec: int = 14400
    user_agent: str = "PolyEdge/1.0"
    _cooldowns: dict[str, float] = field(default_factory=dict)

    def __post_init__(self):
        self.private_key = (self.private_key or "").strip()
        if not self.private_key:
            self.enabled = False
            self.signer_address = ""
            self.holder_address = (self.holder_address or "").strip()
            self.query_address = (self.query_address or self.holder_address or "").strip()
            self.web3 = None
            self.ctf = None
            self.disable_reason = "missing_private_key"
            return

        self.web3 = Web3(Web3.HTTPProvider(self.rpc_url or DEFAULT_RPC))
        signer = self.web3.eth.account.from_key(self.private_key)
        self.signer_address = Web3.to_checksum_address(signer.address)
        holder = (self.holder_address or self.signer_address).strip()
        self.holder_address = Web3.to_checksum_address(holder)
        query = (self.query_address or self.holder_address).strip()
        self.query_address = Web3.to_checksum_address(query) if query else ""
        self.usdc_address = Web3.to_checksum_address(self.usdc_address or DEFAULT_USDC)
        self.ctf = self.web3.eth.contract(
            address=Web3.to_checksum_address(CTF_ADDRESS),
            abi=_CTF_ABI,
        )
        # redeemPositions burns caller-held conditional tokens.
        self.enabled = self.signer_address.lower() == self.holder_address.lower()
        self.disable_reason = ""
        if not self.enabled:
            self.disable_reason = (
                f"holder_mismatch signer={self.signer_address} holder={self.holder_address}"
            )

    def _request_json(self, url: str) -> list[dict]:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://polymarket.com",
                "Referer": "https://polymarket.com/",
            },
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            if resp.status != 200:
                raise RuntimeError(f"http_{resp.status}")
            payload = json.loads(resp.read().decode("utf-8"))
        if not isinstance(payload, list):
            return []
        return [row for row in payload if isinstance(row, dict)]

    def fetch_positions(
        self,
        *,
        limit: int = 500,
        max_pages: int = 3,
        redeemable_only: bool | None = None,
        query_address: str = "",
    ) -> list[dict]:
        user = (query_address or self.query_address or "").strip()
        if not user:
            return []
        try:
            user = Web3.to_checksum_address(user)
        except Exception:
            return []
        safe_limit = max(1, min(int(limit), 2000))
        safe_pages = max(1, min(int(max_pages), 20))
        offset = 0
        rows: list[dict] = []
        for _ in range(safe_pages):
            query = urllib.parse.urlencode(
                {
                    "user": user,
                    "limit": safe_limit,
                    "offset": offset,
                    "sizeThreshold": 0,
                }
            )
            try:
                page = self._request_json(f"{DATA_API_POSITIONS}?{query}")
            except Exception as exc:
                logger.warning("Claim fetch positions failed: %s", exc)
                break
            if not page:
                break
            rows.extend(page)
            if len(page) < safe_limit:
                break
            offset += safe_limit

        filtered: list[dict] = []
        for pos in rows:
            is_redeemable = bool(pos.get("redeemable"))
            if redeemable_only is True and not is_redeemable:
                continue
            if redeemable_only is False and is_redeemable:
                continue
            size = _to_float(pos.get("size")) or 0.0
            token_id = str(pos.get("asset") or "").strip()
            condition_id = str(pos.get("conditionId") or "").strip()
            if size <= 0 or not token_id or not condition_id:
                continue
            filtered.append(pos)
        filtered.sort(key=lambda p: _to_float(p.get("size")) or 0.0, reverse=True)
        return filtered

    def fetch_redeemable_positions(
        self,
        *,
        limit: int = 500,
        max_pages: int = 3,
    ) -> list[dict]:
        return self.fetch_positions(
            limit=limit,
            max_pages=max_pages,
            redeemable_only=True,
        )

    def in_cooldown(self, token_id: str) -> bool:
        now = time.time()
        until = float(self._cooldowns.get(token_id, 0.0))
        if until > now:
            return True
        if token_id in self._cooldowns:
            del self._cooldowns[token_id]
        return False

    def set_cooldown(self, token_id: str, seconds: int | float):
        if not token_id:
            return
        sec = max(30, int(seconds))
        self._cooldowns[token_id] = time.time() + sec

    def redeem_position(self, pos: dict) -> dict:
        if not self.enabled:
            return {"ok": False, "error": self.disable_reason or "redeemer_disabled"}
        try:
            token_id = int(str(pos.get("asset") or "0"))
            condition_id = str(pos.get("conditionId") or "")
            outcome_index = int(pos.get("outcomeIndex", 0) or 0)
            cond_bytes = _condition_bytes(condition_id)

            balance = self.ctf.functions.balanceOf(self.holder_address, token_id).call()
            if int(balance) <= 0:
                return {"ok": False, "error": "zero_onchain_balance"}
            denom = self.ctf.functions.payoutDenominator(cond_bytes).call()
            if int(denom) <= 0:
                return {"ok": False, "error": "condition_not_resolved"}
            numer = self.ctf.functions.payoutNumerators(cond_bytes, int(outcome_index)).call()
            if int(numer) <= 0:
                return {"ok": False, "error": "outcome_lost"}

            payout_usdc = round((float(balance) * float(numer) / float(denom)) / 1_000_000.0, 6)

            try:
                priority_fee = int(self.web3.eth.max_priority_fee)
            except Exception:
                priority_fee = int(Web3.to_wei(120, "gwei"))
            base_fee = int(self.web3.eth.gas_price)
            max_fee = int(base_fee + (priority_fee * 2))
            nonce = int(self.web3.eth.get_transaction_count(self.signer_address))

            tx = self.ctf.functions.redeemPositions(
                self.usdc_address,
                b"\x00" * 32,
                cond_bytes,
                [1, 2],
            ).build_transaction(
                {
                    "from": self.signer_address,
                    "nonce": nonce,
                    "gas": 300000,
                    "maxFeePerGas": max_fee,
                    "maxPriorityFeePerGas": priority_fee,
                    "chainId": 137,
                }
            )
            signed = self.web3.eth.account.sign_transaction(tx, private_key=self.private_key)
            raw_tx = getattr(signed, "rawTransaction", None) or getattr(signed, "raw_transaction", None)
            if raw_tx is None:
                return {"ok": False, "error": "missing_raw_tx"}
            tx_hash = self.web3.eth.send_raw_transaction(raw_tx)
            receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)
            success = bool(getattr(receipt, "status", 0) == 1)
            return {
                "ok": success,
                "tx_hash": tx_hash.hex(),
                "gas_used": int(getattr(receipt, "gasUsed", 0)),
                "payout_usdc": payout_usdc,
                "error": "" if success else "tx_reverted",
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
