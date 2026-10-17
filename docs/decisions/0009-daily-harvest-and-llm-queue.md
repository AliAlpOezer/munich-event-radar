# ADR 0009: One harvest a day, and an LLM queue of current pages

**Status:** DECIDED, built on branch `llm-queue`
**Date:** 2026-10-17
**Inputs:** Alp, 2026-10-17: "open a PR for the LLM queue fix. One time fetching in every
24h would suffice. We do not need to renew (normally, in production) the schedule or search
for new events, as long as no new organiser got added to our organizers." The measurements
below. Supersedes the harvest schedule of ADR 0006 D1 ("every 2 h from 05:17 to 21:17 UTC").

## What this is for

The board is rebuilt once a day from everything due that day, and the LLM's 35 daily calls
go to pages whose text it has not read yet. A new organiser does not wait for that run: an
approved intake harvests its sources at once (ADR 0003, ADR 0006 D3).

## What was measured

From the hosted database's own timestamps (`run`, `snapshot`, LLM observations, `budget`,
`extraction_job`), read-only, 2026-10-17, with `measure_runs.py` in the records repository:

| Finding | Evidence |
|---|---|
| Only the first run of a UTC day does LLM work: it spends the day's 35 calls, about a minute each; every later run finds the budget spent and re-fetches only | runs 8, 13, 17: 23, 37, 36 min, 35 LLM jobs done each; the 12 other runs of 2026-10-13 to 10-17: 2-13 min, no LLM answer; `budget` 35 of 35 every day |
| A run whose fetch round is large and whose LLM work is the day's first passes the 50 min limit and is killed before judging, so that day's board is not rebuilt | run 21 (2026-10-17 09:28 UTC): 115 pages fetched in 11 min, first LLM answer at +13 min, 31 answers until GitHub cancelled it at 50 min; no `finished_at` |
| The queue grows: 462 jobs pending on 166 URLs (136 at the import on 2026-10-12) | `extraction_job` |
| Most pending jobs are older versions of a page whose newer version is also queued: 305 of 462 | jobs whose `snapshot_hash` is not the URL's latest stored version |
| The versions differ in markup, not in what the LLM reads: of 126 URLs with several pending versions, 115 have the same page text in all of them; 299 versions hold 137 texts (www.tum.de: 225 versions, 87 texts) | `html.text` of each pending version's body |
| An older version extracted after a newer one wins: its observation gets the later `observed_at`, which `resolve` takes as the URL's newest | `resolve.py` keeps the newest observation per URL and method; the queue is ordered by priority and attempts, not by version |
| GitHub starts a scheduled run late by a varying amount | runs scheduled at :17 started 09:28 to 10:18 UTC (`gh run list`, checked 2026-10-17) |

## Decisions

| # | Decision | Rejected, and why |
|---|---|---|
| 1 | `harvest` runs once a day, 04:17 UTC (06:17 Berlin in summer), after the LLM budget's UTC day turns. Event pages of A and B events and listings keep their 24 h cadence, other event pages 72 h, so the daily run fetches what is due | Keeping nine runs a day: after the first the budget is spent, and the rest only re-fetch (measured above); Alp: once in 24 h suffices. Two runs a day: nothing on the board needs fresher than a day, and the second would again find the budget spent |
| 2 | A source is due 6 h (`EARLY_HOURS`) before its cadence passes | Exact cadences: a source fetched at 10:05 yesterday is not due at 09:30 today and waits a second day. A 23 h cadence: still misses whenever GitHub starts more than an hour late |
| 3 | Only a page's current version is asked about: before working the queue, `drop_superseded` deletes the pending jobs whose URL has a newer stored version | Ordering the queue by version: the old versions still spend calls. Deleting only when a new version is queued: misses the backlog, and a URL whose new version needs no LLM keeps its old job |
| 4 | LLM answers are cached by the hash of the question, the page's URL and text, instead of the body's hash | A normalised body (scripts, nonces stripped): a guess at what varies, where the text is exactly what the model reads. A text hash on each job to skip queueing: a schema change for the same effect, since a cache hit costs no call |
| 5 | The LLM step takes no new job 60 min after the harvest starts, and the job may run 90 min: one LLM job can take 12 min more (four models, 180 s each), and judging always runs | Only raising the limit: a slow day at the free models still kills the run before judging. A separate extraction workflow: two jobs to order around one budget |

Answers already cached under body hashes are not found again, so a URL whose text has not
changed costs at most one more call, once. The cache key still ignores the date in the
system prompt, as before.

## Expected

The first daily run drops 305 superseded jobs, leaving 157 (at most one per URL); at 35
calls a day the backlog is gone in about five days. Then calls go only to pages whose text
changed, and to the pages a new organiser brings. The first daily run fetches about 180
pages (about 18 min at the measured rate), which leaves about 40 min for the LLM.

## Check

- Four tests, each written first and seen failing: only the current version of a page
  goes to the LLM; an unchanged text is asked about once; the queue stops at its deadline;
  a source fetched 20 h ago with a 24 h cadence is due, one fetched 10 h ago is not.
- pytest, the node tests, ruff; `event-radar replay` without the LLM unchanged.
- After merging: `measure_runs.py` over the first daily runs (run time, LLM answers, the
  pending count falling by about 35 a day).
