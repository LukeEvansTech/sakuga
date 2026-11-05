"""Pages render, and the sweep's alert and retirement rules hold, on a small catalogue."""

import dataclasses

import pytest
from fastapi.testclient import TestClient
from sakuga import config, notify, sweep
from sakuga.db import DB
from sakuga.web import create_app

T = "2026-11-05 09:00:00"


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


def test_pages_render(env):
    s, db = env
    c = TestClient(create_app(s, db))
    for path in (
        "/today",
        "/catalogue",
        "/catalogue?franchise=eva&sort=price&show=all",
        "/cel/2",
        "/log",
    ):
        r = c.get(path)
        assert r.status_code == 200, path
    assert "Rei Ayanami 2" in c.get("/today").text
    assert "Rei Ayanami 1" not in c.get("/today").text  # the baseline is not news
    assert c.get("/cel/99").status_code == 404
    assert c.get("/img/../sakuga.db").status_code == 404
    m = c.get("/metrics").text
    assert "sakuga_last_success_timestamp_seconds 0" in m and "sakuga_cels 3" in m


def test_cross_site_post_refused(env):
    s, db = env
    c = TestClient(create_app(s, db))
    assert c.post("/sweep", headers={"origin": "https://evil.example"}).status_code == 403


def test_digest_marks_cels_only_after_send(env, monkeypatch):
    s, db = env
    s = dataclasses.replace(s, apprise_urls="json://localhost")
    monkeypatch.setattr(notify, "send", lambda *a, **k: "failed: down")
    assert sweep.digest(db, s).startswith("failed")
    assert len(sweep.pending(db)) == 2
    sent = []
    monkeypatch.setattr(
        notify, "send", lambda u, title, body, link, lt: sent.append((title, link)) or "sent"
    )
    assert sweep.digest(db, s) == "sent"
    assert sent[0][0] == "2 new cels today" and sent[0][1].endswith("/today")
    assert sweep.pending(db) == []
    assert sweep.digest(db, s) == "nothing new"


def test_failed_site_is_not_retired(env):
    _, db = env
    sweep.retire(db, seen={"yahoo:a1"}, failed={"Mercari"})
    active = {r["id"] for r in db.all("SELECT id FROM listings WHERE active = 1")}
    assert active == {"yahoo:a1", "mercari:m3"}
    avail = {r["id"]: r["available"] for r in db.all("SELECT id, available FROM cels")}
    assert avail == {1: 1, 2: 0, 3: 1}  # cel 3 still on Mercari, whose search failed
