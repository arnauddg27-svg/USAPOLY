"""PolyEdge dashboard with simulation and runtime controls."""

import glob
import hmac
import json
import os
import sys
import urllib.parse
import urllib.request
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

# Ensure package imports work even when Streamlit executes this file directly.
APP_ROOT = Path(__file__).resolve().parent.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from polyedge.paths import (
    AUDIT_DIR,
    CONFIG_ENV_PATH,
    HEALTH_PATH,
    KILLSWITCH_PATH,
    RUNTIME_CONFIG_PATH,
    SIM_STATE_PATH,
)

ENV_PATH = CONFIG_ENV_PATH
ORDER_ACTIONS = {"SUBMITTED", "PLACED"}  # PLACED kept for backward compatibility in old logs.
EXECUTION_ACTIONS = ORDER_ACTIONS | {"SIMULATED"}

ALTAIR_THEME_NAME = "polyedge_light"
try:
    _decision_days_env = int(os.getenv("DASHBOARD_DECISION_DAYS", "1"))
except ValueError:
    _decision_days_env = 1
DECISION_LOAD_DAYS = max(1, min(_decision_days_env, 1))
try:
    _decision_lines_env = int(os.getenv("DASHBOARD_MAX_DECISION_LINES", "50000"))
except ValueError:
    _decision_lines_env = 50000
DECISION_MAX_LINES = max(5_000, min(_decision_lines_env, 100_000))
try:
    _dashboard_http_timeout_env = float(os.getenv("DASHBOARD_HTTP_TIMEOUT_SEC", "2.0"))
except ValueError:
    _dashboard_http_timeout_env = 2.0
DASHBOARD_HTTP_TIMEOUT_SEC = max(0.5, min(_dashboard_http_timeout_env, 10.0))


def _read_env_value(key: str) -> str:
    try:
        for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == key:
                return v.strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


