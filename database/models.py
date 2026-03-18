"""
Data models for TradeDog's database layer.
Pure dataclasses — no ORM, no framework dependency.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Position:
    """A single stock position (open or closed)."""
    id: Optional[int] = None
    ticker: str = ""
    side: str = "BUY"
    entry_price: float = 0.0
    entry_time: Optional[datetime] = None
    qty: int = 0
    cost_basis: float = 0.0
    highest_price: Optional[float] = None
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_reason: Optional[str] = None
    status: str = "OPEN"
    broker: str = "paper"

    @property
    def market_value(self) -> float:
        """Current market value (uses exit_price if closed, else entry_price)."""
        price = self.exit_price if self.status == "CLOSED" else self.entry_price
        return (price or 0.0) * self.qty

    @property
    def pnl(self) -> Optional[float]:
        """Realized P&L (only meaningful for closed positions)."""
        if self.exit_price is None:
            return None
        return (self.exit_price - self.entry_price) * self.qty

    @property
    def pnl_pct(self) -> Optional[float]:
        """Realized P&L percentage."""
        if self.exit_price is None or self.entry_price == 0:
            return None
        return (self.exit_price - self.entry_price) / self.entry_price * 100


@dataclass
class Order:
    """A single order submitted to a broker."""
    id: Optional[int] = None
    broker_order_id: Optional[str] = None
    ticker: str = ""
    side: str = "BUY"
    order_type: str = "MARKET"
    qty: int = 0
    requested_price: Optional[float] = None
    filled_price: Optional[float] = None
    status: str = "PENDING"
    position_id: Optional[int] = None
    broker: str = "paper"
    created_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None


@dataclass
class AccountInfo:
    """Broker account snapshot."""
    total_value: float = 0.0
    cash: float = 0.0
    invested: float = 0.0
    num_positions: int = 0
    buying_power: float = 0.0
    broker: str = "paper"
