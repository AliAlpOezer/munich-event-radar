"""The platform adapters' invariants (ADR 0008) and the registry's answers."""

import ast
import re
from pathlib import Path

import pytest
from selectolax.lexbor import LexborHTMLParser

from event_radar.platforms import PLATFORMS, is_ticket_host, listing_feed, uid_key

SRC = Path(__file__).resolve().parents[1] / "src"
PLATFORM_DIR = SRC / "event_radar" / "platforms"
FOREIGN = "https://example.org/events/2026/ai-night"
HOST_LIKE = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+")
DATA = '{"props":{"pageProps":{"initialData":{"data":%s}}}}'
ODD = (
    '{"event": {"name": "x", "geo_address_info": "Munich"}, "ticket_info": [], "hosts": [1],'
    ' "categories": "ai"}'
)
PAGES = {
    "empty": "",
    "broken JSON": '{"props": [',
    "no event": DATA % '{"event": null, "calendar": "cal-1"}',
    "odd shapes": DATA % ODD,
}
SUBMODULES = {f"event_radar.platforms.{p.stem}" for p in PLATFORM_DIR.glob("*.py")} - {
    "event_radar.platforms.__init__"
}


def _modules():
    for path in sorted(SRC.rglob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"))


def _imports(path: Path, tree: ast.Module):
    """Each imported module, absolute, and for `from x import y` also `x.y`."""
    package = path.relative_to(SRC).with_suffix("").parts[:-1]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else ()
            module = ".".join([*base, *(node.module.split(".") if node.module else [])])
            if node.module:
                yield module
            yield from (f"{module}.{alias.name}" for alias in node.names)


def _names_a_platform(text: str) -> bool:
    hosts = {h for p in PLATFORMS for h in p.hosts}
    return any(h in text for h in hosts) or any(
        p.claims(f"https://{name}/") for name in HOST_LIKE.findall(text.lower()) for p in PLATFORMS
    )


def test_no_platform_host_is_named_outside_its_adapter():
    named = [
        (path.name, node.value)
        for path, tree in _modules()
        if PLATFORM_DIR not in path.parents
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and _names_a_platform(node.value)
    ]
    assert named == []


def test_only_the_registry_is_imported_and_platforms_import_no_reader():
    for path, tree in _modules():
        imported = set(_imports(path, tree))
        if PLATFORM_DIR in path.parents:
            radar = {m.split(".")[1] for m in imported if m.startswith("event_radar.")}
            assert radar <= {"platforms", "html", "model"}, (path, radar)
        else:
            assert not imported & SUBMODULES, (path, imported & SUBMODULES)


def test_platforms_have_their_own_names_and_hosts():
    assert len({p.name for p in PLATFORMS}) == len(PLATFORMS)
    for platform in PLATFORMS:
        for host in platform.hosts:
            url = f"https://www.{host}/forms/d/x"  # a path every platform claims on its hosts
            assert [p for p in PLATFORMS if p.claims(url)] == [platform]


@pytest.mark.parametrize(
    ("url", "ticket"),
    [
        ("https://docs.google.com/forms/d/e/1FAIpQLSexample/viewform", True),
        ("https://docs.google.com/document/d/1example/edit", False),
        ("https://docs.google.com/spreadsheets/d/1example", False),
        ("https://api.lu.ma/ics/get?entity=calendar&id=cal-example", True),
        ("https://www.eventbrite.co.uk/e/ai-night-tickets-123456789", True),
        ("https://www.eventbrite.de/o/example-org-123456", True),
        ("https://notluma.com/example-ai-night", False),
        ("https://luma.com.example.org/example-ai-night", False),
        ("https://xmeetup.com/aws-munich/events/123456/", False),
    ],
)
def test_a_platform_claims_its_hosts_and_their_subdomains_only(url, ticket):
    assert is_ticket_host(url) is ticket


def test_a_feed_uid_names_its_platform_event():
    assert uid_key("evt-AbC123@events.lu.ma") == "luma:evt-AbC123"
    assert uid_key("event_312345678@meetup.com") == "meetup:312345678"
    assert uid_key("4711@example.org") is None


def test_a_luma_calendar_page_names_its_feed():
    data = '{"props":{"pageProps":{"initialData":{"data":{"calendar":{"api_id":"cal-XyZ1"}}}}}}'
    page = LexborHTMLParser(f'<script id="__NEXT_DATA__">{data}</script>'.encode())
    assert listing_feed(page) == "https://api.lu.ma/ics/get?entity=calendar&id=cal-XyZ1"


@pytest.mark.parametrize("platform", PLATFORMS, ids=lambda p: p.name)
def test_an_adapter_answers_nothing_on_a_page_or_url_not_its_own(platform):
    assert not platform.claims(FOREIGN)
    assert platform.event_key(FOREIGN) is None
    assert platform.uid_key("4711@example.org") is None
    assert platform.organiser_source(FOREIGN) is None
    for data in PAGES.values():
        page = LexborHTMLParser(f'<script id="__NEXT_DATA__">{data}</script>'.encode())
        assert platform.listing_feed(page) is None
        assert platform.page_events(page) == []
        assert platform.topic_keys(page) == set()
        assert not platform.is_topic_page(page)