def _read_json(path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    except Exception:
        pass
    return {}


def _fmt_usd(v) -> str:
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _fmt_pct(v) -> str:
    try:
        return f"{float(v):+.2f}%"
    except (TypeError, ValueError):
        return "0.00%"


def _to_float_or_none(v):
    if isinstance(v, pd.Series):
        non_null = v.dropna()
        if non_null.empty:
            return None
        v = non_null.iloc[0]
    elif isinstance(v, (list, tuple)):
        if not v:
            return None
        v = v[0]
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_float(v, default: float) -> float:
    parsed = _to_float_or_none(v)
    return float(parsed) if parsed is not None else float(default)


def _clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def _fmt_prob_pct(v) -> str:
    try:
        return f"{float(v) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _prob_to_american(prob) -> str:
    p = _to_float_or_none(prob)
    if p is None or p <= 0 or p >= 1:
        return "—"
    if p >= 0.5:
        odds = -100.0 * p / (1.0 - p)
        return f"{round(odds):d}"
    odds = 100.0 * (1.0 - p) / p
    return f"+{round(odds):d}"


def _window_df(df: pd.DataFrame, hours: int) -> pd.DataFrame:
    if df.empty or "timestamp" not in df.columns:
        return df.copy()
    cutoff = datetime.now(timezone.utc) - pd.Timedelta(hours=hours)
    return df[df["timestamp"] >= cutoff].copy()


def _parse_activity_timestamp(row: dict) -> datetime | None:
    for key in ("timestamp", "createdAt", "created_at", "blockTimestamp"):
        raw = row.get(key)
        if raw in (None, ""):
            continue
        try:
            if isinstance(raw, (int, float)):
                ts = float(raw)
                if ts > 1e12:
                    ts /= 1000.0
                return datetime.fromtimestamp(ts, tz=timezone.utc)
            if isinstance(raw, str):
                text = raw.strip()
                if not text:
                    continue
                maybe_num = _to_float_or_none(text)
                if maybe_num is not None:
                    ts = float(maybe_num)
                    if ts > 1e12:
                        ts /= 1000.0
                    return datetime.fromtimestamp(ts, tz=timezone.utc)
                ts = pd.to_datetime(text, utc=True, errors="coerce")
                if pd.notna(ts):
                    return ts.to_pydatetime()
        except Exception:
            continue
    return None


def _extract_activity_usd(row: dict) -> float:
    for key in ("usdcSize", "usdc_size", "amount_usdc", "amountUsd", "value"):
        val = _to_float_or_none(row.get(key))
        if val is not None and val >= 0:
            return float(val)

    size = _to_float_or_none(row.get("size"))
    if size is None:
        size = _to_float_or_none(row.get("shares"))
    price = _to_float_or_none(row.get("price"))
    if price is None:
        price = _to_float_or_none(row.get("outcomePrice"))
    if size is not None and price is not None and size >= 0 and price >= 0:
        return float(size * price)

    amount = _to_float_or_none(row.get("amount"))
    if amount is not None and amount >= 0:
        return float(amount)
    return 0.0


def _extract_activity_sport(row: dict) -> str:
    for key in ("sport", "sport_key", "sportKey"):
        val = str(row.get(key) or "").strip().lower()
        if val:
            return val
    return ""


def _sum_by_sport_token(values_by_sport: dict[str, int | float], token: str) -> int:
    total = 0.0
    for sport_key, value in values_by_sport.items():
        if _sport_matches_token(str(sport_key), token):
            v = _to_float_or_none(value)
            if v is not None:
                total += float(v)
    return int(round(total))


def _sport_prefix_for_token(token: str) -> str | None:
    t = str(token or "").strip().lower()
    if t.endswith("_all"):
        # soccer_all -> soccer_, tennis_all -> tennis_
        return t[:-3]
    if t.endswith("_*"):
        return t[:-1]
    # Family keys resolve to rotating league/tournament keys in Odds API.
    if t == "tennis_atp":
        return "tennis_atp_"
    if t == "tennis_wta":
        return "tennis_wta_"
    if t == "cricket":
        return "cricket_"
    if t == "rugby":
        return "rugby_"
    if t == "table_tennis":
        return "table_tennis_"
    return None


def _sport_matches_token(sport_value: str, token: str) -> bool:
    sport = str(sport_value or "").strip().lower()
    if not sport:
        return False
    token_norm = str(token or "").strip().lower()
    if not token_norm:
        return False
    if token_norm in {"rugby", "rugby_all", "rugby_*"}:
        return sport.startswith("rugby_") or sport.startswith("rugbyleague_")
    if token_norm in {"rugbyleague", "rugbyleague_all", "rugbyleague_*", "rugby_league", "rugby_league_all", "rugby_league_*"}:
        return sport.startswith("rugbyleague_")
    prefix = _sport_prefix_for_token(token_norm)
    if prefix is not None:
        return sport.startswith(prefix)
    return sport == token_norm


def _sport_slice(df: pd.DataFrame, token: str) -> pd.DataFrame:
    if df.empty or "sport" not in df.columns:
        return df.iloc[0:0].copy()
    mask = df["sport"].astype(str).apply(lambda s: _sport_matches_token(s, token))
    return df[mask].copy()


def _edge_breakdown_text(row: pd.Series) -> str:
    true_prob = _to_float_or_none(row.get("true_prob"))
    fill_prob = _to_float_or_none(row.get("poly_fill"))
    raw_edge = _to_float_or_none(row.get("raw_edge"))
    adj_edge = _to_float_or_none(row.get("adjusted_edge"))
    if None in (true_prob, fill_prob, raw_edge, adj_edge):
        return "—"
    haircut_pp = (raw_edge - adj_edge) * 100.0
    return (
        f"{true_prob * 100:.1f}% ({_prob_to_american(true_prob)}) - "
        f"{fill_prob * 100:.1f}% - haircut {haircut_pp:.1f}pp = {adj_edge * 100:.1f}pp"
    )


def _query_param_get(name: str, default: str = "") -> str:
    try:
        raw = st.query_params.get(name, default)
        if isinstance(raw, list):
            return str(raw[0]) if raw else default
        return str(raw)
    except Exception:
        try:
            return str(st.experimental_get_query_params().get(name, [default])[0])
        except Exception:
            return default


def _query_param_set(name: str, value: str) -> None:
    try:
        st.query_params[name] = value
    except Exception:
        try:
            st.experimental_set_query_params(**{name: value})
        except Exception:
            pass


def _has_action(df: pd.DataFrame, action: str) -> pd.Series:
    if "action" not in df.columns:
        return pd.Series([False] * len(df), index=df.index)
    return df["action"] == action


def _has_actions(df: pd.DataFrame, actions: set[str]) -> pd.Series:
    if "action" not in df.columns:
        return pd.Series([False] * len(df), index=df.index)
    return df["action"].isin(actions)


def _to_excel_bytes(df: pd.DataFrame) -> bytes:
    safe = df.copy()
    for col in safe.columns:
        if pd.api.types.is_datetime64_any_dtype(safe[col]):
            safe[col] = pd.to_datetime(safe[col], utc=True, errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")
            continue
        if safe[col].apply(lambda v: isinstance(v, (dict, list))).any():
            safe[col] = safe[col].apply(
                lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
            )

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        safe.to_excel(writer, index=False, sheet_name="decision_log")
    return output.getvalue()


def _prepare_decision_log_df(source_df: pd.DataFrame) -> pd.DataFrame:
    prepared = source_df.copy()

    if {"buy_outcome", "outcome_a", "outcome_b"}.issubset(prepared.columns):
        prepared["selected_outcome"] = prepared.apply(
            lambda row: row["outcome_a"] if row["buy_outcome"] == "a" else row["outcome_b"],
            axis=1,
        )
    elif "buy_outcome" in prepared.columns:
        prepared["selected_outcome"] = prepared["buy_outcome"].fillna("").astype(str).str.upper()

    if "true_prob" in prepared.columns:
        prepared["consensus_prob"] = prepared["true_prob"].apply(_fmt_prob_pct)
        prepared["consensus_odds"] = prepared["true_prob"].apply(_prob_to_american)
    if {"agg_prob_a", "agg_prob_b"}.issubset(prepared.columns):
        prepared["agg_market"] = prepared.apply(
            lambda row: f"A {row['agg_prob_a'] * 100:.1f}% vs B {row['agg_prob_b'] * 100:.1f}%"
            if pd.notna(row["agg_prob_a"]) and pd.notna(row["agg_prob_b"])
            else "—",
            axis=1,
        )
    if "poly_fill" in prepared.columns:
        prepared["poly_fill_prob"] = prepared["poly_fill"].apply(_fmt_prob_pct)
    if "adjusted_edge" in prepared.columns:
        prepared["edge_pp"] = (prepared["adjusted_edge"] * 100).round(2).astype(str) + "pp"
    if {"raw_edge", "adjusted_edge", "true_prob", "poly_fill"}.issubset(prepared.columns):
        prepared["edge_breakdown"] = [_edge_breakdown_text(row) for _, row in prepared.iterrows()]

    cols = [
        "timestamp",
        "action",
        "event",
        "sport",
        "market_type",
        "selected_outcome",
        "edge_breakdown",
        "reject_reason",
        "consensus_prob",
        "consensus_odds",
        "poly_fill_prob",
        "edge_pp",
        "books_used",
        "bet_usd",
        "condition_id",
        "agg_market",
        "market_question",
        "event_start",
        "sim_expected_pnl_usd",
        "sim_bankroll_after_usd",
    ]
    cols = [c for c in cols if c in prepared.columns]
    return prepared[cols].copy()


def _safe_dataframe(df: pd.DataFrame, *, use_container_width: bool = True, hide_index: bool = True) -> None:
    try:
        st.dataframe(df, use_container_width=use_container_width, hide_index=hide_index)
        return
    except Exception:
        safe = df.copy()
        for col in safe.columns:
            if pd.api.types.is_datetime64_any_dtype(safe[col]):
                safe[col] = pd.to_datetime(safe[col], utc=True, errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")
                continue
            if safe[col].dtype == "object":
                safe[col] = safe[col].apply(
                    lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else ("" if v is None else str(v))
                )
        st.dataframe(safe, use_container_width=use_container_width, hide_index=hide_index)


def _decision_file_date(path: str):
    stem = Path(path).stem
    if not stem.startswith("decisions_"):
        return None
    day_str = stem[len("decisions_") :]
    try:
        return datetime.strptime(day_str, "%Y-%m-%d").date()
    except ValueError:
        return None


def _iter_jsonl_lines_reverse(path: str, block_size: int = 1024 * 1024):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            position = fh.tell()
            pending = b""
            while position > 0:
                read_size = block_size if position >= block_size else position
                position -= read_size
                fh.seek(position)
                chunk = fh.read(read_size)
                parts = (chunk + pending).split(b"\n")
                pending = parts[0]
                for raw in reversed(parts[1:]):
                    yield raw.decode("utf-8", errors="ignore")
            if pending:
                yield pending.decode("utf-8", errors="ignore")
    except OSError:
        return


def _parse_timestamp_utc(raw_ts) -> datetime | None:
    if raw_ts is None:
        return None
    text = str(raw_ts).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@st.cache_data(ttl=8, show_spinner=False)
def load_decisions(days: int = 7, max_lines: int | None = None) -> pd.DataFrame:
    files = sorted(glob.glob(str(AUDIT_DIR / "decisions_*.jsonl")))
    safe_days = max(1, int(days))
    cutoff_date = (datetime.now(timezone.utc) - pd.Timedelta(days=safe_days - 1)).date()
    cutoff_ts = datetime.now(timezone.utc) - pd.Timedelta(days=safe_days)
    safe_max_lines = DECISION_MAX_LINES if max_lines is None else max(10_000, min(int(max_lines), 1_000_000))
    selected_files = []
    for path in files:
        file_day = _decision_file_date(path)
        # Keep unknown filenames to avoid accidental drops when rotation format changes.
        if file_day is None or file_day >= cutoff_date:
            selected_files.append(path)

    rows = []
    lines_read = 0
    reached_cutoff = False
    for path in reversed(selected_files):
        for line in _iter_jsonl_lines_reverse(path):
            if lines_read >= safe_max_lines:
                reached_cutoff = True
                break
            line = line.strip()
            if not line:
                continue
            lines_read += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _parse_timestamp_utc(row.get("timestamp"))
            if ts is not None and ts < cutoff_ts:
                reached_cutoff = True
                break
            rows.append(row)
        if reached_cutoff:
            break

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    if "action" in df.columns:
        # Normalize historical log variants (case/whitespace) so counters stay accurate.
        action_norm = df["action"].where(df["action"].notna(), "")
        df["action"] = action_norm.astype(str).str.strip().str.upper()
    if "simulation" in df.columns:
        sim = df["simulation"].apply(lambda x: x if isinstance(x, dict) else {})
        for key in (
            "stake_usd",
            "expected_pnl_usd",
            "bankroll_before_usd",
            "bankroll_after_usd",
            "bet_count",
        ):
            df[f"sim_{key}"] = sim.apply(lambda x: x.get(key))

    numeric_cols = (
        "raw_edge",
        "adjusted_edge",
        "agg_prob_a",
        "agg_prob_b",
        "true_prob",
        "poly_fill",
        "poly_mid",
        "poly_spread",
        "poly_depth",
        "books_used",
        "bet_usd",
        "sim_stake_usd",
        "sim_expected_pnl_usd",
        "sim_bankroll_before_usd",
        "sim_bankroll_after_usd",
    )
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "timestamp" in df.columns:
        df = df.sort_values("timestamp", ascending=False)
    return df


@st.cache_data(ttl=3, show_spinner=False)
def load_runtime_config() -> dict:
    return _read_json(RUNTIME_CONFIG_PATH)


@st.cache_data(ttl=120, show_spinner=False)
def load_positions_summary(user_address: str, limit: int = 500, max_pages: int = 5) -> dict:
    """Fetch portfolio positions and compute active positions value for equity cards."""
    address = (user_address or "").strip()
    if not address:
        return {"fetched": False, "error": "missing_address"}

    base_url = "https://data-api.polymarket.com/positions"
    all_positions: list[dict] = []
    seen_assets: set[str] = set()
    offset = 0
    safe_limit = max(1, min(int(limit), 2000))
    safe_pages = max(1, min(int(max_pages), 20))

    for _ in range(safe_pages):
        query = urllib.parse.urlencode(
            {
                "user": address,
                "limit": safe_limit,
                "offset": offset,
                "sizeThreshold": 0,
            }
        )
        req = urllib.request.Request(
            f"{base_url}?{query}",
            headers={"User-Agent": "PolyEdge-Dashboard/1.0"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=DASHBOARD_HTTP_TIMEOUT_SEC) as resp:
                if resp.status != 200:
                    return {
                        "fetched": False,
                        "error": f"http_{resp.status}",
                    }
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            return {"fetched": False, "error": str(exc)}

        if not isinstance(payload, list) or not payload:
            break

        for pos in payload:
            if not isinstance(pos, dict):
                continue
            asset = str(pos.get("asset") or "")
            if asset:
                if asset in seen_assets:
                    continue
                seen_assets.add(asset)
            all_positions.append(pos)

        if len(payload) < safe_limit:
            break
        offset += safe_limit

    open_positions_value_usd = 0.0
    open_positions_count = 0
    for pos in all_positions:
        cur_price = _to_float_or_none(pos.get("curPrice"))
        size = _to_float_or_none(pos.get("size"))
        current_value = _to_float_or_none(pos.get("currentValue"))
        if current_value is None and cur_price is not None and size is not None:
            current_value = cur_price * size
        current_value = max(0.0, float(current_value or 0.0))

        is_open = (
            size is not None
            and size > 0.0
            and cur_price is not None
            and 0.001 < cur_price < 0.999
            and current_value > 0.01
        )
        if is_open:
            open_positions_count += 1
            open_positions_value_usd += current_value

    return {
        "fetched": True,
        "open_positions_count": open_positions_count,
        "open_positions_value_usd": round(open_positions_value_usd, 2),
        "rows": len(all_positions),
        "address": address,
    }


@st.cache_data(ttl=120, show_spinner=False)
def load_unsettled_value(user_address: str) -> dict:
    """Fetch unsettled/claimable value from Polymarket value endpoint."""
    address = (user_address or "").strip()
    if not address:
        return {"fetched": False, "error": "missing_address"}
    query = urllib.parse.urlencode({"user": address})
    req = urllib.request.Request(
        f"https://data-api.polymarket.com/value?{query}",
        headers={"User-Agent": "PolyEdge-Dashboard/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=DASHBOARD_HTTP_TIMEOUT_SEC) as resp:
            if resp.status != 200:
                return {"fetched": False, "error": f"http_{resp.status}"}
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"fetched": False, "error": str(exc)}

    values = payload if isinstance(payload, list) else []
    total = 0.0
    for row in values:
        if not isinstance(row, dict):
            continue
        v = _to_float_or_none(row.get("value"))
        if v is not None and v > 0:
            total += float(v)
    return {
        "fetched": True,
        "address": address,
        "rows": len(values),
        "unsettled_value_usd": round(total, 2),
    }


@st.cache_data(ttl=45, show_spinner=False)
def load_exchange_activity_summary(
    user_address: str,
    session_start_epoch: float | None = None,
    lookback_hours: int = 24,
    limit: int = 200,
    max_pages: int = 3,
) -> dict:
    """Fetch recent exchange activity (fills) for the wallet."""
    address = (user_address or "").strip()
    if not address:
        return {"fetched": False, "error": "missing_address"}

    safe_limit = max(1, min(int(limit), 500))
    safe_pages = max(1, min(int(max_pages), 20))
    offset = 0
    rows: list[dict] = []
    seen_ids: set[str] = set()
    base_url = "https://data-api.polymarket.com/activity"

    for _ in range(safe_pages):
        query = urllib.parse.urlencode({"user": address, "limit": safe_limit, "offset": offset})
        req = urllib.request.Request(
            f"{base_url}?{query}",
            headers={"User-Agent": "PolyEdge-Dashboard/1.0"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=DASHBOARD_HTTP_TIMEOUT_SEC) as resp:
                if resp.status != 200:
                    return {"fetched": False, "error": f"http_{resp.status}"}
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            return {"fetched": False, "error": str(exc)}

        if not isinstance(payload, list) or not payload:
            break

        for raw in payload:
            if not isinstance(raw, dict):
                continue
            row_id = str(raw.get("id") or raw.get("transactionHash") or "")
            if row_id:
                if row_id in seen_ids:
                    continue
                seen_ids.add(row_id)
            rows.append(raw)

        if len(payload) < safe_limit:
            break
        offset += safe_limit

    now_utc = datetime.now(timezone.utc)
    cutoff_24h = now_utc - pd.Timedelta(hours=max(1, int(lookback_hours)))
    session_cutoff = None
    session_cutoff_source = "session_start"
    if session_start_epoch is not None:
        try:
            session_cutoff = datetime.fromtimestamp(float(session_start_epoch), tz=timezone.utc)
        except Exception:
            session_cutoff = None
    if session_cutoff is None:
        # Bound session metrics to lookback window when bot start is unavailable.
        session_cutoff = cutoff_24h
        session_cutoff_source = "lookback_fallback"

    fills_24h = 0
    fills_session = 0
    fill_volume_usd_24h = 0.0
    fill_volume_usd_session = 0.0
    fills_by_sport_24h: dict[str, int] = {}
    fills_by_sport_session: dict[str, int] = {}

    for row in rows:
        side = str(row.get("side") or row.get("type") or "").strip().upper()
        if side not in {"BUY", "SELL", "MARKET_BUY", "MARKET_SELL"}:
            continue
        ts = _parse_activity_timestamp(row)
        if ts is None:
            continue
        amt_usd = _extract_activity_usd(row)
        sport = _extract_activity_sport(row)

        if ts >= cutoff_24h:
            fills_24h += 1
            fill_volume_usd_24h += amt_usd
            if sport:
                fills_by_sport_24h[sport] = fills_by_sport_24h.get(sport, 0) + 1
        if ts >= session_cutoff:
            fills_session += 1
            fill_volume_usd_session += amt_usd
            if sport:
                fills_by_sport_session[sport] = fills_by_sport_session.get(sport, 0) + 1

    return {
        "fetched": True,
        "address": address,
        "rows_scanned": len(rows),
        "fills_24h": int(fills_24h),
        "fills_session": int(fills_session),
        "fill_volume_usd_24h": round(fill_volume_usd_24h, 2),
        "fill_volume_usd_session": round(fill_volume_usd_session, 2),
        "fills_by_sport_24h": fills_by_sport_24h,
        "fills_by_sport_session": fills_by_sport_session,
        "session_cutoff_source": session_cutoff_source,
    }


def save_runtime_config(cfg: dict):
    RUNTIME_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    merged = load_runtime_config()
    merged.update(cfg)
    tmp_path = RUNTIME_CONFIG_PATH.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    tmp_path.replace(RUNTIME_CONFIG_PATH)
    load_runtime_config.clear()


def reset_simulation_state(start_bankroll: float):
    payload = {
        "start_bankroll": float(start_bankroll),
        "current_bankroll": float(start_bankroll),
        "total_staked": 0.0,
        "expected_pnl": 0.0,
        "bet_count": 0,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    SIM_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SIM_STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(SIM_STATE_PATH)


def is_killswitch_active() -> bool:
    return KILLSWITCH_PATH.exists()


def activate_killswitch():
    KILLSWITCH_PATH.parent.mkdir(parents=True, exist_ok=True)
    KILLSWITCH_PATH.write_text(
        json.dumps({"paused_at": datetime.now(timezone.utc).isoformat()}),
        encoding="utf-8",
    )


def deactivate_killswitch():
    if KILLSWITCH_PATH.exists():
        KILLSWITCH_PATH.unlink()


def check_password() -> bool:
    pw = os.getenv("DASHBOARD_PASSWORD", "").strip() or _read_env_value("DASHBOARD_PASSWORD")
    allow_no_password = os.getenv("DASHBOARD_ALLOW_NO_PASSWORD", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not pw:
        if allow_no_password:
            st.warning("Dashboard auth disabled via `DASHBOARD_ALLOW_NO_PASSWORD=1`.")
            return True
        st.error("`DASHBOARD_PASSWORD` is not set. Refusing to run unsecured dashboard controls.")
        st.caption("Set `DASHBOARD_PASSWORD` in config/env.")
        return False
    if st.session_state.get("authenticated"):
        return True
    with st.form("login"):
        st.text_input("Dashboard Password", type="password", key="password_input")
        if st.form_submit_button("Login"):
            if hmac.compare_digest(st.session_state.password_input, pw):
                st.session_state.authenticated = True
                st.rerun()
            st.error("Incorrect password")
    return False


def inject_theme():
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=JetBrains+Mono:wght@500&display=swap');
        :root {
          --bg-1: #f4f7fb;
          --bg-2: #edf3fa;
          --ink: #0f172a;
          --muted: #475569;
          --card: #ffffff;
          --line: #d6dfeb;
          --accent: #0ea5a4;
          --accent-2: #0284c7;
        }
        html, body, [class*="css"] {
          font-family: 'Space Grotesk', sans-serif;
        }
        .stApp {
          --text-color: var(--ink);
          --background-color: var(--bg-1);
          --secondary-background-color: var(--card);
          --primary-color: var(--accent);
          color-scheme: light !important;
          background:
            radial-gradient(860px 380px at -8% -10%, #d8f6ef 0%, transparent 58%),
            radial-gradient(760px 320px at 104% 0%, #dbeafe 0%, transparent 54%),
            linear-gradient(180deg, var(--bg-1) 0%, var(--bg-2) 100%);
          color: var(--ink);
        }
        .block-container {
          max-width: 1240px;
          padding-top: 1.3rem;
          padding-bottom: 2.4rem;
        }
        [data-testid="stHeader"] {
          background: rgba(244, 247, 251, 0.92);
          backdrop-filter: blur(6px);
        }
        [data-testid="stToolbar"] {
          background: transparent;
        }
        div[data-testid="stMetric"] {
          background: linear-gradient(180deg, #ffffff 0%, #f8fbff 100%);
          border: 1px solid var(--line);
          border-radius: 16px;
          padding: 14px 16px;
          box-shadow: 0 10px 24px rgba(15, 23, 42, 0.04);
        }
        div[data-testid="stMetricLabel"] {
          font-size: 0.76rem;
          text-transform: uppercase;
          letter-spacing: 0.06em;
          color: #64748b;
        }
        div[data-testid="stMetricValue"] {
          font-size: 2rem;
          letter-spacing: -0.02em;
          color: var(--ink);
        }
        div[data-testid="stMetricDelta"] {
          color: var(--muted);
        }
        .hero {
          position: relative;
          overflow: hidden;
          border: 1px solid var(--line);
          border-radius: 20px;
          background: linear-gradient(115deg, #ffffff 0%, #ecfeff 38%, #eef2ff 100%);
          padding: 20px 22px;
          margin-bottom: 12px;
          box-shadow: 0 14px 30px rgba(14, 116, 144, 0.08);
        }
        .hero::after {
          content: "";
          position: absolute;
          right: -40px;
          top: -60px;
          width: 240px;
          height: 240px;
          border-radius: 999px;
          background: radial-gradient(circle, rgba(14, 165, 164, 0.16), rgba(14, 165, 164, 0.0) 70%);
          pointer-events: none;
        }
        .hero h1 {
          margin: 0;
          color: var(--ink);
          font-size: 2.35rem;
          line-height: 1.05;
          letter-spacing: -0.03em;
        }
        .hero p {
          margin: 10px 0 0 0;
          color: var(--muted);
          font-size: 1.0rem;
          max-width: 760px;
        }
        .chip {
          display: inline-block;
          margin-right: 8px;
          margin-top: 6px;
          padding: 4px 10px;
          border-radius: 999px;
          font-size: 0.78rem;
          border: 1px solid var(--line);
          background: #fff;
          color: #1e293b;
        }
        .chip.ok {
          border-color: #7dd3fc;
          background: #e0f2fe;
          color: #075985;
        }
        .chip.warn {
          border-color: #fcd34d;
          background: #fffbeb;
          color: #92400e;
        }
        .stTabs [data-baseweb="tab-list"] {
          gap: 10px;
        }
        .stTabs [data-baseweb="tab"] {
          font-weight: 600;
          border-radius: 10px 10px 0 0;
          color: #334155;
        }
        .stTabs [aria-selected="true"] {
          color: #0f172a;
        }
        .stButton > button {
          border-radius: 10px;
          border: 1px solid #0369a1;
          background: #0369a1;
          color: #ffffff;
          font-weight: 600;
        }
        .stButton > button:hover {
          border-color: #075985;
          background: #075985;
          color: #ffffff;
        }
        .stButton > button:disabled {
          border-color: #cbd5e1;
          background: #f1f5f9;
          color: #94a3b8;
        }
        div[data-baseweb="select"] > div,
        .stSlider > div > div {
          border-radius: 10px;
        }
        div[data-baseweb="select"] > div {
          background: #ffffff !important;
          border: 1px solid var(--line) !important;
          color: var(--ink) !important;
        }
        div[data-baseweb="select"] span,
        div[data-baseweb="select"] input,
        div[data-baseweb="select"] svg {
          color: var(--ink) !important;
          fill: var(--ink) !important;
        }
        div[data-baseweb="popover"] div[data-baseweb="menu"] {
          background: #ffffff !important;
          border: 1px solid var(--line) !important;
          color: var(--ink) !important;
        }
        div[data-baseweb="popover"] div[data-baseweb="menu"] * {
          color: var(--ink) !important;
        }
        [data-testid="stMarkdownContainer"],
        [data-testid="stCaptionContainer"] {
          color: var(--ink);
        }
        [data-testid="stDataFrame"] {
          border: 1px solid var(--line);
          border-radius: 12px;
          background: #ffffff;
        }
        [data-testid="stDataFrame"] * {
          color: var(--ink) !important;
        }
        [data-testid="stVegaLiteChart"] {
          border: 1px solid var(--line);
          border-radius: 12px;
          background: #ffffff;
          padding: 8px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def enable_altair_theme():
    theme = {
        "config": {
            "background": "#ffffff",
            "view": {"stroke": "transparent"},
            "axis": {
                "labelColor": "#334155",
                "titleColor": "#0f172a",
                "gridColor": "#e2e8f0",
                "domainColor": "#cbd5e1",
                "tickColor": "#cbd5e1",
            },
            "legend": {
                "labelColor": "#334155",
                "titleColor": "#0f172a",
            },
            "title": {"color": "#0f172a"},
            "style": {
                "guide-label": {"font": "Space Grotesk"},
                "guide-title": {"font": "Space Grotesk"},
            },
        }
    }
    try:
        alt.themes.register(ALTAIR_THEME_NAME, lambda: theme)
    except Exception:
        pass
    try:
        alt.themes.enable(ALTAIR_THEME_NAME)
    except Exception:
        pass


st.set_page_config(page_title="PolyEdge", page_icon="📈", layout="wide")
inject_theme()
enable_altair_theme()

if not check_password():
    st.stop()

runtime = load_runtime_config()
health = _read_json(HEALTH_PATH)
sim_state = _read_json(SIM_STATE_PATH)
df = load_decisions(days=DECISION_LOAD_DAYS)
recent_df_all = _window_df(df, hours=24)
session_start_global = pd.to_datetime(health.get("started_at"), utc=True, errors="coerce")
if pd.notna(session_start_global) and "timestamp" in df.columns:
    session_df_all = df[df["timestamp"] >= session_start_global].copy()
else:
    session_df_all = recent_df_all.copy()

if "auto_refresh_enabled" not in st.session_state:
    st.session_state.auto_refresh_enabled = _query_param_get("auto_refresh", "0").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
if "external_api_enabled" not in st.session_state:
    st.session_state.external_api_enabled = _query_param_get("external_api", "0").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

sim_mode = bool(runtime.get("SIMULATION_MODE", True))
trading_enabled = bool(runtime.get("TRADING_ENABLED", False))
dry_run = bool(health.get("dry_run", True))

if sim_mode:
    mode = "Simulation"
elif trading_enabled and not dry_run:
    mode = "Live"
elif trading_enabled and dry_run:
    mode = "Dry Run (Live Blocked)"
else:
    mode = "Dry Run"
paused = is_killswitch_active()
status_chip = "warn" if paused else "ok"
status_text = "Paused" if paused else "Running"
sports_active = health.get("sports")
if not isinstance(sports_active, list) or not sports_active:
    sports_raw = runtime.get("SPORTS")
    if isinstance(sports_raw, list):
        sports_active = sports_raw
    else:
        sports_active = [s.strip() for s in _read_env_value("SPORTS").split(",") if s.strip()]
sports_text = ", ".join(sports_active[:4]) if sports_active else "n/a"

st.markdown(
    f"""
    <div class="hero">
      <h1>PolyEdge Control Room</h1>
      <p>Opportunity feed for live order decisions, exposure, and execution quality.</p>
      <span class="chip {status_chip}">Status: {status_text}</span>
      <span class="chip">Mode: {mode}</span>
      <span class="chip">Cycle: {health.get("cycle", 0)}</span>
      <span class="chip">Sports: {sports_text}</span>
    </div>
    """,
    unsafe_allow_html=True,
)

ctrl_a, ctrl_b, ctrl_c = st.columns([1, 1, 2])
with ctrl_a:
    if paused:
        if st.button("Resume Bot", type="primary", use_container_width=True):
            deactivate_killswitch()
            st.rerun()
    else:
        if st.button("Pause Bot", type="secondary", use_container_width=True):
            activate_killswitch()
            st.rerun()
with ctrl_b:
    if st.button("Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
with ctrl_c:
    auto_refresh = st.checkbox("Auto-refresh every 20s", key="auto_refresh_enabled")
    qp_value = "1" if auto_refresh else "0"
    if _query_param_get("auto_refresh", "0") != qp_value:
        _query_param_set("auto_refresh", qp_value)
    external_api_enabled = st.checkbox("Live API cards", key="external_api_enabled")
    external_qp_value = "1" if external_api_enabled else "0"
    if _query_param_get("external_api", "0") != external_qp_value:
        _query_param_set("external_api", external_qp_value)
    if auto_refresh:
        st.markdown('<meta http-equiv="refresh" content="20">', unsafe_allow_html=True)

tab_overview, tab_decisions, tab_config = st.tabs(["Overview", "Decisions", "Config"])

with tab_overview:
    external_api_enabled = bool(st.session_state.get("external_api_enabled", False))
    recent_df = recent_df_all.copy()
    session_df = session_df_all.copy()
    session_start = session_start_global
    configured_sports = [str(s).strip() for s in sports_active if str(s).strip()]
    total_window = len(df)
    window_label = f"{DECISION_LOAD_DAYS}d"
    total_24h = len(recent_df)
    total_session = len(session_df)
    submitted_window = int(_has_actions(df, ORDER_ACTIONS).sum())
    submitted_24h = int(_has_actions(recent_df, ORDER_ACTIONS).sum())
    submitted_session = int(_has_actions(session_df, ORDER_ACTIONS).sum())
    simulated_window = int(_has_action(df, "SIMULATED").sum())
    simulated_24h = int(_has_action(recent_df, "SIMULATED").sum())
    simulated_session = int(_has_action(session_df, "SIMULATED").sum())
    rejected_window = int(_has_action(df, "REJECTED").sum())
    rejected_24h = int(_has_action(recent_df, "REJECTED").sum())
    rejected_session = int(_has_action(session_df, "REJECTED").sum())
    dry_run_window = int(_has_action(df, "DRY_RUN").sum())
    dry_run_24h = int(_has_action(recent_df, "DRY_RUN").sum())
    dry_run_session = int(_has_action(session_df, "DRY_RUN").sum())
    sport_activity_rows: list[dict] = []
    sports_traded_24h = 0
    sports_traded_session = 0
    if "sport" in recent_df.columns or "sport" in session_df.columns:
        session_sports = session_df["sport"].dropna().astype(str).tolist() if "sport" in session_df.columns else []
        recent_sports = recent_df["sport"].dropna().astype(str).tolist() if "sport" in recent_df.columns else []
        all_sports = configured_sports or sorted(set(session_sports) | set(recent_sports))
        for sport_name in all_sports:
            sport_24h_df = _sport_slice(recent_df, sport_name)
            sport_df = _sport_slice(session_df, sport_name)
            decisions_24h = len(sport_24h_df)
            decisions_session = len(sport_df)
            submitted_sport_24h = int(_has_actions(sport_24h_df, ORDER_ACTIONS).sum())
            submitted_sport_session = int(_has_actions(sport_df, ORDER_ACTIONS).sum())
            rejected_sport_24h = int(_has_action(sport_24h_df, "REJECTED").sum())
            rejected_sport_session = int(_has_action(sport_df, "REJECTED").sum())
            if submitted_sport_24h > 0:
                sports_traded_24h += 1
            if submitted_sport_session > 0:
                sports_traded_session += 1
            sport_activity_rows.append(
                {
                    "sport": sport_name,
                    "submitted_session": submitted_sport_session,
                    "rejected_session": rejected_sport_session,
                    "decisions_session": decisions_session,
                    "submitted_24h": submitted_sport_24h,
                    "rejected_24h": rejected_sport_24h,
                    "decisions_24h": decisions_24h,
                }
            )

    submitted_df = recent_df[_has_actions(recent_df, ORDER_ACTIONS)].copy()
    simulated_df = recent_df[_has_action(recent_df, "SIMULATED")].copy()
    if "bet_usd" in submitted_df.columns:
        submitted_bets = submitted_df["bet_usd"].dropna()
        submitted_bets = submitted_bets[submitted_bets > 0]
        avg_submitted_bet = float(submitted_bets.mean()) if not submitted_bets.empty else 0.0
    else:
        avg_submitted_bet = 0.0
    if "bet_usd" in simulated_df.columns:
        sim_bets = simulated_df["bet_usd"].dropna()
        sim_bets = sim_bets[sim_bets > 0]
        avg_sim_bet = float(sim_bets.mean()) if not sim_bets.empty else 0.0
    else:
        avg_sim_bet = 0.0

    start_bankroll = _safe_float(
        sim_state.get("start_bankroll"),
        _safe_float(runtime.get("SIMULATION_START_BANKROLL"), 1000.0),
    )
    current_bankroll = _safe_float(sim_state.get("current_bankroll"), start_bankroll)
    expected_pnl = _safe_float(sim_state.get("expected_pnl"), 0.0)
    pnl_pct = (expected_pnl / start_bankroll * 100.0) if start_bankroll > 0 else 0.0
    invested_usd = _to_float_or_none(health.get("invested_usd")) or 0.0
    exchange_open_orders_count = _to_float_or_none(health.get("exchange_open_orders_count"))
    exchange_open_orders_notional_usd = _to_float_or_none(health.get("exchange_open_orders_notional_usd"))
    tracked_open_orders_count = _to_float_or_none(health.get("tracked_open_orders_count"))
    tracked_open_orders_notional_usd = _to_float_or_none(health.get("tracked_open_orders_notional_usd"))
    open_orders_count = int(
        exchange_open_orders_count
        if exchange_open_orders_count is not None
        else (health.get("open_orders_count", 0) or 0)
    )
    open_orders_notional_usd = (
        exchange_open_orders_notional_usd
        if exchange_open_orders_notional_usd is not None
        else (_to_float_or_none(health.get("open_orders_notional_usd")) or 0.0)
    )
    wallet_balance_usd = _to_float_or_none(health.get("wallet_balance_usd"))
    wallet_start_usd = _to_float_or_none(health.get("wallet_start_usd"))
    live_pnl_usd = _to_float_or_none(health.get("pnl_usd"))
    claims_today = int(_to_float_or_none(health.get("claims_today")) or 0)
    claimed_usdc_today = _to_float_or_none(health.get("claimed_usdc_today")) or 0.0
    portfolio_address = (
        str(health.get("wallet_address") or "").strip()
        or _read_env_value("POLY_FUNDER_ADDRESS")
        or _read_env_value("POLY_ADDRESS")
    )
    positions_summary = {"fetched": False, "error": "not_needed"}
    open_positions_value_usd = _to_float_or_none(health.get("open_positions_value_usd"))
    open_positions_count_raw = _to_float_or_none(health.get("open_positions_count"))
    needs_positions_fallback = (
        external_api_enabled
        and
        not sim_mode
        and bool(portfolio_address)
        and (open_positions_value_usd is None or open_positions_count_raw is None)
    )
    if needs_positions_fallback:
        positions_summary = load_positions_summary(portfolio_address, limit=100, max_pages=1)
        if open_positions_value_usd is None and positions_summary.get("fetched"):
            open_positions_value_usd = _to_float_or_none(positions_summary.get("open_positions_value_usd"))
        if open_positions_count_raw is None and positions_summary.get("fetched"):
            open_positions_count_raw = _to_float_or_none(positions_summary.get("open_positions_count"))
    open_positions_count = int(open_positions_count_raw or 0)
    total_equity_usd = _to_float_or_none(health.get("total_equity_usd"))
    if total_equity_usd is None and wallet_balance_usd is not None and open_positions_value_usd is not None:
        total_equity_usd = wallet_balance_usd + open_positions_value_usd
    unsettled_summary = (
        load_unsettled_value(portfolio_address)
        if (external_api_enabled and not sim_mode and portfolio_address)
        else {"fetched": False, "error": "simulation_or_missing_address"}
    )
    unsettled_value_usd = (
        _to_float_or_none(unsettled_summary.get("unsettled_value_usd"))
        if unsettled_summary.get("fetched")
        else None
    )
    account_equity_estimate_usd = (
        total_equity_usd + unsettled_value_usd
        if total_equity_usd is not None and unsettled_value_usd is not None
        else None
    )
    live_pnl_pct = (
        (live_pnl_usd / wallet_start_usd * 100.0)
        if live_pnl_usd is not None and wallet_start_usd not in (None, 0.0)
        else None
    )
    fills_summary = {"fetched": False, "error": "simulation_or_missing_address"}
    fills_session_cutoff_source = "session_start"
    fills_24h = 0
    fills_session = 0
    fill_volume_usd_24h = 0.0
    fill_volume_usd_session = 0.0
    fills_by_sport_24h: dict[str, int] = {}
    fills_by_sport_session: dict[str, int] = {}
    if external_api_enabled and not sim_mode and portfolio_address:
        session_start_epoch = (
            float(session_start.timestamp())
            if pd.notna(session_start)
            else None
        )
        fills_summary = load_exchange_activity_summary(
            portfolio_address,
            session_start_epoch=session_start_epoch,
            lookback_hours=24,
            limit=100,
            max_pages=1,
        )
        if fills_summary.get("fetched"):
            fills_24h = int(fills_summary.get("fills_24h") or 0)
            fills_session = int(fills_summary.get("fills_session") or 0)
            fill_volume_usd_24h = _safe_float(fills_summary.get("fill_volume_usd_24h"), 0.0)
            fill_volume_usd_session = _safe_float(fills_summary.get("fill_volume_usd_session"), 0.0)
            fills_session_cutoff_source = str(fills_summary.get("session_cutoff_source") or "session_start")
            fills_by_sport_24h = (
                fills_summary.get("fills_by_sport_24h")
                if isinstance(fills_summary.get("fills_by_sport_24h"), dict)
                else {}
            )
            fills_by_sport_session = (
                fills_summary.get("fills_by_sport_session")
                if isinstance(fills_summary.get("fills_by_sport_session"), dict)
                else {}
            )
    fills_available = bool(fills_summary.get("fetched"))

    if sport_activity_rows:
        for row in sport_activity_rows:
            sport_name = str(row.get("sport") or "")
            row["fills_session"] = _sum_by_sport_token(fills_by_sport_session, sport_name)
            row["fills_24h"] = _sum_by_sport_token(fills_by_sport_24h, sport_name)

    if sim_mode and dry_run_window > 0:
        st.caption("`DRY_RUN` rows are signal checks only and are not submitted orders.")
    st.caption("Live cards are bot-scoped: bot-submitted orders and bot-tracked USDC cash, not full Polymarket portfolio P&L.")
    if not sim_mode:
        if not external_api_enabled:
            st.caption("Live API cards are paused for faster load. Enable `Live API cards` above.")
        st.caption("Total equity = bot cash + current value of active open positions.")
        st.caption("`Submitted` = bot decision time; `Exchange fills` = actual fill events (can happen later).")
        if not fills_available and portfolio_address:
            st.caption(f"Exchange fills feed unavailable: {fills_summary.get('error', 'unknown_error')}")
        if pd.notna(session_start):
            st.caption(f"Session start: {session_start.strftime('%m-%d %H:%M:%S UTC')}")
        elif fills_available and fills_session_cutoff_source == "lookback_fallback":
            st.caption("Session start unavailable; exchange session metrics are using rolling 24h.")

    top_a, top_b, top_c, top_d = st.columns(4)
    if sim_mode:
        top_a.metric("Decisions (24h)", total_24h, f"{window_label}: {total_window}")
        top_b.metric("Orders Submitted (24h)", submitted_24h, f"{window_label}: {submitted_window}")
        top_c.metric("Simulated (24h)", simulated_24h, f"{window_label}: {simulated_window}")
        top_d.metric("Dry-Run Signals (24h)", dry_run_24h, f"{window_label}: {dry_run_window}")
    else:
        top_a.metric("Decisions (Session)", total_session, f"24h: {total_24h}")
        top_b.metric("Orders Submitted (Session)", submitted_session, f"24h: {submitted_24h}")
        top_c.metric("Rejected (Session)", rejected_session, f"24h: {rejected_24h}")
        if configured_sports:
            top_d.metric(
                "Sports Traded (Session)",
                f"{sports_traded_session}/{len(configured_sports)}",
                f"24h: {sports_traded_24h}/{len(configured_sports)}",
            )
        else:
            top_d.metric("Sports Traded (Session)", str(sports_traded_session), f"24h: {sports_traded_24h}")
        fill_a, fill_b = st.columns(2)
        fills_session_display = fills_session if fills_available else "—"
        fills_24h_delta = f"24h: {fills_24h}" if fills_available else "24h: —"
        fill_volume_session_display = _fmt_usd(fill_volume_usd_session) if fills_available else "—"
        fill_volume_24h_delta = f"24h: {_fmt_usd(fill_volume_usd_24h)}" if fills_available else "24h: —"
        fill_a.metric("Exchange Fills (Session)", fills_session_display, fills_24h_delta)
        fill_b.metric(
            "Exchange Fill Volume (Session)",
            fill_volume_session_display,
            fill_volume_24h_delta,
        )

    if not sim_mode and sport_activity_rows:
        activity_df = pd.DataFrame(sport_activity_rows).sort_values(
            ["fills_24h", "submitted_24h", "decisions_24h", "sport"],
            ascending=[False, False, False, True],
        )
        with st.expander("Sport Activity (24h + Session)", expanded=True):
            _safe_dataframe(activity_df, use_container_width=True, hide_index=True)

    if not sim_mode:
        odds_games_by_sport = health.get("odds_games_by_sport") if isinstance(health.get("odds_games_by_sport"), dict) else {}
        matches_by_sport = health.get("matches_by_sport") if isinstance(health.get("matches_by_sport"), dict) else {}
        aggregated_by_sport = health.get("aggregated_by_sport") if isinstance(health.get("aggregated_by_sport"), dict) else {}
        aggregated_by_market_type = (
            health.get("aggregated_by_market_type")
            if isinstance(health.get("aggregated_by_market_type"), dict)
            else {}
        )
        if odds_games_by_sport or matches_by_sport or aggregated_by_sport:
            all_feed_sports = sorted(
                set(configured_sports)
                | set(str(k) for k in odds_games_by_sport.keys())
                | set(str(k) for k in matches_by_sport.keys())
                | set(str(k) for k in aggregated_by_sport.keys())
            )
            feed_rows = []
            for sport_name in all_feed_sports:
                feed_rows.append(
                    {
                        "sport": sport_name,
                        "odds_games": int(odds_games_by_sport.get(sport_name, 0) or 0),
                        "matched_events": int(matches_by_sport.get(sport_name, 0) or 0),
                        "aggregated_events": int(aggregated_by_sport.get(sport_name, 0) or 0),
                    }
                )
            feed_df = pd.DataFrame(feed_rows).sort_values(
                ["aggregated_events", "matched_events", "odds_games", "sport"],
                ascending=[False, False, False, True],
            )
            with st.expander("Live Feed Coverage", expanded=True):
                _safe_dataframe(feed_df, use_container_width=True, hide_index=True)
                if aggregated_by_market_type:
                    mt = ", ".join(
                        f"{k}: {int(v)}"
                        for k, v in sorted(aggregated_by_market_type.items())
                    )
                    st.caption(f"Aggregated by market type: {mt}")

        fast_cycle = health.get("last_fast_cycle")
        if isinstance(fast_cycle, dict) and fast_cycle:
            diag_order = [
                "cycle",
                "status",
                "matches_total",
                "with_agg",
                "opportunities",
                "submitted",
                "rejected",
                "simulated",
                "dry_run",
                "skipped_no_agg",
                "skipped_order_book_fetch",
                "skipped_segment_market",
                "skipped_event_started",
                "skipped_pre_event_window",
                "skipped_no_edge_or_gates",
                "skipped_bet_too_small",
                "skipped_exposure",
                "skipped_opposite_side_locked",
                "blocked_circuit_breaker",
                "blocked_bankroll_unavailable",
                "blocked_bankroll_zero",
                "trip_reason",
            ]
            diag_rows: list[dict] = []
            for key in diag_order:
                if key not in fast_cycle:
                    continue
                value = fast_cycle.get(key)
                if key == "trip_reason" and not value:
                    continue
                diag_rows.append({"metric": key, "value": value})
            if diag_rows:
                with st.expander("Last Fast Cycle Diagnostics", expanded=True):
                    diag_df = pd.DataFrame(diag_rows)
                    if "value" in diag_df.columns:
                        diag_df["value"] = diag_df["value"].apply(
                            lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
                        )
                    _safe_dataframe(diag_df, use_container_width=True, hide_index=True)
                    if int(fast_cycle.get("submitted", 0) or 0) == 0:
                        blockers = []
                        if int(fast_cycle.get("blocked_circuit_breaker", 0) or 0) > 0:
                            reason = str(fast_cycle.get("trip_reason") or "unknown")
                            blockers.append(f"circuit breaker ({reason})")
                        if int(fast_cycle.get("blocked_bankroll_unavailable", 0) or 0) > 0:
                            blockers.append("bankroll unavailable")
                        if int(fast_cycle.get("blocked_bankroll_zero", 0) or 0) > 0:
                            blockers.append("bankroll is zero")
                        if int(fast_cycle.get("skipped_no_edge_or_gates", 0) or 0) > 0:
                            blockers.append("no opportunities passed edge/gates")
                        if int(fast_cycle.get("skipped_bet_too_small", 0) or 0) > 0:
                            blockers.append("bet size computed to zero")
                        if int(fast_cycle.get("skipped_exposure", 0) or 0) > 0:
                            blockers.append("exposure limits blocked")
                        if blockers:
                            st.caption("Why no order this cycle: " + ", ".join(blockers) + ".")

    base_a, base_b, base_c, base_d = st.columns(4)
    if sim_mode:
        base_a.metric("Paper Bankroll", _fmt_usd(current_bankroll), _fmt_pct(pnl_pct))
        base_b.metric("Expected PnL", _fmt_usd(expected_pnl))
        base_c.metric("Total Sim Stakes", _fmt_usd(invested_usd))
        base_d.metric("Avg Sim Stake", _fmt_usd(avg_sim_bet))
    else:
        base_a.metric("Live Engine", "Active" if not dry_run else "Blocked")
        base_b.metric("Bot Cash (USDC)", _fmt_usd(wallet_balance_usd) if wallet_balance_usd is not None else "—")
        base_c.metric(
            "Open Positions Value",
            _fmt_usd(open_positions_value_usd) if open_positions_value_usd is not None else "—",
        )
        base_d.metric("Total Equity", _fmt_usd(total_equity_usd) if total_equity_usd is not None else "—")
        foot_a, foot_b, foot_c, foot_d = st.columns(4)
        foot_a.metric("Open Positions", open_positions_count)
        foot_b.metric("Exchange Open Orders", open_orders_count)
        foot_c.metric("Open Orders Notional", _fmt_usd(open_orders_notional_usd))
        foot_d.metric("Avg Order Size (24h)", _fmt_usd(avg_submitted_bet))
        claim_a, claim_b = st.columns(2)
        claim_a.metric("Claims (Session)", claims_today)
        claim_b.metric("Claimed USDC (Session)", _fmt_usd(claimed_usdc_today))
        pnl_col, status_col = st.columns([1, 3])
        with pnl_col:
            if live_pnl_usd is not None:
                st.metric("Cash P&L (since bot start)", _fmt_usd(live_pnl_usd), _fmt_pct(live_pnl_pct))
            else:
                st.metric("Cash P&L (since bot start)", "—")
        with status_col:
            if positions_summary.get("fetched"):
                st.caption(
                    f"Positions source: {positions_summary.get('rows', 0)} rows from data API "
                    f"for {positions_summary.get('address', '')}."
                )
            elif portfolio_address:
                st.caption("Positions source unavailable right now; equity card will refresh when API responds.")
            if exchange_open_orders_count is not None:
                st.caption("Open orders source: exchange CLOB API (authoritative).")
            elif tracked_open_orders_count is not None:
                st.caption(
                    f"Open orders source fallback: bot tracker ({int(tracked_open_orders_count)} tracked, "
                    f"{_fmt_usd(tracked_open_orders_notional_usd or 0.0)} tracked notional)."
                )
        if unsettled_value_usd is not None:
            eq_a, eq_b = st.columns(2)
            with eq_a:
                eq_a.metric("Unsettled Value (Claimable)", _fmt_usd(unsettled_value_usd))
            with eq_b:
                if account_equity_estimate_usd is not None:
                    eq_b.metric("Est. Account Equity", _fmt_usd(account_equity_estimate_usd))
            st.caption("Est. Account Equity = Bot cash + open positions value + unsettled claimable value.")

    if df.empty:
        st.info("No decision data yet. Let the bot run for a few cycles.")
    else:
        chart_scope_options = ["Session", "24h", "Window"]
        chart_scope_default = 0 if not sim_mode else 1
        chart_scope = st.selectbox(
            "Chart Window",
            chart_scope_options,
            index=chart_scope_default,
            key="chart_window_scope",
        )
        include_dry_run = st.checkbox("Include DRY_RUN in charts", value=False) if sim_mode else False
        if chart_scope == "Session":
            chart_df = session_df_all.copy()
        elif chart_scope == "24h":
            chart_df = recent_df_all.copy()
        else:
            chart_df = df.copy()
        if not include_dry_run:
            chart_df = chart_df[~_has_action(chart_df, "DRY_RUN")]

        if chart_df.empty:
            st.info("No non-DRY_RUN records available for charts yet.")
        else:
            left, right = st.columns([3, 2])
            with left:
                st.subheader("Edge Distribution (pp)")
                edge_df = pd.DataFrame({
                    "edge_pp": (chart_df.get("adjusted_edge", pd.Series(dtype=float)).dropna() * 100).clip(-5, 35)
                })
                if edge_df.empty:
                    st.info("No edge values available yet.")
                else:
                    edge_chart = (
                        alt.Chart(edge_df)
                        .mark_bar(color="#0284c7", cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
                        .encode(
                            x=alt.X("edge_pp:Q", bin=alt.Bin(maxbins=26), title="Adjusted edge (percentage points)"),
                            y=alt.Y("count():Q", title="Count"),
                        )
                        .properties(height=280)
                    )
                    st.altair_chart(edge_chart, use_container_width=True)

            with right:
                st.subheader("Sports Mix")
                if "sport" not in chart_df.columns or chart_df["sport"].dropna().empty:
                    st.info("No sport labels found.")
                else:
                    sport_df = (
                        chart_df["sport"]
                        .dropna()
                        .value_counts()
                        .rename_axis("sport")
                        .reset_index(name="count")
                    )
                    sport_chart = (
                        alt.Chart(sport_df)
                        .mark_bar(color="#0ea5a4", cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
                        .encode(
                            x=alt.X("sport:N", sort="-y", title="Sport"),
                            y=alt.Y("count:Q", title="Decisions"),
                        )
                        .properties(height=280)
                    )
                    st.altair_chart(sport_chart, use_container_width=True)

            st.subheader("Decision Flow")
            if "timestamp" in chart_df.columns and not chart_df["timestamp"].dropna().empty:
                flow = chart_df.copy()
                flow["bucket"] = flow["timestamp"].dt.floor("5min")
                flow = (
                    flow.groupby(["bucket", "action"], dropna=False)
                    .size()
                    .rename("count")
                    .reset_index()
                    .sort_values("bucket")
                )
                flow_chart = (
                    alt.Chart(flow)
                    .mark_area(opacity=0.5)
                    .encode(
                        x=alt.X("bucket:T", title="Time"),
                        y=alt.Y("count:Q", stack="zero", title="Decisions"),
                        color=alt.Color("action:N", legend=alt.Legend(title="Action")),
                    )
                    .properties(height=260)
                )
                st.altair_chart(flow_chart, use_container_width=True)
            else:
                st.info("No timestamped decisions to chart.")

        if sim_mode:
            st.subheader("Paper Bankroll Curve")
            sim_df = df[_has_action(df, "SIMULATED")].copy()
            sim_curve_ok = (
                not sim_df.empty
                and "sim_bankroll_after_usd" in sim_df.columns
                and sim_df["sim_bankroll_after_usd"].notna().any()
            )
            if sim_curve_ok:
                sim_df = sim_df.sort_values("timestamp")
                curve = sim_df[["timestamp", "sim_bankroll_after_usd"]].dropna()
                curve_chart = (
                    alt.Chart(curve)
                    .mark_line(color="#16a34a", strokeWidth=3)
                    .encode(
                        x=alt.X("timestamp:T", title="Time"),
                        y=alt.Y("sim_bankroll_after_usd:Q", title="Bankroll (USD)"),
                    )
                    .properties(height=260)
                )
                st.altair_chart(curve_chart, use_container_width=True)
            else:
                st.info("No simulated-trade equity points yet.")

with tab_decisions:
    st.subheader("Decision Log")
    if df.empty:
        st.info("No decisions logged yet.")
    else:
        st.caption("`SUBMITTED` means order sent to the exchange, not a guaranteed fill.")
        st.caption("Edge breakdown uses devigged consensus probability, expected fill probability, and safety haircut.")
        st.caption(f"Loaded window: last {DECISION_LOAD_DAYS} day(s) for dashboard performance.")
        action_options = ["Executed only", "Submitted only", "Rejected only", "All"]
        if sim_mode:
            action_options.insert(2, "Simulated only")
            action_options.insert(3, "Dry run only")
        time_options = ["Session only", "Last 24h", "Loaded window"]
        default_time_idx = 0 if not sim_mode else 1
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            action_scope = st.selectbox(
                "Action Scope",
                action_options,
                index=0,
            )
        with col2:
            time_scope = st.selectbox("Time Scope", time_options, index=default_time_idx)
        with col3:
            sports = ["All"] + sorted(df["sport"].dropna().unique().tolist()) if "sport" in df.columns else ["All"]
            sport_filter = st.selectbox("Sport", sports)
        with col4:
            row_count = st.slider("Rows", 20, 300, 80)

        if time_scope == "Session only":
            filtered = session_df_all.copy()
        elif time_scope == "Last 24h":
            filtered = recent_df_all.copy()
        else:
            filtered = df.copy()
        if action_scope == "Executed only":
            filtered = filtered[_has_actions(filtered, EXECUTION_ACTIONS)].copy()
        elif action_scope == "Submitted only":
            filtered = filtered[_has_actions(filtered, ORDER_ACTIONS)].copy()
        elif action_scope == "Simulated only":
            filtered = filtered[_has_action(filtered, "SIMULATED")].copy()
        elif action_scope == "Dry run only":
            filtered = filtered[_has_action(filtered, "DRY_RUN")].copy()
        elif action_scope == "Rejected only":
            filtered = filtered[_has_action(filtered, "REJECTED")].copy()
        if sport_filter != "All" and "sport" in filtered.columns:
            filtered = filtered[filtered["sport"] == sport_filter].copy()

        prepared = _prepare_decision_log_df(filtered)

        view = prepared.head(row_count).copy()

        if "timestamp" in view.columns:
            view["timestamp"] = view["timestamp"].dt.strftime("%m-%d %H:%M:%S")
        if "bet_usd" in view.columns:
            view["bet_usd"] = view["bet_usd"].apply(_fmt_usd)
        if "sim_expected_pnl_usd" in view.columns:
            view["sim_expected_pnl_usd"] = view["sim_expected_pnl_usd"].apply(_fmt_usd)
        if "sim_bankroll_after_usd" in view.columns:
            view["sim_bankroll_after_usd"] = view["sim_bankroll_after_usd"].apply(_fmt_usd)
        if "event_start" in view.columns:
            event_start = pd.to_datetime(view["event_start"], utc=True, errors="coerce")
            view["event_start"] = event_start.dt.strftime("%m-%d %H:%M:%S")

        _safe_dataframe(view, use_container_width=True, hide_index=True)

        st.caption("Excel export always contains the last 24h of decisions, independent of table filters.")
        export_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        if st.button("Prepare Excel export (24h)", key="prepare_decision_log_export"):
            with st.spinner("Preparing export..."):
                export_df = _prepare_decision_log_df(recent_df_all.copy())
                if "timestamp" in export_df.columns:
                    ts_export = pd.to_datetime(export_df["timestamp"], utc=True, errors="coerce")
                    export_df["timestamp"] = ts_export.dt.strftime("%Y-%m-%d %H:%M:%S")
                if "event_start" in export_df.columns:
                    start_export = pd.to_datetime(export_df["event_start"], utc=True, errors="coerce")
                    export_df["event_start"] = start_export.dt.strftime("%Y-%m-%d %H:%M:%S")
                st.session_state["decision_export_csv_bytes"] = export_df.to_csv(index=False).encode("utf-8")
                st.session_state["decision_export_csv_name"] = f"decision_log_24h_{export_ts}.csv"
                try:
                    st.session_state["decision_export_excel_bytes"] = _to_excel_bytes(export_df)
                    st.session_state["decision_export_excel_name"] = f"decision_log_24h_{export_ts}.xlsx"
                    st.session_state["decision_export_excel_error"] = ""
                except Exception as exc:
                    st.session_state["decision_export_excel_bytes"] = b""
                    st.session_state["decision_export_excel_name"] = f"decision_log_24h_{export_ts}.xlsx"
                    st.session_state["decision_export_excel_error"] = str(exc)

        csv_bytes = st.session_state.get("decision_export_csv_bytes")
        if csv_bytes:
            st.download_button(
                "Download CSV (24h)",
                data=csv_bytes,
                file_name=st.session_state.get("decision_export_csv_name", f"decision_log_24h_{export_ts}.csv"),
                mime="text/csv",
                key="decision_log_csv_export",
            )

        excel_bytes = st.session_state.get("decision_export_excel_bytes")
        if excel_bytes:
            st.download_button(
                "Download Excel (.xlsx)",
                data=excel_bytes,
                file_name=st.session_state.get("decision_export_excel_name", f"decision_log_24h_{export_ts}.xlsx"),
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="decision_log_excel_export",
            )
        elif st.session_state.get("decision_export_excel_error"):
            st.warning("Excel export unavailable right now. CSV fallback is available.")

with tab_config:
    st.subheader("Runtime Configuration")
    st.caption("Changes apply on the next cycle without restart.")
    st.caption("Trading sports are read from `SPORTS` in env (currently: " + (", ".join(sports_active) if sports_active else "n/a") + ").")
    current = load_runtime_config()

    with st.form("runtime_form"):
        a, b = st.columns(2)
        with a:
            st.markdown("**Execution Mode**")
            simulation_mode = st.checkbox(
                "Simulation Mode (fake money)",
                value=bool(current.get("SIMULATION_MODE", True)),
            )
            trading_enabled = st.checkbox(
                "Live Trading Enabled",
                value=bool(current.get("TRADING_ENABLED", False)),
            )
            sim_start = st.number_input(
                "Simulation Starting Bankroll",
                min_value=100.0,
                max_value=1_000_000.0,
                value=_clamp(_safe_float(current.get("SIMULATION_START_BANKROLL"), 1000.0), 100.0, 1_000_000.0),
                step=100.0,
            )

        with b:
            st.markdown("**Risk/Sizing**")
            min_edge = st.number_input(
                "Min Edge (pp)",
                min_value=0.001,
                max_value=0.50,
                value=_clamp(_safe_float(current.get("MIN_EDGE_PP"), 0.0085), 0.001, 0.50),
                step=0.001,
                format="%.3f",
            )
            max_spread = st.number_input(
                "Spread Aggressiveness (max spread)",
                min_value=0.001,
                max_value=0.100,
                value=_clamp(_safe_float(current.get("MAX_SPREAD"), 0.01), 0.001, 0.100),
                step=0.001,
                format="%.3f",
                help="Higher value is more aggressive: allows wider books.",
            )
            fraction_kelly = st.number_input(
                "Fraction Kelly",
                min_value=0.01,
                max_value=1.0,
                value=_clamp(_safe_float(current.get("FRACTION_KELLY"), 0.15), 0.01, 1.0),
                step=0.01,
                format="%.2f",
            )
            event_cap_kelly_multiplier = st.number_input(
                "Event Cap Kelly Multiplier",
                min_value=1.0,
                max_value=5.0,
                value=_clamp(
                    _safe_float(current.get("EVENT_CAP_KELLY_MULTIPLIER"), 3.0),
                    1.0,
                    5.0,
                ),
                step=0.1,
                format="%.1f",
                help="How many Kelly-sized entries the bot may hold per event before hitting the event cap.",
            )
            moneyline_favorites_only = st.checkbox(
                "Moneyline: favorites only",
                value=bool(current.get("MONEYLINE_FAVORITES_ONLY", True)),
            )
            no_resting_orders = st.checkbox(
                "No resting orders (IOC-like)",
                value=bool(current.get("NO_RESTING_ORDERS", True)),
                help="Submit with a max buy price and cancel unfilled remainder immediately.",
            )
            auto_claim_enabled = st.checkbox(
                "Auto-claim winning positions",
                value=bool(current.get("AUTO_CLAIM_ENABLED", True)),
                help="Automatically redeem claimable winnings on-chain when markets resolve.",
            )
            claim_cooldown_min = st.slider(
                "Claim retry cooldown (minutes)",
                min_value=1,
                max_value=720,
                value=max(1, int(current.get("CLAIM_COOLDOWN_SEC", 14400)) // 60),
                step=1,
            )
            claim_max_per_cycle = st.slider(
                "Max claims per cycle",
                min_value=1,
                max_value=5,
                value=max(1, int(current.get("CLAIM_MAX_PER_CYCLE", 1))),
                step=1,
            )
            max_per_event = st.number_input(
                "Max Per Event %",
                min_value=0.005,
                max_value=0.50,
                value=_clamp(_safe_float(current.get("MAX_PER_EVENT_PCT"), 0.02), 0.005, 0.50),
                step=0.005,
                format="%.3f",
            )
            max_total = st.number_input(
                "Max Total Exposure %",
                min_value=0.05,
                max_value=1.0,
                value=_clamp(_safe_float(current.get("MAX_TOTAL_EXPOSURE_PCT"), 0.30), 0.05, 1.0),
                step=0.01,
                format="%.2f",
            )
            close_before_event_min = st.slider(
                "Pre-event close window (minutes)",
                min_value=0,
                max_value=120,
                value=max(0, int(current.get("CLOSE_ORDERS_BEFORE_EVENT_SEC", 900)) // 60),
                step=1,
            )

        submitted = st.form_submit_button("Save Runtime Config", type="primary")
        if submitted:
            save_runtime_config(
                {
                    "SIMULATION_MODE": simulation_mode,
                    "TRADING_ENABLED": trading_enabled,
                    "SIMULATION_START_BANKROLL": sim_start,
                    "MIN_EDGE_PP": min_edge,
                    "MAX_SPREAD": max_spread,
                    "FRACTION_KELLY": fraction_kelly,
                    "EVENT_CAP_KELLY_MULTIPLIER": event_cap_kelly_multiplier,
                    "MONEYLINE_FAVORITES_ONLY": moneyline_favorites_only,
                    "NO_RESTING_ORDERS": no_resting_orders,
                    "MAX_PER_EVENT_PCT": max_per_event,
                    "MAX_TOTAL_EXPOSURE_PCT": max_total,
                    "CLOSE_ORDERS_BEFORE_EVENT_SEC": int(close_before_event_min) * 60,
                    "AUTO_CLAIM_ENABLED": auto_claim_enabled,
                    "CLAIM_COOLDOWN_SEC": int(claim_cooldown_min) * 60,
                    "CLAIM_MAX_PER_CYCLE": int(claim_max_per_cycle),
                }
            )
            if simulation_mode and trading_enabled:
                st.warning("Simulation mode is ON, so real order placement stays disabled.")
            st.success("Runtime config saved.")

    st.subheader("Quick Kelly Controls")
    current_runtime = load_runtime_config()
    current_kelly = _clamp(_safe_float(current_runtime.get("FRACTION_KELLY"), 0.15), 0.01, 1.0)
    k1, k2, k3 = st.columns([1, 1, 2])
    with k1:
        if st.button("Kelly -0.01", use_container_width=True):
            updated = max(0.01, round(current_kelly - 0.01, 2))
            save_runtime_config({"FRACTION_KELLY": updated})
            st.success(f"Kelly set to {updated:.2f}")
            st.rerun()
    with k2:
        if st.button("Kelly +0.01", use_container_width=True):
            updated = min(1.0, round(current_kelly + 0.01, 2))
            save_runtime_config({"FRACTION_KELLY": updated})
            st.success(f"Kelly set to {updated:.2f}")
            st.rerun()
    with k3:
        st.metric("Current Fraction Kelly", f"{current_kelly:.2f}")

    r1, r2 = st.columns(2)
    with r1:
        if sim_mode:
            if st.button("Reset Paper Bankroll", use_container_width=True):
                start = _safe_float(load_runtime_config().get("SIMULATION_START_BANKROLL"), 1000.0)
                reset_simulation_state(start)
                st.success("Simulation bankroll reset.")
        else:
            st.caption("Simulation controls are hidden while live mode is active.")
    with r2:
        if st.button("Reload Config Cache", use_container_width=True):
            load_runtime_config.clear()
            st.success("Config cache cleared.")

    st.subheader("Current Runtime Overrides")
    st.json(load_runtime_config(), expanded=False)
