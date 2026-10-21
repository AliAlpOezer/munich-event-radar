# ADR 0018: The LLM judges what every event is about; code decides the board

**Status:** DECIDED, building on branch `judgments`
**Date:** 2026-10-20
**Inputs:** Alp, 2026-10-20, on the refused intake of
https://www.eventbrite.com/e/tum-speakers-series-x-susanne-marell-roland-hehn-tickets-2003259324566:
"its not only about ai, but our focus is ai, but [the] IT of lidl/schwarz group is also
interesting to us. It is not the same similarity for example a salsa workshop [...] we need
to find a efficient, smart way that works well and fast". Asked how the radar should decide
what an event is about, Alp chose "LLM judgments, code decides" over embedding similarity,
and general tech events "Yes, as tier C". Asked whether an AI institute's talk that is not
about AI should be off topic or C, Alp chose C (2026-10-20). Amends ADR 0010 (the triage),
ADR 0011 D1 (the scope) and ADR 0011's scope rule for AI-focused organisers; builds on ADR 0011 invariant 2 (a model states, code decides) and ADR 0014 invariant 2
(one rule for every event).

## What this is for

Whether an event belongs on the board, and how high, is decided from what its page says,
read as a person would read it, and in the same way for every event. AI stays the focus;
startup, career and tech events show below it; a salsa workshop does not show. The answer
for an added event comes within seconds.

## What was measured

| Finding | Evidence |
|---|---|
| The Schwarz talk was refused as off topic, at intake #3 (2026-10-19 19:24 UTC), and reproduced locally on 2026-10-20 | hosted `intake` row, read-only; the drain on a local database |
| The judge read its title and Eventbrite's JSON-LD summary only: "Join us for an exciting event with Susanne Marell (EVP Schwarz Corporate Affairs) & Roland Hehn (CHRO Schwarz Corporate Solutions)". No AI or scope word | `parse` on the fetched page; `Judge.judge`: `off_topic`, `ai=unknown`, `kind=None` |
| The page's own text says what the talk is: "what AI and the future of work mean for the next generation of talent", "recruiters from the companies of Schwarz Group" | `html.text` of the page, "About the Event" |
| The page text is read only when the page goes to the LLM, and a page with structured registration fields does not | `check_relevance` (ADR 0014 D4 of its build notes); `parse` sets `needs_llm` only when registration fields are missing |
| Had the intake passed it, the next harvest would have deleted it: the judge reads the same fields | `Harvester.judge_events`, ADR 0011 invariant 3 |
| Words cannot make this call: "recruiters" is not `recruiting`, and Schwarz Group's IT names no scope word | `profile.yaml` `scope` |
| The LLM's relevance verdict exists only for pages in the LLM queue, as a yes or no that writes nothing | ADR 0010 invariant 3 |
| A dry run of `judgments-v2` over the hosted board (read-only, 2026-10-20): 16 of the 89 events owing a judgment are judged by the fields of a feed or listing item, and a bare title was judged `ai: none`. "AAIF Community Munich #29" (a Luma feed item: "Get up-to-date information at: https://luma.com/sfkb4b7f") went from B to off topic, as an AI-focused organiser's event not about AI, and "DLD Munich 27" (its title alone) from B to C | `differential/judgment_dry_run.py` in the records repository |
| The dry run of `judgments-v3` (read-only, 2026-10-21): 85 events owe a judgment, asked in 9 calls in 585 s; 11 answers are `unknown`, among them the AAIF meetup and DLD, which stay B. 12 verdicts move: 7 from off topic to C (startup or tech events, such as the TUM Blockchain Conference and Munich Climate Week), MCML's econometrics colloquium and an MDSI workshop on research data management from B to C, ITCS München from B to A, TUM Open Campus from off topic to an audience drop, and MCube's talk on climate-resilient streets from C to off topic | `differential/judgment_dry_run-2026-10-21.jsonl` in the records repository |
| Groq's gpt-oss models: 30 requests and 8K tokens a minute, 1K requests and 200K tokens a day, on the Developer plan; free-plan limits are shown per account (UNVERIFIED) | https://console.groq.com/docs/rate-limits, checked 2026-10-20 |

## Invariants

