"""PolyEdge dashboard with simulation and runtime controls."""

import glob
import hmac
import json
import os
import sys
import urllib.parse
import urllib.request
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


@st.cache_data(ttl=8, show_spinner=False)
def load_decisions(days: int = 7) -> pd.DataFrame:
    files = sorted(glob.glob(str(AUDIT_DIR / "decisions_*.jsonl")))[-days:]
    rows = []
    for path in files:
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
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
            with urllib.request.urlopen(req, timeout=4) as resp:
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
        with urllib.request.urlopen(req, timeout=4) as resp:
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
    if not pw:
        return True
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
df = load_decisions(days=7)
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
    if auto_refresh:
        st.markdown('<meta http-equiv="refresh" content="20">', unsafe_allow_html=True)

tab_overview, tab_decisions, tab_config = st.tabs(["Overview", "Decisions", "Config"])

with tab_overview:
    recent_df = recent_df_all.copy()
    session_df = session_df_all.copy()
    session_start = session_start_global
    configured_sports = [str(s).strip() for s in sports_active if str(s).strip()]
    total_7d = len(df)
    total_24h = len(recent_df)
    total_session = len(session_df)
    submitted_7d = int(_has_actions(df, ORDER_ACTIONS).sum())
    submitted_24h = int(_has_actions(recent_df, ORDER_ACTIONS).sum())
    submitted_session = int(_has_actions(session_df, ORDER_ACTIONS).sum())
    simulated_7d = int(_has_action(df, "SIMULATED").sum())
    simulated_24h = int(_has_action(recent_df, "SIMULATED").sum())
    simulated_session = int(_has_action(session_df, "SIMULATED").sum())
    rejected_7d = int(_has_action(df, "REJECTED").sum())
    rejected_24h = int(_has_action(recent_df, "REJECTED").sum())
    rejected_session = int(_has_action(session_df, "REJECTED").sum())
    dry_run_7d = int(_has_action(df, "DRY_RUN").sum())
    dry_run_24h = int(_has_action(recent_df, "DRY_RUN").sum())
    dry_run_session = int(_has_action(session_df, "DRY_RUN").sum())
    sport_activity_rows: list[dict] = []
    sports_traded_24h = 0
    sports_traded_session = 0
    if "sport" in session_df.columns:
        session_sports = session_df["sport"].dropna().astype(str).tolist()
        all_sports = sorted(set(configured_sports) | set(session_sports))
        for sport_name in all_sports:
            sport_24h_df = recent_df[recent_df["sport"] == sport_name].copy()
            sport_df = session_df[session_df["sport"] == sport_name].copy()
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

    start_bankroll = float(sim_state.get("start_bankroll", runtime.get("SIMULATION_START_BANKROLL", 1000)))
    current_bankroll = float(sim_state.get("current_bankroll", start_bankroll))
    expected_pnl = float(sim_state.get("expected_pnl", 0.0))
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
        not sim_mode
        and bool(portfolio_address)
        and (open_positions_value_usd is None or open_positions_count_raw is None)
    )
    if needs_positions_fallback:
        positions_summary = load_positions_summary(portfolio_address, limit=200, max_pages=2)
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
        if (not sim_mode and portfolio_address)
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

    if sim_mode and dry_run_7d > 0:
        st.caption("`DRY_RUN` rows are signal checks only and are not submitted orders.")
    st.caption("Live cards are bot-scoped: bot-submitted orders and bot-tracked USDC cash, not full Polymarket portfolio P&L.")
    if not sim_mode:
        st.caption("Total equity = bot cash + current value of active open positions.")
        if pd.notna(session_start):
            st.caption(f"Session start: {session_start.strftime('%m-%d %H:%M:%S UTC')}")

    top_a, top_b, top_c, top_d = st.columns(4)
    if sim_mode:
        top_a.metric("Decisions (24h)", total_24h, f"7d: {total_7d}")
        top_b.metric("Orders Submitted (24h)", submitted_24h, f"7d: {submitted_7d}")
        top_c.metric("Simulated (24h)", simulated_24h, f"7d: {simulated_7d}")
        top_d.metric("Dry-Run Signals (24h)", dry_run_24h, f"7d: {dry_run_7d}")
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

    if not sim_mode and sport_activity_rows:
        activity_df = pd.DataFrame(sport_activity_rows).sort_values(
            ["submitted_24h", "decisions_24h", "sport"],
            ascending=[False, False, True],
        )
        with st.expander("Sport Activity (24h + Session)", expanded=True):
            st.dataframe(activity_df, use_container_width=True, hide_index=True)

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
                st.dataframe(feed_df, use_container_width=True, hide_index=True)
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
                    st.dataframe(pd.DataFrame(diag_rows), use_container_width=True, hide_index=True)
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
        chart_scope_options = ["Session", "24h", "7d"]
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
        action_options = ["Executed only", "Submitted only", "Rejected only", "All"]
        if sim_mode:
            action_options.insert(2, "Simulated only")
            action_options.insert(3, "Dry run only")
        time_options = ["Session only", "Last 24h", "Last 7d"]
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

        if {"buy_outcome", "outcome_a", "outcome_b"}.issubset(filtered.columns):
            filtered["selected_outcome"] = filtered.apply(
                lambda row: row["outcome_a"] if row["buy_outcome"] == "a" else row["outcome_b"],
                axis=1,
            )
        elif "buy_outcome" in filtered.columns:
            filtered["selected_outcome"] = filtered["buy_outcome"].fillna("").astype(str).str.upper()

        view = filtered.head(row_count).copy()

        if "true_prob" in view.columns:
            view["consensus_prob"] = view["true_prob"].apply(_fmt_prob_pct)
            view["consensus_odds"] = view["true_prob"].apply(_prob_to_american)
        if {"agg_prob_a", "agg_prob_b"}.issubset(view.columns):
            view["agg_market"] = view.apply(
                lambda row: f"A {row['agg_prob_a'] * 100:.1f}% vs B {row['agg_prob_b'] * 100:.1f}%"
                if pd.notna(row["agg_prob_a"]) and pd.notna(row["agg_prob_b"])
                else "—",
                axis=1,
            )
        if "poly_fill" in view.columns:
            view["poly_fill_prob"] = view["poly_fill"].apply(_fmt_prob_pct)
        if "adjusted_edge" in view.columns:
            view["edge_pp"] = (view["adjusted_edge"] * 100).round(2).astype(str) + "pp"
        if {"raw_edge", "adjusted_edge", "true_prob", "poly_fill"}.issubset(view.columns):
            view["edge_breakdown"] = [_edge_breakdown_text(row) for _, row in view.iterrows()]

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
        cols = [c for c in cols if c in view.columns]
        view = view[cols].copy()

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

        st.dataframe(view, use_container_width=True, hide_index=True)

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
                value=float(current.get("SIMULATION_START_BANKROLL", 1000.0)),
                step=100.0,
            )

        with b:
            st.markdown("**Risk/Sizing**")
            min_edge = st.number_input(
                "Min Edge (pp)",
                min_value=0.001,
                max_value=0.50,
                value=float(current.get("MIN_EDGE_PP", 0.05)),
                step=0.001,
                format="%.3f",
            )
            max_spread = st.number_input(
                "Spread Aggressiveness (max spread)",
                min_value=0.001,
                max_value=0.100,
                value=float(current.get("MAX_SPREAD", 0.01)),
                step=0.001,
                format="%.3f",
                help="Higher value is more aggressive: allows wider books.",
            )
            fraction_kelly = st.number_input(
                "Fraction Kelly",
                min_value=0.01,
                max_value=1.0,
                value=float(current.get("FRACTION_KELLY", 0.15)),
                step=0.01,
                format="%.2f",
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
                value=float(current.get("MAX_PER_EVENT_PCT", 0.02)),
                step=0.005,
                format="%.3f",
            )
            max_total = st.number_input(
                "Max Total Exposure %",
                min_value=0.05,
                max_value=1.0,
                value=float(current.get("MAX_TOTAL_EXPOSURE_PCT", 0.30)),
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
    current_kelly = float(current_runtime.get("FRACTION_KELLY", 0.15))
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
                start = float(load_runtime_config().get("SIMULATION_START_BANKROLL", 1000.0))
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
