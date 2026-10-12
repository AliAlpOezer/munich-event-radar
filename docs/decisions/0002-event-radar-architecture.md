# ADR 0002: Event-radar architecture

**Status:** DECIDED for design; implementation has not started
**Date:** 2026-10-07
**Inputs:** the wayfinder map (Q1-Q9, R1-R4), kept in the private records repository.
Those decisions are cited here, not re-argued.

## What this is for

Alp hears about every free, in-person Munich AI event worth attending while its
registration is still open, with a sourced window, without having to remember to look.

True whatever the technology: *a missed registration window is the failure, a noisy
feed is not. Every fact shown traces back to a page the system read, and silence from
the system is never mistaken for "nothing new".*

## Invariants

1. **No fact without a source.** Every stored event field points to the observation it
   came from (URL, fetch time, source class TICKET, OWN or AGG). An LLM-extracted field
   with no verbatim quote from the fetched page is stored as UNVERIFIED, never as a value.
   A year the page does not state is UNVERIFIED. *Enforced by:* the observation schema
   (a value requires evidence) and the grounding verifier.
2. **Read-only on the web.** The agent never submits a form, logs in or registers (Q2).
   It writes only to its store, the "Event radar" calendar, Telegram, healthchecks pings
   and its own git branch. *Enforced by:* the fetcher issues GET and HEAD only; no
   ticket-platform credential exists; the calendar credential reaches only the one
   calendar shared with it (D9).
3. **Registration before date.** No A or B event is shown without a registration state:
   a sourced window, or `unknown`/`unchecked` with a recheck scheduled. `unknown` is
   allowed only after the organiser page and any linked ticket page were read; an
   aggregator listing alone gives `unchecked`. *Enforced by:* resolver precedence (D5)
   and the planner.
4. **Only logged code drops an event.** The scope filter (past, online-only, outside
   Munich, Q4) is deterministic and writes a drop reason. The model extracts features; it
   never removes or tiers an event. Price never drops an event (Q5). *Enforced by:* the
   judge; a dropped event without `drop_reason` violates a constraint.
5. **Outward effects converge, never duplicate.** A rerun updates calendar entries in
   place, because each entry's ID is derived from the stable event key. Every push is
   recorded under a unique (event, kind, fact hash). *Enforced by:* the calendar
   reconciler and a unique constraint on the push ledger.
6. **Silence is not success.** Every run pings a dead-man's switch off the box. A source
   that used to yield events and now yields none is flagged, never read as "no events".
   *Enforced by:* the run ledger, source health and healthchecks.io (D10).
7. **Free quotas are ceilings.** An exhausted LLM or search budget defers work to a later
   run. It never triggers a paid call, a retry loop or a guessed value (Q9). *Enforced by:*
   the budget ledger in the LLM router and the search provider.

## Shape

```
 timer: every 2 h, 07-23          timer: weekly, night
          |                               |
 +--------v-------------------------+  +--v--------------------+
 | runner (flock, single flight)    |  | browser unit          |
 |  plan > fetch > parse >          |  | (MemoryMax cap,       |
 |  extract+verify > resolve >      |  |  ~4 JS-only sources)  |
 |  judge > calendar > notify >     |  +--+--------------------+
 |  export   (discovery when due)   |     |
 +--------+-------------------------+     |
          | every handoff is a row        |
 +--------v-------------------------------v--+
 | SQLite store (WAL) + snapshot files       |<-- web app: FastAPI, Tailscale only,
 +-------------------------------------------+    also consumes Telegram button taps
```

No stage calls another. The runner orders them, and each one reads its due work from the
store and writes its result back, so any stage can be rerun alone and a crash loses
nothing that was committed.

## Components

