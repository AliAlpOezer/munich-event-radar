# ADR 0019: An added event's day and place come from its page

**Status:** PROPOSED, two questions open for Alp (below)
**Date:** 2026-10-22
**Inputs:** Alp, 2026-10-22: "by adding the event, we dont want user to give location and
date per hand, but when we are checking the url of the event, we should be finding that
info. Some events continues more than one day, so we do not need to be strict there."
Amends ADR 0014 (checks 5 and 6, the form, `api.suggest_event`) and ADR 0016 (an admin
approving a found event gives its day and place). ADR 0018 D8 (check 7 asks the page's
judgment) is unchanged.

## What this is for

A person adds an event by its URL alone. The check reads the event's day and place from
the page, as the board does, and the card shows what it found.

## What was measured

| Finding | Evidence |
|---|---|
| The given day and place exist to check a page without the LLM | ADR 0014 invariant 3: no LLM call before a check that needs none; checks 5 and 6 compare them with the structured fields or the page text |
| A running multi-day event is not past | `Judge.fact_drop` drops an event only when its last day, `ends_at` or else `starts_at`, is before today |
| The judge decides Munich from the fields | `Judge._in_munich`: the city field decides when there is one; else a postcode in the address or venue (Munich's prefixes or not); else a `munich.cities` name in them; else the place is unknown, which is not dropped |
| Text dates are already read | `extract/verify.py` `_spans` finds day and month spans in German and English, which `names_date` uses for check 5 today |
| Intake #16 (Eventbrite, JSON-LD) states its start, end, venue, address and city | Hosted event `3ae3941d6bb2` over the public API, read-only, checked 2026-10-22 |

## Invariants

1. Nothing about an added event reaches the board on the person's word: they now give
   nothing but the URL (ADR 0014 invariant 1 holds trivially).
2. No LLM call is spent on an added page before checks that need none have passed
   (ADR 0014 invariant 3, restated): one event on the page, a day not past, a place in
   Munich, and for a page without structured data a scope word in its text.
3. An event passes on any of its days: the check asks only that its last day is not past,
   the judge's own rule.

## The check, new rows 5 and 6

| # | Check | Refused with |
|---|---|---|
| 5 | Day: the structured event has a start whose last day (end, else start) is not past; without one, the page text names a day, alone or in a range, that is today or later | `rejected`: the page states no day for the event, or the event is over |
| 6 | Place: the structured venue, address or city is in Munich by the judge's rule; without one, or when it names no place, the page text names a `munich.cities` name or a Munich postcode | `rejected`: the page does not say the event is in Munich |

Checks 1 to 4 and 7 stay. After an extraction, 5 to 7 run again on the LLM's fields, as now.

## Decisions

| # | Decision | Rejected, and why |
|---|---|---|
| 1 | The form asks for the URL only; `api.suggest_event(url)`; `api.decide_intake(intake_id, approve)` approves a found event without a day and place | The day kept as optional: two paths to test, and the person still has to look it up. The place only: the judge already checks Munich |
| 2 | Checks 5 and 6 read the page as above; a multi-day event passes while any of its days is ahead | One day matched exactly: refuses a running festival or a conference given by a day the page writes as a range |
| 3 | The card shows the day and place the page states, from the event's fields, once it is on the board | Echoing them during `checking`: no fields are stored until `harvesting` |
| 4 | Migration 0007 drops the `CHECK` that an event intake has `starts_on` and `place`; the columns stay for older rows and stay empty on new ones; `api.event_place` goes | Dropping the columns: loses what older intakes were checked against |
| 5 | The page deploys at merge and the API changes when the next job opens the store, so the intake workflow is dispatched right after the merge | Old and new `suggest_event` side by side for a release: a second signature to keep for a gap of minutes on a site with a handful of users |

## Open questions for Alp

| # | Question | Recommended |
|---|---|---|
| 1 | A structured event whose page names no place at all (no city, address, postcode or Munich name): refuse, or let the judge keep it as an unknown place, as it keeps a tracked organiser's? | Refuse: an added page comes from a stranger, and the radar is about Munich |
| 2 | A page without structured data: require a coming day and a Munich place in its text before its one LLM call (invariant 2), or send every such page to the LLM and let the recheck decide? | Require them: the budget is 35 calls a day and each account may add 10 events |

## Plan

| Step | Change | Test first |
|---|---|---|
| 1 | Checks 5 and 6 in `intake/event.py`; a text-day finder beside `names_date` | Structured: running multi-day passes, past refused, no start refused, outside Munich refused; text: coming day and Munich name pass to the LLM, else refused |
| 2 | Migration 0007, `api.suggest_event(url)`, `api.decide_intake` without day and place, `Intakes.create_event(url)` | `test_access`: an event intake without day and place is accepted; the old caps and refusals hold |
| 3 | Page: the form and the found-event approval without day and place; the card's day and place; the step label "Check that the page names a coming day in Munich" | node tests for the form's payload |
| 4 | ADR 0014 and 0016 marked as amended here | |
