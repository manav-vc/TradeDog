"""
Conviction Scorer for TradeDog.

Extracts per-agent conviction signals from the existing propagate() state
WITHOUT modifying any tradingagents/ code.  Two scoring modes:

  - "heuristic" (default): Fast keyword/regex analysis. Free, no LLM call.
  - "llm": One lightweight LLM call to score all reports. More accurate.

Returns an AgentSignals dataclass with per-agent scores (-1.0 to +1.0)
and a weighted composite conviction score (0-100).
"""

import json
import re
from dataclasses import dataclass, field
from typing import Optional

# ── Weights from design_reference.md ──────────────────────────────────
CONVICTION_WEIGHTS = {
    "technical": 0.25,     # market_report
    "fundamental": 0.25,   # fundamentals_report
    "sentiment": 0.20,     # sentiment_report + news_report
    "bull_bear": 0.30,     # investment_debate_state outcome
}

# ── Keyword lists for heuristic scoring ───────────────────────────────
_BULLISH_KEYWORDS = [
    "bullish", "strong buy", "buy", "outperform", "overweight",
    "upside", "growth", "positive", "beat expectations", "exceeded",
    "momentum", "breakout", "uptrend", "accumulate", "opportunity",
    "strong earnings", "revenue growth", "profit growth", "beat estimates",
    "golden cross", "support level", "higher highs",
]

_BEARISH_KEYWORDS = [
    "bearish", "strong sell", "sell", "underperform", "underweight",
    "downside", "decline", "negative", "missed expectations", "below",
    "weakness", "breakdown", "downtrend", "distribute", "risk",
    "poor earnings", "revenue decline", "profit decline", "missed estimates",
    "death cross", "resistance level", "lower lows", "overvalued",
]

_NEUTRAL_KEYWORDS = [
    "hold", "neutral", "mixed", "sideways", "consolidat", "range-bound",
    "wait", "unclear", "uncertain",
]


@dataclass
class AgentSignal:
    """Signal from a single agent dimension."""
    name: str
    signal: str = "HOLD"            # "BUY", "SELL", "HOLD"
    score: float = 0.0              # -1.0 (strong sell) to +1.0 (strong buy)
    key_reason: str = ""


@dataclass
class ConvictionResult:
    """Full conviction scoring result."""
    ticker: str = ""
    composite_score: float = 0.0    # 0–100 scale
    raw_score: float = 0.0          # -1.0 to +1.0 (weighted sum)
    decision: str = "HOLD"          # "BUY", "SELL", "HOLD"
    signals: list = field(default_factory=list)
    agents_agreeing_buy: int = 0
    agents_agreeing_sell: int = 0
    scoring_mode: str = "heuristic"

    @property
    def agents_agree_count(self) -> int:
        """Number of agents agreeing with the final decision direction."""
        if self.decision == "BUY":
            return self.agents_agreeing_buy
        elif self.decision == "SELL":
            return self.agents_agreeing_sell
        return 0


def score_from_state(
    state: dict,
    mode: str = "heuristic",
    llm=None,
) -> ConvictionResult:
    """
    Score conviction from an existing propagate() state dict.

    Args:
        state: The full state dict returned by propagate()
        mode: "heuristic" (free) or "llm" (one API call)
        llm: LLM instance for "llm" mode (langchain ChatModel)

    Returns:
        ConvictionResult with per-agent signals and composite score
    """
    if mode == "llm" and llm is not None:
        return _score_llm(state, llm)
    return _score_heuristic(state)


# ===================================================================
# Heuristic Scoring (free, no LLM)
# ===================================================================

def _score_heuristic(state: dict) -> ConvictionResult:
    """Score conviction using keyword frequency analysis."""
    ticker = state.get("company_of_interest", "")

    # Score each dimension
    technical = _score_text(
        state.get("market_report", ""),
        "technical",
    )
    fundamental = _score_text(
        state.get("fundamentals_report", ""),
        "fundamental",
    )

    # Sentiment combines social media + news
    sentiment_text = (
        state.get("sentiment_report", "") + "\n" +
        state.get("news_report", "")
    )
    sentiment = _score_text(sentiment_text, "sentiment")

    # Bull/bear from the debate outcome and investment plan
    debate_state = state.get("investment_debate_state", {})
    debate_text = (
        str(debate_state.get("judge_decision", "")) + "\n" +
        state.get("investment_plan", "") + "\n" +
        state.get("final_trade_decision", "")
    )
    bull_bear = _score_text(debate_text, "bull_bear")

    signals = [technical, fundamental, sentiment, bull_bear]

    # Weighted composite
    raw = (
        technical.score * CONVICTION_WEIGHTS["technical"] +
        fundamental.score * CONVICTION_WEIGHTS["fundamental"] +
        sentiment.score * CONVICTION_WEIGHTS["sentiment"] +
        bull_bear.score * CONVICTION_WEIGHTS["bull_bear"]
    )

    # Convert -1..+1 to 0..100
    composite = (raw + 1.0) / 2.0 * 100.0
    composite = max(0.0, min(100.0, composite))

    # Determine overall decision
    buy_count = sum(1 for s in signals if s.signal == "BUY")
    sell_count = sum(1 for s in signals if s.signal == "SELL")

    if raw > 0.1:
        decision = "BUY"
    elif raw < -0.1:
        decision = "SELL"
    else:
        decision = "HOLD"

    return ConvictionResult(
        ticker=ticker,
        composite_score=round(composite, 1),
        raw_score=round(raw, 3),
        decision=decision,
        signals=signals,
        agents_agreeing_buy=buy_count,
        agents_agreeing_sell=sell_count,
        scoring_mode="heuristic",
    )


