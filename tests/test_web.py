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

    def classify(self, title, extra, price, photos):
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


def _fake_collect(rounds):
    """collect() as a sweep sees it: every search recorded as read, and these listings."""

    def collect(s, db, run_id, _check=None):
        for name, _, _ in sweep.search_jobs(s):
            db.run("INSERT INTO run_sources (run_id, name, count) VALUES (?, ?, 0)", run_id, name)
        return next(rounds), set(), set(), {}

    return collect


def test_a_new_source_starts_with_a_quiet_read(env, monkeypatch):
    s, db = env
    s = dataclasses.replace(s, ebay_client_id="id", ebay_client_secret="secret")
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
    monkeypatch.setattr(sweep, "collect", _fake_collect(iter([[first, ys], [later]])))
    monkeypatch.setattr(sweep, "to_check", lambda db: [])
    monkeypatch.setattr(sweep.images, "download", lambda *a, **k: ("", ""))
    sweep.run(db, s, notify_on=False, llm=LLM("", "", "claude"))
    base = {
        r["title_jp"][:3]: r["baseline"]
        for r in db.all("SELECT title_jp, baseline FROM cels WHERE id > 3")
    }
    assert base == {"Eva": 1, "エヴァ": 0}  # eBay's first read is quiet; Yahoo's news is not
    assert "eBay" in db.get("baselined_sources", [])
    sweep.run(db, s, notify_on=False, llm=LLM("", "", "claude"))
    assert db.one("SELECT baseline FROM cels WHERE title_jp LIKE 'Evangelion A%'")["baseline"] == 0


def test_an_empty_first_read_still_baselines(env, monkeypatch):
    s, db = env
    s = dataclasses.replace(s, ebay_client_id="id", ebay_client_secret="secret")
    db.put("baselined", True)
    monkeypatch.setattr(sweep, "collect", _fake_collect(iter([[]])))
    monkeypatch.setattr(sweep, "to_check", lambda db: [])
    sweep.run(db, s, notify_on=False, llm=LLM("", "", "claude"))
    assert "eBay" in db.get("baselined_sources", [])


def test_unsearched_source_keeps_its_listings(env, monkeypatch):
    s, db = env  # no eBay keys: eBay is not searched this sweep
    db.put("baselined", True)
    db.run(
        "INSERT INTO listings (id, source, cel_id, title, url, franchise, kind, first_seen, last_seen)"
        " VALUES ('ebay:9', 'eBay', 1, 'Evangelion cel', 'https://example.invalid/e', 'eva', 'cel', ?, ?)",
        T,
        T,
    )
    monkeypatch.setattr(sweep, "collect", _fake_collect(iter([[]])))
    monkeypatch.setattr(sweep, "to_check", lambda db: [])
    sweep.run(db, s, notify_on=False, llm=LLM("", "", "claude"))
    assert db.one("SELECT active FROM listings WHERE id = 'ebay:9'")["active"] == 1


def test_one_failed_search_does_not_silence_a_source(env, monkeypatch):
    s, db = env  # a fresh catalogue whose first sweep loses one Yahoo search

    def collect(s, db, run_id, _check=None):
        for i, (name, _, _) in enumerate(sweep.search_jobs(s)):
            db.run(
                "INSERT INTO run_sources (run_id, name, count) VALUES (?, ?, ?)",
                run_id,
                name,
                None if i == 0 else 0,
            )
        return [], {"Yahoo"}, set(), {}

    monkeypatch.setattr(sweep, "collect", collect)
    monkeypatch.setattr(sweep, "to_check", lambda db: [])
    db.run("DELETE FROM kv")
    sweep.run(db, s, notify_on=False, llm=LLM("", "", "claude"))
    assert "Yahoo" in db.get("baselined_sources", [])


class _Seen(LLM):
    """Records the photos each classify call was given."""

    def __init__(self, verdict):
        super().__init__("", "", "claude")
        self.verdict, self.calls = verdict, []

    def classify(self, title, extra, price, photos):
        self.calls.append([p.name for p in photos])
        return self.verdict


def _fake_photos(monkeypatch, names):
    """Photos by URL: a name downloads, '' is gone for good, None fails this time."""
    monkeypatch.setattr(
        sweep.images, "download", lambda url, *_a, **_k: (names.get(url) or "", "0f0f0f0f0f0f0f0f")
    )
    monkeypatch.setattr(sweep.images, "download_more", lambda url, *_a, **_k: names.get(url, ""))


