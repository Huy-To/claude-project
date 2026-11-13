"""SQLite storage: keeps every post seen and the newest id per account.

The newest id lets each poll ask only for newer posts, which keeps paid sources cheap.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import Post

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id          TEXT PRIMARY KEY,
    account     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    text        TEXT NOT NULL,
    url         TEXT NOT NULL,
    source      TEXT NOT NULL,
    data        TEXT NOT NULL,
    fetched_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS posts_account_created ON posts(account, created_at);
CREATE TABLE IF NOT EXISTS cursors (
    account  TEXT PRIMARY KEY,
    since_id TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)

    def since_id(self, account: str) -> str | None:
        row = self.db.execute(
            "SELECT since_id FROM cursors WHERE account = ?", (account.lower(),)
        ).fetchone()
        return row[0] if row else None

    def save(self, posts: list[Post]) -> list[Post]:
        """Insert posts, advance cursors, and return only the posts not seen before."""
        new: list[Post] = []
        with self.db:
            for p in posts:
                cur = self.db.execute(
                    "INSERT OR IGNORE INTO posts (id, account, created_at, text, url, source, data)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (p.id, p.account.lower(), p.created_at.isoformat(), p.text, p.url, p.source,
                     json.dumps(p.to_dict(), ensure_ascii=False)),
                )
                if cur.rowcount:
                    new.append(p)
            newest: dict[str, int] = {}
            for p in posts:
                key = p.account.lower()
                newest[key] = max(newest.get(key, 0), int(p.id))
            for account, pid in newest.items():
                old = self.since_id(account)
                if old is None or pid > int(old):
                    self.db.execute(
                        "INSERT INTO cursors (account, since_id) VALUES (?, ?)"
                        " ON CONFLICT(account) DO UPDATE SET since_id = excluded.since_id",
                        (account, str(pid)),
                    )
        return new

    def recent(self, account: str | None = None, limit: int = 20) -> list[dict]:
        sql = "SELECT data FROM posts"
        args: tuple = ()
        if account:
            sql += " WHERE account = ?"
            args = (account.lower(),)
        sql += " ORDER BY CAST(id AS INTEGER) DESC LIMIT ?"
        return [json.loads(r[0]) for r in self.db.execute(sql, (*args, limit))]

    def close(self) -> None:
        self.db.close()
