"""
Tests for Phase 6C — Telegram Notifications.

Tests:
  - Message templates produce correct format
  - TelegramNotifier sends via HTTP and handles failures
  - TelegramAlertManager routes alerts through templates → notifier
  - CompositeAlertManager fans out to multiple backends
  - Notification toggles respect config
"""

import unittest
from unittest.mock import MagicMock, patch, PropertyMock
from dataclasses import dataclass

from notifications.templates import (
    trade_opened_message,
    trade_closed_message,
    guard_blocked_message,
    daily_loss_message,
    engine_error_message,
    weekly_digest_message,
)
from notifications.telegram_notifier import TelegramNotifier
from monitoring.alert_manager import (
    TelegramAlertManager,
    CompositeAlertManager,
    LogAlertManager,
)
from monitoring.exit_rules import ExitSignal


# ── Helpers ──────────────────────────────────────────────────────────

@dataclass
class FakePosition:
    id: int = 1
    ticker: str = "NVDA"
    entry_price: float = 142.30
    exit_price: float = None
    qty: int = 14
    cost_basis: float = 1992.20
    highest_price: float = 165.00
    entry_time: str = "2025-01-15 10:00:00"
    exit_time: str = None
    exit_reason: str = None
    status: str = "OPEN"
    side: str = "BUY"
    broker: str = "paper"


# =====================================================================
# Template Tests
# =====================================================================

class TestTradeOpenedTemplate(unittest.TestCase):

    def test_basic_format(self):
        msg = trade_opened_message("NVDA", 142.30, 14, 1992.20, 82.0, "RSI reversal")
        self.assertIn("TRADE OPENED", msg)
        self.assertIn("NVDA", msg)
        self.assertIn("$142.30", msg)
        self.assertIn("14 shares", msg)
        self.assertIn("82/100", msg)
        self.assertIn("RSI reversal", msg)

    def test_no_reason(self):
        msg = trade_opened_message("AAPL", 180.0, 5, 900.0)
        self.assertIn("AAPL", msg)
        self.assertNotIn("Reason:", msg)


class TestTradeClosedTemplate(unittest.TestCase):

    def test_profit_format(self):
        msg = trade_closed_message("NVDA", 142.30, 163.65, 14, 298.10, 14.9, "Profit target")
        self.assertIn("TRADE CLOSED", msg)
        self.assertIn("$142.30", msg)
        self.assertIn("$163.65", msg)
        self.assertIn("+$298.10", msg)
        self.assertIn("+14.9%", msg)

    def test_loss_format(self):
        msg = trade_closed_message("META", 350.0, 320.0, 10, -300.0, -8.6, "Stop loss")
        self.assertIn("-$300.00", msg)
        self.assertIn("-8.6%", msg)
        self.assertIn("Stop loss", msg)


class TestGuardBlockedTemplate(unittest.TestCase):

    def test_format(self):
        msg = guard_blocked_message("AAPL", "DAILY_LOSS", "Daily loss limit reached (-3.1%)")
        self.assertIn("PORTFOLIO GUARD", msg)
        self.assertIn("AAPL", msg)
        self.assertIn("DAILY_LOSS", msg)


class TestDailyLossTemplate(unittest.TestCase):

    def test_format(self):
        msg = daily_loss_message(-3.1, 24000.0)
        self.assertIn("DAILY LOSS LIMIT", msg)
        self.assertIn("-3.1%", msg)
        self.assertIn("$24,000.00", msg)


class TestEngineErrorTemplate(unittest.TestCase):

    def test_format(self):
        msg = engine_error_message("monitor", "Connection timeout")
        self.assertIn("ENGINE ERROR", msg)
        self.assertIn("monitor", msg)
        self.assertIn("Connection timeout", msg)


class TestWeeklyDigestTemplate(unittest.TestCase):

    def test_full_digest(self):
        msg = weekly_digest_message(
            portfolio_value=24840.0,
            week_pnl_pct=2.3,
            trades_opened=3,
            trades_closed=2,
            win_rate=67.0,
            best_ticker="MSFT",
            best_pnl_pct=11.2,
            worst_ticker="META",
            worst_pnl_pct=-4.1,
        )
        self.assertIn("WEEKLY DIGEST", msg)
        self.assertIn("$24,840.00", msg)
        self.assertIn("+2.3%", msg)
        self.assertIn("3 opened", msg)
        self.assertIn("67%", msg)
        self.assertIn("MSFT", msg)
        self.assertIn("META", msg)

    def test_no_best_worst(self):
        msg = weekly_digest_message(10000.0, -1.0, 0, 0, 0.0)
        self.assertIn("WEEKLY DIGEST", msg)
        self.assertNotIn("Best:", msg)


# =====================================================================
# TelegramNotifier Tests
# =====================================================================

