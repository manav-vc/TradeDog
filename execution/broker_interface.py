"""
Abstract broker interface for TradeDog.

All broker implementations (Paper, Alpaca, IBKR) implement this contract.
The rest of the system only talks to BrokerInterface — swapping brokers is
a one-line config change.
"""

from abc import ABC, abstractmethod

from database.models import Position, Order, AccountInfo


class BrokerInterface(ABC):
    """Contract that every broker must implement."""

    @abstractmethod
    def place_market_buy(self, ticker: str, qty: int) -> Order:
        """Submit a market buy order. Returns the Order with fill details."""
        ...

    @abstractmethod
    def place_market_sell(self, ticker: str, qty: int) -> Order:
        """Submit a market sell order. Returns the Order with fill details."""
        ...

    @abstractmethod
    def get_positions(self) -> list[Position]:
        """Return all currently open positions."""
        ...

    @abstractmethod
    def get_account(self) -> AccountInfo:
        """Return current account snapshot (cash, value, etc.)."""
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order. Returns True if cancelled successfully."""
        ...

    @abstractmethod
    def get_current_price(self, ticker: str) -> float:
        """Get the current market price for a ticker."""
        ...
