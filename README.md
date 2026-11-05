# sakuga

A personal catalogue of Japanese anime production cels for sale. It watches Yahoo Auctions (through
Buyee), Mercari and Mandarake for cels of a few franchises, recognises a relisted cel by its photo,
and sends one Pushover digest a day of the cels it has never seen before. Everything still for sale is
on the Catalogue page, however long it has been listed.

## How it works

```text
sweep (every 30 min) ─▶ Buyee / Mercari / Mandarake shop + auctions (headless Chromium)
                     ─▶ regular expression first pass (franchise from the Japanese title, prints and goods dropped)
                     ─▶ photo saved + perceptual hash
                     ─▶ LLM reads title + photo once: cel / genga / reproduction / print, English title
                     ─▶ same cel as one already catalogued? (pHash; LLM compares borderline photos)
                     ─▶ listings not seen any more retire; a cel stays available while any listing is
digest (08:00)       ─▶ one alert for the cels first seen since the last digest
```

- **Cel and listing.** A listing is one sighting on one site; a cel is the artwork. A relist, or the
  same cel on another site, joins the existing cel, so it is announced once.
- **Instant alerts** only for GAINAX's numbered limited reproduction cels; everything else waits for
  the digest. The first sweep is a baseline and is never announced.
- **Franchises** (Broly, Fullmetal Alchemist, Evangelion, Pokémon) and their searches live in
  `sakuga/filters.py`.

## Run it

```sh
uv venv -p 3.13 .venv && uv pip install -p .venv -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -m pytest -q
SAKUGA_DATA=data .venv/bin/python -m sakuga sweep --no-notify
SAKUGA_DATA=data .venv/bin/python -m sakuga digest   # send the digest now; stop the server first
SAKUGA_DATA=data .venv/bin/python -m sakuga serve   # :8080, sweeps every 30 min, digest 08:00
```

Docker: `docker build -t sakuga .` then
`docker run --read-only --tmpfs /tmp -v sakuga:/data -p 8080:8080 sakuga`. The image runs as uid
65532 with a read-only root filesystem as long as `/tmp` is writable.

## Configuration

| Variable            | Default         | Description                                                                |
| ------------------- | --------------- | -------------------------------------------------------------------------- |
| `SAKUGA_DATA`       | `data`          | Directory for `sakuga.db` and the stored photos (`img/`).                  |
| `SAKUGA_PUBLIC_URL` | none            | Base URL of the web UI; alert links are absolute when set.                 |
| `SAKUGA_SCHEDULE`   | `*/30 * * * *`  | Cron for sweeps.                                                           |
| `SAKUGA_DIGEST`     | `0 8 * * *`     | Cron for the daily digest.                                                 |
| `SAKUGA_TZ`         | `Europe/London` | Timezone both crons are read in.                                           |
| `APPRISE_URLS`      | none            | Comma-separated Apprise URLs. Empty sends nothing.                         |
| `SAKUGA_LLM_URL`    | none            | OpenAI-compatible endpoint. Empty: regular expression filter only, no LLM. |
| `SAKUGA_LLM_KEY`    | none            | Bearer key for that endpoint.                                              |
| `SAKUGA_LLM_MODEL`  | `claude`        | Model name; it must accept images.                                         |
| `SAKUGA_TRIES`      | `3`             | Page loads per search before it counts as failed.                          |

## Pages

`/today` (new since the last digest, and the last digest), `/catalogue` (filters and sorts),
`/cel/<id>` (photos, every sighting, price history), `/log` (each sweep and its sources),
`/health`, `/metrics`. The UI has no login: run it behind an internal-only route.
