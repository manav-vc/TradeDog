"""
Phase 5 — Portfolio guard tests for TradeDog.

Tests each guard rule in isolation, then integration with OrderManager.
All tests use temp SQLite databases — no persistent state.
"""

import os
import tempfile
import pytest
from unittest.mock import patch

from database.db import Database
from database.models import Position
from execution.paper_broker import PaperBroker
from execution.order_manager import OrderManager
from portfolio.portfolio_guard import (
    PortfolioGuard,
    GuardResult,
    GuardCheckResult,
)
from portfolio.conviction_gate import ConvictionGate


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
        "GOOG": "Technology", "META": "Technology", "AVGO": "Technology",
        "JPM": "Financials", "V": "Financials", "GS": "Financials",
        "UNH": "Healthcare", "JNJ": "Healthcare",
        "XOM": "Energy", "CVX": "Energy",
        "AMZN": "Consumer Discretionary", "TSLA": "Consumer Discretionary",
        "PG": "Consumer Staples",
        "CAT": "Industrials",
        "NEE": "Utilities",
    }
    return PortfolioGuard(
        db=db,
        starting_cash=100_000.0,
        max_positions=10,
        max_sector_exposure=0.30,
        max_single_position=0.08,
        daily_loss_limit=-0.03,
        cash_reserve=0.10,
        sector_map=sector_map,
    )


@pytest.fixture
def paper_broker(db):
    return PaperBroker(db=db, starting_cash=100_000.0)


def _insert_position(db, ticker, price, qty):
    """Helper to insert an open position directly."""
    pos = Position(
        ticker=ticker, entry_price=price, qty=qty,
        cost_basis=price * qty, status="OPEN",
    )
    return db.insert_position(pos)


# ===================================================================
# Max Positions Check
# ===================================================================

class TestMaxPositions:
    def test_under_limit_passes(self, guard, db):
        _insert_position(db, "AAPL", 150.0, 10)
        result = guard.can_open_position("MSFT", 1500.0)
        assert result.allowed is True

    def test_at_limit_blocked(self, guard, db):
        tickers = ["AAPL", "MSFT", "NVDA", "JPM", "UNH", "XOM", "AMZN", "PG", "CAT", "NEE"]
        for t in tickers:
            _insert_position(db, t, 100.0, 10)

        result = guard.can_open_position("GS", 1000.0)
        assert result.allowed is False
        assert "Max positions" in result.reason

    def test_11th_position_blocked(self, guard, db):
        """Task 5.11: 10 positions open, 11th is blocked."""
        for i, t in enumerate(["AAPL", "MSFT", "NVDA", "JPM", "UNH",
                                "XOM", "AMZN", "PG", "CAT", "NEE"]):
            _insert_position(db, t, 100.0, 10)

        result = guard.can_open_position("V", 1000.0)
        assert result.allowed is False
        assert any(c.rule == "MAX_POSITIONS" and not c.passed for c in result.checks)


# ===================================================================
# Duplicate Holding Check
# ===================================================================

class TestDuplicateHolding:
    def test_new_ticker_passes(self, guard, db):
        _insert_position(db, "AAPL", 150.0, 10)
        result = guard.can_open_position("MSFT", 1500.0)
        assert result.allowed is True

    def test_duplicate_blocked(self, guard, db):
        _insert_position(db, "AAPL", 150.0, 10)
        result = guard.can_open_position("AAPL", 1500.0)
        assert result.allowed is False
        assert "Already holding" in result.reason


# ===================================================================
# Sector Exposure Check
# ===================================================================

class TestSectorExposure:
    def test_under_limit_passes(self, guard, db):
        # Tech = $5k out of $100k = 5%, well under 30%
        _insert_position(db, "AAPL", 500.0, 10)
        result = guard.can_open_position("MSFT", 5000.0)
        assert result.allowed is True

    def test_over_limit_blocked(self, guard, db):
        # Tech already at $25k, adding $10k would be $35k / $100k = 35% > 30%
        _insert_position(db, "AAPL", 250.0, 100)  # $25k
        result = guard.can_open_position("NVDA", 10_000.0)
        assert result.allowed is False
        assert "Sector" in result.reason
        assert "Technology" in result.reason

    def test_different_sector_passes(self, guard, db):
        # Tech at $25k, but buying in Financials is fine
        _insert_position(db, "AAPL", 250.0, 100)  # $25k Tech
        result = guard.can_open_position("JPM", 5000.0)
        assert result.allowed is True

    def test_unknown_sector_treated_separately(self, guard, db):
        # Ticker not in sector map
        result = guard.can_open_position("ZZZZ", 5000.0)
        assert result.allowed is True


# ===================================================================
# Single Position Size Check
# ===================================================================

