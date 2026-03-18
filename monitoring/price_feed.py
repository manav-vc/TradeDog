"""
Price feed for TradeDog position monitoring.

Fetches near-real-time quotes from yfinance with a short TTL cache
to avoid hammering the API when checking multiple positions.
"""

import time
import logging
from typing import Optional

import yfinance as yf

logger = logging.getLogger(__name__)


class PriceFeed:
    """Cached price fetcher using yfinance."""

    def __init__(self, cache_ttl_seconds: int = 30):
        """
        Args:
            cache_ttl_seconds: How long to cache a price before re-fetching.
                               Default 30s — good enough for 5-min monitor intervals.
        """
        self.cache_ttl = cache_ttl_seconds
        self._cache: dict[str, tuple[float, float]] = {}  # ticker -> (price, timestamp)

    def get_price(self, ticker: str) -> float:
        """
        Get the current price for a ticker.

        Returns cached price if within TTL, otherwise fetches fresh.
        Raises ValueError if price cannot be obtained.
        """
        now = time.time()
        cached = self._cache.get(ticker)
        if cached and (now - cached[1]) < self.cache_ttl:
            return cached[0]

        price = self._fetch_price(ticker)
        self._cache[ticker] = (price, now)
        return price

    def get_prices_batch(self, tickers: list[str]) -> dict[str, Optional[float]]:
        """
        Fetch prices for multiple tickers at once.

        Returns dict of ticker -> price (None if fetch failed).
        """
        results = {}
        for ticker in tickers:
            try:
                results[ticker] = self.get_price(ticker)
            except Exception as e:
                logger.warning(f"Failed to fetch price for {ticker}: {e}")
                results[ticker] = None
        return results

    def invalidate(self, ticker: Optional[str] = None):
        """Clear cache for a specific ticker or all tickers."""
        if ticker:
            self._cache.pop(ticker, None)
        else:
            self._cache.clear()

    def _fetch_price(self, ticker: str) -> float:
        """Fetch fresh price from yfinance."""
        try:
            info = yf.Ticker(ticker).info
            price = info.get("currentPrice") or info.get("regularMarketPrice")
            if price and float(price) > 0:
                return float(price)
        except Exception:
            pass

        # Fallback: last close from history
        try:
            hist = yf.Ticker(ticker).history(period="1d")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception:
            pass

        raise ValueError(f"Cannot fetch price for {ticker}")