def test_a_new_listing_is_judged_on_its_further_photos(env, monkeypatch):
    s, db = env
    _fake_photos(monkeypatch, {"u1": "main.jpg", "u2": "two.jpg", "u3": "seal.jpg"})
    raw = Raw(
        "mercari:m9",
        "Mercari",
        "エヴァンゲリオン セル画 碇シンジ",
        "https://example.invalid/m9",
        img_url="u1",
        yen=3500,
        photos=["u2", "u3", "u4"],  # u4 does not download
    )
    llm = _Seen({"kind": "cel", "cel_club": True})
    sweep.ingest(db, s, llm, [raw], 200.0, False)
    assert llm.calls == [["main.jpg", "two.jpg", "seal.jpg"]]
    row = db.one("SELECT ok, reason, photos FROM listings WHERE id = 'mercari:m9'")
    assert (row["ok"], row["reason"]) == (0, "LLM: Cel Club")
    assert row["photos"] == '["two.jpg", "seal.jpg"]'


def test_a_kept_listing_is_asked_again_once_its_photos_arrive(env, monkeypatch):
    s, db = env
    db.run("UPDATE listings SET image = 'a.jpg', verdict = '{\"kind\": \"cel\"}'")
    _fake_photos(monkeypatch, {"p2": "seal.jpg", "p4": None})
    db.run(
        "INSERT INTO listings (id, source, cel_id, title, url, franchise, kind, first_seen, last_seen,"
        " image) VALUES ('yahoo:a4', 'Yahoo', 3, 'セル画', 'https://x.invalid', 'eva', 'cel', ?, ?, '')",
        T,
        T,
    )
    again = [
        Raw("yahoo:a4", "Yahoo", "セル画", "https://example.invalid/x", photos=["p2", "p4"]),
        Raw("yahoo:a2", "Yahoo", "セル画", "https://example.invalid/x", yen=120000, photos=["p2"]),
        Raw("yahoo:a1", "Yahoo", "セル画", "https://example.invalid/x", yen=120000, photos=[]),
        Raw("yahoo:a3", "Yahoo", "セル画", "https://example.invalid/x", yen=120000),  # not gathered
    ]
    sweep.ingest(db, s, _Seen(None), again, 200.0, False)
    rows = db.all("SELECT id, photos, recheck, photos_done FROM listings")
    got = {r["id"]: (r["photos"], r["recheck"], r["photos_done"]) for r in rows}
    assert got["yahoo:a2"] == ('["seal.jpg"]', 1, 1)
    assert got["yahoo:a1"] == ("[]", 0, 1)  # nothing more to see: no second call
    assert got["yahoo:a3"] == (None, 0, 0)  # gathered next sweep
    assert got["yahoo:a4"] == ('["seal.jpg"]', 1, 0)  # seen now; p4 failed and is fetched again
    _fake_photos(monkeypatch, {"p2": "seal.jpg", "p4": "back.jpg"})
    sweep.ingest(db, s, _Seen(None), again[:1], 200.0, False)
    row = db.one("SELECT photos, photos_done FROM listings WHERE id = 'yahoo:a4'")
    assert (row["photos"], row["photos_done"]) == ('["seal.jpg", "back.jpg"]', 1)
    llm = _Seen({"kind": "cel", "cel_club": True})
    assert sweep.backfill(db, llm, s) == 1  # yahoo:a4 waits for its main photo
    assert llm.calls == [["a.jpg", "seal.jpg"]]
    row = db.one("SELECT ok, recheck FROM listings WHERE id = 'yahoo:a2'")
    assert (row["ok"], row["recheck"]) == (0, 0)
    assert db.one("SELECT rejected FROM cels WHERE id = 2")["rejected"] == 1
    assert sweep.backfill(db, llm, s) == 0  # asked once


def test_photos_are_gathered_only_for_listings_the_llm_will_read(env):
    _, db = env
    db.run("UPDATE listings SET photos = '[]' WHERE id = 'yahoo:a1'")

    class Page:
        opened: list = []

    raws = [
        Raw("mercari:m77", "Mercari", "エヴァンゲリオン セル画", "https://x.invalid", yen=9000),
        Raw("mercari:m78", "Mercari", "エヴァンゲリオン ポスター", "https://x.invalid", yen=9000),
        Raw("yahoo:a1", "Yahoo", "セル画", "https://x.invalid"),  # photos already stored
        Raw("mercari:m3", "Mercari", "セル画", "https://x.invalid"),  # kept, never gathered
    ]
    sweep.gather_photos(Page(), db, raws)
    got = {r.id: r.photos for r in raws}
    assert got["mercari:m77"][0].endswith("/m77_2.jpg") and len(got["mercari:m77"]) == 2
    assert got["mercari:m78"] is None and got["yahoo:a1"] is None
    assert got["mercari:m3"][1].endswith("/m3_3.jpg")


