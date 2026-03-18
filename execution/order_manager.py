"""
Order Manager for TradeDog.

Sits between TradingAgentsGraph.propagate() output and the broker.
Takes the agent decision, logs it as a signal, and routes BUY decisions
to the broker for execution.

This module does NOT modify tradingagents/ — it wraps the framework's
output and builds on top of it.
"""

import json
import logging
from typing import Optional

from database.db import Database
from database.models import Order
from execution.broker_interface import BrokerInterface
from portfolio.position_sizer import calculate_position_size

logger = logging.getLogger(__name__)


class OrderManager:
    """Translates agent decisions into broker orders."""

    def __init__(
        self,
        broker: BrokerInterface,
        db: Optional[Database] = None,
        dry_run: bool = False,
        max_position_pct: float = 0.05,
    ):
        self.broker = broker
        self.db = db or Database()
        self.dry_run = dry_run
        self.max_position_pct = max_position_pct

    def process_decision(
        self,
        ticker: str,
        trade_date: str,
        decision: str,
        full_state: Optional[dict] = None,
    ) -> Optional[Order]:
        """
        Process a single agent decision from propagate().

        Args:
            ticker: Stock ticker (e.g. "AAPL")
            trade_date: Date used for the analysis (e.g. "2024-11-01")
            decision: Parsed decision string from SignalProcessor ("BUY", "SELL", "HOLD")
            full_state: Full agent state dict (optional, for logging)

        Returns:
            Order if a trade was executed, None otherwise.
        """
        parsed = self._parse_decision(decision)
        state_json = None
        if full_state:
            try:
                # Serialize what we can, skip non-serializable fields
                serializable = {
                    k: v for k, v in full_state.items()
                    if isinstance(v, (str, int, float, bool, list, dict, type(None)))
                }
                state_json = json.dumps(serializable, default=str)
            except (TypeError, ValueError):
                state_json = None

        if parsed == "BUY":
            return self._handle_buy(ticker, trade_date, decision, parsed, state_json)
        elif parsed == "SELL":
            return self._handle_sell(ticker, trade_date, decision, parsed, state_json)
        else:
            # HOLD — log and skip
            self.db.insert_signal(
                ticker=ticker,
                trade_date=trade_date,
                agent_decision=decision,
                parsed_action=parsed,
                action_taken="SKIPPED",
                skip_reason="HOLD signal — no action",
                full_state_json=state_json,
            )
            logger.info(f"{ticker}: HOLD — no action taken")
            return None

    def _handle_buy(
        self, ticker: str, trade_date: str, decision: str,
        parsed: str, state_json: Optional[str]
    ) -> Optional[Order]:
        """Execute a BUY decision."""
        # Check if we already have an open position
        existing = [p for p in self.broker.get_positions() if p.ticker == ticker]
        if existing:
            self.db.insert_signal(
                ticker=ticker, trade_date=trade_date,
                agent_decision=decision, parsed_action=parsed,
                action_taken="SKIPPED",
                skip_reason=f"Already holding {ticker} (position #{existing[0].id})",
                full_state_json=state_json,
            )
            logger.info(f"{ticker}: BUY skipped — already holding")
            return None

        # Calculate position size
        account = self.broker.get_account()
        price = self.broker.get_current_price(ticker)
        qty = calculate_position_size(
            account_value=account.total_value,
            price=price,
            max_position_pct=self.max_position_pct,
        )

        if qty <= 0:
            self.db.insert_signal(
                ticker=ticker, trade_date=trade_date,
                agent_decision=decision, parsed_action=parsed,
                action_taken="SKIPPED",
                skip_reason="Position size calculated as 0 shares",
                full_state_json=state_json,
            )
            return None

        if self.dry_run:
            self.db.insert_signal(
                ticker=ticker, trade_date=trade_date,
                agent_decision=decision, parsed_action=parsed,
                action_taken="DRY_RUN",
                skip_reason=f"Would buy {qty} shares @ ~${price:.2f}",
                full_state_json=state_json,
            )
            logger.info(f"{ticker}: DRY RUN — would buy {qty} shares @ ${price:.2f}")
            return None

        # Execute
        order = self.broker.place_market_buy(ticker, qty)

        action = "BOUGHT" if order.status == "FILLED" else "REJECTED"
        skip_reason = None if action == "BOUGHT" else f"Order {order.status}"
        self.db.insert_signal(
            ticker=ticker, trade_date=trade_date,
            agent_decision=decision, parsed_action=parsed,
            action_taken=action, skip_reason=skip_reason,
            full_state_json=state_json,
        )
        logger.info(f"{ticker}: {action} — {qty} shares @ ${order.filled_price or price:.2f}")
        return order

    def _handle_sell(
        self, ticker: str, trade_date: str, decision: str,
        parsed: str, state_json: Optional[str]
    ) -> Optional[Order]:
        """Execute a SELL decision."""
        existing = [p for p in self.broker.get_positions() if p.ticker == ticker]
        if not existing:
            self.db.insert_signal(
                ticker=ticker, trade_date=trade_date,
                agent_decision=decision, parsed_action=parsed,
                action_taken="SKIPPED",
                skip_reason=f"SELL signal but no open position for {ticker}",
                full_state_json=state_json,
            )
            logger.info(f"{ticker}: SELL skipped — no position to sell")
            return None

        if self.dry_run:
            self.db.insert_signal(
                ticker=ticker, trade_date=trade_date,
                agent_decision=decision, parsed_action=parsed,
                action_taken="DRY_RUN",
                skip_reason=f"Would sell {existing[0].qty} shares of {ticker}",
                full_state_json=state_json,
            )
            return None

        order = self.broker.place_market_sell(ticker, existing[0].qty)

        action = "SOLD" if order.status == "FILLED" else "REJECTED"
        self.db.insert_signal(
            ticker=ticker, trade_date=trade_date,
            agent_decision=decision, parsed_action=parsed,
            action_taken=action,
            full_state_json=state_json,
        )
        return order

    def _parse_decision(self, decision: str) -> str:
        """Extract BUY/SELL/HOLD from the decision text."""
        text = decision.upper().strip()
        if "BUY" in text:
            return "BUY"
        elif "SELL" in text:
            return "SELL"
        else:
            return "HOLD"
