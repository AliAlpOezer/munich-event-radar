import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from selectolax.lexbor import LexborHTMLParser

from event_radar.extract.router import BudgetExhausted
from event_radar.fetch import Fetched
from event_radar.intake import discovery, urls
from event_radar.intake.service import IntakeService
from event_radar.model import IntakeState, JudgedEvent, Verdict
from event_radar.platforms import organiser_source

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
        ("https://www.eventbrite.de/o/example-org-123456", "eventbrite.de/o/example-org-123456"),
        ("https://www.eventbrite.de/o/other-org-654321", "eventbrite.de/o/other-org-654321"),
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
        organiser_source("https://www.meetup.com/aws-munich/")
        == "https://www.meetup.com/aws-munich/events/ical/"
    )
    assert organiser_source("https://www.meetup.com/aws-munich/events/123/") is None
    assert organiser_source("https://pretix.eu/MDSI.TUM/") == "https://pretix.eu/MDSI.TUM/"


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

    def complete(self, messages, schema, name="events", first=None):
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


# Adding one event from its page (ADR 0014).

PAGE = "https://example.org/events/ai-night"
DROPPED_OFF_TOPIC = "The page is not about AI, startups, careers or tech."
A = Verdict("A", ("ai=core",))
ONLINE = "https://schema.org/OnlineEventAttendanceMode"


def _jsonld(name: str, start: str, **more) -> str:
    event = {
        "@context": "https://schema.org",
        "@type": "Event",
        "name": name,
        "startDate": start,
        "location": {
            "@type": "Place",
            "name": "Example Hall",
            "address": {"addressLocality": "München", "streetAddress": "Arcisstr. 21"},
        },
        "offers": {"@type": "Offer", "price": "0", "url": "https://example.org/register"},
        **more,
    }
    return f"<script type='application/ld+json'>{json.dumps(event)}</script>"


def _page(*scripts: str, text: str = "") -> str:
    return f"<html><body><main><p>{text}</p>{''.join(scripts)}</main></body></html>"


def _day() -> date:
    return date.fromisoformat(_start()[:10])


def _add(store, profile, page: str, day: date | None = None, place="Munich", url=PAGE, router=None):
    """Work an event intake for `url`, which serves `page`; returns the service and row."""
    service = _service(store, profile, {"/events/ai-night": page}, router=router)
    intake_id = store.intakes.create_event(url, day or _day(), place)
    service.process(intake_id)
    return service, store.intakes.get(intake_id)


def test_an_event_with_structured_data_is_on_the_board_at_once(store, profile):
    service, row = _add(store, profile, _page(_jsonld("Applied AI Night", _start())))
    assert row["state"] == "done", row["log"]
    [source] = store.sources.all()
    assert (source["url"], source["kind"], source["organiser_id"], source["added_via"]) == (
        PAGE,
        "event_page",
        None,
        f"intake:{row['id']}",
    )
    [event] = store.events.all()
    assert event["tier"] == "B" and row["error"] is None
    assert row["result"] == {
        "event_key": event["key"],
        "title": "Applied AI Night",
        "tier": "B",
        "drop_reason": None,
        "already": False,
    }
    assert [e["step"] for e in row["log"]] == ["checking", "done"]
    # A job cut off mid-harvest resumes with the page it added.
    store.intakes.update(row["id"], state="harvesting")
    assert service.drain() == 1
    assert store.intakes.get(row["id"])["result"] == row["result"]
    assert len(store.sources.all()) == 1 and len(store.events.all()) == 1


def test_a_page_without_structured_data_passes_on_its_words(store, profile):
    day = _day()
    text = f"Applied AI Night. {day.day} {day:%B %Y}, 18:00, Example Hall, München."
    _, row = _add(store, profile, _page(text=text))
    assert row["state"] == "done", row["log"]
    assert row["result"] == {"waiting": True}
    assert "next daily run" in row["log"][-1]["text"]
    assert [s["url"] for s in store.sources.all()] == [PAGE]
    assert [j["url"] for j in store.extraction.pending(10)] == [PAGE]


