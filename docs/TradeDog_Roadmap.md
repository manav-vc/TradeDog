# TradeDog — Solo Developer Roadmap
### From Research Framework to Autonomous Trading Platform
*Built on TauricResearch/TradingAgents + LangGraph | NYSE + NASDAQ | Long-Only*

---

## What You Already Have

Your fork already gives you the core intelligence layer:

| Agent | Role | Status |
|---|---|---|
| Fundamentals Analyst | Financials, earnings, insider data | ✅ Built |
| Sentiment Analyst | Reddit/Twitter mood scoring | ✅ Built |
| News Analyst | Macro/event impact | ✅ Built |
| Technical Analyst | Indicators, patterns | ✅ Built |
| Bull/Bear Researcher | Debate-based conviction | ✅ Built |
| Trader Agent | Decision synthesis | ✅ Built |
| Risk Manager | Exposure checks | ✅ Built |
| Fund Manager | Final approval | ✅ Built |

**What's missing:** Production-grade execution layer, auto-buy logic, exit/monitoring loop, position tracking, conviction scoring, and a dashboard to observe all of it.

---

## The Full Architecture Target

```
[Watchlist Scanner]
       ↓
[Research Pipeline] ← Fundamentals / Sentiment / News / Technical
       ↓
[Bull vs Bear Debate]
       ↓
[Trader → Risk Manager → Fund Manager]
       ↓
  Conviction Score
       ↓
[Auto-Buy Engine] ←→ [Broker API: Alpaca / IBKR]
       ↓
[Position Monitor — runs every N minutes]
  ├── Profit target hit → SELL
  ├── Trailing stop triggered → SELL
  ├── Reversal signal detected → SELL
  └── Time-based exit (optional)
       ↓
[Trade Logger + Dashboard]
```

---

## Phase Overview

| Phase | Focus | Duration | Deliverable |
|---|---|---|---|
| **0** | Codebase audit & cleanup | 1–2 weeks | Clean, documented fork |
| **1** | Data layer hardening + watchlist | 1–2 weeks | Reliable data, fallbacks, liquidity filters |
| **2** | Paper trading execution layer | 3–4 weeks | Auto-buy/sell in simulation |
| **3** | Conviction scoring + signal control | 2–3 weeks | Buy only when score crosses threshold |
| **4** | Position monitoring & auto-exit | 3–4 weeks | Trailing stops, profit targets, reversals |
| **5** | Portfolio-level risk controls | 2–3 weeks | Max positions, sector exposure, drawdown limits |
| **6** | Dashboard & observability | 2–3 weeks | Web UI showing live state |
| **7** | Live trading (gradual) | Ongoing | Real money, small size, scaled carefully |

**Total realistic timeline: 5–7 months** for a solo developer going at a sustainable pace.

---

## Phase 0 — Codebase Audit & Foundation
**Duration: 1–2 weeks**

### Goals
Understand every file before adding anything. Establish a clean base.

### Tasks

**Week 1 — Read and map everything**
- [ ] Read all files under `tradingagents/` top to bottom
- [ ] Draw a flow diagram showing how `TradingAgentsGraph.propagate()` calls each agent
- [ ] Document what each agent returns (format, fields, meaning)
- [ ] Identify where `FinnHub` API is called and what endpoints are used
- [ ] Identify what config options exist in `default_config.py`
- [ ] Run `main.py` and `test.py` and make sure they work from scratch in your environment
- [ ] Set up a `.env` file with all required keys (FinnHub, OpenAI, etc.)

**Week 2 — Clean and prepare**
- [ ] Add Python type hints and docstrings to any function that doesn't have them
- [ ] Create a `docs/architecture.md` with your flow diagram
- [ ] Create a `docs/agent_contracts.md` documenting each agent's input/output schema
- [ ] Set up `pytest` and write one smoke test per agent
- [ ] Set up a `dev` branch — all new work goes to `dev`, only tested code merges to `main`
- [ ] Add `logging` (not print) throughout using Python's `logging` module
- [ ] Pin all dependency versions in `requirements.txt`

### Key Files to Study
```
tradingagents/
├── graph/trading_graph.py    ← Main orchestrator, start here
├── agents/                   ← One file per agent role
├── dataflows/                ← Data fetching layer
└── default_config.py         ← All tunable knobs
```

