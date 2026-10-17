"""Finding an organisation's event pages from its homepage (ADR 0003 D2-D3)."""

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser

from .. import html
from ..fetch import Fetcher
from ..model import Kind
from ..parse import luma, parse
from ..platforms import canonical, platform_key
from ..render import Renderer
from ..store import Store
from .urls import platform_source

SHELL_TEXT = 300
SHELL_LINKS = 5
_WORD = re.compile(r"[\wäöüß]+")
_FEED_TYPES = ("calendar", "rss", "atom")


@dataclass
class Candidate:
    """A page that may list the organisation's events, and why it was considered."""

    url: str
    why: str
    kind: Kind = Kind.LISTING
    browser: bool = False
    events: int = 0
    links: int = 0


def matches(text: str, signals: tuple[str, ...]) -> bool:
    """`text` holds a signal: a phrase anywhere, or a single word as a whole word."""
    words = set(_WORD.findall(text.lower()))
    lowered = f" {text.lower()} "
    return any(f" {s} " in lowered if " " in s else s in words for s in signals)


def declared_feeds(tree: LexborHTMLParser, base: str) -> list[str]:
    feeds = []
    for link in tree.css("link[rel~='alternate'][href]"):
        kind = (link.attributes.get("type") or "").lower()
        if any(t in kind for t in _FEED_TYPES):
            feeds.append(urljoin(base, link.attributes["href"]))
    feeds += [url for url in html.links(tree, base) if urlsplit(url).path.endswith(".ics")]
    if feed := luma.calendar_feed(tree):
        feeds.append(feed)
    return feeds


def scan_links(tree: LexborHTMLParser, base: str, signals: tuple[str, ...]) -> list[str]:
    """Anchors that carry an event signal in their text or path, and platform pages."""
    home = urlsplit(base).hostname
    found = []
    for url, label in html.anchors(tree, base):
        if platform := platform_source(url):
            found.append(platform)
            continue
        parts = urlsplit(url)
        if parts.hostname != home or url.rstrip("/") == base.rstrip("/"):
            continue
        segments = [s.lower() for s in parts.path.split("/") if s]
        if matches(label, signals) or any(s in signals for s in segments):
            found.append(url)
    return list(dict.fromkeys(found))


def is_shell(body: bytes, url: str) -> bool:
    """Plain HTML that only renders its content with JavaScript."""
    tree = LexborHTMLParser(body)
    return len(html.text(body, url)) < SHELL_TEXT or len(html.anchors(tree, url)) < SHELL_LINKS


def page_title(tree: LexborHTMLParser) -> str | None:
    """The page title without its site suffix (`Club | Munich` is `Club`)."""
    node = tree.css_first("title")
    text = node.text(strip=True) if node else ""
    return re.split(r"\s+[|\-–]\s+", text)[0] or None


def candidates(
    home: str,
    body: bytes,
    events_url: str | None,
    llm_links: list[str],
    signals: tuple[str, ...],
) -> list[Candidate]:
    """Every page worth probing, most trusted first, each URL once."""
    tree = LexborHTMLParser(body)
    found: list[Candidate] = []
    if events_url:
        found.append(Candidate(events_url, "the events page you gave"))
    found += [Candidate(f, "a feed the homepage declares") for f in declared_feeds(tree, home)]
    if parse(home, "text/html", body, Kind.LISTING).events:
        found.append(Candidate(home, "events listed on the homepage"))
    found += [
        Candidate(u, "an events link on the homepage") for u in scan_links(tree, home, signals)
    ]
    found += [Candidate(u, "named by the LLM") for u in llm_links]
    if not found:
        parts = urlsplit(home)
        found.append(Candidate(f"{parts.scheme}://{parts.netloc}/events", "a guess"))
    unique: dict[str, Candidate] = {}
    for candidate in found:
        unique.setdefault(canonical(candidate.url), candidate)
    return list(unique.values())


def probe(
    candidate: Candidate, store: Store, fetcher: Fetcher, renderer: Renderer | None
) -> Candidate | None:
    """The candidate as a source when it yields events or event links, rendering a
    JavaScript shell first; None when it yields nothing."""
    fetched = fetcher.get(candidate.url)
    if not fetched.ok:
        return None
    body = store.pages.body(fetched.content_hash)
    result = parse(candidate.url, fetched.content_type, body, Kind.LISTING)
    url, browser = candidate.url, False
    useful = result.events or result.follow or result.method == "ics@1"
    if (
        not useful
        and renderer
        and "html" in (fetched.content_type or "")
        and is_shell(body, candidate.url)
    ):
        rendered = renderer.fetch(candidate.url)
        if rendered.ok:
            url, browser = rendered.final_url or candidate.url, True
            body = store.pages.body(rendered.content_hash)
            result = parse(url, "text/html", body, Kind.LISTING)
            useful = result.events or result.follow
    if not useful:
        return None
    kind = Kind.FEED if result.method in {"ics@1", "rss@1"} else Kind.LISTING
    if platform_key(url) and len(result.events) == 1 and not result.follow:
        kind = Kind.EVENT_PAGE
    return Candidate(url, candidate.why, kind, browser, len(result.events), len(result.follow))
