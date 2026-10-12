# ADR 0004: Public hosting on Supabase and Cloudflare Pages

**Status:** DECIDED, being built (steps 1-3 done locally, shaped by ADR 0005; not deployed)
**Date:** 2026-10-09
**Inputs:** Alp's request of 2026-10-09 (host the app for common use, Supabase plus
Cloudflare, pay for nothing, ask before changing any feature) and Alp's four choices of the
same day. Builds on ADR 0002 (store, invariants, D15 web app) and ADR 0003 (intake).

## What this is for

Anyone can open the radar on the web and see every Munich AI event and organisation with
its registration window. Someone who logs in also keeps their own applied, registered or
skipped status, and can suggest an organisation, which Alp approves before anything is
fetched. Nothing costs money.

## Decisions (Alp, 2026-10-09)

| Question | Chosen | Rejected, and why |
|---|---|---|
| Audience | Public board; login adds personal status and organisation suggestions | Invite-only: hides a public good for a smaller legal surface. Full multi-user (own profile, organisers, notifications per user): makes the judge, pushes and calendar per-user, far larger for no current user |
| Store | Supabase Postgres is the only store; SQLite goes | SQLite on the box plus a published copy: user status and intakes would sync back the other way, two truths |
| Harvester host | Stays on the box (timer, Playwright, snapshots on disk) | GitHub Actions: the repo is private (minute cap), snapshots would need Supabase Storage (1 GB) and the browser unit a runner image |
| Login | Google and GitHub OAuth through Supabase Auth | Email magic link: Supabase's built-in mail reaches only project team members, 2 per hour; own SMTP usually needs a paid domain |
| Page host | Cloudflare Pages, static, on `munichai.pages.dev` (name chosen 2026-10-12) | FastAPI on Workers: 10 ms CPU per request on the free plan. Cloudflare Tunnel to the box: the site would go down with the box (offline about 2026-08-27 to 2026-10-07). A custom domain: costs money |

## Shape

```
 Cloudflare Pages: static page --supabase-js (anon key)--> Supabase
                                                           - Postgres, the one store
                                                           - Auth: Google, GitHub
                                                           - RLS on every table
                                                                 ^
 the box:  runner (timer) + intake worker ----------------------+  service role, server only
            snapshots stay on the box's disk
```

There is no API server. The page reads tables and views directly; RLS is the only gate.
The derivations in today's `/api/events` (cover de-duplication `SHARED_IMAGE`, the
organisers behind each source) move into a Postgres view or into columns the resolver
writes.

## What changes ownership

| Thing | Today | Hosted |
|---|---|---|
| Events, organisers, sources | SQLite, read by FastAPI | Postgres; public read through RLS |
| Observations, extraction cache, budget, push ledger, run ledger | SQLite | Postgres; no public access |
| Raw snapshots | files on the box | unchanged; never published (copies of third-party pages) |
| `user_state` | one row per event, Alp's | keyed by (user, event); each user reads and writes only their own rows |
| Intake | web-process thread, `POST /api/intakes` | logged-in insert into a queue; a worker on the box claims rows. Every user-submitted intake waits in `review` for an admin before any fetch or LLM call (ADR 0003's `review` state, now mandatory), so strangers cannot spend the 35 daily LLM calls (ADR 0002 D8) |
| Organiser demotion and removal, run and budget status | web app, anyone on Tailscale | admins only (an `admin` table checked by RLS) |
| Telegram pushes, Google Calendar | Alp's | unchanged, Alp's only; the notifier reads Alp's `user_state` |
| `profile.yaml` | Alp's judge policy | unchanged; it is the shared default |
| Secrets | `/etc/event-radar/env` | unchanged, plus the Supabase service role key and database URL. The page ships only the anon key |

## Free-tier constraints (checked 2026-10-09)

- Supabase Free: 500 MB database, 5 GB egress, 50,000 monthly active users, 2 active
  projects (https://supabase.com/pricing). Local `radar.db` is 0.7 MB, so snapshots must stay off the database
  and the headroom is large.
- Supabase pauses a free project after a week of low "user database activity"
  (https://supabase.com/docs/guides/platform/free-project-pausing). Whether the
  harvester's writes count is UNVERIFIED; watch for the warning email.
- Supabase built-in SMTP: team members only, 2 messages per hour
  (https://supabase.com/docs/guides/auth/auth-smtp).
- Cloudflare Workers Free: 10 ms CPU per request, 100,000 requests a day
  (https://developers.cloudflare.com/workers/platform/limits/). Cloudflare Pages Free: 500 builds a
  month, static requests unmetered; Pages Functions count as Workers requests
  (https://developers.cloudflare.com/pages/platform/limits/). The page uses no Functions.

## Public-site obligations (not legal advice)

- Logged-in users' emails are personal data: a privacy notice (Datenschutzerklärung) is
  needed, and a public German site likely needs an Impressum. Contents are Alp's to supply.
- Fonts are self-hosted, not loaded from Google Fonts (LG München I, 20.01.2022,
  3 O 17493/20, held that embedding Google Fonts transmits visitors' IPs unlawfully).
- The banner's CC BY-SA 3.0 credit stays.

## Build order

1. Port `store.py` to Postgres (psycopg); tests run against Postgres. Migrate the box's data.
2. Schema for hosting: RLS policies, per-user `user_state`, the `admin` table, the public
   events view.
3. Intake as a queue worked on the box; user intakes always wait for admin review.
4. The page reads Supabase via supabase-js, adds Google and GitHub login; deploy to Pages.
5. Self-hosted fonts; privacy and Impressum pages.
6. Repoint the runner at Supabase; retire `event-radar-web.service` and the Tailscale URL.

Each step changes behaviour, so each starts only after Alp agrees to it (Alp, 2026-10-09:
"Before changing any feature, ask me").

## Open questions

1. How intakes reach the worker quickly: Postgres `LISTEN`/`NOTIFY` over a direct
   connection, or a poll every minute. Decided in ADR 0005: both, `NOTIFY` with a 30 s poll
   as the fallback.
2. Where tests get Postgres on the 4 GB laptop: a native Windows Postgres, or a second free
   Supabase project as the test database. Decide in step 1.
3. Who the admins are beyond Alp. Answered (Alp, 2026-10-12): nobody; Alp is always the only admin.
4. Does the harvester's activity prevent pausing? Observe after step 6.
