# ADR 0016: A daily web search into the review queue

**Status:** DECIDED, merged; approving a found event without its day and place amended by ADR 0019
**Date:** 2026-10-19
**Inputs:** Alp's idea (c) and answers of 2026-10-19: a daily web search through Tavily; a
page the radar does not know goes to the admin review queue, never straight to the board;
a hit on a single event's page on a platform becomes an event candidate, and the admin
gives its day and place when approving it. Amends ADR 0002's Discovery section, which had
search admit organisers on its own with no review queue (Q2). Builds on ADR 0003 (the
organisation intake), ADR 0005 D4 (writes through `SECURITY DEFINER` functions), ADR 0006
D3 (an intake becoming `queued` dispatches `intake.yml`) and ADR 0014 (the event intake).

## What this is for

The radar only knows the organisers it was seeded with and those people add. Once a day it
asks a search engine for Munich AI, startup and career events, and each page it does not
know, from a site it does not know, waits in the admin's review list, where one click adds
it through the intake that already exists.

## Invariants

| # | Never or always | Enforced by |
|---|---|---|
| 1 | Nothing a search finds is fetched, judged or shown on the board before an admin approves it | the search writes intake rows in `review` only; only `decide_intake` moves one to `queued`, and only `queued` dispatches the intake workflow |
| 2 | A site or page the radar knows, or one an admin rejected or removed, is never proposed again | suppression and insert run under one advisory lock (`search`), and count intakes in every state |
| 3 | The free plan is a ceiling: at most `search.daily_cap` searches a UTC day, no retry, no paid call | the `budget` ledger (provider `tavily`), taken before each request; 401, 429, 432 and 433 end the run's searching |
| 4 | No third-party text is stored beyond a hit's URL and title | the snippet is read by the gate and dropped; the log entry names the query and the title |
| 5 | The review list stays short: at most `search.daily_candidates` new rows a day | counted under the same lock, from the rows whose first log line is the search's |

## Flow

1. `event-radar search` (workflow `search.yml`, daily) takes the day's slice of
   `search.queries` from `profile.yaml`, rotating so every query runs within a few days.
2. Each query is one basic Tavily search (`max_results` 10, `exclude_domains` from
   `search.exclude_domains`).
3. Each hit passes a gate in code: an http(s) URL on a host that is not excluded; its title
   and snippet name an AI or scope word (`Judge.names_scope`); and its title, snippet or URL
   names a Munich place (`munich.cities`).
4. A hit becomes a candidate of one kind:
   - **event**, when a platform adapter recognises the URL as one event's page (a Luma,
     Meetup or Eventbrite event): the intake names that page;
   - **organisation**, otherwise: the intake names the site's homepage, or the platform
     organiser page (a Luma calendar, a Meetup group), with the hit as its events URL.
5. A candidate is dropped when the radar knows it: an organiser or an intake of any state
   with the same organiser domain, a source with the same canonical URL, an event intake of
   any state for the same page, or an event whose aliases hold the page's platform key.
6. The rest, best Tavily score first, up to the day's cap, become intake rows in `review`
   with `submitted_by` empty and one log entry: "Found by the web search for "<query>":
   <title>".
7. The admin sees them in the Organisations tab. An organisation candidate is approved or
   rejected as a suggestion is today. An event candidate's review form asks for the day and
   the place; approving it queues ADR 0014's event check with them.

## Components and seams

| Component | Change | Its own failure |
|---|---|---|
| `search/tavily.py` | new: one request, results as `(url, title, snippet, score)` | the provider is down, out of credit, or the key is wrong |
| `search/gate.py` | new, pure: hit to candidate or a drop reason | a noisy queue (tuned in `profile.yaml`) |
| `Store.intakes.propose` | new: suppression and insert in one transaction | a duplicate row (prevented by the lock and the query) |
| `jobs.search`, CLI `search [--dry-run]`, `search.yml` | new | a run that writes nothing and says so |
| `decide_intake` | gains `starts_on` and `place`, required to approve an event candidate | a wrong day or place fails ADR 0014's check, as for any user |
| migration 0005 | an event intake needs its day and place only once it leaves `review` and `rejected` | |
| `site/js/intakes.js` | the review section of an event candidate asks for the day and the place | |

