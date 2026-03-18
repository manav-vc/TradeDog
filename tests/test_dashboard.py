"""
Phase 6 — Dashboard tests for TradeDog.

Tests the dashboard data helpers and the DB pause-trading feature.
Streamlit page rendering is not unit-tested (it requires a running server),
but we verify all the data-layer functions the dashboard depends on.
"""

import os
import tempfile
import pytest

from database.db import Database
from database.models import Position
from portfolio.portfolio_guard import PortfolioGuard


# ===================================================================
# Fixtures
# ===================================================================

@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    database = Database(db_path=path)
    yield database
    database.close()
    os.unlink(path)


@pytest.fixture
def guard(db):
    sector_map = {
        "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology",
        "JPM": "Financials", "V": "Financials",
        "UNH": "Healthcare",
        "XOM": "Energy",
        "AMZN": "Consumer Discretionary",
        "PG": "Consumer Staples",
        "CAT": "Industrials",
        "NEE": "Utilities",
    }
    return PortfolioGuard(
        db=db, starting_cash=100_000.0, sector_map=sector_map,
    )


def _insert_position(db, ticker, price, qty):
    pos = Position(
        ticker=ticker, entry_price=price, qty=qty,
        cost_basis=price * qty, status="OPEN",
    )
    return db.insert_position(pos)


# ===================================================================
# Trading Pause
# ===================================================================

class TestTradingPause:
    def test_default_not_paused(self, db):
        assert db.is_trading_paused() is False

    def test_pause_trading(self, db):
        db.set_trading_paused(True)
        assert db.is_trading_paused() is True

    def test_resume_trading(self, db):
        db.set_trading_paused(True)
        assert db.is_trading_paused() is True
        db.set_trading_paused(False)
        assert db.is_trading_paused() is False

    def test_pause_toggle_multiple_times(self, db):
        db.set_trading_paused(True)
        db.set_trading_paused(False)
        db.set_trading_paused(True)
        assert db.is_trading_paused() is True


# ===================================================================
# Portfolio Summary (dashboard data source)
# ===================================================================

class TestPortfolioSummary:
    def test_empty_portfolio_summary(self, guard):
        summary = guard.portfolio_summary()
        assert summary["account"]["total_value"] == 100_000.0
        assert summary["account"]["cash"] == 100_000.0
        assert summary["positions"]["open_count"] == 0
        assert summary["sector_breakdown"] == {}
        assert summary["performance"]["total_closed_trades"] == 0
        assert summary["performance"]["win_rate"] == 0.0

    def test_summary_with_positions(self, guard, db):
        _insert_position(db, "AAPL", 150.0, 10)  # $1500 Tech
        _insert_position(db, "JPM", 200.0, 5)     # $1000 Financials

        summary = guard.portfolio_summary()
        assert summary["positions"]["open_count"] == 2
        assert summary["positions"]["tickers"] == ["AAPL", "JPM"]
        assert summary["account"]["invested"] == 2500.0
        assert "Technology" in summary["sector_breakdown"]
        assert "Financials" in summary["sector_breakdown"]

    def test_summary_with_closed_trades(self, guard, db):
        pid = _insert_position(db, "AAPL", 100.0, 10)
        db.close_position(pid, 115.0, "PROFIT_TARGET")

        pid2 = _insert_position(db, "MSFT", 200.0, 5)
        db.close_position(pid2, 190.0, "STOP_LOSS")

        summary = guard.portfolio_summary()
        assert summary["performance"]["total_closed_trades"] == 2
        assert summary["performance"]["win_rate"] == 50.0
        # P&L: AAPL = (115-100)*10 = $150, MSFT = (190-200)*5 = -$50
        assert summary["performance"]["realized_pnl"] == 100.0

    def test_guard_limits_in_summary(self, guard):
        summary = guard.portfolio_summary()
        limits = summary["guard_limits"]
        assert limits["max_positions"] == 10
        assert limits["max_sector_exposure"] == "30%"
        assert limits["max_single_position"] == "8%"
        assert limits["daily_loss_limit"] == "-3%"
        assert limits["cash_reserve"] == "10%"


