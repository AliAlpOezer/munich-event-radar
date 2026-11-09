# ADR 0020: A listing's URL names none of its events, and a stored event goes on as one

**Status:** DECIDED, built on branch `event-splits`
**Date:** 2026-10-22
**Inputs:** Alp, 2026-10-22, on https://www.tum.de/en/news-and-events/events/event-overview:
"we might not taking all of the class="news-calendar__item-link" items ... (which is
actually being the MOST important organization.) Make sure that our scraper is working
properly for this url". The Munich Career Fair AI & Data Science, an A event, was missing
from the board. Builds the split handling ADR 0002 D4 names as its cost; amends ADR 0015
D4 (which stored events' stars an event has).

## What this is for

An event on the board is one event. Two events read from one listing page stay two events.
A stored event, with the stars and statuses people gave it, goes on as one event, run after
run, also when the pages that named it come to describe several events.

## What was measured

All hosted reads were read-only, on 2026-10-22, with probes kept in the session scratchpads
and the record in the records repository's session log.

| Finding | Evidence |
|---|---|
| Link discovery and parsing of the TUM overview are complete: 75 cards, 75 detail pages with JSON-LD | live page and `parse`, session log 2026-10-22 |
| An LLM reading a listing gives each event the listing's own URL as `event_url`, and `_with_keys` defaults it to the page too. `resolve.aliases` made that one `url:` alias, and union-find joined every event of the listing | `extract/__init__.py`, `resolve.py` before this ADR |
| The fuzzy pass then chained listings: one group held MDSI's listing with 11 events, TUM's `terminuebersicht`, both pages of Shafi Goldwasser's lecture and more. 13 of 256 hosted groups joined unrelated titles, each shown as one event | hosted `observation`, `resolve` on main |
| Aliases were never released. Stored event `8aa21ce42e49` held the Career Fair's TUM, MCML, MDSI and pretix aliases, a BAdW lecture and two archive listings. Every harvest, several groups named it, and `_plan` wrote the row once per group, the last one winning: the board showed a past MDSI pitch talk under the key that held the fair and a mark on it. Two more stored events, `ab6b6268bc0a` and `9c4832f9e32b`, were split the same way | hosted `event`, `event_alias`, `user_state` |
| Three rules for when a page's URL names its event, over today's observations: per read (the URL names an event only when its read finds one) gives 300 groups and 8 mixed-title groups; per page (no current read of the page by any method finds several) gives the same 300 and 8, and also withholds the URL from 2 single-event reads of real listings (one model's read of `it-cs.io` and of MDSI's listing); per page over every read ever stored gives the same today | `rules_probe.py`, session 2026-10-22 |
| With the listing rule, 4 stored events lose the only alias that named them, their listing's URL: "ITCS München" (tier A, one mark), "DLD Munich 27", "Industrial Edge Workshops for Startups and SMEs" and an online-only talk. Each has an event with its folded title among the events read from that page, and none of them has one on another day | same probe |
| Two reads of UnternehmerTUM's listing gave "Industrial Edge Workshops" once with its day and once without. The fuzzy pass never joins an event without a day, so the event would show twice | `twins_probe.py` |
| With this ADR: 299 groups. 149 of the 152 stored events go on, 4 of them through their page. The 3 that do not are past online-only meetups that main does not carry on either. No event carries on several stored ones. The Career Fair names 7 of `8aa21ce42e49`'s aliases and keeps it with its mark | `claim_probe.py` |
| The golden replay without an LLM is identical to main's (A recall 0.636, the no-LLM baseline of every earlier replay). The replay with the LLM needs the extraction cache in the hosted store and was not run | `event-radar replay` against a local database |

## Invariants

1. The URL of a listing names none of its events. The URL of a page with one event names it.
2. Each stored event goes to at most one event per save. No row is written twice in one save.
3. An alias names the event that was last written under it, or no event once that event
   leaves the board.
4. A split moves no star or status. The stored event, with everything on it, goes to one
   event, and the others start anew.
5. The stars and the last judgment the judge reads are the ones `Events.save` then leaves:
   both come from one claim (`_claim`).

## Decisions

| # | Decision | Rejected, and why |
|---|---|---|
| 1 | A page is a listing when a current read of it, by any method, finds several events. The page's own URL names none of the events read on a listing; they are known by their page, title and day (`obs:` aliases). On one page, an event read without its day is the one event there with its title and a day, when there is exactly one (`_join_undated`) | Per read: an incomplete read of a listing keeps the listing's URL and joins by it. Over every read ever stored: a page misread once loses its URL forever. Fixing extraction only (no `event_url` default for listing items): any model can still return the listing's URL, and the stored events stay joined. ADR 0002's alias exclusion list: a hand-kept list of listings that is never complete. Joining undated events across pages: one generic title can belong to two organisers |
| 2 | `_claim`: a stored event that several events name goes to the one naming most of its aliases, then the one on its day, then the one sharing most of its sources, then the first. An event keeps the oldest key it wins and merges in the others | Last writer wins: the bug above. Letting an event on the board win over one leaving it: whether a stored event, stars and all, is deleted would follow the verdict instead of the pages |
| 3 | A stored event that no event names but that is known by the URL of a page an event was read from goes to the event from that page with its folded title, on the same day or with the day unknown on one side. Any alias claim beats it. The listing alias stays on the stored event, and from the next save on, the event's own `obs:` alias names it | Deleting stored events known only by listing URLs: loses statuses (ITCS München has one). A one-off data migration: migrations are SQL, `obs:` aliases need Python's folding, and a page can become a listing again later. The title alone, without the day: a series' next date would take over the last one's stars and statuses |
| 4 | Every alias of a written event is upserted to it (`ON CONFLICT (alias) DO UPDATE`), only where its owner differs. An event that leaves the board deletes the stored events it won and the stored aliases it names | `ON CONFLICT DO NOTHING`, as before: an alias stays with the event it first named, so every later run splits that event again |
| 5 | `Events.continued(groups)` returns per event the keys it carries on, its stars and its last judgment, and replaces `star_counts` and `judgment_keys`. Amends ADR 0015 D4: the stars are those of the stored events the event wins, not of every stored event its aliases name | Keeping the two queries: they counted stars of stored events that another event takes, so the judge saw stars the saved event does not have |
| 6 | `save` counts `new`, `updated`, `split` (an event that names stored events others carry on), `merged` and `deleted` | No `split` count: a split would read as `new` in the run summary |

## How it landed

`resolve.aliases(..., listing)`, `resolve._join_undated`, `resolve.title_and_day`;
`store/events.py` `_named` (one lookup by aliases and page URLs), `_claim`, `_plan`,
`Events.continued`; `harvester.judge_events` reads `continued`. Tests:
`test_core` (listing items stay apart, a single read of a listing lends no URL, an undated
read joins the one dated event, a series stays apart), `test_access` (a split keeps the star
with one event, in both orders; a leaving event takes only its part; an event known by a
page that becomes a listing goes on by title and day), `test_jobs` (stars and judgment of
the stored events an event carries on).
