# ADR 0017: Closed-answer decisions go to a decision model; the LLM keeps the quoted facts

**Status:** PROPOSED. No paid Jev: a free open model first (wayfinder Q1, Alp 2026-10-19), `laya-multilingual` then Kev-0.8B (Q7), calibrated in Q2; no code yet
**Date:** 2026-10-19
**Inputs:** Alp, 2026-10-19: "i have decided to think about using jev instead of llm calls,
when we are deciding for events, organizations, etc. [...] where they could cut waiting &
llm calls without compromising quality." TypeSafe's documentation, read 2026-10-19. The
measurements of ADR 0009 and ADR 0010. Route and open questions:
`../munich-event-radar-records/docs/context/wayfinder/jev/map.md`.

## What this is for

The radar's few, slow free LLM calls go only to work that needs quoted facts copied from a
page. Every decision with a closed answer, such as "is this in scope", "does this page
state how to register" or "is this a real organisation in Munich", is made in under a
second. A decision moves to the faster method only where measurement shows it is no worse
than today's method. Everywhere else, today's method still decides.

## What Jev is (checked 2026-10-19)

| Fact | Source |
|---|---|
| A "System One" decision model from TypeSafe AI. It returns typed answers with probabilities and never generates text. Question types: `noul` (probability of yes), `choice` (at most 255 options, with a distribution), `score` (2 to 10 ordered levels) | https://docs.typesafe.ai/api |
| One endpoint, `POST https://api.typesafe.ai/v1/systemone`. A request sends one `state` with many named questions, and the state is read once | https://docs.typesafe.ai/api, https://docs.typesafe.ai/models |
| Price: $0.042 per million input tokens, output free. Context: 64k tokens per request, 32k per question | https://docs.typesafe.ai/models |
| Model IDs `jev-1.13.0` and `jev-1.14.0` (from 2026-10-14). The alias `jev-latest` points to 1.13 and moves when a release ships. The docs advise sending the versioned ID once thresholds are tuned | https://docs.typesafe.ai/models |
| English is the main training language. Other languages are "handled but not equally well". Many of the radar's pages are German | https://docs.typesafe.ai/models |
| Known weak points: literal reading, numbers, date comparison, indirection, large state with irrelevant detail, adversarial page text, bias toward the first Choice option, generation | https://docs.typesafe.ai/model-jaggedness/jev-1.13 (reviewed by TypeSafe 2026-10-02) |
| Rate limits are set per organisation and shown in the console. Requests are not used for training | https://docs.typesafe.ai/models |
| Reported speed 70 to 500 ms. On the vendor's four workflows: Jev 68%, GPT-5.6 Terra 68%, Opus 5 73% accuracy. These are early, vendor-reported results that no one has reproduced | https://arize.com/blog/typesafe-jev-llm-judge/ |
| No free tier is advertised, and the direct API may need a waitlist. Both claims come only from secondary sources: UNVERIFIED | https://www.eesel.ai/blog/typesafe-jev-pricing, https://flaviocopes.com/jev-pricing/ |
| Vercel AI Gateway serves Jev at the same price, in TypeSafe's request format: `POST https://ai-gateway.vercel.sh/v1/evaluate` with the gateway key. Jev is not on the gateway's free tier, so it needs purchased credits (checked 2026-10-19) | https://vercel.com/changelog/ai-gateway-now-supports-typesafe-clients-and-an-http-api-for-jev, https://vercel.com/docs/ai-gateway/pricing |
| The Python SDK is at v0.7.5, with three releases from 2026-10-09 to 2026-10-14 | https://docs.typesafe.ai/sdk/python/changelog |

## Where the radar decides today

