# ADR 0021: The board ranks events by the people worth meeting there

**Status:** DECIDED, building on branch `network-value`
**Date:** 2026-11-08
**Inputs:** Alp, 2026-11-06, on CDTM's events missing from the board: "ai events following
is there to build network, community, not only ai events". Alp's go for this ADR the same
day ("yeah, start with that") on the plan in the records repository
(`docs/context/ranking-quality.md`, last section): a curated organiser standing, community
formats raised, AI one topic among startup, career and builder, no silent drop of a tracked
organiser's event, and the two ranking fixes of the 2026-11-03 health check (a conference
with AI as one theme is not A; a news page is not an event). Amends ADR 0002 D6 (tiers),
ADR 0011 D4 (startup organisers) and ADR 0018 (the judgment and how the judge reads it).

## What this is for

The radar exists to meet people: the contacts and roles that turn a TUM degree into offers.
An event ranks by who will be there and whether meeting them is the point, with AI one
topic among startups, careers and building software. The communities the board exists for
never fall below B, and an event of an organiser someone chose to follow is never dropped
for its topic.

## What was measured

All read-only; the comparison is in the records repository,
`research/2026-11-06-network-gap.md`.

| Finding | Evidence |
|---|---|
| Of about 20 networking events in Munich in the five weeks from 2026-11-06, 4 were on the board, all C | own search against anon `api.events`, 2026-11-06 |
| `Judge` tiers by AI relevance times format: an event not about AI is C at best, whoever runs it, so every CDTM, START and UnternehmerTUM event sat in C | `judge._by_rules`: "not AI-relevant", "AI relevance unknown, organiser not AI-focused" |
| A conference or programme with AI as one theme is A: "Awareness week: Mental health in the digital age", "Munich Vascular Conference", "Patientensymposium: Herz aus dem Takt" (AHA Bayern), W-JAX (paid) | hosted board 2026-11-08, `network_dump.py` (session scratchpad) with the current judge |
| News pages are judged as events: "TUM opens Convergence Center and TUM RoboGym" A as `demo_day`, "Federal Minister of Finance Lars Klingbeil visits TUM" B | same |
| A judgment with no topic drops a tracked organiser's event unstored: 5 of 150 off-topic groups on 2026-11-08 (4 START Munich, 1 CDTM), 145 from the general calendars TUM central (90), AHA Bayern (40) and BAdW (17) | same |
| CDTM is an intake organiser (`discovered_via = manual`, kind `industry`, `ai_focus` false), so no seed edit reaches its flags | hosted `organiser`, 2026-11-08 |

## Invariants

| # | Invariant | Enforced by |
|---|---|---|
| 1 | A model states what an event is about and whether it is one; code decides scope and tier (ADR 0018 invariant 1) | `Judge.judge` reads the judgment as one input |
| 2 | An organiser's standing is curated by a person, in `seed/organisers.yaml`; no model and no intake raises it | the seed owns `standing`; intake rows keep the default |
| 3 | An event of an organiser of standing `community` or `anchor` is never off topic; only a general calendar's events need a topic | `Judge._by_rules` |
| 4 | An anchor's event in Munich, upcoming and for adults, is at least B | the anchor floor, after the tier rules, like stars (ADR 0015) |
| 5 | Every drop names its reason; a page judged not to be an event is stored as a drop, not deleted, so a wrong judgment is visible | `not_event` is not in `NOT_STORED` |
| 6 | Without an LLM the same rules run on words (ADR 0018 invariant 7) | `Judge.topic` and `Judge.networking` fall back to keywords and kinds |

## Organiser standing

| Standing | Meaning | Organisers (seed, 2026-11-08) |
|---|---|---|
| `anchor` | A community the board exists for (`radar/clubs.md` Tier 1 and the joint institutions). Every event in scope and at least B; a networking event of theirs is A whatever its topic | CDTM, TUM.ai, START Munich, UnternehmerTUM, MCML |
| `community` | Runs its own events and was chosen by a person. Every event in scope | the other seed organisers that run their own events |
| `calendar` | Lists many organisers' events, or nobody curated it yet. Its events need a topic | TUM central events, BAdW, AI-HUB@LMU, Munich Startup, baiosphere; every intake organiser until the seed names it |