| Component | Shape | Sole writer of | Autonomy | Fails how |
|---|---|---|---|---|
| Store | store | schema, migrations | n/a | disk loss: restore from the export branch (D12) |
| Planner | judge, pure | due fetches, rechecks | autonomous | a late recheck; the golden live check shows it |
| Fetcher | collector | snapshots, fetch health | autonomous | site down, robots disallow, layout change |
| Browser unit | collector | snapshots for JS-only sources | autonomous, weekly | out of memory or timeout: sources flagged `needs_browser` |
| Parsers | collector, pure | observations from structured data | autonomous | zero yield after a site change, flagged |
| Extractor | producer | LLM observations (after the verifier), extraction cache | autonomous | budget gone: job stays pending |
| Grounding verifier | verifier | accept or reject per field | autonomous | fails closed: the field becomes UNVERIFIED |
| Resolver | judge | event identity, aliases, resolved fields | autonomous | a wrong fuzzy merge, visible in the UI |
| Judge | judge | tier, tier reasons, drop reason | autonomous | a profile that is off; fixed by editing `profile.yaml` |
| Discovery | collector | new organisers and sources | autonomous (Q2a) | a junk organiser; demoted or removed in the UI |
| Calendar reconciler | actuator | entries in the "Event radar" calendar | autonomous (Q2b, Q7) | API down: the diff waits for the next run |
| Notifier | notifier | push ledger | autonomous | Telegram down: pending pushes go next run |
| Exporter | producer | branch `radar-export` | autonomous | push fails: retried next run |
| Web app | gate | user status, feedback, organiser demotion and removal | human | down: pushes still arrive; Telegram holds taps 24 h |
| Golden evaluator | verifier | eval reports | on demand and in CI | n/a |

Registration on a ticket platform is not a component. It stays Alp's action (Q2c).

## Data model

```
organiser   id, name, homepage, kind, ai_focus, status (active|demoted|removed|dormant),
            discovered_via (seed|search|crawl|event_mention|manual), discovered_at, discovery_url
source      id, organiser_id, url, access (ics|api|rss|html|browser), parser, cadence,
            last_ok_at, last_yield, empty_streak, error_streak
snapshot    content_hash, url, fetched_at, http_status, etag, path        raw, gzip on disk
observation id, snapshot_hash, source_class (TICKET|OWN|AGG), method (parser@v | llm:model@prompt_v),
            platform_key, fields {name: {value, quote}}                    append-only
event       key, aliases, resolved fields with provenance {field: observation_id}, merge_basis,
            tier, tier_reasons, drop_reason, user_status, feedback
push_ledger event_key, kind, fact_hash, sent_at, message_id       unique (event_key, kind, fact_hash)
budget      provider, day, used, cap
run         id, started_at, finished_at, per-stage counts, outcome
```

Registration and prerequisites, the fog item from the map:

| Field | Values | Notes |
|---|---|---|
| `reg.mode` | none_needed, open_signup, approval, application, invite_only, ticket_purchase, unknown | Luma `require_approval` maps to approval |
| `reg.opens_at`, `reg.closes_at` | a time with precision (datetime, date, month) and `year_stated` | "closes 27 Oct" with no year gives `year_stated=false`, shown UNVERIFIED (the TUM.ai case) |
| `reg.availability` | not_open, open, waitlist, sold_out, closed, unknown, unchecked | `unchecked` means only an aggregator was read (invariant 3) |
| `reg.url` | ticket or form URL | |
| `reg.spots_left` | integer or null | Luma `spots_remaining`, pretix widget |
| `price` | free, free_student_route, paid, unknown | `unknown` is tiered as free: recall first (Q5) |
| `extra_signups` | list of {label, url, closes_at} | for example the ITCS masterclasses |
| `prerequisites` | list of {kind: student_status, university, application, membership, language, other; text} | displayed, never used to drop |

Every value carries its provenance. Times are stored in UTC with the source's offset and
shown in Europe/Berlin.

`user_status` is the one human-owned state machine: `none > applied > registered`,
`none > registered`, `none|applied > skipped`, and any state back to `none` (undo). The
notifier stops closing reminders at `registered` or `skipped`; `applied` gets an
approval-watch reminder the day before the event (the Munich NLP x JetBrains case).

## Seams

