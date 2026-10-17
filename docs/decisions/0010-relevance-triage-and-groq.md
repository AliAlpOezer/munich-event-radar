# ADR 0010: Ask which events are about AI first, and fall back to Groq

**Status:** DECIDED, built on branch `llm-triage`
**Date:** 2026-10-17
**Inputs:** Alp, 2026-10-17: "go, build the Groq fallback. Lets change our architecture of
llm calls a bit. Lets collect all the events from a page, and at the first stage, check
only those ones which are about ai events (after that we collect all of them, put in a
tuple, we could give all of them once to an llm, it could iterate in one call on all of
those events, and mark the ones which are related. [...] If we see that they are having
the exact same json object attributes, (their json body is same) we dont have to try to
parse each and every of them individually." ADR 0009's measurements.

## What this is for

The radar's few free LLM calls go to the events it exists for, and extraction keeps
working when one provider is down or spent. True whatever the models: deciding what is
past or online stays in code, and a model only judges what code cannot.

## What was measured

The 155 pages waiting for the LLM on 2026-10-17 (read-only), parsed without it:

| | Pages |
|---|---|
| a structured event (JSON-LD) | 103 |
| no structured data | 52 |
| structured and past | 22 |
| structured and online-only | 14 |
| structured, judged A, B, C by keywords | 5, 3, 59 |
| identical event bodies across pages | 0 |

On www.tum.de, where most calls go, the LLM found any registration field on 35 of 91 pages
and a registration window on 7 (ADR 0009). Every call went to OpenRouter's free models;
their account limit is 50 requests a day below 10 credits purchased
(https://openrouter.ai/docs/api/reference/limits, checked 2026-10-17).

## Invariants

| # | Invariant | Enforced by |
|---|---|---|
| 1 | Past and online-only events never reach the LLM | The judge, in code, before any call |
| 2 | One question is asked once: a relevance verdict is cached by the event's own content (identical events across pages share it), an extraction by the page's text (ADR 0009) | `extraction` cache keyed by question hash and version |
| 3 | The relevance check only saves calls: it never removes an event from the board, and an event the model left unanswered counts as relevant | Triage writes no observation; the board's relevance stays the judge's (ADR 0002 D6) |
| 4 | One provider's cap, outage or timeout never stops the work while another has budget, and no provider passes its own daily cap | Per-provider budget rows; the router moves to the next provider |

## Components and seams

| Component | Does | Changes when |
|---|---|---|
| `Harvester.extract` | drops superseded jobs, builds a candidate per job, skips what the judge drops, triages the rest in batches, extracts the relevant ones | the order of the stages changes |
| `extract/triage.py` | the candidate of a page (its structured event, else its title and opening text), its key, and the batched relevance question | what counts as relevant changes |
| `Extractor` | asks the router, caches by question (per-page extraction and batched triage) | prompts change |
| `Router` | providers in order, each with its models, key and daily cap | a provider is added or its terms change |

| Seam | Carrier | Contract | Failure |
|---|---|---|---|
| Queue | `extraction_job` rows | `pending` to `done`, `needs_look`, or new `skipped` (no call owed: the judge drops the event, or triage found it unrelated) | a run that dies leaves jobs pending; the next run repeats only uncached questions |
| Candidate | a JSON object per job | key = sha256 of its canonical JSON; identical candidates share one verdict | a page that fails to parse becomes a text candidate |
| Triage answer | `{"verdicts": [{"id", "relevant"}]}`, ids are positions in the batch | unanswered or malformed ids count as relevant | all providers fail: jobs stay pending |
| Providers | `Router.complete(messages, schema, name)` | the first answer from OpenRouter's chain, then Groq's | `BudgetExhausted` when every provider is spent; `AllModelsFailed` otherwise |

A candidate is relevant when it is about artificial intelligence, machine learning or data
science, or is of a kind the profile surfaces whatever its topic (`any_topic_kinds`: career
fairs and hackathons). Pages of an organiser with `ai_focus` skip the question. Batches
hold 20 candidates, one call each.

## Decisions

| # | Decision | Rejected, and why |
|---|---|---|
| 1 | A relevance stage before extraction, batched 20 to a call | One call per page (today): 119 calls for the waiting pages, where 6 batched calls sort them and only the related ones get a call each. All candidates in one call: one malformed answer loses the whole day's sorting, and Groq's free tokens per minute are small. Relevance and extraction in one per-page call: still one call per page |
| 2 | Not-relevant events stay on the board without registration details | Dropping them: which events the board shows is the judge's job and Alp's planned relevance PR |
| 3 | Candidates come from parsing, which is free; a page without structured data is asked about by its title and first 600 characters | Extracting first, then sorting: the expensive call happens before the cheap one |
| 4 | Verdicts live in the `extraction` cache under version `triage-v1` | A new table: the same contract, an answer by the hash of its question |
| 5 | A new job state `skipped` | Marking such jobs `done`: it would claim an extraction that never happened |
| 6 | Groq is a second provider inside the router: `openai/gpt-oss-120b`, then `openai/gpt-oss-20b`, both with strict JSON schema (https://console.groq.com/docs/structured-outputs, checked 2026-10-17); key in the `GROQ_API_KEY` secret, absent means OpenRouter only | OmniRoute: a self-hosted gateway needs a server ADR 0006 removed, and still each provider's key. A second OpenRouter account: limits are per account and sharing them breaks its terms |
| 7 | Groq's daily cap is 30 calls (`RADAR_GROQ_DAILY_CAP`). Groq documents only Developer-plan limits (gpt-oss: 1K requests, 200K tokens a day, https://console.groq.com/docs/rate-limits, checked 2026-10-17); its free-plan limits show per account and are UNVERIFIED. An extraction takes about 6K tokens, so 200K tokens hold about 30 calls; a 429 ends Groq's turn for the run | Leaning on Groq's 429 alone: a call spent past the free limit is a failed call |
| 8 | A provider that times out or cannot be reached is skipped for the rest of the run | Retrying it per job: 180 s lost on each, past ADR 0009's deadline |

## Check

- Tests written first and seen failing: the router falls over to Groq when OpenRouter is
  spent or unreachable, and counts each budget apart; a past event and an unrelated event
  cost no extraction; identical candidates are asked about once; a cached verdict is not
  asked again.
- pytest, node tests, ruff; replay without the LLM unchanged.
- After merging: `measure_runs.py` shows the day's calls, and the run summary counts
  `llm_skipped`, `llm_unrelated` and the triage calls.
