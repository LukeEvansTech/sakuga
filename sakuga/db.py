"""SQLite storage. One connection per unit of work; WAL lets the web UI read while a sweep writes.

A **listing** is one sighting on one site (a Yahoo auction id, a Mercari item). A **cel** is the
artwork: relists and copies of the same cel on other sites join it, so it is announced once.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cels (
    id INTEGER PRIMARY KEY,
    franchise TEXT NOT NULL,
    kind TEXT NOT NULL,
    title_en TEXT NOT NULL DEFAULT '',
    title_jp TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    image TEXT NOT NULL DEFAULT '',
    phash TEXT NOT NULL DEFAULT '',
    gainax INTEGER NOT NULL DEFAULT 0,
    baseline INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    available INTEGER NOT NULL DEFAULT 1,
    digest_id INTEGER,
    instant_sent TEXT
);
CREATE INDEX IF NOT EXISTS cels_first ON cels (first_seen);
CREATE TABLE IF NOT EXISTS listings (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    cel_id INTEGER REFERENCES cels(id),
    title TEXT NOT NULL,
    extra TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL,
    img_url TEXT NOT NULL DEFAULT '',
    image TEXT NOT NULL DEFAULT '',
    phash TEXT NOT NULL DEFAULT '',
    yen INTEGER,
    gbp REAL,
    info TEXT NOT NULL DEFAULT '',
    auction INTEGER NOT NULL DEFAULT 0,
    franchise TEXT NOT NULL,
    kind TEXT NOT NULL,
    verdict TEXT NOT NULL DEFAULT '',
    ok INTEGER NOT NULL DEFAULT 1,
    reason TEXT NOT NULL DEFAULT '',
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS listings_cel ON listings (cel_id);
CREATE TABLE IF NOT EXISTS prices (
    listing_id TEXT NOT NULL,
    seen TEXT NOT NULL,
    yen INTEGER,
    PRIMARY KEY (listing_id, seen)
);
CREATE TABLE IF NOT EXISTS same (
    a TEXT NOT NULL,
    b TEXT NOT NULL,
    same INTEGER NOT NULL,
    PRIMARY KEY (a, b)
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started TEXT NOT NULL,
    finished TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    trigger TEXT NOT NULL DEFAULT 'schedule',
    listings INTEGER NOT NULL DEFAULT 0,
    kept INTEGER NOT NULL DEFAULT 0,
    new_cels INTEGER NOT NULL DEFAULT 0,
    llm_calls INTEGER NOT NULL DEFAULT 0,
    llm_cost REAL NOT NULL DEFAULT 0,
    alerts TEXT NOT NULL DEFAULT '',
    error TEXT
);
CREATE TABLE IF NOT EXISTS run_sources (
    run_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    count INTEGER,
    error TEXT,
    seconds REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, name)
);
CREATE TABLE IF NOT EXISTS digests (
    id INTEGER PRIMARY KEY,
    created TEXT NOT NULL,
    cels INTEGER NOT NULL,
    result TEXT NOT NULL
);
"""


class DB:
    def __init__(self, path: Path):
        self.path = path
        with self.tx() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def all(self, sql: str, *args: Any) -> list[sqlite3.Row]:
        with self.tx() as c:
            return c.execute(sql, args).fetchall()

    def one(self, sql: str, *args: Any) -> sqlite3.Row | None:
        with self.tx() as c:
            return c.execute(sql, args).fetchone()

    def run(self, sql: str, *args: Any) -> int:
        with self.tx() as c:
            cur = c.execute(sql, args)
            return int(cur.lastrowid or 0)

    def get(self, key: str, default: Any) -> Any:
        row = self.one("SELECT value FROM kv WHERE key = ?", key)
        return json.loads(row["value"]) if row else default

    def put(self, key: str, value: Any) -> None:
        self.run(
            "INSERT INTO kv (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            key,
            json.dumps(value),
        )