class TestTelegramNotifier(unittest.TestCase):

    def test_disabled_without_credentials(self):
        """No token/chat_id = notifier disabled, no crash."""
        with patch.dict("os.environ", {}, clear=True):
            n = TelegramNotifier(bot_token="", chat_id="")
        self.assertFalse(n.is_enabled)
        self.assertFalse(n.send_message("test"))

    def test_enabled_with_credentials(self):
        n = TelegramNotifier(bot_token="123:ABC", chat_id="999")
        self.assertTrue(n.is_enabled)

    @patch("notifications.telegram_notifier.requests.post")
    def test_send_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True}
        mock_post.return_value = mock_resp

        n = TelegramNotifier(bot_token="123:ABC", chat_id="999")
        result = n.send_message("Hello")
        self.assertTrue(result)

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        self.assertEqual(call_kwargs[1]["json"]["chat_id"], "999")
        self.assertEqual(call_kwargs[1]["json"]["text"], "Hello")

    @patch("notifications.telegram_notifier.requests.post")
    def test_send_api_error(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "Bad Request"
        mock_post.return_value = mock_resp

        n = TelegramNotifier(bot_token="123:ABC", chat_id="999")
        result = n.send_message("Hello")
        self.assertFalse(result)

    @patch("notifications.telegram_notifier.requests.post")
    def test_send_network_error(self, mock_post):
        import requests as req
        mock_post.side_effect = req.ConnectionError("timeout")

        n = TelegramNotifier(bot_token="123:ABC", chat_id="999")
        result = n.send_message("Hello")
        self.assertFalse(result)  # Graceful failure, no crash


# =====================================================================
# TelegramAlertManager Tests
# =====================================================================

class TestTelegramAlertManager(unittest.TestCase):

    def setUp(self):
        self.notifier = MagicMock()
        self.notifier.send_message = MagicMock(return_value=True)
        self.tam = TelegramAlertManager(notifier=self.notifier)

    def test_on_position_opened(self):
        pos = FakePosition()
        self.tam.on_position_opened(pos, 142.30, 14)
        self.notifier.send_message.assert_called_once()
        msg = self.notifier.send_message.call_args[0][0]
        self.assertIn("TRADE OPENED", msg)
        self.assertIn("NVDA", msg)

    def test_on_position_closed(self):
        pos = FakePosition()
        signal = ExitSignal(
            should_exit=True, rule="PROFIT_TARGET",
            reason="Profit target hit", current_price=163.65,
        )
        self.tam.on_position_closed(pos, signal)
        self.notifier.send_message.assert_called_once()
        msg = self.notifier.send_message.call_args[0][0]
        self.assertIn("TRADE CLOSED", msg)

    def test_on_guard_blocked(self):
        self.tam.on_guard_blocked("AAPL", "DAILY_LOSS", "Down 3%")
        self.notifier.send_message.assert_called_once()
        msg = self.notifier.send_message.call_args[0][0]
        self.assertIn("PORTFOLIO GUARD", msg)

    def test_on_daily_loss_limit(self):
        self.tam.on_daily_loss_limit(-3.1, 24000.0)
        self.notifier.send_message.assert_called_once()
        msg = self.notifier.send_message.call_args[0][0]
        self.assertIn("DAILY LOSS LIMIT", msg)

    def test_on_engine_error(self):
        self.tam.on_engine_error("monitor", "Crash!")
        self.notifier.send_message.assert_called_once()
        msg = self.notifier.send_message.call_args[0][0]
        self.assertIn("ENGINE ERROR", msg)

    def test_cycle_complete_is_silent(self):
        """Cycle complete is too noisy — should NOT send telegram."""
        self.tam.on_monitor_cycle_complete(5, 1)
        self.notifier.send_message.assert_not_called()


class TestTelegramToggle(unittest.TestCase):
    """Test that notification toggles are respected."""

    def test_disabled_alert_not_sent(self):
        notifier = MagicMock()
        config = MagicMock()
        config.get.return_value = {"trade_opened": False}

        tam = TelegramAlertManager(notifier=notifier, config_manager=config)
        tam.on_position_opened(FakePosition(), 100.0, 5)
        notifier.send_message.assert_not_called()

    def test_enabled_alert_sent(self):
        notifier = MagicMock()
        config = MagicMock()
        config.get.return_value = {"trade_opened": True}

        tam = TelegramAlertManager(notifier=notifier, config_manager=config)
        tam.on_position_opened(FakePosition(), 100.0, 5)
        notifier.send_message.assert_called_once()

    def test_no_config_sends_all(self):
        """If no config_manager, all alerts are enabled by default."""
        notifier = MagicMock()
        tam = TelegramAlertManager(notifier=notifier, config_manager=None)
        tam.on_guard_blocked("AAPL", "MAX_POS", "Too many")
        notifier.send_message.assert_called_once()


# =====================================================================
# CompositeAlertManager Tests
# =====================================================================

class TestCompositeAlertManager(unittest.TestCase):

    def test_fans_out_to_all(self):
        m1 = MagicMock()
        m2 = MagicMock()
        composite = CompositeAlertManager([m1, m2])

        pos = FakePosition()
        composite.on_position_opened(pos, 100.0, 5)
        m1.on_position_opened.assert_called_once()
        m2.on_position_opened.assert_called_once()

    def test_one_failure_doesnt_stop_others(self):
        m1 = MagicMock()
        m1.on_position_closed.side_effect = Exception("m1 broke")
        m2 = MagicMock()

        composite = CompositeAlertManager([m1, m2])
        signal = ExitSignal(should_exit=True, rule="STOP_LOSS", current_price=90.0)
        composite.on_position_closed(FakePosition(), signal)
        # m2 still called even though m1 raised
        m2.on_position_closed.assert_called_once()

    def test_guard_blocked_fans_out(self):
        m1 = MagicMock()
        m2 = MagicMock()
        composite = CompositeAlertManager([m1, m2])

        composite.on_guard_blocked("AAPL", "MAX_POS", "Too many")
        m1.on_guard_blocked.assert_called_once()
        m2.on_guard_blocked.assert_called_once()


if __name__ == "__main__":
    unittest.main()