| # | Invariant | Enforced by |
|---|---|---|
| 1 | A model states what an event is about; code and `profile.yaml` decide scope and tier (ADR 0011 invariant 2) | `Judge.judge` reads a judgment as one input |
| 2 | Every event in scope by its facts is judged by the same question on what its page says, whatever its source: harvested, added or extracted | one candidate rule, one question, `judgments.VERSION` |
| 3 | One question is asked once: a judgment is cached by the content it read, so an unchanged page or event is never asked again | `judgment` table keyed by the candidate's hash |
| 4 | A missing answer never deletes an event: an event whose new candidate is unanswered, or answered only to an older version of the question, is judged by the answer it was last judged by | `event.judgment_key`, `Harvester.judge_events` |
| 5 | No fact is written by a judgment: dates, places, prices and registration stay with the parsers and the LLM's quoted fields (ADR 0002 D8) | the judgment schema holds topics, a kind and an audience only |
| 6 | Past, online-only and outside-Munich events are never asked about (ADR 0010 invariant 1, ADR 0011 D9) | `Judge.fact_drop` before a candidate is asked |
| 7 | With no LLM, or a judgment that says its text was too thin to tell, today's radar remains: keywords and scope words decide, as before this ADR | the judge falls back when no judgment is answered; `judgments.telling` reads an `unknown` one as none |

## The judgment

One batched question, `judgments-v3`, about up to 10 candidates per call. Per candidate:

| Field | Values |
|---|---|
| `ai` | `core` (its text names AI, machine learning or data science as the topic), `some` (as one theme among others), `none` (its text says what the event is about, and that is not AI), `unknown` (its text says too little to tell, such as a bare title or a link) |
| `startup` | true when it is about startups, founding or investing |
| `career` | true when it is about careers, jobs or hiring, or employers meet students there |
| `tech` | true when its subject is software, IT, engineering or a digital industry; science, art or a show that touches on technology is not |
| `kind` | one of the 12 kinds of the extraction schema |
| `audience` | `adults`, `families`, `school_pupils`, `children` |

What a candidate is:

| The event | Its candidate | Why |
|---|---|---|
| The one event of a page (an event page, or any page that parses or extracts to one event) | the page's `<title>` and the first 2,000 characters of its text, without link addresses | The page says what the event is; the same page has the same candidate before and after extraction, so the question that decides whether to extract is the one the judge reads |
| An event of a listing or feed | its title, description, organiser and venue | The listing's text is about many events |

## How the judge reads it

A judgment that answers `ai: unknown` counts as no judgment, in every row below (D10).

| Decision | Order |
|---|---|
| AI relevance | the judgment, then the LLM's quoted `ai_relevance`, then keywords |
| Kind | a stated kind (parser or quoted LLM field), then the judgment, then title keywords |
| Audience other than adults | any of: the LLM's quoted field, the judgment, a title phrase (ADR 0012) |
| Scope | AI `core` or `some`, an AI-focused or startup organiser, an `any_topic_kinds` kind, or a topic of `profile.yaml` `topics` the judgment names; scope words only for an event without an answered judgment |
| Tier | AI `none` in scope is C, so a startup, career or tech event without AI is C, and so is an AI-focused organiser's event not about AI (D9); a career fair or hackathon (`any_topic_kinds`) ranks by its kind whatever its AI relevance, as it did when keywords left that relevance unknown |

## Decisions

