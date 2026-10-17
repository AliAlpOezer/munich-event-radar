# ADR 0012: Events aimed at children, school pupils or families leave the board

**Status:** DECIDED, built on branch `audience`
**Date:** 2026-10-18
**Inputs:** Alp, 2026-10-17: "radar should filter by audience. our audience is young adults/
adults.", then, asked whether family events (adults bringing their children) count:
"Families leave too". Widens ADR 0011's scope rule.

## What this is for

The board is for young adults and adults. An event aimed at children, school pupils or
families is not for them, whatever its topic or organiser: an AI workshop for children
leaves the board like a concert does.

## What was measured

The hosted board on 2026-10-17 (`api.events`, read as the page reads it): 162 events, A 17,
B 12, C 6. Two upcoming events are aimed at children, both from TUM central events:
"Children's Program at the Long Night of Museums" (`fc30aa152c8d`, A on the LLM's
`ai_relevance` quote "create social robots using AI") and "Children's University: How Is AI
Changing the World of Images and Videos?" (`79b6398bc7f9`, B). Among the 217 observed event
groups, the ones aimed at children or families say so in the title: six Children's
University dates, the Long Night of Museums programme, and "appliedAI Developers Meetup @
Heilbronn - Family Edition" (past, still stored). "Open House in Garching" states "Open to
all; students, staff, external guests, families" in `prerequisites` and is for everyone. The
extraction states no audience today.

## Invariants

| # | Invariant | Enforced by |
|---|---|---|
| 1 | An event aimed at children, school pupils or families is not stored, whatever its topic, kind, organiser or date; one already stored is deleted | The judge's audience check, first of all checks; `Events.save` |
| 2 | Decided in code: the LLM's stated `audience` is one input, a title phrase the other | `Judge.audience` |
| 3 | An event open to everyone stays, also when children are welcome | The extraction's wording of `audience`; title phrases name an audience, never a topic |
| 4 | Nothing observed is deleted, so narrowing the rule brings the events back on the next run | Observations are untouched |

## The audience rule

| Signal | Source | Example dropped |
|---|---|---|
| `audience` stated as `children`, `school_pupils` or `families`, with a quote the page holds | the LLM's extraction (`events-v2`) | a robotics course "for pupils in grades 9 to 12" |
| a phrase of `profile.yaml` `audience` in the title | the title, any source | Children's University: ..., Kinder-Uni, Schülerlabor, Girls'Day, ... Family Edition |

Kept: "AI in Children's Medicine" and "Familienunternehmen und KI" (a topic, not an
audience), "Open House in Garching" (stated `adults`).

## Decisions

| # | Decision | Rejected, and why |
|---|---|---|
| 1 | Children, school pupils and families leave; university students are the audience (Alp) | Children and pupils only: Alp chose families too. Hiding them behind a toggle: ADR 0011 D3, every visitor still downloads them |
| 2 | A drop reason of its own, `audience`, not stored, like `off_topic` | Folding it into `off_topic`: the run counts and dry runs could not tell a topic from an audience, and `off_topic` is the one drop the LLM can still change, while a title phrase is final |
| 3 | The extraction states `audience` (an enum with a quote, checked by `verify` like every field); its version goes from `events-v1` to `events-v2` | Words in `prerequisites`: free text, mostly null, and "Open to all; ... families" would match. Words in the description: a description names children as a topic ("children's privacy") as often as an audience. A separate LLM question: one more call per page, while the extraction already reads it. Keeping `events-v1`: the cached answers carry no audience, and the observation method would name the wrong question |
| 4 | Title phrases name an event format for the audience ("children's university", "kinder-uni", "schüler", "family edition"), never a bare word ("children", "kinder", "family", "pupils", "for children") | Bare words: "Children's Health", "Kinderschutz", "Family Office", "Familienunternehmen", "Pupil dilation" and "AI for Children's Health" are adult events. Recall first (`profile.yaml`); the LLM's `audience` catches the titles that do not say it |
| 5 | The audience check comes first, before past, online and outside Munich | After the fact drops, as the scope check (ADR 0011): a past or online children's event stays stored and shows under "Show past" or the dropped filter |
| 6 | An audience drop skips the relevance question and the extraction, like a past event | Asking anyway: the LLM cannot unsay the title, so the call buys nothing |
| 7 | The relevance question (`triage-v2`) is unchanged | Telling it children's events are not relevant: a skipped page gets no stated `audience`, so a children's event with an AI title from structured data would stay; the triage stays a cost saving (ADR 0010 invariant 3) |

## Cost

`events-v2` misses the cache once per page: a page is asked again at its next changed
snapshot that passes the relevance question, never before. Pages whose snapshot does not
change keep their `events-v1` observations, with no audience.

## Check

- Tests written first and seen failing: children's, pupils' and family events leave by
  title phrase and by stated audience, before past and whatever the organiser; topic words
  and "Open House" stay; a stored event that turns `audience` is deleted with what hangs on
  it; a children's page is skipped without the relevance question; `verify` keeps a quoted
  `audience`.
- pytest, node tests, ruff; replay without the LLM: A recall unchanged.
- Before merging: a read-only dry run over the hosted observations lists every event the
  next harvest deletes and the users' marks on them.

## How it landed (2026-10-18)

| Check | Result |
|---|---|
| New tests, before the code | 4 failed (the children's event stayed B, `verify` took any audience, the stored `audience` event was kept, the children's page was asked about) |
| pytest, node tests, ruff | 107 passed, 14 passed, clean |
| Replay without the LLM, against `main` | A recall 0.636 on both; no golden event changes |
| Dry run over the hosted observations (read-only, `audience_dry_run.py` in the records repository) | 3 of 162 events deleted: `fc30aa152c8d` Children's Program at the Long Night of Museums (A, held 2026-10-17; one user's star on it goes by cascade), `79b6398bc7f9` Children's University on AI and images (B), `62d844b59255` appliedAI's Family Edition meetup (past, Heilbronn). No status or push ledger row on them; no other tier changes |

Seen, not changed: the scope check of ADR 0011 comes after the fact drops, so 36 stored
events are off topic but kept as past (22), online only (13) or outside Munich (1), such as
"TUM tanzt!" and "Finissage for the Convivium exhibition"; they show under "Show past" and
the dropped filter.
