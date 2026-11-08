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
    instant_sent TEXT,
    rejected INTEGER NOT NULL DEFAULT 0,
    mailed TEXT
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
    active INTEGER NOT NULL DEFAULT 1,
    checked TEXT,
    asked TEXT,
    photos TEXT,
    recheck INTEGER NOT NULL DEFAULT 0,
    photos_tried TEXT,
    photos_done INTEGER NOT NULL DEFAULT 0
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
            cols = {r["name"] for r in c.execute("PRAGMA table_info(listings)")}
            if "checked" not in cols:  # added after the first release of the schema
                c.execute("ALTER TABLE listings ADD COLUMN checked TEXT")
            cel_cols = {r["name"] for r in c.execute("PRAGMA table_info(cels)")}
            if (
                "rejected" not in cel_cols
            ):  # hidden by a later LLM verdict; kept for its id and history
                c.execute("ALTER TABLE cels ADD COLUMN rejected INTEGER NOT NULL DEFAULT 0")
            if "mailed" not in cel_cols:  # when the daily email carried it
                # One transaction: the column must never exist without its backfill, or the
                # first email would announce what Pushover already did.
                c.commit()
                c.execute("BEGIN")
                c.execute("ALTER TABLE cels ADD COLUMN mailed TEXT")
                c.execute(
                    "UPDATE cels SET mailed = 'before email'"
                    " WHERE digest_id IS NOT NULL OR instant_sent IS NOT NULL"
                )
                c.execute("COMMIT")
            if "asked" not in cols:  # when backfill last asked the LLM about this listing
                c.execute("ALTER TABLE listings ADD COLUMN asked TEXT")
            if "photos" not in cols:  # further photo files stored so far (JSON list)
                c.execute("ALTER TABLE listings ADD COLUMN photos TEXT")
            if "recheck" not in cols:  # new photos arrived after the LLM's verdict: ask again
                c.execute("ALTER TABLE listings ADD COLUMN recheck INTEGER NOT NULL DEFAULT 0")
            if "photos_tried" not in cols:  # when an item page was last opened for its photos
                c.execute("ALTER TABLE listings ADD COLUMN photos_tried TEXT")
            if "photos_done" not in cols:  # every further photo stored, or gone for good
                c.execute("ALTER TABLE listings ADD COLUMN photos_done INTEGER NOT NULL DEFAULT 0")
            # A sweep cut off by a restart never finishes; mark it so pages stop saying "Sweeping".
            c.execute(
                "UPDATE runs SET status = 'interrupted', finished = started WHERE status = 'running'"
            )

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