def test_item_pages_go_to_new_listings_then_the_longest_waiting(env, monkeypatch):
    _, db = env
    monkeypatch.setattr(sweep, "MAX_PHOTO_PAGES", 2)
    opened = []
    monkeypatch.setattr(sweep, "more_photos", lambda _p, raw: opened.append(raw.id) or [])
    db.run("UPDATE listings SET photos_tried = '2026-01-02 00:00:00' WHERE id = 'yahoo:a1'")
    db.run("UPDATE listings SET photos_tried = '2026-01-01 00:00:00' WHERE id = 'yahoo:a2'")
    raws = [
        Raw("yahoo:a1", "Yahoo", "セル画", "https://x.invalid"),
        Raw("yahoo:a2", "Yahoo", "セル画", "https://x.invalid"),
        Raw("yahoo:new", "Yahoo", "エヴァンゲリオン セル画", "https://x.invalid", yen=9000),
    ]
    sweep.gather_photos(None, db, raws)
    assert opened == ["yahoo:new", "yahoo:a2"]  # yahoo:a1 had its turn most recently
    tried = db.one("SELECT photos_tried FROM listings WHERE id = 'yahoo:a2'")["photos_tried"]
    assert tried > "2026-01-01 00:00:00"


def test_further_photos_wait_for_the_main_one(env, monkeypatch):
    s, db = env
    _fake_photos(monkeypatch, {"u2": "seal.jpg"})  # the main photo u1 fails
    raw = Raw(
        "mercari:m10",
        "Mercari",
        "エヴァンゲリオン セル画 碇シンジ",
        "https://example.invalid/m10",
        img_url="u1",
        yen=3500,
        photos=["u2"],
    )
    llm = _Seen({"kind": "cel"})
    sweep.ingest(db, s, llm, [raw], 200.0, False)
    assert llm.calls == [[]]  # not the seal alone
    assert db.one("SELECT recheck FROM listings WHERE id = 'mercari:m10'")["recheck"] == 1
    assert sweep.backfill(db, llm, s) == 0  # waits for the main photo
    _fake_photos(monkeypatch, {"u1": "main.jpg", "u2": "seal.jpg"})
    sweep.ingest(db, s, llm, [raw], 200.0, False)  # the main photo arrives
    sweep.backfill(db, llm, s)
    assert ["main.jpg", "seal.jpg"] in llm.calls


def test_hinted_listing_waits_for_the_llm_and_misfiled_ones_drop(env, monkeypatch):
    s, db = env
    monkeypatch.setattr(sweep.images, "download", lambda *a, **k: ("h.jpg", "0f0f0f0f0f0f0f0f"))
    typo = Raw("yahoo:o1", "Yahoo", "エゥンゲリオン セル画", "https://example.invalid/o", yen=60000)
    typo = dataclasses.replace(typo, cel_category=True, auction=True, hint="eva")
    sweep.ingest(db, s, _Says(None), [typo], 200.0, False)
    assert db.one("SELECT id FROM listings WHERE id = 'yahoo:o1'") is None  # asked again next sweep
    misfiled = dataclasses.replace(typo, id="yahoo:o2", title="王立宇宙軍 オネアミスの翼 セル画")
    sweep.ingest(db, s, _Says({"kind": "cel", "franchise": "other"}), [misfiled], 200.0, False)
    row = db.one("SELECT ok, reason FROM listings WHERE id = 'yahoo:o2'")
    assert (row["ok"], row["reason"]) == (0, "LLM: not Eva, though filed there")
    got = sweep.ingest(db, s, _Says({"kind": "cel", "franchise": "eva"}), [typo], 200.0, False)
    assert got["new_cels"] and db.one("SELECT ok FROM listings WHERE id = 'yahoo:o1'")["ok"] == 1


def test_a_new_franchise_reads_quietly_first(env, monkeypatch):
    s, db = env
    monkeypatch.setattr(sweep.images, "download", lambda *a, **k: ("k.jpg", "f0f0f0f0f0f0f0f0"))
    kiki = Raw(
        "yahoo:k1", "Yahoo", "魔女の宅急便 キキ セル画", "https://example.invalid/k", yen=50000
    )
    got = sweep.ingest(
        db, s, LLM("", "", "claude"), [kiki], 200.0, False, quiet_franchises={"kiki"}
    )
    assert not got["new_cels"]
    assert db.one("SELECT baseline FROM cels WHERE franchise = 'kiki'")["baseline"] == 1