| Seam | Carrier | Contract | Failure |
|---|---|---|---|
| search job > Tavily | HTTPS POST `https://api.tavily.com/search`, `Authorization: Bearer` | `query`, `search_depth` basic, `max_results`, `exclude_domains`; results `url`, `title`, `content`, `score` | 401, 432, 433: no more search this run, the run fails; 429: no more search this run; a timeout: that query is skipped |
| search job > admin | the `intake` table | a row in `review`, `submitted_by` NULL, kind, URL, events URL, a log entry naming the query | the review list is the only reader; nothing else moves the row |
| admin > intake workflow | `decide_intake`, then the existing dispatch trigger | unchanged for organisations; an event needs a future day and a place, as `suggest_event` checks them | as today |

The search job holds only `RADAR_DATABASE_URL` and `TAVILY_API_KEY`: it asks no LLM and
fetches no page. Tavily's documentation, checked 2026-10-19
(https://docs.tavily.com/documentation/api-reference/endpoint/search and
https://docs.tavily.com/documentation/api-credits): 1 credit per basic search, the free plan
1,000 credits a month.

## Decisions

| # | Decision | Rejected, and why |
|---|---|---|
| 1 | Candidates are intake rows in `review` | A `candidate` table: it would need its own API, row-level security and review list, and an approved candidate becomes an intake anyway |
| 2 | Every candidate waits for an admin (Alp, 2026-10-19) | Admission when feed detection finds a future in-scope event, as ADR 0002 planned: it fetches unreviewed pages and admits junk an admin then has to remove |
| 3 | A platform event page is an event candidate; the admin gives its day and place on approval (Alp, 2026-10-19) | Tracing it to its organiser: a fetch before review, and one-off events stay missing. Leaving such pages out: misses groups that exist only on Luma or Meetup. Reading the day from the snippet: a guessed date |
| 4 | The gate is code: scope words and a Munich place in the title, snippet or URL | An LLM reading the snippets: a second provider and daily LLM spend in this job; revisit if the queue fills with noise |
| 5 | Tavily, basic depth (Alp, 2026-10-19) | SerpApi (250 free searches a month); Brave (card requested, overage uncapped); Google's Custom Search JSON API (closed to new customers) |
| 6 | Rotating slices of a query list in `profile.yaml`, at most `daily_cap` a day | The same few queries every day: after the first days nearly every hit is suppressed |
| 7 | Its own workflow and secrets | Inside the harvest: the harvest is near its time limit, and the search key would sit beside the LLM keys |
| 8 | A known organiser's domain suppresses every hit on it | Proposing unknown pages on known domains: the intake's first check rejects a tracked domain anyway. This misses new pages on large tracked domains (a TUM chair on `tum.de`), which reach the radar through the organiser's sources or a person's suggestion |

## Check

- Tests: the gate's kinds and drop reasons; suppression for each known kind, including a
  rejected intake; the daily candidate cap and the budget ledger under two runs the same
  day; 401 and 429 end the searching; `decide_intake` refuses an event candidate without a
  future day and a place, and queues it with them; the review form in node tests and in
  Chromium at 375 and 1280 px.
- `event-radar search --dry-run` prints each hit's candidate or drop reason and writes
  nothing; it calibrates the gate once the key exists.
- Before the first run: Alp creates a Tavily key and adds the `TAVILY_API_KEY` Actions
  secret.

## How it landed (2026-10-19)

| Commit | What |
|---|---|
| db58783 | this decision |
| c06c543 | migration 0005; `decide_intake(intake_id, approve, starts_on, place)`; `event_place` checks a day and place for it and `suggest_event`; a person adding a page the search proposed takes the candidate over |
| 2b8525e | `event_radar/search/` (`gate.py`, `tavily.py`, `run`), `Intakes.propose`, `event-radar search [--dry-run]`, `profile.yaml` `search` (40 queries, 25 searches and 5 candidates a day), `search.yml` |
| 8c4617b | the page: "Found by the web search", an admin-only list apart from the five recent intakes; a found event's review form asks for its day and place |

Found while building: a platform organiser page stays the intake's URL (Meetup's
`organiser_source` is the group's iCal feed, which the intake's homepage check refuses), a
platform page that is neither an event nor an organiser page (an Eventbrite search page) is
dropped, and the form services are excluded. A search candidate an admin rejects leaves the
lists, as a candidate is the search's proposal and not a person's.

Checks: pytest (18 in `test_search.py`, 2 in `test_access.py`), 21 node tests, ruff; the
page with the local stand-in (`tools/devsite.py`) in Chromium at 1280 and 375 px, light and
dark: no horizontal overflow, no console error besides the 400 of an approval without a day,
whose message the form shows; approving with a day and a place queues the check. Not run:
a real Tavily search (no key yet), so the gate is not calibrated on real results.
