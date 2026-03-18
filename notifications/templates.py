"""
Telegram message templates for TradeDog alerts.

Each function returns a plain-text message string ready to send.
Uses emoji for quick visual parsing on mobile.

No tradingagents/ modifications.
"""


def trade_opened_message(
    ticker: str,
    price: float,
    qty: int,
    total_cost: float,
    conviction_score: float = 0.0,
    reason: str = "",
) -> str:
    lines = [
        "\U0001f7e2 TRADE OPENED",
        f"{ticker} @ ${price:,.2f} | {qty} shares | ${total_cost:,.2f}",
        f"Conviction: {conviction_score:.0f}/100",
    ]
    if reason:
        lines.append(f"Reason: {reason}")
    return "\n".join(lines)


def trade_closed_message(
    ticker: str,
    entry_price: float,
    exit_price: float,
    qty: int,
    pnl: float,
    pnl_pct: float,
    exit_reason: str = "",
) -> str:
    emoji = "\U0001f534" if pnl < 0 else "\U0001f7e2"
    pnl_sign = "+" if pnl >= 0 else "-"
    pnl_pct_sign = "+" if pnl_pct >= 0 else "-"
    lines = [
        f"{emoji} TRADE CLOSED",
        f"{ticker} | Entry ${entry_price:,.2f} \u2192 Exit ${exit_price:,.2f}",
        f"P&L: {pnl_sign}${abs(pnl):,.2f} ({pnl_pct_sign}{abs(pnl_pct):.1f}%)",
    ]
    if exit_reason:
        lines.append(f"Reason: {exit_reason}")
    return "\n".join(lines)


def guard_blocked_message(
    ticker: str,
    rule: str,
    reason: str,
) -> str:
    return "\n".join([
        "\u26a0\ufe0f PORTFOLIO GUARD",
        f"Blocked: {ticker} buy",
        f"Rule: {rule}",
        f"Reason: {reason}",
    ])


def daily_loss_message(
    daily_pnl_pct: float,
    portfolio_value: float,
) -> str:
    return "\n".join([
        "\U0001f6d1 DAILY LOSS LIMIT",
        f"Portfolio down {daily_pnl_pct:.1f}% today",
        f"Portfolio value: ${portfolio_value:,.2f}",
        "Engine: buys paused until tomorrow open",
    ])


def engine_error_message(
    component: str,
    error: str,
) -> str:
    return "\n".join([
        "\u274c ENGINE ERROR",
        f"Component: {component}",
        f"Error: {error}",
    ])


def weekly_digest_message(
    portfolio_value: float,
    week_pnl_pct: float,
    trades_opened: int,
    trades_closed: int,
    win_rate: float,
    best_ticker: str = "",
    best_pnl_pct: float = 0.0,
    worst_ticker: str = "",
    worst_pnl_pct: float = 0.0,
) -> str:
    lines = [
        "\U0001f4ca WEEKLY DIGEST",
        f"Portfolio: ${portfolio_value:,.2f} ({'+' if week_pnl_pct >= 0 else ''}{week_pnl_pct:.1f}% this week)",
        f"Trades: {trades_opened} opened, {trades_closed} closed",
        f"Win rate: {win_rate:.0f}% (all-time)",
    ]
    if best_ticker:
        lines.append(f"Best: {best_ticker} +{best_pnl_pct:.1f}% | Worst: {worst_ticker} {worst_pnl_pct:.1f}%")
    return "\n".join(lines)
