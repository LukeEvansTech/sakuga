"""The daily email: every cel first seen since the last email, with its photo, and sweep health.

It goes out with the digest each morning, even when nothing is new, so a quiet day reads as quiet
rather than broken. Photos are embedded as inline attachments (Outlook blocks remote images) from
the catalogue's own copies, shrunk to thumbnails. Each cel it carries is marked (cels.mailed), so a
cel is emailed once, and one the email missed while the relay was down goes in the next one.
"""

from __future__ import annotations

import datetime as dt
import io
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import Settings
from .db import DB
from .filters import FRANCHISES

log = logging.getLogger(__name__)

LAST_RUN = "mail_last_run"  # kv: the newest finished run the last sent email covered
LAST_HTML = "mail_last_html"  # kv: the last sent email, with web photo links
LAST_RESULT = "mail_last_result"
MAX_PHOTOS = 60  # thumbnails per email; later cels are listed without one
THUMB = 240  # px, longest side
CODES = {
    "Yahoo": "Y",
    "Mercari": "M",
    "Mandarake": "S",
    "Mandarake auction": "A",
    "eBay": "E",
}
HERE = Path(__file__).resolve().parent
ENV = Environment(
    loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape(["html"])
)


def _view(db: DB, row) -> dict:
    live = db.one(
        "SELECT source, yen, gbp, auction FROM listings WHERE cel_id = ? AND ok = 1"
        " ORDER BY active DESC, yen IS NULL, yen LIMIT 1",
        row["id"],
    )
    sources = [
        r["source"]
        for r in db.all(
            "SELECT DISTINCT source FROM listings WHERE cel_id = ? AND ok = 1 AND active = 1"
            " ORDER BY source",
            row["id"],
        )
    ]
    c = dict(row)
    src = live["source"] if live else ""
    c.update(
        code=f"{CODES.get(src, '–')}-{row['id']:04d}",
        title=row["title_en"] or row["title_jp"],
        label=FRANCHISES.get(row["franchise"], {}).get("label", row["franchise"]),
        yen=live["yen"] if live else None,
        gbp=live["gbp"] if live else None,
        auction=bool(live["auction"]) if live else False,
        sources=sources,
    )
    return c


def gather(db: DB, now: dt.datetime | None = None) -> dict:
    """What the email would say right now; ``last_run`` is where the next one's sweeps start.
    A cel GAINAX's instant alert announced is not repeated here (announced once, as in pending()).
    """
    rows = db.all(
        "SELECT * FROM cels WHERE mailed IS NULL AND baseline = 0 AND rejected = 0"
        " AND instant_sent IS NULL ORDER BY id DESC"
    )
    cels = [_view(db, r) for r in rows]

    newest = db.one("SELECT MAX(id) n FROM runs WHERE status != 'running'")
    last_run = int(newest["n"] or 0) if newest else 0
    after_run = db.get(LAST_RUN, None)
    if after_run is None:  # first email: the last day of sweeps
        day_ago = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        first = db.one("SELECT MIN(id) n FROM runs WHERE started >= ?", day_ago)
        after_run = (first["n"] - 1) if first and first["n"] else last_run
    runs = db.all(
        "SELECT id, status, llm_cost FROM runs WHERE id > ? AND id <= ? AND status != 'running'",
        after_run,
        last_run,
    )
    failed_sources = db.all(
        "SELECT name, COUNT(*) n FROM run_sources WHERE run_id > ? AND run_id <= ?"
        " AND count IS NULL GROUP BY name ORDER BY n DESC",
        after_run,
        last_run,
    )
    by: dict[str, int] = {}
    for c in cels:
        by[c["label"]] = by.get(c["label"], 0) + 1
    avail = db.one("SELECT COUNT(*) n FROM cels WHERE available = 1 AND rejected = 0")
    return {
        "now": now or dt.datetime.now(),
        "cels": cels,
        "by": sorted(by.items(), key=lambda x: -x[1]),
        "available": avail["n"] if avail else 0,
        "runs": len(runs),
        # cut off by a restart counts as a problem too: it may not have read everything
        "bad_runs": sum(1 for r in runs if r["status"] in ("failed", "interrupted")),
        "failed_sources": [(r["name"], r["n"]) for r in failed_sources],
        "cost": sum(r["llm_cost"] or 0 for r in runs),
        "last_run": last_run,
    }


