"""
Watchlist loader and liquidity filter for TradeDog.

Reads watchlist.json, flattens tickers across sectors, and filters out
any that don't meet minimum average volume and market cap thresholds.
Uses yfinance (already a project dependency) for live screening data.
"""

import json
import os
from typing import Optional

import yfinance as yf

_WATCHLIST_PATH = os.path.join(os.path.dirname(__file__), "watchlist.json")


def load_watchlist(path: Optional[str] = None) -> dict:
    """Load and return the raw watchlist JSON."""
    path = path or _WATCHLIST_PATH
    with open(path, "r") as f:
        return json.load(f)


def get_all_tickers(watchlist: Optional[dict] = None) -> list[str]:
    """Return a flat, deduplicated list of every ticker in the watchlist."""
    watchlist = watchlist or load_watchlist()
    tickers = []
    for sector_tickers in watchlist["tickers"].values():
        tickers.extend(sector_tickers)
    return sorted(set(tickers))


def get_sector_map(watchlist: Optional[dict] = None) -> dict[str, str]:
    """Return a mapping of ticker -> sector from the watchlist."""
    watchlist = watchlist or load_watchlist()
    sector_map = {}
    for sector, tickers in watchlist["tickers"].items():
        for ticker in tickers:
            sector_map[ticker] = sector
    return sector_map


def filter_by_liquidity(
    tickers: Optional[list[str]] = None,
    min_avg_volume: Optional[int] = None,
    min_market_cap_billions: Optional[float] = None,
) -> dict:
    """
    Screen tickers against minimum average daily volume and market cap.

    Returns a dict with two keys:
        "passed" : list of tickers that meet both thresholds
        "failed" : list of dicts with ticker, reason, and actual values

    Uses defaults from watchlist.json if thresholds are not provided.
    """
    watchlist = load_watchlist()
    tickers = tickers or get_all_tickers(watchlist)

    filters = watchlist.get("filters", {})
    min_avg_volume = min_avg_volume or filters.get("min_avg_volume", 1_000_000)
    min_market_cap_billions = min_market_cap_billions or filters.get(
        "min_market_cap_billions", 10
    )
    min_market_cap = min_market_cap_billions * 1e9

    passed = []
    failed = []

    for ticker in tickers:
        try:
            info = yf.Ticker(ticker).info
            avg_vol = info.get("averageVolume") or info.get("averageDailyVolume10Day")
            mkt_cap = info.get("marketCap")

            reasons = []
            if avg_vol is None or avg_vol < min_avg_volume:
                reasons.append(
                    f"avg_volume={avg_vol or 'N/A'} < {min_avg_volume}"
                )
            if mkt_cap is None or mkt_cap < min_market_cap:
                cap_display = f"${mkt_cap/1e9:.1f}B" if mkt_cap else "N/A"
                reasons.append(
                    f"market_cap={cap_display} < ${min_market_cap_billions}B"
                )

            if reasons:
                failed.append({"ticker": ticker, "reasons": reasons})
            else:
                passed.append(ticker)

        except Exception as e:
            failed.append({"ticker": ticker, "reasons": [f"lookup error: {e}"]})

    return {"passed": passed, "failed": failed}


if __name__ == "__main__":
    print("--- All watchlist tickers ---")
    all_tickers = get_all_tickers()
    print(f"{len(all_tickers)} tickers: {all_tickers}\n")

    print("--- Running liquidity filter ---")
    result = filter_by_liquidity()
    print(f"\nPassed ({len(result['passed'])}): {result['passed']}")
    if result["failed"]:
        print(f"\nFailed ({len(result['failed'])}):")
        for f in result["failed"]:
            print(f"  {f['ticker']}: {', '.join(f['reasons'])}")
