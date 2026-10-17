# Munich event radar

Finds, ranks and tracks the AI/ML, startup and tech events, organisations, conferences and
programmes in Munich, with their registration windows. A harvester fetches organiser pages,
ticket platforms and calendars, parses them (structured data first, an LLM only where a
page has no registration fields), merges duplicates and ranks each event A, B or C against
`profile.yaml`. A static page shows the board; logging in adds a personal star and status per
event and lets you suggest an organisation.

## Layout

| Path | Holds |
|---|---|
| `src/event_radar/` | The harvester: `fetch`, `render` (Chromium), `parse`, `extract` (LLM), `resolve`, `judge`, `intake`, `store` (Postgres) |
| `site/` | The page, served as is by Cloudflare Pages |
| `seed/organisers.yaml` | The organisers tracked from the start |
| `profile.yaml` | What counts as A, B or C |
| `eval/golden.yaml` | The golden set the judge is scored against |
| `tests/` | pytest on hand-written fixtures, and node tests for the page's pure modules |
| `tools/devsite.py` | A local stand-in for Supabase |
| `docs/decisions/` | ADRs, from the architecture (0002) to hosting (0004-0006) and stars (0007) |

The golden eval replays frozen copies of third-party pages. They are not published here:
`event-radar replay` reads them from `RADAR_FIXTURES_DIR` (ADR 0006 D7).

## Development

The store is Postgres (ADR 0004). Tests need a local server and never read
`RADAR_DATABASE_URL`; each test works in its own throwaway schema.

| Step | Command |
|---|---|
| Install Postgres (Windows, no admin) | `scoop install postgresql` |
| Start it | `pg_ctl start -D ~/scoop/persist/postgresql/data -l ~/scoop/persist/postgresql/pg.log` |
| Create the test database once | `psql -U postgres -h localhost -c "create database radar_test"` |
| Run the tests | `.venv/Scripts/python -m pytest` (override with `RADAR_TEST_DATABASE_URL`) |
| Move a SQLite store into Postgres | `RADAR_DATABASE_URL=... event-radar import-sqlite path/to/radar.db` |
| Test the page's pure modules | `node --test "tests/site/*.test.mjs"` (Node 24, no npm) |
| Run the page locally | `scoop install postgrest`, then `python tools/devsite.py` and open http://127.0.0.1:8010/ |
| Make a local login an admin | `RADAR_DATABASE_URL=postgresql://postgres@localhost/radar_dev event-radar grant-admin EMAIL` |
| Score the golden set | `RADAR_FIXTURES_DIR=<frozen pages> event-radar replay` |

## The page

`site/` is the whole front end, published as is by Cloudflare Pages: plain ES modules in
`site/js/`, supabase-js vendored in `site/vendor/`, response headers in `site/_headers`. It
reads and writes only through the `api` schema (ADR 0005). `site/config.js` holds the
project URL and publishable key, which are public by design.

`tools/devsite.py` stands in for Supabase on this machine: PostgREST on the `api` schema,
a fake `/auth/v1` that logs in any email through the same PKCE flow, and the site with its
headers. It refuses any database that is not local.

## Running (ADR 0006)

Every job runs on GitHub Actions; no machine of ours has to be on.

| Workflow | When | Does |
|---|---|---|
| `harvest` | daily, 04:17 UTC | `event-radar seed`, then `harvest`: fetch what is due, extract with OpenRouter (at most 60 min), judge |
| `browse` | Sundays 03:17 UTC | `event-radar browse`: renders JavaScript-only sources with Chromium |
| `intake` | dispatched by Postgres when an intake is queued, and hourly | `event-radar intakes`: works the queue, then exits |
| `test` | every push and pull request | ruff and pytest against a Postgres 17 service, and the page's node tests |

One-time setup:

| Where | What |
|---|---|
| GitHub, Settings > Secrets > Actions | `RADAR_DATABASE_URL` (Supabase session pooler) and `OPENROUTER_API_KEY`; optionally `GROQ_API_KEY`, the fallback when OpenRouter is spent or down (ADR 0010) |
| GitHub, a fine-grained token | this repository only, Actions: read and write; it dispatches `intake.yml` |
| Supabase, SQL editor | `select vault.create_secret('https://api.github.com/repos/<owner>/<repo>/actions/workflows/intake.yml/dispatches', 'intake_dispatch_url');` and `select vault.create_secret('<token>', 'intake_dispatch_token');` |

Without the two Vault secrets nothing is dispatched, and the hourly `intake` run works the
queue. A failed scheduled run emails whoever last changed the workflow's schedule.
