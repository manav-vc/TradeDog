"""
Position sizing logic for TradeDog.

Determines how many shares to buy for a given ticker based on
account value and risk parameters.

Conviction-based scaling is stubbed for Phase 3.
"""


def calculate_position_size(
    account_value: float,
    price: float,
    max_position_pct: float = 0.05,
    conviction_score: float = 1.0,
) -> int:
    """
    Calculate the number of shares to buy.

    Args:
        account_value: Total portfolio value (cash + invested)
        price: Current share price
        max_position_pct: Max fraction of portfolio for one position (default 5%)
        conviction_score: 0.0–1.0 scaling factor (Phase 3, defaults to 1.0 = full size)

    Returns:
        Number of whole shares to buy (minimum 1 if any allocation, 0 if price too high)
    """
    if price <= 0 or account_value <= 0:
        return 0

    # Cap conviction to [0, 1]
    conviction_score = max(0.0, min(1.0, conviction_score))

    max_dollars = account_value * max_position_pct
    dollars_to_invest = max_dollars * conviction_score
    shares = int(dollars_to_invest / price)

    return max(1, shares) if dollars_to_invest >= price else 0