## The judgment, `judgments-v4`

Two answers join ADR 0018's six:

| Field | Values |
|---|---|
| `event` | false only when the text is clearly not something people can attend: a news article, a press release, a report on something past, a page about an organisation, place or service |
| `networking` | true when meeting the other attendees or the hosts is a main part of it: a mixer, Stammtisch, open day, info session, community meetup, career fair, hackathon, pitch night, a talk with networking after; false for a lecture, ceremony, course or exhibition where attendees mostly listen |

The version change asks every candidate again. Until a candidate's v4 answer exists, its v3
answer holds (ADR 0018 invariant 4) and is read with `event` true and `networking` from its
kind and title.

## How the judge reads it

| Step | Rule |
|---|---|
| Topic | strong: AI `core`, `startup` or `career`; AI unknown at an AI-focused organiser. Weak: AI `some`, `tech`. Without a judgment: an AI keyword or a startup or career scope word is strong, a developer word weak |
| Networking | the judgment's `networking`; without one, a kind of `profile.yaml` `networking.kinds` or a title word of `networking.words` |
| 1. Audience | an audience other than adults drops it, unstored (ADR 0012) |
| 2. Off topic | at a `calendar` organiser, no topic and no `any_topic_kinds` kind drops it, unstored (ADR 0011) |
| 3. Not an event | the judgment's `event` false drops it as `not_event`, stored |
| 4. Facts | past, online only, outside Munich (ADR 0011 D8) |
| 5. Paid | C |
| 6. A | a career fair or hackathon (`any_topic_kinds`); a strong topic with an `a_kinds` kind or networking; an anchor's networking event |
| 7. B | a strong topic; a weak topic at a `community` or `anchor` organiser |
| 8. C | the rest: a weak topic at a calendar, no topic at a curated organiser |
| 9. Floors | an anchor's event is at least B; stars lift (ADR 0015) |

## Decisions

