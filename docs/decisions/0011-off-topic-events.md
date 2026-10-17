# ADR 0011: Events outside the radar's scope leave the board and the database

**Status:** DECIDED, built on branch `off-topic`
**Date:** 2026-10-17
**Inputs:** Alp, 2026-10-17: "let us clean the supabase data base unrelevant events", then,
asked what counts as relevant: AI, startup and career events (not every tech event), and
the others deleted rather than hidden. Amends ADR 0010 D2 ("not-relevant events stay on the
board") and the tier C rule of `profile.yaml` (ADR 0002 D6).

## What this is for

The board lists AI, startup and career events in Munich, and nothing else is stored as an
event. True whatever the sources: a general calendar such as TUM's can stay a source
without its concerts and dance classes reaching the board.

## What was measured

The hosted board on 2026-10-17 (`api.events`, read as the page reads it): 220 events, A 17,
B 12, C 64; dropped as past 96, online only 29, outside Munich 2. Of the 64 in C, 54 come
from TUM central events, 7 from BAdW and 3 from UnternehmerTUM; 56 are C only because their
AI relevance is unknown at an organiser that is not AI-focused, 7 because the LLM stated no
AI relevance, 1 for its price. Most are exhibitions, dance classes, theatre trips, alumni
days, Children's University and museum nights. 78 of the 220 events have a description;
most TUM events have only a title.

Each harvest rebuilds every event from all observations (`Harvester.judge_events`), so
deleting event rows alone brings them back on the next run.

## Invariants

| # | Invariant | Enforced by |
|---|---|---|
| 1 | An event is on the board only when it is about AI, about startups, about careers, of a kind the profile surfaces whatever its topic, or from an AI-focused or startup organiser | The judge's scope check, before any tier |
| 2 | Off topic is decided in code, never by a model alone; a model's stated `ai_relevance` is one input, as for tiers | `Judge.judge` |
| 3 | An off-topic event is not stored; one already stored is deleted with what hangs on it | `Events.save` |
| 4 | Nothing observed is deleted, so widening the scope brings the events back on the next run | Observations are untouched |
| 5 | A keyword verdict on a bare title never stops the LLM from looking: off topic does not skip extraction's relevance question | `Harvester._worth_extracting` |

## The scope rule

| Signal | Source | Example kept |
|---|---|---|
| AI relevance `core` or `some` | keywords, or the LLM's stated `ai_relevance` | Munich AI Lecture |
| AI relevance unknown at an `ai_focus` organiser | `seed/organisers.yaml`, intake | AAIF Community Munich #29 |
| a kind in `any_topic_kinds` (career fairs, hackathons) | kind keywords, or the LLM's stated kind | Fit for TUMorrow Day |
| an organiser of kind `startup` | `seed/organisers.yaml`, intake | Opportunity Hunters Talks (UnternehmerTUM) |
| a startup or career word in title, description or organiser | `profile.yaml` `scope` | Industrial Edge Workshops for Startups, Engineers for Germany: Career Lounge |

Anything else is dropped as `off_topic`. Tier C now means in scope but paid, or in scope
but not about AI.

## Decisions

| # | Decision | Rejected, and why |
|---|---|---|
| 1 | The scope is AI, startups and careers (Alp) | AI only: startup events such as Opportunity Hunters Talks would go. AI plus any tech: keeps TUM Blockchain Conference and cloud meetups, which Alp did not want |
| 2 | Off topic is a judge drop, from keywords, kinds and organiser flags | The LLM triage verdict of ADR 0010 deciding the board: it exists only for pages in the LLM queue, not for feed or listing events, and ADR 0010 invariant 3 keeps it a cost saving. Deleting the rows once: the next harvest rebuilds them |
| 3 | Off-topic events are not stored, and a stored one is deleted with its aliases and push ledger rows; users' statuses and stars on it go by cascade | Keeping them as dropped rows behind a toggle, like "Online only": every visitor still downloads them, and Alp chose deletion. Keeping an event because a user marked it: the board's scope is the radar's, and an event can turn off topic only when its facts or the scope change |
| 4 | An organiser of kind `startup` (UnternehmerTUM, Munich Startup) puts all its events in scope, like `ai_focus` for AI | A new `startup_focus` column: the organiser's kind already says it, and the intake already sets it |
| 5 | The scope check comes before the price check, so a paid off-topic event goes too | Price first: a paid concert would stay in C |
| 6 | Off topic does not skip the relevance question before extraction, and the question now asks about startups and careers too (`triage-v2`, so the cached AI-only verdicts are asked again, about 5 calls) | Skipping off-topic pages like past ones: most TUM pages have only a title, so the keyword verdict is the weak one; the LLM's extraction can state an AI relevance that keeps the event. Keeping `triage-v1`: its cached "unrelated" would skip startup events |
| 7 | A startup organiser's pages skip the relevance question, like an AI-focused organiser's | Asking: the judge keeps their events whatever the answer, so the call buys nothing |

## Check

- Tests written first and seen failing: an off-topic event is dropped (a dance class, a
  blockchain conference, a paid fermentation evening); startup and career events and a
  startup organiser's events stay in C; saving an off-topic event deletes the stored one,
  its alias, its push ledger rows and users' stars; an off-topic page still goes to the
  relevance question.
- pytest, node tests, ruff; replay without the LLM: A recall unchanged.
- Before merging: a read-only dry run over the hosted observations lists every event that
  the next harvest deletes and the users' marks on them.
