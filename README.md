# Munich event radar

Finds, ranks and tracks the AI/ML, startup and tech events, organisations, conferences and
programmes in Munich worth Alp's time, with their registration windows. Split out of
`munich-masters-career` on 2026-10-07 (`docs/decisions/0001-separate-repo.md`).

## Layout

| Path | Holds |
|---|---|
| `radar/event-radar.md` | Organisers that matter and upcoming events, ranked A/B/C, with registration |
| `radar/clubs.md` | Munich AI and entrepreneurship map: clubs, joint institutions, programmes |
| `radar/fairs.md` | Job fairs and recruiting events |
| `timeline/calendar.md` | Every event date and registration action |
| `research/` | Raw sweeps, never cited directly |
| `docs/context/` | Handoffs: `event-tracking.md`, and the agent design in `wayfinder/event-radar/` |
| `docs/decisions/` | ADRs |

## Status (2026-10-07)

- Curated radar and calendar moved here with their git history.
- Next: the event-radar agent and app. Requirements: `docs/context/wayfinder/event-radar/map.md`
  (wayfinding complete). Architecture: `docs/decisions/0002-event-radar-architecture.md`.
  Then UI design, then implementation.

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

## Running (ADR 0006)

Every job runs on GitHub Actions; no machine of ours has to be on.

| Workflow | When | Does |
|---|---|---|
| `harvest` | every 2 h, 05:17-21:17 UTC | `event-radar seed`, then `run`: fetch, extract with OpenRouter, judge |
| `browse` | Sundays 03:17 UTC | `event-radar browse`: renders JavaScript-only sources with Chromium |
| `intake` | dispatched by Postgres when an intake is queued, and hourly | `event-radar intakes`: works the queue, then exits |
| `test` | every push and pull request | ruff and pytest against a Postgres 17 service |

One-time setup:

| Where | What |
|---|---|
| GitHub, Settings > Secrets > Actions | `RADAR_DATABASE_URL` (Supabase session pooler) and `OPENROUTER_API_KEY` |
| GitHub, a fine-grained token | this repository only, Actions: read and write; it dispatches `intake.yml` |
| Supabase, SQL editor | `select vault.create_secret('https://api.github.com/repos/<owner>/<repo>/actions/workflows/intake.yml/dispatches', 'intake_dispatch_url');` and `select vault.create_secret('<token>', 'intake_dispatch_token');` |

Without the two Vault secrets nothing is dispatched, and the hourly `intake` run works the
queue. A failed scheduled run emails whoever last changed the workflow's schedule.
