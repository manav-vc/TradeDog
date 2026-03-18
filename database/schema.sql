-- TradeDog database schema (SQLite)
-- Created by Phase 2 — Paper Trading Execution Layer

-- All positions (open and closed)
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    side TEXT NOT NULL DEFAULT 'BUY',          -- 'BUY' (long only for now)
    entry_price REAL NOT NULL,
    entry_time DATETIME NOT NULL,
    qty INTEGER NOT NULL,
    cost_basis REAL NOT NULL,                  -- entry_price * qty
    highest_price REAL,                        -- For trailing stop tracking
    exit_price REAL,
    exit_time DATETIME,
    exit_reason TEXT,                          -- 'PROFIT_TARGET', 'TRAILING_STOP', 'STOP_LOSS', 'REVERSAL', 'TIME_EXIT', 'MANUAL'
    status TEXT NOT NULL DEFAULT 'OPEN',       -- 'OPEN' or 'CLOSED'
    broker TEXT NOT NULL DEFAULT 'paper',      -- 'paper', 'alpaca', 'ibkr'
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- All orders submitted to the broker
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    broker_order_id TEXT,                      -- ID returned by the broker (null for paper)
    ticker TEXT NOT NULL,
    side TEXT NOT NULL,                        -- 'BUY' or 'SELL'
    order_type TEXT NOT NULL DEFAULT 'MARKET', -- 'MARKET', 'LIMIT'
    qty INTEGER NOT NULL,
    requested_price REAL,                      -- Price at time of order request
    filled_price REAL,                         -- Actual fill price
    status TEXT NOT NULL DEFAULT 'PENDING',    -- 'PENDING', 'FILLED', 'CANCELLED', 'REJECTED'
    position_id INTEGER,                       -- Links to positions table
    broker TEXT NOT NULL DEFAULT 'paper',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    filled_at DATETIME,
    FOREIGN KEY (position_id) REFERENCES positions(id)
);

-- Agent signals log (for analysis and debugging)
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    trade_date TEXT NOT NULL,                  -- Date the agents analyzed
    timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    agent_decision TEXT,                       -- Raw decision text from propagate()
    parsed_action TEXT,                        -- 'BUY', 'SELL', 'HOLD'
    conviction_score REAL,                     -- 0-100 (Phase 3)
    action_taken TEXT,                         -- 'BOUGHT', 'SKIPPED', 'REJECTED_BY_GUARD'
    skip_reason TEXT,
    full_state_json TEXT                       -- Full agent state JSON for debugging
);

-- Daily portfolio snapshots
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date DATE NOT NULL UNIQUE,
    total_value REAL,
    cash REAL,
    invested REAL,
    num_positions INTEGER,
    daily_pnl REAL,
    daily_pnl_pct REAL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- System events and errors
CREATE TABLE IF NOT EXISTS system_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    level TEXT NOT NULL DEFAULT 'INFO',        -- 'INFO', 'WARNING', 'ERROR'
    component TEXT,                            -- 'order_manager', 'paper_broker', 'monitor', etc.
    message TEXT
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_positions_ticker ON positions(ticker);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_signals_ticker ON signals(ticker);
CREATE INDEX IF NOT EXISTS idx_signals_timestamp ON signals(timestamp);
CREATE INDEX IF NOT EXISTS idx_system_log_level ON system_log(level);
