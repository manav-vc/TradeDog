"""
Phase 1 — Data layer integration tests for TradeDog.

Tests that the existing TradingAgents data layer returns clean, usable data
for a representative set of watchlist tickers.  Also verifies that the
vendor fallback mechanism in route_to_vendor works when the primary vendor
raises an error.

These tests hit live yfinance APIs — run sparingly, not on every commit.
"""

import math
import pytest
from unittest.mock import patch

from watchlist import get_all_tickers

# ── Data layer imports (read-only, no modifications to tradingagents/) ──
from tradingagents.dataflows.interface import route_to_vendor, VENDOR_METHODS
from tradingagents.dataflows.y_finance import (
    get_YFin_data_online,
    get_stock_stats_indicators_window,
    get_fundamentals as yf_fundamentals,
)

# ---------------------------------------------------------------------------
# Config: representative subset of watchlist (10 tickers, mixed sectors)
# ---------------------------------------------------------------------------
SAMPLE_TICKERS = ["AAPL", "MSFT", "NVDA", "AMZN", "JPM", "UNH", "XOM", "CAT", "NEE", "KO"]
TEST_START = "2024-10-01"
TEST_END = "2024-11-01"
TEST_CURR_DATE = "2024-11-01"


# ===================================================================
# Task 1.3 — Verify clean data across 10 tickers (no empty / NaN)
# ===================================================================


class TestOHLCVData:
    """get_stock_data (OHLCV) returns parseable, non-empty CSV for each ticker."""

    @pytest.mark.parametrize("ticker", SAMPLE_TICKERS)
    def test_ohlcv_returns_data(self, ticker):
        result = get_YFin_data_online(ticker, TEST_START, TEST_END)
        assert isinstance(result, str), f"{ticker}: expected string result"
        # Should contain CSV header row with OHLCV columns
        assert "Open" in result, f"{ticker}: missing 'Open' column"
        assert "Close" in result, f"{ticker}: missing 'Close' column"
        assert "Volume" in result, f"{ticker}: missing 'Volume' column"

    @pytest.mark.parametrize("ticker", SAMPLE_TICKERS)
    def test_ohlcv_has_rows(self, ticker):
        result = get_YFin_data_online(ticker, TEST_START, TEST_END)
        # Filter out comment lines (start with #) and blanks
        data_lines = [
            line for line in result.strip().split("\n")
            if line.strip() and not line.startswith("#")
        ]
        # At least header + 1 data row
        assert len(data_lines) >= 2, f"{ticker}: no data rows returned"

    @pytest.mark.parametrize("ticker", SAMPLE_TICKERS)
    def test_ohlcv_no_nan_prices(self, ticker):
        result = get_YFin_data_online(ticker, TEST_START, TEST_END)
        lines = [
            line for line in result.strip().split("\n")
            if line.strip() and not line.startswith("#")
        ]
        header = lines[0].split(",")
        close_idx = header.index("Close")
        for line in lines[1:]:
            fields = line.split(",")
            close_val = fields[close_idx]
            assert close_val.strip() != "", f"{ticker}: empty Close value"
            price = float(close_val)
            assert not math.isnan(price), f"{ticker}: NaN Close price"
            assert price > 0, f"{ticker}: non-positive Close price {price}"


class TestTechnicalIndicators:
    """Technical indicator retrieval returns non-empty, parseable results."""

    @pytest.mark.parametrize("ticker", SAMPLE_TICKERS[:5])
    def test_macd_returns_data(self, ticker):
        result = get_stock_stats_indicators_window(ticker, "macd", TEST_CURR_DATE, 30)
        assert isinstance(result, str), f"{ticker}: expected string"
        assert len(result) > 50, f"{ticker}: result too short — likely empty"

    @pytest.mark.parametrize("ticker", SAMPLE_TICKERS[:5])
    def test_rsi_returns_data(self, ticker):
        result = get_stock_stats_indicators_window(ticker, "rsi", TEST_CURR_DATE, 30)
        assert isinstance(result, str), f"{ticker}: expected string"
        assert len(result) > 50, f"{ticker}: result too short — likely empty"