| From > To | Carrier | Contract | On failure | Repeats and order |
|---|---|---|---|---|
| Web > fetcher | HTTPS GET with an honest user agent, RFC 9309 robots, 1 request per 5 s per host, conditional GET | snapshot row plus file, keyed by content hash | error streak on the source; no observation; retried when next due | an unchanged hash creates no work |
| Fetcher > parsers | store, then a pure call | `parse(snapshot) -> observations`, parser name and version recorded | parse error recorded; zero yield where history had events raises `empty_streak` | upsert on (snapshot, parser@v): reparsing is idempotent |
| Parsers > extractor | `extraction_job` rows (snapshot, purpose, priority) | pages whose structured data lacks registration fields, for events that could be A or B; cleaned text | budget gone: pending; every model fails: attempt count, after 3 the event shows `needs_look` in the UI | cached by (content hash, schema version, prompt version): an unchanged page is never sent twice (Q9) |
| Extractor > verifier | in-process call | each field is `{value, quote}` | an ungrounded field becomes null and is logged | n/a |
| Router > OpenRouter, later Groq | HTTPS POST, strict JSON schema | the R1 chain; the model list is read each run | upstream 429 or 503 in a 200 body: next model. OpenRouter's own 429: no more LLM this run. 400 on `response_format`: `json_object` mode or skip the model | n/a |
| Discovery > Tavily | `SearchProvider` interface | query in, URLs out; a URL only seeds a fetch (R2) | 429: no more search this run | n/a |
| Observations > resolver | store | identity (D4) and precedence (D5) | n/a, pure | recomputes the touched events; order-independent |
| Resolver > judge | in-process, `profile.yaml` | features in, tier and reasons out (D6) | an invalid profile fails the run before any outward stage | n/a |
| Store > calendar | Google Calendar API via a service account (D9) | desired set = each A or B event x {event, registration opens, registration closes}; entry ID = hash(event key, kind) | API error: the diff waits; alert after 3 failed runs | inserting an existing ID becomes an update; entries outside the desired set are deleted; a read-back confirms |
| Store > Telegram | Bot API | push kinds: new A or B with an open window, opens today, closes within 72 h, fewer than 20 spots left, daily digest | send fails: stays pending, sent next run | at least once; a duplicate only if the process dies between send and ledger write (accepted, D13) |
| Telegram > web app | `getUpdates` long poll, single consumer | button taps: applied, registered, skip, thumbs up, thumbs down | web app down: Telegram keeps updates for 24 h | last write wins; it is the same human as the UI |
| Runner > healthchecks.io | HTTPS ping: start, success, fail | ping body is the run summary | a failed ping is ignored; missed pings alert off the box | n/a |
| Store > exporter > git | deploy key, branch `radar-export` | `radar/generated/events.md`, `calendar.md`, `state.json` | push fails: retried next run | commit only when the content hash changed |

**Dependency direction.** Everything depends inward on the domain model and the store.
Adapters (HTTP, LLM, calendar, Telegram, Tavily, git) depend on those and never the
reverse. Policy is data in the repo (`profile.yaml`, the precedence table, the discovery
keyword set) and is read by code. The web app reads the store and writes only user-owned
fields; nothing depends on the web app.

## Field ownership

One writer per fact (masters repo ADR 0002, invariant 6).

| Field | Writer | Conflict rule |
|---|---|---|
| organiser and source rows | seed importer (once), discovery (insert only) | discovery never changes an existing row |
| `organiser.ai_focus` | seed importer (a re-import applies seed edits), discovery at insert | |
| `organiser.status` | web app | `removed` is a tombstone: discovery cannot re-add that domain |
| snapshot, source health | fetcher, browser unit, parsers | |
| observation | parsers, extractor through the verifier | append-only |
| event identity and resolved fields | resolver | derived; rebuildable from observations |
| tier, reasons, drop reason | judge | derived |
| `user_status`, feedback | web app, including Telegram taps | last write wins |
| "Event radar" calendar entries | calendar reconciler | sole writer; manual edits there are overwritten |
| push ledger | notifier | |
| budget and run ledgers | router and search provider, runner | |

## Discovery

Weekly, when the planner says it is due.

