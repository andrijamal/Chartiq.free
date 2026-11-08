import sqlite3
import os
import uuid
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "sessions.db")


class SessionNotFoundError(ValueError):
    """Dipakai saat trade disimpan ke sesi yang tidak ada."""


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _recalc_balance(cursor, session_id: str):
    cursor.execute(
        """
        SELECT s.initial_balance + COALESCE(SUM(CASE WHEN t.outcome != 'OPEN' THEN t.pnl ELSE 0 END), 0) AS recalculated_balance
        FROM sessions s
        LEFT JOIN trades t ON s.id = t.session_id
        WHERE s.id = ?
        GROUP BY s.id
        """,
        (session_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise SessionNotFoundError(f"Session '{session_id}' tidak ditemukan")
    current_balance = float(row["recalculated_balance"] or 0.0)
    cursor.execute(
        "UPDATE sessions SET current_balance = ?, updated_at = ? WHERE id = ?",
        (current_balance, datetime.now(timezone.utc).isoformat(), session_id),
    )
    return current_balance


def init_db():
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                initial_balance REAL NOT NULL,
                current_balance REAL NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                notes TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                type TEXT NOT NULL,
                entry_time INTEGER NOT NULL,
                exit_time INTEGER,
                entry_price REAL NOT NULL,
                exit_price REAL,
                stop_loss REAL NOT NULL,
                take_profit REAL NOT NULL,
                lot_size REAL NOT NULL,
                pnl REAL DEFAULT 0,
                pnl_percent REAL DEFAULT 0,
                rr REAL DEFAULT 0,
                outcome TEXT DEFAULT 'OPEN',
                notes TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


init_db()


def create_session(name: str, symbol: str, timeframe: str, initial_balance: float = 10000.0, notes: str = "") -> dict:
    session_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO sessions (id, name, symbol, timeframe, initial_balance, current_balance, created_at, updated_at, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, name, symbol, timeframe, initial_balance, initial_balance, now, now, notes),
        )
        conn.commit()
    finally:
        conn.close()
    return get_session(session_id)


def get_session(session_id: str) -> dict:
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
        row = cursor.fetchone()
        if not row:
            return None
        session_dict = dict(row)
        cursor.execute("SELECT * FROM trades WHERE session_id = ? ORDER BY entry_time ASC", (session_id,))
        trades = [dict(t) for t in cursor.fetchall()]
        session_dict["trades"] = trades
        return session_dict
    finally:
        conn.close()


def list_sessions() -> list[dict]:
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT s.*, count(t.id) as total_trades,
                   SUM(CASE WHEN t.pnl > 0 AND t.outcome != 'OPEN' THEN 1 ELSE 0 END) as win_count
            FROM sessions s
            LEFT JOIN trades t ON s.id = t.session_id
            GROUP BY s.id
            ORDER BY s.updated_at DESC
            """
        )
        rows = [dict(r) for r in cursor.fetchall()]
        return rows
    finally:
        conn.close()


def update_session(session_id: str, name: str = None, notes: str = None) -> dict:
    conn = get_db()
    try:
        cursor = conn.cursor()
        fields = []
        params = []
        if name is not None:
            fields.append("name = ?")
            params.append(name)
        if notes is not None:
            fields.append("notes = ?")
            params.append(notes)
        if not fields:
            return get_session(session_id)

        fields.append("updated_at = ?")
        params.extend([datetime.now(timezone.utc).isoformat(), session_id])
        cursor.execute(f"UPDATE sessions SET {', '.join(fields)} WHERE id = ?", tuple(params))
        conn.commit()
        return get_session(session_id)
    finally:
        conn.close()


def get_session_stats(session_id: str) -> dict:
    session = get_session(session_id)
    if not session:
        raise SessionNotFoundError(f"Session '{session_id}' tidak ditemukan")

    trades = session.get("trades", [])
    closed_trades = [trade for trade in trades if trade.get("outcome") != "OPEN"]
    wins = sum(1 for trade in closed_trades if float(trade.get("pnl", 0) or 0) > 0)
    losses = sum(1 for trade in closed_trades if float(trade.get("pnl", 0) or 0) < 0)
    breakeven = sum(1 for trade in closed_trades if abs(float(trade.get("pnl", 0) or 0)) < 0.01)
    net_profit = sum(float(trade.get("pnl", 0) or 0) for trade in closed_trades)
    gross_profit = sum(float(trade.get("pnl", 0) or 0) for trade in closed_trades if float(trade.get("pnl", 0) or 0) > 0)
    gross_loss = abs(sum(float(trade.get("pnl", 0) or 0) for trade in closed_trades if float(trade.get("pnl", 0) or 0) < 0))
    avg_rr = sum(float(trade.get("rr", 0) or 0) for trade in closed_trades) / len(closed_trades) if closed_trades else 0.0

    peak = float(session.get("initial_balance", 0.0))
    balance = float(session.get("initial_balance", 0.0))
    max_drawdown = 0.0
    for trade in sorted(closed_trades, key=lambda item: int(item.get("entry_time", 0))):
        balance += float(trade.get("pnl", 0) or 0)
        if balance > peak:
            peak = balance
        max_drawdown = max(max_drawdown, peak - balance)

    profit_factor = None
    if gross_loss > 0:
        profit_factor = round(gross_profit / gross_loss, 2) if gross_profit > 0 else 0.0
    elif gross_profit > 0:
        profit_factor = None

    return {
        "win_rate": round((wins / len(closed_trades)) * 100, 2) if closed_trades else 0.0,
        "net_profit": round(net_profit, 2),
        "profit_factor": profit_factor,
        "avg_rr": round(avg_rr, 2),
        "max_drawdown": round(max_drawdown, 2),
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "total_trades": len(closed_trades),
    }


def save_trade(trade: dict) -> dict:
    trade_id = trade.get("id") or str(uuid.uuid4())[:8]
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM sessions WHERE id = ?", (trade["session_id"],))
        if cursor.fetchone() is None:
            raise SessionNotFoundError(f"Session '{trade['session_id']}' tidak ditemukan")

        cursor.execute(
            """
            INSERT OR REPLACE INTO trades
            (id, session_id, symbol, type, entry_time, exit_time, entry_price, exit_price, stop_loss, take_profit, lot_size, pnl, pnl_percent, rr, outcome, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_id,
                trade["session_id"],
                trade["symbol"],
                trade["type"],
                trade["entry_time"],
                trade.get("exit_time"),
                trade["entry_price"],
                trade.get("exit_price"),
                trade["stop_loss"],
                trade["take_profit"],
                trade["lot_size"],
                trade.get("pnl", 0.0),
                trade.get("pnl_percent", 0.0),
                trade.get("rr", 0.0),
                trade.get("outcome", "OPEN"),
                trade.get("notes", ""),
            ),
        )
        _recalc_balance(cursor, trade["session_id"])
        conn.commit()
        saved_trade = dict(trade)
        saved_trade["id"] = trade_id
        return saved_trade
    finally:
        conn.close()


def delete_trade(trade_id: str):
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT session_id FROM trades WHERE id = ?", (trade_id,))
        row = cursor.fetchone()
        if row is not None:
            session_id = row["session_id"]
            cursor.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
            _recalc_balance(cursor, session_id)
            conn.commit()
    finally:
        conn.close()


def delete_session(session_id: str):
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM trades WHERE session_id = ?", (session_id,))
        cursor.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
    finally:
        conn.close()

