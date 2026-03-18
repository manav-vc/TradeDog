"""
Position monitor for TradeDog.

Periodically checks all open positions against exit rules and
auto-sells when a rule triggers. Supports both async and sync modes.

Does NOT modify tradingagents/ — uses the existing data layer
for reversal detection and the broker interface for sells.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from database.db import Database
from execution.broker_interface import BrokerInterface
from monitoring.price_feed import PriceFeed
from monitoring.exit_rules import check_all_exit_rules, ExitSignal
from monitoring.alert_manager import AlertManager, LogAlertManager

logger = logging.getLogger(__name__)


class PositionMonitor:
    """Monitors open positions and auto-exits based on rules."""

    def __init__(
        self,
        broker: BrokerInterface,
        db: Database,
        price_feed: Optional[PriceFeed] = None,
        alert_manager: Optional[AlertManager] = None,
        # Exit rule thresholds
        profit_target_pct: float = 0.15,
        trailing_stop_pct: float = 0.07,
        stop_loss_pct: float = 0.08,
        max_hold_days: int = 30,
        check_reversals: bool = False,
        # Monitor config
        interval_seconds: int = 300,
    ):
        self.broker = broker
        self.db = db
        self.price_feed = price_feed or PriceFeed()
        self.alert_manager = alert_manager or LogAlertManager(db)

        self.profit_target_pct = profit_target_pct
        self.trailing_stop_pct = trailing_stop_pct
        self.stop_loss_pct = stop_loss_pct
        self.max_hold_days = max_hold_days
        self.check_reversals = check_reversals
        self.interval_seconds = interval_seconds

        self._running = False

    def check_once(self) -> list[ExitSignal]:
        """
        Run one monitoring cycle synchronously.

        Checks all open positions, triggers exits where needed.
        Returns list of ExitSignals that triggered sells.
        """
        positions = self.db.get_open_positions()
        triggered = []
        curr_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        for position in positions:
            try:
                price = self.price_feed.get_price(position.ticker)

                signal = check_all_exit_rules(
                    position=position,
                    current_price=price,
                    update_highest_callback=self.db.update_highest_price,
                    profit_target_pct=self.profit_target_pct,
                    trailing_stop_pct=self.trailing_stop_pct,
                    stop_loss_pct=self.stop_loss_pct,
                    max_hold_days=self.max_hold_days,
                    check_reversal_flag=self.check_reversals,
                    curr_date=curr_date,
                )

                if signal.should_exit:
                    self._execute_exit(position, signal)
                    triggered.append(signal)
                else:
                    # Update highest price even if no exit
                    if price > (position.highest_price or 0):
                        self.db.update_highest_price(position.id, price)

            except Exception as e:
                self.alert_manager.on_monitor_error(
                    position.ticker, str(e)
                )

        self.alert_manager.on_monitor_cycle_complete(
            positions_checked=len(positions),
            exits_triggered=len(triggered),
        )

        return triggered

    async def run_async(self, max_cycles: Optional[int] = None):
        """
        Run the monitor loop asynchronously.

        Args:
            max_cycles: Stop after N cycles (None = run forever).
                        Useful for testing.
        """
        self._running = True
        cycles = 0
        self.db.log("INFO", "monitor", "Position monitor started")

        try:
            while self._running:
                self.check_once()
                cycles += 1

                if max_cycles and cycles >= max_cycles:
                    break

                await asyncio.sleep(self.interval_seconds)

        except asyncio.CancelledError:
            logger.info("Monitor loop cancelled")
        finally:
            self._running = False
            self.db.log("INFO", "monitor", "Position monitor stopped")

    def stop(self):
        """Signal the monitor to stop after the current cycle."""
        self._running = False

    def _execute_exit(self, position, signal: ExitSignal):
        """Sell the position and log the exit."""
        try:
            # Close position in DB with exit reason
            self.db.close_position(
                position.id, signal.current_price, signal.rule
            )

            # Place sell order through broker
            order = self.broker.place_market_sell(
                position.ticker, position.qty
            )

            # Alert
            self.alert_manager.on_position_closed(position, signal)

            # Log signal
            self.db.insert_signal(
                ticker=position.ticker,
                trade_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                agent_decision=f"AUTO_EXIT: {signal.rule}",
                parsed_action="SELL",
                action_taken="SOLD",
                skip_reason=signal.reason,
            )

            logger.info(
                f"EXIT {position.ticker}: {signal.rule} — "
                f"sold {position.qty} shares @ ${signal.current_price:.2f}"
            )

        except Exception as e:
            self.alert_manager.on_monitor_error(
                position.ticker, f"Exit execution failed: {e}"
            )