class TestSinglePositionSize:
    def test_small_position_passes(self, guard, db):
        # $5k out of $100k = 5%, under 8%
        result = guard.can_open_position("AAPL", 5000.0)
        assert result.allowed is True

    def test_large_position_blocked(self, guard, db):
        # $9k out of $100k = 9% > 8%
        result = guard.can_open_position("AAPL", 9000.0)
        assert result.allowed is False
        assert "POSITION_SIZE" in str([c.rule for c in result.checks if not c.passed])


# ===================================================================
# Daily Loss Limit Check
# ===================================================================

class TestDailyLossLimit:
    def test_no_loss_passes(self, guard, db):
        result = guard.can_open_position("AAPL", 5000.0)
        assert result.allowed is True

    def test_3pct_loss_blocked(self, guard, db):
        """Task 5.12: simulate 3% daily loss, verify no new buys."""
        # Save yesterday's snapshot at $100k
        from datetime import datetime, timezone, timedelta
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        db.conn.execute(
            """INSERT INTO portfolio_snapshots
               (snapshot_date, total_value, cash, invested, num_positions,
                daily_pnl, daily_pnl_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (yesterday, 100_000.0, 50_000.0, 50_000.0, 5, 0.0, 0.0),
        )
        db.conn.commit()

        # Simulate positions that lost value: invested $50k now worth less
        # Account summary: cash=$50k + invested=$47k = $97k
        # That's -3% from yesterday's $100k
        # To make get_account_summary return $97k, we need positions worth $47k
        # and starting_cash - $47k_cost + realized = cash
        # Let's insert positions with cost_basis that makes total = $97k
        # starting_cash=100k, invested=47k → cash=100k-47k=53k, total=100k
        # Hmm, account_summary doesn't track unrealized losses...
        # We need to mock the account value. Let me use a different approach.

        # Actually, the guard uses db.get_account_summary() which computes
        # total_value = cash + invested (cost basis, not market value).
        # For a 3% loss test, we need to reduce total_value below $97k.
        # The simplest way: insert positions with high cost, then close some at a loss.

        # Buy $50k worth, then close $3k at a loss to realize -$3k
        _insert_position(db, "AAPL", 100.0, 100)  # $10k
        _insert_position(db, "MSFT", 100.0, 100)  # $10k
        _insert_position(db, "NVDA", 100.0, 100)  # $10k

        # Close one at a big loss to bring total below $97k
        # Position 1 closed at $70 → loss = ($70-$100)*100 = -$3000
        db.close_position(1, 70.0, "STOP_LOSS")  # -$3k realized

        # Now: invested = $20k (positions 2,3), realized = -$3k
        # cash = 100k - 20k + (-3k) = 77k
        # total = 77k + 20k = 97k
        # Previous day was 100k → daily change = -3% = -0.03
        # That exactly hits the limit

        result = guard.can_open_position("JPM", 2000.0)
        assert result.allowed is False
        assert "Daily loss" in result.reason

    def test_small_loss_passes(self, guard, db):
        """1% loss should not trigger the 3% limit."""
        from datetime import datetime, timezone, timedelta
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        db.conn.execute(
            """INSERT INTO portfolio_snapshots
               (snapshot_date, total_value, cash, invested, num_positions,
                daily_pnl, daily_pnl_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (yesterday, 100_000.0, 50_000.0, 50_000.0, 5, 0.0, 0.0),
        )
        db.conn.commit()

        # Only $1k loss → 99k total → -1%, should pass
        _insert_position(db, "AAPL", 100.0, 100)
        db.close_position(1, 90.0, "STOP_LOSS")  # -$1k

        result = guard.can_open_position("JPM", 2000.0)
        assert result.allowed is True


# ===================================================================
# Cash Reserve Check
# ===================================================================

class TestCashReserve:
    def test_enough_cash_passes(self, guard, db):
        # $100k cash, buying $5k, leaves $95k > $10k reserve
        result = guard.can_open_position("AAPL", 5000.0)
        assert result.allowed is True

    def test_too_little_cash_blocked(self, guard, db):
        # Eat up most of the cash
        for i, t in enumerate(["AAPL", "MSFT", "NVDA", "JPM", "UNH"]):
            _insert_position(db, t, 180.0, 100)  # 5 × $18k = $90k invested

        # Cash = $100k - $90k = $10k. Reserve needed = 10% of $100k = $10k.
        # Buying $1k more would leave $9k < $10k reserve
        result = guard.can_open_position("XOM", 1000.0)
        assert result.allowed is False
        assert "Cash reserve" in result.reason


# ===================================================================
# Portfolio Summary
# ===================================================================

class TestPortfolioSummary:
    def test_empty_portfolio(self, guard):
        summary = guard.portfolio_summary()
        assert summary["account"]["total_value"] == 100_000.0
        assert summary["positions"]["open_count"] == 0
        assert summary["sector_breakdown"] == {}

    def test_with_positions(self, guard, db):
        _insert_position(db, "AAPL", 150.0, 10)  # Tech, $1500
        _insert_position(db, "JPM", 200.0, 5)     # Financials, $1000

        summary = guard.portfolio_summary()
        assert summary["positions"]["open_count"] == 2
        assert "Technology" in summary["sector_breakdown"]
        assert "Financials" in summary["sector_breakdown"]
        assert summary["account"]["invested"] == 2500.0

    def test_with_closed_trades(self, guard, db):
        pos_id = _insert_position(db, "AAPL", 100.0, 10)
        db.close_position(pos_id, 120.0, "PROFIT_TARGET")

        summary = guard.portfolio_summary()
        assert summary["performance"]["total_closed_trades"] == 1
        assert summary["performance"]["win_rate"] == 100.0
        assert summary["performance"]["realized_pnl"] == 200.0


# ===================================================================
# Guard Result Structure
# ===================================================================

class TestGuardResult:
    def test_all_checks_returned(self, guard, db):
        result = guard.can_open_position("AAPL", 5000.0)
        assert len(result.checks) == 6
        rules = {c.rule for c in result.checks}
        assert rules == {
            "MAX_POSITIONS", "DUPLICATE_HOLDING", "SECTOR_EXPOSURE",
            "POSITION_SIZE", "DAILY_LOSS", "CASH_RESERVE",
        }

    def test_first_failure_is_reason(self, guard, db):
        # Duplicate holding should be the reason
        _insert_position(db, "AAPL", 150.0, 10)
        result = guard.can_open_position("AAPL", 5000.0)
        assert not result.allowed
        assert "Already holding" in result.reason

    def test_custom_limits(self, db):
        strict_guard = PortfolioGuard(
            db=db, starting_cash=100_000.0,
            max_positions=3,
            max_sector_exposure=0.10,
            max_single_position=0.03,
            daily_loss_limit=-0.01,
            cash_reserve=0.20,
            sector_map={"AAPL": "Technology"},
        )
        # $4k position = 4% > 3% limit
        result = strict_guard.can_open_position("AAPL", 4000.0)
        assert not result.allowed


# ===================================================================
# Integration with OrderManager
# ===================================================================

class TestOrderManagerIntegration:
    @patch("execution.paper_broker.yf")
    def test_guard_blocks_buy(self, mock_yf, paper_broker, db, guard):
        """Guard blocks when position too large."""
        mock_yf.Ticker.return_value.info = {"currentPrice": 150.0}

        om = OrderManager(
            broker=paper_broker, db=db,
            portfolio_guard=guard,
            max_position_pct=0.10,  # 10% = $10k at $150 = 66 shares
        )
        # Try to buy — position would be $10k = 10% > 8% guard limit
        order = om.process_decision(
            ticker="AAPL", trade_date="2024-11-01", decision="BUY"
        )
        assert order is None

        signals = db.get_recent_signals()
        assert any(s["action_taken"] == "REJECTED_BY_GUARD" for s in signals)

    @patch("execution.paper_broker.yf")
    def test_guard_allows_small_buy(self, mock_yf, paper_broker, db, guard):
        """Guard allows when position within limits."""
        mock_yf.Ticker.return_value.info = {"currentPrice": 150.0}

        om = OrderManager(
            broker=paper_broker, db=db,
            portfolio_guard=guard,
            max_position_pct=0.05,  # 5% = $5k at $150 = 33 shares
        )
        order = om.process_decision(
            ticker="AAPL", trade_date="2024-11-01", decision="BUY"
        )
        assert order is not None
        assert order.status == "FILLED"

    @patch("execution.paper_broker.yf")
    def test_guard_blocks_11th_position(self, mock_yf, paper_broker, db, guard):
        """10 positions open, 11th buy is blocked by guard."""
        mock_yf.Ticker.return_value.info = {"currentPrice": 10.0}

        om = OrderManager(
            broker=paper_broker, db=db,
            portfolio_guard=guard,
            max_position_pct=0.01,  # Tiny positions
        )

        tickers = ["AAPL", "MSFT", "NVDA", "JPM", "UNH",
                    "XOM", "AMZN", "PG", "CAT", "NEE"]
        for t in tickers:
            om.process_decision(ticker=t, trade_date="2024-11-01", decision="BUY")

        assert len(paper_broker.get_positions()) == 10

        # 11th should be blocked
        order = om.process_decision(
            ticker="V", trade_date="2024-11-01", decision="BUY"
        )
        assert order is None

    @patch("execution.paper_broker.yf")
    def test_no_guard_passes_through(self, mock_yf, paper_broker, db):
        """No guard configured → buys pass through normally."""
        mock_yf.Ticker.return_value.info = {"currentPrice": 150.0}

        om = OrderManager(
            broker=paper_broker, db=db,
            portfolio_guard=None,
            max_position_pct=0.05,
        )
        order = om.process_decision(
            ticker="AAPL", trade_date="2024-11-01", decision="BUY"
        )
        assert order is not None
        assert order.status == "FILLED"