| # | Decision | Rejected, and why |
|---|---|---|
| 1 | Rank by topic strength, networking and organiser standing, as ordered rules whose deciding rule is the last tier reason | A points score per axis: the thresholds trade one axis against another invisibly, and a reason list of points does not say why. Keeping AI relevance as the axis: the measured gap |
| 2 | AI `some` and `tech` are weak topics: B at a curated organiser, C at a calendar. This is health check fix 1 in general form: a conference with AI as one theme at a general calendar is C | A rule for conferences only: the same error came as a programme (Awareness week) and a symposium. Tech as strong: TUM central's engineering talks would fill B |
| 3 | Standing is one column, `organiser.standing`, default `calendar`, owned by the seed for every organiser the seed names by domain and name, also an intake's | Three booleans: anchor implies community. The intake profile deciding it: a model would decide trust (invariant 2). Default `community`: AHA Bayern's 40 off-topic events (concerts, children's labs) would be stored as C |
| 4 | `startup_focus` (organiser kind `startup`, ADR 0011 D4) goes: UnternehmerTUM is an anchor, Munich Startup a calendar whose events the judgment calls startup | Keeping it beside standing: two flags that put events in scope, and Munich Startup's listing items are other organisers' events |
| 5 | `ai_focus` stays: it says what an organiser's thin texts are about, not how much the radar trusts it | Folding it into standing: MCML is an anchor and AI-focused, Munich NLP a community and AI-focused |
| 6 | `networking` and `event` are judgment answers, read from the page as a person would | Title words only: "Climate Coffee" and "Homecoming" name no format, and a news page names no format either. A new kind: one kind per event cannot be both a talk and networking |
| 7 | A news page is a stored drop, `not_event`, labelled "Not an event" on the page | Unstored, like off topic: a false judgment would delete an event silently, against invariant 5 |
| 8 | An anchor's networking event is A whatever its topic | B only: CDTM's Open Day and MCML's Stammtisch are the board's purpose |
| 9 | Off-topic and audience drops stay unstored (ADR 0011 D3, ADR 0012); the harvest log lists each, title and organiser, so a drop is visible to the maintainer | Storing them behind a toggle: ADR 0011 D3's reason holds, every visitor would download 150 rows of concerts and lab tours |

## Check

- Tests first, failing before the change: CDTM's Open Day is A; a CDTM talk judged with no
  topic is B; a calendar's conference with AI `some` is C; a news page is `not_event` and
  stored; START Munich's event judged with no topic is not off topic; a calendar's event
  with no topic is.
- The hosted board, read-only, through `network_dump.py` with this branch's judge and the
  v3 judgments, compared with `main`'s verdicts.
- After the first harvest with `judgments-v4`: the board again, and the count of
  `not_event` drops read by hand.

## How it landed (2026-11-08)

Dry run, read-only, over the hosted observations and their `judgments-v3` answers with the
stored events' stars (`network_dump.py` in the session scratchpad, standing from this
branch's seed): 30 of 389 groups move, none leaves or joins.

| | A | B | C | off topic | past |
|---|---|---|---|---|---|
| `event-splits` (bd77fc3) | 23 | 13 | 25 | 150 | 146 |
| this branch | 22 | 29 | 10 | 145 | 151 |

| Move | Events |
|---|---|
| A to C, AI as a side theme on a general calendar | Awareness week (mental health), Munich Vascular Conference, Patientensymposium: Herz aus dem Takt, W-JAX 2026, bidt Digital Transformation Research Conference |
| C to A | CDTM Open Day (career, Open Day), Women Startup Pitch Night & Award Ceremony and Women Startup Award Pitch Night (UnternehmerTUM), Mobility Experts Stammtisch (UnternehmerTUM) |
| C to B, a strong topic | CDTM International Stories, CDTM Climate Coffee, Opportunity Hunters Talks (12.11), Industrial Edge Workshops for Startups, TUM IDEAward, Women in STEM Round Table x G+D, TUM Alumni Homecoming, Welcome@TUM |
| C to B, the anchor floor only | MCML Stammtisch, Road to START Summit (paid), Opportunity Hunters Talks (19.11, 26.11, not yet judged), and four UnternehmerTUM events with no topic: "How reel is it? The Substance", "Inspiration & Talk: Was Olympia für München in Gang setzen kann", CIRCULAR REPUBLIC Textile Forum, "Start and Spread Winter 2024" |
| off topic to past, now stored | 4 START Munich events, 1 CDTM |

The news pages ("TUM opens Convergence Center", "Lars Klingbeil visits TUM") keep their
tiers until their `judgments-v4` answers say `event` false; the dry run has no LLM key.
Networking from the judgment is not measured either: with v3 answers only titles name it,
so the A count after the first v4 harvest is expected higher.

Golden replay without the LLM, against local `radar_test`: A recall 0.636 (7 of 11), every
golden event's tier or drop the same as on `event-splits`.

Not built here: seeding the organisers the radar misses. Checked 2026-11-08 by parsing each
live page as this branch would: BayStartUP's `/termine` yields 75 detail links across
Bavaria, a large share of a harvest that used 47 of its 60 minutes on 2026-11-02; WERK1's
`/events` and LMU IEC's home page yield 6 and 3; AI Tinkerers Munich links its events as
`/p/<slug>`, which no listing rule follows; SCE's events page redirects to its Eventbrite
organiser page, whose listing would follow Eventbrite's `/d/` city pages. They belong with
the listing readers, measured against the harvest budget.

Open for Alp: UnternehmerTUM as `anchor` (the plan of 2026-11-06) puts its four events
with no topic at B; as `community` they would be C, and its startup events would keep B
and A by their topic.
