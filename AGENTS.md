# sakuga agent guide

Single-user catalogue of Japanese anime production cels for sale. `README.md` covers what it does and
how to run it; this file holds what the code does not say.

- Pipeline: `sakuga/sweep.py` `run()` (sources → regular expression first pass → photo + pHash → LLM verdict →
  group into a cel → retire what is gone → instant GAINAX alerts). `digest()` sends the daily alert.
  Rules that decide anything are pure and tested: `filters.py` (regular expression), `catalog.py` (keep, group).
- Deployed on the talos-cluster (`kubernetes/apps/custom/sakuga`), which alerts on
  `sakuga_last_success_timestamp_seconds` from `GET /metrics`.
- Before pushing: `python -m pytest -q`, then black, isort, flake8, pylint, ruff and mypy with the
  configs in `.github/linters/` (super-linter runs the same).

## Review rules

- **A cel is announced once.** New means new to the catalogue (`cels.first_seen`), never a new
  listing ID. Never alert per listing.
- **Mark an alert sent only after the send succeeded.** `instant_sent` and `digest_id` are written
  after `notify.send` returns `sent`.
- **A failed search is not an empty one.** `retire()` skips every listing of a site whose search failed
  this sweep, or live cels go dark.
- **The first sweep is a baseline.** Its cels carry `baseline = 1` and never reach a digest.
- **The regular expression keeps the franchise; the LLM may only narrow.** `catalog.keep` lets the LLM drop a
  print or a reproduction, never add a franchise.
- **Do not add evasion of bot checks** (stealth patches, captcha solving). A blocked load is retried as is.
