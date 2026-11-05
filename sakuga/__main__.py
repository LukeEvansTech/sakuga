"""sakuga command line.

python -m sakuga serve                  # web UI on :8080 plus the scheduled sweeps and daily digest
python -m sakuga sweep [--no-notify]    # one sweep now
python -m sakuga digest [--no-notify]   # send the daily digest now
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from . import config, sweep
from .db import DB


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sakuga")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve")
    for name in ("sweep", "digest"):
        p = sub.add_parser(name)
        p.add_argument("--no-notify", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = config.load()
    db = DB(s.db_path)

    if a.cmd == "serve":
        import uvicorn  # pylint: disable=import-outside-toplevel

        from .web import create_app  # pylint: disable=import-outside-toplevel

        app = create_app(s, db, start_scheduler=True)
        uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))  # nosec B104
        return 0
    if a.cmd == "sweep":
        run_id = sweep.run(db, s, trigger="cli", notify_on=not a.no_notify)
        row = db.one("SELECT status FROM runs WHERE id = ?", run_id)
        return 0 if row and row["status"] in ("success", "partial") else 1
    if a.cmd == "digest":
        r = sweep.digest(db, s, notify_on=not a.no_notify)
        print(r)
        return 0 if r in ("sent", "off", "nothing new") else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
