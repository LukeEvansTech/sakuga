"""One sweep: read every source, catalogue what passes, retire what is gone, alert GAINAX cels now.

The daily digest (digest()) is separate: it sends the cels first seen since the last digest. A cel is
announced once in its life however often it is relisted, so a cel listed for years never alerts again.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import threading
import time
from pathlib import Path

from . import catalog, filters, images, notify
from .config import Settings
from .db import DB
from .llm import LLM
from .sources import UA, Raw, jobs, mercari_available

log = logging.getLogger(__name__)
LOCK = threading.Lock()
# The digest runs on its own scheduler thread: one lock covers choosing, sending and marking, so a
# GAINAX cel cannot go out both instantly and in the digest.
NOTIFY_LOCK = threading.Lock()


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def collect(
    s: Settings, db: DB, run_id: int, check: list[str] | None = None
) -> tuple[list[Raw], set[str], set[str], dict[str, bool | None]]:
    """Every listing from every search; the families with a failed search and with a partial read
    (neither is retired on absence); and, for each Mercari URL in check, whether it is for sale."""
    from playwright.sync_api import sync_playwright  # pylint: disable=import-outside-toplevel

    raws: list[Raw] = []
    failed: set[str] = set()
    partial: set[str] = set()
    checked: dict[str, bool | None] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=s.headless)
        try:
            ctx = browser.new_context(
                locale="ja-JP", user_agent=UA, viewport={"width": 1366, "height": 900}
            )
            page = ctx.new_page()
            for name, family, fn in jobs():
                started, got, err = time.monotonic(), None, ""
                for attempt in range(1, s.tries + 1):
                    try:
                        got = fn(page)
                        break
                    except Exception as e:  # pylint: disable=broad-exception-caught
                        err = f"{type(e).__name__}: {str(e)[:200]}"
                        log.warning("%s attempt %d failed: %s", name, attempt, err)
                        if page.is_closed():
                            page = ctx.new_page()
                        time.sleep(5)
                if got is None:
                    failed.add(family)
                else:
                    raws += got
                    err = ""
                    if not getattr(got, "complete", True):
                        partial.add(family)
                db.run(
                    "INSERT OR REPLACE INTO run_sources (run_id, name, count, error, seconds) VALUES (?,?,?,?,?)",
                    run_id,
                    name,
                    None if got is None else len(got),
                    err or None,
                    round(time.monotonic() - started, 1),
                )
            for url in check or []:
                try:
                    checked[url] = mercari_available(page, url)
                except Exception:  # pylint: disable=broad-exception-caught
                    checked[url] = None
        finally:
            browser.close()
    return raws, failed, partial, checked


def yen_per_gbp(raws: list[Raw], old: float | None) -> float | None:
    """Buyee prints both currencies on every card; their median ratio is the day's rate."""
    r = sorted(x.yen / x.gbp for x in raws if x.source == "Yahoo" and x.yen and x.gbp)
    return r[len(r) // 2] if r else old


def price_text(yen: int | None, gbp: float | None) -> str:
    if yen:
        return f"¥{yen:,}" + (f" (~£{gbp:,.0f})" if gbp else "")
    return f"£{gbp:,.0f}" if gbp else "price not shown"


def _same_fn(db: DB, llm: LLM, img_dir: Path, listing_id: str, image: str):
    def same(cel_id: int) -> bool | None:
        hit = db.one("SELECT same FROM same WHERE a = ? AND b = ?", listing_id, str(cel_id))
        if hit:
            return bool(hit["same"])
        cel = db.one("SELECT image FROM cels WHERE id = ?", cel_id)
        if not (cel and cel["image"] and image):
            return None
        v = llm.same(img_dir / image, img_dir / cel["image"])
        if v is not None:
            db.run(
                "INSERT OR REPLACE INTO same (a, b, same) VALUES (?,?,?)",
                listing_id,
                str(cel_id),
                int(v),
            )
        return v

    return same


def ingest(
    db: DB, s: Settings, llm: LLM, raws: list[Raw], rate: float | None, baseline: bool
) -> dict:
    """Store and catalogue one sweep's listings. Returns counts and the new cel ids."""
    t = now()
    seen: set[str] = set()
    kept, new_cels = 0, []
    for raw in raws:
        if raw.id in seen:
            continue
        yen = raw.yen or (round(raw.gbp * rate) if raw.gbp and rate else None)
        gbp = raw.gbp or (raw.yen / rate if raw.yen and rate else None)
        old = db.one("SELECT ok, cel_id, yen, image FROM listings WHERE id = ?", raw.id)
        if (
            old
        ):  # already catalogued: the admission filters (a price cut below the floor) do not apply
            seen.add(raw.id)
            db.run(
                "UPDATE listings SET last_seen=?, active=1, yen=?, gbp=?, info=? WHERE id=?",
                t,
                yen,
                gbp,
                raw.info,
                raw.id,
            )
            if yen and yen != old["yen"]:
                db.run(
                    "INSERT OR IGNORE INTO prices (listing_id, seen, yen) VALUES (?,?,?)",
                    raw.id,
                    t,
                    yen,
                )
            if not old["image"] and raw.img_url:  # a photo that failed to download: try again
                image, ph = images.download(raw.img_url, s.img_dir, referer=raw.url)
                if image:
                    db.run("UPDATE listings SET image=?, phash=? WHERE id=?", image, ph, raw.id)
                    if old["cel_id"]:
                        db.run(
                            "UPDATE cels SET image=?, phash=? WHERE id=? AND image=''",
                            image,
                            ph,
                            old["cel_id"],
                        )
                        regroup(db, llm, s, raw, old["cel_id"], image, ph)
            if old["ok"] and old["cel_id"]:
                kept += 1
                db.run("UPDATE cels SET last_seen=?, available=1 WHERE id=?", t, old["cel_id"])
            continue

        # Converted first, so the minimum price applies to Mercari's pound-only prices too.
        c = filters.classify(raw.title, raw.extra, yen, raw.cel_category, raw.auction)
        if not c:
            continue
        seen.add(raw.id)
        image, ph = images.download(raw.img_url, s.img_dir, referer=raw.url)
        verdict = llm.classify(
            raw.title, raw.extra, price_text(yen, gbp), s.img_dir / image if image else None
        )
        k = catalog.keep(c["franchise"], c["kind"], verdict)
        if verdict is None and (twin := rejected_twin(db, c["franchise"], ph)):
            # No answer this time, but the same photo was rejected before: a relist of that artwork.
            k = catalog.Keep(False, twin["kind"], False, f"same photo as rejected {twin['id']}")
        cel_id = None
        if k.ok:
            kept += 1
            cands = candidates(db, c["franchise"])
            cel_id = catalog.group(
                ph, c["franchise"], cands, _same_fn(db, llm, s.img_dir, raw.id, image), raw.title
            )
            v = verdict or {}
            if cel_id is None:
                cel_id = db.run(
                    "INSERT INTO cels (franchise, kind, title_en, title_jp, notes, image, phash, gainax,"
                    " baseline, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    c["franchise"],
                    k.kind,
                    v.get("title_en", ""),
                    raw.title[:300],
                    _notes(v),
                    image,
                    ph,
                    int(k.gainax),
                    int(baseline),
                    t,
                    t,
                )
                new_cels.append(cel_id)
            else:
                db.run("UPDATE cels SET last_seen=?, available=1 WHERE id=?", t, cel_id)
                if image:
                    db.run(
                        "UPDATE cels SET image=?, phash=? WHERE id=? AND image=''",
                        image,
                        ph,
                        cel_id,
                    )
        db.run(
            "INSERT INTO listings (id, source, cel_id, title, extra, url, img_url, image, phash, yen, gbp,"
            " info, auction, franchise, kind, verdict, ok, reason, first_seen, last_seen)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            raw.id,
            raw.source,
            cel_id,
            raw.title[:500],
            raw.extra[:500],
            raw.url,
            raw.img_url,
            image,
            ph,
            yen,
            gbp,
            raw.info[:200],
            int(raw.auction),
            c["franchise"],
            k.kind,
            _json(verdict),
            int(k.ok),
            k.reason,
            t,
            t,
        )
        if yen:
            db.run(
                "INSERT OR IGNORE INTO prices (listing_id, seen, yen) VALUES (?,?,?)",
                raw.id,
                t,
                yen,
            )
    return {"seen": seen, "kept": kept, "new_cels": new_cels}


def rejected_twin(db: DB, franchise: str, ph: str):
    """A listing the LLM rejected whose photo is this one (a relist), or None."""
    if not ph:
        return None
    rows = db.all(
        "SELECT id, kind, phash FROM listings WHERE franchise = ? AND ok = 0 AND verdict != ''"
        " AND phash != ''",
        franchise,
    )
    return next((r for r in rows if images.distance(ph, r["phash"]) <= catalog.NEAR), None)


MAX_BACKFILL = 10


def backfill(db: DB, llm: LLM, s: Settings, limit: int = MAX_BACKFILL) -> int:
    """Ask the LLM again about kept listings it never answered for (a timeout, a cut-off reply).

    The verdict counts as it would have on first sight: a Cel Club cel, print or drawing leaves the
    catalogue (its cel is hidden if nothing else holds it), and kind and GAINAX follow the LLM. Returns the number answered.
    """
    rows = db.all(
        "SELECT l.id, l.title, l.extra, l.yen, l.gbp, l.image, l.franchise, l.cel_id, c.gainax"
        " FROM listings l LEFT JOIN cels c ON c.id = l.cel_id"
        " WHERE l.ok = 1 AND l.verdict = '' AND l.image != '' AND l.active = 1"
        # Never asked first, newest first; then the longest since the last try, so failures rotate.
        " ORDER BY l.asked IS NOT NULL, l.asked, l.first_seen DESC LIMIT ?",
        limit,
    )
    answered = 0
    for r in rows:
        db.run("UPDATE listings SET asked = ? WHERE id = ?", now(), r["id"])
        verdict = llm.classify(
            r["title"], r["extra"], price_text(r["yen"], r["gbp"]), s.img_dir / r["image"]
        )
        if not verdict:
            continue
        answered += 1
        k = catalog.keep(r["franchise"], "gainax-limited" if r["gainax"] else "cel", verdict)
        # One transaction per listing: an interruption leaves the listing and its cel consistent.
        with NOTIFY_LOCK, db.tx() as con:
            if not k.ok:
                con.execute(
                    "UPDATE listings SET verdict=?, ok=0, reason=?, kind=? WHERE id=?",
                    (_json(verdict), k.reason, k.kind, r["id"]),
                )
                # Hidden, not deleted: its id stays out of reuse (alert links), and relists still match it.
                con.execute(
                    "UPDATE cels SET rejected = 1 WHERE id = ? AND NOT EXISTS"
                    " (SELECT 1 FROM listings WHERE cel_id = cels.id AND ok = 1)",
                    (r["cel_id"],),
                )
                log.info("listing %s dropped on a second look: %s", r["id"], k.reason)
                continue
            con.execute(
                "UPDATE listings SET verdict=?, kind=? WHERE id=?",
                (_json(verdict), k.kind, r["id"]),
            )
            con.execute(
                "UPDATE cels SET title_en = CASE WHEN title_en = '' THEN ? ELSE title_en END,"
                " notes = CASE WHEN notes = '' THEN ? ELSE notes END,"
                " kind = ?, gainax = ? WHERE id = ?",
                (
                    verdict.get("title_en", ""),
                    _notes(verdict),
                    k.kind,
                    int(k.gainax),  # the LLM decides GAINAX either way, as on first sight
                    r["cel_id"],
                ),
            )
    return answered


def candidates(db: DB, franchise: str, exclude: int = 0) -> list[catalog.Candidate]:
    """Every known photo of every cel in the franchise: each cel's own, and each of its listings'
    (a relist may reuse any photo the cel was ever shown with, under any title)."""
    rows = db.all(
        "SELECT id AS cel, phash, title_jp AS title FROM cels WHERE franchise = ? AND id != ?"
        " UNION ALL SELECT cel_id, phash, title FROM listings"
        " WHERE franchise = ? AND ok = 1 AND cel_id IS NOT NULL AND cel_id != ? AND phash != ''",
        franchise,
        exclude,
        franchise,
        exclude,
    )
    return [catalog.Candidate(r["cel"], r["phash"], franchise, r["title"]) for r in rows]


def regroup(db: DB, llm: LLM, s: Settings, raw: Raw, cel_id: int, image: str, ph: str) -> None:
    """A photo that arrived late may show a cel already catalogued: if this listing's cel holds
    only this listing and has not been announced, fold it into the matching cel. Held under the
    notify lock so a cel is never merged away while a digest is sending it."""
    with NOTIFY_LOCK:
        cel = db.one("SELECT * FROM cels WHERE id = ?", cel_id)
        n = db.one("SELECT COUNT(*) n FROM listings WHERE cel_id = ?", cel_id)
        if not cel or not n or n["n"] != 1 or cel["digest_id"] or cel["instant_sent"]:
            return
        match = catalog.group(
            ph,
            cel["franchise"],
            candidates(db, cel["franchise"], cel_id),
            _same_fn(db, llm, s.img_dir, raw.id, image),
            raw.title,
        )
        if match is None:
            return
        with db.tx() as con:
            con.execute("UPDATE listings SET cel_id = ? WHERE id = ?", (match, raw.id))
            # Keep the earlier discovery: a baseline cel stays baseline, never announced as new.
            con.execute(
                "UPDATE cels SET last_seen = ?, available = 1, first_seen = MIN(first_seen, ?),"
                " baseline = MAX(baseline, ?) WHERE id = ?",
                (now(), cel["first_seen"], cel["baseline"], match),
            )
            con.execute("DELETE FROM cels WHERE id = ?", (cel_id,))
    log.info("listing %s joined cel %d (its photo arrived late)", raw.id, match)


def _json(v) -> str:
    return json.dumps(v, ensure_ascii=False) if v else ""


def _notes(v: dict) -> str:
    bits = []
    if v.get("characters"):
        bits.append(", ".join(v["characters"]))
    if v.get("episode_cut"):
        bits.append(v["episode_cut"])
    if v.get("background"):
        bits.append("with background")
    if v.get("notes"):
        bits.append(v["notes"])
    return " · ".join(bits)[:400]


# How a listing missing from this sweep's results retires:
# - Mandarake shop and auctions are read whole (when the read was complete), so it is gone.
# - Yahoo (Buyee) searches show a window of results; auctions end within a week and a relist gets
#   a new ID, so STALE_DAYS unseen means gone.
# - Mercari is fixed price and can sit unsold for months outside the search window, so a stale
#   listing is checked on its own page and retires only when the page says it is sold or removed.
READ_WHOLE = {"Mandarake", "Mandarake auction"}
STALE_DAYS = 3
# Yahoo auctions run up to seven days, so one can leave the search window and still be running.
# A Mandarake auction lot beyond a full 240-lot page is judged the same way (they end too).
STALE_DAYS_BY_SOURCE = {"Yahoo": 8, "Mandarake auction": 8}
MAX_CHECKS = 12


def _cutoff(source: str = "") -> str:
    days = STALE_DAYS_BY_SOURCE.get(source, STALE_DAYS)
    t = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    return t.strftime("%Y-%m-%d %H:%M:%S")


def to_check(db: DB) -> list[str]:
    """Stale Mercari listings whose own page decides whether they retire: never checked first, then
    the longest since their last check, so an inconclusive page does not hold the queue."""
    rows = db.all(
        "SELECT url FROM listings WHERE active = 1 AND source = 'Mercari' AND last_seen < ?"
        " ORDER BY checked IS NOT NULL, checked, last_seen LIMIT ?",
        _cutoff(),
        MAX_CHECKS,
    )
    return [r["url"] for r in rows]


def retire(
    db: DB,
    seen: set[str],
    failed: set[str],
    partial: frozenset[str] | set[str] = frozenset(),
    checked: dict[str, bool | None] | None = None,
) -> None:
    """Retire listings that are gone; never on a site whose search failed this sweep."""
    checked = checked or {}
    for r in db.all("SELECT id, source, url, last_seen FROM listings WHERE active = 1"):
        src = r["source"]
        if r["id"] in seen or src in failed:
            continue
        if src in READ_WHOLE and src not in partial:
            gone = True
        elif src == "Mandarake":
            gone = False  # a truncated shop read proves nothing about what it left out
        elif src == "Mercari":
            verdict = checked.get(r["url"])
            if r["url"] in checked:
                db.run("UPDATE listings SET checked = ? WHERE id = ?", now(), r["id"])
            if verdict:  # still for sale outside the search window
                db.run("UPDATE listings SET last_seen = ? WHERE id = ?", now(), r["id"])
                db.run(
                    "UPDATE cels SET last_seen = ? WHERE id = (SELECT cel_id FROM listings WHERE id = ?)",
                    now(),
                    r["id"],
                )
            gone = verdict is False
        else:
            gone = r["last_seen"] < _cutoff(src)
        if gone:
            db.run("UPDATE listings SET active = 0 WHERE id = ?", r["id"])
    db.run(
        "UPDATE cels SET available = EXISTS (SELECT 1 FROM listings l WHERE l.cel_id = cels.id"
        " AND l.active = 1 AND l.ok = 1)"
    )


def instant(db: DB, s: Settings) -> list[str]:
    """Instant alerts under the notify lock."""
    with NOTIFY_LOCK:
        return _instant(db, s)


def _instant(db: DB, s: Settings) -> list[str]:
    """GAINAX limited cels alert at once (priority 1); everything else waits for the digest.

    Read from the catalogue, not this sweep's new cels, so a send that failed is retried next sweep.
    """
    out = []
    for cel in db.all(
        "SELECT * FROM cels WHERE gainax = 1 AND baseline = 0 AND instant_sent IS NULL AND rejected = 0"
        " AND digest_id IS NULL"
    ):
        cel_id = cel["id"]
        lst = db.one(
            "SELECT source, yen, gbp FROM listings WHERE cel_id = ? ORDER BY active DESC, first_seen LIMIT 1",
            cel_id,
        )
        price = price_text(lst["yen"], lst["gbp"]) if lst else ""
        title = f"GAINAX limited cel: {price}"
        body = f"{cel['title_en'] or cel['title_jp'][:120]}\n{cel['notes']}\n{lst['source'] if lst else ''}"
        r = notify.send(s.apprise_urls, title, body.strip(), s.link(f"/cel/{cel_id}"), "Open cel")
        if r == "sent":  # marked only after the send succeeded
            db.run("UPDATE cels SET instant_sent = ? WHERE id = ?", now(), cel_id)
        out.append(f"cel {cel_id}: {r}")
    return out


def run(
    db: DB, s: Settings, trigger: str = "schedule", notify_on: bool = True, llm: LLM | None = None
) -> int:
    """One sweep. Returns the run id, or 0 when another sweep is already running."""
    # Released in the finally below; a with-block cannot skip when the lock is already held.
    acquired = LOCK.acquire(blocking=False)  # pylint: disable=consider-using-with
    if not acquired:
        log.info("sweep already running; skipped")
        return 0
    model = llm or LLM(s.llm_url, s.llm_key, s.llm_model)
    run_id = 0
    try:
        run_id = db.run("INSERT INTO runs (started, trigger) VALUES (?, ?)", now(), trigger)
        raws, failed, partial, checked = collect(s, db, run_id, to_check(db))
        row = db.one(
            "SELECT COUNT(*) n FROM run_sources WHERE run_id = ? AND count IS NOT NULL", run_id
        )
        ok_jobs = row["n"] if row else 0
        rate = yen_per_gbp(raws, db.get("yen_per_gbp", None))
        if rate:
            db.put("yen_per_gbp", rate)
        baseline = not db.get("baselined", False)
        got = ingest(db, s, model, raws, rate, baseline)
        backfill(db, model, s)
        retire(db, got["seen"], failed, partial, checked)
        alerts = instant(db, s) if notify_on else []
        status = "failed" if ok_jobs == 0 else ("partial" if failed else "success")
        if baseline and ok_jobs:
            db.put("baselined", True)
        db.run(
            "UPDATE runs SET finished=?, status=?, listings=?, kept=?, new_cels=?, llm_calls=?, llm_cost=?,"
            " alerts=? WHERE id=?",
            now(),
            status,
            len(raws),
            got["kept"],
            0 if baseline else len(got["new_cels"]),
            model.usage.calls,
            round(model.usage.cost, 4),
            "; ".join(alerts),
            run_id,
        )
        log.info(
            "sweep %d %s: %d listings, %d kept, %d new cels, %d LLM calls",
            run_id,
            status,
            len(raws),
            got["kept"],
            len(got["new_cels"]),
            model.usage.calls,
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        log.exception("sweep %d crashed", run_id)
        db.run(
            "UPDATE runs SET finished=?, status='failed', error=? WHERE id=?",
            now(),
            f"{type(e).__name__}: {e}"[:500],
            run_id,
        )
    finally:
        LOCK.release()
    return run_id


def new_since_digest(db: DB) -> list:
    """Today's discoveries: cels first seen since the last digest. A GAINAX cel alerted instantly
    never joins a digest, so it shows only until the next digest goes out."""
    last = db.one("SELECT MAX(created) t FROM digests")
    since = last["t"] if last and last["t"] else ""
    return db.all(
        "SELECT * FROM cels WHERE digest_id IS NULL AND baseline = 0 AND rejected = 0"
        " AND (instant_sent IS NULL OR instant_sent >= ?) ORDER BY first_seen DESC",
        since,
    )


def pending(db: DB) -> list:
    """Cels first seen since the last digest (the first sweep's catalogue is not news)."""
    return db.all(
        "SELECT * FROM cels WHERE digest_id IS NULL AND baseline = 0 AND instant_sent IS NULL AND rejected = 0"
        " ORDER BY first_seen DESC"
    )


def digest(db: DB, s: Settings, notify_on: bool = True) -> str:
    """The daily digest under the notify lock."""
    with NOTIFY_LOCK:
        return _digest(db, s, notify_on)


def _digest(db: DB, s: Settings, notify_on: bool = True) -> str:
    """The daily alert: one message for every cel first seen since the last one. Nothing new, no alert."""
    cels = pending(db)
    if not cels:
        log.info("digest: nothing new")
        return "nothing new"
    by: dict[str, int] = {}
    for c in cels:
        by[c["franchise"]] = by.get(c["franchise"], 0) + 1
    label = {k: f["label"] for k, f in filters.FRANCHISES.items()}
    lines = [
        ", ".join(f"{n} {label.get(k, k)}" for k, n in sorted(by.items(), key=lambda x: -x[1]))
    ]
    for c in cels[:6]:
        lst = db.one(
            "SELECT source, yen, gbp FROM listings WHERE cel_id = ? ORDER BY active DESC, first_seen LIMIT 1",
            c["id"],
        )
        lines.append(
            f"• {(c['title_en'] or c['title_jp'])[:60]} · {price_text(lst['yen'], lst['gbp']) if lst else ''}"
        )
    n = len(cels)
    title = f"{n} new cel{'s' if n != 1 else ''} today"
    r = (
        notify.send(s.apprise_urls, title, "\n".join(lines), s.link("/today"), "See them")
        if notify_on
        else "off"
    )
    if r == "sent":  # marked only after the send succeeded; "off" keeps them pending
        did = db.run("INSERT INTO digests (created, cels, result) VALUES (?,?,?)", now(), n, r)
        with db.tx() as con:
            con.executemany(
                "UPDATE cels SET digest_id = ? WHERE id = ?", [(did, c["id"]) for c in cels]
            )
    log.info("digest of %d cels: %s", n, r)
    return r