- **Search.** Tavily queries cross AI terms (KI *(AI)*, künstliche Intelligenz, machine
  learning, LLM, data science) with event terms (Veranstaltung *(event)*, Vortrag
  *(talk)*, Meetup, Hackathon, Karrieremesse *(career fair)*, Konferenz, Symposium) and
  München or Munich, plus `site:tum.de` and `site:lmu.de` variants. About 40 queries a
  week, roughly 170 of the 1,000 monthly credits (R2). The list lives in `discovery.yaml`.
- **Crawl.** The TUM and LMU faculty and institute index pages, depth 2, only tum.de and
  lmu.de hosts, looking for event pages and feeds.
- **Event mentions.** Co-organisers and hosts named on event pages become candidates
  (relAI on the career fair). This is the cheapest path and probably the richest.
- **Admission.** A candidate is added only if feed detection finds at least one future,
  in-scope event (an ICS link, a Luma calendar, a Meetup group, a pretix organiser, RSS or
  JSON-LD `Event`). Otherwise it is `dormant` and rechecked monthly. There is no review
  queue (Q2).

## Failure model

Only the lines that are not obvious.

| Component | Failure | Behaviour |
|---|---|---|
| Runner | crash mid-run | every stage's committed output is durable; the next run starts from the due work. No checkpoint database. |
| Runner | two runs at once (timer and "run now") | `flock` on the store; the second exits as busy |
| Runner | the box is dead or off | healthchecks.io alerts by email after 26 h without a ping. systemd `Persistent=true` runs the missed tick on boot. |
| Fetcher | a 200 with an empty or redesigned page | the parser yields 0 where the source used to yield events: `empty_streak`; at 2 it is flagged in the digest and the UI |
| Fetcher | robots disallows | source marked `blocked_robots`, shown in the UI, not fetched |
| Extractor | a plausible but wrong date | rejected unless the date parses from a quote that appears in the page |
| Extractor | prompt injection in a page | the model has no tools and its output is data; grounding limits every field to text already on the page |
| Extractor | budget gone for days | A and B registration jobs go first; the pending count is in the digest; 3 days with pending A or B jobs raises an alert |
| Resolver | two distinct events merged | fuzzy merges happen only when neither side has a platform key; the merge basis is shown; splitting adds an alias exclusion |
| Calendar | partial reconcile | each entry is independent and idempotent; the next run converges |
| Calendar | the key leaks | it reaches only the shared calendar; revoke by unsharing |
| Notifier | crash after send, before the ledger write | one duplicate push, accepted (D13) |
| Browser unit | out of memory on the shared box | its own unit with `MemoryMax=`; a failure leaves those sources stale and flagged |
| Discovery | adds a junk organiser | its events still pass through the judge; demoting caps them at C, removing tombstones the organiser |
| Store | disk loss | registry, user status and feedback come back from `state.json` on `radar-export`; events rebuild on the next full fetch |

**Trust boundary on the host.** The radar parses untrusted HTML and model output on the
same box as another project's services. It runs as its own unprivileged user,
and its secrets are readable only by that user: `/etc/event-radar/env` (mode 600,
systemd `EnvironmentFile=`) holds the OpenRouter, Tavily, Telegram, Eventbrite and later
Groq keys and the healthchecks ping URL; the service-account key and the deploy key are
files beside it. Nothing secret is in the repo.

## Decisions

### D1. Stages around a store, no LangGraph
Every handoff is a durable row, so crash recovery, single-stage reruns and a different
cadence per stage come for free. In the apartment agent LangGraph earns its place by
checkpointing an in-memory pipeline state; here there is no such state to checkpoint.
**Rejected:** a LangGraph graph per run, the apartment pattern. It duplicates the store
as a checkpoint database, puts every stage on one schedule and adds a fast-moving
dependency. A task queue (Celery and a broker): a broker to run for a few hundred jobs a day.
**Cost:** no graph view or LangGraph tracing, and a runner that differs from the
apartment agent's.

