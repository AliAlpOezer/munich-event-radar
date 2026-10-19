"""The daily web search (ADR 0016): what a hit becomes, what the radar already knows, and the
budget and caps that keep the review list short."""

import json
from dataclasses import replace
from datetime import date

import httpx
import pytest

from event_radar import search
from event_radar.judge import Judge
from event_radar.model import IntakeKind, IntakeState
from event_radar.search import gate
from event_radar.search.tavily import Tavily

DAY = date(2026, 10, 20)


def _hit(url: str, title: str = "AI Night Munich", snippet: str = "") -> gate.Hit:
    return gate.Hit(url, title, snippet, 0.5)


@pytest.mark.parametrize(
    "url, title, made",
    [
        ("https://lu.ma/ai-night-munich", "AI Night Munich", ("event", None)),
        ("https://www.meetup.com/munich-ai/events/301234567/", "AI Night", ("event", None)),
        (
            "https://aimunich.org/events/talk",
            "AI talk in München",
            ("organisation", "https://aimunich.org/events/talk"),
        ),
        ("https://aimunich.org/", "Munich AI club", ("organisation", None)),
        ("https://www.meetup.com/munich-ai/", "Munich AI meetup group", ("organisation", None)),
        ("https://www.linkedin.com/events/ai-night", "AI Night Munich", "excluded_site"),
        ("https://www.eventbrite.de/d/germany--munich/ai/", "AI events in Munich", "platform_page"),
        ("https://wine.example/tasting", "Wine tasting Munich", "off_topic"),
        ("https://ai.example/berlin", "AI meetup Berlin", "not_in_munich"),
        ("ftp://ai.example/munich", "AI Munich", "not_a_web_page"),
    ],
)
def test_a_hit_becomes_an_event_or_an_organisation(profile, url, title, made):
    found = gate.candidate(_hit(url, title), "q", profile, Judge(profile))
    if isinstance(made, str):
        assert found == made
    else:
        assert (found.kind, found.events_url) == made


def test_an_organisation_candidate_is_the_site_or_its_platform_page(profile):
    judge = Judge(profile)
    site = gate.candidate(_hit("https://www.aimunich.org/events/talk"), "q", profile, judge)
    group = gate.candidate(_hit("https://www.meetup.com/munich-ai/"), "q", profile, judge)
    assert (site.url, site.site) == ("https://www.aimunich.org/", "aimunich.org")
    assert group.site == "meetup.com/munich-ai"


def test_the_queries_rotate_through_the_list(profile):
    few = replace(profile.search, queries=("a", "b", "c", "d", "e"), daily_cap=2)
    days = [search.todays_queries(few, date.fromordinal(DAY.toordinal() + i)) for i in range(3)]
    assert {q for day in days for q in day} == {"a", "b", "c", "d", "e"}
    assert all(len(day) == 2 for day in days)


def _tavily(results: dict[str, list[dict]], status: dict[str, int] | None = None, asked=None):
    def handler(request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content)["query"]
        if asked is not None:
            asked.append(query)
        if status and query in status:
            return httpx.Response(status[query], text="refused")
        return httpx.Response(200, json={"results": results.get(query, [])})

    return Tavily("tvly-test", (), httpx.MockTransport(handler))


def _result(url: str, title: str, score: float = 0.5) -> dict:
    return {"url": url, "title": title, "content": "A talk in Munich.", "score": score}


def _profile(profile, queries: tuple[str, ...], cap: int = 10, candidates: int = 5):
    return replace(
        profile,
        search=replace(profile.search, queries=queries, daily_cap=cap, daily_candidates=candidates),
    )


def test_only_what_the_radar_does_not_know_waits_for_review(store, profile):
    known_org = store.organisers.add("Known", "known.example", "seed")
    store.sources.add("https://known.example/events", "listing", "OWN", 24, "seed", known_org)
    store.sources.add("https://lu.ma/tracked-night", "event_page", "TICKET", 72, "seed")
    rejected = store.intakes.propose(IntakeKind.ORGANISATION, "https://said-no.example/", None, "x")
    store.intakes.update(rejected, state=IntakeState.REJECTED)
    results = {
        "AI Munich": [
            _result("https://new-club.example/events", "AI meetup Munich", 0.9),
            _result("https://known.example/talks/llm", "LLM talk Munich"),
            _result("https://said-no.example/ai", "AI day Munich"),
            _result("https://lu.ma/tracked-night", "AI Night Munich"),
            _result("https://lu.ma/fresh-night", "AI Night Munich", 0.7),
            _result("https://new-club.example/other", "AI meetup Munich", 0.4),
        ]
    }
    summary = search.run(
        store, _profile(profile, ("AI Munich",)), _tavily(results), DAY, dry_run=False
    )
    proposed = [i for i in store.intakes.all() if i["state"] == IntakeState.REVIEW]
    assert [(i["kind"], i["url"]) for i in proposed] == [
        ("organisation", "https://new-club.example/"),
        ("event", "https://lu.ma/fresh-night"),
    ]
    row = store.intakes.get(proposed[0]["id"])
    assert row["submitted_by"] is None and row["events_url"] == "https://new-club.example/events"
    assert row["log"][0]["text"] == 'Found by the web search for "AI Munich": AI meetup Munich'
    assert (summary["proposed"], summary["known_site"], summary["known_page"]) == (2, 2, 1)


def test_the_day_has_a_cap_on_searches_and_on_candidates(store, profile):
    results = {q: [_result(f"https://club-{q}.example/", "AI meetup Munich")] for q in "abc"}
    asked: list[str] = []
    capped = _profile(profile, ("a", "b", "c"), cap=2, candidates=1)
    first = search.run(store, capped, _tavily(results, asked=asked), DAY, dry_run=False)
    second = search.run(store, capped, _tavily(results, asked=asked), DAY, dry_run=False)
    assert len(asked) == 2 and first["searches"] == 2 and second["budget_spent"] == 1
    assert (first["proposed"], second.get("proposed", 0)) == (1, 0)


@pytest.mark.parametrize("status, failed", [(429, False), (401, True), (432, True)])
def test_a_refusal_ends_the_searching(store, profile, status, failed):
    asked: list[str] = []
    tavily = _tavily({}, status={"a": status}, asked=asked)
    summary = search.run(store, _profile(profile, ("a", "b")), tavily, DAY, dry_run=False)
    assert asked == ["a"] and summary["failed"] is failed


def test_a_dry_run_proposes_nothing_and_shows_every_verdict(store, profile):
    results = {
        "a": [
            _result("https://new.example/", "AI meetup Munich"),
            _result("https://wine.example/", "Wine tasting Munich"),
        ]
    }
    summary = search.run(store, _profile(profile, ("a",)), _tavily(results), DAY, dry_run=True)
    assert store.intakes.all() == []
    verdicts = {h["url"]: h["verdict"] for h in summary["hits"]}
    assert verdicts == {
        "https://wine.example/": "off_topic",
        "https://new.example/": "organisation",
    }
