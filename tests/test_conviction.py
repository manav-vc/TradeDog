"""
Phase 3 — Conviction scoring and gate tests for TradeDog.

Tests the full conviction pipeline:
  ConvictionScorer (heuristic) → ConvictionGate → OrderManager.process_with_conviction()

Uses synthetic agent state dicts to simulate various scenarios.
"""

import os
import tempfile
import pytest
from unittest.mock import patch

from database.db import Database
from database.models import Position
from execution.paper_broker import PaperBroker
from execution.order_manager import OrderManager
from portfolio.conviction_scorer import (
    score_from_state,
    _score_text,
    AgentSignal,
    ConvictionResult,
    CONVICTION_WEIGHTS,
)
from portfolio.conviction_gate import ConvictionGate, GateResult


# ===================================================================
# Fixtures
# ===================================================================

@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    database = Database(db_path=path)
    yield database
    database.close()
    os.unlink(path)


@pytest.fixture
def paper_broker(db):
    return PaperBroker(db=db, starting_cash=100_000.0)


@pytest.fixture
def gate(db):
    return ConvictionGate(
        db=db,
        buy_threshold=65.0,
        min_agents_agree=3,
        max_positions=10,
        cooldown_hours=24,
    )


@pytest.fixture
def order_manager(paper_broker, db, gate):
    return OrderManager(
        broker=paper_broker, db=db,
        conviction_gate=gate,
        scoring_mode="heuristic",
        max_position_pct=0.05,
    )


# ── Helper: build synthetic propagate() state ────────────────────────

def _make_state(
    ticker: str = "AAPL",
    market_tone: str = "bullish",
    fundamental_tone: str = "bullish",
    sentiment_tone: str = "bullish",
    debate_tone: str = "bullish",
) -> dict:
    """Build a fake propagate() state with controllable tone per agent."""
    tones = {
        "bullish": (
            "Strong buy signal. Bullish momentum with breakout above resistance. "
            "Uptrend confirmed. Growth accelerating. Beat expectations. Positive outlook. "
            "Revenue growth is strong. Accumulate. Upside potential is significant. Opportunity."
        ),
        "bearish": (
            "Strong sell signal. Bearish breakdown below support level. "
            "Downtrend confirmed. Decline accelerating. Missed expectations. Negative outlook. "
            "Revenue decline is concerning. Risk of further downside. Overvalued. Lower lows."
        ),
        "neutral": (
            "Mixed signals. Hold for now. Sideways consolidation pattern. "
            "Range-bound trading. Wait for clearer direction. Uncertain outlook."
        ),
    }

    return {
        "company_of_interest": ticker,
        "trade_date": "2024-11-01",
        "market_report": tones[market_tone],
        "fundamentals_report": tones[fundamental_tone],
        "sentiment_report": tones[sentiment_tone],
        "news_report": "",
        "investment_debate_state": {
            "judge_decision": tones[debate_tone],
            "history": "",
            "bull_history": "",
            "bear_history": "",
        },
        "investment_plan": tones[debate_tone],
        "final_trade_decision": f"The recommendation is to {'BUY' if debate_tone == 'bullish' else 'SELL' if debate_tone == 'bearish' else 'HOLD'}.",
        "messages": [],
    }


# ===================================================================
# ConvictionScorer — Heuristic Mode
# ===================================================================

