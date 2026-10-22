# ADR 0014: Adding a single event from its page

**Status:** DECIDED, merged; checks 5 and 6, the form and `api.suggest_event` amended by ADR 0019
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
| A `developer` scope group (`" coding "`, developer, programming, software engineer) moves no verdict on today's board | Hosted observations, read-only, 2026-10-18: `board_dump.py` on `main` 68231c2 and on the same tree with the group added, `board_compare.py`: 0 of 235 groups move (A 16, B 14, C 10, off topic 87, past 86, online only 12, audience 8, outside Munich 2 on both) |

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
| 9 | `profile.yaml` `scope` gains a `developer` group: `" coding "` (a word, so not "encoding"), developer, programming, software engineer; for every event, as Alp named "coding, developer" as relevant words | Only for submitted events: invariant 2. Leaving the scope as ADR 0011 set it: a submitted coding event would be refused though Alp counts it relevant; measured, the group changes nothing stored today |

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

## Alp's answers (2026-10-18)

Alp, to the three questions below: "i liked every suggestion. you can continue".

| Question | Answer |
|---|---|
| "coding" and "developer" as scope words widen what the whole board keeps, not only submitted events (invariant 2); ADR 0011 chose AI, startups and careers, "not every tech event" | Measure first what a `developer` group (coding, developer, programming, software engineering) would keep over the hosted observations (`board_dump.py` with the changed profile), then decide |
| Publishing without review (D3), though what a stranger submits shows on the public board until an admin removes it | Yes |
| The caps of D8 | 5 open, 10 a day |

## Plan

Each step its own commit, tests first.

| # | Step | Check |
|---|---|---|
| 1 | Migration 0004: `intake.kind`, `starts_on`, `place`, state `removed`; `api.suggest_event`, `api.remove_event`; `intakes` view columns | `test_access`: logged out refused; caps; a removed URL refused; `remove_event` admin only and removes everything in one transaction |
| 2 | An organiser-less source follows no links; then, its own commit, the `developer` scope group (D9) | a test with the Luma fixture's "Part two" link; a judge test that a coding meetup is in scope and "encoding" is not |
| 3 | `intake/event.py`, checks 1 to 7, and the drain's dispatch by kind | `test_intake` with hand-written fixtures: a structured page passes; a wrong date is refused with the page's date; a listing is refused; a text-only page passes the gate; past, online, outside Munich, off topic refused; a tracked URL ends `done` with its event |
| 4 | Harvesting: source, ingest, one extraction without the relevance question, judge, the re-check after extraction | a fake router; the budget spent leaves the page queued and the intake `done` with that said |
| 5 | Page: "Add an event" in the board toolbar for logged-in users, the intake card, the admin's "Remove" | node tests for any pure function; a Playwright walk at 1280 and 375 px, light and dark |
| 6 | How it landed: pytest, node tests, ruff, replay without the LLM, a local run on three real pages (a Luma event, a TUM page, a page without structured data) | |

Before merging: dispatch `intake.yml` on the branch so migration 0004 applies before the
page that calls `suggest_event` deploys (as for stars, ADR 0007).

## How it landed (2026-10-18)

| Commit | Step |
|---|---|
| 6195e8f | 1: migration 0004 (`intake.kind`, `starts_on`, `place`, state `removed`, `canonical_url`, `drop_intake_page`), `api.suggest_event`, `api.remove_event`, the `intakes` view's columns |
| 450c117 | 2: an organiser-less source follows no links |
| 336ba6c | 2: the `developer` scope group (D9) |
| ac014ef | found while building: `Pages.prune` keeps the latest body only of pages that are sources |
| 8fdbb35 | 3: `intake/event.py`, checks 1 to 7, the drain's dispatch by kind, harvesting without the LLM |
| 4fc5ef7 | 4: the LLM at once without the relevance question, the check after it, one event per organiser-less page |
| afe61fc | 5: the page, and the privacy notice's lines on added events |
| 3afffe5 | 6: a page that shows no text without JavaScript is refused with its own message |

Decided while building, within the decisions above:

