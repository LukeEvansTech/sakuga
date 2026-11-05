"""Catalogue rules: what to keep, and when a listing joins a cel already catalogued."""

import io

import numpy as np
from PIL import Image, ImageDraw
from sakuga.catalog import NEAR, Candidate, group, keep, title_sim
from sakuga.images import distance, phash


def drawing(seed: int) -> Image.Image:
    """A stand-in cel: random filled shapes on a light ground."""
    rng = np.random.default_rng(seed)
    img = Image.new("RGB", (480, 600), (238, 232, 220))
    d = ImageDraw.Draw(img)
    for _ in range(12):
        x, y = rng.integers(0, 400), rng.integers(0, 520)
        w, h = rng.integers(40, 200), rng.integers(40, 200)
        d.ellipse((x, y, x + w, y + h), fill=tuple(int(v) for v in rng.integers(0, 255, 3)))
    return img


def jpeg(img: Image.Image, q: int) -> Image.Image:
    b = io.BytesIO()
    img.save(b, "JPEG", quality=q)
    return Image.open(io.BytesIO(b.getvalue()))


def test_relisted_photo_is_near():
    a = drawing(1)
    h = phash(a)
    assert distance(h, phash(a.resize((240, 300)))) <= NEAR
    assert distance(h, phash(jpeg(a, 50))) <= NEAR


def test_different_cels_are_far():
    assert distance(phash(drawing(1)), phash(drawing(2))) > 20


def test_group_joins_near_and_asks_llm_in_between():
    asked = []

    def same(cel_id):
        asked.append(cel_id)
        return cel_id == 2

    h = phash(drawing(1))
    near = Candidate(1, h, "eva")
    assert group(h, "eva", [near]) == 1
    assert group(h, "pokemon", [near]) is None  # never across franchises
    flipped = f"{int(h, 16) ^ 0xFFF:016x}"  # 12 bits away: borderline
    assert group(h, "eva", [Candidate(2, flipped, "eva")], same) == 2 and asked == [2]
    assert group(h, "eva", [Candidate(3, flipped, "eva")], same) is None
    assert group(h, "eva", [Candidate(3, flipped, "eva")]) is None  # no LLM: a new cel


def test_same_title_far_photo_goes_to_llm():
    asked = []
    far = f"{int(phash(drawing(1)), 16) ^ (2**40 - 1):016x}"
    t = "新世紀エヴァンゲリオン　綾波レイ　セル画　動画付き　劇場版"
    group(phash(drawing(1)), "eva", [Candidate(5, far, "eva", t)], asked.append, t)
    assert asked == [5]
    assert title_sim(t, t) == 1.0 and title_sim(t, "ポケモン　ピカチュウ　セル画") < 0.3


def test_keep_rules():
    assert keep("eva", "cel", None).ok
    assert not keep("eva", "cel", {"kind": "print"}).ok
    assert not keep("eva", "cel", {"kind": "reproduction", "gainax_limited": False}).ok
    g = keep("eva", "gainax-limited", {"kind": "reproduction", "gainax_limited": True})
    assert g.ok and g.gainax and g.kind == "cel"
    # the title pattern said GAINAX; the LLM, seeing the photo, says an ordinary reproduction
    assert not keep("eva", "gainax-limited", {"kind": "reproduction", "gainax_limited": False}).ok
    assert not keep("broly", "cel", {"kind": "genga"}).ok
    # Cel Club seen in the photo drops the listing whatever else the LLM said.
    club = keep(
        "eva", "gainax-limited", {"kind": "reproduction", "gainax_limited": True, "cel_club": True}
    )
    assert not club.ok and club.reason == "LLM: Cel Club"
    assert not keep("eva", "cel", {"kind": "cel", "cel_club": True}).ok
    assert keep("fma", "genga", {"kind": "douga"}).ok


def test_llm_answers_are_type_checked(monkeypatch, tmp_path):
    from sakuga.llm import LLM  # pylint: disable=import-outside-toplevel

    m = LLM("http://llm.invalid", "k", "claude")
    img = tmp_path / "a.jpg"
    img.write_bytes(b"x")
    monkeypatch.setattr(m, "_ask", lambda *a, **k: {"same": "false"})
    assert m.same(img, img) is None
    monkeypatch.setattr(
        m, "_ask", lambda *a, **k: {"kind": "cel", "title_en": None, "characters": "Rei"}
    )
    v = m.classify("t", "", "", None)
    assert v and v["title_en"] == "" and v["characters"] == [] and v["gainax_limited"] is False
    monkeypatch.setattr(m, "_ask", lambda *a, **k: {"kind": ["cel"]})
    assert m.classify("t", "", "", None) is None


def test_scheduler_waits_across_the_autumn_clock_change():
    import datetime as dt  # pylint: disable=import-outside-toplevel
    from zoneinfo import ZoneInfo  # pylint: disable=import-outside-toplevel

    from sakuga.scheduler import next_time  # pylint: disable=import-outside-toplevel

    london = ZoneInfo("Europe/London")
    now = dt.datetime(2026, 10, 25, 1, 40, tzinfo=london)  # first 01:40, still BST
    at = next_time("*/30 * * * *", "Europe/London", now)
    assert at.timestamp() > now.timestamp()
