"""The daily email: what it covers, when its start point moves, and how it joins the digest."""

import dataclasses
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sakuga import mail, notify, sweep
from sakuga.web import create_app

from tests.test_web import T, env  # noqa: F401  pylint: disable=unused-import


class FakeSMTP:
    sent: list = []
    fail = False

    def __init__(self, host, port, timeout=0):
        self.host = host

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
def mailed(env, monkeypatch):  # noqa: F811  pylint: disable=redefined-outer-name
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


def test_off_until_configured(env):  # noqa: F811  pylint: disable=redefined-outer-name
    s, db = env
    assert not s.mail_on
    assert mail.run(db, s) == "off"
    assert sweep.digest(db, s) == "off"  # no Pushover either: the cels stay pending
    assert len(sweep.pending(db)) == 2


def test_email_alone_delivers_the_digest(mailed):  # pylint: disable=redefined-outer-name
    s, db = mailed
    img = io.BytesIO()
    Image.new("RGB", (800, 600), "white").save(img, "JPEG")
    (s.img_dir / ("a" * 24 + ".jpg")).write_bytes(img.getvalue())
    db.run("UPDATE cels SET image = ? WHERE id = 2", "a" * 24 + ".jpg")

    assert sweep.digest(db, s) == "sent"
    assert sweep.pending(db) == []  # marked: the email delivered them
    msg = FakeSMTP.sent[-1]
    assert msg["Subject"] == "Sakuga: 2 new cels (2 Eva)"
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
    assert FakeSMTP.sent[-1]["Subject"] == "Sakuga: no new cels, 3 for sale"


def test_failed_email_keeps_its_start(mailed, monkeypatch):  # pylint: disable=redefined-outer-name
    s, db = mailed
    FakeSMTP.fail = True
    assert sweep.digest(db, s) == "failed: OSError"
    assert len(sweep.pending(db)) == 2
    assert db.get(mail.LAST_CEL, None) is None

    # Pushover delivers while the relay is down: the cels are digested, and still emailed later.
    s2 = dataclasses.replace(s, apprise_urls="json://localhost")
    monkeypatch.setattr(notify, "send", lambda *a, **k: "sent")
    assert sweep.digest(db, s2) == "sent"
    assert sweep.pending(db) == []
    FakeSMTP.fail = False
    assert mail.run(db, s) == "sent"
    assert FakeSMTP.sent[-1]["Subject"].startswith("Sakuga: 2 new cels")


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


def test_web_copy(mailed):  # pylint: disable=redefined-outer-name
    s, db = mailed
    c = TestClient(create_app(s, db))
    assert c.get("/digest/last").status_code == 404
    assert "Not sent yet" in c.get("/log").text
    mail.run(db, s)
    r = c.get("/digest/last")
    assert r.status_code == 200 and "Rei Ayanami 2" in r.text and "cid:" not in r.text
    assert "last one" in c.get("/log").text
