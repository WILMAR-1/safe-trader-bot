"""
Base de datos local SQLite para almacenar trades.
Todo se guarda localmente, nada sale de tu maquina.
"""

import sqlite3
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, db_path: str = "data/trades.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                amount REAL NOT NULL,
                stake_amount REAL NOT NULL,
                profit REAL,
                profit_pct REAL,
                status TEXT DEFAULT 'open',
                entry_reason TEXT,
                exit_reason TEXT,
                entry_time TEXT NOT NULL,
                exit_time TEXT,
                entry_order_id TEXT,
                exit_order_id TEXT
            );

            CREATE TABLE IF NOT EXISTS bot_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                balance REAL,
                drawdown_pct REAL,
                open_trades INTEGER,
                total_trades INTEGER,
                win_rate REAL,
                stats_json TEXT
            );
        """)
        self.conn.commit()

    def open_trade(self, symbol: str, entry_price: float, amount: float,
                   stake_amount: float, entry_reason: str = "",
                   order_id: str = "") -> int:
        cursor = self.conn.execute(
            """INSERT INTO trades (symbol, side, entry_price, amount, stake_amount,
               status, entry_reason, entry_time, entry_order_id)
               VALUES (?, 'buy', ?, ?, ?, 'open', ?, ?, ?)""",
            (symbol, entry_price, amount, stake_amount, entry_reason,
             datetime.now().isoformat(), order_id),
        )
        self.conn.commit()
        trade_id = cursor.lastrowid
        logger.info("Trade #%d abierto: %s @ %.2f", trade_id, symbol, entry_price)
        return trade_id

    def close_trade(self, trade_id: int, exit_price: float, exit_reason: str = "",
                    order_id: str = ""):
        trade = self.get_trade(trade_id)
        if not trade:
            return

        profit = (exit_price - trade["entry_price"]) * trade["amount"]
        profit_pct = ((exit_price / trade["entry_price"]) - 1) * 100

        self.conn.execute(
            """UPDATE trades SET exit_price=?, profit=?, profit_pct=?, status='closed',
               exit_reason=?, exit_time=?, exit_order_id=?
               WHERE id=?""",
            (exit_price, profit, profit_pct, exit_reason,
             datetime.now().isoformat(), order_id, trade_id),
        )
        self.conn.commit()
        logger.info("Trade #%d cerrado: %s @ %.2f | P/L: %.2f (%.2f%%)",
                     trade_id, trade["symbol"], exit_price, profit, profit_pct)

    def get_trade(self, trade_id: int) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
        return dict(row) if row else None

    def get_open_trades(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM trades WHERE status='open' ORDER BY entry_time"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_trade_history(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM trades WHERE status='closed' ORDER BY exit_time DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def save_stats(self, stats: dict):
        self.conn.execute(
            """INSERT INTO bot_stats (timestamp, balance, drawdown_pct, open_trades,
               total_trades, win_rate, stats_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (datetime.now().isoformat(), stats.get("balance"),
             stats.get("drawdown_pct"), stats.get("open_trades"),
             stats.get("total_trades"), stats.get("win_rate"),
             json.dumps(stats)),
        )
        self.conn.commit()

    def close(self):
        self.conn.close()