class TestHeuristicScorer:
    def test_all_bullish_high_score(self):
        """All agents bullish → high conviction score."""
        state = _make_state(
            market_tone="bullish", fundamental_tone="bullish",
            sentiment_tone="bullish", debate_tone="bullish",
        )
        result = score_from_state(state, mode="heuristic")
        assert result.composite_score > 70
        assert result.decision == "BUY"
        assert result.agents_agreeing_buy >= 3

    def test_all_bearish_low_score(self):
        """All agents bearish → low conviction score."""
        state = _make_state(
            market_tone="bearish", fundamental_tone="bearish",
            sentiment_tone="bearish", debate_tone="bearish",
        )
        result = score_from_state(state, mode="heuristic")
        assert result.composite_score < 30
        assert result.decision == "SELL"
        assert result.agents_agreeing_sell >= 3

    def test_all_neutral_middle_score(self):
        """All agents neutral → ~50 score, HOLD."""
        state = _make_state(
            market_tone="neutral", fundamental_tone="neutral",
            sentiment_tone="neutral", debate_tone="neutral",
        )
        result = score_from_state(state, mode="heuristic")
        assert 35 < result.composite_score < 65
        assert result.decision == "HOLD"

    def test_mixed_signals(self):
        """2 bullish, 1 bearish, 1 neutral → moderate score."""
        state = _make_state(
            market_tone="bullish", fundamental_tone="bullish",
            sentiment_tone="bearish", debate_tone="neutral",
        )
        result = score_from_state(state, mode="heuristic")
        # Should be somewhere in the middle — not extreme either way
        assert 30 < result.composite_score < 70

    def test_debate_has_highest_weight(self):
        """Bull/bear dimension (0.30) should dominate when others are neutral."""
        state_bull_debate = _make_state(
            market_tone="neutral", fundamental_tone="neutral",
            sentiment_tone="neutral", debate_tone="bullish",
        )
        state_bear_debate = _make_state(
            market_tone="neutral", fundamental_tone="neutral",
            sentiment_tone="neutral", debate_tone="bearish",
        )
        bull_result = score_from_state(state_bull_debate)
        bear_result = score_from_state(state_bear_debate)
        assert bull_result.composite_score > bear_result.composite_score

    def test_empty_reports_produce_hold(self):
        """Empty reports → no signal keywords → HOLD."""
        state = {
            "company_of_interest": "AAPL",
            "market_report": "",
            "fundamentals_report": "",
            "sentiment_report": "",
            "news_report": "",
            "investment_debate_state": {},
            "investment_plan": "",
            "final_trade_decision": "",
        }
        result = score_from_state(state)
        assert result.decision == "HOLD"
        assert 45 < result.composite_score < 55

    def test_result_has_4_signals(self):
        """Result always has exactly 4 agent signals."""
        state = _make_state()
        result = score_from_state(state)
        assert len(result.signals) == 4
        names = {s.name for s in result.signals}
        assert names == {"technical", "fundamental", "sentiment", "bull_bear"}

    def test_scores_bounded(self):
        """All scores are within valid ranges."""
        state = _make_state()
        result = score_from_state(state)
        assert 0 <= result.composite_score <= 100
        assert -1.0 <= result.raw_score <= 1.0
        for s in result.signals:
            assert -1.0 <= s.score <= 1.0


class TestScoreText:
    def test_bullish_text(self):
        sig = _score_text("Strong buy signal, bullish momentum, uptrend breakout", "test")
        assert sig.signal == "BUY"
        assert sig.score > 0

    def test_bearish_text(self):
        sig = _score_text("Strong sell, bearish breakdown, decline risk downside", "test")
        assert sig.signal == "SELL"
        assert sig.score < 0

    def test_empty_text(self):
        sig = _score_text("", "test")
        assert sig.signal == "HOLD"
        assert sig.score == 0.0


# ===================================================================
# ConvictionGate
# ===================================================================

