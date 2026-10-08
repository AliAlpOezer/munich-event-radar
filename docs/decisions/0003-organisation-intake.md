# ADR 0003: Adding an organisation from the web app

**Status:** DECIDED, being built
**Date:** 2026-10-08
**Inputs:** Alp's request of 2026-10-08 (CDTM as the example of a missing organiser), and
ADR 0002 (the store, the registry and its field ownership, the admission rule in
"Discovery", D11 on the browser).

## What this is for

A person who knows an organiser the radar misses can add it from the Organisations tab
with only a URL. Within minutes the organiser is checked, its event pages are found, and
its events are on the timeline with the same tier, registration window and ticket link as
every other event. Nothing is added that the system did not read.

## Flow

An *intake* is a row in the store with a state machine, worked by one background thread
in the web process, so a closed browser tab does not lose it and the page can show
progress.

| State | Work | Ends in |
|---|---|---|
| `queued` | waiting for the worker | `checking` |
| `checking` | step 1: the URL is http(s), every address it resolves to is public, the homepage answers 2xx with HTML, robots.txt allows it, and the domain is neither tracked nor removed | `profiling` or `failed` |
| `profiling` | step 2: the LLM reads the homepage (and its about or imprint page, if linked) and returns a profile: name, kind, summary, Munich-based, runs events, AI focus, a legitimacy verdict with reasons, and the event links it saw | `finding`, `review` or `rejected` |
| `review` | the LLM was unsure, or there is no LLM key: Alp approves or rejects in the UI | `finding` or `rejected` |
| `finding` | step 3: event sources, below | `harvesting` |
| `harvesting` | step 4: the organiser and its sources are inserted (`discovered_via=manual`), fetched now, their event links followed once, LLM extraction run for pages that need it, then events are resolved and judged | `done` |
| `done` | step 5: `active` if at least one event is in the future and not dropped, otherwise `dormant` | |

`rejected` keeps an "add anyway" action: the LLM informs, a person decides.

### Step 3: finding event sources

In order, de-duplicated, at most six candidates fetched:

1. The events URL the person gave, if any.
2. Feeds the homepage declares (`<link rel="alternate">` for iCalendar, RSS, Atom), a Luma
   calendar in its `__NEXT_DATA__`, or `Event` JSON-LD on the homepage itself.
3. **Link scan.** Every anchor whose text or path carries an event signal word
   (`profile.yaml` `intake.link_signals`: events, veranstaltungen, upcoming, termine,
   kalender, ...), and every link to a ticket-platform organiser page (a Luma calendar or
   user, a Meetup group, a pretix organiser). Meetup groups become their iCal feed.
4. The links the LLM named in step 2, only if they occur on the page (grounding).
5. `<origin>/events` when nothing else was found.

A candidate is kept as a source if it yields events, event links or a feed. A page whose
plain HTML is a JavaScript shell (almost no text and links) is rendered with headless
Chromium; such a source is stored with `browser=1` and refreshed by `event-radar browse`.

## Decisions

### D1. Legitimacy is the LLM's judgement, not a rule list
Step 1 checks only mechanics (the URL works and is safe to fetch). Whether the
organisation is real, in Munich, and runs events is asked of the LLM with the page text
as evidence, and its reasons are shown. **Rejected:** hand-written criteria (Impressum
present, Munich address, a search hit). Alp: they encode guesses about what a legitimate
organiser looks like and fail on student clubs with thin sites.
**Cost:** a page can claim anything; the LLM only sees the organiser's own site. The
"review" state and the reasons on screen are the guard.

### D2. A worker thread in the web process, state in the store
**Rejected:** doing the work inside the POST (a minute or more with polite fetch pacing,
lost on a closed tab); a separate queue service or systemd path unit (a second process to
deploy for a rare action). Interrupted intakes (restart) are re-queued at startup from
their last state.
The final resolve-and-judge pass takes the runner's lock, waiting for it, so an intake
and a scheduled run never write events at the same time.

### D3. Rendering through Playwright, only where plain HTML is a shell
CDTM's `/events` is a client-side redirect to a Luma user page that lists events only
after JavaScript runs (checked 2026-10-08). Playwright returns the final URL, which the
link resolution needs; `chrome-headless-shell --dump-dom` does not.
**Rejected:** rendering every candidate (memory on the shared box, ADR 0002 D11).
This is the first piece of ADR 0002's browser unit: `event-radar browse` renders due
`browser=1` sources, for a weekly systemd timer.

### D4. Server-side request forgery guard at step 1
Every address a submitted host resolves to must be global unicast: no loopback, private,
link-local, carrier-grade NAT (Tailscale's 100.64.0.0/10) or reserved range. Needed now
because the box sits on the tailnet, and before any public hosting.

## Field ownership (amends ADR 0002)

| Field | Writer |
|---|---|
| intake rows | web app (create, approve, reject), intake worker (progress) |
| organiser and source rows with `discovered_via=manual` / `added_via=intake` | intake worker, insert only |
| `organiser.kind`, `ai_focus` at insert | the LLM profile, else unknown and false |

ADR 0002 "Discovery" says there is no review queue. That stays true for autonomous
discovery; a manual intake has a review state because a person is already there.

## Acceptance check

- Unit tests: the link scan finds `/events` and the Luma link on CDTM's homepage (frozen
  fixture), the guard refuses private and tailnet addresses, the API walks the states.
- Live: submitting `https://www.cdtm.com` locally ends `done` with a Luma source.
  CDTM had no future events on 2026-10-08, so the expected result is `dormant` with its
  past events judged and dropped as past.