@pytest.mark.parametrize(
    ("page", "day", "place", "message"),
    [
        (
            _page(_jsonld("Applied AI Night", "2030-11-05T18:00:00+01:00")),
            date(2030, 11, 6),
            "Munich",
            "The page says the event is on 2030-11-05, not 2030-11-06.",
        ),
        (
            _page(
                _jsonld("Applied AI Night", "2030-11-05T18:00:00+01:00"),
                _jsonld("Applied AI Day", "2030-11-12T18:00:00+01:00"),
            ),
            date(2030, 11, 5),
            "Munich",
            "The page lists 2 events; give the event's own page.",
        ),
        (
            _page(text="Applied AI Night, every first Tuesday in Munich."),
            date(2030, 11, 5),
            "Munich",
            "The page does not name 5 November 2030.",
        ),
        (
            _page(text="Applied AI Night, 5 November, in Munich."),
            date(2030, 11, 5),
            "Munich",
            "The page does not name 5 November 2030.",
        ),
        (
            _page(_jsonld("Applied AI Night", "2030-11-05T18:00:00+01:00")),
            date(2030, 11, 5),
            "Werk1",
            "The page does not name Werk1.",
        ),
        (
            _page(text="Salsa night, 5 November 2030, in Munich."),
            date(2030, 11, 5),
            "Munich",
            "The page is not about AI, startups, careers or tech.",
        ),
        (
            # aistrategyconference.com, 2026-10-18: the page renders only with JavaScript.
            '<html><body><div id="root"></div><script src="/app.js"></script></body></html>',
            date(2030, 11, 5),
            "Munich",
            "The page shows no text without JavaScript; give the event's ticket page instead.",
        ),
    ],
    ids=["other day", "listing", "no day", "no year", "other place", "no topic words", "no text"],
)
def test_the_page_must_name_the_day_and_the_place(store, profile, page, day, place, message):
    _, row = _add(store, profile, page, day, place)
    assert (row["state"], row["error"]) == ("rejected", message), row["log"]
    assert row["log"][-1]["level"] == "warn"
    assert store.sources.all() == [] and store.events.all() == []


@pytest.mark.parametrize(
    ("event", "place", "message"),
    [
        (_jsonld("Talk on AI agents", "2020-03-01T18:00:00+01:00"), "Munich", "The event is over."),
        (
            _jsonld("Talk on AI agents", "2030-11-05T18:00:00+01:00", eventAttendanceMode=ONLINE),
            "Munich",
            "The event is online only; the radar shows events in Munich.",
        ),
        (
            _jsonld(
                "Talk on AI agents",
                "2030-11-05T18:00:00+01:00",
                location={"@type": "Place", "address": {"addressLocality": "Berlin"}},
            ),
            "Berlin",
            "The event is not in Munich.",
        ),
        (
            _jsonld("TUM tanzt!", "2030-11-05T18:00:00+01:00"),
            "Munich",
            "The page is not about AI, startups, careers or tech.",
        ),
        (
            _jsonld("Children's University: How Do AI Agents Learn?", "2030-11-05T17:00:00+01:00"),
            "Munich",
            "The event is for children, school pupils or families; the radar is for adults.",
        ),
    ],
    ids=["past", "online", "outside Munich", "off topic", "children"],
)
def test_the_judge_refuses_what_it_would_not_show(store, profile, event, place, message):
    start = re.search(r'"startDate": "(\d{4}-\d{2}-\d{2})', event).group(1)
    _, row = _add(store, profile, _page(event), date.fromisoformat(start), place)
    assert (row["state"], row["error"]) == ("rejected", message), row["log"]
    assert store.sources.all() == []


def test_a_page_the_llm_reads_passes_on_its_words_though_its_title_names_no_topic(store, profile):
    no_tickets = _jsonld("Fireside chat", "2030-11-05T18:00:00+01:00", offers=None)
    page = _page(no_tickets, text="A fireside chat on machine learning in industry.")
    _, row = _add(store, profile, page, date(2030, 11, 5))
    assert row["state"] == "done", row["log"]
    assert [j["url"] for j in store.extraction.pending(10)] == [PAGE]


class _Judgments:
    """An LLM stand-in that judges every event as `answer`, and keeps whom it asked first."""

    def __init__(self, **answer):
        topics = {"startup": False, "career": False, "tech": False}
        self.answer = {"ai": "none", **topics, "kind": "other", "audience": "adults", **answer}
        self.asked: list[tuple[str, str | None]] = []

    def complete(self, messages, schema, name="events", first=None):
        self.asked.append((name, first))
        lines = messages[-1]["content"].splitlines()
        return "test-model", {"judgments": [{"id": i, **self.answer} for i in range(len(lines))]}