def subject(g: dict) -> str:
    n = len(g["cels"])
    if not n:
        return f"Sakuga: no new cels, {g['available']} for sale"
    parts = ", ".join(f"{k} {label}" for label, k in g["by"][:4])
    return f"Sakuga: {n} new cel{'s' if n != 1 else ''} ({parts})"


def _thumb(path: Path) -> bytes | None:
    """A small JPEG of the catalogue photo, or None (the card shows without one)."""
    from PIL import Image  # pylint: disable=import-outside-toplevel

    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((THUMB, THUMB))
            out = io.BytesIO()
            im.save(out, "JPEG", quality=80)
            return out.getvalue()
    except (OSError, ValueError):
        return None


def render(s: Settings, g: dict, img) -> tuple[str, str]:
    """(html, plain text). ``img(cel)`` returns the photo's src, or '' for none."""
    title = subject(g)
    html = ENV.get_template("digest_email.html").render(g=g, img=img, link=s.link, title=title)
    lines = [title, ""]
    for c in g["cels"]:
        price = f"¥{c['yen']:,}" if c["yen"] else (f"£{c['gbp']:,.0f}" if c["gbp"] else "")
        url = s.link(f"/cel/{c['id']}")
        lines.append(f"{c['code']}  {c['title']}  {price}  {url}")
    lines += ["", health_text(g), "", s.link("/today")]
    return html, "\n".join(lines)


def health_text(g: dict) -> str:
    if not g["runs"]:
        return "No sweeps ran since the last email."
    out = f"{g['runs']} sweep{'s' if g['runs'] != 1 else ''} ran"
    if g["bad_runs"]:
        out += f", {g['bad_runs']} failed or cut off"
    if g["failed_sources"]:
        out += "; searches that failed: " + ", ".join(f"{n} ({k}×)" for n, k in g["failed_sources"])
    return out + "."


def send(s: Settings, g: dict) -> str:
    """Send the email; 'sent' or 'failed: <reason>'."""
    cids: dict[str, tuple[str, bytes]] = {}
    for c in g["cels"]:
        if len(cids) < MAX_PHOTOS and c["image"] and c["image"] not in cids:
            data = _thumb(s.img_dir / c["image"])
            if data:
                cids[c["image"]] = (make_msgid(domain="sakuga")[1:-1], data)
    html, text = render(s, g, lambda c: f"cid:{cids[c['image']][0]}" if c["image"] in cids else "")
    msg = EmailMessage()
    msg["Subject"] = subject(g)
    msg["From"] = formataddr(("Sakuga", s.mail_from))
    msg["To"] = s.digest_to
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=s.mail_from.rpartition("@")[2] or "sakuga")
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    body = msg.get_body(("html",))
    for cid, data in cids.values():
        if isinstance(body, EmailMessage):
            body.add_related(data, "image", "jpeg", cid=f"<{cid}>", disposition="inline")
    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=60) as smtp:
            smtp.ehlo()
            if smtp.has_extn("starttls"):
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            refused = smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as e:
        log.warning("digest email failed: %s", type(e).__name__)
        return f"failed: {type(e).__name__}"
    if refused:
        return f"failed: {len(refused)} recipient(s) refused"
    return "sent"


def run(db: DB, s: Settings) -> tuple[str, list[int]]:
    """Gather, send, and mark what it carried only when the email went. Off when unconfigured.
    Returns the result and the ids of the cels the email delivered."""
    if not s.mail_on:
        return "off", []
    from zoneinfo import ZoneInfo  # pylint: disable=import-outside-toplevel

    g = gather(db, dt.datetime.now(ZoneInfo(s.timezone)))
    res = send(s, g)
    if res == "sent":
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with db.tx() as con:
            con.executemany(
                "UPDATE cels SET mailed = ? WHERE id = ?", [(stamp, c["id"]) for c in g["cels"]]
            )
        db.put(LAST_RUN, g["last_run"])
        web = render(s, g, lambda c: s.link(f"/img/{c['image']}") if c["image"] else "")[0]
        db.put(LAST_HTML, web)
    db.put(LAST_RESULT, f"{dt.datetime.now().isoformat(timespec='minutes')} {res}")
    log.info("digest email of %d cels: %s", len(g["cels"]), res)
    return res, [c["id"] for c in g["cels"]] if res == "sent" else []
