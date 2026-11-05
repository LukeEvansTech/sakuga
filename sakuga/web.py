"""Web UI. The digest opens /today; an instant alert opens /cel/<id>. No login: internal route only."""

from __future__ import annotations

import datetime as dt
import json
import re
import threading
import urllib.parse
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import scheduler, sweep
from .config import Settings
from .db import DB
from .filters import FRANCHISES

HERE = Path(__file__).resolve().parent
SAFE_NAME = re.compile(r"^[a-f0-9]{24}\.jpg$")
SOURCES = ["Yahoo", "Mercari", "Mandarake", "Mandarake auction", "eBay"]
KINDS = {"cel": "Cel", "genga": "Genga", "douga": "Douga"}
SORTS = {
    "newest": "Newest",
    "longest": "Longest listed",
    "price": "Price, low first",
    "price_desc": "Price, high first",
}


def parse_ts(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    return dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)


def days_between(a: str | None, b: str | None = None) -> int:
    start = parse_ts(a)
    end = parse_ts(b) or dt.datetime.now(dt.timezone.utc)
    return max(0, (end - start).days) if start else 0


def yen(v) -> str:
    return f"¥{int(v):,}" if v else ""


def gbp(v) -> str:
    return f"£{float(v):,.0f}" if v else ""


def ago(s: str | None) -> str:
    t = parse_ts(s)
    if not t:
        return ""
    secs = int((dt.datetime.now(dt.timezone.utc) - t).total_seconds())
    if secs < 90:
        return "just now"
    if secs < 3600:
        return f"{secs // 60} min ago"
    if secs < 86400:
        return f"{secs // 3600} h ago"
    return f"{secs // 86400} d ago"


def local_time(s: str | None, tz: str) -> str:
    from zoneinfo import ZoneInfo  # pylint: disable=import-outside-toplevel

    t = parse_ts(s)
    return t.astimezone(ZoneInfo(tz)).strftime("%a %d %b %H:%M") if t else ""


def cel_view(db: DB, row) -> dict:
    """A cel with what its card needs: best current listing, every source, days on the market."""
    listings = [
        dict(r)
        for r in db.all(
            "SELECT * FROM listings WHERE cel_id = ? AND ok = 1 ORDER BY active DESC, yen IS NULL, yen",
            row["id"],
        )
    ]
    live = [x for x in listings if x["active"]]
    best = live[0] if live else (listings[0] if listings else {})
    c = dict(row)
    c.update(
        listings=listings,
        best=best,
        sources=sorted({x["source"] for x in live}),
        days=days_between(row["first_seen"], None if row["available"] else row["last_seen"]),
        label=FRANCHISES.get(row["franchise"], {}).get("label", row["franchise"]),
        title=row["title_en"] or row["title_jp"],
    )
    return c