| # | What | Why |
|---|---|---|
| 1 | Check 2 also ends `done` when the board knows the page's event by its aliases (platform key or event URL), and it matches sources by platform key too, so `lu.ma/x` is `luma.com/x` | Invariant 6: no second source or event for an event the board shows already |
| 2 | One intake per page is a partial unique index on `canonical_url(url)`, the SQL twin of `platforms.canonical`, which `tests/test_access.py` holds to the same answers. A page being checked or added is refused; one rejected or failed can be given again; a removed one only by an admin. A day before today (Munich) is refused | D8's "one intake per canonical URL"; a person whose day or place was wrong can correct it |
| 3 | Check 6 passes on one word of the given place, with every Munich name in `profile.yaml` as one word and filler words and numbers left out | The day already ties the page to the event, and the judge checks that it is in Munich; all words would refuse "TUM Garching" for a page that says "Campus Garching" |
| 4 | Check 7: a structured event that is off topic by its parsed fields alone passes on a word of the page text when the page goes to the LLM; the check after the extraction then decides | The LLM states an event's relevance (ADR 0010), and the JSON-LD parser reads no description |
| 5 | After an extraction, checks 4 to 7 run on the page's own observations, resolved; anything but one event refuses it as a listing. In every run, an organiser-less page the LLM reads as several events keeps none (`llm_several`) | When the budget is spent at intake time the page waits for the daily run, which would otherwise put every event of a listing on the board |
| 6 | Removal deletes the events that page alone holds and the page's fetches, so its body prunes; an event another source also holds stays, and the next judge rewrites it. It holds the judge's lock: `remove_event` refuses while a judge runs ("try again in a minute"), a refusal in the drain waits | `judge_events` writes back what it read before, so a removal during a judge would undo itself |
| 7 | `remove_event` works on intakes `done`, `rejected` or `failed`; it is refused while one is worked and does nothing on one removed already | Removing a refused page keeps it from being given again; the drain must not insert a source after its removal |
| 8 | `Pages.prune` keeps the latest body of sources only, not of every URL | Each refused page of a stranger (up to 5 MB, 10 a day per account) was otherwise kept for good. The first harvest after the merge drops the bodies of URLs that are no source, such as the organisation intake's probes; how many is not measured |

| Check | Result |
|---|---|
| New tests, before the code | Each step's tests failed before its code; checks 5 to 7 were mutated one at a time (no place, day, year, LLM-word, known-event or tracked check), and each mutation fails a test |
| pytest, node tests, ruff | 147 passed, 19 passed, clean |
| Replay without the LLM, against `main` | identical output, A recall 0.636 |
| Playwright walk on `tools/devsite.py` (local database `radar_walk`; the drain's states set through the store, since its address guard refuses local pages) | 84 of 84 checks at 1280 and 375 px, light and dark: the button only for logged-in users, the form and its errors, an intake from queued to "On the board" with the link to its event, "Events you added", a refusal with "Change and try again" filling the form, a page added already refused, the admin's two-step removal taking the event off the board. The only console errors are the 409s of that deliberate duplicate |

The real drain on real pages, against `radar_walk` (checked 2026-10-18; 3 calls to an
OpenRouter free model):

| Page | Day given | Read by | Result |
|---|---|---|---|
| https://luma.com/1mgtk6yk (START Munich, "YC Stories - From SF To Munich") | 2026-10-19 | `luma@3` | added, C: AI relevance unknown, no organiser to lend a focus |
| https://www.tum.de/en/news-and-events/events/details/munich-career-fair-ai-and-data-science | 2026-10-29 | `jsonld@2` without registration fields, then the LLM at once | added, A: career fair, AI core |
| https://badw.de/en/events/view/2nd-bavarian-conference-on-ai-in-medicine-2026 | 2026-11-30 | `jsonld@2` (BAdW's event pages carry JSON-LD now), then the LLM | added, A: conference, AI core |
| https://widsmunich.de/ | 2026-12-11 | no structured data; the LLM at once | added, A: conference, AI core |
| https://aistrategyconference.com/ | 2026-11-20, a test value, not its day (UNVERIFIED) | nothing: the page renders only with JavaScript | rejected; then "does not name 20 November 2026", since 3afffe5 "shows no text without JavaScript" |

Seen, not changed: `parse/jsonld.py` reads no `description`, so a structured event whose
title names no AI or scope word is off topic on the board even when its page is about AI,
for every source, not only an added one; and an event page that renders only with
JavaScript cannot be added (the Cost section names pages that state their date only in an
image).
