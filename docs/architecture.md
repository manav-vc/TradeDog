### High-Level Overview (The Big Picture)

TradeDog = LangGraph + Specialized Agents + Real-Time Loop + Paper Execution

Orchestration Engine: LangGraph (stateful graph of nodes/edges) — this is the "brain" that connects everything.
Core Loop: Runs every 15–60 minutes during market hours (or on-demand).
Key Innovation (Your Addition): Profit Guardian + Executor for autonomous profit-taking.
Modes:
Analysis Mode: Deep research for new entries.
Monitoring Mode: Real-time profit/exit checks (runs lighter, cheaper).
Backtest Mode: Historical simulation for tuning.



Text Diagram (the mental model):

[Market Data Feed (Alpaca + Polygon + Alpha Vantage)]
          ↓ (ingest every cycle)
[Shared State (Portfolio, Positions, History, Signals)]
          ↓
[Agent Graph (LangGraph)]
  ├── Analyst Team (parallel)
  ├── Researcher Team (debate)
  ├── Decision Maker
  ├── Executor (Alpaca orders)
  └── Profit Guardian (exit logic)
          ↓
[Output: Trade Actions + Dashboard Update]