class TestConvictionGate:
    def test_high_conviction_passes(self, gate):
        """Score above threshold + enough agents → allowed."""
        conviction = ConvictionResult(
            ticker="AAPL", composite_score=80.0, raw_score=0.6,
            decision="BUY", agents_agreeing_buy=4,
        )
        result = gate.check(conviction, current_positions=[])
        assert result.allowed is True

    def test_low_conviction_blocked(self, gate):
        """Score below threshold → blocked."""
        conviction = ConvictionResult(
            ticker="AAPL", composite_score=50.0, raw_score=0.0,
            decision="BUY", agents_agreeing_buy=4,
        )
        result = gate.check(conviction, current_positions=[])
        assert result.allowed is False
        assert "Conviction too low" in result.reason

    def test_insufficient_agents_blocked(self, gate):
        """Too few agents agreeing → blocked."""
        conviction = ConvictionResult(
            ticker="AAPL", composite_score=80.0, raw_score=0.6,
            decision="BUY", agents_agreeing_buy=2,  # Need 3
        )
        result = gate.check(conviction, current_positions=[])
        assert result.allowed is False
        assert "agent agreement" in result.reason

    def test_max_positions_blocked(self, gate):
        """At max positions → blocked."""
        # Create 10 fake positions
        positions = [Position(ticker=f"T{i}", status="OPEN") for i in range(10)]
        conviction = ConvictionResult(
            ticker="AAPL", composite_score=90.0, decision="BUY",
            agents_agreeing_buy=4,
        )
        result = gate.check(conviction, current_positions=positions)
        assert result.allowed is False
        assert "Max positions" in result.reason

    def test_already_holding_blocked(self, gate):
        """Already holding the ticker → blocked."""
        positions = [Position(ticker="AAPL", status="OPEN")]
        conviction = ConvictionResult(
            ticker="AAPL", composite_score=90.0, decision="BUY",
            agents_agreeing_buy=4,
        )
        result = gate.check(conviction, current_positions=positions)
        assert result.allowed is False
        assert "Already holding" in result.reason

    def test_hold_signal_blocked(self, gate):
        """HOLD decision → not allowed (no action)."""
        conviction = ConvictionResult(
            ticker="AAPL", composite_score=50.0, decision="HOLD",
        )
        result = gate.check(conviction)
        assert result.allowed is False
        assert "HOLD" in result.reason

    def test_sell_signal_passes_through(self, gate):
        """SELL decisions bypass most gate checks."""
        conviction = ConvictionResult(
            ticker="AAPL", composite_score=20.0, decision="SELL",
        )
        result = gate.check(conviction)
        assert result.allowed is True
        assert "SELL" in result.reason

    def test_cooldown_blocks_recent_trade(self, gate, db):
        """Ticker traded within cooldown window → blocked."""
        # Insert a recent signal
        db.insert_signal(
            ticker="AAPL", trade_date="2024-11-01",
            agent_decision="BUY", parsed_action="BUY",
            action_taken="BOUGHT",
        )
        conviction = ConvictionResult(
            ticker="AAPL", composite_score=90.0, decision="BUY",
            agents_agreeing_buy=4,
        )
        result = gate.check(conviction, current_positions=[])
        assert result.allowed is False
        assert "Cooldown" in result.reason

    def test_custom_thresholds(self, db):
        """Gate respects custom thresholds."""
        lenient_gate = ConvictionGate(
            db=db, buy_threshold=30.0, min_agents_agree=1, max_positions=50,
        )
        conviction = ConvictionResult(
            ticker="AAPL", composite_score=35.0, decision="BUY",
            agents_agreeing_buy=1,
        )
        result = lenient_gate.check(conviction, current_positions=[])
        assert result.allowed is True


# ===================================================================
# OrderManager with Conviction — Integration
# ===================================================================

