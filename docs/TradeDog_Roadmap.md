# TradeDog — Personal Autonomous Trading Platform Roadmap

**From Research Framework to a Self-Running Trading System for One**
*Built on TauricResearch/TradingAgents + LangGraph | NYSE + NASDAQ | Long-Only | Single User*

For code snippets, schemas, and architecture patterns see [design_reference.md](design_reference.md).

> **Scope decision (v2):** This platform is built for personal use only — one user, one brokerage account, runs locally. No auth, no KYC, no payment rails, no RIA registration required. The dashboard IS the product.

> **Build-on-top rule:** The existing TradingAgents framework (`tradingagents/`) is treated as a dependency — never modify its code. All new functionality is built in separate modules that import from and wrap the framework. This keeps the upstream codebase clean and makes it easy to pull in future updates.

---

## What You Already Have

| Component               | Role                                         |
| ----------------------- | -------------------------------------------- |
| Fundamentals Analyst    | Financials, earnings, insider data           |
| Sentiment Analyst       | Reddit/Twitter mood scoring                  |
| News Analyst            | Macro/event impact                           |
| Technical Analyst       | Indicators, patterns                         |
| Bull/Bear Researcher    | Debate-based conviction                      |
| Trader Agent            | Decision synthesis                           |
| Risk Manager            | Exposure checks                              |
| Fund Manager            | Final approval                               |
| LangGraph orchestration | Multi-agent pipeline with state management   |
| Multi-LLM support       | OpenAI, Google, Anthropic, XAI, OpenRouter   |
| Data layer              | yfinance + Alpha Vantage with fallback/cache |
| Rich CLI                | Interactive terminal UI with live output     |
| Docs & flow diagram     | Architecture docs, propagation flow diagram  |
| Memory system           | FinancialSituationMemory for learning        |

**What's missing:** Tests, execution layer, auto-buy logic, exit/monitoring loop, position tracking, conviction scoring, risk config UI, dashboard, and notifications.

**Not in scope:** Modifying anything inside `tradingagents/`. The framework's data layer, logging, and dependencies are used as-is. All new work is built on top.

---

# Milestone v0.1 — Minimal End-to-End Loop

*Manual trigger, single ticker analysis through to a paper trade execution.*

---

## Phase 0 — Codebase Audit and Foundation

**Goal:** Understand every file before adding anything. Establish a clean, documented, testable base.

### Week 1 — Read and Map

| #   | Task                                                                         | Files                                                                        |
| --- | ---------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| 0.1 | Read all files under `tradingagents/` top to bottom                          | `tradingagents/**`                                                           |
| 0.2 | Draw a flow diagram of how `TradingAgentsGraph.propagate()` calls each agent | `docs/propagate_flow_diagram.html`                                           |
| 0.4 | Map all data API calls and endpoints used across dataflows                   | `docs/design_reference.md`                                                   |
| 0.5 | Review all config options in `default_config.py`                             | `tradingagents/default_config.py`                                            |
| 0.6 | Run `main.py` and `test.py` end-to-end in your environment                   | `main.py`, `test.py`                                                         |
| 0.7 | Set up `.env` with all required API keys                                     | `.env`, `.env.example`                                                       |

### Week 2 — Clean and Prepare

| #    | Task                                                                             | Files                                       |
| ---- | -------------------------------------------------------------------------------- | ------------------------------------------- |
| 0.10 | Create `dev` branch — all new work goes there, only tested code merges to `main` | git                                         |

### Decision Points (resolve before moving on)

- Choose LLM provider for production (recommendation: Claude Sonnet for analysts, reasoning model for Trader/Risk Manager)
- Choose broker for paper trading (recommendation: Alpaca — free paper API, full NYSE/NASDAQ, fractional shares)

---

## Phase 1 — Watchlist + Integration Tests

**Goal:** Build the watchlist module and validate that the existing data layer works reliably across tickers. No changes to `tradingagents/` — just build on top and test what's already there.

**Prereqs:** Phase 0 complete

