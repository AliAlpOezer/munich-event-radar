# ADR 0014: Adding a single event from its page

**Status:** PROPOSED, open questions below
**Date:** 2026-10-18
**Inputs:** Alp's request of 2026-10-18: anyone with an event's URL, giving its date and
place, can add the event without its organisation, "still a fact check: fetch the page and
look for relevant words (coding, developer, ai, ki, etc.)". Builds on ADR 0003 (the
organisation intake), ADR 0005 D4 and D8 (writes through `SECURITY DEFINER` functions, no
LLM spend before review), ADR 0006 D3 (an intake becoming `queued` dispatches `intake.yml`),
ADR 0011 and ADR 0013 (scope words, matched as words).

## What this is for

A person who knows one relevant Munich event whose organiser the radar does not track can
put it on the board with its URL, date and place. Within minutes it shows with the same
tier, registration window and ticket link as every other event, and nothing about it shows
that its own page does not state.

## What was measured

| Finding | Evidence |
|---|---|
| No tracked source lacks an organiser | Hosted `source`, read-only, 2026-10-18: 0 of 232 with `organiser_id` null, so an organiser-less source would be new and only an intake's |
| Organiser-less sources are fetched | `Sources.due` joins `organiser` with a left join and keeps `COALESCE(o.status, 'active') <> 'removed'` |
| An event page's links become sources | `Harvester.harvest` adds every `follow` link of a page as a source of the same organiser; for an event page these are its ticket-platform links (`parse`) |
| The judge rebuilds every event from all observations on every run | `Harvester.judge_events`; so a verdict made only by the intake would not survive the next run |
| A date can be found in page text in German and English forms | `extract/verify.py` `_date_in_quote` already checks the LLM's date quotes this way |
| Alp's words "coding" and "developer" are not scope words | `profile.yaml` `scope` holds startup and career words only (ADR 0011: AI, startups and careers, "not every tech event") |

## Invariants

1. Nothing about a submitted event reaches the board on the person's word. Every field
   shown is read from its page, by a parser or by the LLM with a quote found on the page;
   the date and place the person gives only test the page.
2. One rule for every event: the judge and `profile.yaml` decide a submitted event like
   any other, so the next run never deletes what the intake admitted, and the intake admits
   nothing the judge would drop.
3. No LLM call is spent on a submission before its page has passed a check that needs
   none; after it, at most one extraction call, counted against the same daily budget.
4. One URL in, one source: the page is fetched only through the address guard (ADR 0003
   D4) and robots.txt, and none of its links becomes a source.
5. An admin can take a submitted event off the board, and it stays off: its URL is refused
   afterwards.
6. Submitting a URL the radar already tracks creates no second source, fetch or event.

## Flow

An event intake is an `intake` row of kind `event`, worked by the same drain
(`event-radar intakes`, ADR 0006 D3) as an organisation intake. It skips `profiling`,
`review` and `finding`.

| State | Work | Ends in |
|---|---|---|
| `queued` | waiting for the drain | `checking` |
| `checking` | the check below, on one fetch of the page; nothing is stored but the snapshot | `harvesting`, `done` (already tracked), `rejected` or `failed` |
| `harvesting` | insert the source (`event_page`, no organiser, `added_via = intake:<id>`), ingest the snapshot the check read, extract it now if parsing fell short, then judge under the judge lock | `done` or `rejected` |
| `done` | the result holds the event key and tier, or that the page waits for the LLM's next daily run | |
| `rejected` | the result says which check refused it, in plain words | |
| `removed` | an admin took the event off the board (D7) | |

### The check

| # | Check | Refused with |
|---|---|---|
| 1 | The URL is http(s), every address it resolves to is public, robots.txt allows it | `failed`: the address cannot be fetched |
| 2 | The URL, canonical (`platforms.canonical`), is not already a source | `done`: already on the radar, with its event if it has one |
| 3 | The page answers 2xx with HTML | `failed`: the page did not load |
| 4 | The page holds one event: one structured event (a platform adapter or JSON-LD), or none, when its text is the evidence | `rejected`: the page lists several events; give the event's own page |
| 5 | Date: the structured event's local dates (start to end) contain the given day; without one, the page text names that day and its year (`_date_in_quote`) | `rejected`: the page says 2026-11-06, or does not name 5 November 2026 |
| 6 | Place: the given place's words appear in the structured venue, address or city, or in the page text; the Munich names in `profile.yaml` `munich.cities` count as one | `rejected`: the page does not name the place |
| 7 | Relevance: a structured event is kept by the judge (not past, online only, outside Munich, off topic or for an audience other than adults); without one, the page text holds an AI or scope word, matched as words (ADR 0013) | `rejected`, with the judge's reason |

After an extraction, checks 5 to 7 run again on the LLM's fields. A refusal then deletes
the source with the removal of D7, so a page the radar would drop is not fetched again
every 72 hours.

## Decisions