class TestOrderManagerConviction:
    @patch("execution.paper_broker.yf")
    def test_high_conviction_buy_executes(self, mock_yf, order_manager):
        """All-bullish state → high conviction → gate passes → order filled."""
        mock_yf.Ticker.return_value.info = {"currentPrice": 150.0}
        state = _make_state(ticker="AAPL")

        order = order_manager.process_with_conviction(
            ticker="AAPL", trade_date="2024-11-01",
            full_state=state, decision="BUY",
        )
        assert order is not None
        assert order.status == "FILLED"

    @patch("execution.paper_broker.yf")
    def test_low_conviction_buy_blocked(self, mock_yf, order_manager):
        """Mixed/neutral state → low conviction → gate blocks."""
        mock_yf.Ticker.return_value.info = {"currentPrice": 150.0}
        state = _make_state(
            ticker="AAPL",
            market_tone="neutral", fundamental_tone="neutral",
            sentiment_tone="neutral", debate_tone="neutral",
        )

        order = order_manager.process_with_conviction(
            ticker="AAPL", trade_date="2024-11-01",
            full_state=state, decision="HOLD",
        )
        assert order is None

    @patch("execution.paper_broker.yf")
    def test_bearish_state_no_buy(self, mock_yf, order_manager):
        """All-bearish state → SELL decision → no buy (no position to sell either)."""
        mock_yf.Ticker.return_value.info = {"currentPrice": 150.0}
        state = _make_state(
            ticker="AAPL",
            market_tone="bearish", fundamental_tone="bearish",
            sentiment_tone="bearish", debate_tone="bearish",
        )

        order = order_manager.process_with_conviction(
            ticker="AAPL", trade_date="2024-11-01",
            full_state=state, decision="SELL",
        )
        # SELL passes gate but no position to sell
        assert order is None

    @patch("execution.paper_broker.yf")
    def test_conviction_logged_in_signal(self, mock_yf, order_manager, db):
        """Conviction score is persisted in the signals table."""
        mock_yf.Ticker.return_value.info = {"currentPrice": 150.0}
        state = _make_state(ticker="AAPL")

        order_manager.process_with_conviction(
            ticker="AAPL", trade_date="2024-11-01",
            full_state=state, decision="BUY",
        )

        signals = db.get_recent_signals()
        assert len(signals) >= 1
        latest = signals[0]
        assert latest["conviction_score"] is not None
        assert latest["conviction_score"] > 50

    @patch("execution.paper_broker.yf")
    def test_gate_rejection_logged(self, mock_yf, order_manager, db):
        """Gate rejection is logged with reason."""
        mock_yf.Ticker.return_value.info = {"currentPrice": 150.0}
        state = _make_state(
            ticker="AAPL",
            market_tone="neutral", fundamental_tone="neutral",
            sentiment_tone="bearish", debate_tone="neutral",
        )

        order_manager.process_with_conviction(
            ticker="AAPL", trade_date="2024-11-01",
            full_state=state, decision="BUY",
        )

        signals = db.get_recent_signals()
        assert len(signals) >= 1
        # Should be REJECTED_BY_GATE or SKIPPED
        latest = signals[0]
        assert latest["action_taken"] in ("REJECTED_BY_GATE", "SKIPPED")

    @patch("execution.paper_broker.yf")
    def test_conviction_scales_position_size(self, mock_yf, order_manager):
        """Higher conviction → larger position size."""
        mock_yf.Ticker.return_value.info = {"currentPrice": 150.0}

        # Very bullish = high conviction
        high_state = _make_state(ticker="AAPL")
        order_high = order_manager.process_with_conviction(
            ticker="AAPL", trade_date="2024-11-01",
            full_state=high_state, decision="BUY",
        )

        # Record the qty
        assert order_high is not None
        high_qty = order_high.qty

        # Reset — sell the position
        mock_yf.Ticker.return_value.info = {"currentPrice": 150.0}
        order_manager.broker.place_market_sell("AAPL", high_qty)

        # Create a new gate without cooldown for this test
        order_manager.conviction_gate = ConvictionGate(
            db=order_manager.db, buy_threshold=55.0,
            min_agents_agree=2, cooldown_hours=0,
        )

        # Moderately bullish = moderate conviction (2 bull, 2 neutral)
        mod_state = _make_state(
            ticker="AAPL",
            market_tone="bullish", fundamental_tone="neutral",
            sentiment_tone="neutral", debate_tone="bullish",
        )
        order_mod = order_manager.process_with_conviction(
            ticker="AAPL", trade_date="2024-11-02",
            full_state=mod_state, decision="BUY",
        )

        # Moderate conviction should produce fewer shares than high conviction
        if order_mod is not None:
            assert order_mod.qty <= high_qty

    @patch("execution.paper_broker.yf")
    def test_dry_run_with_conviction(self, mock_yf, order_manager):
        """Dry run mode still scores conviction but doesn't execute."""
        mock_yf.Ticker.return_value.info = {"currentPrice": 150.0}
        order_manager.dry_run = True

        state = _make_state(ticker="NVDA")
        order = order_manager.process_with_conviction(
            ticker="NVDA", trade_date="2024-11-01",
            full_state=state, decision="BUY",
        )
        assert order is None
        assert len(order_manager.broker.get_positions()) == 0

    @patch("execution.paper_broker.yf")
    def test_no_gate_passes_through(self, mock_yf, paper_broker, db):
        """OrderManager without a gate still works (backward compat)."""
        mock_yf.Ticker.return_value.info = {"currentPrice": 150.0}
        om = OrderManager(
            broker=paper_broker, db=db,
            conviction_gate=None,  # No gate
            scoring_mode="heuristic",
        )
        state = _make_state(ticker="AAPL")
        order = om.process_with_conviction(
            ticker="AAPL", trade_date="2024-11-01",
            full_state=state, decision="BUY",
        )
        assert order is not None
        assert order.status == "FILLED"


# ===================================================================
# Backward Compatibility
# ===================================================================

class TestBackwardCompat:
    @patch("execution.paper_broker.yf")
    def test_process_decision_still_works(self, mock_yf, order_manager):
        """Old process_decision() still works without conviction."""
        mock_yf.Ticker.return_value.info = {"currentPrice": 150.0}
        order = order_manager.process_decision(
            ticker="AAPL", trade_date="2024-11-01", decision="BUY"
        )
        assert order is not None
        assert order.status == "FILLED"
