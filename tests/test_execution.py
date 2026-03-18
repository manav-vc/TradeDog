"""
Phase 2 — Execution layer tests for TradeDog.

Tests the full pipeline: Database → Models → PaperBroker → OrderManager → PositionSizer.
All tests use an in-memory SQLite database — no persistent state between tests.
"""

import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock

from database.db import Database
from database.models import Position, Order, AccountInfo
from execution.paper_broker import PaperBroker
from execution.order_manager import OrderManager
from portfolio.position_sizer import calculate_position_size


# ===================================================================
# Fixtures
# ===================================================================

@pytest.fixture
def db():
    """Create a fresh in-memory database for each test."""
    # Use a temp file so paper_broker can reuse the connection
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    database = Database(db_path=path)
    yield database
    database.close()
    os.unlink(path)


@pytest.fixture
def paper_broker(db):
    """Paper broker with $100k starting cash and mocked prices."""
    broker = PaperBroker(db=db, starting_cash=100_000.0)
    return broker


@pytest.fixture
def order_manager(paper_broker, db):
    """OrderManager wired to the paper broker."""
    return OrderManager(broker=paper_broker, db=db, max_position_pct=0.05)


# ===================================================================
# Database & Models Tests
# ===================================================================

class TestDatabase:
    def test_schema_creates_tables(self, db):
        tables = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = [t["name"] for t in tables]
        assert "positions" in table_names
        assert "orders" in table_names
        assert "signals" in table_names
        assert "portfolio_snapshots" in table_names
        assert "system_log" in table_names

    def test_insert_and_get_position(self, db):
        pos = Position(ticker="AAPL", entry_price=150.0, qty=10, cost_basis=1500.0)
        pos_id = db.insert_position(pos)
        assert pos_id >= 1

        fetched = db.get_position(pos_id)
        assert fetched is not None
        assert fetched.ticker == "AAPL"
        assert fetched.entry_price == 150.0
        assert fetched.qty == 10
        assert fetched.status == "OPEN"

    def test_close_position(self, db):
        pos = Position(ticker="NVDA", entry_price=800.0, qty=5, cost_basis=4000.0)
        pos_id = db.insert_position(pos)
        db.close_position(pos_id, exit_price=900.0, exit_reason="PROFIT_TARGET")

        fetched = db.get_position(pos_id)
        assert fetched.status == "CLOSED"
        assert fetched.exit_price == 900.0
        assert fetched.exit_reason == "PROFIT_TARGET"

    def test_open_and_closed_queries(self, db):
        p1 = Position(ticker="AAPL", entry_price=150.0, qty=10, cost_basis=1500.0)
        p2 = Position(ticker="NVDA", entry_price=800.0, qty=5, cost_basis=4000.0)
        id1 = db.insert_position(p1)
        id2 = db.insert_position(p2)
        db.close_position(id2, 900.0, "PROFIT_TARGET")

        assert len(db.get_open_positions()) == 1
        assert len(db.get_closed_positions()) == 1
        assert db.get_open_positions()[0].ticker == "AAPL"

    def test_update_highest_price(self, db):
        pos = Position(ticker="MSFT", entry_price=400.0, qty=5, cost_basis=2000.0)
        pos_id = db.insert_position(pos)
        db.update_highest_price(pos_id, 420.0)
        fetched = db.get_position(pos_id)
        assert fetched.highest_price == 420.0

        # Should NOT downgrade
        db.update_highest_price(pos_id, 410.0)
        fetched = db.get_position(pos_id)
        assert fetched.highest_price == 420.0

    def test_insert_and_get_orders(self, db):
        pos = Position(ticker="AAPL", entry_price=150.0, qty=10, cost_basis=1500.0)
        pos_id = db.insert_position(pos)

        order = Order(ticker="AAPL", side="BUY", qty=10, filled_price=150.0,
                      status="FILLED", position_id=pos_id)
        order_id = db.insert_order(order)

        orders = db.get_orders_for_position(pos_id)
        assert len(orders) == 1
        assert orders[0].ticker == "AAPL"
        assert orders[0].filled_price == 150.0

    def test_insert_signal(self, db):
        sig_id = db.insert_signal(
            ticker="AAPL", trade_date="2024-11-01",
            agent_decision="BUY with strong conviction",
            parsed_action="BUY", action_taken="BOUGHT",
        )
        assert sig_id >= 1

        signals = db.get_recent_signals(limit=10)
        assert len(signals) == 1
        assert signals[0]["ticker"] == "AAPL"

    def test_system_log(self, db):
        db.log("INFO", "test", "hello world")
        rows = db.conn.execute("SELECT * FROM system_log").fetchall()
        assert len(rows) == 1  # excluding broker init log
        assert rows[0]["message"] == "hello world"

    def test_account_summary(self, db):
        # No positions — all cash
        acct = db.get_account_summary(starting_cash=100_000.0)
        assert acct.total_value == 100_000.0
        assert acct.cash == 100_000.0
        assert acct.num_positions == 0

        # Add a position
        pos = Position(ticker="AAPL", entry_price=150.0, qty=10, cost_basis=1500.0)
        db.insert_position(pos)
        acct = db.get_account_summary(starting_cash=100_000.0)
        assert acct.invested == 1500.0
        assert acct.cash == 98_500.0
        assert acct.num_positions == 1


