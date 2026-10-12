"""Which addresses an intake may fetch, and how a URL names its organiser."""

import ipaddress
import re
import socket
from urllib.parse import urlsplit

import httpx

from ..platforms import host, is_ticket_host

_LANG = re.compile(r"^[a-z]{2}(-[a-z]{2})?$")


class UnsafeAddress(httpx.HTTPError):
    pass


def check_host(name: str | None) -> None:
    """ADR 0003 D4: refuse hosts that resolve to anything but public addresses."""
    if not name:
        raise UnsafeAddress("no host")
    try:
        infos = socket.getaddrinfo(name, None)
    except socket.gaierror as exc:
        raise UnsafeAddress(f"{name} does not resolve") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%")[0])
        if not address.is_global or address.is_multicast:
            raise UnsafeAddress(f"{name} resolves to a non-public address ({address})")


def _guard_request(request: httpx.Request) -> None:
    check_host(request.url.host)


def guarded_client(user_agent: str) -> httpx.Client:
    """Every request, redirects included, passes the address check."""
    return httpx.Client(
        headers={"User-Agent": user_agent},
        follow_redirects=True,
        timeout=15.0,
        event_hooks={"request": [_guard_request]},
    )


def organiser_domain(url: str) -> str:
    """The registry's key for the organiser behind `url`: its host, plus the first path
    segment past a language prefix, or the organiser's page on a ticket platform."""
    name = host(url)
    name = "luma.com" if name == "lu.ma" else name
    segments = [s for s in urlsplit(url).path.split("/") if s]
    if is_ticket_host(url):
        depth = 2 if segments[:1] == ["user"] else 1
        return "/".join([name, *segments[:depth]])
    if segments and _LANG.match(segments[0].lower()):
        segments = segments[1:]
    return f"{name}/{segments[0]}" if segments else name


def platform_source(url: str) -> str | None:
    """A ticket-platform organiser page, as the URL to track, or None."""
    name = host(url)
    segments = [s for s in urlsplit(url).path.split("/") if s]
    if not segments:
        return None
    if name in {"luma.com", "lu.ma"}:
        depth = 2 if segments[0] == "user" else 1
        return f"https://luma.com/{'/'.join(segments[:depth])}"
    if name == "meetup.com" and len(segments) <= 2 and segments[1:] in ([], ["events"]):
        return f"https://www.meetup.com/{segments[0]}/events/ical/"
    if name == "pretix.eu" and len(segments) == 1:
        return f"https://pretix.eu/{segments[0]}/"
    if name.startswith("eventbrite.") and segments[0] == "o" and len(segments) == 2:
        return url
    return None
