"""Which addresses an intake may fetch, and how a URL names its organiser."""

import ipaddress
import re
import socket

import httpx

from .. import platforms

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
    """The registry's key for the organiser behind `url`: its page on a platform, else its
    host plus the first path segment past a language prefix."""
    if (key := platforms.organiser_domain(url)) is not None:
        return key
    name = platforms.host(url)
    segments = platforms.segments(url)
    if segments and _LANG.match(segments[0].lower()):
        segments = segments[1:]
    return f"{name}/{segments[0]}" if segments else name