| # | Decision | Rejected, and why |
|---|---|---|
| 1 | A judgment is its own question, asked of every event whose facts keep it, not only of pages the LLM extracts | Topics in the extraction schema: structured pages are never extracted, which is how the Schwarz talk was refused. Embedding similarity to labelled examples (Alp chose judgments): a model host and a calibrated threshold, and ADR 0017's open models were weak zero-shot. More keywords: "recruiters", "Schwarz IT" and "sovereign cloud" would each need a word, and each word drags in events it should not |
| 2 | The candidate of a page's one event is the page text, of a listing's event its fields (table above) | Fields only: Eventbrite's summary names no topic; the page does. The whole page: 12,000 characters, and Groq's 8K tokens a minute would hold one page a minute |
| 3 | Candidates are written with the observation (`observation.judgment_key`, a `judgment` row with the candidate), and asked outside the judge lock, in batches of 10 | Judgments as observations: a judgment carrying copies of the title and day forms a phantom event once the page moves the day. Building candidates when judging: every page body read on every judge run, about 150 from a runner in the US. Asking under the judge lock: an added event would wait for the harvest's calls |
| 4 | An added event's judgment asks Groq first; the harvest's judgments and extraction keep OpenRouter first (ADR 0010 D6) | OpenRouter first for the added event: its free models take 12 to 49 s a call (ADR 0010), and the person who added it waits. Groq first for the harvest too: its 30 calls a day (`RADAR_GROQ_DAILY_CAP`) are extraction's fallback |
| 5 | Tech is in scope as C: `profile.yaml` `topics: [startup, career, tech]` (Alp, 2026-10-20) | AI, startups and careers only, as ADR 0011 D1 chose on 2026-10-17: Alp now wants Schwarz Group's IT on the board, and graded, below AI. Removing `tech` from `topics` restores ADR 0011's scope |
| 6 | An event whose new candidate is unanswered is judged by the answer it was last judged by (`event.judgment_key`), and an answer to an older version of the question holds until the current version answers; an event never judged is judged by keywords meanwhile | Keywords for every unjudged event: an LLM outage, a changed page or a new question version would delete an event kept on its judgment, and users' stars with it (ADR 0011 D3). Keeping the stored row unchanged while its judgment is owed (this ADR's first draft): the row's stars, dates and registration froze until the LLM answered, and forever without one. Showing nothing until judged: an added event would wait for the LLM even when keywords keep it |
| 7 | Judgments replace the yes or no triage: a page in the LLM queue is extracted when its judgment is in scope or its organiser is AI-focused or a startup. `triage.py` keeps only what `event-radar calibrate` reads of the old `triage-v2` answers | Both questions: two calls about the same page. Removing the reader: ADR 0017's D1 measurement reads those labels; its re-measure (Q9) should move to judgments |
| 8 | An added event's check 7 asks its page's judgment at once, one call; without an answer it is today's check | Waiting for the daily run: the reason for this ADR |
| 9 | AI-focused and startup organisers' events are judged too. An AI-focused organiser's event that is not about AI, judged or stated by the LLM's extraction, is C, as a startup organiser's event is: in scope by its organiser, below the AI events (Alp, 2026-10-20, on MCML's econometrics colloquium "Correlation Robust Inference When Economic Distance Is Latent") | Off topic, as ADR 0011's scope rule had it for an event the LLM stated not about AI: Alp chose C. Skipping them, as the triage did: their tier then rests on title keywords, and an AI club's summer party is A or B by kind alone |
| 10 | A judgment can answer that its text says too little to tell (`ai: unknown`), and such an answer counts as no judgment: the event's words decide its tier, its page is extracted, and an added event gets the check it had before this ADR (`judgments-v3`) | `none` for every text that does not name AI (`judgments-v2`): a bare title is then evidence against AI, and the dry run moved the AAIF meetup from B to off topic and DLD from B to C. Answering from what the model knows of an event series (`judgments-v1`, which judged the AAIF meetup `core`): that is not what the page says, and v1 also judged an academy's lecture on religion `core`. Keeping the topics, kind and audience of an `unknown` answer: they are read from the same thin text, and one rule is easier to check than four |

## Components and seams

| Component | Changes when | Carrier and contract | Failure |
|---|---|---|---|
| `extract/judgments.py` | the question or the candidate rule changes | `candidate`, `key`, the batched question and its schema; `VERSION`; `telling`, which every reader of an answer applies | a malformed or missing answer for an id leaves it unanswered |
| `store/judgments.py`, table `judgment` | what is kept changes | key, candidate, version, model, result; `want` writes candidates, `answers` reads them | a candidate of another version counts as unanswered |
| `Extractor.judge` | batching changes | asks the unanswered keys, provider `first` first (Groq for an added event), writes answers | budget spent or every model failed: the keys stay unanswered |
| `Harvester.ask_judgments` | which events are asked about | the groups that no fact drops, and the LLM queue's pages | nothing asked: invariant 7 |
| `Judge.judge(..., judgment)` | `profile.yaml` or the order above changes | a judgment dict or None | None is today's judge |
| Migration 0006 | | `observation.judgment_key` (the candidate its event is judged by), `event.judgment_key` (the judgment it was last judged by), table `judgment` | |

Every candidate costs input tokens once. With 10 candidates of about 600 tokens, a batch is
about 6K tokens and the question, under Groq's 8K a minute. A new version of the question
asks every candidate again once; answers keep the same fields, so the older answers hold
meanwhile, and a version that changes the fields must clear them.

## Plan

| # | Step | Check |
|---|---|---|
| 1 | Migration 0006; `extract/judgments.py`; `store/judgments.py`; candidates written at ingest and extraction | tests: a page's candidate is its text, and the same before and after extraction; a listing's events have their fields |
| 2 | `Router.complete(first=)`, `Extractor.judge`, `Harvester.ask_judgments`, the triage replaced | a fake router: a cached judgment is not asked again; an off-topic judgment skips the extraction; a fact-dropped event is not asked |
| 3 | `Judge.judge` with a judgment; `topics` in `profile.yaml`; `event.judgment_key` | the Schwarz talk (hand-written fixture) is kept, a salsa workshop dropped, a cloud meetup C; an event whose changed page, or whose question's new version, waits for an answer keeps its tier; one with five stars still rises to B |
| 4 | The intake's check 7 | the Schwarz fixture is added; without an LLM it is refused as today |
| 5 | How it landed: pytest, node tests, ruff, replay without the LLM against `main`; a read-only dry run over the hosted observations that lists every verdict that moves, shown to Alp before merging | |
