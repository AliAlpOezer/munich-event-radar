import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from selectolax.lexbor import LexborHTMLParser

from event_radar.fetch import Fetched
from event_radar.intake import discovery, urls
from event_radar.intake.service import IntakeService
from event_radar.model import IntakeState

FIXTURES = Path(__file__).parent / "fixtures"
SIGNALS = ("events", "event", "upcoming", "veranstaltungen", "termine", "calendar")


@pytest.mark.parametrize(
    ("url", "domain"),
    [
        ("https://www.cdtm.com/", "cdtm.com"),
        ("https://www.lmu.de/ai-hub/en/", "lmu.de/ai-hub"),
        ("https://www.tum.de/en/socialai", "tum.de/socialai"),
        ("https://lu.ma/munichnlp", "luma.com/munichnlp"),
        ("https://lu.ma/user/usr-155pp5bEZxqeQBp", "luma.com/user/usr-155pp5bEZxqeQBp"),
        ("https://www.meetup.com/aws-munich/", "meetup.com/aws-munich"),
    ],
)
def test_organiser_domain_matches_the_seed_convention(url, domain):
    assert urls.organiser_domain(url) == domain


@pytest.mark.parametrize("host", ["127.0.0.1", "10.0.0.8", "192.168.1.1", "100.100.1.1", "::1"])
def test_private_and_tailnet_addresses_are_refused(host):
    with pytest.raises(urls.UnsafeAddress):
        urls.check_host(host)


def test_link_scan_finds_the_events_page_and_luma_calendar():
    url = "https://www.example-club.org/"
    tree = LexborHTMLParser((FIXTURES / "club-home.html").read_bytes())
    found = discovery.scan_links(tree, url, SIGNALS)
    assert found == [
        "https://www.example-club.org/events",
        "https://luma.com/user/usr-example123",
    ]


def test_an_events_page_that_renders_with_javascript_is_a_shell():
    events = (FIXTURES / "club-events-shell.html").read_bytes()
    home = (FIXTURES / "club-home.html").read_bytes()
    assert discovery.is_shell(events, "https://www.example-club.org/events")
    assert not discovery.is_shell(home, "https://www.example-club.org/")


def test_platform_pages_become_trackable_sources():
    assert (
        urls.platform_source("https://www.meetup.com/aws-munich/")
        == "https://www.meetup.com/aws-munich/events/ical/"
    )
    assert urls.platform_source("https://www.meetup.com/aws-munich/events/123/") is None
    assert urls.platform_source("https://pretix.eu/MDSI.TUM/") == "https://pretix.eu/MDSI.TUM/"


def _site(start: str) -> dict[str, str]:
    event = {
        "@context": "https://schema.org",
        "@type": "Event",
        "name": "Applied AI Night",
        "startDate": start,
        "location": {
            "@type": "Place",
            "name": "Example Hall",
            "address": {"addressLocality": "München", "streetAddress": "Arcisstr. 21"},
        },
        "offers": {"@type": "Offer", "price": "0", "url": "https://example.org/register"},
    }
    return {
        "/": "<html><head><title>Example AI Club | Munich</title></head><body><main>"
        + "<p>The Example AI Club brings students and engineers together in Munich. " * 10
        + '</p><a href="/about">About us</a><a href="/events">Upcoming events</a>'
        '<a href="/team">Team</a><a href="/blog">Blog</a><a href="/join">Join</a>'
        "</main></body></html>",
        "/events": "<html><body><h1>Events</h1><script type='application/ld+json'>"
        + json.dumps(event)
        + "</script></body></html>",
    }


def _start() -> str:
    return (datetime.now(UTC) + timedelta(days=30)).strftime("%Y-%m-%dT18:00:00+01:00")


def _service(store, profile, pages: dict[str, str], renderer=None, router=None) -> IntakeService:
    def handler(request: httpx.Request) -> httpx.Response:
        body = pages.get(request.url.path)
        if body is None:
            return httpx.Response(404, text="not found")
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    return IntakeService(
        store,
        profile,
        router,
        open_client=lambda ua: httpx.Client(
            transport=httpx.MockTransport(handler), follow_redirects=True
        ),
        guard=lambda host: None,
        renderer=renderer,
        min_interval_s=0,
    )