| # | Decision | Where | Today | Cost measured |
|---|---|---|---|---|
| 1 | Is this page's event in scope, so worth an extraction? | `Harvester._worth_extracting`, `extract/triage.py` | LLM, 20 candidates per call | 89 candidates in 5 calls of 12 to 49 s (ADR 0010, 2026-10-17) |
| 2 | Is an extraction owed at all? | `parse`: every event page without registration fields | none: every such page goes to the LLM | the LLM found a registration field on 35 of 91 tum.de pages and a window date on 7 (ADR 0009) |
| 3 | AI relevance, kind, audience of an event | the LLM's extraction enums; otherwise keywords in `judge.py` | LLM when a page was extracted, keywords otherwise | part of each extraction call, about 1 min |
| 4 | Is a submitted organisation real, in Munich, running events, AI-focused? Which links list its events? | `intake/profiling.py` | LLM, one call | about 1 min per intake. With no LLM key the intake goes to `review` |
| 5 | Is a search hit an organiser, an event or noise? | `search/gate.py` | code words; the first 5 a day wait for review | dry run: 104 unknown candidates, noise left to the admin (2026-10-19 log) |
| 6 | Is a text-only page an event submission in scope? | `intake/event.py` check 7 | code words (`names_scope`) | free |

## The split

Each decision goes to the cheapest method that can make it at measured quality. When that
method is unsure, the next one decides:

1. **Code rules** decide what code can compute: past, online only, outside Munich, audience
   phrases, dates, counts. Jev is weak at exactly these.
2. **Jev** gives judgments with a closed answer: scope, whether a page states registration
   details, page type, organisation traits, which link lists events.
3. **The LLM** extracts facts that need a verified quote: dates, deadlines, URLs, price,
   title, venue.

Today, decisions 1 and 3 ride inside LLM calls, and decision 2 is not asked. Asking decision 2
first is where most of the savings come from. The LLM is called for a structured page only
when the page states registration details the parser missed.

## Invariants

| # | Invariant | Enforced by |
|---|---|---|
| 1 | Jev never writes a fact. No date, time, price, URL, venue, organiser or registration state comes from Jev | `Decider` returns verdicts, not fields. Only Q4 could let Jev write fields, limited to the three judgment enums |
| 2 | Jev acts only inside a band measured for its exact model ID. Outside the band, or when Jev is down, slow, spent or has no key, today's path runs unchanged. Removing `TYPESAFE_API_KEY` gives back today's radar | each band in `profile.yaml` names the `model` it was tuned on, and is ignored for any other ID. Tests and `replay` with Jev off match today's output byte for byte |
| 3 | Jev skips work only when it is confident the work is not owed. A false skip is measured, and a page whose text changes is decided again | asymmetric bands. The audit sampler (D7). The cache is keyed by page text |
| 4 | Questions live in code, numbers live in data. Bands, modes and the daily cap are in `profile.yaml`, set from a calibration report kept in the records repository | `decide/questions.py` holds no thresholds |
| 5 | Spending has a ceiling: at most `decisions.daily_cap` Jev requests per UTC day. Past the cap, Jev answers "unsure" | the `budget` ledger, provider `typesafe`, as for Tavily (ADR 0016) |

## Decisions per site