class TestModels:
    def test_position_pnl(self):
        pos = Position(ticker="AAPL", entry_price=100.0, qty=10, exit_price=120.0)
        assert pos.pnl == 200.0
        assert abs(pos.pnl_pct - 20.0) < 0.01

    def test_position_pnl_none_when_open(self):
        pos = Position(ticker="AAPL", entry_price=100.0, qty=10)
        assert pos.pnl is None
        assert pos.pnl_pct is None

    def test_position_market_value(self):
        pos = Position(ticker="NVDA", entry_price=800.0, qty=5, status="OPEN")
        assert pos.market_value == 4000.0


# ===================================================================
# Position Sizer Tests
# ===================================================================

class TestPositionSizer:
    def test_basic_sizing(self):
        # $100k account, 5% max, $150 stock = $5000 / 150 = 33 shares
        shares = calculate_position_size(100_000, 150.0, max_position_pct=0.05)
        assert shares == 33

    def test_expensive_stock(self):
        # $100k account, 5% max, $6000 stock = $5000 / 6000 < 1, but min 0
        shares = calculate_position_size(100_000, 6000.0, max_position_pct=0.05)
        assert shares == 0  # Can't afford even 1 share within limit

    def test_conviction_scaling(self):
        # Full conviction = 33 shares, half conviction = 16 shares
        full = calculate_position_size(100_000, 150.0, conviction_score=1.0)
        half = calculate_position_size(100_000, 150.0, conviction_score=0.5)
        assert full == 33
        assert half == 16

    def test_zero_price(self):
        assert calculate_position_size(100_000, 0.0) == 0

    def test_zero_account(self):
        assert calculate_position_size(0, 150.0) == 0

    def test_minimum_one_share(self):
        # $100k, 5% = $5000, stock at $4999 → 1 share
        shares = calculate_position_size(100_000, 4999.0, max_position_pct=0.05)
        assert shares == 1


# ===================================================================
# Paper Broker Tests
# ===================================================================

class TestPaperBroker:
    @patch("execution.paper_broker.yf")
    def test_buy_creates_position(self, mock_yf, paper_broker):
        mock_yf.Ticker.return_value.info = {"currentPrice": 150.0}

        order = paper_broker.place_market_buy("AAPL", 10)
        assert order.status == "FILLED"
        assert order.filled_price == 150.0
        assert order.side == "BUY"

        positions = paper_broker.get_positions()
        assert len(positions) == 1
        assert positions[0].ticker == "AAPL"
        assert positions[0].qty == 10

    @patch("execution.paper_broker.yf")
    def test_sell_closes_position(self, mock_yf, paper_broker):
        mock_yf.Ticker.return_value.info = {"currentPrice": 150.0}
        paper_broker.place_market_buy("AAPL", 10)

        mock_yf.Ticker.return_value.info = {"currentPrice": 170.0}
        sell_order = paper_broker.place_market_sell("AAPL", 10)
        assert sell_order.status == "FILLED"
        assert sell_order.filled_price == 170.0

        # Position should now be closed
        assert len(paper_broker.get_positions()) == 0

    @patch("execution.paper_broker.yf")
    def test_sell_no_position_rejected(self, mock_yf, paper_broker):
        mock_yf.Ticker.return_value.info = {"currentPrice": 150.0}
        order = paper_broker.place_market_sell("AAPL", 10)
        assert order.status == "REJECTED"

    @patch("execution.paper_broker.yf")
    def test_buy_insufficient_cash_rejected(self, mock_yf, paper_broker):
        mock_yf.Ticker.return_value.info = {"currentPrice": 50000.0}
        order = paper_broker.place_market_buy("BRK-A", 3)  # $150k > $100k
        assert order.status == "REJECTED"

    @patch("execution.paper_broker.yf")
    def test_account_updates_after_buy(self, mock_yf, paper_broker):
        mock_yf.Ticker.return_value.info = {"currentPrice": 100.0}

        acct_before = paper_broker.get_account()
        assert acct_before.cash == 100_000.0

        paper_broker.place_market_buy("AAPL", 10)
        acct_after = paper_broker.get_account()
        assert acct_after.cash == 99_000.0
        assert acct_after.invested == 1_000.0
        assert acct_after.num_positions == 1

    @patch("execution.paper_broker.yf")
    def test_multiple_positions(self, mock_yf, paper_broker):
        mock_yf.Ticker.return_value.info = {"currentPrice": 100.0}
        paper_broker.place_market_buy("AAPL", 10)
        paper_broker.place_market_buy("NVDA", 5)

        positions = paper_broker.get_positions()
        assert len(positions) == 2
        tickers = {p.ticker for p in positions}
        assert tickers == {"AAPL", "NVDA"}


# ===================================================================
# Order Manager Tests
# ===================================================================