### Decision Points
- Confirm which LLM provider you'll use for production (Claude Sonnet is cost-effective for the quick agents; use it for analysts, use a reasoning model for Trader/Risk Manager)
- Confirm your broker choice now — **Alpaca** is strongly recommended for paper trading (free paper API, full NYSE/NASDAQ coverage). IBKR is a solid alternative for live trading later.

---

## Phase 1 — Data Layer Hardening + Watchlist
**Duration: 1–2 weeks** *(shorter than originally planned — FinnHub already covers NYSE/NASDAQ well)*

### Goals
The framework already targets US stocks, so this phase is about making the data layer robust and production-grade rather than adding a new market. You want reliable, clean OHLCV and fundamental data before any money is on the line.

### Data Source Strategy

You're already in great shape here. Both FinnHub and yfinance have excellent US coverage.

| Source | Use Case | Cost | Notes |
|---|---|---|---|
| FinnHub | Real-time quotes, news, insider trades | Free tier | Already wired in |
| `yfinance` | OHLCV history, fundamentals fallback | Free | Add as secondary/fallback |
| Polygon.io | Higher-quality tick data if needed later | Paid | Skip for now |
| Alpha Vantage | Alternative fundamentals | Free tier | Keep as backup |

**Implementation Plan**
- [ ] Add `yfinance` as a fallback in `dataflows/` — if FinnHub returns empty or errors, fall through to yfinance
- [ ] Add rate limit handling and retry logic for FinnHub (it drops requests under load)
- [ ] Standardize the OHLCV return format into a `MarketData` dataclass used by all agents
- [ ] Add a data validation step — reject and log any ticker that returns incomplete data
- [ ] Cache responses to disk (pickle or SQLite) so a re-run doesn't re-hit the API

**Watchlist Setup**
Create a curated starting watchlist. Don't try to scan the whole NYSE — start focused:
```json
{
  "large_cap": ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "JPM", "UNH", "V", "MA"],
  "growth": ["CRWD", "SNOW", "NET", "DDOG", "SMCI", "ARM", "PLTR"],
  "value": ["BRK-B", "JNJ", "PG", "KO", "WMT", "HD", "MCD"],
  "financials": ["GS", "MS", "BAC", "C", "WFC"],
  "energy": ["XOM", "CVX", "COP", "SLB"]
}
```
This gives you ~36 quality tickers across sectors. Plenty to start.

**Market Hours & Scheduling**
- NYSE/NASDAQ: 9:30 AM – 4:00 PM ET
- Pre-market analysis run: 8:00–9:15 AM ET (agents analyze, build signals)
- Market open: execution window 9:30–10:30 AM ET (buy signals fire here)
- Monitoring loop: runs every 5 min during market hours
- After-hours: position review, log summary, prep next day's watchlist

**Liquidity Filter**
Only analyze stocks with sufficient liquidity to avoid slippage:
```python
MIN_AVG_DAILY_VOLUME = 1_000_000   # 1M shares/day minimum
MIN_MARKET_CAP = 2_000_000_000     # $2B market cap minimum
```

**Testing**
- [ ] Run full `propagate()` on 10 tickers across different sectors
- [ ] Verify clean data returns for all 10 — no empty fields, no NaN prices
- [ ] Simulate a FinnHub API failure and verify yfinance fallback activates
- [ ] Log API call counts per run so you can estimate monthly costs

---

## Phase 2 — Paper Trading Execution Layer
**Duration: 3–4 weeks**

### Goals
Connect the agent decision to an actual order. Use paper trading only. No real money yet. This is the most critical phase — get it right before going live.

### Broker Setup: Alpaca (Recommended Start)
Alpaca offers a free paper trading API with full NYSE and NASDAQ coverage. It's the best starting point for paper trading — no account minimums, clean REST API, and a Python SDK.

**Alpaca Setup**
```bash
pip install alpaca-trade-api
# or the newer:
pip install alpaca-py
```

**Architecture**

Create a new module: `tradingagents/execution/`

```
execution/
├── broker_interface.py    ← Abstract base class
├── alpaca_broker.py       ← Alpaca implementation
├── ibkr_broker.py         ← IBKR implementation (Phase 7)
├── paper_broker.py        ← Local simulation (no API needed)
└── order_manager.py       ← Order lifecycle tracking
```