| Site | Question(s) to Jev | Acts when | Otherwise (today's path) |
|---|---|---|---|
| D1 triage (decision 1) | One `noul` per `profile.yaml` scope group: ai, startup, career, developer, and "a hackathon or career fair". Each group's words, with their German glosses, are its criteria. The nouls are independent of each other, so option order cannot bias them | every group's probability is below `lo`: skip | keep (ADR 0010's "when unsure, relevant"). With Jev off, the LLM triage runs as today |
| D2 extraction owed (decision 2) | Atomic `noul`s on the page's structured event plus the passages code finds around registration words: a way to sign up; a deadline or opening date; a price; places left | every probability is below `lo`: the page is judged on its structured fields, and no LLM call is made. Only once Q4 settles where its `ai_relevance` and `kind` come from: skipping 21 such pages moved 2 of 16 A events (Q6, 2026-10-19) | LLM extraction, as today |
| D3 judgments (decision 3) | `choice` ai_relevance (core, some, none), `choice` kind (the 12 kinds), `choice` audience | only after Q4: written as their own observation method `jev:<model>@<version>`, ranked below parsers and quoted LLM fields | keywords, as today |
| D4 organisation (decision 4) | `noul`s: own site; parked, shop or link farm; lists other organisers' events; hosts events; based in Munich; AI is the main topic. `choice` kind. One `noul` per candidate link: "lists this organisation's events" | all confident: `finding` or `rejected` (with "add anyway", ADR 0003) | `review`. What the admin sees in place of the LLM's reasons is Q5 |
| D5 search hits (decision 5) | `choice` page type (organiser home, organiser events page, single event page, listing of other organisers, news, jobs, course provider, other), plus the D1 scope nouls and a Munich `noul` | ranks the day's candidates. Below `lo`, a hit is dropped with reason `jev_noise` | the code gate's order. ADR 0016 invariant 1 holds: an admin still approves every hit |
| D6 event intake (decision 6) | the D1 nouls on the page text | a confident no: refused as off topic | `names_scope`, as today |

D1, D5 and D6 ask one question, "in scope", at three sites, so it is calibrated once.

## Components and seams

| Component | Does | Changes when |
|---|---|---|
| `decide/systemone.py` | sends one POST of the System One protocol with httpx, which Jev, `laya.serve` and `kev.serve` all speak: versioned model ID, 5 s timeout, one retry on 429 honouring `retry-after`. After a timeout, the model is skipped for the rest of the run. Built for step 2: the POST and its timeout; the retry and the skip come with the `Decider` | the protocol changes |
| `decide/questions.py` | the catalogue: `in_scope`, `registration`, `judgments`, `organisation`, `search_hit`. Each is a versioned function from the profile and a state to questions. Built for step 2: `in_scope-v1` (one noul per scope group and one for the any-topic kinds) and `registration-v1` (sign-up, window, price, places), and the passages, whose words live in `profile.yaml` `decisions.registration` | what we ask changes |
| `Decider` (`decide/__init__.py`) | builds and trims the state (only what the question needs), reads and writes the cache, takes from the budget, applies bands, returns a `Decision` | banding policy changes |
| `profile.yaml` `decisions:` | per decision: `mode` (off, shadow, on), `model`, `bands`. Also `daily_cap` and `audit` | a calibration report says so |
| `event-radar calibrate` | offline and read-only: asks a model about hosted page texts that already have LLM answers, and writes `rows.jsonl` (URL, label, probabilities; no page text) and `summary.json` (AUROC, recall and skips per band, German and English apart, seconds, tokens) to `--out`. `--labels-only` counts the labels without a model. Run by the manual `calibrate` workflow, the model served on the runner's CPU | the metrics change |

| Seam | Carrier | Contract | Failure |
|---|---|---|---|
| call site to `Decider` | in-process call | `decide(name, state) -> Decision(answer: yes, no or unsure; p: probabilities; model)`. In mode `shadow` the answer is always `unsure`, and `p` is counted | never raises. Every failure is `unsure` |
| `Decider` to TypeSafe | HTTPS | the API reference's JSON, with the versioned model ID | timeout, 5xx, 429 after the retry, or no key: `unsure`. Counted as `jev_failed` in the run summary |
| `Decider` to cache | `extraction` table | key = sha256 of the questions and the state; version = `<decision>-v<n>@<model>` | a cached answer is reused. A model change asks again |
| calibration to bands | report file, then a `profile.yaml` edit | bands carry the model ID they were measured on | a band for another model: `unsure` |
| harvest to run summary | `counts` | `jev_skip`, `jev_unsure`, `jev_failed`, `jev_would_skip` (shadow), `jev_gate_miss` (audit) | missing counts mean Jev did not run |

## Decisions

| # | Decision | Rejected, and why |
|---|---|---|
| 1 | Jev answers judgments. Facts stay with parsers and with the LLM's verified quotes | Extracting facts with Jev by chaining choices: TypeSafe calls it slow and poor ("Generation"), and the quote that `verify` grounds would be lost. Letting Jev decide tiers directly: the tier is policy in `judge.py` and `profile.yaml`, and stays replayable code |
| 2 | Each decision has two thresholds and a fallback to today's path, so only the confident region changes behaviour | One threshold at 0.5: an error on the costly side (a missed registration window) would cost as much as one on the cheap side (one extra call) |
| 3 | Our own client of one POST over httpx | `typesafe-sdk`: pre-1.0, three releases in five days, and a dependency tree for one endpoint. The router already talks to its providers this way. A gateway (LiteLLM or Vercel AI Gateway): another account and another failure mode, kept only as an access route if Q1 needs one |
| 4 | Offline calibration first, on the hosted LLM answers, then `shadow` for a few days, then `on` | Weeks of shadowing in production before any decision: the hosted cache already holds the reference answers, and one offline run yields the same agreement figures |
| 5 | Nouls per scope group and per link, not one Choice over many options | Choice favours its first option (jaggedness 8). Independent nouls have no order and stay literal |
| 6 | D2's state is the structured event plus passages of 300 characters that code finds around registration words (`regist`, not `registr`), not the whole page. Q2 compares both | The whole page: state with irrelevant detail costs Jev accuracy (jaggedness 5). Measured (Q3, 2026-10-19): the passages hold 95 of 96 quotes that grounded a registration field, at a median of 676 characters against 2,242 for the whole text |
| 7 | Audit sampler: when the harvest has LLM budget left after the queue, up to `decisions.audit` pages that D2 skipped are extracted anyway, picked at random. A verified registration field on one counts `jev_gate_miss` | No live check: calibration ages as pages and models change. A fixed share held back from D2 every day: it spends calls when budget is short. Leftover calls cost nothing |
| 8 | Each Jev request is one state. Candidates run concurrently, a few at a time | Every candidate packed into one state as an array: fewer requests, but a larger state costs accuracy, and each answer would need an index lookup |
| 9 | The no-Jev path stays: LLM triage, keywords, `review` | Deleting the LLM triage once Jev is on: then an outage or a missing key would change the board. Revisit after a month of `jev_failed = 0` |

## Expected

The figures below are estimates from the 2026-10-17 queue (ADR 0010). Q2 measures the real
ones.

| | Today | With D1 and D2 |
|---|---|---|
| Triage calls | 5 calls, 12 to 49 s each | 0 LLM calls; 89 Jev requests, a few seconds in total |
| Extraction calls | about 49 (30 AI-focused pages plus 19 found relevant) | about 25 to 30, if D2 skips structured pages at tum.de's rate (56 of 91 pages had no registration field) and Q4 supplies their judgments. Without Q4, D2's skips fall on in-scope pages, and 2 of 9 such skips moved an A event (Q6) |
| LLM step of the daily run | about 55 min at about 1 min per call | about 30 min |
| An added event (ADR 0014) with structured data and no registration details | one LLM call, or a day's wait when the budget is spent | done in the same run |
| A submitted organisation | about 1 min of profiling, then `review` when unsure | unchanged: every intake keeps its LLM profile (Q5, Alp 2026-10-19), so D4 saves nothing |
| Spend | 0 | input tokens only. 89 + 49 requests at most about 3k tokens each (a page's text is at most 12,000 characters, the median 2,242, Q3) is at most about 0.4 M tokens, about $0.02 a day before caching (UNVERIFIED until Q2) |

## Plan

| Step | Delivers | Check |
|---|---|---|
| 0 | Q1 settled: access route, key as an Actions secret, `daily_cap` | Alp's answer |
| 1 | `decide/` with a fake client, all modes `off` | tests written first: off gives today's output byte for byte in pytest and `replay`; a timeout, a 429 and a missing key give `unsure`; a band for another model is ignored |
| 2 | `event-radar calibrate`, run on the hosted cache (Q2, Q3) | report in the records repository: agreement, recall on the costly side at each band, German against English, latency, tokens |
| 3 | D1 `shadow`, then `on` | `jev_would_skip` against the LLM triage on the same days |
| 4 | D2 and the audit sampler, `shadow` then `on` | `jev_gate_miss` stays within the band Q2 set |
| 5 | D5 ordering of search hits | the admin's approvals per day, compared with the week before |
| 6 | D4, after Q5 | an intake fixture: confident yes reaches `finding`, unsure reaches `review` |
| 7 | D3, after Q4 | golden replay and a board diff shown to Alp before `on` |

## Not decided here

Facts extracted from candidates that code proposes and Jev picks (dates near a
registration word, `reg_url` among the page's anchors) could remove most of the remaining
calls, since the candidates are page text by construction. It waits for steps 2 to 4 (map:
fog).
