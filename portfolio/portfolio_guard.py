"""
Portfolio-level risk guard for TradeDog.

Enforces hard limits on the whole portfolio before any new position is opened:
  1. Max positions (default 10)
  2. Sector exposure (no single sector > 30%)
  3. Single position size (no stock > 8% of portfolio)
  4. Daily loss limit (halt buys if down 3% today)
  5. Cash reserve (always keep 10% in cash)

Also provides portfolio_summary() for the dashboard.

No tradingagents/ modifications — reads from the DB built in Phase 2.
"""

import json
import os
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from database.db import Database
from database.models import Position, AccountInfo

logger = logging.getLogger(__name__)

_SECTOR_MAP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "watchlist", "sector_map.json"
)

# ── Default limits (from design_reference.md) ─────────────────────────
DEFAULT_MAX_POSITIONS = 10
DEFAULT_MAX_SECTOR_EXPOSURE = 0.30      # 30%
DEFAULT_MAX_SINGLE_POSITION = 0.08      # 8%
DEFAULT_DAILY_LOSS_LIMIT = -0.03        # -3%
DEFAULT_CASH_RESERVE = 0.10             # 10%


@dataclass
class GuardCheckResult:
    """Result of a single guard check."""
    passed: bool
    rule: str
    reason: str


@dataclass
class GuardResult:
    """Result of the full guard evaluation."""
    allowed: bool
    reason: str
    checks: list  # list of GuardCheckResult


def _load_sector_map(path: Optional[str] = None) -> dict[str, str]:
    """Load ticker→sector mapping from JSON."""
    path = path or _SECTOR_MAP_PATH
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