**The Broker Interface (define this first)**
```python
class BrokerInterface:
    def place_market_buy(self, ticker: str, qty: int) -> Order: ...
    def place_market_sell(self, ticker: str, qty: int) -> Order: ...
    def get_positions(self) -> list[Position]: ...
    def get_account(self) -> AccountInfo: ...
    def cancel_order(self, order_id: str) -> bool: ...
```

Start with `PaperBroker` — a pure Python simulation that tracks positions in a local SQLite database. This lets you test the full loop without any API.

**Tasks**
- [ ] Build `PaperBroker` with SQLite backend first
- [ ] Create `Position` and `Order` dataclasses
- [ ] Wire the Fund Manager agent's approval → `BrokerInterface.place_market_buy()`
- [ ] Test: run `propagate()` on AAPL and NVDA, confirm they create position records
- [ ] Add position sizing logic (see Phase 3)
- [ ] Build `AlpacaBroker` implementing the same interface
- [ ] Switch config to use `AlpacaBroker` with paper credentials
- [ ] Run 10 paper trades end-to-end, inspect results

**Position Size Formula (start simple)**
```python
def calculate_position_size(account_value, conviction_score, price, max_position_pct=0.05):
    # Never risk more than 5% of account on one trade
    max_dollars = account_value * max_position_pct
    # Scale by conviction (0.0 to 1.0)
    dollars_to_invest = max_dollars * conviction_score
    shares = int(dollars_to_invest / price)
    return max(1, shares)
```

---

## Phase 3 — Conviction Scoring & Auto-Buy Control
**Duration: 2–3 weeks**

### Goals
Not every agent decision should trigger a buy. Add a conviction score system so the platform only buys when multiple agents agree strongly.

### Conviction Score Design

The agents currently produce a BUY/SELL/HOLD decision with rationale. Extend this to also produce a **conviction score (0–100)**.

**Scoring Approach — Weight Each Agent's Input**
```python
CONVICTION_WEIGHTS = {
    "technical":    0.25,   # RSI, MACD, moving average signals
    "fundamental":  0.25,   # P/E, growth, financial health
    "sentiment":    0.20,   # Social/news mood
    "bull_bear":    0.30,   # Debate outcome (most decisive)
}

def calculate_conviction(agent_signals: dict) -> float:
    score = 0
    for agent, weight in CONVICTION_WEIGHTS.items():
        # Each agent returns -1.0 (strong sell) to +1.0 (strong buy)
        score += agent_signals[agent] * weight
    return score  # -1.0 to +1.0
```

**Auto-Buy Rules**
```python
BUY_THRESHOLD = 0.65      # Must have >65% conviction to buy
MIN_AGENTS_AGREE = 3      # At least 3 of 4 analyst agents must agree direction
MAX_POSITIONS = 10        # Never hold more than 10 stocks at once
COOLDOWN_HOURS = 24       # Don't re-analyze same stock within 24h of last trade
```

### Tasks
- [ ] Add `conviction_score: float` to the `TradingAgentsGraph` output
- [ ] Prompt each analyst agent to return a numerical score alongside their text analysis
- [ ] Build `ConvictionGate` — checks all rules before passing to execution
- [ ] Add a `signal_log` database table: ticker, timestamp, conviction score, decision, action taken
- [ ] Test: force a high-conviction scenario and verify buy fires; force low and verify it doesn't
- [ ] Add a dry-run mode flag: logs what would have happened without executing

**Prompt Addition for Each Analyst Agent**
Add to each analyst's system prompt:
```
At the end of your analysis, always output a JSON block:
{"signal": "BUY|SELL|HOLD", "conviction": 0.85, "key_reason": "..."}
```
Then parse this structured output in the graph state.

---

## Phase 4 — Position Monitoring & Auto-Exit
**Duration: 3–4 weeks**

### Goals
This is the "profit guard" layer. Once a position is open, a separate monitoring loop checks it every N minutes and auto-exits based on your rules.

### Exit Conditions

| Condition | Rule | Notes |
|---|---|---|
| Profit target | Exit when gain ≥ 15% | Hard target |
| Trailing stop | Exit when price drops 7% from highest point reached | Locks in gains |
| Stop loss | Exit when loss ≥ 8% from entry | Hard floor |
| Reversal signal | Exit when Technical Agent says strong SELL | Agent-driven exit |
| Time-based | Exit after 30 days if none of above triggered | Prevents zombie positions |

