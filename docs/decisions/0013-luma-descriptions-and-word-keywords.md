# ADR 0013: Luma events are judged by their description, and keywords match words

**Status:** DECIDED, built on branch `luma-description`
**Date:** 2026-10-18
**Inputs:** the stored events that ADR 0011's scope check finds off topic but keeps behind an
earlier drop, checked one by one before deleting them, since Alp wants them gone "if they
are irrelevant" (2026-10-18). Amends how `profile.yaml` keywords match (ADR 0002 D6).

## What this is for

ADR 0011 keeps an event whose title, description or organiser holds a startup or career
word, and its invariant 5 says a keyword verdict on a bare title never stops the LLM from
looking. Luma event pages broke both: the adapter read no description, and their structured
data sends them to no LLM. So a Luma event of an organiser that is neither AI-focused nor a
startup was judged on its title alone, and deleted when the title named no scope word.

## What was measured

The hosted observations on 2026-10-18, read-only (`board_dump.py` and `board_compare.py`
in the records repository, with each Luma observation parsed again from its stored body):

| Finding | Evidence |
|---|---|
| START Munich (kind `student`) and CDTM (kind `industry`) publish on Luma only, and none of their events carries a description | 21 event groups (START Munich 17, CDTM 4), all observed by `luma@2` only, none with a description |
| Upcoming START Munich events dropped as off topic | "YC Stories - From SF To Munich" (2026-10-19), "Why START? Info Session" (2026-10-22), "START & Friends Run Club - Coffee Run" (2026-10-24) |
| Luma's page data holds the description, as a ProseMirror document in `description_mirror` | YC Stories: "startup culture is defined by Y Combinator ... Builders, founders and anyone interested" |
| Reading it, with the judge unchanged, moves 5 verdicts, 2 of them wrongly | "How to fundraise in the age of AI" B to A as kind `hackathon`, from its "About roboTUM ... a week of workshops, hackathons" paragraph; "Why START? Info Session" off topic to A as kind `programme`, from a speaker's startup "part of the Schub accelerator" |
| The golden replay without the LLM loses `cloud-native-october` (off topic) to B | The keyword `" ai"` matches "confidential computing aims to"; `"ai "` matches "Thai" and "Dubai" the same way |

## Invariants

| # | Invariant | Enforced by |
|---|---|---|
| 1 | A Luma event is judged on its title, description and organiser, like an event from any other parser | The Luma adapter |
| 2 | A keyword matches words, never part of a word it does not name, whatever punctuation surrounds it | `Judge._text`, and `Profile.load` refusing a keyword with punctuation |
| 3 | An event's kind comes from what the event calls itself: its title, or the kind the LLM states | `Judge.kind` |

## Decisions

| # | Decision | Rejected, and why |
|---|---|---|
| 1 | The Luma adapter reads `description_mirror` as text, a line per paragraph or line break. `luma@2` becomes `luma@3`, so each Luma page is observed again at its next fetch (event pages every 72 h) | START Munich and CDTM as organisers of kind `startup` (ADR 0011 D4): mends two organisers, not the next one on Luma, and START Munich is a student initiative. Sending Luma pages to the LLM: a call per page for text the page already holds as data. The `description` meta tag: cut at 160 characters |
| 2 | Keywords match words: the judge lowercases title, description and organiser and reads punctuation as a space. Keywords are written without punctuation (`" ki "` covers "KI-gestützt", `"start up"` covers "start-up"), and `Profile.load` refuses one with punctuation | Substrings as before, dropping only `" ai"` and `"ai "`: "AI," and "(AI)" would stop counting. A regular expression per keyword: the profile stays a plain word list Alp can edit |
| 3 | The kind is read from the title alone; a kind the LLM states still wins | Title first, then the whole text, as before: a description names other formats too, and the first kind in the profile's order wins, so a talk became a hackathon. The kind mentioned first in the description: the "About" paragraphs still decide. A kinds from the title only, the others from the text: the description would still decide `any_topic_kinds` (career fair, hackathon), which puts an event in scope |

## Cost

| Case | Effect |
|---|---|
| ITCS Munich, golden A, replayed without the LLM | B: `conference` came from text other than its title "ITCS Munich". It still surfaces, so A recall is unchanged; on the hosted board the LLM states its kind and it stays A |
| "Why START? Info Session" | B, not C: a speaker is "Founder of Kleia, an AI companion". AI relevance from a description is the recall-first rule every parser already has (ADR 0002 D6) |
| "Mobilität im Klimawandel" (Munich Climate Week, via TUM) | C, not off topic: a panellist is "Co-founder & CEO", and a startup word anywhere in the text puts an event in scope (ADR 0011) |

## Check

- Tests seen failing against the code before the change: a Luma page's description is read and keeps a
  bare-titled event in scope; "AI,", "(AI)", "AI-powered" and "KI-gestützt" count, "aims",
  "air in Dubai" and "Ski-Ausflug" do not; "hackathons" in a description does not make a
  talk a hackathon; a keyword with punctuation is refused.
- pytest, node tests, ruff; replay without the LLM, against `main`.
- A read-only dry run over the hosted observations lists every verdict that moves.

## How it landed (2026-10-18)

| Check | Result |
|---|---|
| New tests, against the code before the change | 4 failed (no `description` on the Luma event, "(AI)" not AI-relevant, the talk was A, the keyword `"ki-"` loaded) |
| pytest, node tests, ruff | 107 passed, 14 passed, clean |
| Replay without the LLM, against `main` | A recall 0.636 on both; `itcs-munich` A to B (see Cost); `cloud-native-october` stays off topic; no other golden event moves |
| Dry run over the hosted observations, against `main` (26 Luma observations parsed again; 14 older ones have no stored body left) | 4 verdicts move, A unchanged: YC Stories, the Coffee Run and "Mobilität im Klimawandel" off topic to C, "Why START? Info Session" off topic to B. No status, star or push ledger row on them |
