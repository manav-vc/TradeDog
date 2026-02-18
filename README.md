### Agentic AI system for stock trading
### TradeDog = LangGraph + Specialized Agents + Real-Time Loop + Paper Execution

TradingAgents (full name: TradingAgents: Multi-Agents LLM Financial Trading Framework) is currently one of the strongest and most popular open-source projects for exactly what you're building: an agentic AI system for stock trading powered by large language models (LLMs).
It was developed by Tauric Research (a group focused on AI for trading intelligence) and released openly on GitHub. As of February 2026, it has massive traction: ~30k stars, very active updates (v0.2.0 came out in early February 2026 with major improvements), an associated arXiv paper, and it's inspiring many forks/extensions (including ones that add real broker integrations like Alpaca).
What TradingAgents Actually Is
It's a multi-agent simulation of a real trading desk / hedge fund inside code. Instead of one single LLM deciding trades, it creates a team of specialized AI agents that work together, debate, and reach a consensus — just like analysts, traders, and risk managers in a professional firm.
Core agents/roles (as of v0.2.0):

Fundamentals Analyst — evaluates company financials, earnings, balance sheets, valuations (P/E, DCF, etc.)
Sentiment Analyst — processes news, social media (X/Twitter), Reddit, etc. for market mood
Technical Analyst — looks at charts, indicators (RSI, MACD, moving averages, volume patterns)
News / Researcher Agent — digs deeper into events, filings, macroeconomic data
Trader Agents — multiple versions with different risk appetites (conservative, aggressive, balanced)
Risk Manager — enforces position sizing, stop-loss rules, portfolio limits, drawdown controls

They communicate in a structured way (often via "debate" rounds or message passing), produce reasoning, and finally output a trading decision: BUY, SELL, HOLD + size + confidence.
Key strengths in 2026:

Supports many LLM providers out of the box: Grok, Claude 4, GPT-5 series, Gemini 3, OpenRouter, local Ollama — easy to switch or mix
Built on modern agent frameworks (likely LangGraph / similar graph-based orchestration)
Includes backtesting mode + simulation (paper trading style)
Focuses on explainability: every decision has detailed reasoning chain you can log/review

It's not a plug-and-play live trading bot yet — the base repo is more about analysis + decision-making in simulated or backtest environments. But it's designed to be extended with execution layers (which is perfect for your TradeDog plan).
How to Use It for Your TradeDog Project
Your goal = autonomous agent that invests → monitors → takes profits/exits automatically in paper trading mode.
TradingAgents is an excellent base because:

It already has the "brain" (multi-agent reasoning + decision)
You just need to add the "hands" (execution via Alpaca paper API) and "memory/guardian" (position tracking + profit/exit logic)



Step-by-step plan to turn it into TradeDog:

Clone and Run Baseline (Do This Today)Bashgit clone https://github.com/TauricResearch/TradingAgents.git
cd TradingAgents
# Follow their README — usually:
pip install -r requirements.txt
# Set up .env with your LLM API keys (e.g. GROQ_API_KEY or others)
# Run example / demo script (they have CLI + Jupyter examples)
python -m tradingagents run --ticker SHOP --mode backtest→ You should see agents debating SHOP (Shopify — great Toronto/TSX example) and outputting a simulated trade recommendation.
Add Your Profit-Taking / Exit Logic (Core TradeDog Feature)
Extend the framework by adding a new agent or post-processing node called ProfitGuard or ExitManager.
It runs after every decision loop:
Checks open positions (you'll need to add simple portfolio state — dict or SQLite)
Calculates current profit % , trailing high, days held
Forces SELL if: profit > 10–15%, trailing stop hit (e.g. -7% from peak), or reversal signal from technical/sentiment agents

Example pseudocode to integrate:Python# In the main loop or as new graph node
def exit_check(portfolio, current_data):
    for pos in portfolio:
        profit = (current_data[pos.ticker].price - pos.entry_price) / pos.entry_price
        if profit >= TAKE_PROFIT_PCT or trailing_stop_triggered(pos):
            return {"action": "SELL", "quantity": pos.quantity, "reason": f"Profit hit {profit*100:.1f}%"}
    return {"action": "HOLD"}

Connect to Paper Trading Execution
Use Alpaca (easiest for paper mode):
Sign up → get paper keys → install alpaca-py
Add an Executor module that turns agent decisions into real API calls:Pythonfrom alpaca.trading.client import TradingClient
client = TradingClient(API_KEY, SECRET_KEY, paper=True)
# When agents decide BUY 10 shares SHOP
client.submit_order(symbol="SHOP", qty=10, side="buy", type="market")

Start with US-listed or cross-listed TSX names (SHOP, TD, etc.). Later add IBKR for full TSX if needed.

Make It Run Autonomously (24/7-ish)
Wrap in a loop with schedule lib or asyncio: run full agent cycle every 15–60 min during market hours.
Add logging: save every decision + reasoning + P&L to file/DB.
Build a simple Streamlit dashboard: current portfolio, equity curve, recent agent debates.

Iteration Path for Serious Profits in Paper Mode
Week 1–2: Get baseline running + add basic exit rules → paper test on 5–10 stocks
Week 3–4: Tune prompts (make agents more conservative/profitable), add X sentiment via tools
Month 2: Track metrics (Sharpe, win rate, max drawdown) vs SPY/TSX benchmark
Month 3+: A/B test configs (different take-profit %, risk levels), add more data sources










