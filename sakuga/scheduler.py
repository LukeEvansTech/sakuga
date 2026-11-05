"""In-process scheduler: one daemon thread per cron expression (sweeps, and the daily digest)."""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from zoneinfo import ZoneInfo

from croniter import croniter

log = logging.getLogger(__name__)


def next_time(expr: str, tz: str, now: dt.datetime | None = None) -> dt.datetime:
    base = now or dt.datetime.now(ZoneInfo(tz))
    return croniter(expr, base).get_next(dt.datetime)


def start(expr: str, tz: str, job, name: str = "sweep") -> threading.Thread:
    if not croniter.is_valid(expr):
        raise ValueError(f"invalid {name} schedule: {expr!r}")

    def loop() -> None:
        while True:
            at = next_time(expr, tz)
            log.info("next %s at %s", name, at.isoformat())
            # Timestamps, not wall-clock datetimes: across the autumn clock change the next time can
            # read earlier than now in the same zone while being 30 minutes away.
            while (wait := at.timestamp() - time.time()) > 0:
                time.sleep(min(wait, 60))
            try:
                job()
            except Exception:  # pylint: disable=broad-exception-caught
                log.exception("scheduled %s crashed", name)

    t = threading.Thread(target=loop, name=f"scheduler-{name}", daemon=True)
    t.start()
    return t
