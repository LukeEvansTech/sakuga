"""Shared fixture: a small catalogue of three Eva cels, the first of them the baseline."""

import datetime as dt

import pytest
from sakuga import config
from sakuga.db import DB

# Relative to now: retire() compares last_seen with the real clock.
T = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SAKUGA_DATA", str(tmp_path))
    s = config.load()
    db = DB(s.db_path)
    for cel_id, baseline in ((1, 1), (2, 0), (3, 0)):
        db.run(
            "INSERT INTO cels (id, franchise, kind, title_en, title_jp, baseline, first_seen, last_seen)"
            " VALUES (?, 'eva', 'cel', ?, 'セル画　綾波レイ', ?, ?, ?)",
            cel_id,
            f"Rei Ayanami {cel_id}",
            baseline,
            T,
            T,
        )
        db.run(
            "INSERT INTO listings (id, source, cel_id, title, url, yen, franchise, kind, first_seen, last_seen)"
            " VALUES (?, ?, ?, 'セル画', 'https://example.invalid/x', 120000, 'eva', 'cel', ?, ?)",
            f"yahoo:a{cel_id}",
            "Yahoo",
            cel_id,
            T,
            T,
        )
    db.run(
        "INSERT INTO listings (id, source, cel_id, title, url, yen, franchise, kind, first_seen, last_seen)"
        " VALUES ('mercari:m3', 'Mercari', 3, 'セル画', 'https://example.invalid/m', 99000, 'eva', 'cel', ?, ?)",
        T,
        T,
    )
    return s, db
