"""
Conviction Gate for TradeDog.

Decides whether a BUY signal should actually execute, based on:
  1. Conviction score threshold (must exceed minimum)
  2. Minimum agent agreement (N of 4 agents must agree on direction)
  3. Cooldown (don't re-buy same ticker within N hours)
  4. Max positions (don't exceed portfolio limit)

Sits between ConvictionScorer output and OrderManager execution.
Pure Python — no LLM calls, no tradingagents/ modifications.
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

from database.db import Database
from portfolio.conviction_scorer import ConvictionResult


@dataclass
class GateResult:
    """Result of the conviction gate check."""
    allowed: bool
    reason: str
    conviction_score: float = 0.0
    decision: str = "HOLD"


# ── Default thresholds (from design_reference.md) ────────────────────
DEFAULT_BUY_THRESHOLD = 65.0        # Composite score 0-100
DEFAULT_SELL_THRESHOLD = 35.0       # Below this → SELL signal
DEFAULT_MIN_AGENTS_AGREE = 3        # At least 3 of 4 must agree
DEFAULT_MAX_POSITIONS = 10
DEFAULT_COOLDOWN_HOURS = 24


class ConvictionGate:
    """Gate that decides whether a conviction-scored signal should execute."""

    def __init__(
        self,
        db: Optional[Database] = None,
        buy_threshold: float = DEFAULT_BUY_THRESHOLD,
        sell_threshold: float = DEFAULT_SELL_THRESHOLD,
        min_agents_agree: int = DEFAULT_MIN_AGENTS_AGREE,
        max_positions: int = DEFAULT_MAX_POSITIONS,
        cooldown_hours: int = DEFAULT_COOLDOWN_HOURS,
    ):
        self.db = db
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold
        self.min_agents_agree = min_agents_agree
        self.max_positions = max_positions
        self.cooldown_hours = cooldown_hours

    def check(
        self,
        conviction: ConvictionResult,
        current_positions: Optional[list] = None,
    ) -> GateResult:
        """
        Run all gate checks against a ConvictionResult.

        Args:
            conviction: Result from ConvictionScorer
            current_positions: List of currently open Position objects

        Returns:
            GateResult with allowed=True/False and reason
        """
        current_positions = current_positions or []

        # ── Check 1: Is the decision a BUY? ──
        if conviction.decision == "HOLD":
            return GateResult(
                allowed=False,
                reason="HOLD signal — no action",
                conviction_score=conviction.composite_score,
                decision="HOLD",
            )

        if conviction.decision == "SELL":
            # SELL signals bypass most gates — just pass through
            return GateResult(
                allowed=True,
                reason="SELL signal — passing through to execution",
                conviction_score=conviction.composite_score,
                decision="SELL",
            )

        # ── From here, decision is BUY ──

        # ── Check 2: Conviction threshold ──
        if conviction.composite_score < self.buy_threshold:
            return GateResult(
                allowed=False,
                reason=(
                    f"Conviction too low: {conviction.composite_score:.1f} "
                    f"< threshold {self.buy_threshold:.1f}"
                ),
                conviction_score=conviction.composite_score,
                decision="BUY",
            )

        # ── Check 3: Minimum agent agreement ──
        if conviction.agents_agreeing_buy < self.min_agents_agree:
            return GateResult(
                allowed=False,
                reason=(
                    f"Insufficient agent agreement: {conviction.agents_agreeing_buy} "
                    f"of 4 agree BUY (need {self.min_agents_agree})"
                ),
                conviction_score=conviction.composite_score,
                decision="BUY",
            )

        # ── Check 4: Max positions ──
        if len(current_positions) >= self.max_positions:
            return GateResult(
                allowed=False,
                reason=(
                    f"Max positions reached: {len(current_positions)} "
                    f">= limit {self.max_positions}"
                ),
                conviction_score=conviction.composite_score,
                decision="BUY",
            )

        # ── Check 5: Already holding this ticker ──
        held_tickers = {p.ticker for p in current_positions}
        if conviction.ticker in held_tickers:
            return GateResult(
                allowed=False,
                reason=f"Already holding {conviction.ticker}",
                conviction_score=conviction.composite_score,
                decision="BUY",
            )

        # ── Check 6: Cooldown ──
        if self.db and self.cooldown_hours > 0:
            cooldown_result = self._check_cooldown(conviction.ticker)
            if cooldown_result is not None:
                return cooldown_result

        # ── All checks passed ──
        return GateResult(
            allowed=True,
            reason=(
                f"All checks passed — conviction {conviction.composite_score:.1f}, "
                f"{conviction.agents_agreeing_buy}/4 agents agree BUY"
            ),
            conviction_score=conviction.composite_score,
            decision="BUY",
        )

    def _check_cooldown(self, ticker: str) -> Optional[GateResult]:
        """Check if ticker was traded too recently."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.cooldown_hours)
        cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

        row = self.db.conn.execute(
            """SELECT timestamp FROM signals
               WHERE ticker = ? AND action_taken IN ('BOUGHT', 'SOLD')
               AND timestamp > ?
               ORDER BY timestamp DESC LIMIT 1""",
            (ticker, cutoff_str),
        ).fetchone()

        if row:
            return GateResult(
                allowed=False,
                reason=(
                    f"Cooldown active: {ticker} was traded at {row['timestamp']} "
                    f"(within {self.cooldown_hours}h window)"
                ),
                conviction_score=0.0,
                decision="BUY",
            )
        return None