class TestFundamentals:
    """Fundamental data retrieval returns non-empty results."""

    @pytest.mark.parametrize("ticker", SAMPLE_TICKERS[:5])
    def test_fundamentals_returns_data(self, ticker):
        result = yf_fundamentals(ticker)
        assert isinstance(result, str), f"{ticker}: expected string"
        assert len(result) > 20, f"{ticker}: result too short — likely empty"


class TestRouteToVendor:
    """route_to_vendor correctly dispatches and returns data."""

    def test_route_stock_data(self):
        result = route_to_vendor("get_stock_data", "AAPL", TEST_START, TEST_END)
        assert "Open" in result
        assert "Close" in result


# ===================================================================
# Task 1.4 — Verify vendor fallback when primary fails
# ===================================================================


class TestVendorFallback:
    """When the primary vendor raises an error, route_to_vendor falls through
    to the next available vendor."""

    def test_fallback_on_primary_failure(self):
        """Patch yfinance to raise rate-limit error, verify alpha_vantage is attempted."""
        call_log = []

        def failing_yf(*args, **kwargs):
            call_log.append("yfinance")
            from tradingagents.dataflows.alpha_vantage_common import AlphaVantageRateLimitError
            raise AlphaVantageRateLimitError("simulated failure")

        def mock_av(*args, **kwargs):
            call_log.append("alpha_vantage")
            return "alpha_vantage data for AAPL"

        with patch.dict(
            VENDOR_METHODS["get_stock_data"],
            {"yfinance": failing_yf, "alpha_vantage": mock_av},
        ):
            result = route_to_vendor("get_stock_data", "AAPL", TEST_START, TEST_END)
            assert "yfinance" in call_log, "yfinance should have been tried first"
            assert "alpha_vantage" in call_log, "alpha_vantage should have been tried as fallback"
            assert result == "alpha_vantage data for AAPL"

    def test_fallback_chain_order(self):
        """Verify the fallback chain tries vendors in config order."""
        call_log = []

        def logging_yf(*args, **kwargs):
            call_log.append("yfinance")
            from tradingagents.dataflows.alpha_vantage_common import AlphaVantageRateLimitError
            raise AlphaVantageRateLimitError("simulated")

        def logging_av(*args, **kwargs):
            call_log.append("alpha_vantage")
            return "fallback data"

        with patch.dict(
            VENDOR_METHODS["get_stock_data"],
            {"yfinance": logging_yf, "alpha_vantage": logging_av},
        ):
            result = route_to_vendor("get_stock_data", "AAPL", TEST_START, TEST_END)
            assert call_log == ["yfinance", "alpha_vantage"]
            assert result == "fallback data"

    def test_no_fallback_on_non_rate_limit_error(self):
        """Non-rate-limit errors should NOT trigger fallback — they propagate up."""
        def broken_yf(*args, **kwargs):
            raise ValueError("bad data")

        with patch.dict(VENDOR_METHODS["get_stock_data"], {"yfinance": broken_yf}):
            with pytest.raises(ValueError, match="bad data"):
                route_to_vendor("get_stock_data", "AAPL", TEST_START, TEST_END)


# ===================================================================
# Watchlist sanity check
# ===================================================================

class TestWatchlist:
    """Verify watchlist module loads and contains expected tickers."""

    def test_all_tickers_loaded(self):
        tickers = get_all_tickers()
        assert len(tickers) >= 35, f"Expected ~36 tickers, got {len(tickers)}"

    def test_sample_tickers_present(self):
        tickers = get_all_tickers()
        for t in ["AAPL", "NVDA", "JPM", "XOM"]:
            assert t in tickers, f"{t} missing from watchlist"

    def test_no_duplicates(self):
        tickers = get_all_tickers()
        assert len(tickers) == len(set(tickers)), "Duplicate tickers in watchlist"