def _score_text(text: str, name: str) -> AgentSignal:
    """Score a block of text using keyword frequency."""
    text_lower = text.lower()

    bull_hits = sum(1 for kw in _BULLISH_KEYWORDS if kw in text_lower)
    bear_hits = sum(1 for kw in _BEARISH_KEYWORDS if kw in text_lower)
    neutral_hits = sum(1 for kw in _NEUTRAL_KEYWORDS if kw in text_lower)

    total = bull_hits + bear_hits + neutral_hits
    if total == 0:
        return AgentSignal(name=name, signal="HOLD", score=0.0, key_reason="No signal keywords found")

    # Net score: positive = bullish, negative = bearish
    net = bull_hits - bear_hits
    # Normalize to -1..+1 range
    score = max(-1.0, min(1.0, net / max(total, 1)))

    if score > 0.15:
        signal = "BUY"
    elif score < -0.15:
        signal = "SELL"
    else:
        signal = "HOLD"

    key_reason = f"bull_keywords={bull_hits}, bear_keywords={bear_hits}, neutral={neutral_hits}"
    return AgentSignal(name=name, signal=signal, score=round(score, 3), key_reason=key_reason)


# ===================================================================
# LLM Scoring (one API call)
# ===================================================================

_LLM_SCORING_PROMPT = """You are a trading signal analyzer. Given the following analyst reports from a multi-agent trading system, extract a conviction score for each dimension.

REPORTS:
---
Technical (Market Report):
{market_report}
---
Fundamental Report:
{fundamentals_report}
---
Sentiment (Social + News):
{sentiment_report}
{news_report}
---
Debate Outcome & Final Decision:
{debate_text}
---

For each of the 4 dimensions, output a JSON object with:
- signal: "BUY", "SELL", or "HOLD"
- score: float from -1.0 (strong sell) to +1.0 (strong buy)
- key_reason: one sentence explaining why

Output ONLY valid JSON in this exact format, nothing else:
{{"technical": {{"signal": "...", "score": 0.0, "key_reason": "..."}}, "fundamental": {{"signal": "...", "score": 0.0, "key_reason": "..."}}, "sentiment": {{"signal": "...", "score": 0.0, "key_reason": "..."}}, "bull_bear": {{"signal": "...", "score": 0.0, "key_reason": "..."}}}}"""


def _score_llm(state: dict, llm) -> ConvictionResult:
    """Score conviction using one LLM call."""
    ticker = state.get("company_of_interest", "")

    debate_state = state.get("investment_debate_state", {})
    debate_text = (
        str(debate_state.get("judge_decision", "")) + "\n" +
        state.get("investment_plan", "") + "\n" +
        state.get("final_trade_decision", "")
    )

    prompt = _LLM_SCORING_PROMPT.format(
        market_report=state.get("market_report", "N/A")[:2000],
        fundamentals_report=state.get("fundamentals_report", "N/A")[:2000],
        sentiment_report=state.get("sentiment_report", "N/A")[:1500],
        news_report=state.get("news_report", "N/A")[:1500],
        debate_text=debate_text[:2000],
    )

    try:
        response = llm.invoke([("human", prompt)])
        content = response.content.strip()

        # Extract JSON from response (handle markdown code blocks)
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if not json_match:
            # Fallback to heuristic
            return _score_heuristic(state)

        parsed = json.loads(json_match.group())

        signals = []
        for dim_name in ["technical", "fundamental", "sentiment", "bull_bear"]:
            dim = parsed.get(dim_name, {})
            signals.append(AgentSignal(
                name=dim_name,
                signal=dim.get("signal", "HOLD"),
                score=max(-1.0, min(1.0, float(dim.get("score", 0.0)))),
                key_reason=dim.get("key_reason", ""),
            ))

        # Weighted composite
        raw = sum(
            s.score * CONVICTION_WEIGHTS[s.name]
            for s in signals
        )
        composite = (raw + 1.0) / 2.0 * 100.0
        composite = max(0.0, min(100.0, composite))

        buy_count = sum(1 for s in signals if s.signal == "BUY")
        sell_count = sum(1 for s in signals if s.signal == "SELL")

        if raw > 0.1:
            decision = "BUY"
        elif raw < -0.1:
            decision = "SELL"
        else:
            decision = "HOLD"

        return ConvictionResult(
            ticker=ticker,
            composite_score=round(composite, 1),
            raw_score=round(raw, 3),
            decision=decision,
            signals=signals,
            agents_agreeing_buy=buy_count,
            agents_agreeing_sell=sell_count,
            scoring_mode="llm",
        )

    except Exception:
        # LLM failed — fall back to heuristic
        return _score_heuristic(state)