# The Eventbrite talk refused on 2026-10-19, hand-written with invented names: tickets in its
# structured data, so no LLM extraction, and its topic only in its text.
TALK = _page(
    _jsonld("Campus Speakers Series x Example Retail Group", "2030-10-26T17:30:00+01:00"),
    text="What AI and the future of work mean for talent; recruiters join the after-event.",
)


def test_an_event_its_words_do_not_place_is_added_on_its_judgment(store, profile):
    judge = _Judgments(ai="some", career=True, tech=True, kind="industry_talk")
    _, row = _add(store, profile, TALK, date(2030, 10, 26), router=judge)
    assert row["state"] == "done", row["log"]
    assert row["result"]["tier"] == "A"
    assert judge.asked == [("judgments", "groq")]
    [event] = store.events.all()
    assert event["judgment_key"]


def test_an_event_judged_off_topic_is_refused_and_without_an_llm_the_words_decide(store, profile):
    _, row = _add(store, profile, TALK, date(2030, 10, 26), router=_Judgments(kind="workshop"))
    assert (row["state"], row["error"]) == ("rejected", DROPPED_OFF_TOPIC), row["log"]
    store.intakes.update(row["id"], state="failed")
    _, row = _add(store, profile, TALK, date(2030, 10, 26))
    assert (row["state"], row["error"]) == ("rejected", DROPPED_OFF_TOPIC), row["log"]


def test_an_event_whose_page_says_too_little_to_judge_is_checked_by_its_words(store, profile):
    page = _page(_jsonld("Talk on AI agents", "2030-11-05T18:00:00+01:00"), text="Join us.")
    judge = _Judgments(ai="unknown")
    _, row = _add(store, profile, page, date(2030, 11, 5), router=judge)
    assert row["state"] == "done", row["log"]
    assert row["result"]["tier"] == "B" and judge.asked == [("judgments", "groq")]


def test_a_page_on_the_radar_already_adds_nothing(store, profile):
    org = store.organisers.add("Example AI Club", "example.org", "test", ai_focus=True)
    store.sources.add(PAGE, "event_page", "OWN", 72, "seed", org)
    title = {"title": {"value": "Applied AI Night"}}
    store.events.save([JudgedEvent({f"url:{PAGE}"}, title, "url", A, [PAGE])])
    [event] = store.events.all()
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(404)

    service = IntakeService(
        store,
        profile,
        open_client=lambda ua: httpx.Client(transport=httpx.MockTransport(handler)),
        guard=lambda host: None,
        min_interval_s=0,
    )
    intake_id = store.intakes.create_event("https://EXAMPLE.org/events/ai-night/", _day(), "x")
    service.process(intake_id)
    row = store.intakes.get(intake_id)
    assert row["state"] == "done" and asked == []
    assert row["result"] == {
        "event_key": event["key"],
        "title": "Applied AI Night",
        "tier": "A",
        "drop_reason": None,
        "already": True,
    }
    assert row["log"][-1]["text"] == "Already on the radar: Applied AI Night, tier A."
    assert len(store.sources.all()) == 1


def test_an_event_the_board_knows_from_another_page_adds_nothing(store, profile):
    title = {"title": {"value": "Applied AI Night"}}
    listing = "https://example.org/events"
    store.events.save([JudgedEvent({f"url:{PAGE}"}, title, "url", A, [listing])])
    page = _page(_jsonld("Applied AI Night", _start(), url=PAGE))
    _, row = _add(store, profile, page, url=f"{PAGE}?utm_source=newsletter")
    assert row["state"] == "done" and row["result"]["already"] is True, row["log"]
    assert store.sources.all() == []


def test_a_page_that_cannot_be_read_fails(store, profile):
    _, row = _add(store, profile, "", url="https://example.org/events/gone")
    assert (row["state"], row["error"]) == ("failed", "The page did not load: http_404.")

    def guard(host: str | None) -> None:
        raise urls.UnsafeAddress(f"{host} resolves to a non-public address (10.0.0.8)")

    service = IntakeService(store, profile, guard=guard, min_interval_s=0)
    intake_id = store.intakes.create_event("https://intranet.example/e", _day(), "Munich")
    service.process(intake_id)
    row = store.intakes.get(intake_id)
    assert row["state"] == "failed" and row["error"].startswith("This address cannot be fetched")


