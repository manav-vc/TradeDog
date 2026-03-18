"""
SQLite database manager for TradeDog.

Handles schema initialization, CRUD for positions/orders/signals,
and portfolio snapshot queries.  Thread-safe via check_same_thread=False.
"""

import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from .models import Position, Order, AccountInfo

_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")
_DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "tradedog.db"
)


class Database:
    """Thin wrapper around SQLite for TradeDog."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or _DEFAULT_DB_PATH
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self):
        with open(_SCHEMA_PATH, "r") as f:
            self.conn.executescript(f.read())

    def close(self):
        self.conn.close()

    # ── Positions ─────────────────────────────────────────────────────

    def insert_position(self, pos: Position) -> int:
        cur = self.conn.execute(
            """INSERT INTO positions
               (ticker, side, entry_price, entry_time, qty, cost_basis,
                highest_price, status, broker)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pos.ticker,
                pos.side,
                pos.entry_price,
                pos.entry_time or datetime.now(timezone.utc),
                pos.qty,
                pos.cost_basis or pos.entry_price * pos.qty,
                pos.highest_price or pos.entry_price,
                pos.status,
                pos.broker,
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def close_position(
        self, position_id: int, exit_price: float, exit_reason: str
    ) -> None:
        self.conn.execute(
            """UPDATE positions
               SET exit_price = ?, exit_time = ?, exit_reason = ?, status = 'CLOSED'
               WHERE id = ?""",
            (exit_price, datetime.now(timezone.utc), exit_reason, position_id),
        )
        self.conn.commit()

    def update_highest_price(self, position_id: int, price: float) -> None:
        self.conn.execute(
            "UPDATE positions SET highest_price = ? WHERE id = ? AND highest_price < ?",
            (price, position_id, price),
        )
        self.conn.commit()

    def get_position(self, position_id: int) -> Optional[Position]:
        row = self.conn.execute(
            "SELECT * FROM positions WHERE id = ?", (position_id,)
        ).fetchone()
        return self._row_to_position(row) if row else None

    def get_open_positions(self) -> list[Position]:
        rows = self.conn.execute(
            "SELECT * FROM positions WHERE status = 'OPEN' ORDER BY entry_time"
        ).fetchall()
        return [self._row_to_position(r) for r in rows]

    def get_closed_positions(self) -> list[Position]:
        rows = self.conn.execute(
            "SELECT * FROM positions WHERE status = 'CLOSED' ORDER BY exit_time DESC"
        ).fetchall()
        return [self._row_to_position(r) for r in rows]

    def get_all_positions(self) -> list[Position]:
        rows = self.conn.execute(
            "SELECT * FROM positions ORDER BY created_at DESC"
        ).fetchall()
        return [self._row_to_position(r) for r in rows]

    def _row_to_position(self, row: sqlite3.Row) -> Position:
        return Position(
            id=row["id"],
            ticker=row["ticker"],
            side=row["side"],
            entry_price=row["entry_price"],
            entry_time=row["entry_time"],
            qty=row["qty"],
            cost_basis=row["cost_basis"],
            highest_price=row["highest_price"],
            exit_price=row["exit_price"],
            exit_time=row["exit_time"],
            exit_reason=row["exit_reason"],
            status=row["status"],
            broker=row["broker"],
        )

    # ── Orders ────────────────────────────────────────────────────────

    def insert_order(self, order: Order) -> int:
        cur = self.conn.execute(
            """INSERT INTO orders
               (broker_order_id, ticker, side, order_type, qty,
                requested_price, filled_price, status, position_id, broker, filled_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                order.broker_order_id,
                order.ticker,
                order.side,
                order.order_type,
                order.qty,
                order.requested_price,
                order.filled_price,
                order.status,
                order.position_id,
                order.broker,
                order.filled_at,
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def update_order_status(
        self, order_id: int, status: str, filled_price: Optional[float] = None
    ) -> None:
        if filled_price is not None:
            self.conn.execute(
                "UPDATE orders SET status = ?, filled_price = ?, filled_at = ? WHERE id = ?",
                (status, filled_price, datetime.now(timezone.utc), order_id),
            )
        else:
            self.conn.execute(
                "UPDATE orders SET status = ? WHERE id = ?", (status, order_id)
            )
        self.conn.commit()

    def get_orders_for_position(self, position_id: int) -> list[Order]:
        rows = self.conn.execute(
            "SELECT * FROM orders WHERE position_id = ? ORDER BY created_at",
            (position_id,),
        ).fetchall()
        return [self._row_to_order(r) for r in rows]

    def _row_to_order(self, row: sqlite3.Row) -> Order:
        return Order(
            id=row["id"],
            broker_order_id=row["broker_order_id"],
            ticker=row["ticker"],
            side=row["side"],
            order_type=row["order_type"],
            qty=row["qty"],
            requested_price=row["requested_price"],
            filled_price=row["filled_price"],
            status=row["status"],
            position_id=row["position_id"],
            broker=row["broker"],
            created_at=row["created_at"],
            filled_at=row["filled_at"],
        )

    # ── Signals ───────────────────────────────────────────────────────

    def insert_signal(
        self,
        ticker: str,
        trade_date: str,
        agent_decision: str,
        parsed_action: str,
        action_taken: str,
        skip_reason: Optional[str] = None,
        conviction_score: Optional[float] = None,
        full_state_json: Optional[str] = None,
    ) -> int:
        cur = self.conn.execute(
            """INSERT INTO signals
               (ticker, trade_date, agent_decision, parsed_action,
                action_taken, skip_reason, conviction_score, full_state_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ticker,
                trade_date,
                agent_decision,
                parsed_action,
                action_taken,
                skip_reason,
                conviction_score,
                full_state_json,
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_recent_signals(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM signals ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ── System Log ────────────────────────────────────────────────────

    def log(self, level: str, component: str, message: str) -> None:
        self.conn.execute(
            "INSERT INTO system_log (level, component, message) VALUES (?, ?, ?)",
            (level, component, message),
        )
        self.conn.commit()

    # ── Trading Pause ──────────────────────────────────────────────

    def is_trading_paused(self) -> bool:
        """Check if trading is paused (set via dashboard toggle)."""
        row = self.conn.execute(
            "SELECT message FROM system_log WHERE component = 'TRADING_PAUSE' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row["message"] == "PAUSED" if row else False

    def set_trading_paused(self, paused: bool) -> None:
        """Set the trading pause flag."""
        self.log("INFO", "TRADING_PAUSE", "PAUSED" if paused else "ACTIVE")

    # ── Portfolio Snapshots ───────────────────────────────────────────

    def save_snapshot(
        self,
        total_value: float,
        cash: float,
        invested: float,
        num_positions: int,
        daily_pnl: float,
        daily_pnl_pct: float,
    ) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.conn.execute(
            """INSERT OR REPLACE INTO portfolio_snapshots
               (snapshot_date, total_value, cash, invested, num_positions,
                daily_pnl, daily_pnl_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (today, total_value, cash, invested, num_positions, daily_pnl, daily_pnl_pct),
        )
        self.conn.commit()

    # ── Account Summary (computed from positions) ─────────────────────

    def get_account_summary(self, starting_cash: float = 100_000.0) -> AccountInfo:
        """Compute account state from positions table."""
        open_positions = self.get_open_positions()
        invested = sum(p.cost_basis for p in open_positions)

        # Cash = starting capital - invested + realized gains from closed positions
        closed = self.get_closed_positions()
        realized_pnl = sum((p.pnl or 0.0) for p in closed)
        cash = starting_cash - invested + realized_pnl

        return AccountInfo(
            total_value=cash + invested,
            cash=cash,
            invested=invested,
            num_positions=len(open_positions),
            buying_power=cash,
            broker="paper",
        )
