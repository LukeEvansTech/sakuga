"""Pages render, and the sweep's alert and retirement rules hold, on a small catalogue."""

import dataclasses
import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sakuga import config, notify, sweep
from sakuga.db import DB
from sakuga.llm import LLM
from sakuga.sources import Raw
from sakuga.web import create_app

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


def test_retire_rules(env):
    _, db = env
    old = "2026-01-01 09:00:00"
    db.run("UPDATE listings SET source = 'Mandarake' WHERE id = 'yahoo:a2'")
    db.run("UPDATE listings SET last_seen = ? WHERE id IN ('yahoo:a1', 'mercari:m3')", old)
    assert sweep.to_check(db) == ["https://example.invalid/m"]
    # Mercari m3 is stale but its page says it is still for sale: kept, and seen again.
    sweep.retire(db, seen=set(), failed=set(), checked={"https://example.invalid/m": True})
    active = {r["id"] for r in db.all("SELECT id FROM listings WHERE active = 1")}
    # a1: Yahoo unseen for months: gone. a2: Mandarake (read whole), unseen: gone.
    # a3: Yahoo, seen an hour ago, may just have left the search window: kept.
    assert active == {"yahoo:a3", "mercari:m3"}
    assert sweep.to_check(db) == []
    avail = {r["id"]: r["available"] for r in db.all("SELECT id, available FROM cels")}
    assert avail == {1: 0, 2: 0, 3: 1}


def test_retire_needs_complete_reads_and_mercari_proof(env):
    _, db = env
    db.run("UPDATE listings SET source = 'Mandarake' WHERE id = 'yahoo:a2'")
    db.run("UPDATE listings SET last_seen = '2026-01-01 09:00:00' WHERE id = 'mercari:m3'")
    sweep.retire(db, seen=set(), failed=set(), partial={"Mandarake"}, checked={})
    active = {r["id"] for r in db.all("SELECT id FROM listings WHERE active = 1")}
    assert {"yahoo:a2", "mercari:m3"} <= active  # partial read; Mercari page not checked
    db.run("UPDATE listings SET last_seen = '2026-01-01 09:00:00' WHERE id = 'yahoo:a2'")
    sweep.retire(db, seen=set(), failed=set(), partial={"Mandarake"}, checked={})
    assert db.one("SELECT active FROM listings WHERE id = 'yahoo:a2'")["active"] == 1  # not by age
    sweep.retire(db, seen=set(), failed=set(), checked={"https://example.invalid/m": None})
    assert db.one("SELECT checked FROM listings WHERE id = 'mercari:m3'")["checked"]  # rotates
    sweep.retire(db, seen=set(), failed={"Mercari"}, checked={"https://example.invalid/m": False})
    assert (
        db.one("SELECT active FROM listings WHERE id = 'mercari:m3'")["active"] == 1
    )  # site failed
    sweep.retire(db, seen=set(), failed=set(), checked={"https://example.invalid/m": False})
    assert db.one("SELECT active FROM listings WHERE id = 'mercari:m3'")["active"] == 0  # sold


def test_digest_off_keeps_cels_pending(env):
    s, db = env
    assert sweep.digest(db, s) == "off"  # no APPRISE_URLS
    assert len(sweep.pending(db)) == 2


def test_instant_alert_retries_and_skips_digest(env, monkeypatch):
    s, db = env
    db.run("UPDATE cels SET gainax = 1 WHERE id = 2")
    monkeypatch.setattr(notify, "send", lambda *a, **k: "failed: down")
    sweep.instant(db, s)
    assert db.one("SELECT instant_sent FROM cels WHERE id = 2")["instant_sent"] is None
    monkeypatch.setattr(notify, "send", lambda *a, **k: "sent")
    assert sweep.instant(db, s) == ["cel 2: sent"]  # retried on the next sweep
    assert [c["id"] for c in sweep.pending(db)] == [3]  # announced once, not again in the digest
    assert {c["id"] for c in sweep.new_since_digest(db)} == {2, 3}  # still on Today
    db.run("INSERT INTO digests (created, cels, result) VALUES ('2999-01-01 00:00:00', 1, 'sent')")
    assert {c["id"] for c in sweep.new_since_digest(db)} == {3}  # gone after the next digest
    assert not sweep.instant(db, s)


def test_interrupted_run_is_closed_on_start(env):
    s, db = env
    db.run("INSERT INTO runs (started, status) VALUES (?, 'running')", T)
    db2 = DB(s.db_path)
    assert db2.one("SELECT status FROM runs")["status"] == "interrupted"


