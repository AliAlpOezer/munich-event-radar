# ADR 0006: Harvest on GitHub Actions, and a public repository

**Status:** DECIDED, being built. D1's harvest schedule is superseded by ADR 0009 (once a day)
**Date:** 2026-10-12
**Inputs:** Alp, 2026-10-12: "go ahead with GitHub Actions. We can convert the project also
into a public project", provided the whole codebase first meets a clean-code bar; and Alp's
three choices of the same day (flip this repository and rewrite its history; keep
third-party page copies private; process an approved intake instantly). Amends ADR 0004
(harvester host) and supersedes ADR 0005 D11-D14 (the always-on intake worker).

## What this is for

The radar keeps running when no machine of Alp's is on. The box was offline from about
2026-08-27 to 2026-10-07 and again on 2026-10-12. Since ADR 0004 everything durable lives
off the box (Supabase, Cloudflare Pages, OpenRouter), so the box is only a scheduler with
a disk. The LLM never ran there:
extraction has always called OpenRouter over HTTPS (ADR 0002 D8).

## Decisions

| # | Decision | Rejected, and why |
|---|---|---|
| 1 | Every job runs on GitHub-hosted runners of this repository: `harvest` every 2 h from 05:17 to 21:17 UTC (07:17 to 23:17 Berlin in summer, an hour earlier in winter, since GitHub's cron knows only UTC), `browse` weekly (Playwright), `intake` on dispatch (D3), `test` on every push and pull request against a Postgres service container. The repository is public, so standard runners cost nothing (https://docs.github.com/en/actions/concepts/billing-and-usage, checked 2026-10-12) | The box or another always-on box: one more machine to keep alive, patch and secure. Oracle's free VM: the same, on someone else's capacity terms. Cloudflare Workers: 10 ms CPU per request, no Python or Chromium. Supabase Edge Functions plus `pg_cron`: a rewrite in Deno, still without a browser |
| 2 | Page bodies move into Postgres: table `page_body` (content hash, gzip bytes), written by the fetcher and renderer, never exposed through `api`. Pruned after each harvest to the bodies still referenced by a URL's latest snapshot or a pending extraction. The extraction cache already lives in Postgres, keyed by hash, so a lost body never costs an LLM call | Runner disk: gone after each job. `actions/cache`: evicted after a week unused, and racy between jobs. Supabase Storage: a second API and key for a few MB |
| 3 | An intake becoming `queued` makes Postgres call GitHub's `workflow_dispatch` for `intake.yml` through `pg_net` (asynchronous, sent after commit; https://supabase.com/docs/guides/database/extensions/pg_net, checked 2026-10-12). The token is a fine-grained token limited to this repository's Actions, kept in Supabase Vault. The job drains the queue and exits. An hourly schedule of the same workflow is the fallback when a dispatch is lost or the token has expired | A drain every 15 minutes: slower, and 96 empty jobs a day. Only with the next harvest: up to 2 h. Keeping the LISTEN/NOTIFY worker: needs an always-on host, which D1 removes |
| 4 | Concurrency: each workflow has its own GitHub `concurrency` group without cancelling the running job. In Postgres, one blocking session advisory lock serialises intake drainers (so requeueing cut-off intakes at the start stays safe, as in ADR 0005 D13), and another serialises judging, the one step that rewrites `event` | One lock around whole jobs: an approved intake would wait behind a harvest run. The file lock in `data/run.lock`: there is no shared disk |
| 5 | Secrets: GitHub Actions secrets hold `RADAR_DATABASE_URL` (session pooler) and `OPENROUTER_API_KEY`; Vault holds the dispatch token. Workflows triggered by pull requests run only `test`, which needs no secret; forks never see one | Secrets in the repository or in workflow files |
| 6 | This repository becomes public after its history is rewritten. Before the rewrite, the full history is pushed to a new private repository, `munich-event-radar-records`, which from then on keeps the personal record (`timeline/`, `radar/`, `research/`, the session log `docs/context/event-tracking.md`, the wayfinder notes) and every copy of a third-party page (`eval/fixtures/`, `research/**/pages/`, the real pages in `tests/fixtures/`). The public tree keeps code, tests on hand-written fixtures, `seed/`, `profile.yaml`, `eval/golden.yaml`, and the technical ADRs, scrubbed of private details | Flipping as-is: publishes personal notes and copies of others' pages. A fresh public repository with a new first commit: was offered; Alp chose to keep this one |
| 7 | The golden eval reads its fixtures from `RADAR_FIXTURES_DIR` (default `eval/fixtures`, ignored in this repository) and runs from a checkout of the private records next to this one | Publishing the fixtures: copyright, and ADR 0004 already keeps page copies private |
| 8 | FastAPI, `event-radar-web.service`, the Tailscale URL, `deploy/` and the systemd units are deleted; the page moves to Cloudflare Pages with supabase-js (ADR 0004 step 4) before the repository goes public | Keeping FastAPI until later: it has no host after D1, and a public repository would show two front ends |
| 9 | The migrations are squashed into one initial schema before the flip. The hosted database holds no data yet (checked 2026-10-12), so its `radar` and `api` schemas are dropped and rebuilt | Keeping 0001-0003: their renames only make sense against a SQLite past that no reader of the public code has |

## Shape

```
 GitHub Actions (public repo)                                   Supabase
  harvest.yml  every 2 h  --- session pooler (secret) -------->  Postgres: radar (private)
  browse.yml   weekly     --------------------------------------> page_body, events, ...
  intake.yml   dispatch + hourly fallback  <-- pg_net POST ----  trigger on intake 'queued'
  test.yml     push / PR, Postgres service container            api (views, functions)
                                                                        ^
 Cloudflare Pages: static page --supabase-js (publishable key)---------+
```

## The code bar before the flip

Alp asked that a reader find the code easy to navigate and self-revealing: simple, small
units, one reason to change each. Concretely, as found in the review of 2026-10-12:

| Today | Change |
|---|---|
| `store.py`: one 600-line class over every table | `store/` package: the connection and migrations, plus one small repository per aggregate (`sources`, `pages`, `observations`, `extraction`, `events`, `intakes`, `runs`) |
| `runner.py`: free functions passing seven collaborators by position; `intake.py` imports its private `_profile` | A `Harvester` that owns fetcher, renderer, store and profile; a typed `Profile` loaded once in `profile.py` |
| `intake.py`: 620 lines mixing URL rules, link discovery, the LLM prompt and the state machine, plus the old web API | `intake/` package: `urls`, `discovery`, `profiling`, `service`; the API side goes (SQL functions own it since ADR 0005) |
| `Renderer` calls `Fetcher._check_robots`, `_pace`, `_fail` | The politeness rules (robots, pacing, recording a failure) are one public collaborator both use |
| States as bare strings: intake, extraction job, run outcome | `StrEnum`s next to `UserStatus` |
| Duplicates: `_value(fields, name)` twice, `_anchors` beside `html.links`, `removeprefix("www.")` four times, a second `now_iso` | One home each |
| `Settings` carries Tavily, Telegram, Google and Eventbrite keys no code reads | Removed until a feature needs them |
| `cli.py` special-cases two commands outside its table | `argparse` subcommands, one function each |
| `app.js`: 950 lines in one file | Rewritten as small ES modules in ADR 0004 step 4 |

## Build order

1. Page bodies in Postgres, the two advisory locks, `event-radar intakes` (drain and exit),
   the dispatch trigger with a shim for `pg_net` and Vault so tests run without Supabase,
   the four workflows; delete the worker loop, the file lock, `deploy/`.
2. The clean-code pass above, behaviour unchanged, golden replay and tests equal before and
   after.
3. ADR 0004 step 4: the static page on supabase-js with Google and GitHub login; FastAPI goes.
4. Squash the migrations; rebuild the hosted schemas; import the data.
5. Push the full history to `munich-event-radar-records` (private); rewrite this repository's
   history; Alp checks the result; force-push; make it public; set the secrets and the
   Vault token; first scheduled run.

Steps 4 and 5 touch the hosted database and publish the repository: each waits for Alp's go
at that moment.

How step 2 landed (2026-10-12), where it differs from the table:

| Change | Why |
|---|---|
| Two more repositories, `organisers` and `users` (admins and statuses); the LLM budget sits in `extraction` | The registry and the people are aggregates of their own; the budget only meters LLM calls |
| `migrations/` and `sql/` moved into `store/` | They are the store's schema |
| The intake API side (`submit`, `decide`, URL normalising) moved into `web/` until step 3 deletes it | Its only caller is FastAPI; the SQL functions of ADR 0005 replace it |
| The `Extractor` returns its events and the caller stores them | The golden replay stores into a scratch schema but caches LLM answers in the real one |
| `IntakeService` takes its HTTP client, address guard and renderer as arguments | Tests pass stand-ins instead of monkeypatching modules |
| `event-radar run` is now `event-radar harvest` | The command names the job it starts, as the workflow does |

How step 3 landed (2026-10-12):

| Change | Why |
|---|---|
| The page is `site/`, plain ES modules, one per concern (`api`, `board`, `timeline`, `drawer`, `intakes`, `account`, ...), with no build step; supabase-js 2.117.2 is vendored byte-exact from the npm tarball | An esbuild bundle of supabase-js: a build step for a file the tarball already ships |
| Login uses the PKCE flow, which returns `?code=` | The implicit flow returns tokens in the hash, which the page uses for its routes |
| `api.board_status()` tells anyone when the board last harvested and gives a version the page polls each minute while visible; run detail stays admin-only | Polling `run`: admin-only by ADR 0005 D3. A timestamp alone: stamps have second precision and missed changes |
| `theme-init.js` is a file, so the CSP allows scripts from `'self'` only | An inline script and a CSP hash that changes with every edit |
| `facts.js` and `time.js` have node tests in `tests/site/`, run in `test.yml` on Node 24 | A browser test runner in CI: the rest is checked end to end against `tools/devsite.py` |
| `tools/devsite.py` stands in for Supabase locally (ADR 0005, "Local stand-in") | `supabase start`: its containers do not fit in 4 GB |
| FastAPI, `web/`, the `web` extra and `UserStatus` are gone | Nothing else used them |

Not done in step 3, each waiting for Alp: deploying to Pages (privacy and Impressum, ADR 0004
step 5, come first) and a real Google or GitHub OAuth login.

How step 4 landed (2026-10-12):

| Change | Why |
|---|---|
| `0001_initial.sql` now builds the final schema directly; 0002-0004 are gone. The stem stays `0001_initial`, so a local database already at 0004 counts as migrated | A new stem: every existing local database would rerun it over tables that exist |
| Proof: fresh databases built from the old four files and from the squash give the same `pg_dump --schema-only` of `radar` and `api` (grants, policies, triggers included), except that Postgres 18 kept the NOT NULL constraint names `user_state_*_not_null` on the renamed `user_state_unclaimed`; the squash gives it its own. Postgres 17, as hosted, names no NOT NULL constraints | Trusting a read-through of the SQL |
| Page bodies are not imported: none of the 136 pending LLM jobs in the 2026-10-08 copy has its body on disk, and `Extraction.pending` skips a job without a body until a fetch stores one | Importing the 14 bodies in `data/snapshots/`: no pending job reads them |

The hosted rebuild ran on 2026-10-12 (Alp ran the drop and import, since Claude Code's auto
mode refused a `DROP SCHEMA` on the hosted database): migration `0001_initial` only, 18
tables all with RLS, pg_net 0.20.4 enabled by the shim, and the 2026-10-08 copy imported (22
organisers, 174 sources, 164 events, 2 intakes, 1 unclaimed status, 3 runs; identity
sequences past their max ids; no dispatch sent). Over HTTP with the publishable key, `events`
counts 164 and `organisers` 22, `runs` is 401, and `rpc/board_status` reports the
2026-10-08 harvest.

How step 5 landed (2026-10-12):

| Change | Why |
|---|---|
| Hand-written pages replace the CDTM, Luma and BAdW copies in `tests/` | D6: the public tree holds no third-party page |
| `event-radar replay` and `freeze` read `RADAR_FIXTURES_DIR` (default `eval/fixtures`, ignored here) | D7 |
| Before the rewrite, every branch went to `munich-event-radar-records` (private, Actions off), which from then on holds only the record | D6 |
| `git filter-repo` dropped `radar/`, `timeline/`, `research/`, `docs/context/`, `eval/fixtures/`, the CDTM pages and ADR 0001 from every commit; commits left empty fell away (56 on `hosting-plan` to 30) | D6 |
| The same pass rewrote text in every commit and message: the box's host name became "the box", and the other project on it, with what it holds, "another project". The rules file stays in the records repository, since it names what it removes | A host name and what else runs beside it map a target; the design reads the same without them |
| ADR 0001 (the split from a study-planning repository) stays in the records repository | It is about personal repositories, not the radar |
| Only `main` is published; `hosting-plan` and `frontend-colour` remain in the records repository | Both are merged into `main` by the rewrite |
| The public repository is a new one under the old name; the old repository is renamed `munich-event-radar-old` and stays private (Alp, 2026-10-12). This amends D6, which kept this repository | GitHub keeps `refs/pull/1/head` and `refs/pull/2/head` on the old history, whose tree holds the record and the page copies; a force-push does not move them, and only GitHub Support can remove them |

## Open questions

1. The data to import: the 2026-10-08 laptop copy, or a fresh copy if the box comes back
   once (it holds the runs and any status changes since).
2. GitHub's fine-grained token permission for `workflow_dispatch`: the REST docs name only
   the classic `repo` scope (checked 2026-10-12); "Actions: write" is expected, UNVERIFIED
   until the token is created.
