# ADR 0008: One adapter per platform

**Status:** DECIDED (Alp's go 2026-10-15), built on branch `platform-adapters` (2026-10-17);
the differential over the hosted pages is still to run (see "How it landed")
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
| 2 | All of one platform's knowledge lives in its module under `platforms/`: no host name of a registered platform appears in a string literal anywhere else in `src/` | A test that walks the AST of every other module and flags a literal holding a registered host or a host-like name the registry claims |
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
| `platforms/base.py` | the `Platform` base class, `host`, `canonical`, `segments` | the adapter contract changes |
| `platforms/__init__.py` | `PLATFORMS` and the functions the rest of the radar calls | the dispatch changes |
| `platforms/luma.py`, `meetup.py`, `eventbrite.py`, `pretix.py`, `forms.py` (Typeform, Tally, Google Forms, a class each) | everything one platform needs, including Luma's page parser | that platform changes |
| `parse/` | format readers and `parse()`, platform-blind | a format changes |

The adapter contract, a base class whose defaults answer None or []. Most of an adapter is
declarations; the base class turns them into answers:

```python
class Platform:
    name: str  # key prefix, "luma"
    hosts: tuple[str, ...] = ()  # no two platforms share one
    method: str | None = None  # observation method of page_events, "luma@2"
    event_url: re.Pattern[str] | None = None  # an event page's URL, its id in the groups
    not_events: frozenset[str] = frozenset()  # ids the pattern matches that are no event
    not_event_prefixes: tuple[str, ...] = ()
    feed_uid: re.Pattern[str] | None = None  # a feed entry's UID, its id in group 1
    organiser_path: str | None = None  # organiser pages sit one level deeper under it

    def claims(self, url: str) -> bool: ...  # on its hosts or their subdomains
    def event_key(self, url: str) -> str | None: ...  # "luma:8hqv1871"
    def uid_key(self, uid: str) -> str | None: ...  # "luma:evt-..."
    def organiser_source(self, url: str) -> str | None: ...  # the URL to track
    def organiser_domain(self, url: str) -> str: ...  # the registry key
    def listing_feed(self, tree) -> str | None: ...  # a listing page's own feed
    def page_events(self, tree) -> list[ParsedEvent]: ...  # an event page's events
    def topic_keys(self, tree) -> set[str]: ...  # keys of links naming topic pages
```

## Seams

| Seam | Carrier | Contract | Failure |
|---|---|---|---|
| Callers to registry | calls to `platforms` | `platform_key`, `is_ticket_host`, `uid_key`, `organiser_source` (was `intake.urls.platform_source`), `organiser_domain`, `listing_feed`, `page_events`, `topic_keys`; `platform_key` and `is_ticket_host` keep their names | None or [] means "no platform claims this", and the caller takes its generic path (`intake.urls.organiser_domain` keeps the one for own sites) |
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
| 7 | The base class turns declarations (`event_url`, `not_events`, `feed_uid`, `organiser_path`) into answers, so Meetup, pretix and the form services are declarations plus at most one method | Each adapter matching its own URLs: the same match, id and not-an-event logic seven times |
| 8 | The base class lives in `platforms/base.py`, and adapters import it from there | In `platforms/__init__.py`: an adapter would import the package that imports the adapters, and the import order would decide whether that works |
| 9 | Eventbrite claims `eventbrite.com` and its country domains (`eventbrite.de`, `eventbrite.co.uk`) by pattern; the other platforms claim their hosts and subdomains | Listing Eventbrite's country domains: a list to keep current, of domains the radar has not checked |
| 10 | Luma's category pages are recognised from the event page that links to them: its `__NEXT_DATA__` lists `categories` by slug, and their pages are `luma.com/<slug>` | A list of category slugs: it misses new categories. Recognising the category page itself: its page data is not yet seen (the golden pages hold only event pages) |

## Found while mapping

| Defect | Evidence | Plan |
|---|---|---|
| `is_ticket_host` matches substrings of the host, so every `docs.google.com` page (Docs, Sheets) counts as a ticket platform: TICKET class, no LLM | `platforms.py:67-69` | Fixed: a host or its subdomain; Google only under `/forms/` |
| `eventbrite.com/d/` in the aggregators never applies, because the ticket check runs first: Eventbrite's search pages would be TICKET and their registration fields trusted | `profile.py:49-53`, `profile.yaml:13`, `resolve.py:186` | Fixed: the aggregator check runs first |
| The intake's host checks disagree with the ticket list: `platform_source` matches hosts exactly, `is_ticket_host` by substring | `intake/urls.py:53,67-74` | Unified by the registry: an organiser source is asked only of the platform that claims the URL |
| `luma.com/climate`, `/fitness` and `/running` are tracked as event pages. An event page that yields no event asks for the LLM, ticket host or not | local `radar_dev` sources, 2026-10-15; `parse/__init__.py:81-82`. Found 2026-10-17: they were added as links of the Luma event pages `luma.com/5lh8doqn` and `luma.com/zvc9xqlk`, and every golden Luma event page links to its categories (`/ai`, `/tech`), named in its `__NEXT_DATA__` | Fixed for new links: an event page no longer follows its categories' pages (D10). The sources added before stay until their rows are deleted (no source is retired automatically); checking the hosted sources and LLM jobs first, then deleting with Alp's go |
| Found 2026-10-17: `organiser_domain` keys every Eventbrite organiser page (`/o/<name-id>`) as `eventbrite.de/o`, so the intake refuses a second Eventbrite organiser as "Already in the registry" | `intake/urls.py` `organiser_domain`, `intake/service.py` `_check` | Fixed: `organiser_path` per platform (Luma `user`, Eventbrite `o`) |
| Found 2026-10-17: a Luma page whose data holds `"event": null` or a field of another type raises in the parser; `jobs.harvest` catches only at the run level, so one such page ends the whole harvest | `parse/luma.py` `events` | Fixed: the page yields no event and goes to the LLM (invariant 6) |
| Found 2026-10-17: a link such as `http://[broken` makes `urljoin` raise in `html.anchors`, `image_url` and `text`, so one such link on any page ends the whole harvest | `html.py` | Fixed: the link is skipped |

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

## How it landed

Built 2026-10-17. The differential ran over the golden pages (20), the test fixtures (4),
every URL in the local `radar_dev` (288: sources, observations, their links, organiser
domains), every link on those pages, and 57 edge cases (an ICS feed with Luma, Meetup and
other UIDs, a Luma calendar page, broken page data, foreign, malformed and look-alike
URLs): 7,206 answers over 1,172 URLs. Each commit was compared with the one before.

| Commit | Change | Differential |
|---|---|---|
| 334e5e3 | `parse/html.py` to `html.py` | none |
| 0b2050d | the registry and the adapters | none, but for the malformed `https://[::1`: `is_ticket_host`, `source_class` and the organiser source raised `ValueError` and now answer False, OWN, None |
| 1eec2f5 | invariant tests 2-4, each seen failing on a planted violation | tests only |
| f1d1858 | Luma data of another shape yields nothing (invariant 6) | none |
| 9a6daf1 | hosts and subdomains, Google only under `/forms/` | 6 URLs TICKET to OWN: a Google Docs link on a stored page and 5 edge cases (Docs, Sheets, `notluma.com`, `luma.com.evil.org`, `xmeetup.com`) |
| bc26085 | aggregators before ticket hosts | 1 edge case, `eventbrite.com/d/...`, TICKET to AGG |
| 5ed6ed2 | `organiser_path` | 3 edge cases: two Eventbrite `/o/` pages keep their own keys; `meetup.com/user/abc` keys as `meetup.com/user` |
| 54d24a7 | category pages not followed | only the fixture, which now has a category link: its follow loses `luma.com/climate`. The golden Luma pages name `ai` and `tech`, too short to look like events |
| 7c8df78 | malformed links skipped | none |

`event-radar replay` without the LLM gives a byte-identical report on 431a60d and the branch
(a_recall 0.636, 7 of 11 A, as before). Not run: the replay with the LLM (no cached answers
are left locally) and the differential over the hosted database's latest body per URL
(Claude Code's auto mode refused the read-only export; Alp runs it). Tests: 88 pytest (62
before), 14 node, ruff clean.

## Open

- PR A adds `page_of(entry)` to the contract (a feed entry's event page), so feeds can
  follow their entries' pages.
- `harvester._follow_kind` still knows feed URL shapes (`/ics`, `/ical/`) outside the
  adapters; PR A touches feeds and can move it.
- Eventbrite organiser keys keep the country domain, so one organiser seen on
  `eventbrite.de` and `eventbrite.com` would key twice.