def create_app(s: Settings, db: DB | None = None, start_scheduler: bool = False) -> FastAPI:
    db = db or DB(s.db_path)
    app = FastAPI(title="sakuga", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    tpl = Jinja2Templates(directory=HERE / "templates")
    tpl.env.filters.update(yen=yen, gbp=gbp, ago=ago, local=lambda v: local_time(v, s.timezone))
    tpl.env.globals.update(
        version=s.version,
        franchises=FRANCHISES,
        schedule=s.schedule,
        digest_schedule=s.digest_schedule,
        tz=s.timezone,
    )

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        """Refuse a browser write another site's page sent (no login here, so no CSRF token)."""
        if request.method in ("POST", "PUT", "DELETE"):
            origin = request.headers.get("origin") or request.headers.get("referer") or ""
            host = urllib.parse.urlsplit(origin).netloc
            if origin and host != request.headers.get("host", ""):
                return PlainTextResponse("cross-site request refused", status_code=403)
        return await call_next(request)

    def page(request: Request, name: str, nav: str, **ctx) -> HTMLResponse:
        last = db.one("SELECT * FROM runs WHERE status != 'running' ORDER BY id DESC LIMIT 1")
        running = sweep.LOCK.locked()
        return tpl.TemplateResponse(
            request, name, {"nav": nav, "last_run": last, "running": running, **ctx}
        )

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/today", status_code=302)

    @app.get("/today", response_class=HTMLResponse)
    def today(request: Request):
        fresh = [cel_view(db, r) for r in sweep.new_since_digest(db)]
        last = db.one("SELECT * FROM digests ORDER BY id DESC LIMIT 1")
        sent = []
        if last:
            sent = [
                cel_view(db, r)
                for r in db.all(
                    "SELECT * FROM cels WHERE digest_id = ? AND rejected = 0 ORDER BY first_seen DESC",
                    last["id"],
                )
            ]
        total = db.one("SELECT COUNT(*) n FROM cels WHERE available = 1 AND rejected = 0")
        return page(
            request,
            "today.html",
            "today",
            fresh=fresh,
            sent=sent,
            digest=last,
            available=total["n"] if total else 0,
        )

    @app.get("/catalogue", response_class=HTMLResponse)
    def catalogue(
        request: Request,
        franchise: str = "",
        source: str = "",
        kind: str = "",
        sort: str = "newest",
        show: str = "available",
    ):
        where, args = ["c.rejected = 0"], []
        if franchise in FRANCHISES:
            where.append("c.franchise = ?")
            args.append(franchise)
        if kind in KINDS:
            where.append("c.kind = ?")
            args.append(kind)
        if show == "available":
            where.append("c.available = 1")
        elif show == "gone":
            where.append("c.available = 0")
        if source in SOURCES:
            where.append(
                "EXISTS (SELECT 1 FROM listings l WHERE l.cel_id = c.id AND l.ok = 1"
                " AND l.source = ?" + (" AND l.active = 1" if show == "available" else "") + ")"
            )
            args.append(source)
        # The live price, or for a cel no longer for sale its last known one.
        price = (
            "COALESCE((SELECT MIN(l.yen) FROM listings l WHERE l.cel_id = c.id AND l.ok = 1"
            " AND l.active = 1), (SELECT MIN(l.yen) FROM listings l WHERE l.cel_id = c.id"
            " AND l.ok = 1))"
        )
        order = {
            "newest": "c.first_seen DESC",
            "longest": "c.first_seen ASC",
            "price": f"{price} IS NULL, {price} ASC",
            "price_desc": f"{price} DESC",
        }.get(sort, "c.first_seen DESC")
        rows = db.all(
            f"SELECT c.* FROM cels c WHERE {' AND '.join(where)} ORDER BY {order}", *args
        )  # nosec B608
        cels = [cel_view(db, r) for r in rows]
        counts = {
            r["franchise"]: r["n"]
            for r in db.all(
                "SELECT franchise, COUNT(*) n FROM cels WHERE available = 1 AND rejected = 0 GROUP BY franchise"
            )
        }
        return page(
            request,
            "catalogue.html",
            "catalogue",
            cels=cels,
            counts=counts,
            f={"franchise": franchise, "source": source, "kind": kind, "sort": sort, "show": show},
            sources=SOURCES,
            kinds=KINDS,
            sorts=SORTS,
        )

    @app.get("/cel/{cel_id}", response_class=HTMLResponse)
    def cel(request: Request, cel_id: int):
        row = db.one("SELECT * FROM cels WHERE id = ?", cel_id)
        if not row:
            raise HTTPException(404, "no such cel")
        c = cel_view(db, row)
        history = {}
        for x in c["listings"]:
            history[x["id"]] = [
                dict(p)
                for p in db.all(
                    "SELECT seen, yen FROM prices WHERE listing_id = ? ORDER BY seen", x["id"]
                )
            ]
            try:
                x["v"] = json.loads(x["verdict"]) if x["verdict"] else {}
            except json.JSONDecodeError:
                x["v"] = {}
        photos = []
        for x in c["listings"]:
            if x["image"] and x["image"] not in photos:
                photos.append(x["image"])
        return page(request, "cel.html", "catalogue", c=c, history=history, photos=photos)

    @app.get("/log", response_class=HTMLResponse)
    def log_page(request: Request):
        runs = [dict(r) for r in db.all("SELECT * FROM runs ORDER BY id DESC LIMIT 60")]
        for r in runs:
            r["sources"] = [
                dict(x)
                for x in db.all("SELECT * FROM run_sources WHERE run_id = ? ORDER BY name", r["id"])
            ]
            r["failed"] = [x for x in r["sources"] if x["count"] is None]
            end, start = parse_ts(r["finished"]), parse_ts(r["started"])
            r["seconds"] = (end - start).seconds if end and start else None
        digests = db.all("SELECT * FROM digests ORDER BY id DESC LIMIT 14")
        spend = db.one(
            "SELECT COALESCE(SUM(llm_cost),0) c, COALESCE(SUM(llm_calls),0) n FROM runs"
            " WHERE started >= datetime('now','-30 days')"
        )
        return page(request, "log.html", "log", runs=runs, digests=digests, spend=spend)

    @app.post("/sweep")
    def sweep_now():
        if sweep.LOCK.locked():
            return RedirectResponse("/log", status_code=303)
        threading.Thread(
            target=sweep.run, args=(db, s), kwargs={"trigger": "manual"}, daemon=True
        ).start()
        return RedirectResponse("/log", status_code=303)

    @app.get("/img/{name}")
    def img(name: str):
        if not SAFE_NAME.match(name) or not (s.img_dir / name).exists():
            raise HTTPException(404, "no such image")
        return FileResponse(
            s.img_dir / name,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    @app.get("/health")
    def health():
        return {"ok": True, "version": s.version}

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics():
        """A sweep counts only when it read something; a run that searched nothing is not a success."""
        ok = db.one(
            "SELECT finished FROM runs WHERE status IN ('success','partial') AND listings > 0"
            " ORDER BY id DESC LIMIT 1"
        )
        last = db.one("SELECT status FROM runs WHERE status != 'running' ORDER BY id DESC LIMIT 1")
        cels = db.one(
            "SELECT COUNT(*) n, COALESCE(SUM(available),0) a FROM cels WHERE rejected = 0"
        )
        t = parse_ts(ok["finished"]) if ok else None
        lines = [
            "# HELP sakuga_last_success_timestamp_seconds Finish time of the last sweep that read listings.",
            "# TYPE sakuga_last_success_timestamp_seconds gauge",
            f"sakuga_last_success_timestamp_seconds {t.timestamp() if t else 0:.0f}",
            "# TYPE sakuga_last_run_failed gauge",
            f"sakuga_last_run_failed {1 if last and last['status'] == 'failed' else 0}",
            "# TYPE sakuga_cels gauge",
            f"sakuga_cels {cels['n'] if cels else 0}",
            "# TYPE sakuga_cels_available gauge",
            f"sakuga_cels_available {cels['a'] if cels else 0}",
        ]
        return "\n".join(lines) + "\n"

    if start_scheduler:
        scheduler.start(s.schedule, s.timezone, lambda: sweep.run(db, s), "sweep")
        scheduler.start(s.digest_schedule, s.timezone, lambda: sweep.digest(db, s), "digest")
    return app