def test_without_an_llm_the_intake_waits_for_a_person_then_collects_events(store, profile):
    service = _service(store, profile, _site(_start()))
    intake_id = store.intakes.create("https://example.org", None, None)

    service.process(intake_id)
    assert store.intakes.get(intake_id)["state"] == "review"
    # What decide_intake does when an admin approves it.
    store.intakes.update(intake_id, state=IntakeState.QUEUED, approved=True)

    service.process(intake_id)
    row = store.intakes.get(intake_id)
    assert row["state"] == "done", row["log"]
    assert row["result"]["status"] == "active" and row["result"]["upcoming"] == 1

    [org] = store.organisers.all()
    assert (org["name"], org["domain"], org["discovered_via"]) == (
        "Example AI Club",
        "example.org",
        "manual",
    )
    sources = [s["url"] for s in store.sources.of_organiser(org["id"])]
    assert sources == ["https://example.org/events"]
    [event] = store.events.all()
    assert event["fields"]["title"]["value"] == "Applied AI Night"
    assert event["sources"] == sources and event["tier"] in {"A", "B"}

    again = store.intakes.create("https://www.example.org/", None, None)
    service.process(again)
    assert (
        store.intakes.get(again)["error"] == "Already in the registry as Example AI Club (active)."
    )


CALENDAR = "https://example.org/calendar"
SHELL = "<html><body><div id='app'></div><script src='/app.js'></script></body></html>"


def test_a_page_reached_through_two_links_is_rendered_once(store, profile):
    # CDTM, 2026-10-12: /events renders to its Luma calendar, which the homepage also links.
    site = _site(_start())
    listing = site.pop("/events")
    site["/"] = site["/"].replace(
        '<a href="/team">', '<a href="/calendar">Calendar</a><a href="/team">'
    )
    site["/events"] = site["/calendar"] = SHELL
    renders: list[str] = []

    class Renderer:
        def __init__(self, politeness, guard=None):
            self.store = politeness.store

        def fetch(self, url: str) -> Fetched:
            renders.append(url)
            digest = self.store.pages.record(url, listing.encode())
            return Fetched(url, 200, digest, "text/html", True, final_url=CALENDAR)

        def close(self) -> None:
            pass

    service = _service(store, profile, site, renderer=Renderer)
    intake_id = store.intakes.create("https://example.org", None, None)
    store.intakes.update(intake_id, approved=True)
    service.process(intake_id)

    row = store.intakes.get(intake_id)
    assert row["state"] == "done", row["log"]
    # Finding renders /events once; the harvest then renders the calendar it found.
    assert renders == ["https://example.org/events", CALENDAR]
    assert [s["url"] for s in store.sources.of_organiser(row["organiser_id"])] == [CALENDAR]
    finding = [e["text"] for e in row["log"] if e["step"] == "finding"]
    assert sum(CALENDAR in t for t in finding) == 2 and any("already" in t for t in finding)


class _Router:
    """Answers every question with the same profile, and keeps what it was asked."""

    def __init__(self, answer: dict):
        self.answer = answer
        self.asked: list[str] = []

    def complete(self, messages, schema, name="events"):
        self.asked.append(messages[-1]["content"])
        return "test-model", dict(self.answer)


CLUB = {
    "name": "Example AI Club",
    "kind": "student",
    "summary": "A student AI club in Munich.",
    "based_in_munich": "yes",
    "runs_events": "yes",
    "ai_focus": True,
    "legitimate": "yes",
    "reasons": ["Its address is Arcisstr. 21, Munich."],
    "event_links": ["https://example.org/events", "https://invented.example/events"],
}


def test_the_llm_profile_admits_a_genuine_organiser_and_rejects_a_fake(store, profile):
    site = _site(_start())
    site["/about"] = "<html><body><p>Founded in 2019 at TUM, Arcisstr. 21.</p></body></html>"
    router = _Router(CLUB)
    intake_id = store.intakes.create("https://example.org", None, None)
    _service(store, profile, site, router=router).process(intake_id)

    row = store.intakes.get(intake_id)
    assert row["state"] == "done", row["log"]
    assert "About page https://example.org/about:\nFounded in 2019" in router.asked[0]
    # A link the LLM names must be on the page.
    assert row["profile"]["event_links"] == ["https://example.org/events"]
    assert row["profile"]["model"] == "test-model"
    [org] = store.organisers.all()
    assert org["kind"] == "student" and org["ai_focus"] is True

    fake = store.intakes.create("https://spam.example", None, None)
    _service(store, profile, site, router=_Router({**CLUB, "legitimate": "no"})).process(fake)
    assert store.intakes.get(fake)["state"] == "rejected"
