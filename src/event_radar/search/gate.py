"""What a search hit becomes (ADR 0016): an event or an organisation candidate, or the reason
it becomes neither. Nothing here fetches a page."""

from dataclasses import dataclass
from urllib.parse import urlsplit

from ..intake.urls import organiser_domain
from ..judge import Judge
from ..model import IntakeKind
from ..platforms import canonical, host, is_ticket_host, organiser_source, platform_key
from ..profile import Profile


@dataclass(frozen=True)
class Hit:
    url: str
    title: str
    snippet: str
    score: float


@dataclass(frozen=True)
class Candidate:
    kind: IntakeKind
    url: str  # the event's page, the organisation's homepage, or its page on a platform
    events_url: str | None
    site: str  # the organiser registry's key for `url`
    aliases: frozenset[str]  # the keys a stored event holds for this page
    query: str
    title: str
    score: float


@dataclass(frozen=True)
class Known:
    """What the radar knows: organiser keys (an organiser's or an organisation intake's),
    pages (sources and event intakes, canonical), and the hosts the keys sit on."""

    sites: frozenset[str]
    pages: frozenset[str]

    @property
    def hosts(self) -> frozenset[str]:
        return frozenset(s.split("/")[0] for s in self.sites)


def candidate(hit: Hit, query: str, profile: Profile, judge: Judge) -> Candidate | str:
    """The candidate `hit` makes, or why it makes none."""
    parts = urlsplit(hit.url)
    name = host(hit.url)
    if parts.scheme not in ("http", "https") or not name:
        return "not_a_web_page"
    if any(name == d or name.endswith("." + d) for d in profile.search.exclude_domains):
        return "excluded_site"
    if any(a in hit.url for a in profile.aggregators):
        return "aggregator"
    text = f"{hit.title} {hit.snippet}"
    if not judge.names_scope(text):
        return "off_topic"
    if judge.audience({"title": {"value": hit.title}})[0]:
        return "audience"
    if not any(city in f"{text} {hit.url}".lower() for city in profile.cities):
        return "not_in_munich"
    found = (query, hit.title, hit.score)
    if key := platform_key(hit.url):
        aliases = frozenset({key, "url:" + canonical(hit.url)})
        return Candidate(
            IntakeKind.EVENT, hit.url, None, organiser_domain(hit.url), aliases, *found
        )
    if not is_ticket_host(hit.url):
        page = f"{parts.scheme}://{parts.hostname}/"
    elif organiser_source(hit.url) is not None:
        page = hit.url
    else:
        return "platform_page"
    events_url = None if canonical(page) == canonical(hit.url) else hit.url
    return Candidate(
        IntakeKind.ORGANISATION, page, events_url, organiser_domain(page), frozenset(), *found
    )


def known_reason(found: Candidate, known: Known, stored_event: bool) -> str | None:
    """Why the radar already knows `found`, or None. `stored_event`: an event holds one of
    its aliases. A homepage's host counts as known when any organiser key sits on it."""
    if found.kind == IntakeKind.EVENT:
        if canonical(found.url) in known.pages:
            return "known_page"
        return "known_event" if stored_event else None
    if found.site in known.sites or ("/" not in found.site and found.site in known.hosts):
        return "known_site"
    if found.events_url and canonical(found.events_url) in known.pages:
        return "known_page"
    return None
