"""
Paper trading broker for TradeDog.

Simulates order execution using live yfinance prices and stores
everything in SQLite.  No real money, no API keys needed.
"""

from datetime import datetime, timezone
from typing import Optional

import yfinance as yf

from database.db import Database
from database.models import Position, Order, AccountInfo
from .broker_interface import BrokerInterface


class PaperBroker(BrokerInterface):
    """SQLite-backed paper trading broker using live yfinance prices."""

    def __init__(
        self,
        db: Optional[Database] = None,
        db_path: Optional[str] = None,
        starting_cash: float = 100_000.0,
    ):
        self.db = db or Database(db_path)
        self.starting_cash = starting_cash
        self.db.log("INFO", "paper_broker", f"PaperBroker initialized with ${starting_cash:,.2f}")

    def get_current_price(self, ticker: str) -> float:
        """Fetch the latest price from yfinance."""
        info = yf.Ticker(ticker).info
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        if price is None:
            # Fallback: use last close from history
            hist = yf.Ticker(ticker).history(period="1d")
            if hist.empty:
                raise ValueError(f"Cannot get price for {ticker}")
            price = float(hist["Close"].iloc[-1])
        return float(price)

    def place_market_buy(self, ticker: str, qty: int) -> Order:
        """Simulate a market buy at the current price."""
        price = self.get_current_price(ticker)
        now = datetime.now(timezone.utc)

        # Check we have enough cash
        account = self.get_account()
        cost = price * qty
        if cost > account.cash:
            order = Order(
                ticker=ticker, side="BUY", order_type="MARKET", qty=qty,
                requested_price=price, status="REJECTED", broker="paper",
            )
            order.id = self.db.insert_order(order)
            self.db.log("WARNING", "paper_broker",
                        f"BUY {ticker} x{qty} REJECTED — insufficient cash "
                        f"(need ${cost:,.2f}, have ${account.cash:,.2f})")
            return order

        # Create position
        position = Position(
            ticker=ticker, side="BUY", entry_price=price, entry_time=now,
            qty=qty, cost_basis=price * qty, highest_price=price,
            status="OPEN", broker="paper",
        )
        position.id = self.db.insert_position(position)

        # Create and fill order
        order = Order(
            ticker=ticker, side="BUY", order_type="MARKET", qty=qty,
            requested_price=price, filled_price=price, status="FILLED",
            position_id=position.id, broker="paper", filled_at=now,
        )
        order.id = self.db.insert_order(order)

        self.db.log("INFO", "paper_broker",
                    f"BUY {ticker} x{qty} @ ${price:.2f} = ${cost:,.2f} (position #{position.id})")
        return order

    def place_market_sell(self, ticker: str, qty: int) -> Order:
        """Simulate a market sell at the current price.

        Finds the oldest open position for the ticker and closes it.
        """
        price = self.get_current_price(ticker)
        now = datetime.now(timezone.utc)

        # Find open position for this ticker
        open_positions = [
            p for p in self.db.get_open_positions() if p.ticker == ticker
        ]
        if not open_positions:
            order = Order(
                ticker=ticker, side="SELL", order_type="MARKET", qty=qty,
                requested_price=price, status="REJECTED", broker="paper",
            )
            order.id = self.db.insert_order(order)
            self.db.log("WARNING", "paper_broker",
                        f"SELL {ticker} REJECTED — no open position")
            return order

        position = open_positions[0]  # FIFO: sell oldest first

        # Close position
        self.db.close_position(position.id, price, "MANUAL")

        # Create and fill order
        order = Order(
            ticker=ticker, side="SELL", order_type="MARKET", qty=position.qty,
            requested_price=price, filled_price=price, status="FILLED",
            position_id=position.id, broker="paper", filled_at=now,
        )
        order.id = self.db.insert_order(order)

        pnl = (price - position.entry_price) * position.qty
        self.db.log("INFO", "paper_broker",
                    f"SELL {ticker} x{position.qty} @ ${price:.2f} "
                    f"(P&L: ${pnl:+,.2f}, position #{position.id})")
        return order

    def get_positions(self) -> list[Position]:
        return self.db.get_open_positions()

    def get_account(self) -> AccountInfo:
        return self.db.get_account_summary(self.starting_cash)

    def cancel_order(self, order_id: str) -> bool:
        """Paper broker fills instantly — nothing to cancel."""
        self.db.log("INFO", "paper_broker", f"cancel_order({order_id}) — no-op for paper broker")
        return False
