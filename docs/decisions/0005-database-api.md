# ADR 0005: The database is the API

**Status:** DECIDED, being built (step 2 of ADR 0004)
**Date:** 2026-10-12
**Inputs:** ADR 0004 (public hosting: Supabase, Cloudflare Pages, no API server, RLS on
every table), its build steps 2-5, and Alp's go of 2026-10-09 ("lets start with the general
implementation first for the hosting"; confirmed 2026-10-12: "Lets continue with
implementing it for hosting").

## What this is for

ADR 0004 removed the API server: the page talks to Postgres through Supabase's Data API
(PostgREST). The Postgres schema itself then becomes the public interface, so it needs an
explicit shape: what anyone may read, what a logged-in user may change, what only an admin
may do, and how this is tested on a 4 GB laptop without Supabase.

## Decisions

| # | Decision | Rejected, and why |
|---|---|---|
| 1 | Tables stay in the private schema `radar`, which is never added to "Exposed schemas". PostgREST sees only schema `api` | Exposing `radar` (Supabase's default pattern, tables in `public`): the page would couple to table layout, and every migration could break the page or leak a new column |
| 2 | `api` holds views and functions only. It is not migrated: one file, `src/event_radar/sql/api.sql`, is dropped and recreated whenever its hash or the migrations change | Versioned `api` migrations: views and functions are code, not data, so a rebuild cannot drift and needs no down-migration |
| 3 | Reads: views with `security_invoker = true` over tables with RLS. Public tables (`event`, `organiser`, `source`) have a `SELECT` policy for `anon` and `authenticated`; personal tables (`user_state`, `intake`, `admin`) show a user only their own rows; `run` only admins | Default (definer) views: they run as the owner and bypass RLS, which Supabase's linter reports as an error |
| 4 | Writes: `SECURITY DEFINER` functions in `api` (`set_status`, `suggest_organisation`, `decide_intake`), each checking `auth.uid()` and the admin role itself, with a pinned `search_path`. `anon` and `authenticated` have no write grant on any table | Table grants plus RLS `WITH CHECK`: status transitions and a per-user cap on pending suggestions do not fit row policies, and a direct table write path is one more surface |
| 5 | Every table in `radar` has RLS enabled, including the ones nobody but the owner reads; a test enforces it. Default `EXECUTE` for `PUBLIC` is revoked from every function | RLS only where policies exist: one forgotten grant would then expose a table |
| 6 | Status transitions are enforced by a trigger on the state tables, the single authority; `model.USER_TRANSITIONS` goes | Checks in Python and SQL: two truths. The page keeps its copy only to choose which buttons to show |
| 7 | `user_state` is keyed by (user, event), `user_id` referencing `auth.users`, rows deleted with the user or the event. Alp's existing rows (no user) move to `user_state_unclaimed`; `event-radar grant-admin EMAIL --owner` claims them for the one `owner` admin. Until then, and for the old web app, the owner's state is the unclaimed table | A placeholder UUID for Alp: a fake user that would need special cases forever |
| 8 | A suggestion from a non-admin enters intake state `review`, unapproved, with `submitted_by`; nothing is fetched and no LLM is called until an admin approves it. At most 5 open suggestions per user. An admin's own suggestion enters `queued` (ADR 0003 flow). Duplicate domains are caught by the worker's check step, not in SQL | Profiling suggestions before review: lets strangers spend the 35 daily LLM calls (ADR 0002 D8) |
| 9 | A shim (`src/event_radar/sql/supabase_shim.sql`) creates `anon`, `authenticated`, `service_role`, `authenticator`, `auth.users` and `auth.uid()` only where missing. On Supabase it changes nothing; locally and in CI the same policies and grants run against the same names | `supabase start` (Docker): does not fit in 4 GB. Testing RLS only on a hosted project: slow, and tests must never reach a hosted database |
| 10 | Tests act as PostgREST does: `SET LOCAL ROLE anon` or `authenticated` with `request.jwt.claims` set | Testing through HTTP only: slower, and needs PostgREST running for every test |

## Later steps, decided here so step 2 fits them

- **Intake worker** (step 3, answers ADR 0004 open question 1): its own process on the box,
  woken by `LISTEN`/`NOTIFY` on a session connection, with a 30 s poll as the fallback, so a
  lost notification only delays work.
- **FastAPI retires** with step 4. Until then it keeps working for the owner over the
  Tailscale URL. Its `/api/events` derivations (cover de-duplication, staleness, organisers)
  are duplicated by `api.events` for that time, with a test holding them equal.
- **supabase-js is vendored** into the page (no third-party CDN, the same reasoning as the
  Google Fonts ruling in ADR 0004).
- **Local stand-in** for the Data API: PostgREST from Scoop behind a small dev proxy, as
  `authenticator`, exposing `api` only.

## Sources (checked 2026-10-12)

- A custom schema must be added to "Exposed schemas" and granted to `anon`,
  `authenticated` and `service_role`; Supabase's example grants `ALL`, which this design
  narrows on purpose: https://supabase.com/docs/guides/api/using-custom-schemas
- `anon` is the role for signed-out requests, `authenticated` for signed-in ones,
  `service_role` bypasses RLS: https://supabase.com/docs/guides/database/postgres/roles
- Whether Supabase's `postgres` role has `BYPASSRLS` is not stated there (UNVERIFIED). It
  does not matter: `postgres` owns the tables, and owners bypass RLS unless it is forced.
- Supabase's Postgres major is older than the local 18, so nothing newer than 15 is used
  (`security_invoker` views are 15).
