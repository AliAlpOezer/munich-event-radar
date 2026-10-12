# Repo rules: munich-event-radar

## What this repo is
The event radar: a harvester that finds, ranks and tracks AI/ML, startup and tech events
and organisations in Munich with their registration windows, and the page that shows
them. The private record behind it (session log, calendar, research sweeps, the
requirements map, and the copies of third-party pages the golden eval replays) lives in
the private repository `munich-event-radar-records`, checked out next to this one
(ADR 0006 D6-D7). Nothing from it is copied here.

## Non-negotiables

- **Never invent a date, deadline, price, organiser or club name.** A fact in `seed/`,
  `eval/golden.yaml` or an ADR carries its source URL and the date it was checked:
  `(source: <url>, checked 2026-10-07)`. If it could not be verified, write `UNVERIFIED`.
- **No copies of third-party pages.** Tests use hand-written fixtures in `tests/fixtures/`;
  the golden pages stay in the records repository and are read through `RADAR_FIXTURES_DIR`.
- **No secrets, hostnames or personal notes.** Secrets live in GitHub Actions secrets and
  Supabase Vault (ADR 0006 D5); notes and handoffs live in the records repository.
- Decisions that are expensive to reverse get an ADR in `docs/decisions/`, numbered,
  including the alternatives that were rejected and why.

## Style
- German terms stay in German, with a short gloss on first use.
- Plain dashes, never em dashes.
- Tables over paragraphs for anything comparable.

## Shared knowledge

Keep this file as the canonical project contract for every harness. Native adapters
reference it. Preserve source evidence and domain rules.

Start with `README.md`. The architecture is `docs/decisions/0002-event-radar-architecture.md`,
hosting and the database API are ADRs 0004-0006. The session log is
`../munich-event-radar-records/docs/context/event-tracking.md`; read it before continuing
earlier work, and record progress there, not here.