### D2. Copy and adapt the apartment agent; no shared core (closes Q6)
The parts that would be shared already differ: this router has no paid tier and needs a
budget ledger and three failure classes (R1), the schemas have nothing in common, and this
fetcher must be honest rather than impersonating a browser.
**Rejected:** a shared core package. It couples a deployed agent's releases to a new one,
for a shared surface of about 300 lines.
**Cost:** a router fix is ported by hand. Revisit when a third agent appears.

### D3. Raw snapshots, append-only observations, derived events
Invariant 1 needs field-level provenance. The repo rule "raw first, curated second" becomes
the schema. A parser fix can be replayed over stored snapshots, and the golden replay runs
on the production code path.
**Rejected:** mutable event rows that each source overwrites (the apartment style). The
result depends on fetch order, and provenance is lost on every overwrite.
**Cost:** more tables, and snapshot storage. Retention keeps the latest snapshot per URL and
any snapshot behind a current field, and prunes the rest after 90 days.

### D4. A surrogate event key with platform aliases (resolves Q8)
Platform keys: Luma `evt-…` (a slug resolves to it after one fetch), Meetup `event_<id>`,
Eventbrite id, pretix `<org>/<event>`, Google UID, and the form URL for applications. An
organiser or aggregator observation that links a ticket URL takes that key. With no key,
the same local date and a similar normalised title merge with `merge_basis=fuzzy`, which is
Q8's `UNVERIFIED-merge`. Similar means a sequence ratio of at least 0.6, one title inside
the other, or every distinctive word stem of the shorter title (at least two) inside the
longer one; stated start times must also lie within an hour. Venues are not compared: one
hall is "Bayerische Akademie der Wissenschaften" on its own page and "Bavarian Academy of
Sciences and Humanities" on TUM's (2026-10-07). Two records with different
platform keys never merge.
**Why a surrogate:** an event first seen on an aggregator gains its platform key later. If
the platform ID were the key, the calendar entry ID would change and the entry would
duplicate.
**Rejected:** the platform ID as primary key (above); title plus date as key (German and
English title variants).
**Cost:** an alias table and merge and split handling.

### D5. Precedence: TICKET over OWN over AGG, then parser over LLM, then newest
Registration fields are never taken from an aggregator. An aggregator observation enqueues
the organiser and ticket links it carries and sets `unchecked` until they are read. This is
the AI@STRATEGY rule as code.
**Rejected:** newest wins. Aggregators refresh more often and copy less accurately.
**Cost:** a fetch fan-out per aggregator listing, bounded to future in-scope events.

### D6. The model supplies features; code assigns the tier
Features, each with a quote: event kind, AI relevance, networking signal, price class.
They come from parsers, a German and English keyword classifier, organiser defaults, or the
LLM when those are unsure. `profile.yaml` maps features to A, B or C per Q5.
Organiser default (2026-10-07, after the first live runs): unknown AI relevance counts as
relevant only for an event observed through an organiser with `ai_focus` in the seed.
Elsewhere it is C unless its kind is in `any_topic_kinds` (career fairs, hackathons).
Before this, the general TUM and BAdW calendars put about 60 non-AI events in B. Feedback
reorders events within a tier and can propose a profile edit in the UI; it never changes a
tier by itself (Q3).
Assumption written into the profile: free hackathons and programmes with an application
window (hackaTUM, TUM.ai, CDTM) are A when AI-centred, otherwise B. Alp tunes the file.
**Rejected:** the LLM assigning tiers (not replayable, no auditable reason, Q3); learned
tiers (Q3).
**Cost:** a feature vocabulary to maintain.

### D7. Grounded extraction
Each LLM field is `{value, quote}`. The verifier, which is code, accepts a field only if
the quote is in the cleaned page text and the value parses from the quote.
**Rejected:** trusting schema-valid JSON. R1's six pages rank the models; they do not bound
the error rate.
**Cost:** some true values are lost when the model paraphrases. They show as UNVERIFIED and
get a recheck.

### D8. LLM budget: the agent caps itself at 35 of the 50 shared daily requests
Order: A and B registration extraction, then feature classification, then discovery.
Structured sources never reach the LLM (R3). The other 15 stay for the nemotron MCP; the
cap is a setting. Groq joins the chain only after it passes the R1 kit (R4).
**Rejected:** no self-cap. Whichever of the agent and the MCP runs first would starve the other.
**Cost:** in a heavy discovery week, classification waits.