class _Reader:
    """An LLM stand-in that reads every page as `events`, each value quoted as the page
    writes it, and keeps the names of the questions it was asked."""

    def __init__(self, *events: dict, fail: Exception | None = None):
        self.events = events
        self.fail = fail
        self.asked: list[str] = []

    def complete(self, messages, schema, name="events", first=None):
        self.asked.append(name)
        if self.fail:
            raise self.fail
        answer = [
            {
                k: {"value": v[0], "quote": v[1]}
                if isinstance(v, tuple)
                else {"value": v, "quote": v}
                for k, v in event.items()
            }
            for event in self.events
        ]
        return "test-model", {"events": answer}


NIGHT_TEXT = (
    "Applied AI Night. 5 November 2030, 18:00, Example Hall, München."
    " A talk on machine learning in industry."
)
NIGHT = {
    "title": "Applied AI Night",
    "starts_at": ("2030-11-05T18:00:00+01:00", "5 November 2030, 18:00"),
    "city": "München",
    "ai_relevance": ("core", "A talk on machine learning in industry."),
}


def test_a_page_the_llm_must_read_is_read_at_once_without_the_relevance_question(store, profile):
    reader = _Reader(NIGHT)
    _, row = _add(store, profile, _page(text=NIGHT_TEXT), date(2030, 11, 5), router=reader)
    assert row["state"] == "done", row["log"]
    assert reader.asked == ["judgments", "events"]
    [event] = store.events.all()
    assert (row["result"]["event_key"], row["result"]["tier"]) == (event["key"], "B")
    assert store.extraction.pending(10) == []


@pytest.mark.parametrize(
    ("text", "events", "message"),
    [
        (
            NIGHT_TEXT + " Robotics Breakfast: 12 November 2030, 08:00.",
            (
                NIGHT,
                {
                    "title": "Robotics Breakfast",
                    "starts_at": ("2030-11-12T08:00:00+01:00", "12 November 2030, 08:00"),
                },
            ),
            "The LLM found no single event on the page; give the event's own page.",
        ),
        (
            "Applied AI Night on machine learning. Registration opens 5 November 2030;"
            " the talk is on 12 November 2030, 18:00, in München.",
            (
                {
                    "title": "Applied AI Night",
                    "starts_at": ("2030-11-12T18:00:00+01:00", "12 November 2030, 18:00"),
                },
            ),
            "The page says the event is on 2030-11-12, not 2030-11-05.",
        ),
        (
            "Salsa night for founders. 5 November 2030, 20:00, Example Hall, München.",
            (
                {
                    "title": "Salsa night",
                    "starts_at": ("2030-11-05T20:00:00+01:00", "5 November 2030, 20:00"),
                    "ai_relevance": ("none", "Salsa night"),
                },
            ),
            "The page is not about AI, startups, careers or tech.",
        ),
    ],
    ids=["several events", "other day", "off topic"],
)
def test_the_llms_reading_is_checked_again_and_a_refused_page_leaves(
    store, profile, text, events, message
):
    _, row = _add(store, profile, _page(text=text), date(2030, 11, 5), router=_Reader(*events))
    assert (row["state"], row["error"]) == ("rejected", message), row["log"]
    assert row["log"][-1]["step"] == "harvesting"
    assert store.sources.all() == [] and store.events.all() == []
    with store.tx() as db:
        for table in ("observation", "extraction_job", "snapshot"):
            assert db.execute(f"SELECT FROM {table}").rowcount == 0, table


def test_with_the_llm_budget_spent_the_page_waits_for_the_daily_run(store, profile):
    reader = _Reader(NIGHT, fail=BudgetExhausted())
    _, row = _add(store, profile, _page(text=NIGHT_TEXT), date(2030, 11, 5), router=reader)
    assert (row["state"], row["result"]) == ("done", {"waiting": True}), row["log"]
    assert row["log"][-1]["text"].startswith("Today's LLM calls are used up")
    assert [j["url"] for j in store.extraction.pending(10)] == [PAGE]
