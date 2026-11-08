"""The daily email: what it covers, when its start point moves, and how it joins the digest."""

import dataclasses
import datetime as dt
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sakuga import mail, notify, sweep
from sakuga.web import create_app

T = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")


class FakeSMTP:
    sent: list = []
    fail = False

    def __init__(self, host, port, timeout=0):
        self.host, self.port, self.timeout = host, port, timeout

    def __enter__(self):
        if FakeSMTP.fail:
            raise OSError("relay down")
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        pass

    def has_extn(self, _name):
        return False

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)
        return {}


@pytest.fixture
def mailed(env, monkeypatch):  # pylint: disable=redefined-outer-name
    s, db = env
    s = dataclasses.replace(
        s,
        smtp_host="smtp.example.invalid",
        mail_from="noreply@example.invalid",
        digest_to="me@example.invalid",
        public_url="https://sakuga.example.invalid",
    )
    FakeSMTP.sent, FakeSMTP.fail = [], False
    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    return s, db


def test_off_until_configured(env):  # pylint: disable=redefined-outer-name
    s, db = env
    assert not s.mail_on
    assert mail.run(db, s) == "off"
    assert sweep.digest(db, s) == "off"  # no Pushover either: the cels stay pending
    assert len(sweep.pending(db)) == 2


# pylint: disable-next=redefined-outer-name
def test_email_alone_delivers_the_digest(mailed, monkeypatch):
    s, db = mailed
    img = io.BytesIO()
    Image.new("RGB", (800, 600), "white").save(img, "JPEG")
    (s.img_dir / ("a" * 24 + ".jpg")).write_bytes(img.getvalue())
    db.run("UPDATE cels SET image = ? WHERE id = 2", "a" * 24 + ".jpg")

    real_run = mail.run

    def late_cel(db_, s_):  # a sweep adds a cel between pending() and the email
        db_.run(
            "INSERT INTO cels (id, franchise, kind, title_en, first_seen, last_seen)"
            " VALUES (9, 'eva', 'cel', 'Late', ?, ?)",
            T,
            T,
        )
        return real_run(db_, s_)

    monkeypatch.setattr(mail, "run", late_cel)
    assert sweep.digest(db, s) == "sent"
    assert sweep.pending(db) == []  # marked: the email delivered them, the late one too
    monkeypatch.setattr(mail, "run", real_run)
    msg = FakeSMTP.sent[-1]
    assert msg["Subject"] == "Sakuga: 3 new cels (3 Eva)"
    html = msg.get_body(("html",)).get_content()
    assert "Rei Ayanami 2" in html and "Rei Ayanami 3" in html
    assert "Rei Ayanami 1" not in html  # the first sweep's catalogue is not news
    assert "https://sakuga.example.invalid/cel/2" in html
    assert "cid:" in html  # the photo is attached, not linked
    inline = [p for p in msg.walk() if p.get_content_type() == "image/jpeg"]
    assert len(inline) == 1 and len(inline[0].get_content()) < 20_000  # a thumbnail
    row = db.one("SELECT result FROM digests")
    assert row["result"] == "push off, email sent"

    # The next morning: nothing new, but the email still goes, and says so.
    assert sweep.digest(db, s) == "nothing new"
    assert FakeSMTP.sent[-1]["Subject"] == "Sakuga: no new cels, 4 for sale"


def test_failed_email_keeps_its_start(mailed, monkeypatch):  # pylint: disable=redefined-outer-name
    s, db = mailed
    FakeSMTP.fail = True
    assert sweep.digest(db, s) == "failed: OSError"
    assert len(sweep.pending(db)) == 2
    assert db.one("SELECT COUNT(*) n FROM cels WHERE mailed IS NOT NULL")["n"] == 0

    # Pushover delivers while the relay is down: the cels are digested, and still emailed later.
    s2 = dataclasses.replace(s, apprise_urls="json://localhost")
    monkeypatch.setattr(notify, "send", lambda *a, **k: "sent")
    assert sweep.digest(db, s2) == "sent"
    assert sweep.pending(db) == []
    FakeSMTP.fail = False
    assert mail.run(db, s) == "sent"
    assert FakeSMTP.sent[-1]["Subject"].startswith("Sakuga: 2 new cels")

    # A quiet day whose email fails is a failure, not "nothing new".
    FakeSMTP.fail = True
    assert sweep.digest(db, s) == "failed: OSError"


def test_new_cel_after_an_email(mailed):  # pylint: disable=redefined-outer-name
    s, db = mailed
    assert mail.run(db, s) == "sent"
    db.run(
        "INSERT INTO cels (id, franchise, kind, title_en, title_jp, gainax, first_seen, last_seen)"
        " VALUES (4, 'eva', 'cel', 'Kaworu', 'カヲル', 1, ?, ?)",
        T,
        T,
    )
    g = mail.gather(db)
    assert [c["id"] for c in g["cels"]] == [4]
    # A cel GAINAX's instant alert announced is not news again; nor is a reused id's old cel.
    db.run("UPDATE cels SET instant_sent = ? WHERE id = 4", T)
    assert mail.gather(db)["cels"] == []
    db.run("DELETE FROM listings WHERE cel_id = 3")
    db.run("DELETE FROM cels WHERE id = 3")
    db.run(
        "INSERT INTO cels (id, franchise, kind, title_en, first_seen, last_seen)"
        " VALUES (3, 'eva', 'cel', 'Asuka', ?, ?)",
        T,
        T,
    )
    assert [c["id"] for c in mail.gather(db)["cels"]] == [3]


def test_migration_marks_what_pushover_announced(tmp_path):
    import sqlite3

    from sakuga.db import DB

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE cels (id INTEGER PRIMARY KEY, franchise TEXT NOT NULL, kind TEXT NOT NULL,"
        " title_en TEXT NOT NULL DEFAULT '', title_jp TEXT NOT NULL DEFAULT '',"
        " notes TEXT NOT NULL DEFAULT '', image TEXT NOT NULL DEFAULT '',"
        " phash TEXT NOT NULL DEFAULT '', gainax INTEGER NOT NULL DEFAULT 0,"
        " baseline INTEGER NOT NULL DEFAULT 0, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,"
        " available INTEGER NOT NULL DEFAULT 1, digest_id INTEGER, instant_sent TEXT,"
        " rejected INTEGER NOT NULL DEFAULT 0);"
        "INSERT INTO cels (id, franchise, kind, baseline, digest_id, first_seen, last_seen) VALUES"
        " (1, 'eva', 'cel', 1, NULL, 't', 't'), (2, 'eva', 'cel', 0, 5, 't', 't'),"
        " (3, 'eva', 'cel', 0, NULL, 't', 't');"
    )
    con.close()
    db = DB(path)
    assert [c["id"] for c in mail.gather(db)["cels"]] == [3]
    assert db.one("SELECT mailed FROM cels WHERE id = 1")["mailed"] is None  # can still regroup


def test_web_copy(mailed):  # pylint: disable=redefined-outer-name
    s, db = mailed
    c = TestClient(create_app(s, db))
    assert c.get("/digest/last").status_code == 404
    assert "Not sent yet" in c.get("/log").text
    mail.run(db, s)
    r = c.get("/digest/last")
    assert r.status_code == 200 and "Rei Ayanami 2" in r.text and "cid:" not in r.text
    assert "last one" in c.get("/log").text
