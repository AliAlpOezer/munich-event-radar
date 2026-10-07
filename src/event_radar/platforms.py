import re
from urllib.parse import urlsplit, urlunsplit

from .model import SourceClass

_PATTERNS = (
    ("luma", re.compile(r"^https?://(?:www\.)?(?:luma\.com|lu\.ma)/([A-Za-z0-9-]{6,})/?$")),
    ("eventbrite", re.compile(r"^https?://(?:www\.)?eventbrite\.[a-z.]+/e/(?:[^/?#]*-)?(\d{9,})")),
    ("meetup", re.compile(r"^https?://(?:www\.)?meetup\.com/[^/]+/events/(\d+)")),
    ("pretix", re.compile(r"^https?://pretix\.eu/([^/?#]+)/([^/?#]+)")),
    ("typeform", re.compile(r"^https?://form\.typeform\.com/to/([A-Za-z0-9]+)")),
    ("tally", re.compile(r"^https?://tally\.so/r/([A-Za-z0-9]+)")),
    ("gform", re.compile(r"^https?://(?:forms\.gle/|docs\.google\.com/forms/d/(?:e/)?)([\w-]+)")),
)

_NOT_EVENTS = {
    "luma": {"discover", "calendar", "user", "signin", "login", "home", "create", "explore",
             "pricing", "about", "help", "terms", "privacy", "privacy-policy", "blog", "changelog",
             "ios", "android", "careers", "settings"},
}
_NOT_EVENT_PREFIXES = {"pretix": ("locale/", "control/", "api/", "static/", "about/")}

TICKET_HOSTS = ("luma.com", "lu.ma", "eventbrite.", "meetup.com", "pretix.eu", "typeform.com",
                "tally.so", "forms.gle", "docs.google.com")


def platform_key(url: str) -> str | None:
    for platform, pattern in _PATTERNS:
        match = pattern.match(url.strip())
        if not match:
            continue
        ident = "/".join(g for g in match.groups() if g)
        if ident.lower() in _NOT_EVENTS.get(platform, ()):
            return None
        if ident.lower().startswith(_NOT_EVENT_PREFIXES.get(platform, ())):
            return None
        return f"{platform}:{ident}"
    return None


def is_ticket_host(url: str) -> bool:
    host = urlsplit(url).netloc.lower()
    return any(h in host for h in TICKET_HOSTS)


def source_class(url: str, aggregators: list[str]) -> SourceClass:
    if is_ticket_host(url):
        return SourceClass.TICKET
    return SourceClass.AGG if any(a in url for a in aggregators) else SourceClass.OWN


def canonical(url: str) -> str:
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))