### Architecture

Create `tradingagents/monitoring/`
```
monitoring/
├── position_monitor.py    ← Main loop
├── exit_rules.py          ← All exit condition logic
├── price_feed.py          ← Real-time price fetching
└── alert_manager.py       ← Notifications
```

**The Monitor Loop**
```python
async def monitor_loop(interval_seconds=300):  # Check every 5 min
    while True:
        positions = broker.get_positions()
        for position in positions:
            current_price = price_feed.get_price(position.ticker)
            exit_rule = exit_rules.check(position, current_price)
            if exit_rule.should_exit:
                broker.place_market_sell(position.ticker, position.qty)
                log_exit(position, exit_rule.reason)
        await asyncio.sleep(interval_seconds)
```

**Trailing Stop Implementation**
```python
class Position:
    ticker: str
    entry_price: float
    qty: int
    highest_price: float       # Track this, update every check
    entry_time: datetime

def check_trailing_stop(position, current_price, trail_pct=0.07):
    # Update high-water mark
    if current_price > position.highest_price:
        position.highest_price = current_price
        # Save updated high to DB
    
    # Check if we've fallen trail_pct% from the peak
    trail_level = position.highest_price * (1 - trail_pct)
    if current_price <= trail_level:
        return ExitSignal(should_exit=True, reason="TRAILING_STOP")
    return ExitSignal(should_exit=False)
```

**Tasks**
- [ ] Create `positions` database table with all needed fields including `highest_price`
- [ ] Build `PriceFeed` class — uses yfinance for near-real-time quotes (15-min delay is fine for daily swing trades)
- [ ] Implement each exit rule as a separate function
- [ ] Build monitor loop as an `asyncio` task running in background
- [ ] Add reversal detection: re-run just the Technical Analyst on existing positions (not the full pipeline — too expensive)
- [ ] Wire alerts: when exit fires, log + send yourself a Telegram message (easy to set up)
- [ ] Test each exit rule in isolation with mocked prices
- [ ] Run paper trading for 2 weeks, verify exits fire correctly

**Reversal Detection (Cost-Effective Approach)**
Don't run the full 7-agent pipeline for monitoring. Instead:
```python
async def check_reversal(position):
    # Only run Technical Analyst (fast, cheap, no LLM needed for basic signals)
    tech_signal = technical_agent.quick_check(position.ticker)
    if tech_signal.rsi > 75 and tech_signal.macd_cross == "BEARISH":
        return ExitSignal(should_exit=True, reason="REVERSAL_SIGNAL")
```

---

## Phase 5 — Portfolio-Level Risk Controls
**Duration: 2–3 weeks**

### Goals
Protect the whole portfolio, not just individual positions.

### Rules to Implement

**Hard Limits**
```python
PORTFOLIO_RULES = {
    "max_positions": 10,              # Never hold more than 10 stocks
    "max_sector_exposure": 0.30,      # No single sector > 30% of portfolio
    "max_single_position": 0.08,      # No single stock > 8% of portfolio
    "max_single_exchange": 0.60,       # No more than 60% in NYSE or NASDAQ alone
    "daily_loss_limit": -0.03,        # Stop all buys if down 3% on the day
    "weekly_loss_limit": -0.07,       # Stop all activity if down 7% in a week
    "cash_reserve": 0.10,             # Always keep 10% cash
}
```

**The Portfolio Guard**
```python
class PortfolioGuard:
    def can_open_position(self, ticker, proposed_size) -> tuple[bool, str]:
        # Check each rule before allowing a new buy
        checks = [
            self._check_max_positions(),
            self._check_sector_exposure(ticker),
            self._check_daily_loss(),
            self._check_cash_reserve(proposed_size),
        ]
        failures = [r for r in checks if not r.passed]
        if failures:
            return False, failures[0].reason
        return True, "OK"
```

**Tasks**
- [ ] Build sector classification map for your watchlist tickers
- [ ] Implement `PortfolioGuard` with each rule as a method
- [ ] Insert `PortfolioGuard.can_open_position()` check between Fund Manager approval and order execution
- [ ] Add daily P&L tracking to the database
- [ ] Test: create a scenario where 10 positions are open and verify 11th is blocked
- [ ] Test: simulate a 3% daily loss and verify no new buys are attempted
- [ ] Create a `portfolio_summary()` function for the dashboard

