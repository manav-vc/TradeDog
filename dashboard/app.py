"""
TradeDog Dashboard — Streamlit-based personal trading interface.

This is the main interface for interacting with the TradeDog trading system.
Run with:  streamlit run dashboard/app.py

Pages:
  1. Portfolio Overview — positions, value, sector chart
  2. Signal Feed — agent decisions, conviction scores
  3. Trade History — closed trades, win rate, monthly returns
  4. Agent Monitor — tickers analyzed, agent breakdown
"""

import sys
import os
import json

# Ensure project root is on the path so we can import database/, portfolio/, etc.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timezone, timedelta

from database.db import Database
from database.models import Position
from portfolio.portfolio_guard import PortfolioGuard
from config.config_manager import (
    ConfigManager, DEFAULT_CONFIG, SLIDER_RANGES, LABELS, DESCRIPTIONS,
)

# ── App config ───────────────────────────────────────────────────────

st.set_page_config(
    page_title="TradeDog Dashboard",
    page_icon="🐕",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Database connection (cached per session) ─────────────────────────

@st.cache_resource
def get_db():
    """Singleton database connection."""
    db_path = os.path.join(_PROJECT_ROOT, "tradedog.db")
    return Database(db_path=db_path)


@st.cache_resource
def get_config_manager():
    """Singleton config manager."""
    return ConfigManager()


def get_guard():
    """Portfolio guard using current config values."""
    db = get_db()
    cfg = get_config_manager().load()
    sector_map_path = os.path.join(_PROJECT_ROOT, "watchlist", "sector_map.json")
    try:
        with open(sector_map_path, "r") as f:
            sector_map = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        sector_map = {}

    return PortfolioGuard(
        db=db,
        sector_map=sector_map,
        max_positions=cfg.get("max_positions", 10),
        max_sector_exposure=cfg.get("max_sector_exposure_pct", 30.0) / 100.0,
        max_single_position=cfg.get("risk_per_trade_pct", 5.0) / 100.0,
        daily_loss_limit=-(cfg.get("daily_loss_limit_pct", 3.0) / 100.0),
        cash_reserve=cfg.get("cash_reserve_pct", 10.0) / 100.0,
    )


# ── Sidebar ──────────────────────────────────────────────────────────

st.sidebar.title("🐕 TradeDog")
st.sidebar.markdown("---")

page = st.sidebar.radio(
    "Navigate",
    [
        "📊 Portfolio Overview",
        "📡 Signal Feed",
        "📈 Trade History",
        "🤖 Agent Monitor",
        "⚙️ Risk Settings",
    ],
)

st.sidebar.markdown("---")

# Pause trading toggle
db = get_db()
_pause_row = db.conn.execute(
    "SELECT message FROM system_log WHERE component = 'TRADING_PAUSE' ORDER BY id DESC LIMIT 1"
).fetchone()
current_pause = _pause_row["message"] == "PAUSED" if _pause_row else False

pause_trading = st.sidebar.toggle("⏸️ Pause Trading", value=current_pause)
if pause_trading != current_pause:
    new_state = "PAUSED" if pause_trading else "ACTIVE"
    db.log("INFO", "TRADING_PAUSE", new_state)
    if pause_trading:
        st.sidebar.warning("Trading is **paused**. No new buys will execute.")
    else:
        st.sidebar.success("Trading **resumed**.")

if pause_trading:
    st.sidebar.warning("⚠️ Engine paused — no new trades")

# Auto-refresh
refresh_interval = st.sidebar.selectbox(
    "Auto-refresh", ["Off", "30s", "60s", "5m"], index=2
)
_refresh_map = {"Off": None, "30s": 30, "60s": 60, "5m": 300}
_interval = _refresh_map[refresh_interval]
if _interval:
    st.sidebar.caption(f"Refreshing every {refresh_interval}")

# Metadata
st.sidebar.markdown("---")
st.sidebar.caption(
    f"Last refresh: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
)


# ── Helper functions ─────────────────────────────────────────────────

def _load_sector_map() -> dict:
    path = os.path.join(_PROJECT_ROOT, "watchlist", "sector_map.json")
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _positions_to_df(positions: list[Position]) -> pd.DataFrame:
    """Convert a list of Position objects to a DataFrame."""
    if not positions:
        return pd.DataFrame()
    records = []
    for p in positions:
        records.append({
            "ID": p.id,
            "Ticker": p.ticker,
            "Side": p.side,
            "Entry Price": p.entry_price,
            "Entry Time": p.entry_time,
            "Qty": p.qty,
            "Cost Basis": p.cost_basis,
            "Highest Price": p.highest_price,
            "Exit Price": p.exit_price,
            "Exit Time": p.exit_time,
            "Exit Reason": p.exit_reason,
            "Status": p.status,
            "P&L ($)": p.pnl,
            "P&L (%)": p.pnl_pct,
        })
    return pd.DataFrame(records)


def _get_snapshots_df() -> pd.DataFrame:
    """Load portfolio snapshots into a DataFrame."""
    rows = db.conn.execute(
        "SELECT * FROM portfolio_snapshots ORDER BY snapshot_date"
    ).fetchall()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([dict(r) for r in rows])


def _get_signals_df(limit: int = 50) -> pd.DataFrame:
    """Load recent signals into a DataFrame."""
    signals = db.get_recent_signals(limit=limit)
    if not signals:
        return pd.DataFrame()
    return pd.DataFrame(signals)


def _get_system_log_df(limit: int = 100) -> pd.DataFrame:
    """Load recent system log entries."""
    rows = db.conn.execute(
        "SELECT * FROM system_log ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([dict(r) for r in rows])


# =====================================================================
# Page 1: Portfolio Overview
# =====================================================================

def page_portfolio_overview():
    st.title("📊 Portfolio Overview")

    guard = get_guard()
    summary = guard.portfolio_summary()
    acct = summary["account"]
    perf = summary["performance"]

    # ── Top metrics row ──────────────────────────────────────────
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Value", f"${acct['total_value']:,.2f}")
    col2.metric("Cash", f"${acct['cash']:,.2f}")
    col3.metric("Invested", f"${acct['invested']:,.2f}")
    col4.metric("Buying Power", f"${acct['buying_power']:,.2f}")

    col5, col6, col7, col8 = st.columns(4)
    col5.metric("Open Positions", f"{summary['positions']['open_count']} / {summary['positions']['max_allowed']}")
    col6.metric("Closed Trades", str(perf["total_closed_trades"]))
    col7.metric("Win Rate", f"{perf['win_rate']:.1f}%")
    col8.metric("Realized P&L", f"${perf['realized_pnl']:,.2f}")

    st.markdown("---")

    # ── Open Positions Table ─────────────────────────────────────
    st.subheader("Open Positions")
    positions = db.get_open_positions()

    if positions:
        sector_map = _load_sector_map()
        pos_data = []
        for p in positions:
            sector = sector_map.get(p.ticker, "Unknown")
            # Trailing stop level = highest_price * (1 - 0.07)
            trail_level = (p.highest_price or p.entry_price) * 0.93
            # Stop loss level = entry_price * (1 - 0.08)
            stop_level = p.entry_price * 0.92
            pos_data.append({
                "Ticker": p.ticker,
                "Sector": sector,
                "Entry": f"${p.entry_price:.2f}",
                "Qty": p.qty,
                "Cost Basis": f"${p.cost_basis:,.2f}",
                "Highest": f"${(p.highest_price or p.entry_price):.2f}",
                "Trail Stop": f"${trail_level:.2f}",
                "Stop Loss": f"${stop_level:.2f}",
                "Entry Time": str(p.entry_time)[:19] if p.entry_time else "—",
            })

        df = pd.DataFrame(pos_data)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No open positions.")

    st.markdown("---")

    # ── Sector Exposure + Daily P&L side by side ─────────────────
    left_col, right_col = st.columns(2)

    with left_col:
        st.subheader("Sector Exposure")
        sector_data = summary["sector_breakdown"]
        if sector_data:
            fig = px.pie(
                names=list(sector_data.keys()),
                values=list(sector_data.values()),
                title="Portfolio by Sector (%)",
                hole=0.4,
            )
            fig.update_traces(textposition="inside", textinfo="label+percent")
            fig.update_layout(height=350, margin=dict(t=40, b=20, l=20, r=20))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No sector data to display.")

    with right_col:
        st.subheader("Portfolio Value Over Time")
        snap_df = _get_snapshots_df()
        if not snap_df.empty:
            fig = px.line(
                snap_df,
                x="snapshot_date",
                y="total_value",
                title="Daily Portfolio Value",
                labels={"snapshot_date": "Date", "total_value": "Value ($)"},
            )
            fig.update_layout(height=350, margin=dict(t=40, b=20, l=20, r=20))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No snapshots yet — portfolio value chart will appear after the first day of trading.")

    # ── Guard Limits ─────────────────────────────────────────────
    with st.expander("⚙️ Guard Limits"):
        limits = summary["guard_limits"]
        lcol1, lcol2, lcol3, lcol4, lcol5 = st.columns(5)
        lcol1.metric("Max Positions", limits["max_positions"])
        lcol2.metric("Max Sector", limits["max_sector_exposure"])
        lcol3.metric("Max Position", limits["max_single_position"])
        lcol4.metric("Daily Loss Limit", limits["daily_loss_limit"])
        lcol5.metric("Cash Reserve", limits["cash_reserve"])


# =====================================================================
# Page 2: Signal Feed
# =====================================================================

def page_signal_feed():
    st.title("📡 Signal Feed")

    # Controls
    col1, col2 = st.columns([1, 3])
    with col1:
        limit = st.selectbox("Show last", [25, 50, 100, 200], index=1)
    with col2:
        filter_action = st.multiselect(
            "Filter by action",
            ["BOUGHT", "SOLD", "SKIPPED", "REJECTED_BY_GATE", "REJECTED_BY_GUARD", "DRY_RUN"],
            default=[],
        )

    signals_df = _get_signals_df(limit=limit)

    if signals_df.empty:
        st.info("No signals logged yet. Run an analysis to see agent decisions here.")
        return

    # Apply filter
    if filter_action:
        signals_df = signals_df[signals_df["action_taken"].isin(filter_action)]

    # Color-code conviction scores
    def _conviction_color(score):
        if score is None or pd.isna(score):
            return "⬜"  # no score
        if score >= 75:
            return "🟢"  # strong
        elif score >= 50:
            return "🟡"  # moderate
        else:
            return "🔴"  # weak

    display_df = signals_df.copy()
    display_df["Signal"] = display_df["conviction_score"].apply(_conviction_color)

    # Select columns to display
    display_cols = ["Signal", "ticker", "trade_date", "parsed_action",
                    "conviction_score", "action_taken", "skip_reason", "timestamp"]
    available_cols = [c for c in display_cols if c in display_df.columns]
    display_df = display_df[available_cols]

    # Rename for readability
    col_rename = {
        "ticker": "Ticker",
        "trade_date": "Trade Date",
        "parsed_action": "Action",
        "conviction_score": "Conviction",
        "action_taken": "Result",
        "skip_reason": "Reason",
        "timestamp": "Timestamp",
    }
    display_df = display_df.rename(columns=col_rename)

    st.dataframe(display_df, use_container_width=True, hide_index=True)

    # ── Signal stats ─────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Signal Summary")

    total = len(signals_df)
    bought = len(signals_df[signals_df["action_taken"] == "BOUGHT"])
    skipped = len(signals_df[signals_df["action_taken"] == "SKIPPED"])
    rejected_gate = len(signals_df[signals_df["action_taken"] == "REJECTED_BY_GATE"])
    rejected_guard = len(signals_df[signals_df["action_taken"] == "REJECTED_BY_GUARD"])

    scol1, scol2, scol3, scol4, scol5 = st.columns(5)
    scol1.metric("Total Signals", total)
    scol2.metric("Bought", bought)
    scol3.metric("Skipped", skipped)
    scol4.metric("Gate Rejected", rejected_gate)
    scol5.metric("Guard Rejected", rejected_guard)

    # Conviction distribution
    if "conviction_score" in signals_df.columns:
        scores = signals_df["conviction_score"].dropna()
        if not scores.empty:
            st.subheader("Conviction Score Distribution")
            fig = px.histogram(
                scores,
                nbins=20,
                title="Distribution of Conviction Scores",
                labels={"value": "Conviction Score", "count": "Count"},
            )
            fig.update_layout(height=300, margin=dict(t=40, b=20, l=20, r=20))
            st.plotly_chart(fig, use_container_width=True)


# =====================================================================
# Page 3: Trade History
# =====================================================================

def page_trade_history():
    st.title("📈 Trade History")

    closed = db.get_closed_positions()

    if not closed:
        st.info("No closed trades yet. Trades will appear here once positions are exited.")
        return

    # ── Summary metrics ──────────────────────────────────────────
    total_trades = len(closed)
    wins = [p for p in closed if p.pnl and p.pnl > 0]
    losses = [p for p in closed if p.pnl and p.pnl <= 0]
    win_rate = len(wins) / total_trades * 100 if total_trades > 0 else 0

    total_pnl = sum((p.pnl or 0) for p in closed)
    avg_pnl_pct = sum((p.pnl_pct or 0) for p in closed) / total_trades if total_trades > 0 else 0
    avg_win = sum((p.pnl_pct or 0) for p in wins) / len(wins) if wins else 0
    avg_loss = sum((p.pnl_pct or 0) for p in losses) / len(losses) if losses else 0

    best = max(closed, key=lambda p: p.pnl_pct or 0) if closed else None
    worst = min(closed, key=lambda p: p.pnl_pct or 0) if closed else None

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Trades", total_trades)
    col2.metric("Win Rate", f"{win_rate:.1f}%")
    col3.metric("Total P&L", f"${total_pnl:,.2f}")
    col4.metric("Avg Return", f"{avg_pnl_pct:.1f}%")

    col5, col6, col7, col8 = st.columns(4)
    col5.metric("Wins / Losses", f"{len(wins)} / {len(losses)}")
    col6.metric("Avg Win", f"{avg_win:.1f}%")
    col7.metric("Avg Loss", f"{avg_loss:.1f}%")
    if best:
        col8.metric("Best Trade", f"{best.ticker} +{best.pnl_pct:.1f}%")

    st.markdown("---")

    # ── Closed trades table ──────────────────────────────────────
    st.subheader("All Closed Trades")
    trade_data = []
    for p in closed:
        trade_data.append({
            "Ticker": p.ticker,
            "Entry": f"${p.entry_price:.2f}",
            "Exit": f"${p.exit_price:.2f}" if p.exit_price else "—",
            "Qty": p.qty,
            "P&L ($)": f"${(p.pnl or 0):,.2f}",
            "P&L (%)": f"{(p.pnl_pct or 0):.1f}%",
            "Exit Reason": p.exit_reason or "—",
            "Entry Time": str(p.entry_time)[:19] if p.entry_time else "—",
            "Exit Time": str(p.exit_time)[:19] if p.exit_time else "—",
            "Hold Days": _hold_days(p),
        })

    df = pd.DataFrame(trade_data)
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.markdown("---")

    # ── P&L chart ────────────────────────────────────────────────
    left_col, right_col = st.columns(2)

    with left_col:
        st.subheader("P&L by Trade")
        pnl_data = [{"Trade": f"{p.ticker} #{p.id}", "P&L (%)": p.pnl_pct or 0} for p in closed]
        pnl_df = pd.DataFrame(pnl_data)
        colors = ["green" if v >= 0 else "red" for v in pnl_df["P&L (%)"]]
        fig = go.Figure(go.Bar(
            x=pnl_df["Trade"],
            y=pnl_df["P&L (%)"],
            marker_color=colors,
        ))
        fig.update_layout(
            title="Return per Trade (%)",
            xaxis_title="Trade",
            yaxis_title="P&L (%)",
            height=350,
            margin=dict(t=40, b=20, l=20, r=20),
        )
        st.plotly_chart(fig, use_container_width=True)

    with right_col:
        st.subheader("Exit Reason Breakdown")
        reasons = [p.exit_reason or "Unknown" for p in closed]
        reason_counts = pd.Series(reasons).value_counts()
        fig = px.pie(
            names=reason_counts.index.tolist(),
            values=reason_counts.values.tolist(),
            title="Exit Reasons",
            hole=0.4,
        )
        fig.update_layout(height=350, margin=dict(t=40, b=20, l=20, r=20))
        st.plotly_chart(fig, use_container_width=True)

    # ── Monthly returns (if we have snapshot data) ───────────────
    snap_df = _get_snapshots_df()
    if not snap_df.empty and "daily_pnl" in snap_df.columns:
        st.markdown("---")
        st.subheader("Monthly Returns")

        snap_df["snapshot_date"] = pd.to_datetime(snap_df["snapshot_date"])
        snap_df["month"] = snap_df["snapshot_date"].dt.to_period("M").astype(str)
        monthly = snap_df.groupby("month")["daily_pnl"].sum().reset_index()
        monthly.columns = ["Month", "P&L ($)"]

        colors = ["green" if v >= 0 else "red" for v in monthly["P&L ($)"]]
        fig = go.Figure(go.Bar(
            x=monthly["Month"],
            y=monthly["P&L ($)"],
            marker_color=colors,
        ))
        fig.update_layout(
            title="Monthly P&L ($)",
            xaxis_title="Month",
            yaxis_title="P&L ($)",
            height=350,
            margin=dict(t=40, b=20, l=20, r=20),
        )
        st.plotly_chart(fig, use_container_width=True)


def _hold_days(p: Position) -> str:
    """Calculate hold duration in days."""
    if not p.entry_time or not p.exit_time:
        return "—"
    try:
        entry = pd.to_datetime(p.entry_time)
        exit_ = pd.to_datetime(p.exit_time)
        return str((exit_ - entry).days)
    except Exception:
        return "—"


# =====================================================================
# Page 4: Agent Monitor
# =====================================================================

def page_agent_monitor():
    st.title("🤖 Agent Monitor")

    signals_df = _get_signals_df(limit=200)

    if signals_df.empty:
        st.info("No agent activity logged yet.")
        return

    # ── Today's activity ─────────────────────────────────────────
    st.subheader("Today's Activity")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Filter to today's signals
    today_signals = signals_df[signals_df["trade_date"] == today] if "trade_date" in signals_df.columns else pd.DataFrame()

    if not today_signals.empty:
        col1, col2, col3 = st.columns(3)
        col1.metric("Tickers Analyzed Today", today_signals["ticker"].nunique())
        col2.metric("Signals Today", len(today_signals))
        col3.metric("Buys Today", len(today_signals[today_signals["action_taken"] == "BOUGHT"]))

        st.markdown("**Tickers analyzed today:**")
        tickers_today = today_signals["ticker"].unique().tolist()
        st.write(", ".join(tickers_today))
    else:
        st.info("No analyses run today yet.")

    st.markdown("---")

    # ── Agent breakdown by action per ticker ─────────────────────
    st.subheader("Signal Outcomes by Ticker")

    # Count actions per ticker
    if "ticker" in signals_df.columns and "parsed_action" in signals_df.columns:
        action_breakdown = signals_df.groupby(["ticker", "parsed_action"]).size().reset_index(name="count")

        if not action_breakdown.empty:
            fig = px.bar(
                action_breakdown,
                x="ticker",
                y="count",
                color="parsed_action",
                barmode="group",
                title="Agent Decisions by Ticker",
                labels={"ticker": "Ticker", "count": "Count", "parsed_action": "Decision"},
                color_discrete_map={"BUY": "#2ecc71", "SELL": "#e74c3c", "HOLD": "#95a5a6"},
            )
            fig.update_layout(height=400, margin=dict(t=40, b=20, l=20, r=20))
            st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")

    # ── Signal outcome breakdown ─────────────────────────────────
    st.subheader("Signal Outcomes")
    left_col, right_col = st.columns(2)

    with left_col:
        if "action_taken" in signals_df.columns:
            outcome_counts = signals_df["action_taken"].value_counts()
            fig = px.pie(
                names=outcome_counts.index.tolist(),
                values=outcome_counts.values.tolist(),
                title="Signal Outcomes",
                hole=0.4,
                color_discrete_sequence=px.colors.qualitative.Set2,
            )
            fig.update_layout(height=350, margin=dict(t=40, b=20, l=20, r=20))
            st.plotly_chart(fig, use_container_width=True)

    with right_col:
        if "parsed_action" in signals_df.columns:
            action_counts = signals_df["parsed_action"].value_counts()
            fig = px.pie(
                names=action_counts.index.tolist(),
                values=action_counts.values.tolist(),
                title="Agent Decisions (BUY / SELL / HOLD)",
                hole=0.4,
                color_discrete_map={"BUY": "#2ecc71", "SELL": "#e74c3c", "HOLD": "#95a5a6"},
            )
            fig.update_layout(height=350, margin=dict(t=40, b=20, l=20, r=20))
            st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")

    # ── Conviction over time ─────────────────────────────────────
    st.subheader("Conviction Scores Over Time")
    if "conviction_score" in signals_df.columns and "timestamp" in signals_df.columns:
        scored = signals_df.dropna(subset=["conviction_score"]).copy()
        if not scored.empty:
            scored["timestamp"] = pd.to_datetime(scored["timestamp"])
            fig = px.scatter(
                scored,
                x="timestamp",
                y="conviction_score",
                color="action_taken",
                hover_data=["ticker"],
                title="Conviction Scores Over Time",
                labels={"timestamp": "Time", "conviction_score": "Score", "action_taken": "Outcome"},
            )
            fig.add_hline(y=65, line_dash="dash", line_color="orange",
                          annotation_text="Gate Threshold (65)")
            fig.update_layout(height=400, margin=dict(t=40, b=20, l=20, r=20))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No conviction scores logged yet.")

    # ── System log (recent) ──────────────────────────────────────
    st.markdown("---")
    with st.expander("📋 Recent System Log"):
        log_df = _get_system_log_df(limit=50)
        if not log_df.empty:
            display_cols = [c for c in ["timestamp", "level", "component", "message"] if c in log_df.columns]
            st.dataframe(log_df[display_cols], use_container_width=True, hide_index=True)
        else:
            st.info("No system log entries.")


# =====================================================================
# Page 5: Risk Settings
# =====================================================================

def page_risk_settings():
    st.title("⚙️ Risk Settings")
    st.markdown(
        "Tune your risk parameters without touching code. "
        "Changes take effect on the **next trade evaluation** — no restart needed."
    )

    cm = get_config_manager()
    cfg = cm.load()

    st.markdown("---")

    # ── Group 1: Position & Portfolio Limits ──────────────────────
    st.subheader("Position & Portfolio Limits")
    col1, col2 = st.columns(2)

    with col1:
        cfg["capital_deployed_pct"] = _config_slider("capital_deployed_pct", cfg)
        cfg["max_positions"] = _config_slider("max_positions", cfg)
        cfg["risk_per_trade_pct"] = _config_slider("risk_per_trade_pct", cfg)

    with col2:
        cfg["max_sector_exposure_pct"] = _config_slider("max_sector_exposure_pct", cfg)
        cfg["cash_reserve_pct"] = _config_slider("cash_reserve_pct", cfg)
        cfg["daily_loss_limit_pct"] = _config_slider("daily_loss_limit_pct", cfg)

    st.markdown("---")

    # ── Group 2: Entry Rules ──────────────────────────────────────
    st.subheader("Entry Rules (Conviction Gate)")
    col3, col4 = st.columns(2)

    with col3:
        cfg["conviction_threshold"] = _config_slider("conviction_threshold", cfg)
        cfg["min_agents_agree"] = _config_slider("min_agents_agree", cfg)

    with col4:
        cfg["cooldown_hours"] = _config_slider("cooldown_hours", cfg)

    st.markdown("---")

    # ── Group 3: Exit Rules ───────────────────────────────────────
    st.subheader("Exit Rules")
    col5, col6 = st.columns(2)

    with col5:
        cfg["stop_loss_pct"] = _config_slider("stop_loss_pct", cfg)
        cfg["profit_target_pct"] = _config_slider("profit_target_pct", cfg)

    with col6:
        cfg["trailing_stop_pct"] = _config_slider("trailing_stop_pct", cfg)
        cfg["max_hold_days"] = _config_slider("max_hold_days", cfg)

    st.markdown("---")

    # ── Save / Reset buttons ──────────────────────────────────────
    btn_col1, btn_col2, btn_col3 = st.columns([1, 1, 3])

    with btn_col1:
        if st.button("💾 Save Settings", type="primary", use_container_width=True):
            errors = cm.validate(cfg)
            if errors:
                for e in errors:
                    st.error(e)
            else:
                cm.save(cfg)
                # Log the config change
                db.log("INFO", "CONFIG_CHANGE", json.dumps(cfg))
                st.success("Settings saved! Changes take effect on the next trade evaluation.")

    with btn_col2:
        if st.button("🔄 Reset to Defaults", use_container_width=True):
            cfg = cm.reset_to_defaults()
            db.log("INFO", "CONFIG_RESET", "Reset to factory defaults")
            st.success("Settings reset to defaults.")
            st.rerun()

    # ── Current config summary ────────────────────────────────────
    with st.expander("📋 Current Config (JSON)"):
        st.json(cfg)


def _config_slider(key: str, cfg: dict) -> Any:
    """Render a labeled slider for a config key."""
    lo, hi, step = SLIDER_RANGES[key]
    label = LABELS[key]
    help_text = DESCRIPTIONS.get(key, "")
    current = cfg.get(key, DEFAULT_CONFIG[key])

    # Clamp current value to valid range
    current = max(lo, min(hi, current))

    # Use int slider for int-valued params
    if isinstance(lo, int) and isinstance(step, int):
        return st.slider(label, min_value=lo, max_value=hi, value=int(current),
                         step=step, help=help_text, key=f"slider_{key}")
    else:
        return st.slider(label, min_value=float(lo), max_value=float(hi),
                         value=float(current), step=float(step),
                         help=help_text, key=f"slider_{key}")


# =====================================================================
# Page Router
# =====================================================================

if page == "📊 Portfolio Overview":
    page_portfolio_overview()
elif page == "📡 Signal Feed":
    page_signal_feed()
elif page == "📈 Trade History":
    page_trade_history()
elif page == "🤖 Agent Monitor":
    page_agent_monitor()
elif page == "⚙️ Risk Settings":
    page_risk_settings()


# ── Auto-refresh via st.rerun ────────────────────────────────────────
# Streamlit's st_autorefresh requires streamlit-autorefresh package.
# Instead, we use a simple fragment approach with st.empty + time.
if _interval:
    import time
    placeholder = st.empty()
    time.sleep(_interval)
    st.rerun()
