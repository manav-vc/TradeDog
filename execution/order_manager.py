"""
Order Manager for TradeDog.

Sits between TradingAgentsGraph.propagate() output and the broker.
Takes the agent decision, logs it as a signal, and routes BUY decisions
to the broker for execution.

This module does NOT modify tradingagents/ — it wraps the framework's
output and builds on top of it.
"""

import json
import logging
from typing import Optional

from database.db import Database
from database.models import Order
from execution.broker_interface import BrokerInterface
from portfolio.position_sizer import calculate_position_size
from portfolio.conviction_scorer import score_from_state, ConvictionResult
from portfolio.conviction_gate import ConvictionGate, GateResult
from portfolio.portfolio_guard import PortfolioGuard
from config.config_manager import ConfigManager

logger = logging.getLogger(__name__)


class OrderManager:
    """Translates agent decisions into broker orders."""

    def __init__(
        self,
        broker: BrokerInterface,
        db: Optional[Database] = None,
        dry_run: bool = False,
        max_position_pct: float = 0.05,
        conviction_gate: Optional[ConvictionGate] = None,
        portfolio_guard: Optional[PortfolioGuard] = None,
        scoring_mode: str = "heuristic",
        scoring_llm=None,
        config_manager: Optional[ConfigManager] = None,
    ):
        self.broker = broker
        self.db = db or Database()
        self.dry_run = dry_run
        self.max_position_pct = max_position_pct
        self.conviction_gate = conviction_gate
        self.portfolio_guard = portfolio_guard
        self.scoring_mode = scoring_mode
        self.scoring_llm = scoring_llm
        self.config_manager = config_manager

    def _apply_live_config(self) -> None:
        """
        Hot-reload config values from user_config.json into gate/guard.

        Called before each trade evaluation so UI changes take effect
        without an engine restart.
        """
        if self.config_manager is None:
            return
        cfg = self.config_manager.load()

        # Update position sizing
        self.max_position_pct = cfg.get("risk_per_trade_pct", 5.0) / 100.0

        # Update conviction gate thresholds
        if self.conviction_gate is not None:
            self.conviction_gate.buy_threshold = float(cfg.get("conviction_threshold", 65))
            self.conviction_gate.max_positions = int(cfg.get("max_positions", 10))
            self.conviction_gate.min_agents_agree = int(cfg.get("min_agents_agree", 3))
            self.conviction_gate.cooldown_hours = int(cfg.get("cooldown_hours", 24))

        # Update portfolio guard limits
        if self.portfolio_guard is not None:
            self.portfolio_guard.max_positions = int(cfg.get("max_positions", 10))
            self.portfolio_guard.max_sector_exposure = cfg.get("max_sector_exposure_pct", 30.0) / 100.0
            self.portfolio_guard.max_single_position = cfg.get("risk_per_trade_pct", 5.0) / 100.0
            self.portfolio_guard.daily_loss_limit = -(cfg.get("daily_loss_limit_pct", 3.0) / 100.0)
            self.portfolio_guard.cash_reserve = cfg.get("cash_reserve_pct", 10.0) / 100.0

    def process_decision(
        self,
        ticker: str,
        trade_date: str,
        decision: str,
        full_state: Optional[dict] = None,
    ) -> Optional[Order]:
        """
        Process a single agent decision from propagate().

        Args:
            ticker: Stock ticker (e.g. "AAPL")
            trade_date: Date used for the analysis (e.g. "2024-11-01")
            decision: Parsed decision string from SignalProcessor ("BUY", "SELL", "HOLD")
            full_state: Full agent state dict (optional, for logging)

        Returns:
            Order if a trade was executed, None otherwise.
        """
        self._apply_live_config()

        parsed = self._parse_decision(decision)
        state_json = None
        if full_state:
            try:
                # Serialize what we can, skip non-serializable fields
                serializable = {
                    k: v for k, v in full_state.items()
                    if isinstance(v, (str, int, float, bool, list, dict, type(None)))
                }
                state_json = json.dumps(serializable, default=str)
            except (TypeError, ValueError):
                state_json = None

        if parsed == "BUY":
            return self._handle_buy(ticker, trade_date, decision, parsed, state_json)
        elif parsed == "SELL":
            return self._handle_sell(ticker, trade_date, decision, parsed, state_json)
        else:
            # HOLD — log and skip
            self.db.insert_signal(
                ticker=ticker,
                trade_date=trade_date,
                agent_decision=decision,
                parsed_action=parsed,
                action_taken="SKIPPED",
                skip_reason="HOLD signal — no action",
                full_state_json=state_json,
            )
            logger.info(f"{ticker}: HOLD — no action taken")
            return None

    def process_with_conviction(
        self,
        ticker: str,
        trade_date: str,
        full_state: dict,
        decision: str,
    ) -> Optional[Order]:
        """
        Process a propagate() result through conviction scoring + gate.

        This is the Phase 3+ entry point. Uses the full agent state to
        compute conviction scores, then checks the gate before executing.

        Args:
            ticker: Stock ticker
            trade_date: Analysis date
            full_state: Full state dict from propagate()
            decision: Raw decision text from SignalProcessor

        Returns:
            Order if trade was executed, None otherwise.
        """
        self._apply_live_config()

        # Step 1: Score conviction from the full state
        conviction = score_from_state(
            full_state, mode=self.scoring_mode, llm=self.scoring_llm
        )
        conviction.ticker = ticker

        state_json = self._serialize_state(full_state)

        # Step 2: Run conviction gate (if configured)
        if self.conviction_gate is not None:
            positions = self.broker.get_positions()
            gate_result = self.conviction_gate.check(conviction, positions)

            if not gate_result.allowed:
                action = "REJECTED_BY_GATE"
                self.db.insert_signal(
                    ticker=ticker, trade_date=trade_date,
                    agent_decision=decision,
                    parsed_action=conviction.decision,
                    action_taken=action,
                    skip_reason=gate_result.reason,
                    conviction_score=conviction.composite_score,
                    full_state_json=state_json,
                )
                logger.info(
                    f"{ticker}: BLOCKED by gate — {gate_result.reason} "
                    f"(conviction={conviction.composite_score:.1f})"
                )
                return None

        # Step 3: Gate passed (or no gate) — route to execution
        # Use conviction's decision (derived from reports) instead of raw text
        parsed = conviction.decision

        if parsed == "HOLD":
            self.db.insert_signal(
                ticker=ticker, trade_date=trade_date,
                agent_decision=decision, parsed_action=parsed,
                action_taken="SKIPPED",
                skip_reason="HOLD after conviction scoring",
                conviction_score=conviction.composite_score,
                full_state_json=state_json,
            )
            return None

        if parsed == "SELL":
            return self._handle_sell(ticker, trade_date, decision, parsed, state_json)

        # BUY — use conviction score for position sizing
        return self._handle_buy_with_conviction(
            ticker, trade_date, decision, parsed, state_json, conviction
        )

    def _handle_buy_with_conviction(
        self, ticker: str, trade_date: str, decision: str,
        parsed: str, state_json: Optional[str],
        conviction: ConvictionResult,
    ) -> Optional[Order]:
        """Execute a BUY with conviction-scaled position sizing."""
        existing = [p for p in self.broker.get_positions() if p.ticker == ticker]
        if existing:
            self.db.insert_signal(
                ticker=ticker, trade_date=trade_date,
                agent_decision=decision, parsed_action=parsed,
                action_taken="SKIPPED",
                skip_reason=f"Already holding {ticker}",
                conviction_score=conviction.composite_score,
                full_state_json=state_json,
            )
            return None

        account = self.broker.get_account()
        price = self.broker.get_current_price(ticker)

        # Scale position size by conviction (0-100 → 0.0-1.0)
        conviction_factor = conviction.composite_score / 100.0
        qty = calculate_position_size(
            account_value=account.total_value,
            price=price,
            max_position_pct=self.max_position_pct,
            conviction_score=conviction_factor,
        )

        if qty <= 0:
            self.db.insert_signal(
                ticker=ticker, trade_date=trade_date,
                agent_decision=decision, parsed_action=parsed,
                action_taken="SKIPPED",
                skip_reason="Position size calculated as 0 shares",
                conviction_score=conviction.composite_score,
                full_state_json=state_json,
            )
            return None

        # Portfolio guard check (Phase 5)
        proposed_cost = price * qty
        if self._run_portfolio_guard(
            ticker, trade_date, decision, parsed, state_json,
            proposed_cost, conviction.composite_score,
        ):
            return None  # Blocked by portfolio guard

        if self.dry_run:
            self.db.insert_signal(
                ticker=ticker, trade_date=trade_date,
                agent_decision=decision, parsed_action=parsed,
                action_taken="DRY_RUN",
                skip_reason=f"Would buy {qty} shares @ ~${price:.2f} (conviction={conviction.composite_score:.1f})",
                conviction_score=conviction.composite_score,
                full_state_json=state_json,
            )
            logger.info(
                f"{ticker}: DRY RUN — {qty} shares @ ${price:.2f} "
                f"(conviction={conviction.composite_score:.1f})"
            )
            return None

        order = self.broker.place_market_buy(ticker, qty)
        action = "BOUGHT" if order.status == "FILLED" else "REJECTED"
        self.db.insert_signal(
            ticker=ticker, trade_date=trade_date,
            agent_decision=decision, parsed_action=parsed,
            action_taken=action,
            skip_reason=None if action == "BOUGHT" else f"Order {order.status}",
            conviction_score=conviction.composite_score,
            full_state_json=state_json,
        )
        logger.info(
            f"{ticker}: {action} — {qty} shares @ ${order.filled_price or price:.2f} "
            f"(conviction={conviction.composite_score:.1f})"
        )
        return order

    def _serialize_state(self, full_state: Optional[dict]) -> Optional[str]:
        """Safely serialize state dict to JSON."""
        if not full_state:
            return None
        try:
            serializable = {
                k: v for k, v in full_state.items()
                if isinstance(v, (str, int, float, bool, list, dict, type(None)))
            }
            return json.dumps(serializable, default=str)
        except (TypeError, ValueError):
            return None

    def _handle_buy(
        self, ticker: str, trade_date: str, decision: str,
        parsed: str, state_json: Optional[str]
    ) -> Optional[Order]:
        """Execute a BUY decision."""
        # Check if we already have an open position
        existing = [p for p in self.broker.get_positions() if p.ticker == ticker]
        if existing:
            self.db.insert_signal(
                ticker=ticker, trade_date=trade_date,
                agent_decision=decision, parsed_action=parsed,
                action_taken="SKIPPED",
                skip_reason=f"Already holding {ticker} (position #{existing[0].id})",
                full_state_json=state_json,
            )
            logger.info(f"{ticker}: BUY skipped — already holding")
            return None

        # Calculate position size
        account = self.broker.get_account()
        price = self.broker.get_current_price(ticker)
        qty = calculate_position_size(
            account_value=account.total_value,
            price=price,
            max_position_pct=self.max_position_pct,
        )

        if qty <= 0:
            self.db.insert_signal(
                ticker=ticker, trade_date=trade_date,
                agent_decision=decision, parsed_action=parsed,
                action_taken="SKIPPED",
                skip_reason="Position size calculated as 0 shares",
                full_state_json=state_json,
            )
            return None

        # Portfolio guard check (Phase 5)
        proposed_cost = price * qty
        if self._run_portfolio_guard(
            ticker, trade_date, decision, parsed, state_json, proposed_cost,
        ):
            return None  # Blocked by portfolio guard

        if self.dry_run:
            self.db.insert_signal(
                ticker=ticker, trade_date=trade_date,
                agent_decision=decision, parsed_action=parsed,
                action_taken="DRY_RUN",
                skip_reason=f"Would buy {qty} shares @ ~${price:.2f}",
                full_state_json=state_json,
            )
            logger.info(f"{ticker}: DRY RUN — would buy {qty} shares @ ${price:.2f}")
            return None

        # Execute
        order = self.broker.place_market_buy(ticker, qty)

        action = "BOUGHT" if order.status == "FILLED" else "REJECTED"
        skip_reason = None if action == "BOUGHT" else f"Order {order.status}"
        self.db.insert_signal(
            ticker=ticker, trade_date=trade_date,
            agent_decision=decision, parsed_action=parsed,
            action_taken=action, skip_reason=skip_reason,
            full_state_json=state_json,
        )
        logger.info(f"{ticker}: {action} — {qty} shares @ ${order.filled_price or price:.2f}")
        return order

    def _run_portfolio_guard(
        self, ticker: str, trade_date: str, decision: str,
        parsed: str, state_json: Optional[str],
        proposed_cost: float, conviction_score: Optional[float] = None,
    ) -> Optional[None]:
        """
        Run portfolio guard if configured. Returns None if guard passes,
        or a sentinel None (after logging) if blocked.

        Returns:
            None if guard passes (caller should continue).
            A "marker" value if blocked (caller should return None).
        """
        if self.portfolio_guard is None:
            return None  # No guard — pass through

        guard_result = self.portfolio_guard.can_open_position(ticker, proposed_cost)
        if not guard_result.allowed:
            self.db.insert_signal(
                ticker=ticker, trade_date=trade_date,
                agent_decision=decision, parsed_action=parsed,
                action_taken="REJECTED_BY_GUARD",
                skip_reason=f"Portfolio guard: {guard_result.reason}",
                conviction_score=conviction_score,
                full_state_json=state_json,
            )
            logger.info(f"{ticker}: BLOCKED by portfolio guard — {guard_result.reason}")
            return "BLOCKED"  # Non-None sentinel to signal caller to return

        return None  # Pass through

    def _handle_sell(
        self, ticker: str, trade_date: str, decision: str,
        parsed: str, state_json: Optional[str]
    ) -> Optional[Order]:
        """Execute a SELL decision."""
        existing = [p for p in self.broker.get_positions() if p.ticker == ticker]
        if not existing:
            self.db.insert_signal(
                ticker=ticker, trade_date=trade_date,
                agent_decision=decision, parsed_action=parsed,
                action_taken="SKIPPED",
                skip_reason=f"SELL signal but no open position for {ticker}",
                full_state_json=state_json,
            )
            logger.info(f"{ticker}: SELL skipped — no position to sell")
            return None

        if self.dry_run:
            self.db.insert_signal(
                ticker=ticker, trade_date=trade_date,
                agent_decision=decision, parsed_action=parsed,
                action_taken="DRY_RUN",
                skip_reason=f"Would sell {existing[0].qty} shares of {ticker}",
                full_state_json=state_json,
            )
            return None

        order = self.broker.place_market_sell(ticker, existing[0].qty)

        action = "SOLD" if order.status == "FILLED" else "REJECTED"
        self.db.insert_signal(
            ticker=ticker, trade_date=trade_date,
            agent_decision=decision, parsed_action=parsed,
            action_taken=action,
            full_state_json=state_json,
        )
        return order

    def _parse_decision(self, decision: str) -> str:
        """Extract BUY/SELL/HOLD from the decision text."""
        text = decision.upper().strip()
        if "BUY" in text:
            return "BUY"
        elif "SELL" in text:
            return "SELL"
        else:
            return "HOLD"
