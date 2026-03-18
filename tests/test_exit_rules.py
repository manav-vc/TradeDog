"""
Phase 4 — Exit rules and position monitoring tests for TradeDog.

Tests each exit rule in isolation with mocked prices, then tests
the PositionMonitor integration with the paper broker.
"""

import os
import asyncio
import tempfile
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock, call

from database.db import Database
from database.models import Position
from execution.paper_broker import PaperBroker
from monitoring.price_feed import PriceFeed
from monitoring.exit_rules import (
    check_stop_loss,
    check_profit_target,
    check_trailing_stop,
    check_time_exit,
    check_reversal,
    check_all_exit_rules,
    ExitSignal,
)
from monitoring.position_monitor import PositionMonitor
from monitoring.alert_manager import LogAlertManager


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
def paper_broker(db):
    return PaperBroker(db=db, starting_cash=100_000.0)


def _make_position(**kwargs) -> Position:
    """Helper to create a Position with sensible defaults."""
    defaults = dict(
        id=1,
        ticker="AAPL",
        entry_price=100.0,
        qty=10,
        cost_basis=1000.0,
        highest_price=100.0,
        entry_time=datetime.now(timezone.utc),
        status="OPEN",
    )
    defaults.update(kwargs)
    return Position(**defaults)


# ===================================================================
# Stop Loss Tests
# ===================================================================

class TestStopLoss:
    def test_no_exit_when_price_above_stop(self):
        pos = _make_position(entry_price=100.0)
        signal = check_stop_loss(pos, current_price=95.0, stop_loss_pct=0.08)
        assert signal.should_exit is False

    def test_exit_at_exact_threshold(self):
        pos = _make_position(entry_price=100.0)
        signal = check_stop_loss(pos, current_price=92.0, stop_loss_pct=0.08)
        assert signal.should_exit is True
        assert signal.rule == "STOP_LOSS"

    def test_exit_below_threshold(self):
        pos = _make_position(entry_price=100.0)
        signal = check_stop_loss(pos, current_price=88.0, stop_loss_pct=0.08)
        assert signal.should_exit is True

    def test_no_exit_small_loss(self):
        pos = _make_position(entry_price=100.0)
        signal = check_stop_loss(pos, current_price=97.0, stop_loss_pct=0.08)
        assert signal.should_exit is False

    def test_custom_threshold(self):
        pos = _make_position(entry_price=100.0)
        signal = check_stop_loss(pos, current_price=95.0, stop_loss_pct=0.05)
        assert signal.should_exit is True  # 5% loss >= 5% threshold


# ===================================================================
# Profit Target Tests
# ===================================================================

class TestProfitTarget:
    def test_no_exit_below_target(self):
        pos = _make_position(entry_price=100.0)
        signal = check_profit_target(pos, current_price=110.0, profit_target_pct=0.15)
        assert signal.should_exit is False

    def test_exit_at_target(self):
        pos = _make_position(entry_price=100.0)
        signal = check_profit_target(pos, current_price=115.0, profit_target_pct=0.15)
        assert signal.should_exit is True
        assert signal.rule == "PROFIT_TARGET"

    def test_exit_above_target(self):
        pos = _make_position(entry_price=100.0)
        signal = check_profit_target(pos, current_price=130.0, profit_target_pct=0.15)
        assert signal.should_exit is True

    def test_no_exit_when_down(self):
        pos = _make_position(entry_price=100.0)
        signal = check_profit_target(pos, current_price=90.0, profit_target_pct=0.15)
        assert signal.should_exit is False

    def test_reason_includes_details(self):
        pos = _make_position(entry_price=100.0, ticker="NVDA")
        signal = check_profit_target(pos, current_price=120.0, profit_target_pct=0.15)
        assert "NVDA" in signal.reason
        assert "15%" in signal.reason


# ===================================================================
# Trailing Stop Tests
# ===================================================================

