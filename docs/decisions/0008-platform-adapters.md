# ADR 0008: One adapter per platform

**Status:** DECIDED (Alp's go 2026-10-15), building on branch `platform-adapters`
**Date:** 2026-10-15
**Inputs:** Alp, 2026-10-15: "start with b" (the adapter refactor before the calendar-feed
fix). The feed-parsing findings of 2026-10-15: events known only from a Luma calendar feed
never get their event page (AAIF Community Munich #29), and the fix needs Luma knowledge
in a format reader. Alp's first idea was a dictionary from organiser URL to a parse
function (rejected below).

## What this is for

Supporting a new event platform is one new module and one registry entry. Supporting a
new organiser is data and needs no code, whether it is on a known platform or on its own
site (schema.org JSON-LD first, then the LLM extractor). This change moves code only:
every stored page and URL gives the same result before and after it.

Most events come from organisers' own websites, not from platforms: of the 174 sources in
the local copy on 2026-10-15, 141 are OWN, 3 AGG and 30 TICKET (Luma 16, Eventbrite 5,
forms 5, Meetup 2, pretix 2). Own sites take the generic path, which this change leaves as
it is. The adapters hold only what the seven platforms need.

## Invariants

| # | Invariant | Enforced by |
|---|---|---|
| 1 | Parsing rules are code keyed by platform, never by organiser | Review; no organiser name or domain in `src/` |
| 2 | All of one platform's knowledge lives in its module under `platforms/`: no host name of a registered platform appears in a string literal anywhere else in `src/` | A test that reads the hosts from the registry and walks the AST of every other module |
| 3 | Format readers (ICS, RSS, JSON-LD, HTML links, LLM) know no platform; they ask the registry | Invariant 2's test, plus `parse/` importing only the registry's functions |
| 4 | Dependencies point one way: `html` <- `platforms` <- `parse`, `extract`, `intake`, `profile`, `harvester`. `platforms` imports neither `parse` nor `intake` | Import order cannot break it; ruff and the tests import every module |
| 5 | Stored values do not change: platform keys (`luma:evt-...`), method strings (`luma@2`, `jsonld@2`, `ics@1`) and source classes are byte-identical | The differential check below |
| 6 | An adapter never raises on a foreign or malformed page or URL; it answers None or [] | A parametrised test feeding every registered platform an empty page, a foreign URL and broken JSON |

## Where platform knowledge lives today

| Knowledge | Today | Platforms |
|---|---|---|
| Event URL to key, non-event slugs and prefixes | `platforms.py` `_PATTERNS`, `_NOT_EVENTS`, `_NOT_EVENT_PREFIXES` | all 7 |
| Ticket hosts (TICKET class, page URL is `reg_url`, no LLM) | `platforms.py` `TICKET_HOSTS`, read by `profile`, `parse`, `extract`, `intake/urls` | all 7 |
| Event page parser (`__NEXT_DATA__`) | `parse/luma.py` `events` | Luma |
| A listing page's own feed | `parse/luma.py` `calendar_feed`, read by `parse` and `intake/discovery` | Luma |
| Feed UID to key | `parse/ics.py` `_UID_KEYS` | Luma, Meetup |
| Organiser page to the source to track | `intake/urls.py` `platform_source` | Luma, Meetup, pretix, Eventbrite |
| Organiser registry key | `intake/urls.py` `organiser_domain` (`lu.ma` alias, `user/` depth) | Luma, all ticket hosts |

## Layout

| Module | Holds | Changes when |
|---|---|---|
| `html.py` (was `parse/html.py`, moved unchanged) | anchors, links, images, page text | HTML reading changes |
| `platforms/__init__.py` | the `Platform` base class, `PLATFORMS`, the dispatch functions, `host` and `canonical` | the adapter contract changes |
| `platforms/luma.py`, `meetup.py`, `eventbrite.py`, `pretix.py`, `forms.py` (Typeform, Tally, Google Forms) | everything one platform needs, including Luma's page parser | that platform changes |
| `parse/` | format readers and `parse()`, platform-blind | a format changes |

The adapter contract, a base class whose defaults answer None or []:

```python
class Platform:
    name: str                       # key prefix, "luma"
    hosts: tuple[str, ...]          # claimed hosts; no two platforms share one
    method: str | None = None       # observation method of page_events, "luma@2"

    def event_key(self, url: str) -> str | None: ...           # "luma:8hqv1871"
    def uid_key(self, uid: str) -> str | None: ...             # a feed entry's UID
    def organiser_source(self, url: str) -> str | None: ...    # an organiser link, as the URL to track
    def organiser_domain(self, url: str) -> str | None: ...    # the registry key
    def listing_feed(self, tree) -> str | None: ...            # a listing page's own feed
    def page_events(self, tree) -> list[ParsedEvent]: ...      # an event page's events
```

## Seams

| Seam | Carrier | Contract | Failure |
|---|---|---|---|
| Callers to registry | calls to `platforms` | `platform_key`, `is_ticket_host`, `uid_key`, `organiser_source`, `organiser_domain`, `listing_feed`, `page_events`; the names callers use today stay | None or [] means "no platform claims this", and the caller takes its generic path |
| Registry to adapter | `Platform` methods | URL methods dispatch on the claimed host; page methods ask each adapter in `PLATFORMS` order and the first answer wins | An adapter that answers wrongly affects only its platform's URLs and pages |
| Stored keys | `observation.platform_keys`, `method` | `<name>:<id>` and `<name>@<version>` as today | A changed key splits an event in the resolver, so invariant 5 has a check |

## Decisions

| # | Decision | Rejected, and why |
|---|---|---|
| 1 | One adapter class per platform behind a registry; a new platform is a module plus one entry in `PLATFORMS`, and no dispatch code changes | A dictionary from organiser URL to a parse function (Alp's first idea): it grows with every suggested organiser, so data changes need code changes. Grouping today's functions per platform module without a registry: every caller would still branch on platform names |
| 2 | Rules stay code | Rules in YAML: they grow the same way and cannot be unit-tested like code. Selectors the LLM learns once per site and stores: they break silently when a site changes and would need the verifier's checks again. LLM extraction is already cached by content hash. Revisit only if the 35 daily LLM calls become the bottleneck (the run summary's deferred count shows it) |
| 3 | Page parsers recognise their platform by the page, not the URL, as `luma.events` does today | Dispatch by host: a Luma page arrives under other URLs (CDTM's `/events` renders to its Luma page, ADR 0003) |
| 4 | A base class with defaults | A `typing.Protocol`: most platforms know one to three of the six answers (the form hosts only `event_key`), so each would carry stubs |
| 5 | `platforms/` sits below `parse/`, and the HTML helpers move to `event_radar/html.py` so that Luma's parser can use them | Adapters inside `parse/`: intake and the profile use platform rules that are not parsing. Leaving `parse/html.py` in place: `platforms` would import `parse`, which imports `platforms`, and the import order would decide whether that fails |
| 6 | The move is strictly behaviour-preserving. The defects found while mapping are fixed afterwards in this PR, one commit and one test each, with their measured diff | Fixing them during the move: the differential check could no longer tell a slip from a fix |

## Found while mapping

| Defect | Evidence | Plan |
|---|---|---|
| `is_ticket_host` matches substrings of the host, so every `docs.google.com` page (Docs, Sheets) counts as a ticket platform: TICKET class, no LLM | `platforms.py:67-69` | This PR: a host or its subdomain; Google only under `/forms/` |
| `eventbrite.com/d/` in the aggregators never applies, because the ticket check runs first: Eventbrite's search pages would be TICKET and their registration fields trusted | `profile.py:49-53`, `profile.yaml:13`, `resolve.py:186` | This PR: the aggregator check runs first |
| The intake's host checks disagree with the ticket list: `platform_source` matches hosts exactly, `is_ticket_host` by substring | `intake/urls.py:53,67-74` | Unified by the registry |
| `luma.com/climate`, `/fitness` and `/running` are tracked as event pages; whether they are Luma's topic pages is UNVERIFIED. An event page that yields no event asks for the LLM, ticket host or not | local `radar_dev` sources, 2026-10-15; `parse/__init__.py:81-82` | This PR (Alp, 2026-10-15: these pages need no tracking). Check the hosted sources and LLM jobs first; prefer recognising a non-event Luma page by its `__NEXT_DATA__` over a list of slugs |

Event relevance (which events reach which tier) is out of scope; Alp plans a separate PR
on irrelevant events.

## Check

- Differential: a scratch script runs `main` and this branch over every stored page body
  and every source URL (`parse`, `platform_key`, `is_ticket_host`, `source_class`,
  `organiser_domain`, `platform_source`, the follow kind) and compares. The corpus is the
  hosted database's latest body per URL plus the golden pages, kept outside both
  repositories and deleted afterwards. The move commits must show no difference; each fix
  commit shows only its own.
- `event-radar replay` gives the same report on `main` and on this branch.
- pytest, the node tests and ruff pass.

## Open

- PR A adds `page_of(entry)` to the contract (a feed entry's event page), so feeds can
  follow their entries' pages.