def test_late_photo_folds_duplicate_into_known_cel(env):
    s, db = env
    h = "0f0f0f0f0f0f0f0f"
    llm = LLM("", "", "claude")
    # cel 1 (baseline) is known by a listing photo only; cel 2's photo arrives late and matches it
    db.run("UPDATE listings SET phash = ? WHERE id = 'yahoo:a1'", h)
    raw2 = Raw("yahoo:a2", "Yahoo", "セル画", "https://example.invalid/x")
    sweep.regroup(db, llm, s, raw2, 2, "b.jpg", h)
    assert db.one("SELECT cel_id FROM listings WHERE id = 'yahoo:a2'")["cel_id"] == 1
    assert db.one("SELECT id FROM cels WHERE id = 2") is None
    assert db.one("SELECT baseline FROM cels WHERE id = 1")["baseline"] == 1
    raw3 = Raw("mercari:m3", "Mercari", "セル画", "https://example.invalid/m")
    sweep.regroup(db, llm, s, raw3, 3, "c.jpg", h)
    assert db.one("SELECT id FROM cels WHERE id = 3")  # two listings on cel 3: left alone


def test_pound_prices_meet_the_minimum(env):
    s, db = env
    cheap = Raw("mercari:x1", "Mercari", "ポケモン セル画", "https://example.invalid/x", gbp=5.0)
    got = sweep.ingest(db, s, LLM("", "", "claude"), [cheap], 200.0, False)
    assert got["seen"] == set()  # ¥1,000 is under the ¥3,000 fixed-price floor


def test_known_listing_cut_below_the_floor_stays(env):
    s, db = env
    db.run("UPDATE listings SET source = 'Mandarake' WHERE id = 'yahoo:a2'")
    cut = Raw("yahoo:a2", "Mandarake", "ポケモン セル画", "https://example.invalid/x", yen=2500)
    got = sweep.ingest(db, s, LLM("", "", "claude"), [cut], 200.0, False)
    assert got["seen"] == {"yahoo:a2"}
    assert db.one("SELECT yen FROM listings WHERE id = 'yahoo:a2'")["yen"] == 2500


class _Says(LLM):
    def __init__(self, verdict):
        super().__init__("", "", "claude")
        self.verdict = verdict

    def classify(self, title, extra, price, image):
        return self.verdict


def test_backfill_drops_cel_club_and_fills_titles(env):
    s, db = env
    db.run("UPDATE listings SET image = 'a.jpg' WHERE id IN ('yahoo:a1', 'yahoo:a2')")
    db.run("UPDATE listings SET first_seen = '2099-01-01 00:00:00' WHERE id = 'yahoo:a2'")
    club = _Says({"kind": "cel", "cel_club": True})
    assert sweep.backfill(db, club, s, limit=1) == 1  # newest first: yahoo:a2
    gone = db.one("SELECT ok, reason, cel_id FROM listings WHERE id = 'yahoo:a2'")
    assert (gone["ok"], gone["reason"]) == (0, "LLM: Cel Club")
    assert db.one("SELECT rejected FROM cels WHERE id = 2")["rejected"] == 1  # hidden, id kept
    assert all(c["id"] != 2 for c in sweep.pending(db))
    named = _Says({"kind": "cel", "title_en": "Rei in the entry plug", "notes": "no douga"})
    assert sweep.backfill(db, named, s) == 1  # yahoo:a1; answered listings are not asked again
    assert sweep.backfill(db, named, s) == 0
    cel = db.one("SELECT title_en, notes FROM cels WHERE id = 1")
    assert (cel["title_en"], cel["notes"]) == ("Rei Ayanami 1", "no douga")  # fills blanks only


def test_backfill_rotates_failures_and_applies_gainax(env):
    s, db = env
    db.run("UPDATE listings SET image = 'a.jpg'")
    assert sweep.backfill(db, _Says(None), s, limit=2) == 0
    tried = {r["id"] for r in db.all("SELECT id FROM listings WHERE asked IS NOT NULL")}
    assert len(tried) == 2
    sweep.backfill(db, _Says(None), s, limit=2)
    assert (
        db.one("SELECT COUNT(*) AS n FROM listings WHERE asked IS NULL")["n"] == 0
    )  # the rest got a turn
    gx = _Says({"kind": "reproduction", "gainax_limited": True, "title_en": "Kaworu 04/100"})
    sweep.backfill(db, gx, s, limit=4)
    assert db.one("SELECT gainax, kind FROM cels WHERE id = 3")["gainax"] == 1


