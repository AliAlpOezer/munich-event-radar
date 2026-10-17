import re
from dataclasses import replace
from urllib.parse import urlsplit

from selectolax.lexbor import LexborHTMLParser

from .. import html
from ..model import Evidence, Kind, ParsedEvent, ParseResult
from ..platforms import is_ticket_host, listing_feed, page_events, platform_key
from . import ics, jsonld

_RSS_LINK = re.compile(r"<item\b.*?<link>\s*(?:<!\[CDATA\[)?\s*(\S+?)\s*(?:\]\]>)?\s*</link>", re.S)
_DETAIL_PATH = re.compile(r"/(events?|veranstaltung(en)?|termine?|details?)/", re.I)
_REGISTRATION = ("reg_mode", "reg_url", "reg_availability")


def _is_ics(content_type: str, body: bytes) -> bool:
    return "calendar" in content_type or body.lstrip()[:15] == b"BEGIN:VCALENDAR"


def _is_rss(content_type: str, body: bytes) -> bool:
    head = body.lstrip()[:400].lower()
    return "rss" in content_type or b"<rss" in head or b"<feed" in head


def _detail_links(page_url: str, links: list[str]) -> list[str]:
    host = urlsplit(page_url).netloc
    return [
        link
        for link in links
        if (
            urlsplit(link).netloc == host
            and _DETAIL_PATH.search(urlsplit(link).path)
            and link.rstrip("/") != page_url.rstrip("/")
        )
        or platform_key(link)
    ]


def _single(
    page_url: str, event: ParsedEvent, ticket_links: list[str], image: str | None
) -> ParsedEvent:
    keys = set(event.platform_keys)
    if own := platform_key(page_url):
        keys.add(own)
    fields = dict(event.fields)
    if is_ticket_host(page_url):
        fields.setdefault("reg_url", Evidence(page_url))
    else:
        linked = {platform_key(link) for link in ticket_links} - keys
        if len(linked) == 1:
            keys |= linked
    fields.setdefault("event_url", Evidence(page_url))
    if image:
        fields.setdefault("image", Evidence(image))
    return replace(
        event, platform_keys=tuple(sorted(keys)), links=tuple(ticket_links), fields=fields
    )


def parse(url: str, content_type: str | None, body: bytes, kind: Kind) -> ParseResult:
    content_type = (content_type or "").lower()
    if _is_ics(content_type, body):
        return ParseResult("ics@1", tuple(ics.events(body)))
    if _is_rss(content_type, body):
        return ParseResult(
            "rss@1", follow=tuple(dict.fromkeys(_RSS_LINK.findall(body.decode(errors="replace"))))
        )
    tree = LexborHTMLParser(body)
    method, found = page_events(tree)
    structured = jsonld.events(tree, url)
    if not found:
        found, method = structured, "jsonld@2"
    elif len(found) == 1 and len(structured) == 1:
        found = [replace(found[0], fields={**structured[0].fields, **found[0].fields})]
    page_links = html.links(tree, url)
    ticket_links = [link for link in page_links if platform_key(link)]
    if kind is not Kind.EVENT_PAGE:
        feed = listing_feed(tree)
        follow = [feed] if feed else _detail_links(url, page_links)
        return ParseResult(method, tuple(found), follow=tuple(follow))
    if len(found) != 1:
        return ParseResult(method, tuple(found), follow=tuple(ticket_links), needs_llm=True)
    event = _single(url, found[0], ticket_links, html.og_image(tree, url))
    lacks_registration = not any(f in event.fields for f in _REGISTRATION)
    return ParseResult(
        method,
        (event,),
        follow=tuple(ticket_links),
        needs_llm=lacks_registration and not is_ticket_host(url),
    )