class PortfolioGuard:
    """Portfolio-level risk checks before opening any position."""

    def __init__(
        self,
        db: Database,
        starting_cash: float = 100_000.0,
        max_positions: int = DEFAULT_MAX_POSITIONS,
        max_sector_exposure: float = DEFAULT_MAX_SECTOR_EXPOSURE,
        max_single_position: float = DEFAULT_MAX_SINGLE_POSITION,
        daily_loss_limit: float = DEFAULT_DAILY_LOSS_LIMIT,
        cash_reserve: float = DEFAULT_CASH_RESERVE,
        sector_map: Optional[dict[str, str]] = None,
    ):
        self.db = db
        self.starting_cash = starting_cash
        self.max_positions = max_positions
        self.max_sector_exposure = max_sector_exposure
        self.max_single_position = max_single_position
        self.daily_loss_limit = daily_loss_limit
        self.cash_reserve = cash_reserve
        self.sector_map = sector_map or _load_sector_map()

    def can_open_position(
        self,
        ticker: str,
        proposed_cost: float,
    ) -> GuardResult:
        """
        Run all guard checks before opening a new position.

        Args:
            ticker: Stock ticker to buy
            proposed_cost: Dollar amount of the proposed purchase (price * qty)

        Returns:
            GuardResult with allowed=True/False, reason, and individual check details.
        """
        positions = self.db.get_open_positions()
        account = self.db.get_account_summary(self.starting_cash)

        checks = [
            self._check_max_positions(positions),
            self._check_duplicate_holding(ticker, positions),
            self._check_sector_exposure(ticker, proposed_cost, positions, account),
            self._check_single_position_size(proposed_cost, account),
            self._check_daily_loss(account),
            self._check_cash_reserve(proposed_cost, account),
        ]

        failures = [c for c in checks if not c.passed]

        if failures:
            return GuardResult(
                allowed=False,
                reason=failures[0].reason,
                checks=checks,
            )

        return GuardResult(
            allowed=True,
            reason="All portfolio guard checks passed",
            checks=checks,
        )

    # ── Individual checks ─────────────────────────────────────────────

    def _check_max_positions(self, positions: list[Position]) -> GuardCheckResult:
        """No more than max_positions open at once."""
        count = len(positions)
        if count >= self.max_positions:
            return GuardCheckResult(
                passed=False,
                rule="MAX_POSITIONS",
                reason=(
                    f"Max positions reached: {count} >= {self.max_positions}"
                ),
            )
        return GuardCheckResult(passed=True, rule="MAX_POSITIONS", reason="OK")

    def _check_duplicate_holding(
        self, ticker: str, positions: list[Position]
    ) -> GuardCheckResult:
        """Don't open a duplicate position in the same ticker."""
        held = {p.ticker for p in positions}
        if ticker in held:
            return GuardCheckResult(
                passed=False,
                rule="DUPLICATE_HOLDING",
                reason=f"Already holding {ticker}",
            )
        return GuardCheckResult(passed=True, rule="DUPLICATE_HOLDING", reason="OK")

    def _check_sector_exposure(
        self,
        ticker: str,
        proposed_cost: float,
        positions: list[Position],
        account: AccountInfo,
    ) -> GuardCheckResult:
        """No single sector > max_sector_exposure of portfolio."""
        if account.total_value <= 0:
            return GuardCheckResult(passed=True, rule="SECTOR_EXPOSURE", reason="OK")

        ticker_sector = self.sector_map.get(ticker, "Unknown")

        # Sum cost_basis of existing positions in the same sector
        sector_invested = sum(
            p.cost_basis for p in positions
            if self.sector_map.get(p.ticker, "Unknown") == ticker_sector
        )
        # Add proposed purchase
        new_sector_total = sector_invested + proposed_cost
        sector_pct = new_sector_total / account.total_value

        if sector_pct > self.max_sector_exposure:
            return GuardCheckResult(
                passed=False,
                rule="SECTOR_EXPOSURE",
                reason=(
                    f"Sector '{ticker_sector}' would be {sector_pct:.1%} of portfolio "
                    f"(limit {self.max_sector_exposure:.0%}). "
                    f"Current: ${sector_invested:,.0f}, proposed: +${proposed_cost:,.0f}"
                ),
            )
        return GuardCheckResult(passed=True, rule="SECTOR_EXPOSURE", reason="OK")

    def _check_single_position_size(
        self, proposed_cost: float, account: AccountInfo
    ) -> GuardCheckResult:
        """No single position > max_single_position of portfolio."""
        if account.total_value <= 0:
            return GuardCheckResult(passed=True, rule="POSITION_SIZE", reason="OK")

        position_pct = proposed_cost / account.total_value
        if position_pct > self.max_single_position:
            return GuardCheckResult(
                passed=False,
                rule="POSITION_SIZE",
                reason=(
                    f"Position ${proposed_cost:,.0f} would be {position_pct:.1%} of portfolio "
                    f"(limit {self.max_single_position:.0%})"
                ),
            )
        return GuardCheckResult(passed=True, rule="POSITION_SIZE", reason="OK")

    def _check_daily_loss(self, account: AccountInfo) -> GuardCheckResult:
        """Stop buys if portfolio is down more than daily_loss_limit today."""
        # Get today's snapshot for comparison
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = self.db.conn.execute(
            "SELECT * FROM portfolio_snapshots WHERE snapshot_date < ? ORDER BY snapshot_date DESC LIMIT 1",
            (today,),
        ).fetchone()

        if row is None:
            # No previous snapshot — use starting cash as baseline
            previous_value = self.starting_cash
        else:
            previous_value = row["total_value"]

        if previous_value <= 0:
            return GuardCheckResult(passed=True, rule="DAILY_LOSS", reason="OK")

        daily_change = (account.total_value - previous_value) / previous_value

        if daily_change <= self.daily_loss_limit:
            return GuardCheckResult(
                passed=False,
                rule="DAILY_LOSS",
                reason=(
                    f"Daily loss limit hit: portfolio down {daily_change:.1%} today "
                    f"(limit {self.daily_loss_limit:.0%}). "
                    f"Previous: ${previous_value:,.0f}, current: ${account.total_value:,.0f}. "
                    f"Buys halted until tomorrow."
                ),
            )
        return GuardCheckResult(passed=True, rule="DAILY_LOSS", reason="OK")

    def _check_cash_reserve(
        self, proposed_cost: float, account: AccountInfo
    ) -> GuardCheckResult:
        """Always keep cash_reserve fraction of portfolio in cash."""
        if account.total_value <= 0:
            return GuardCheckResult(passed=True, rule="CASH_RESERVE", reason="OK")

        cash_after = account.cash - proposed_cost
        reserve_needed = account.total_value * self.cash_reserve

        if cash_after < reserve_needed:
            return GuardCheckResult(
                passed=False,
                rule="CASH_RESERVE",
                reason=(
                    f"Cash reserve violated: after purchase cash would be "
                    f"${cash_after:,.0f}, need ${reserve_needed:,.0f} "
                    f"({self.cash_reserve:.0%} reserve of ${account.total_value:,.0f})"
                ),
            )
        return GuardCheckResult(passed=True, rule="CASH_RESERVE", reason="OK")

    # ── Portfolio Summary (for dashboard) ─────────────────────────────

    def portfolio_summary(self) -> dict:
        """
        Generate a full portfolio summary for the dashboard.

        Returns dict with: positions, account, sector_breakdown,
        recent_signals, and guard_status.
        """
        positions = self.db.get_open_positions()
        account = self.db.get_account_summary(self.starting_cash)
        closed = self.db.get_closed_positions()

        # Sector breakdown
        sector_breakdown = {}
        for p in positions:
            sector = self.sector_map.get(p.ticker, "Unknown")
            sector_breakdown[sector] = sector_breakdown.get(sector, 0) + p.cost_basis

        # Convert to percentages
        sector_pcts = {}
        if account.total_value > 0:
            for sector, value in sector_breakdown.items():
                sector_pcts[sector] = round(value / account.total_value * 100, 1)

        # Win rate from closed trades
        wins = sum(1 for p in closed if p.pnl and p.pnl > 0)
        total_closed = len(closed)
        win_rate = (wins / total_closed * 100) if total_closed > 0 else 0.0

        # Total realized P&L
        realized_pnl = sum((p.pnl or 0.0) for p in closed)

        return {
            "account": {
                "total_value": round(account.total_value, 2),
                "cash": round(account.cash, 2),
                "invested": round(account.invested, 2),
                "buying_power": round(account.buying_power, 2),
            },
            "positions": {
                "open_count": len(positions),
                "max_allowed": self.max_positions,
                "tickers": [p.ticker for p in positions],
            },
            "sector_breakdown": sector_pcts,
            "performance": {
                "total_closed_trades": total_closed,
                "win_rate": round(win_rate, 1),
                "realized_pnl": round(realized_pnl, 2),
            },
            "guard_limits": {
                "max_positions": self.max_positions,
                "max_sector_exposure": f"{self.max_sector_exposure:.0%}",
                "max_single_position": f"{self.max_single_position:.0%}",
                "daily_loss_limit": f"{self.daily_loss_limit:.0%}",
                "cash_reserve": f"{self.cash_reserve:.0%}",
            },
        }
