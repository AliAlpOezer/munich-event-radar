import dataclasses
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from selectolax.lexbor import LexborHTMLParser

from event_radar import config, intake, render
from event_radar.fetch import Fetched
from event_radar.web import create_app

FIXTURES = Path(__file__).parent / "fixtures"
SIGNALS = ["events", "event", "upcoming", "veranstaltungen", "termine", "calendar"]


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
    assert intake.organiser_domain(url) == domain


@pytest.mark.parametrize("raw", ["", "not a url", "ftp://example.org", "javascript:alert(1)"])
def test_bad_urls_are_refused(raw):
    with pytest.raises(intake.IntakeError):
        intake.normalise_url(raw)


def test_bare_hosts_get_https():
    assert intake.normalise_url(" cdtm.com ") == "https://cdtm.com"


@pytest.mark.parametrize("host", ["127.0.0.1", "10.0.0.8", "192.168.1.1", "100.100.1.1", "::1"])
def test_private_and_tailnet_addresses_are_refused(host):
    with pytest.raises(intake.UnsafeAddress):
        intake.check_host(host)


def test_link_scan_finds_cdtm_events_page_and_luma_calendar():
    url = "https://www.cdtm.com/"
    tree = LexborHTMLParser((FIXTURES / "cdtm-home-2026-10-08.html").read_bytes())
    found = intake.scan_links(tree, url, SIGNALS)
    assert "https://www.cdtm.com/events" in found
    assert "https://luma.com/user/usr-155pp5bEZxqeQBp" in found
    assert not any("program" in link for link in found)


def test_cdtm_events_page_is_a_javascript_shell():
    events = (FIXTURES / "cdtm-events-2026-10-08.html").read_bytes()
    home = (FIXTURES / "cdtm-home-2026-10-08.html").read_bytes()
    assert intake.is_shell(events, "https://www.cdtm.com/events")
    assert not intake.is_shell(home, "https://www.cdtm.com/")


def test_platform_pages_become_trackable_sources():
    assert (
        intake.platform_source("https://www.meetup.com/aws-munich/")
        == "https://www.meetup.com/aws-munich/events/ical/"
    )
    assert intake.platform_source("https://www.meetup.com/aws-munich/events/123/") is None
    assert intake.platform_source("https://pretix.eu/MDSI.TUM/") == "https://pretix.eu/MDSI.TUM/"


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


@pytest.fixture
def service(tmp_path, monkeypatch, store):
    return _service(tmp_path, monkeypatch, store, _site(_start()))


def _service(tmp_path, monkeypatch, store, pages: dict[str, str]) -> intake.IntakeService:
    def handler(request: httpx.Request) -> httpx.Response:
        body = pages.get(request.url.path)
        if body is None:
            return httpx.Response(404, text="not found")
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    monkeypatch.setattr(intake, "check_host", lambda host: None)
    monkeypatch.setattr(
        intake,
        "guarded_client",
        lambda ua: httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True),
    )
    monkeypatch.setattr(render, "available", lambda: False)
    settings = dataclasses.replace(
        config.load(), data_dir=tmp_path, database_url=None, openrouter_key=None
    )
    svc = intake.IntakeService(settings, store)
    svc.min_interval_s = 0
    return svc


def test_without_an_llm_the_intake_waits_for_a_person_then_collects_events(service):
    client = TestClient(create_app(service.store, service))
    created = client.post("/api/intakes", json={"url": "example.org"})
    assert created.status_code == 202 and created.json()["state"] == "queued"
    intake_id = created.json()["id"]

    service.process(intake_id)
    row = client.get(f"/api/intakes/{intake_id}").json()
    assert row["state"] == "review"
    assert (
        client.post(f"/api/intakes/{intake_id}/decision", json={"approve": True}).json()["state"]
        == "queued"
    )

    service.process(intake_id)
    row = client.get(f"/api/intakes/{intake_id}").json()
    assert row["state"] == "done", row["log"]
    assert row["result"]["status"] == "active" and row["result"]["upcoming"] == 1

    [org] = client.get("/api/organisers").json()["organisers"]
    assert (org["name"], org["domain"], org["discovered_via"]) == (
        "Example AI Club",
        "example.org",
        "manual",
    )
    assert [s["url"] for s in org["sources"]] == ["https://example.org/events"]
    [event] = client.get("/api/events").json()["events"]
    assert event["fields"]["title"]["value"] == "Applied AI Night"
    assert event["organiser_ids"] == [org["id"]] and event["tier"] in {"A", "B"}

    again = client.post("/api/intakes", json={"url": "https://www.example.org/"})
    assert again.status_code == 409


def test_decisions_only_apply_to_intakes_waiting_for_one(service):
    client = TestClient(create_app(service.store, service))
    intake_id = client.post("/api/intakes", json={"url": "example.org"}).json()["id"]
    assert (
        client.post(f"/api/intakes/{intake_id}/decision", json={"approve": True}).status_code == 409
    )
    assert client.post("/api/intakes/999/decision", json={"approve": True}).status_code == 404
    assert client.post("/api/intakes", json={"url": "nonsense"}).status_code == 422


def test_without_the_service_adding_is_disabled(store):
    client = TestClient(create_app(store))
    assert client.get("/api/intakes").json() == {"enabled": False, "intakes": []}
    assert client.post("/api/intakes", json={"url": "example.org"}).status_code == 503


CALENDAR = "https://example.org/calendar"
SHELL = "<html><body><div id='app'></div><script src='/app.js'></script></body></html>"


def test_a_page_reached_through_two_links_is_rendered_once(tmp_path, monkeypatch, store):
    # CDTM, 2026-10-12: /events renders to its Luma calendar, which the homepage also links.
    site = _site(_start())
    listing = site.pop("/events")
    site["/"] = site["/"].replace(
        '<a href="/team">', '<a href="/calendar">Calendar</a><a href="/team">'
    )
    site["/events"] = site["/calendar"] = SHELL
    renders: list[str] = []

    class Renderer:
        def __init__(self, fetcher, snapshots, guard=None):
            self.snapshots = snapshots

        def fetch(self, url: str) -> Fetched:
            renders.append(url)
            digest = self.snapshots.put(listing.encode())
            return Fetched(url, 200, digest, "text/html", True, final_url=CALENDAR)

        def close(self) -> None:
            pass

    svc = _service(tmp_path, monkeypatch, store, site)
    monkeypatch.setattr(render, "available", lambda: True)
    monkeypatch.setattr(render, "Renderer", Renderer)
    intake_id = svc.submit("example.org", None, None)
    svc.store.update_intake(intake_id, approved=True)
    svc.process(intake_id)

    row = svc.store.intake(intake_id)
    assert row["state"] == "done", row["log"]
    # Finding renders /events once; the harvest then renders the calendar it found.
    assert renders == ["https://example.org/events", CALENDAR]
    assert [s["url"] for s in svc.store.organiser_sources(row["organiser_id"])] == [CALENDAR]
    finding = [e["text"] for e in row["log"] if e["step"] == "finding"]
    assert sum(CALENDAR in t for t in finding) == 2 and any("already" in t for t in finding)