class TestTrailingStop:
    def test_no_exit_price_near_peak(self):
        pos = _make_position(entry_price=100.0, highest_price=120.0)
        signal = check_trailing_stop(pos, current_price=115.0, trail_pct=0.07)
        assert signal.should_exit is False

    def test_exit_when_drops_from_peak(self):
        pos = _make_position(entry_price=100.0, highest_price=120.0)
        # 7% drop from 120 = 111.6, so 111.0 triggers
        signal = check_trailing_stop(pos, current_price=111.0, trail_pct=0.07)
        assert signal.should_exit is True
        assert signal.rule == "TRAILING_STOP"

    def test_no_exit_when_at_trail_level(self):
        pos = _make_position(entry_price=100.0, highest_price=120.0)
        trail_level = 120.0 * (1 - 0.07)  # 111.6
        signal = check_trailing_stop(pos, current_price=112.0, trail_pct=0.07)
        assert signal.should_exit is False

    def test_updates_highest_price_callback(self):
        callback = MagicMock()
        pos = _make_position(entry_price=100.0, highest_price=110.0)
        signal = check_trailing_stop(
            pos, current_price=115.0, trail_pct=0.07,
            update_highest_callback=callback,
        )
        assert signal.should_exit is False
        callback.assert_called_once_with(1, 115.0)

    def test_no_callback_when_price_below_highest(self):
        callback = MagicMock()
        pos = _make_position(entry_price=100.0, highest_price=110.0)
        check_trailing_stop(
            pos, current_price=108.0, trail_pct=0.07,
            update_highest_callback=callback,
        )
        callback.assert_not_called()

    def test_uses_entry_price_when_no_highest(self):
        pos = _make_position(entry_price=100.0, highest_price=None)
        signal = check_trailing_stop(pos, current_price=92.0, trail_pct=0.07)
        # 7% of 100 = 93, so 92 triggers
        assert signal.should_exit is True


# ===================================================================
# Time-Based Exit Tests
# ===================================================================

class TestTimeExit:
    def test_no_exit_within_hold_period(self):
        pos = _make_position(
            entry_time=datetime.now(timezone.utc) - timedelta(days=15)
        )
        signal = check_time_exit(pos, max_hold_days=30)
        assert signal.should_exit is False

    def test_exit_after_max_hold(self):
        pos = _make_position(
            entry_time=datetime.now(timezone.utc) - timedelta(days=31)
        )
        signal = check_time_exit(pos, max_hold_days=30)
        assert signal.should_exit is True
        assert signal.rule == "TIME_EXIT"

    def test_exit_at_exact_max_hold(self):
        pos = _make_position(
            entry_time=datetime.now(timezone.utc) - timedelta(days=30)
        )
        signal = check_time_exit(pos, max_hold_days=30)
        assert signal.should_exit is True

    def test_custom_hold_period(self):
        pos = _make_position(
            entry_time=datetime.now(timezone.utc) - timedelta(days=8)
        )
        signal = check_time_exit(pos, max_hold_days=7)
        assert signal.should_exit is True

    def test_handles_string_entry_time(self):
        entry = (datetime.now(timezone.utc) - timedelta(days=35)).isoformat()
        pos = _make_position(entry_time=entry)
        signal = check_time_exit(pos, max_hold_days=30)
        assert signal.should_exit is True

    def test_handles_none_entry_time(self):
        pos = _make_position(entry_time=None)
        signal = check_time_exit(pos, max_hold_days=30)
        assert signal.should_exit is False


# ===================================================================
# Reversal Detection Tests
# ===================================================================

