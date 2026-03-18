"""
Alert manager for TradeDog.

Pluggable alert system with multiple backends:
  - LogAlertManager  — writes to DB + Python logger (stdout)
  - TelegramAlertManager — sends to Telegram via Bot API (Phase 6C)
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional

from database.db import Database
from database.models import Position
from monitoring.exit_rules import ExitSignal

logger = logging.getLogger(__name__)


class AlertManager(ABC):
    """Abstract alert interface — all alert backends implement this."""

    @abstractmethod
    def on_position_opened(self, position: Position, price: float, qty: int) -> None:
        """Called when a new position is opened."""
        ...

    @abstractmethod
    def on_position_closed(
        self, position: Position, exit_signal: ExitSignal
    ) -> None:
        """Called when a position is closed by the monitor."""
        ...

    @abstractmethod
    def on_monitor_error(self, ticker: str, error: str) -> None:
        """Called when the monitor encounters an error."""
        ...

    @abstractmethod
    def on_monitor_cycle_complete(
        self, positions_checked: int, exits_triggered: int
    ) -> None:
        """Called at the end of each monitor cycle."""
        ...


class LogAlertManager(AlertManager):
    """Alert manager that logs to database + Python logger (stdout)."""

    def __init__(self, db: Optional[Database] = None):
        self.db = db

    def on_position_opened(self, position: Position, price: float, qty: int) -> None:
        msg = (
            f"POSITION OPENED: {position.ticker} x{qty} @ ${price:.2f} "
            f"= ${price * qty:,.2f}"
        )
        logger.info(msg)
        if self.db:
            self.db.log("INFO", "monitor", msg)

    def on_position_closed(
        self, position: Position, exit_signal: ExitSignal
    ) -> None:
        pnl = 0.0
        pnl_pct = 0.0
        if exit_signal.current_price and position.entry_price:
            pnl = (exit_signal.current_price - position.entry_price) * position.qty
            pnl_pct = (exit_signal.current_price - position.entry_price) / position.entry_price * 100

        msg = (
            f"POSITION CLOSED: {position.ticker} | "
            f"Entry ${position.entry_price:.2f} -> Exit ${exit_signal.current_price:.2f} | "
            f"P&L: ${pnl:+,.2f} ({pnl_pct:+.1f}%) | "
            f"Reason: {exit_signal.rule} — {exit_signal.reason}"
        )
        logger.info(msg)
        if self.db:
            self.db.log("INFO", "monitor", msg)

    def on_monitor_error(self, ticker: str, error: str) -> None:
        msg = f"MONITOR ERROR for {ticker}: {error}"
        logger.error(msg)
        if self.db:
            self.db.log("ERROR", "monitor", msg)

    def on_monitor_cycle_complete(
        self, positions_checked: int, exits_triggered: int
    ) -> None:
        if exits_triggered > 0:
            msg = (
                f"Monitor cycle: checked {positions_checked} positions, "
                f"{exits_triggered} exit(s) triggered"
            )
            logger.info(msg)
            if self.db:
                self.db.log("INFO", "monitor", msg)

    def on_guard_blocked(self, ticker: str, rule: str, reason: str) -> None:
        msg = f"GUARD BLOCKED: {ticker} — {rule}: {reason}"
        logger.info(msg)
        if self.db:
            self.db.log("WARNING", "guard", msg)

    def on_daily_loss_limit(self, daily_pnl_pct: float, portfolio_value: float) -> None:
        msg = (
            f"DAILY LOSS LIMIT: portfolio down {daily_pnl_pct:.1f}% "
            f"(${portfolio_value:,.2f}). Buys halted."
        )
        logger.warning(msg)
        if self.db:
            self.db.log("WARNING", "guard", msg)

    def on_engine_error(self, component: str, error: str) -> None:
        msg = f"ENGINE ERROR in {component}: {error}"
        logger.error(msg)
        if self.db:
            self.db.log("ERROR", "engine", msg)


class TelegramAlertManager(AlertManager):
    """Alert manager that sends notifications via Telegram."""

    def __init__(
        self,
        notifier=None,
        config_manager=None,
        db: Optional[Database] = None,
    ):
        """
        Args:
            notifier: TelegramNotifier instance (or any object with send_message())
            config_manager: ConfigManager for reading notification toggles
            db: Database for logging (optional)
        """
        # Lazy import to avoid circular deps at module load time
        if notifier is None:
            from notifications.telegram_notifier import TelegramNotifier
            notifier = TelegramNotifier()
        self.notifier = notifier
        self.config_manager = config_manager
        self.db = db

    def _is_alert_enabled(self, alert_type: str) -> bool:
        """Check if a specific alert type is enabled in config."""
        if self.config_manager is None:
            return True  # All alerts on if no config
        toggles = self.config_manager.get("notification_toggles", {})
        return toggles.get(alert_type, True)

    def on_position_opened(self, position: Position, price: float, qty: int) -> None:
        if not self._is_alert_enabled("trade_opened"):
            return
        from notifications.templates import trade_opened_message
        msg = trade_opened_message(
            ticker=position.ticker,
            price=price,
            qty=qty,
            total_cost=price * qty,
        )
        self.notifier.send_message(msg)

    def on_position_closed(
        self, position: Position, exit_signal: ExitSignal
    ) -> None:
        if not self._is_alert_enabled("trade_closed"):
            return
        from notifications.templates import trade_closed_message
        pnl = (exit_signal.current_price - position.entry_price) * position.qty
        pnl_pct = (
            (exit_signal.current_price - position.entry_price)
            / position.entry_price * 100
            if position.entry_price
            else 0.0
        )
        msg = trade_closed_message(
            ticker=position.ticker,
            entry_price=position.entry_price,
            exit_price=exit_signal.current_price,
            qty=position.qty,
            pnl=pnl,
            pnl_pct=pnl_pct,
            exit_reason=f"{exit_signal.rule} — {exit_signal.reason}",
        )
        self.notifier.send_message(msg)

    def on_monitor_error(self, ticker: str, error: str) -> None:
        if not self._is_alert_enabled("engine_error"):
            return
        from notifications.templates import engine_error_message
        msg = engine_error_message(component=f"monitor:{ticker}", error=error)
        self.notifier.send_message(msg)

    def on_monitor_cycle_complete(
        self, positions_checked: int, exits_triggered: int
    ) -> None:
        # Cycle-complete is too noisy for Telegram — skip
        pass

    def on_guard_blocked(self, ticker: str, rule: str, reason: str) -> None:
        if not self._is_alert_enabled("guard_blocked"):
            return
        from notifications.templates import guard_blocked_message
        msg = guard_blocked_message(ticker=ticker, rule=rule, reason=reason)
        self.notifier.send_message(msg)

    def on_daily_loss_limit(self, daily_pnl_pct: float, portfolio_value: float) -> None:
        if not self._is_alert_enabled("daily_loss"):
            return
        from notifications.templates import daily_loss_message
        msg = daily_loss_message(
            daily_pnl_pct=daily_pnl_pct, portfolio_value=portfolio_value
        )
        self.notifier.send_message(msg)

    def on_engine_error(self, component: str, error: str) -> None:
        if not self._is_alert_enabled("engine_error"):
            return
        from notifications.templates import engine_error_message
        msg = engine_error_message(component=component, error=error)
        self.notifier.send_message(msg)


class CompositeAlertManager(AlertManager):
    """Fans out alerts to multiple backends (e.g., Log + Telegram)."""

    def __init__(self, managers: list[AlertManager]):
        self.managers = managers

    def on_position_opened(self, position: Position, price: float, qty: int) -> None:
        for m in self.managers:
            try:
                m.on_position_opened(position, price, qty)
            except Exception as e:
                logger.warning(f"Alert backend failed: {e}")

    def on_position_closed(
        self, position: Position, exit_signal: ExitSignal
    ) -> None:
        for m in self.managers:
            try:
                m.on_position_closed(position, exit_signal)
            except Exception as e:
                logger.warning(f"Alert backend failed: {e}")

    def on_monitor_error(self, ticker: str, error: str) -> None:
        for m in self.managers:
            try:
                m.on_monitor_error(ticker, error)
            except Exception as e:
                logger.warning(f"Alert backend failed: {e}")

    def on_monitor_cycle_complete(
        self, positions_checked: int, exits_triggered: int
    ) -> None:
        for m in self.managers:
            try:
                m.on_monitor_cycle_complete(positions_checked, exits_triggered)
            except Exception as e:
                logger.warning(f"Alert backend failed: {e}")

    def on_guard_blocked(self, ticker: str, rule: str, reason: str) -> None:
        for m in self.managers:
            if hasattr(m, "on_guard_blocked"):
                try:
                    m.on_guard_blocked(ticker, rule, reason)
                except Exception as e:
                    logger.warning(f"Alert backend failed: {e}")

    def on_daily_loss_limit(self, daily_pnl_pct: float, portfolio_value: float) -> None:
        for m in self.managers:
            if hasattr(m, "on_daily_loss_limit"):
                try:
                    m.on_daily_loss_limit(daily_pnl_pct, portfolio_value)
                except Exception as e:
                    logger.warning(f"Alert backend failed: {e}")

    def on_engine_error(self, component: str, error: str) -> None:
        for m in self.managers:
            if hasattr(m, "on_engine_error"):
                try:
                    m.on_engine_error(component, error)
                except Exception as e:
                    logger.warning(f"Alert backend failed: {e}")