def test_a_franchise_is_baselined_once_all_its_searches_read(env, monkeypatch):
    s, db = env
    db.put("baselined", True)
    db.put("baselined_sources", list(sweep.ALL_SOURCES))
    db.put("baselined_franchises", ["broly", "fma", "eva", "pokemon"])
    monkeypatch.setattr(sweep, "backfill", lambda *a, **k: 0)
    failing = {"mercari:魔女の宅急便 セル画"}
    capped = {"buyee:悟空", "mandarake-shop"}  # Mandarake at its cap still counts as read

    def collect(_s, db_, run_id, _check=None):
        for name, _, _ in sweep.search_jobs(_s):
            ok = name not in failing
            db_.run(
                "INSERT INTO run_sources (run_id, name, count, error, partial) VALUES (?,?,?,?,?)",
                run_id,
                name,
                0 if ok else None,
                None if ok else "down",
                int(name in capped),
            )
        return [], set(), set(), {}

    monkeypatch.setattr(sweep, "collect", collect)
    sweep.run(db, s, notify_on=False, llm=LLM("", "", "claude"))
    assert "kiki" not in db.get("baselined_franchises", [])  # one of its searches failed
    assert "bebop" in db.get("baselined_franchises", [])
    assert "goku" not in db.get("baselined_franchises", [])  # stopped at its page cap
    failing.clear()
    failing.add("buyee:魔女の宅急便")  # Kiki's Mercari read last sweep; its Yahoo search fails now
    capped.clear()
    sweep.run(db, s, notify_on=False, llm=LLM("", "", "claude"))
    assert set(db.get("baselined_franchises", [])) == set(sweep.FRANCHISES)


def test_hinted_listing_needs_a_photo_and_keeps_its_franchise_quiet(env, monkeypatch):
    s, db = env
    monkeypatch.setattr(sweep.images, "download", lambda *a, **k: ("", ""))
    typo = Raw("yahoo:o3", "Yahoo", "エゥンゲリオン セル画", "https://example.invalid/o", yen=60000)
    typo = dataclasses.replace(typo, cel_category=True, auction=True, hint="kiki")
    say = _Says({"kind": "cel", "franchise": "kiki"})
    sweep.ingest(db, s, say, [typo], 200.0, False, quiet_franchises={"kiki"})
    assert db.one("SELECT id FROM listings WHERE id = 'yahoo:o3'") is None  # no photo: no verdict
    assert db.get(sweep.QUIET_HELD, []) == ["yahoo:o3"]
    # the photo arrives after Kiki finished its first read: the cel is still not news
    monkeypatch.setattr(sweep.images, "download", lambda *a, **k: ("o.jpg", "0f0f0f0f0f0f0f0f"))
    got = sweep.ingest(db, s, say, [typo], 200.0, False)
    assert not got["new_cels"] and db.get(sweep.QUIET_HELD, None) == []
    assert db.one("SELECT baseline FROM cels WHERE franchise = 'kiki'")["baseline"] == 1


def test_dragon_ball_relist_under_another_character_joins_the_cel(env, monkeypatch):
    s, db = env
    monkeypatch.setattr(sweep.images, "download", lambda *a, **k: ("d.jpg", "00ff00ff00ff00ff"))
    both = Raw(
        "yahoo:d1", "Yahoo", "ドラゴンボール 孫悟空 ベジータ セル画", "https://example.invalid/d"
    )
    one = Raw("mercari:d2", "Mercari", "ドラゴンボール 孫悟空 セル画", "https://example.invalid/e")
    llm = LLM("", "", "claude")
    first = sweep.ingest(db, s, llm, [dataclasses.replace(both, yen=90000)], 200.0, False)
    again = sweep.ingest(db, s, llm, [dataclasses.replace(one, yen=90000)], 200.0, False)
    assert first["new_cels"] and not again["new_cels"]  # filed as Vegeta, then as Goku: one cel


def test_backfill_keeps_the_category_rule(env):
    s, db = env
    db.run("UPDATE listings SET image = 'a.jpg', hinted = 1, verdict = '' WHERE id = 'yahoo:a2'")
    db.run("UPDATE listings SET first_seen = '2099-01-01 00:00:00' WHERE id = 'yahoo:a2'")
    sweep.backfill(db, _Says({"kind": "cel", "franchise": "other"}), s, limit=1)
    row = db.one("SELECT ok, reason FROM listings WHERE id = 'yahoo:a2'")
    assert (row["ok"], row["reason"]) == (0, "LLM: not Eva, though filed there")
