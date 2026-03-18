"""
Alpaca broker implementation for TradeDog.

Supports both paper and live trading via alpaca-py SDK.
Paper trading uses Alpaca's sandbox API (no real money).

Requires environment variables:
    ALPACA_API_KEY
    ALPACA_SECRET_KEY
    ALPACA_PAPER=true  (set to 'false' for live — Phase 7)
"""

import os
import logging
from datetime import datetime
from typing import Optional

from database.models import Position, Order, AccountInfo
from .broker_interface import BrokerInterface

logger = logging.getLogger(__name__)

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
    from alpaca.data.live import StockDataStream
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockLatestQuoteRequest
    ALPACA_AVAILABLE = True
except ImportError:
    ALPACA_AVAILABLE = False


class AlpacaBroker(BrokerInterface):
    """Alpaca implementation of BrokerInterface.

    Works with both paper and live accounts — determined by ALPACA_PAPER env var.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        paper: Optional[bool] = None,
    ):
        if not ALPACA_AVAILABLE:
            raise ImportError(
                "alpaca-py is not installed. Run: pip install alpaca-py"
            )

        self.api_key = api_key or os.environ.get("ALPACA_API_KEY", "")
        self.secret_key = secret_key or os.environ.get("ALPACA_SECRET_KEY", "")

        if not self.api_key or not self.secret_key:
            raise ValueError(
                "Alpaca credentials not found. Set ALPACA_API_KEY and "
                "ALPACA_SECRET_KEY in your .env file."
            )

        if paper is None:
            paper = os.environ.get("ALPACA_PAPER", "true").lower() == "true"
        self.paper = paper

        self.client = TradingClient(
            self.api_key, self.secret_key, paper=self.paper
        )
        self.data_client = StockHistoricalDataClient(
            self.api_key, self.secret_key
        )

        mode = "PAPER" if self.paper else "LIVE"
        logger.info(f"AlpacaBroker initialized in {mode} mode")

    def get_current_price(self, ticker: str) -> float:
        """Get latest quote from Alpaca data API."""
        request = StockLatestQuoteRequest(symbol_or_symbols=ticker)
        quotes = self.data_client.get_stock_latest_quote(request)
        quote = quotes[ticker]
        # Use ask price if available, otherwise bid, otherwise last trade
        price = quote.ask_price or quote.bid_price
        if price is None or price == 0:
            raise ValueError(f"Cannot get price for {ticker} from Alpaca")
        return float(price)

    def place_market_buy(self, ticker: str, qty: int) -> Order:
        """Submit a market buy order to Alpaca."""
        request = MarketOrderRequest(
            symbol=ticker,
            qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
        alpaca_order = self.client.submit_order(request)

        return Order(
            broker_order_id=str(alpaca_order.id),
            ticker=ticker,
            side="BUY",
            order_type="MARKET",
            qty=qty,
            requested_price=None,
            filled_price=float(alpaca_order.filled_avg_price) if alpaca_order.filled_avg_price else None,
            status=alpaca_order.status.value.upper(),
            broker="alpaca",
            filled_at=alpaca_order.filled_at,
        )

    def place_market_sell(self, ticker: str, qty: int) -> Order:
        """Submit a market sell order to Alpaca."""
        request = MarketOrderRequest(
            symbol=ticker,
            qty=qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        alpaca_order = self.client.submit_order(request)

        return Order(
            broker_order_id=str(alpaca_order.id),
            ticker=ticker,
            side="SELL",
            order_type="MARKET",
            qty=qty,
            requested_price=None,
            filled_price=float(alpaca_order.filled_avg_price) if alpaca_order.filled_avg_price else None,
            status=alpaca_order.status.value.upper(),
            broker="alpaca",
            filled_at=alpaca_order.filled_at,
        )

    def get_positions(self) -> list[Position]:
        """Get all open positions from Alpaca."""
        alpaca_positions = self.client.get_all_positions()
        positions = []
        for ap in alpaca_positions:
            positions.append(Position(
                ticker=ap.symbol,
                side="BUY",
                entry_price=float(ap.avg_entry_price),
                qty=int(ap.qty),
                cost_basis=float(ap.cost_basis),
                status="OPEN",
                broker="alpaca",
            ))
        return positions

    def get_account(self) -> AccountInfo:
        """Get account info from Alpaca."""
        acct = self.client.get_account()
        return AccountInfo(
            total_value=float(acct.portfolio_value),
            cash=float(acct.cash),
            invested=float(acct.portfolio_value) - float(acct.cash),
            num_positions=len(self.client.get_all_positions()),
            buying_power=float(acct.buying_power),
            broker="alpaca",
        )

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending Alpaca order."""
        try:
            self.client.cancel_order_by_id(order_id)
            return True
        except Exception as e:
            logger.warning(f"Failed to cancel order {order_id}: {e}")
            return False
