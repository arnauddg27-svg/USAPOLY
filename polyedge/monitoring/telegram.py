import os
import time
import logging
import requests

logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
_last_sent = 0

def _esc(t: str) -> str:
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def _send(text: str) -> bool:
    global _last_sent
    if not BOT_TOKEN or not CHAT_ID:
        return False
    now = time.time()
    if now - _last_sent < 1.0:
        time.sleep(1.0 - (now - _last_sent))
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        _last_sent = time.time()
        return r.ok
    except Exception as e:
        logger.warning("Telegram send failed: %s", e)
        return False

def edge_trade_placed(event: str, side: str, edge_pct: float, bet_usd: float, price: float):
    _send(f"<b>EDGE TRADE</b>\n{_esc(event)}\nSide: {side}\nEdge: {edge_pct:.1%}\nBet: ${bet_usd:.2f} @ {price:.4f}")

def trade_filled(event: str, order_id: str, fill_price: float, size: float):
    _send(f"<b>FILLED</b>\n{_esc(event)}\nOrder: {order_id[:12]}...\nFill: {fill_price:.4f} x {size:.0f}")

def trade_cancelled(event: str, reason: str):
    _send(f"<b>CANCELLED</b>\n{_esc(event)}\nReason: {_esc(reason)}")

def circuit_breaker(reason: str):
    _send(f"<b>CIRCUIT BREAKER</b>\n{_esc(reason)}")

def daily_summary(equity: float, pnl: float, trades: int, positions: int):
    _send(f"<b>DAILY SUMMARY</b>\nEquity: ${equity:.2f}\nP&L: ${pnl:+.2f}\nTrades: {trades}\nOpen: {positions}")

def bot_started():
    _send("<b>PolyEdge Bot Started</b>")

def bot_error(error: str):
    _send(f"<b>ERROR</b>\n{_esc(error)}")