| #   | Task                                                                                        | Files                            |
| --- | ------------------------------------------------------------------------------------------- | -------------------------------- |
| 1.1 | Create `watchlist/watchlist.json` with starter tickers (~36 across sectors)                 | `watchlist/watchlist.json` (new) |
| 1.2 | Implement liquidity filter (min volume + min market cap)                                    | `watchlist/filters.py` (new)     |
| 1.3 | Test: run `propagate()` on 10 tickers, verify clean data with no empty fields or NaN prices | `tests/test_data_layer.py` (new) |
| 1.4 | Test: simulate API failure and verify existing fallback behavior                            | `tests/test_data_layer.py`       |

See [design_reference.md — Watchlist Design](design_reference.md#watchlist-design) for details.

---

## Phase 2 — Paper Trading Execution Layer

**Goal:** Connect the agent decision to an actual order. Paper trading only, no real money. This is the most critical phase.

**Prereqs:** Phase 1 complete

| #    | Task                                                                            | Files                                                                      |
| ---- | ------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| 2.1  | Create `database/schema.sql` and `database/db.py` with SQLite setup             | `database/schema.sql`, `database/db.py` (new)                              |
| 2.2  | Create `database/models.py` with `Position`, `Order`, `AccountInfo` dataclasses | `database/models.py` (new)                                                 |
| 2.3  | Define `BrokerInterface` abstract base class                                    | `execution/broker_interface.py` (new)                                      |
| 2.4  | Build `PaperBroker` implementing `BrokerInterface` with SQLite backend          | `execution/paper_broker.py` (new)                                          |
| 2.5  | Wire Fund Manager agent approval to `BrokerInterface.place_market_buy()`        | `tradingagents/graph/trading_graph.py`, `execution/order_manager.py` (new) |
| 2.6  | Test: run `propagate()` on AAPL and NVDA, confirm position records are created  | `tests/test_execution.py` (new)                                            |
| 2.7  | Add position sizing logic (use formula from design ref)                         | `portfolio/position_sizer.py` (new)                                        |
| 2.8  | Build `AlpacaBroker` implementing `BrokerInterface`                             | `execution/alpaca_broker.py` (new)                                         |
| 2.9  | Add Alpaca paper credentials to `.env` and config                               | `.env`, `tradingagents/default_config.py`                                  |
| 2.10 | Switch config to use `AlpacaBroker` with paper mode                             | `tradingagents/default_config.py`                                          |
| 2.11 | Run 10 paper trades end-to-end, inspect results in DB                           | manual                                                                     |

See [design_reference.md — Execution Layer Architecture](design_reference.md#execution-layer-architecture), [Broker Interface](design_reference.md#broker-interface), [Position Sizing Formula](design_reference.md#position-sizing-formula), and [Database Schema](design_reference.md#database-schema) for implementation details.

---

# Milestone v0.2 — Conviction + Profit Guardian

*Only buy when confident. Auto-exit when conditions are met.*

---

## Phase 3 — Conviction Scoring and Auto-Buy Control

**Goal:** Not every agent decision should trigger a buy. Add conviction scoring so the platform only buys when multiple agents agree strongly.

**Prereqs:** Phase 2 complete

| #    | Task                                                                                 | Files                                                                                |
| ---- | ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------ |
| 3.1  | Add `conviction_score: float` field to `TradingAgentsGraph` output state             | `tradingagents/agents/utils/agent_states.py`, `tradingagents/graph/trading_graph.py` |
| 3.2  | Update each analyst agent prompt to return structured JSON with signal + conviction  | `tradingagents/agents/analysts/*.py`                                                 |
| 3.3  | Parse structured conviction output from each agent in the graph state                | `tradingagents/graph/signal_processing.py`                                           |
| 3.4  | Implement `calculate_conviction()` weighted scoring function                         | `portfolio/conviction_gate.py` (new)                                                 |
| 3.5  | Build `ConvictionGate` — checks threshold, min agents agree, cooldown, max positions | `portfolio/conviction_gate.py`                                                       |
| 3.6  | Add `signals` database table for logging all decisions                               | `database/schema.sql`, `database/db.py`                                              |
| 3.7  | Wire ConvictionGate between graph output and execution                               | `execution/order_manager.py`                                                         |
| 3.8  | Test: force high-conviction scenario, verify buy fires                               | `tests/test_conviction.py` (new)                                                     |
| 3.9  | Test: force low-conviction scenario, verify buy is blocked                           | `tests/test_conviction.py`                                                           |
| 3.10 | Add dry-run mode flag — logs what would have happened without executing              | `tradingagents/default_config.py`, `execution/order_manager.py`                      |

See [design_reference.md — Conviction Scoring Design](design_reference.md#conviction-scoring-design), [Auto-Buy Rules](design_reference.md#auto-buy-rules), and [Agent Prompt Additions](design_reference.md#agent-prompt-additions) for implementation details.

---

## Phase 4 — Position Monitoring and Auto-Exit

**Goal:** Once a position is open, a monitoring loop checks it on a schedule and auto-exits based on predefined rules (profit target, trailing stop, stop loss, reversal, time-based).

**Prereqs:** Phase 3 complete

| #    | Task                                                                                      | Files                                   |
| ---- | ----------------------------------------------------------------------------------------- | --------------------------------------- |
| 4.1  | Ensure `positions` table has `highest_price` column for trailing stop tracking            | `database/schema.sql`, `database/db.py` |
| 4.2  | Build `PriceFeed` class using yfinance for near-real-time quotes                          | `monitoring/price_feed.py` (new)        |
| 4.3  | Implement profit target exit rule (>= 15% gain)                                           | `monitoring/exit_rules.py` (new)        |
| 4.4  | Implement trailing stop exit rule (7% drop from peak)                                     | `monitoring/exit_rules.py`              |
| 4.5  | Implement stop loss exit rule (>= 8% loss from entry)                                     | `monitoring/exit_rules.py`              |
| 4.6  | Implement time-based exit rule (30 days max hold)                                         | `monitoring/exit_rules.py`              |
| 4.7  | Implement reversal detection using only Technical Analyst (lightweight, no full pipeline) | `monitoring/exit_rules.py`              |
| 4.8  | Build the async monitor loop — checks all positions every 5 min                           | `monitoring/position_monitor.py` (new)  |
| 4.9  | Wire exit signals to `broker.place_market_sell()` and log exit reason                     | `monitoring/position_monitor.py`        |
| 4.10 | Build alert manager — log exits + send Telegram notification                              | `monitoring/alert_manager.py` (new)     |
| 4.11 | Test each exit rule in isolation with mocked prices                                       | `tests/test_exit_rules.py` (new)        |
| 4.12 | Run paper trading for 2 weeks, verify exits fire correctly                                | manual                                  |

See [design_reference.md — Exit Conditions and Rules](design_reference.md#exit-conditions-and-rules), [Monitor Loop](design_reference.md#monitor-loop), [Trailing Stop Implementation](design_reference.md#trailing-stop-implementation), and [Reversal Detection](design_reference.md#reversal-detection) for implementation details.

---

# Milestone v0.3 — Portfolio Risk + Dashboard + Personal Controls

*Protect the whole portfolio. See what's happening. Control it without touching code.*

---

## Phase 5 — Portfolio-Level Risk Controls

**Goal:** Protect the portfolio as a whole, not just individual positions. Enforce hard limits on exposure, concentration, and drawdown.

**Prereqs:** Phase 4 complete

| #    | Task                                                                                          | Files                                   |
| ---- | --------------------------------------------------------------------------------------------- | --------------------------------------- |
| 5.1  | Create `watchlist/sector_map.json` mapping each ticker to its sector                          | `watchlist/sector_map.json` (new)       |
| 5.2  | Implement `PortfolioGuard` class with `can_open_position()` method                            | `portfolio/portfolio_guard.py` (new)    |
| 5.3  | Implement max positions check (10 max)                                                        | `portfolio/portfolio_guard.py`          |
| 5.4  | Implement sector exposure check (no sector > 30%)                                             | `portfolio/portfolio_guard.py`          |
| 5.5  | Implement single position size check (no stock > 8%)                                          | `portfolio/portfolio_guard.py`          |
| 5.6  | Implement daily loss limit (stop buys if down 3% on the day)                                  | `portfolio/portfolio_guard.py`          |
| 5.7  | Implement cash reserve check (always keep 10%)                                                | `portfolio/portfolio_guard.py`          |
| 5.8  | Insert `PortfolioGuard.can_open_position()` between Fund Manager approval and order execution | `execution/order_manager.py`            |
| 5.9  | Add `portfolio_snapshots` table for daily P&L tracking                                        | `database/schema.sql`, `database/db.py` |
| 5.10 | Create `portfolio_summary()` function (needed for dashboard)                                  | `portfolio/portfolio_guard.py`          |
| 5.11 | Test: 10 positions open, verify 11th is blocked                                               | `tests/test_portfolio_guard.py` (new)   |
| 5.12 | Test: simulate 3% daily loss, verify no new buys                                              | `tests/test_portfolio_guard.py`         |

See [design_reference.md — Portfolio Guard Design](design_reference.md#portfolio-guard-design) for implementation details.

---

## Phase 6 — Dashboard and Observability *(Your Main Interface)*

**Goal:** The Streamlit dashboard is not just a dev tool — for this personal platform, it IS the product. This is how you interact with your running trading system every day.

**Prereqs:** Phase 5 complete

| #    | Task                                                                                     | Files                                |
| ---- | ---------------------------------------------------------------------------------------- | ------------------------------------ |
| 6.1  | Install Streamlit, Plotly, Pandas dependencies                                           | `requirements.txt`                   |
| 6.2  | Set up Streamlit app shell with sidebar navigation                                       | `dashboard/app.py` (new)             |
| 6.3  | Connect app to SQLite database                                                           | `dashboard/app.py`                   |
| 6.4  | Build Page 1: Portfolio Overview (positions table, total value, daily P&L, sector chart) | `dashboard/app.py`                   |
| 6.5  | Build Page 2: Signal Feed (agent decisions log, conviction scores, pending signals)      | `dashboard/app.py`                   |
| 6.6  | Build Page 3: Trade History (closed trades, win rate, monthly returns chart)             | `dashboard/app.py`                   |
| 6.7  | Build Page 4: Agent Monitor (tickers analyzed, agent breakdown, API cost tracker)        | `dashboard/app.py`                   |
| 6.8  | Add auto-refresh every 60 seconds                                                        | `dashboard/app.py`                   |
| 6.9  | Add "pause trading" toggle that sets a flag in the DB                                    | `dashboard/app.py`, `database/db.py` |
| 6.10 | Test: run dashboard locally alongside paper trading loop                                 | manual                               |

See [design_reference.md — Dashboard Specs](design_reference.md#dashboard-specs) for page layouts.

---

## Phase 6B — Risk Config UI *(New — Personal Platform Addition)*

**Goal:** Replace manual edits to `default_config.py` with a dedicated settings page in the dashboard. You should be able to tune your risk parameters without touching code.

**Prereqs:** Phase 6 complete

| #     | Task                                                                                           | Files                                                          |
| ----- | ---------------------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| 6B.1  | Create `config/user_config.json` to store personal runtime parameters (separate from code)    | `config/user_config.json` (new)                                |
| 6B.2  | Build `ConfigManager` class to read/write `user_config.json`                                   | `config/config_manager.py` (new)                               |
| 6B.3  | Add Page 5 to dashboard: "Risk Settings"                                                       | `dashboard/app.py`                                             |
| 6B.4  | Add slider: **Capital deployed** — how much of Alpaca balance the engine is allowed to use     | `dashboard/app.py`                                             |
| 6B.5  | Add slider: **Max positions** — number of stocks to hold at once (range: 3–15)                 | `dashboard/app.py`                                             |
| 6B.6  | Add slider: **Risk per trade** — % of portfolio per position (range: 1–10%)                    | `dashboard/app.py`                                             |
| 6B.7  | Add slider: **Conviction threshold** — minimum score to trigger a buy (range: 50–90)           | `dashboard/app.py`                                             |
| 6B.8  | Add slider: **Daily loss limit** — halt all buys if portfolio drops X% (range: 1–10%)          | `dashboard/app.py`                                             |
| 6B.9  | Add slider: **Stop-loss width** — ATR multiples before auto-exit (range: 1.0–3.0×)             | `dashboard/app.py`                                             |
| 6B.10 | Add slider: **Profit target** — % gain before auto-exit (range: 5–30%)                         | `dashboard/app.py`                                             |
| 6B.11 | Wire all sliders to write to `user_config.json` on save                                        | `dashboard/app.py`, `config/config_manager.py`                 |
| 6B.12 | Wire engine to read from `user_config.json` at runtime instead of hardcoded config values      | `execution/order_manager.py`, `portfolio/conviction_gate.py`   |
| 6B.13 | Add "Reset to defaults" button                                                                 | `dashboard/app.py`                                             |
| 6B.14 | Test: change conviction threshold in UI, verify engine respects new value without restart      | manual                                                         |

---

## Phase 6C — Telegram Notifications *(New — Personal Platform Addition)*

**Goal:** Know what your engine is doing without staring at the dashboard. Telegram gives you mobile-first awareness of every meaningful event.

**Prereqs:** Phase 6 complete (Phase 6B optional but recommended first)

| #     | Task                                                                                            | Files                                  |
| ----- | ----------------------------------------------------------------------------------------------- | -------------------------------------- |
| 6C.1  | Create a Telegram bot via BotFather, store token in `.env`                                      | `.env`                                 |
| 6C.2  | Build `TelegramNotifier` class with `send_message()` method                                     | `notifications/telegram_notifier.py` (new) |
| 6C.3  | Define message templates for each alert type (see below)                                        | `notifications/templates.py` (new)    |
| 6C.4  | Alert: **Trade opened** — ticker, price, size, conviction score, key reason                     | `execution/order_manager.py`           |
| 6C.5  | Alert: **Trade closed** — ticker, entry/exit price, P&L ($), P&L (%), exit reason              | `monitoring/position_monitor.py`       |
| 6C.6  | Alert: **Portfolio guard triggered** — which rule fired, what was blocked                       | `portfolio/portfolio_guard.py`         |
| 6C.7  | Alert: **Daily loss limit hit** — current day P&L, engine now in halt mode                     | `portfolio/portfolio_guard.py`         |
| 6C.8  | Alert: **Engine error** — any unhandled exception in the main loop                              | `scheduler/main_loop.py`               |
| 6C.9  | Build **weekly digest** — Sunday 6pm ET: week's trades, win rate, portfolio value, best/worst  | `scheduler/main_loop.py`               |
| 6C.10 | Add notification toggles to the dashboard Settings page (which alerts to enable/disable)        | `dashboard/app.py`, `config/user_config.json` |
| 6C.11 | Test: trigger each alert type manually and confirm delivery on mobile                           | manual                                 |

### Alert Templates

```
🟢 TRADE OPENED
NVDA @ $142.30 | 14 shares | $1,992
Conviction: 82/100
Reason: RSI reversal + bullish MACD crossover, beat earnings estimate by 12%

🔴 TRADE CLOSED
NVDA | Entry $142.30 → Exit $163.65
P&L: +$298.10 (+14.9%)
Reason: Profit target hit (15%)

⚠️ PORTFOLIO GUARD
Blocked: AAPL buy
Reason: Daily loss limit reached (-3.1%)
Engine: buys paused until tomorrow open

📊 WEEKLY DIGEST
Portfolio: $24,840 (+2.3% this week)
Trades: 3 opened, 2 closed
Win rate: 67% (all-time)
Best: MSFT +11.2% | Worst: META -4.1%
```

---

# Milestone v1.0 — Live Trading

*Real money. Small size. Scaled carefully.*

---

## Phase 7 — Live Trading (Gradual Rollout)

**Goal:** Graduate from paper to live trading. Never rush this phase.

**Prereqs:** All previous phases complete + all graduation criteria met

### Graduation Criteria (every item must be true before real money)

- 60+ consecutive days of paper trading with no critical bugs
- All exit rules have fired correctly at least 5 times each
- Portfolio guard rules verified under stress scenarios
- Trade log showing positive expectancy (average win > average loss)
- Manual review of every paper trade's entry/exit reasoning
- Risk Config UI working — you can tune parameters from the dashboard
- Telegram notifications confirmed working on mobile for all alert types

### Go-Live Steps

| #   | Task                                                                                          | Files                            |
| --- | --------------------------------------------------------------------------------------------- | -------------------------------- |
| 7.1 | Build `IBKRBroker` implementing `BrokerInterface` (optional, if using IBKR instead of Alpaca) | `execution/ibkr_broker.py` (new) |
| 7.2 | Set up Alpaca live account (or IBKR), add live credentials to `.env`                          | `.env`                           |
| 7.3 | Deploy Week 1: $2,000 max, 2 positions max, $200-300 per trade, monitor hourly                | config / Risk Config UI          |
| 7.4 | After Week 1 with no execution errors: scale to $10,000, 5 max positions                      | config / Risk Config UI          |
| 7.5 | Month 3+: increase to target capital, weekly agent review, monthly threshold recalibration    | Risk Config UI                   |

See [design_reference.md — Broker Setup Commands](design_reference.md#broker-setup-commands) and [US Regulatory Note](design_reference.md#us-regulatory-note) for broker details and PDT rules.

---

## Weekly Rhythm

**Every week:**

- Monday: Review last week's signal log — did the agents call it right?
- Tuesday–Thursday: Build next feature from this roadmap
- Friday: Write tests, review paper trades, update docs

**Every month:**

- Recalibrate conviction thresholds from the Risk Config UI based on real data
- Review which agents are adding value vs noise
- Upgrade watchlist based on what's been performing

---

## Risks and Mitigations

| Risk                                 | Mitigation                                                        |
| ------------------------------------ | ----------------------------------------------------------------- |
| LLM hallucination drives a bad trade | Conviction gate + portfolio guard as hard stops                   |
| API outage during market hours       | Retry logic + fallback to cached data                             |
| Broker API failure                   | Always log intent before execution; reconcile on startup          |
| Runaway losses                       | Daily loss limit halts all activity automatically                 |
| Overfitting to paper trading         | Paper trade on different time periods before going live           |
| Low liquidity stocks                 | Volume filter on watchlist (>1M shares/day avg)                   |
| Bad config change via UI             | "Reset to defaults" button; config changes logged to `system_log` |
| Missed alerts (Telegram outage)      | Dashboard auto-refresh is always the source of truth             |

---

## Updated Target File Structure

```
TradeDog/
├── tradingagents/              ← Upstream framework (DO NOT MODIFY)
│   ├── agents/
│   ├── dataflows/
│   ├── graph/trading_graph.py
│   └── default_config.py
│
├── config/                     ← NEW: Personal runtime config
│   ├── user_config.json            ← Personal risk parameters (edited via UI)
│   └── config_manager.py           ← Read/write user_config.json
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
├── notifications/              ← NEW: Telegram alerts
│   ├── telegram_notifier.py
│   └── templates.py
│
├── database/                   ← NEW: Data persistence
│   ├── schema.sql
│   ├── db.py
│   └── models.py
│
├── dashboard/                  ← NEW: Streamlit UI (your main interface)
│   └── app.py
│       ├── Page 1: Portfolio Overview
│       ├── Page 2: Signal Feed
│       ├── Page 3: Trade History
│       ├── Page 4: Agent Monitor
│       └── Page 5: Risk Settings   ← NEW (Phase 6B)
│
├── watchlist/                  ← NEW: Curated tickers
│   ├── watchlist.json
│   └── sector_map.json
│
├── scheduler/                  ← NEW: Orchestrates daily run
│   └── main_loop.py
│
├── tests/
│   └── ...
│
├── docs/
│   ├── architecture.md
│   ├── agent_contracts.md
│   ├── design_reference.md
│   └── TradeDog_Roadmap.md
│
├── .env
├── main.py
└── requirements.txt
```

---

*Finish each phase completely before starting the next. The order matters.*

---

## Phase Summary

| Phase  | Focus                        |
| ------ | ---------------------------- |
| **0**  | Audit & cleanup              |
| **1**  | Watchlist + integration tests |
| **2**  | Paper trading execution      |
| **3**  | Conviction scoring           |
| **4**  | Position monitoring          |
| **5**  | Portfolio risk controls      |
| **6**  | Streamlit dashboard          |
| **6B** | Risk config UI               |
| **6C** | Telegram notifications       |
| **7**  | Live trading rollout         |