---

## Phase 6 — Dashboard & Observability
**Duration: 2–3 weeks**

### Goals
You need to see what's happening in real time. As a solo dev, a simple web dashboard is far more practical than debugging log files.

### Stack Recommendation
Use **Streamlit** — it's Python-native, fast to build, and perfect for internal tools.

```bash
pip install streamlit plotly pandas
```

**Dashboard Pages**

*Page 1: Portfolio Overview*
- Current positions table (ticker, entry price, current price, P&L%, trailing stop level)
- Total portfolio value + daily P&L
- Cash available
- Sector exposure chart

*Page 2: Signal Feed*
- Live log of agent decisions (last 50)
- Conviction scores with color coding (green = strong buy, yellow = weak, gray = hold)
- Pending signals not yet executed

*Page 3: Trade History*
- All closed trades with entry/exit/reason/profit
- Win rate, average return, best/worst trade
- Monthly return chart

*Page 4: Agent Monitor*
- Which tickers were analyzed today
- Agent breakdown per analysis (which agents said BUY vs SELL)
- API costs tracker (LLM call count × estimated cost)

**Tasks**
- [ ] Set up Streamlit app at `dashboard/app.py`
- [ ] Connect to your SQLite database (or Postgres if you've upgraded)
- [ ] Build each page using `st.dataframe`, `st.metric`, and Plotly charts
- [ ] Add auto-refresh every 60 seconds (`st.rerun()` with `time.sleep`)
- [ ] Deploy locally (you don't need to expose this to the internet — just run it on your machine)
- [ ] Add a simple "pause trading" toggle that sets a flag in the DB (monitor loop respects it)

---

## Phase 7 — Live Trading (Gradual Rollout)
**Duration: Ongoing — never rush this**

### The Graduation Criteria
Before touching real money, you must have:
- [ ] 60+ consecutive days of paper trading with no critical bugs
- [ ] All exit rules verified to have fired correctly at least 5 times each
- [ ] Portfolio guard rules verified under stress scenarios
- [ ] Trade log showing positive expectancy (avg win > avg loss)
- [ ] Manual review of every paper trade's entry/exit reasoning

### Go-Live Steps

**Week 1 with real money: $2,000 max**
- Deploy to Alpaca live account (or IBKR if you prefer)
- Max 2 positions open at once
- Position size: $200–$300 max per trade
- Monitor manually every hour during market hours

**Month 2: Scale to $10,000**
- Only if Week 1 had no execution errors
- Increase to 5 max positions
- Begin trusting the monitor loop for exits

**Month 3+: Full operation**
- Increase to your target capital
- Weekly review of agent decisions vs outcomes
- Monthly recalibration of conviction thresholds

### Broker Setup for Live NYSE/NASDAQ Trading

**Alpaca** is the easiest path for NYSE/NASDAQ live trading. IBKR is a solid alternative with more order types:
```bash
# Alpaca live trading (same SDK as paper, just swap credentials)
pip install alpaca-py
```

For IBKR:
```bash
pip install ib_insync
# Requires IBKR TWS or Gateway running locally
```

Build `IBKRBroker` implementing the same `BrokerInterface` from Phase 2. Switching brokers is just a config change — the rest of the system doesn't care.

### US Regulatory Note
For personal automated trading in a US brokerage account, you're operating under standard retail trading rules. If you make more than 3 day trades in a 5-day rolling window with under $25,000 in the account, you'll trigger Pattern Day Trader rules. Since TradeDog is a swing trading system (holding for days to weeks), this typically isn't an issue — but keep it in mind when sizing up.

---

## Database Schema

Use **SQLite** to start (zero infrastructure). Migrate to Postgres later if needed.

```sql
-- All positions (open and closed)
CREATE TABLE positions (
    id INTEGER PRIMARY KEY,
    ticker TEXT NOT NULL,
    exchange TEXT NOT NULL,        -- 'NYSE' or 'NASDAQ'
    entry_price REAL NOT NULL,
    entry_time DATETIME NOT NULL,
    qty INTEGER NOT NULL,
    highest_price REAL,            -- For trailing stop
    exit_price REAL,
    exit_time DATETIME,
    exit_reason TEXT,              -- 'PROFIT_TARGET', 'TRAILING_STOP', etc.
    status TEXT DEFAULT 'OPEN'     -- 'OPEN' or 'CLOSED'
);

-- All agent signals (for analysis and debugging)
CREATE TABLE signals (
    id INTEGER PRIMARY KEY,
    ticker TEXT NOT NULL,
    timestamp DATETIME NOT NULL,
    conviction_score REAL,
    agent_decision TEXT,           -- JSON of each agent's output
    action_taken TEXT,             -- 'BOUGHT', 'SKIPPED', 'REJECTED_BY_GUARD'
    skip_reason TEXT
);

-- Daily portfolio snapshots
CREATE TABLE portfolio_snapshots (
    id INTEGER PRIMARY KEY,
    snapshot_date DATE NOT NULL,
    total_value REAL,
    cash REAL,
    num_positions INTEGER,
    daily_pnl REAL,
    daily_pnl_pct REAL
);

-- System events and errors
CREATE TABLE system_log (
    id INTEGER PRIMARY KEY,
    timestamp DATETIME NOT NULL,
    level TEXT,                    -- 'INFO', 'WARNING', 'ERROR'
    component TEXT,
    message TEXT
);
```

---

## File Structure (Target State)

```
TradeDog/
├── tradingagents/              ← Upstream framework (minimal changes)
│   ├── agents/
│   ├── dataflows/
│   │   ├── yfinance_fallback.py ← NEW: Fallback when FinnHub fails
│   │   └── data_validator.py   ← NEW: Validates data quality
│   ├── graph/trading_graph.py
│   └── default_config.py
│
├── execution/                  ← NEW: Order execution
│   ├── broker_interface.py
│   ├── paper_broker.py
│   ├── alpaca_broker.py
│   ├── ibkr_broker.py
│   └── order_manager.py
│
├── monitoring/                 ← NEW: Position monitoring
│   ├── position_monitor.py
│   ├── exit_rules.py
│   ├── price_feed.py
│   └── alert_manager.py
│
├── portfolio/                  ← NEW: Risk management
│   ├── portfolio_guard.py
│   ├── conviction_gate.py
│   └── position_sizer.py
│
├── database/                   ← NEW: Data persistence
│   ├── schema.sql
│   ├── db.py
│   └── models.py
│
├── dashboard/                  ← NEW: Streamlit UI
│   └── app.py
│
├── watchlist/                  ← NEW: Curated tickers
│   ├── watchlist.json          ← NYSE/NASDAQ curated tickers
│   └── sector_map.json         ← Ticker → sector classification
│
├── scheduler/                  ← NEW: Orchestrates daily run
│   └── main_loop.py
│
├── tests/
│   └── ...
│
├── docs/
│   ├── architecture.md
│   └── agent_contracts.md
│
├── .env
├── main.py
└── requirements.txt
```

---

## Cost Estimate (Monthly)

| Item | Cost |
|---|---|
| LLM API (Claude Sonnet for analysts, Opus for Trader/Risk) | ~$30–80/mo |
| FinnHub free tier | $0 |
| yfinance | $0 |
| Alpaca paper trading | $0 |
| IBKR live account | $0 (no monthly fee) |
| Hosting (run on your laptop) | $0 |

Keep costs low by: analyzing each ticker once per day (not per minute), using Claude Haiku for the analyst agents, and only using a more powerful model for the final Trader and Risk Manager decisions.

---

## Weekly Rhythm for a Solo Developer

**Every week:**
- Monday: Review last week's signal log — did the agents call it right?
- Tuesday–Thursday: Build next feature from the roadmap
- Friday: Write tests, review paper trades, update docs

**Every month:**
- Recalibrate conviction thresholds based on data
- Review which agents are adding value vs noise
- Upgrade watchlist based on what's been performing

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| LLM hallucination drives a bad trade | Conviction gate + portfolio guard as hard stops |
| API outage during market hours | Retry logic + fallback to cached data |
| Broker API failure | Always log intent before execution; reconcile on startup |
| Runaway losses | Daily loss limit halts all activity automatically |
| Overfitting to paper trading | Paper trade on different time periods before going live |
| Low liquidity stocks | Volume filter on watchlist (>1M shares/day avg) |

---

*This roadmap is designed to be completed one phase at a time. Finish each phase completely before starting the next. The order matters — don't skip to execution before the data layer is solid.*
