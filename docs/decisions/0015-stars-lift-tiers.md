# ADR 0015: Stars lift an event's tier

**Status:** DECIDED, built on branch `star-popularity`, not merged
**Date:** 2026-10-19
**Inputs:** Alp, 2026-10-18: "if lots of people (for example 10% for B Tier and 20% for A
Tier), are adding events to their favorites, so that shows us that those events are
popular, so we can shift them to a better tier". Asked on 2026-10-19 whose stars make the
share and how few make it meaningless, Alp chose absolute counts, then, asked to reconcile
that with the shares, "absolute only": 5 stars for B, 10 for A. Builds on ADR 0007 (stars,
private, no counts) and the judge's order (ADRs 0011 and 0012).

## What this is for

An event that many people keep rises on the board, so the judge's keyword rules are not
the last word on events people clearly want. Nobody learns who starred what.

## What was checked

| Finding | Evidence |
|---|---|
| The judge rebuilds every stored tier on each run | `Harvester.judge_events`, called by the daily `harvest` and the weekly `browse` |
| A merge keeps one star per user | `store/events.py` `_merge` moves a star to the survivor unless the user starred it already |
| The tier drives more than the page | A and B sources are fetched every 24 h (`HOT_HOURS`); the run summary counts tiers |
| A reason without `=` shows as written | `site/js/facts.js` `humanReason`, as "paid, no free route" does |
| Every tier shows by default | `site/js/state.js` `DEFAULT_FILTERS.tiers` |
| How many accounts and stars the hosted radar has | Not measured: auto mode refused the read-only count on 2026-10-19 |

## Invariants

1. Stars only raise a tier. They never lower one and never put a dropped event (past,
   online only, outside Munich) on the board.
2. No star count is public. The public sees only that an event reached 5 or 10 stars.
3. `event.tier` has one writer, the judge step, and one rule, in Python and `profile.yaml`.
4. Without stars every verdict is what it was, so the replay and the golden eval do not move.

## Decisions

| # | Decision | Rejected, and why |
|---|---|---|
| 1 | Absolute counts: an event with 5 or more stars is at least B, with 10 or more A. The thresholds are a floor, so a C event with 10 stars is A | A share of accounts (Alp's first example): with a handful of accounts one star is a large share, and a growing user base would push events down without anyone unstarring. The larger of a count and a share: Alp chose "absolute only". One step per threshold (a C event with 10 stars only reaches B): the same ten people keeping it would count for less because the keyword rules missed it |
| 2 | Stars lift only a tier the judge gave. A dropped event stays dropped; off-topic and children's events are not stored at all, so their stars go by cascade (ADRs 0011, 0012) | Lifting online-only or outside-Munich events: a star says someone wants to keep the event, not that it takes place in Munich in person |
| 3 | The judge applies the rule (`Judge.judge(..., stars=n)`), so the stored tier, the 24 h cadence of A and B sources and the run summary all see it. The harvester reads the stars before judging | Lifting at read time in `api.events`: the stored and the shown tier would differ, the cadence would miss lifted events, and an invoker view cannot count other users' stars without a definer helper. A trigger on `star` that rewrites `event.tier`: a second writer of the tier, with the rule in SQL beside the judge's, and the next judge run would overwrite it |
| 4 | An event's stars are the accounts that starred any stored event its observations' aliases name: the count `Events.save` leaves after merging them. Every account counts the same | Counting only the stored event with the oldest alias: a merge on this run would add stars the verdict did not see until the next run |
| 5 | The reason says "starred by 5 or more" or "starred by 10 or more", never the count | The exact count: ADR 0007 D2 kept counts private because a count of 1 says who; a threshold of 5 says no one |
| 6 | The thresholds live in `profile.yaml` (`stars`) with the judge's other rules | Constants in code: every other tier rule is tuned in the profile |
| 7 | A lift shows with the next judge run: the daily harvest (cron 04:17 UTC, often hours late) or the weekly browse | Judging on every star: the judge rebuilds all events from all observations; one job per click is a lot of runs for a tier that can wait a day |
| 8 | Accepted: five accounts can lift any C event to B, and an account costs a Google or GitHub login. A tier only orders events already on the board; an admin can read the `star` rows | Weighting accounts by age or activity: there are too few accounts to tune it, and the result would be harder to explain than the rule |

## Privacy

Stars were used only to show a person their own events. Counting them per event is a new
use, so the privacy notice says so, with its legal basis. Alp reviews that text.

## Plan

| Step | What | Check |
|---|---|---|
| 1 | `profile.yaml` `stars`, `Profile.star_tiers`, `Judge.judge(stars=)` | Unit tests: 5 lifts C to B, 10 lifts C and B to A, A stays A, a drop stays a drop |
| 2 | `Events.starrers()`, stars per group in `judge_events` | A store test: stars on two events that merge count each account once |
| 3 | Privacy notice | Read by Alp |
| 4 | How it landed | Full suite, node tests, ruff, replay without the LLM identical to `main` |

## How it landed

| Commit | What |
|---|---|
| db68953 | This record |
| 8848566 | `profile.yaml` `stars` (A 10, B 5), `Profile.star_tiers`, `Judge.judge(stars=)`: the rules give a verdict, then stars lift a tier, never a drop |
| 6c2da63 | `Events.star_counts` (one query for all groups: distinct accounts over the stored events each group's aliases name), passed to the judge in `judge_events`; the `users` test fixture moved to `conftest.py` |
| 806fe0d | Privacy notice: stars are counted per event, legal basis Art. 6(1)(f) GDPR |

Checks (2026-10-19): 151 pytest, 19 node tests, ruff check and format clean. The new tests
fail without the change: 5 stars lift C to B and 10 lift C and B to A, a dropped event
stays dropped, stars on two events that merge count each account once (4, not 7), and a
harvested C event starred 5 times is B after the next judging, with its source fetched
every 24 h. Replay without the LLM byte-identical to `main` ba7e3ea (A recall 0.636). The
privacy paragraph checked in Chromium at 375 and 1280 px, light and dark: no horizontal
scroll. Not checked: how many hosted events have stars (the read-only count was refused
by auto mode), so whether any event moves on the first run is unknown; with few accounts,
none is expected.

No migration and no change to the API, so nothing needs to run before merging. The rule
applies from the first harvest or browse job after the merge; the privacy paragraph goes
live with the Pages build of the merge commit.