class TestOrderManager:
    @patch("execution.paper_broker.yf")
    def test_buy_decision_executes(self, mock_yf, order_manager):
        mock_yf.Ticker.return_value.info = {"currentPrice": 150.0}

        order = order_manager.process_decision(
            ticker="AAPL", trade_date="2024-11-01", decision="BUY"
        )
        assert order is not None
        assert order.status == "FILLED"

    @patch("execution.paper_broker.yf")
    def test_hold_decision_skips(self, mock_yf, order_manager):
        result = order_manager.process_decision(
            ticker="AAPL", trade_date="2024-11-01", decision="HOLD"
        )
        assert result is None

    @patch("execution.paper_broker.yf")
    def test_sell_no_position_skips(self, mock_yf, order_manager):
        mock_yf.Ticker.return_value.info = {"currentPrice": 150.0}
        result = order_manager.process_decision(
            ticker="AAPL", trade_date="2024-11-01", decision="SELL"
        )
        assert result is None

    @patch("execution.paper_broker.yf")
    def test_duplicate_buy_skipped(self, mock_yf, order_manager):
        mock_yf.Ticker.return_value.info = {"currentPrice": 150.0}

        # First buy succeeds
        order1 = order_manager.process_decision(
            ticker="AAPL", trade_date="2024-11-01", decision="BUY"
        )
        assert order1 is not None

        # Second buy for same ticker is skipped
        order2 = order_manager.process_decision(
            ticker="AAPL", trade_date="2024-11-02", decision="BUY"
        )
        assert order2 is None

    @patch("execution.paper_broker.yf")
    def test_dry_run_does_not_execute(self, mock_yf, order_manager, db):
        mock_yf.Ticker.return_value.info = {"currentPrice": 150.0}
        order_manager.dry_run = True

        result = order_manager.process_decision(
            ticker="AAPL", trade_date="2024-11-01", decision="BUY"
        )
        assert result is None

        # No positions created
        assert len(order_manager.broker.get_positions()) == 0

        # But signal was logged
        signals = db.get_recent_signals()
        assert len(signals) == 1
        assert signals[0]["action_taken"] == "DRY_RUN"

    @patch("execution.paper_broker.yf")
    def test_decision_parsing(self, mock_yf, order_manager):
        """Verify various decision text formats are parsed correctly."""
        mock_yf.Ticker.return_value.info = {"currentPrice": 150.0}

        # "BUY" in the text → BUY
        order = order_manager.process_decision(
            ticker="AAPL", trade_date="2024-11-01",
            decision="Based on analysis, the recommendation is to BUY AAPL."
        )
        assert order is not None
        assert order.status == "FILLED"

    @patch("execution.paper_broker.yf")
    def test_signals_logged(self, mock_yf, order_manager, db):
        mock_yf.Ticker.return_value.info = {"currentPrice": 150.0}

        order_manager.process_decision(
            ticker="AAPL", trade_date="2024-11-01", decision="BUY"
        )
        order_manager.process_decision(
            ticker="MSFT", trade_date="2024-11-01", decision="HOLD"
        )

        signals = db.get_recent_signals()
        assert len(signals) == 2
        actions = {s["parsed_action"] for s in signals}
        assert actions == {"BUY", "HOLD"}


# ===================================================================
# Integration: Full Pipeline
# ===================================================================

class TestEndToEnd:
    @patch("execution.paper_broker.yf")
    def test_full_buy_sell_cycle(self, mock_yf, order_manager, db):
        """Simulate: BUY AAPL → check position → SELL AAPL → verify closed."""
        mock_yf.Ticker.return_value.info = {"currentPrice": 150.0}

        # BUY
        buy_order = order_manager.process_decision(
            ticker="AAPL", trade_date="2024-11-01", decision="BUY"
        )
        assert buy_order.status == "FILLED"
        assert len(order_manager.broker.get_positions()) == 1

        # Price goes up
        mock_yf.Ticker.return_value.info = {"currentPrice": 175.0}

        # SELL
        sell_order = order_manager.process_decision(
            ticker="AAPL", trade_date="2024-11-15", decision="SELL"
        )
        assert sell_order.status == "FILLED"
        assert len(order_manager.broker.get_positions()) == 0

        # Verify P&L in closed position
        closed = db.get_closed_positions()
        assert len(closed) == 1
        assert closed[0].exit_price == 175.0

    @patch("execution.paper_broker.yf")
    def test_multi_ticker_portfolio(self, mock_yf, order_manager):
        """Buy 3 different tickers, verify portfolio state."""
        prices = {"AAPL": 150.0, "NVDA": 800.0, "JPM": 200.0}
        mock_yf.Ticker.return_value.info = {"currentPrice": 150.0}

        for ticker in ["AAPL", "NVDA", "JPM"]:
            mock_yf.Ticker.return_value.info = {"currentPrice": prices[ticker]}
            order_manager.process_decision(
                ticker=ticker, trade_date="2024-11-01", decision="BUY"
            )

        positions = order_manager.broker.get_positions()
        assert len(positions) == 3
        tickers = {p.ticker for p in positions}
        assert tickers == {"AAPL", "NVDA", "JPM"}

        acct = order_manager.broker.get_account()
        assert acct.num_positions == 3
        assert acct.invested > 0
        assert acct.cash < 100_000.0