| # | Decision | Rejected, and why |
|---|---|---|
| 1 | An event intake is an `intake` row with `kind = 'event'` and the columns `starts_on` (date) and `place` (text), plus the state `removed`; same drain, dispatch trigger, RLS policy and list on the page | Its own table: a second state machine, dispatch trigger, policy and page list for rows that differ by three columns |
| 2 | The person's date and place test the page and are never stored as event fields | Storing them as fields: a fact on the board that no page states. An observation written from them once the page text confirms them: a third way facts enter, beside the parsers and the LLM, whose fields never refresh, so the board would keep the old date after the page moves it |
| 3 | Any logged-in account submits, and an event that passes the check is on the board without review; the guards are the check, the caps of D8 and the removal of D7 | An admin's review before any fetch, as for organisations (ADR 0005 D8): D8's reason was strangers spending the daily LLM calls, and this check spends none; Alp: "anyone ... can add it" |
| 4 | One URL, one source, without an organiser: an organiser-less source follows no links | Following its ticket links: a stranger's page can link dozens of events, each a new source nobody vouched for; the person can give the ticket page itself. Joining a tracked organiser by domain: shared hosts serve many organisers (tum.de, luma.com), and the wrong one would lend the event its AI focus |
| 5 | Relevance is the judge's verdict on the structured or extracted fields, with a page-text word gate before the LLM | A looser rule for submitted events: the next run deletes them (invariant 2). The LLM's relevance question (ADR 0010): a call per submission before anything else has passed |
| 6 | A page that needs the LLM is extracted at once, one call within the daily budget, without the relevance question; when the budget is spent it waits in the queue like any page | Waiting for the daily harvest: up to a day before the person sees a result. The organisation intake's extraction of every page found: that is for an admin-approved organiser, not a stranger's URL |
| 7 | Removal is an admin's `api.remove_event(intake_id)`: in one transaction it deletes the source, its LLM jobs and observations, and the event with its aliases, push ledger rows and marks, then sets the intake `removed`; `suggest_event` refuses that URL for anyone but an admin | A hidden flag on the event: `judge_events` rewrites every event each run. A one-off cleanup script per case, as for the Luma category pages (ADR 0008, 2026-10-17): an admin needs the action from the page |
| 8 | At most 5 open (queued or working) and 10 a day per account that is not an admin; one intake per canonical URL | Organisations' cap only (5 in review): event intakes do not wait in review, so a cap on review states would not hold |

## Components and seams

| Component | Changes when | Carrier and contract | Failure |
|---|---|---|---|
| `api.suggest_event(url, starts_on, place)` | the caps or input rules change | RPC; returns the intake id; refuses logged-out callers, malformed input, a removed URL and a full cap with a message the page shows | Nothing is written on a refusal |
| `intake` row, kind `event` | the flow changes | the store is the interface: the drain claims `queued` rows; the trigger dispatches `intake.yml` after commit | A lost dispatch waits for the hourly fallback |
| Event check (`intake/event.py`) | the check rules change | reads the row, fetches once, returns the parse and a verdict; writes only the intake's state and log | A crash requeues the intake (`requeue_interrupted`); every step is safe to repeat (`source.url` unique, observations `ON CONFLICT DO NOTHING`) |
| Harvester | how sources are read | an organiser-less source adds no follow sources | unchanged |
| Judge | never for this ADR | unchanged | unchanged |
| `api.remove_event(intake_id)` | what removal must reach | admin-only RPC, one transaction | Rolls back whole, so an event is never half removed |
| Page: "Add an event" | the form or card changes | URL, date, place; then the intake card (state, log, the event's link) | Shows the RPC's message |

Autonomy: the check and the publish are autonomous, since a wrong admission is a bad row an
admin removes; removal is human-triggered and bound to one intake id.

## Cost

- A stranger's event can be on the public board within minutes of submitting it. A page
  that passes checks 4 to 7 can still be spam; removal comes after the fact.
- An event whose page has no structured data and states its date only in an image fails
  check 5, though the event is real.
- Submitted events have no organiser, so they never count as AI-focused or startup and
  land in C when their page names no AI word; the board's organiser filter hides them.
- No source is retired automatically (ADR 0008, the finding on `luma.com/climate`), so a
  submitted past event's page is fetched every 72 hours like every other.

## Open questions for Alp

1. "coding" and "developer": as scope words they widen what the whole board keeps, not
   only submitted events (invariant 2), and ADR 0011 chose AI, startups and careers, "not
   every tech event". Proposed: measure first what a `developer` group (coding, developer,
   programming, software engineering) would keep over the hosted observations
   (`board_dump.py` with the changed profile), then decide.
2. Publishing without review (D3): confirm, given that what a stranger submits shows on the
   public board until an admin removes it.
3. The caps of D8 (5 open, 10 a day): proposed numbers.

## Plan

Each step its own commit, tests first.

| # | Step | Check |
|---|---|---|
| 1 | Migration 0004: `intake.kind`, `starts_on`, `place`, state `removed`; `api.suggest_event`, `api.remove_event`; `intakes` view columns | `test_access`: logged out refused; caps; a removed URL refused; `remove_event` admin only and removes everything in one transaction |
| 2 | An organiser-less source follows no links | a test with the Luma fixture's "Part two" link |
| 3 | `intake/event.py`, checks 1 to 7, and the drain's dispatch by kind | `test_intake` with hand-written fixtures: a structured page passes; a wrong date is refused with the page's date; a listing is refused; a text-only page passes the gate; past, online, outside Munich, off topic refused; a tracked URL ends `done` with its event |
| 4 | Harvesting: source, ingest, one extraction without the relevance question, judge, the re-check after extraction | a fake router; the budget spent leaves the page queued and the intake `done` with that said |
| 5 | Page: "Add an event" in the board toolbar for logged-in users, the intake card, the admin's "Remove" | node tests for any pure function; a Playwright walk at 1280 and 375 px, light and dark |
| 6 | How it landed: pytest, node tests, ruff, replay without the LLM, a local run on three real pages (a Luma event, a TUM page, a page without structured data) | |

Before merging: dispatch `intake.yml` on the branch so migration 0004 applies before the
page that calls `suggest_event` deploys (as for stars, ADR 0007).
