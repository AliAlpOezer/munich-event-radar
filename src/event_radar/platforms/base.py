"""What a platform adapter answers (ADR 0008), and the URL helpers the radar shares."""

import re
from urllib.parse import urlsplit, urlunsplit

from selectolax.lexbor import LexborHTMLParser

from ..model import ParsedEvent


def host(url: str) -> str:
    """The URL's host, lower case and without `www.`."""
    return (urlsplit(url).hostname or "").removeprefix("www.")


def canonical(url: str) -> str:
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def segments(url: str) -> list[str]:
    return [s for s in urlsplit(url).path.split("/") if s]


class Platform:
    """An event platform. A subclass declares its hosts and URL patterns and overrides
    what its platform has; the defaults answer None or []."""

    name: str
    hosts: tuple[str, ...] = ()
    method: str | None = None
    event_url: re.Pattern[str] | None = None
    not_events: frozenset[str] = frozenset()
    not_event_prefixes: tuple[str, ...] = ()
    feed_uid: re.Pattern[str] | None = None

    def claims(self, url: str) -> bool:
        try:
            netloc = urlsplit(url).netloc.lower()
        except ValueError:
            return False
        return any(h in netloc for h in self.hosts)

    def event_key(self, url: str) -> str | None:
        """`<name>:<id>` when `url` is one of its event pages."""
        match = self.event_url.match(url.strip()) if self.event_url else None
        if not match:
            return None
        ident = "/".join(g for g in match.groups() if g)
        if ident.lower() in self.not_events or ident.lower().startswith(self.not_event_prefixes):
            return None
        return f"{self.name}:{ident}"

    def uid_key(self, uid: str) -> str | None:
        """`<name>:<id>` when `uid` names one of its events in a feed."""
        match = self.feed_uid.match(uid) if self.feed_uid else None
        return f"{self.name}:{match.group(1)}" if match else None

    def organiser_source(self, url: str) -> str | None:
        """An organiser's page on this platform, as the URL to track."""
        return None

    def organiser_domain(self, url: str) -> str:
        """The organiser registry's key for one of its pages."""
        path = segments(url)
        depth = 2 if path[:1] == ["user"] else 1
        return "/".join([host(url), *path[:depth]])

    def listing_feed(self, tree: LexborHTMLParser) -> str | None:
        """The feed one of its listing pages offers."""
        return None

    def page_events(self, tree: LexborHTMLParser) -> list[ParsedEvent]:
        """The events on one of its pages, recognised by the page, not its URL."""
        return []
