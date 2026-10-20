"""SQLite persistence for a small, local Discord demonstration bot.

This is a STARTER store, not the authoritative cloud APEX position service.
No brokerage integration, quotes, or financial recommendations are included.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from decimal import Decimal

SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_user_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    price TEXT NOT NULL,
    shares TEXT NOT NULL,
    signal_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_positions_owner ON positions(discord_user_id);
"""

def initialize(database: Path) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        connection.executescript(SCHEMA)

def add_position(database: Path, user_id: int, ticker: str, price: Decimal,
                 shares: Decimal, signal_id: str) -> int:
    if price <= 0 or shares <= 0:
        raise ValueError("Entry price and share count must be positive")
    with sqlite3.connect(database) as connection:
        cursor = connection.execute(
            "INSERT INTO positions (discord_user_id,ticker,price,shares,signal_id) "
            "VALUES (?,?,?,?,?)",
            (str(user_id), ticker, str(price), str(shares), signal_id)
        )
        return int(cursor.lastrowid)

def get_positions(database: Path, user_id: int) -> list[tuple]:
    with sqlite3.connect(database) as connection:
        return connection.execute(
            "SELECT id,ticker,price,shares,signal_id,created_at "
            "FROM positions WHERE discord_user_id=? ORDER BY id DESC LIMIT 25",
            (str(user_id),)
        ).fetchall()

def delete_position(database: Path, user_id: int, position_id: int) -> bool:
    with sqlite3.connect(database) as connection:
        result = connection.execute(
            "DELETE FROM positions WHERE id=? AND discord_user_id=?",
            (position_id, str(user_id))
        )
        return result.rowcount == 1