class TestReversal:
    @patch("monitoring.exit_rules._get_indicator")
    def test_reversal_triggered(self, mock_indicators):
        """RSI > 75 + negative MACD histogram → reversal."""
        # Mock RSI = 80
        mock_indicators.side_effect = [
            "# RSI\nDate,rsi\n2024-11-01,80.0",    # RSI
            "# MACDH\nDate,macdh\n2024-11-01,-0.5",  # MACD histogram
        ]
        pos = _make_position(ticker="AAPL")
        signal = check_reversal(pos, current_price=150.0, curr_date="2024-11-01")
        assert signal.should_exit is True
        assert signal.rule == "REVERSAL"

    @patch("monitoring.exit_rules._get_indicator")
    def test_no_reversal_low_rsi(self, mock_indicators):
        """RSI < 75 → no reversal even with negative MACD."""
        mock_indicators.side_effect = [
            "# RSI\nDate,rsi\n2024-11-01,60.0",
            "# MACDH\nDate,macdh\n2024-11-01,-0.5",
        ]
        pos = _make_position(ticker="AAPL")
        signal = check_reversal(pos, current_price=150.0, curr_date="2024-11-01")
        assert signal.should_exit is False

    @patch("monitoring.exit_rules._get_indicator")
    def test_no_reversal_positive_macd(self, mock_indicators):
        """Positive MACD → no reversal even with high RSI."""
        mock_indicators.side_effect = [
            "# RSI\nDate,rsi\n2024-11-01,80.0",
            "# MACDH\nDate,macdh\n2024-11-01,0.3",
        ]
        pos = _make_position(ticker="AAPL")
        signal = check_reversal(pos, current_price=150.0, curr_date="2024-11-01")
        assert signal.should_exit is False

    @patch("monitoring.exit_rules._get_indicator")
    def test_reversal_graceful_on_error(self, mock_indicators):
        """If indicator fetch fails, returns no-exit (safe default)."""
        mock_indicators.side_effect = Exception("API error")
        pos = _make_position(ticker="AAPL")
        signal = check_reversal(pos, current_price=150.0, curr_date="2024-11-01")
        assert signal.should_exit is False


# ===================================================================
# Combined Check (check_all_exit_rules)
# ===================================================================

class TestCombinedCheck:
    def test_stop_loss_highest_priority(self):
        """Stop loss fires before profit target (shouldn't happen in practice)."""
        # Price is both 15% above AND 8% below entry? No — test priority with stop loss.
        pos = _make_position(entry_price=100.0, highest_price=100.0)
        signal = check_all_exit_rules(pos, current_price=91.0)
        assert signal.should_exit is True
        assert signal.rule == "STOP_LOSS"

    def test_profit_target_fires(self):
        pos = _make_position(entry_price=100.0, highest_price=120.0)
        signal = check_all_exit_rules(pos, current_price=120.0)
        assert signal.should_exit is True
        assert signal.rule == "PROFIT_TARGET"

    def test_trailing_stop_fires_after_rise(self):
        pos = _make_position(entry_price=100.0, highest_price=112.0)
        # Price rose to 112, dropped to 104 (7.1% from peak)
        signal = check_all_exit_rules(pos, current_price=104.0)
        assert signal.should_exit is True
        assert signal.rule == "TRAILING_STOP"

    def test_time_exit_fires(self):
        pos = _make_position(
            entry_price=100.0, highest_price=105.0,
            entry_time=datetime.now(timezone.utc) - timedelta(days=31),
        )
        signal = check_all_exit_rules(pos, current_price=102.0)
        assert signal.should_exit is True
        assert signal.rule == "TIME_EXIT"

    def test_no_exit_healthy_position(self):
        pos = _make_position(
            entry_price=100.0, highest_price=108.0,
            entry_time=datetime.now(timezone.utc) - timedelta(days=10),
        )
        signal = check_all_exit_rules(pos, current_price=106.0)
        assert signal.should_exit is False
        assert signal.rule == "NONE"


# ===================================================================
# PriceFeed Tests
# ===================================================================

class TestPriceFeed:
    @patch("monitoring.price_feed.yf")
    def test_fetches_price(self, mock_yf):
        mock_yf.Ticker.return_value.info = {"currentPrice": 150.0}
        feed = PriceFeed(cache_ttl_seconds=60)
        price = feed.get_price("AAPL")
        assert price == 150.0

    @patch("monitoring.price_feed.yf")
    def test_caches_price(self, mock_yf):
        mock_yf.Ticker.return_value.info = {"currentPrice": 150.0}
        feed = PriceFeed(cache_ttl_seconds=60)
        feed.get_price("AAPL")
        feed.get_price("AAPL")
        # Should only call yfinance once due to caching
        assert mock_yf.Ticker.call_count == 1

    @patch("monitoring.price_feed.yf")
    def test_batch_prices(self, mock_yf):
        mock_yf.Ticker.return_value.info = {"currentPrice": 100.0}
        feed = PriceFeed()
        prices = feed.get_prices_batch(["AAPL", "NVDA"])
        assert len(prices) == 2

    @patch("monitoring.price_feed.yf")
    def test_invalidate_cache(self, mock_yf):
        mock_yf.Ticker.return_value.info = {"currentPrice": 150.0}
        feed = PriceFeed(cache_ttl_seconds=600)
        feed.get_price("AAPL")
        feed.invalidate("AAPL")
        feed.get_price("AAPL")
        # Should call twice because cache was invalidated
        assert mock_yf.Ticker.call_count == 2


