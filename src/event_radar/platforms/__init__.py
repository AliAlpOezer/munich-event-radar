"""Event platforms (ADR 0008): one adapter per platform behind a registry. A URL goes to
the platform that claims its host; a page goes to each platform in turn until one answers.
None or [] means no platform claims it, and the caller takes its generic path."""

from selectolax.lexbor import LexborHTMLParser

from ..model import ParsedEvent
from .base import Platform, canonical, host, segments
from .eventbrite import Eventbrite
from .forms import GoogleForms, Tally, Typeform
from .luma import Luma
from .meetup import Meetup
from .pretix import Pretix

__all__ = [
    "PLATFORMS",
    "Platform",
    "canonical",
    "host",
    "is_ticket_host",
    "listing_feed",
    "organiser_domain",
    "organiser_source",
    "page_events",
    "platform_key",
    "segments",
    "uid_key",
]

PLATFORMS: tuple[Platform, ...] = (
    Luma(),
    Eventbrite(),
    Meetup(),
    Pretix(),
    Typeform(),
    Tally(),
    GoogleForms(),
)


def _owner(url: str) -> Platform | None:
    return next((p for p in PLATFORMS if p.claims(url)), None)


def platform_key(url: str) -> str | None:
    owner = _owner(url)
    return owner.event_key(url) if owner else None


def is_ticket_host(url: str) -> bool:
    """A platform page: a TICKET source whose registration fields need no LLM."""
    return _owner(url) is not None


def organiser_source(url: str) -> str | None:
    owner = _owner(url)
    return owner.organiser_source(url) if owner else None


def organiser_domain(url: str) -> str | None:
    owner = _owner(url)
    return owner.organiser_domain(url) if owner else None


def uid_key(uid: str) -> str | None:
    return next((key for p in PLATFORMS if (key := p.uid_key(uid))), None)


def listing_feed(tree: LexborHTMLParser) -> str | None:
    return next((feed for p in PLATFORMS if (feed := p.listing_feed(tree))), None)


def page_events(tree: LexborHTMLParser) -> tuple[str | None, list[ParsedEvent]]:
    """The observation method and the events of the first platform that knows the page."""
    for platform in PLATFORMS:
        if found := platform.page_events(tree):
            return platform.method, found
    return None, []