def test_relist_of_rejected_artwork_stays_out_when_the_llm_fails(env, monkeypatch):
    s, db = env
    h = "0f0f0f0f0f0f0f0f"
    db.run(
        "UPDATE listings SET image = 'a.jpg', phash = ?, first_seen = '2099-01-01 00:00:00'"
        " WHERE id = 'yahoo:a2'",
        h,
    )
    sweep.backfill(db, _Says({"kind": "cel", "cel_club": True}), s, limit=1)
    monkeypatch.setattr(sweep.images, "download", lambda *a, **k: ("r.jpg", h))
    relist = Raw(
        "mercari:r1",
        "Mercari",
        "エヴァンゲリオン セル画 綾波レイ",
        "https://example.invalid/r",
        yen=9000,
    )
    got = sweep.ingest(db, s, _Says(None), [relist], 200.0, False)
    assert not got["new_cels"]
    row = db.one("SELECT ok, reason FROM listings WHERE id = 'mercari:r1'")
    assert (row["ok"], row["reason"]) == (0, "same photo as rejected yahoo:a2")


def test_accepted_relist_brings_a_hidden_cel_back(env, monkeypatch):
    s, db = env
    h = "0f0f0f0f0f0f0f0f"
    db.run("UPDATE listings SET image = 'a.jpg', phash = ? WHERE id = 'yahoo:a2'", h)
    db.run("UPDATE cels SET phash = ? WHERE id = 2", h)
    sweep.backfill(db, _Says({"kind": "print"}), s, limit=3)
    assert db.one("SELECT rejected FROM cels WHERE id = 2")["rejected"] == 1
    monkeypatch.setattr(sweep.images, "download", lambda *a, **k: ("r.jpg", h))
    relist = Raw(
        "mercari:r2",
        "Mercari",
        "エヴァンゲリオン セル画 綾波レイ",
        "https://example.invalid/r",
        yen=9000,
    )
    sweep.ingest(db, s, _Says({"kind": "cel"}), [relist], 200.0, False)
    assert db.one("SELECT cel_id FROM listings WHERE id = 'mercari:r2'")["cel_id"] == 2
    assert db.one("SELECT rejected FROM cels WHERE id = 2")["rejected"] == 0


def test_hidden_state_is_derived_from_sightings(env):
    _, db = env
    db.run("UPDATE listings SET ok = 0, verdict = '{}' WHERE id = 'yahoo:a1'")
    db.run("UPDATE cels SET rejected = 1 WHERE id = 3")  # stale: its listings are accepted
    sweep.reconcile_hidden(db)
    got = {r["id"]: r["rejected"] for r in db.all("SELECT id, rejected FROM cels")}
    assert got == {1: 1, 2: 0, 3: 0}


def test_a_new_source_starts_with_a_quiet_read(env, monkeypatch):
    s, db = env
    db.put("baselined", True)  # an existing catalogue, from before eBay
    first = Raw(
        "ebay:1",
        "eBay",
        "Evangelion Rei production cel",
        "https://example.invalid/e",
        gbp=100.0,
        lang="en",
    )
    later = Raw(
        "ebay:2",
        "eBay",
        "Evangelion Asuka production cel",
        "https://example.invalid/f",
        gbp=100.0,
        lang="en",
    )
    ys = Raw(
        "yahoo:n1",
        "Yahoo",
        "エヴァンゲリオン セル画 アスカ",
        "https://example.invalid/y",
        yen=9000,
        auction=True,
    )
    rounds = iter([[first, ys], [later]])
    monkeypatch.setattr(sweep, "collect", lambda *a, **k: (next(rounds), set(), set(), {}))
    monkeypatch.setattr(sweep, "to_check", lambda db: [])
    monkeypatch.setattr(sweep.images, "download", lambda *a, **k: ("", ""))
    sweep.run(db, s, notify_on=False, llm=LLM("", "", "claude"))
    base = {
        r["title_jp"][:3]: r["baseline"]
        for r in db.all("SELECT title_jp, baseline FROM cels WHERE id > 3")
    }
    assert base == {"Eva": 1, "エヴァ": 0}  # eBay's first read is quiet; Yahoo's news is not
    sweep.run(db, s, notify_on=False, llm=LLM("", "", "claude"))
    assert db.one("SELECT baseline FROM cels WHERE title_jp LIKE 'Evangelion A%'")["baseline"] == 0