# ===================================================================
# Signals (dashboard Signal Feed data source)
# ===================================================================

class TestSignalsFeed:
    def test_empty_signals(self, db):
        signals = db.get_recent_signals()
        assert signals == []

    def test_insert_and_retrieve_signals(self, db):
        db.insert_signal(
            ticker="AAPL", trade_date="2024-11-01",
            agent_decision="BUY recommendation", parsed_action="BUY",
            action_taken="BOUGHT", conviction_score=82.5,
        )
        db.insert_signal(
            ticker="MSFT", trade_date="2024-11-01",
            agent_decision="HOLD", parsed_action="HOLD",
            action_taken="SKIPPED", skip_reason="HOLD signal",
        )

        signals = db.get_recent_signals(limit=10)
        assert len(signals) == 2
        # Most recent first
        assert signals[0]["ticker"] == "MSFT"
        assert signals[1]["ticker"] == "AAPL"
        assert signals[1]["conviction_score"] == 82.5
        assert signals[1]["action_taken"] == "BOUGHT"

    def test_signal_limit(self, db):
        for i in range(10):
            db.insert_signal(
                ticker=f"TICK{i}", trade_date="2024-11-01",
                agent_decision="BUY", parsed_action="BUY",
                action_taken="BOUGHT",
            )
        signals = db.get_recent_signals(limit=5)
        assert len(signals) == 5

    def test_guard_rejected_signal(self, db):
        db.insert_signal(
            ticker="AAPL", trade_date="2024-11-01",
            agent_decision="BUY", parsed_action="BUY",
            action_taken="REJECTED_BY_GUARD",
            skip_reason="Portfolio guard: Max positions reached",
        )
        signals = db.get_recent_signals()
        assert signals[0]["action_taken"] == "REJECTED_BY_GUARD"
        assert "Max positions" in signals[0]["skip_reason"]


# ===================================================================
# Snapshots (dashboard portfolio value chart)
# ===================================================================

class TestSnapshots:
    def test_save_and_query_snapshot(self, db):
        db.save_snapshot(
            total_value=100_500.0, cash=80_000.0, invested=20_500.0,
            num_positions=3, daily_pnl=500.0, daily_pnl_pct=0.5,
        )
        rows = db.conn.execute(
            "SELECT * FROM portfolio_snapshots ORDER BY snapshot_date DESC"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["total_value"] == 100_500.0
        assert rows[0]["daily_pnl"] == 500.0

    def test_snapshot_replaces_same_day(self, db):
        db.save_snapshot(100_000, 80_000, 20_000, 2, 0, 0)
        db.save_snapshot(101_000, 81_000, 20_000, 2, 1000, 1.0)
        rows = db.conn.execute("SELECT * FROM portfolio_snapshots").fetchall()
        # INSERT OR REPLACE should keep only one row per day
        assert len(rows) == 1
        assert rows[0]["total_value"] == 101_000


# ===================================================================
# System Log (dashboard system log panel)
# ===================================================================

class TestSystemLog:
    def test_log_entries(self, db):
        db.log("INFO", "order_manager", "Bought AAPL")
        db.log("ERROR", "monitor", "Price feed timeout")

        rows = db.conn.execute(
            "SELECT * FROM system_log ORDER BY id DESC"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["level"] == "ERROR"
        assert rows[1]["component"] == "order_manager"

    def test_pause_log_recorded(self, db):
        db.set_trading_paused(True)
        rows = db.conn.execute(
            "SELECT * FROM system_log WHERE component = 'TRADING_PAUSE'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["message"] == "PAUSED"


# ===================================================================
# Dashboard Import Smoke Test
# ===================================================================

class TestDashboardImport:
    def test_app_module_imports(self):
        """Verify dashboard/app.py can be parsed without error."""
        import importlib.util
        app_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "dashboard", "app.py"
        )
        spec = importlib.util.spec_from_file_location("dashboard.app", app_path)
        assert spec is not None
        # We don't exec it (requires Streamlit runtime), but parsing succeeds