# ===================================================================
# PositionMonitor Integration Tests
# ===================================================================

class TestPositionMonitor:
    @patch("execution.paper_broker.yf")
    def test_check_once_no_positions(self, mock_yf, paper_broker, db):
        """No positions → no exits."""
        monitor = PositionMonitor(
            broker=paper_broker, db=db,
            interval_seconds=1,
        )
        triggered = monitor.check_once()
        assert triggered == []

    @patch("execution.paper_broker.yf")
    @patch("monitoring.price_feed.yf")
    def test_stop_loss_triggers_sell(self, mock_price_yf, mock_broker_yf, paper_broker, db):
        """Position drops past stop loss → auto-sell."""
        # Buy at 100
        mock_broker_yf.Ticker.return_value.info = {"currentPrice": 100.0}
        paper_broker.place_market_buy("AAPL", 10)
        assert len(paper_broker.get_positions()) == 1

        # Price drops to 91 (9% loss > 8% stop)
        mock_price_yf.Ticker.return_value.info = {"currentPrice": 91.0}
        mock_broker_yf.Ticker.return_value.info = {"currentPrice": 91.0}

        monitor = PositionMonitor(
            broker=paper_broker, db=db,
            price_feed=PriceFeed(cache_ttl_seconds=0),
            stop_loss_pct=0.08,
        )
        triggered = monitor.check_once()

        assert len(triggered) == 1
        assert triggered[0].rule == "STOP_LOSS"
        # Position should be closed
        assert len(db.get_open_positions()) == 0
        closed = db.get_closed_positions()
        assert len(closed) == 1
        assert closed[0].exit_reason == "STOP_LOSS"

    @patch("execution.paper_broker.yf")
    @patch("monitoring.price_feed.yf")
    def test_profit_target_triggers_sell(self, mock_price_yf, mock_broker_yf, paper_broker, db):
        """Position rises past profit target → auto-sell."""
        mock_broker_yf.Ticker.return_value.info = {"currentPrice": 100.0}
        paper_broker.place_market_buy("AAPL", 10)

        # Price rises to 116 (16% > 15% target)
        mock_price_yf.Ticker.return_value.info = {"currentPrice": 116.0}
        mock_broker_yf.Ticker.return_value.info = {"currentPrice": 116.0}

        monitor = PositionMonitor(
            broker=paper_broker, db=db,
            price_feed=PriceFeed(cache_ttl_seconds=0),
            profit_target_pct=0.15,
        )
        triggered = monitor.check_once()

        assert len(triggered) == 1
        assert triggered[0].rule == "PROFIT_TARGET"
        closed = db.get_closed_positions()
        assert closed[0].exit_reason == "PROFIT_TARGET"

    @patch("execution.paper_broker.yf")
    @patch("monitoring.price_feed.yf")
    def test_trailing_stop_triggers_sell(self, mock_price_yf, mock_broker_yf, paper_broker, db):
        """Position peaked then dropped → trailing stop fires."""
        mock_broker_yf.Ticker.return_value.info = {"currentPrice": 100.0}
        paper_broker.place_market_buy("AAPL", 10)

        # Simulate peak at 112, then drop to 104
        db.update_highest_price(1, 112.0)
        mock_price_yf.Ticker.return_value.info = {"currentPrice": 104.0}
        mock_broker_yf.Ticker.return_value.info = {"currentPrice": 104.0}

        monitor = PositionMonitor(
            broker=paper_broker, db=db,
            price_feed=PriceFeed(cache_ttl_seconds=0),
            trailing_stop_pct=0.07,
        )
        triggered = monitor.check_once()

        assert len(triggered) == 1
        assert triggered[0].rule == "TRAILING_STOP"

    @patch("execution.paper_broker.yf")
    @patch("monitoring.price_feed.yf")
    def test_no_exit_healthy_position(self, mock_price_yf, mock_broker_yf, paper_broker, db):
        """Healthy position → no exits triggered."""
        mock_broker_yf.Ticker.return_value.info = {"currentPrice": 100.0}
        paper_broker.place_market_buy("AAPL", 10)

        # Price up 5% — healthy
        mock_price_yf.Ticker.return_value.info = {"currentPrice": 105.0}

        monitor = PositionMonitor(
            broker=paper_broker, db=db,
            price_feed=PriceFeed(cache_ttl_seconds=0),
        )
        triggered = monitor.check_once()
        assert triggered == []
        # Position still open
        assert len(db.get_open_positions()) == 1

    @patch("execution.paper_broker.yf")
    @patch("monitoring.price_feed.yf")
    def test_updates_highest_price(self, mock_price_yf, mock_broker_yf, paper_broker, db):
        """Monitor updates highest_price when price rises."""
        mock_broker_yf.Ticker.return_value.info = {"currentPrice": 100.0}
        paper_broker.place_market_buy("AAPL", 10)

        mock_price_yf.Ticker.return_value.info = {"currentPrice": 110.0}

        monitor = PositionMonitor(
            broker=paper_broker, db=db,
            price_feed=PriceFeed(cache_ttl_seconds=0),
        )
        monitor.check_once()

        pos = db.get_position(1)
        assert pos.highest_price == 110.0

    @patch("execution.paper_broker.yf")
    @patch("monitoring.price_feed.yf")
    def test_multiple_positions_checked(self, mock_price_yf, mock_broker_yf, paper_broker, db):
        """Monitor checks all open positions."""
        mock_broker_yf.Ticker.return_value.info = {"currentPrice": 100.0}
        paper_broker.place_market_buy("AAPL", 10)
        paper_broker.place_market_buy("NVDA", 5)

        # Both healthy
        mock_price_yf.Ticker.return_value.info = {"currentPrice": 105.0}

        monitor = PositionMonitor(
            broker=paper_broker, db=db,
            price_feed=PriceFeed(cache_ttl_seconds=0),
        )
        triggered = monitor.check_once()
        assert triggered == []
        assert len(db.get_open_positions()) == 2

    @patch("execution.paper_broker.yf")
    @patch("monitoring.price_feed.yf")
    def test_async_loop_runs(self, mock_price_yf, mock_broker_yf, paper_broker, db):
        """Async monitor loop runs and respects max_cycles."""
        mock_broker_yf.Ticker.return_value.info = {"currentPrice": 100.0}
        mock_price_yf.Ticker.return_value.info = {"currentPrice": 105.0}

        monitor = PositionMonitor(
            broker=paper_broker, db=db,
            price_feed=PriceFeed(cache_ttl_seconds=0),
            interval_seconds=0,  # No delay for testing
        )

        asyncio.run(monitor.run_async(max_cycles=3))
        # Should have completed without error


# ===================================================================
# AlertManager Tests
# ===================================================================

class TestAlertManager:
    def test_log_alert_on_close(self, db):
        alert = LogAlertManager(db=db)
        pos = _make_position(ticker="AAPL", entry_price=100.0, qty=10)
        signal = ExitSignal(
            should_exit=True, rule="PROFIT_TARGET",
            reason="15% gain", current_price=115.0,
        )
        alert.on_position_closed(pos, signal)

        logs = db.conn.execute(
            "SELECT * FROM system_log WHERE component='monitor'"
        ).fetchall()
        assert len(logs) >= 1
        assert "AAPL" in logs[-1]["message"]
        assert "PROFIT_TARGET" in logs[-1]["message"]

    def test_log_alert_on_error(self, db):
        alert = LogAlertManager(db=db)
        alert.on_monitor_error("NVDA", "Price fetch failed")

        logs = db.conn.execute(
            "SELECT * FROM system_log WHERE level='ERROR'"
        ).fetchall()
        assert len(logs) >= 1
        assert "NVDA" in logs[-1]["message"]
