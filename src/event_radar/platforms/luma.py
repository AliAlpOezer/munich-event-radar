"""Luma (luma.com, formerly lu.ma): a page carries its data in `__NEXT_DATA__`, and says
whether it is an event, a calendar or a category; an event page holds its registration
state, a calendar page names the calendar's ICS feed."""

import json
import re

from selectolax.lexbor import LexborHTMLParser

from ..html import image_url
from ..model import Evidence, ParsedEvent
from .base import Platform, host, segments


def _initial_data(tree: LexborHTMLParser) -> object:
    script = tree.css_first("script#__NEXT_DATA__")
    if script is None:
        return None
    try:
        return json.loads(script.text())["props"]["pageProps"]["initialData"]
    except (ValueError, KeyError, TypeError):
        return None


def _next_data(tree: LexborHTMLParser) -> object:
    try:
        return _initial_data(tree)["data"]
    except (KeyError, TypeError):
        return None


def _availability(data: dict, ticket: dict, event: dict) -> str:
    if ticket.get("is_sold_out"):
        return "waitlist" if event.get("waitlist_status") == "enabled" else "sold_out"
    return {"open": "open", "not_open_yet": "not_open", "closed": "closed"}.get(
        data.get("registration_availability"), "unknown"
    )


def _text(node: dict) -> str:
    if node.get("type") == "text":
        return node.get("text") or ""
    if node.get("type") == "hard_break":
        return "\n"
    text = "".join(_text(n) for n in node.get("content") or [] if isinstance(n, dict))
    return text + "\n" if node.get("type") in {"paragraph", "heading", "code_block"} else text


def _description(data: dict) -> str | None:
    """The event's description, which Luma keeps as a ProseMirror document: a line per
    paragraph or line break."""
    doc = data.get("description_mirror")
    lines = (line.strip() for line in _text(doc).splitlines()) if isinstance(doc, dict) else ()
    return "\n".join(line for line in lines if line) or None


def _closes_at(data: dict) -> str | None:
    ends = [t.get("valid_end_at") for t in data.get("ticket_types") or [] if not t.get("is_hidden")]
    ends = [e for e in ends if e]
    return max(ends) if ends else None


def _event(data: dict, event: dict) -> ParsedEvent:
    ticket = data.get("ticket_info") or {}
    geo = event.get("geo_address_info") or {}
    url = f"https://luma.com/{event['url']}" if event.get("url") else None
    values = {
        "title": event.get("name"),
        "description": _description(data),
        "starts_at": event.get("start_at"),
        "ends_at": event.get("end_at"),
        "venue": geo.get("address"),
        "address": geo.get("full_address"),
        "city": geo.get("city"),
        "attendance": {"offline": "in_person", "online": "online"}.get(event.get("location_type")),
        "organiser": ", ".join(h["name"] for h in data.get("hosts") or [] if h.get("name")) or None,
        "event_url": url,
        "image": image_url(event.get("cover_url"), "https://luma.com/"),
        "reg_mode": "approval" if ticket.get("require_approval") else "open_signup",
        "reg_url": url,
        "reg_availability": _availability(data, ticket, event),
        "reg_closes_at": _closes_at(data),
        "spots_left": ticket.get("spots_remaining"),
        "price": "free" if ticket.get("is_free") else "paid" if "is_free" in ticket else None,
    }
    keys = tuple(f"luma:{k}" for k in (event.get("api_id"), event.get("url")) if k)
    return ParsedEvent({k: Evidence(v) for k, v in values.items() if v is not None}, keys)


class Luma(Platform):
    name = "luma"
    hosts = ("luma.com", "lu.ma")
    method = "luma@3"
    event_url = re.compile(r"^https?://(?:www\.)?(?:luma\.com|lu\.ma)/([A-Za-z0-9-]{6,})/?$")
    not_events = frozenset(
        {
            "discover",
            "calendar",
            "user",
            "signin",
            "login",
            "home",
            "create",
            "explore",
            "pricing",
            "about",
            "help",
            "terms",
            "privacy",
            "privacy-policy",
            "blog",
            "changelog",
            "ios",
            "android",
            "careers",
            "settings",
        }
    )
    feed_uid = re.compile(r"^(evt-[A-Za-z0-9]+)@events\.lu\.ma$")
    organiser_path = "user"

    def organiser_source(self, url: str) -> str | None:
        path = segments(url)
        if host(url) not in self.hosts or not path:
            return None
        depth = 2 if path[0] == "user" else 1
        return f"https://luma.com/{'/'.join(path[:depth])}"

    def organiser_domain(self, url: str) -> str:
        key = super().organiser_domain(url)
        return f"luma.com{key.removeprefix('lu.ma')}" if host(url) == "lu.ma" else key

    def listing_feed(self, tree: LexborHTMLParser) -> str | None:
        data = _next_data(tree)
        try:
            calendar_id = data["calendar"]["api_id"]
        except (KeyError, TypeError):
            return None
        if "event" in data or not str(calendar_id).startswith("cal-"):
            return None
        return f"https://api.lu.ma/ics/get?entity=calendar&id={calendar_id}"

    def page_events(self, tree: LexborHTMLParser) -> list[ParsedEvent]:
        """An event page's event; none when its data has another shape than expected,
        so the page goes to the LLM instead of stopping the harvest."""
        data = _next_data(tree)
        event = data.get("event") if isinstance(data, dict) else None
        if not isinstance(event, dict):
            return []
        try:
            return [_event(data, event)]
        except (AttributeError, TypeError):
            return []

    def is_topic_page(self, tree: LexborHTMLParser) -> bool:
        initial = _initial_data(tree)
        return isinstance(initial, dict) and initial.get("kind") == "category"

    def topic_keys(self, tree: LexborHTMLParser) -> set[str]:
        """An event page names its categories, whose pages (`/climate`) look like events."""
        data = _next_data(tree)
        categories = data.get("categories") if isinstance(data, dict) else None
        if not isinstance(categories, list):
            return set()
        slugs = (c.get("slug") for c in categories if isinstance(c, dict))
        urls = [f"https://luma.com/{slug}" for slug in slugs if isinstance(slug, str)]
        return {key for url in urls if (key := self.event_key(url))}