### D9. Calendar through a service account with access to one shared calendar
Alp creates "Event radar" in their own account, so Alp stays the data owner and can delete
it in one step (Q7), and shares it with the service account's address with "make changes
to events". The credential reaches nothing else, which makes invariant 2 structural. The
same credential reads MCML's public Google Calendar (R3).
**Rejected:**
- User OAuth. Its calendar scopes reach the main calendar, and a consent screen in
  "Testing" status issues refresh tokens that expire in 7 days, a silent weekly breakage
  (source: https://developers.google.com/identity/protocols/oauth2, checked 2026-10-07).
- The service account owning the calendar. Google advises against a service account as
  data owner; ownership cannot be transferred (source:
  https://developers.google.com/workspace/calendar/api/v3/reference/calendars/insert,
  via search snippet, checked 2026-10-07).
- An ICS feed served by the agent. Google polls it on its own schedule, and it needs a
  public URL while the box is reachable only over Tailscale.
- The claude.ai connector, which cron cannot reach (Q1).

**Cost:** a Google Cloud project and a key file on the box. Manual edits to the agent's
entries are overwritten.

### D10. Liveness checked from off the box: healthchecks.io
The free plan monitors 20 jobs (source: https://healthchecks.io/pricing/, checked 2026-10-07).
**Rejected:** a Telegram heartbeat alone. A dead box cannot report its own death, and
the box has been offline since about 2026-08-27 (Tailscale "last seen 41d ago",
checked 2026-10-07). A self-hosted watchdog needs a second always-on host.
**Cost:** one more account; the ping URL is a secret.

### D11. An honest fetcher; the headless browser as an isolated weekly unit
httpx with an honest user agent and contact address, an RFC 9309 robots matcher such as
`protego` (R3: the stdlib parser mishandles wildcards), conditional GET and a per-host rate
limit. The browser is its own systemd unit with `MemoryMax=`, runs weekly at night and
writes snapshots like the fetcher.
**Rejected:** curl-cffi TLS impersonation, the apartment pattern. It contradicts the
honest-user-agent rule, and these sites do not fingerprint clients. Playwright in the main
run: the box has 8 GB, and another project used about 4.7 GB RSS with swap thrashing
(observed 2026-07-14).
**Cost:** JS-only sources can be a week stale.

### D12. Export to a bot-owned branch, which is also the off-box backup
Daily, only when the content changed: `radar/generated/events.md`, `calendar.md` and
`state.json` (registry, user status, feedback) pushed to `radar-export` with a deploy key
for this repo only.
**Rejected:** committing to main. Bot commits would interleave with human history and race
the laptop's pushes. No export: Q1 asks for one, and a disk loss would erase the registry,
statuses and feedback. Litestream to object storage: another account and secret to back up
a few hundred kilobytes.
**Cost:** a write-capable deploy key on the box, and readers look at another branch. After
cutover, main keeps a pointer.

### D13. Scheduling with systemd timers; pushes at least once
The main run fires every 2 h from 07:00 to 23:00 Europe/Berlin, and the planner decides
what is due: ICS sources daily, HTML weekly, A and B rechecks daily, discovery weekly, and
a recheck on each known registration-open date. `Persistent=true` runs a missed tick after
downtime. The web app is a separate always-on service.
**Rejected:** cron, which has no catch-up after the box was off. A daemon loop: one more
process to keep alive, and nothing needs latency under 2 hours. At-most-once pushes: a
missed push is the expensive error (Q5).
**Cost:** a spots-left alert can be up to 2 h late (Munich NLP lost 7 spots in a few
hours on 2026-10-07). A crash in the send window gives one duplicate push.

### D14. The seed is a curated file
`seed/organisers.yaml` is built once from the four 2026-10-07 sweeps (about 240 rows),
reviewed, committed, then imported with `discovered_via=seed` and each row's source URL.
**Rejected:** parsing the research Markdown at runtime. The tables have mixed shapes, and
`research/` is raw intake by repo rule, not an input contract.

### D15. Web app: a JSON API and a zero-build page, served from the box over Tailscale (2026-10-08)
*Hosting superseded by ADR 0004 (2026-10-09): Supabase plus Cloudflare Pages, no API server.*
`event_radar.web` is FastAPI with three read routes (`/api/events`, `/api/organisers`,
`/api/status`) and one write (`POST /api/events/{key}/status`, the `user_status`
transitions). It is the apartment agent's dashboard pattern: a hand-written HTML, CSS and JS
page that polls every minute and reloads when a new run finishes. It runs as
`event-radar-web.service` on port 8010, bound to the box's Tailscale address. Two tabs, as
Alp asked: Timeline (every event by month and day, filters for tier, organiser, search, past
and dropped) and Organisations (the registry, source health, and each organiser's events).
One event opens in a side panel with registration, tier reasons, sources and status
buttons; `#event-<key>` links to it. Events store the URLs of their observations (`sources`)
so that the page can name the organisers behind each one.
**Rejected:** a static snapshot published as a claude.ai page (2026-10-07; Alp: not live, no
tabs, no status writes); React or Next.js (a Node build on the box for one user and about
200 rows); server-rendered HTMX (fine, but it departs from the sibling project's pattern,
and client-side filtering of a few hundred events is instant).
**Cost:** the page holds its own rendering code, and the fonts load from Google Fonts with a
system fallback.

## Acceptance check (Q5, made concrete)

- **Golden set** `eval/golden.yaml`: every event in the 2026-10-07 sweeps, tagged A, B or C
  by the Q5 profile, with each A event's sourced registration window.
- **Offline replay**, in CI: frozen raw snapshots of the golden pages in `eval/fixtures/`,
  with `now` pinned to 2026-10-07. Pass: at least 9 of 10 A events surface as A or B, and
  every sourced window matches. A and B count alike because both get a push and a calendar
  entry (Q7); tier accuracy is reported, not gated.
- **Live check**: `event-radar eval --live` reports which golden organisers are in the
  registry and which golden events a live run found. Informational, because past events
  drop off their pages.
- **Cutover**: the offline replay passes and 14 days of runs pass without a missed ping.
  Then `AGENTS.md` changes as Q1 says: the store owns event facts, and
  `radar/event-radar.md` and `timeline/calendar.md` on main become pointers to the export.
  Until then the manual rule stands.

## Build order

Each step ends on something checkable.

1. Golden set and fixtures. The pages change or vanish after their events, so this is first.
2. Store, fetcher, parsers (ICS, JSON-LD, Luma `__NEXT_DATA__`, pretix widget, RSS,
   Eventbrite API), resolver and judge. Check: the offline replay on structured sources.
3. Extractor, router and verifier. Check: the replay on the CMS pages.
4. Calendar reconciler, notifier, healthchecks. Check: a dry run against a test calendar.
5. Web app, designed in the UI phase with the taste skill. Built 2026-10-08 (D15).
6. Discovery, the browser unit, the exporter.

## Open questions

1. **Blocking deployment, not design (Alp): the host.** The box has been offline since
   about 2026-08-27 (Tailscale, checked 2026-10-07), so its headroom could not be measured.
   Bring it back, or run the radar on another Linux host? The design needs systemd, Python
   3.11 or later, about 1 GB of disk and outbound HTTPS. On whichever host, measure free
   memory with the other project running before the browser unit is enabled.
2. **Carried, Alp may veto: D1 drops LangGraph.** If LangGraph matters for learning or
   portfolio reasons, it can wrap the stage runner later without changing the store or
   the seams.
3. **Carried (Alp): a free Groq key** for the R4 bake-off. Until then the chain ends at
   `openrouter/free`.
4. **Verify at setup (implementation):** sharing a consumer-Gmail calendar with a service
   account; reading MCML's public calendar with the same credential; healthchecks.io
   alerts by email on the free plan; the Eventbrite API on a third-party event (R3: not
   tested).
