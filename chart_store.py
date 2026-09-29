"""A single SQLite cache for charts served by the local website."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3


class ChartStore:
    def __init__(self, directory: Path):
        self.path = Path(directory) / "charts.sqlite3"
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS charts (
                symbol TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                market TEXT NOT NULL,
                current REAL NOT NULL,
                asof TEXT NOT NULL,
                generated TEXT NOT NULL,
                payload TEXT NOT NULL
            )""")
            db.commit()

    def get(self, symbol: str) -> dict | None:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT payload FROM charts WHERE symbol = ?", (symbol,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, payload: dict) -> None:
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("""INSERT INTO charts (symbol, name, market, current, asof, generated, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    name=excluded.name, market=excluded.market, current=excluded.current,
                    asof=excluded.asof, generated=excluded.generated, payload=excluded.payload""",
                (payload["symbol"], payload.get("name", ""), payload["market"], payload["current"],
                 payload["asof"], payload["generated"], json.dumps(payload, ensure_ascii=False, allow_nan=False)))
            db.commit()

    def recent(self, limit: int = 8) -> list[dict]:
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute("""SELECT symbol, name, market, current, asof FROM charts
                ORDER BY generated DESC LIMIT ?""", (limit,)).fetchall()
        return [dict(zip(("symbol", "name", "market", "current", "asof"), row)) for row in rows]
