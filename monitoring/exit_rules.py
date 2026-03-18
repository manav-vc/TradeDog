"""
Exit rules for TradeDog position monitoring.

Five exit conditions, checked in priority order:
  1. Stop loss       — hard floor, exit if loss >= 8% from entry
  2. Profit target   — hard target, exit if gain >= 15%
  3. Trailing stop   — exit if price drops 7% from highest reached
  4. Reversal signal — exit if RSI overbought + MACD bearish crossover
  5. Time-based      — exit after 30 days if none of the above triggered

Each rule is a pure function: (position, current_price, **kwargs) -> ExitSignal.
No tradingagents/ modifications — reversal detection uses yfinance directly.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ExitSignal:
    """Result of an exit rule check."""
    should_exit: bool
    rule: str = ""           # e.g. "STOP_LOSS", "PROFIT_TARGET", etc.
    reason: str = ""         # Human-readable explanation
    current_price: float = 0.0
    trigger_value: float = 0.0  # The threshold that was breached


# ── Default thresholds ────────────────────────────────────────────────
DEFAULT_PROFIT_TARGET_PCT = 0.15     # 15% gain
DEFAULT_TRAILING_STOP_PCT = 0.07     # 7% drop from peak
DEFAULT_STOP_LOSS_PCT = 0.08         # 8% loss from entry
DEFAULT_MAX_HOLD_DAYS = 30           # 30 days
DEFAULT_RSI_OVERBOUGHT = 75.0        # RSI above this = overbought


# ===================================================================
# Individual Exit Rules
# ===================================================================

def check_stop_loss(
    position, current_price: float,
    stop_loss_pct: float = DEFAULT_STOP_LOSS_PCT,
) -> ExitSignal:
    """Exit if loss from entry exceeds threshold."""
    loss_pct = (position.entry_price - current_price) / position.entry_price

    if loss_pct >= stop_loss_pct:
        return ExitSignal(
            should_exit=True,
            rule="STOP_LOSS",
            reason=(
                f"Stop loss triggered: {position.ticker} down "
                f"{loss_pct:.1%} from entry ${position.entry_price:.2f} "
                f"(current ${current_price:.2f}, limit {stop_loss_pct:.0%})"
            ),
            current_price=current_price,
            trigger_value=position.entry_price * (1 - stop_loss_pct),
        )

    return ExitSignal(should_exit=False, rule="STOP_LOSS", current_price=current_price)


def check_profit_target(
    position, current_price: float,
    profit_target_pct: float = DEFAULT_PROFIT_TARGET_PCT,
) -> ExitSignal:
    """Exit if gain from entry exceeds threshold."""
    gain_pct = (current_price - position.entry_price) / position.entry_price

    if gain_pct >= profit_target_pct:
        return ExitSignal(
            should_exit=True,
            rule="PROFIT_TARGET",
            reason=(
                f"Profit target hit: {position.ticker} up "
                f"{gain_pct:.1%} from entry ${position.entry_price:.2f} "
                f"(current ${current_price:.2f}, target {profit_target_pct:.0%})"
            ),
            current_price=current_price,
            trigger_value=position.entry_price * (1 + profit_target_pct),
        )

    return ExitSignal(should_exit=False, rule="PROFIT_TARGET", current_price=current_price)


def check_trailing_stop(
    position, current_price: float,
    trail_pct: float = DEFAULT_TRAILING_STOP_PCT,
    update_highest_callback=None,
) -> ExitSignal:
    """
    Exit if price drops trail_pct from the highest price reached.

    If current_price > highest_price, updates the high watermark via callback.
    """
    highest = position.highest_price or position.entry_price

    # Update high watermark if new high
    if current_price > highest:
        highest = current_price
        if update_highest_callback:
            update_highest_callback(position.id, current_price)

    trail_level = highest * (1 - trail_pct)

    if current_price <= trail_level:
        drop_pct = (highest - current_price) / highest
        return ExitSignal(
            should_exit=True,
            rule="TRAILING_STOP",
            reason=(
                f"Trailing stop triggered: {position.ticker} dropped "
                f"{drop_pct:.1%} from peak ${highest:.2f} "
                f"(current ${current_price:.2f}, trail level ${trail_level:.2f})"
            ),
            current_price=current_price,
            trigger_value=trail_level,
        )

    return ExitSignal(should_exit=False, rule="TRAILING_STOP", current_price=current_price)


def check_time_exit(
    position,
    current_price: float = 0.0,
    max_hold_days: int = DEFAULT_MAX_HOLD_DAYS,
    now: Optional[datetime] = None,
) -> ExitSignal:
    """Exit if position has been held longer than max_hold_days."""
    now = now or datetime.now(timezone.utc)

    entry_time = position.entry_time
    if isinstance(entry_time, str):
        # Parse string timestamps from DB
        try:
            entry_time = datetime.fromisoformat(entry_time)
        except (ValueError, TypeError):
            return ExitSignal(should_exit=False, rule="TIME_EXIT", current_price=current_price)

    # Make timezone-aware if needed
    if entry_time and entry_time.tzinfo is None:
        entry_time = entry_time.replace(tzinfo=timezone.utc)

    if entry_time is None:
        return ExitSignal(should_exit=False, rule="TIME_EXIT", current_price=current_price)

    days_held = (now - entry_time).days

    if days_held >= max_hold_days:
        return ExitSignal(
            should_exit=True,
            rule="TIME_EXIT",
            reason=(
                f"Time-based exit: {position.ticker} held for "
                f"{days_held} days (limit {max_hold_days} days)"
            ),
            current_price=current_price,
            trigger_value=float(max_hold_days),
        )

    return ExitSignal(should_exit=False, rule="TIME_EXIT", current_price=current_price)


def _get_indicator(symbol: str, indicator: str, curr_date: str, lookback: int) -> str:
    """Wrapper around tradingagents indicator fetch — patchable for tests."""
    from tradingagents.dataflows.y_finance import get_stock_stats_indicators_window
    return get_stock_stats_indicators_window(symbol, indicator, curr_date, lookback)


def check_reversal(
    position,
    current_price: float = 0.0,
    rsi_threshold: float = DEFAULT_RSI_OVERBOUGHT,
    curr_date: Optional[str] = None,
) -> ExitSignal:
    """
    Lightweight reversal detection using RSI + MACD from yfinance.

    Does NOT run the full agent pipeline — just fetches technical indicators
    directly from the existing data layer.

    Triggers exit when: RSI > 75 AND MACD histogram is negative (bearish).
    """

    if curr_date is None:
        curr_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        # Fetch RSI
        rsi_data = _get_indicator(position.ticker, "rsi", curr_date, 30)
        rsi_value = _extract_latest_value(rsi_data)

        # Fetch MACD histogram
        macdh_data = _get_indicator(position.ticker, "macdh", curr_date, 30)
        macdh_value = _extract_latest_value(macdh_data)

        if rsi_value is None or macdh_value is None:
            return ExitSignal(should_exit=False, rule="REVERSAL", current_price=current_price)

        # Reversal signal: overbought RSI + bearish MACD momentum
        if rsi_value > rsi_threshold and macdh_value < 0:
            return ExitSignal(
                should_exit=True,
                rule="REVERSAL",
                reason=(
                    f"Reversal detected for {position.ticker}: "
                    f"RSI={rsi_value:.1f} (>{rsi_threshold}) + "
                    f"MACD histogram={macdh_value:.4f} (bearish)"
                ),
                current_price=current_price,
                trigger_value=rsi_value,
            )

    except Exception as e:
        logger.warning(f"Reversal check failed for {position.ticker}: {e}")

    return ExitSignal(should_exit=False, rule="REVERSAL", current_price=current_price)


def _extract_latest_value(indicator_csv: str) -> Optional[float]:
    """Extract the most recent numeric value from an indicator CSV string."""
    lines = [
        line for line in indicator_csv.strip().split("\n")
        if line.strip() and not line.startswith("#")
    ]
    if len(lines) < 2:
        return None

    # Last data row, last column (the indicator value)
    try:
        last_line = lines[-1]
        fields = last_line.split(",")
        # Try the last field, then second-to-last
        for idx in [-1, -2]:
            try:
                val = float(fields[idx].strip())
                return val
            except (ValueError, IndexError):
                continue
    except Exception:
        pass
    return None


# ===================================================================
# Combined Check — runs all rules in priority order
# ===================================================================

def check_all_exit_rules(
    position,
    current_price: float,
    update_highest_callback=None,
    profit_target_pct: float = DEFAULT_PROFIT_TARGET_PCT,
    trailing_stop_pct: float = DEFAULT_TRAILING_STOP_PCT,
    stop_loss_pct: float = DEFAULT_STOP_LOSS_PCT,
    max_hold_days: int = DEFAULT_MAX_HOLD_DAYS,
    check_reversal_flag: bool = False,
    curr_date: Optional[str] = None,
) -> ExitSignal:
    """
    Check all exit rules in priority order. Returns the first triggered signal.

    Priority: stop_loss > profit_target > trailing_stop > reversal > time_exit

    Args:
        position: Position object (needs ticker, entry_price, highest_price, entry_time)
        current_price: Current market price
        update_highest_callback: fn(position_id, price) to persist new highs
        check_reversal_flag: Whether to run reversal detection (makes API calls)
        curr_date: Date string for reversal check

    Returns:
        ExitSignal — should_exit=True if any rule triggers
    """
    # 1. Stop loss — highest priority (protect capital)
    signal = check_stop_loss(position, current_price, stop_loss_pct)
    if signal.should_exit:
        return signal

    # 2. Profit target — lock in gains
    signal = check_profit_target(position, current_price, profit_target_pct)
    if signal.should_exit:
        return signal

    # 3. Trailing stop — protect unrealized gains
    signal = check_trailing_stop(
        position, current_price, trailing_stop_pct, update_highest_callback
    )
    if signal.should_exit:
        return signal

    # 4. Reversal detection — optional, makes API calls
    if check_reversal_flag:
        signal = check_reversal(position, current_price, curr_date=curr_date)
        if signal.should_exit:
            return signal

    # 5. Time-based exit — last resort
    signal = check_time_exit(position, current_price, max_hold_days)
    if signal.should_exit:
        return signal

    # No exit triggered
    return ExitSignal(should_exit=False, rule="NONE", current_price=current_price)
