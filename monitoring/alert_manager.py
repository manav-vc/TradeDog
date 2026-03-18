"""
Alert manager for TradeDog.

Pluggable alert system. Ships with a LogAlertManager (writes to DB + stdout).
TelegramAlertManager will be added in Phase 6C.
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
