# sakuga agent guide

Single-user catalogue of Japanese anime production cels for sale. `README.md` covers what it does and
how to run it; this file holds what the code does not say.

- Pipeline: `sakuga/sweep.py` `run()` (sources → regular expression first pass → photo + pHash → LLM verdict →
  group into a cel → retire what is gone → instant GAINAX alerts). `digest()` sends the daily Pushover alert and
  the email (`mail.py`).
  Rules that decide anything are pure and tested: `filters.py` (regular expression), `catalog.py` (keep, group).
- Deployed on the talos-cluster (`kubernetes/apps/custom/sakuga`), which alerts on
  `sakuga_last_success_timestamp_seconds` from `GET /metrics`. The cluster runs the image of the
  latest `v*` tag: a merge to `main` deploys nothing until a tag is pushed and Renovate's pin PR merges. Live state: `/log` shows each sweep
  and the last email's result; the pod's database is `/data/sakuga.db` (`kv(key, value)`).
- Linting: `.lefthook.toml` runs black, isort, flake8, pylint, ruff and mypy on commit with the
  configs in `.github/linters/` (super-linter runs the same); `lefthook install` once per clone.
  Before pushing: `python -m pytest -q`.

## Review rules

- **A cel is announced once.** New means new to the catalogue (`cels.first_seen`), never a new
  listing ID. Never alert per listing.
- **Mark an alert sent only after the send succeeded.** `instant_sent`, `digest_id` and `mailed` are
  written after the send (`notify.send`, `mail.send`) returns `sent`, and only on the cels it carried.
- **A failed search is not an empty one.** `retire()` skips every listing of a site whose search failed
  this sweep, or live cels go dark.
- **The first sweep is a baseline.** Its cels carry `baseline = 1` and never reach a digest. So does a
  source (`baselined_sources`) or franchise (`baselined_franchises`) added later, until it has read once.
- **The regular expression finds the franchise; the LLM may only narrow.** `catalog.keep` lets the LLM drop
  another show, a print or a reproduction, never add a franchise. Yahoo titles are padded with other shows'
  names, so a title's franchise stands only when the LLM names it too (`catalog.wrong_show`; Dragon Ball's
  the series counts, not the character: any Dragon Ball cel stands under broly, vegeta or goku). A Yahoo category's hint (`hinted`) needs the same.
- **Do not add evasion of bot checks** (stealth patches, captcha solving). A blocked load is retried as is.
