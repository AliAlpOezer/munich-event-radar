"""Jobs on GitHub Actions (ADR 0006): an intake becoming 'queued' dispatches the intake
workflow, a drain works the queue and exits, store locks serialise jobs, and page bodies
live in the store until nothing can read them again."""

import json
import re
import threading
import time
from datetime import UTC, datetime

import httpx
import pytest

from event_radar.extract import Extractor
from event_radar.fetch import Fetcher
from event_radar.harvester import Harvester
from event_radar.intake.service import IntakeService

DISPATCH_URL = "https://api.github.com/repos/owner/repo/actions/workflows/intake.yml/dispatches"


def _insert(store, state: str) -> int:
    with store.tx() as db:
        return db.execute(
            "INSERT INTO intake (url, state, created_at, updated_at)"
            " VALUES ('https://example.org/', %s, now(), now()) RETURNING id",
            (state,),
        ).fetchone()["id"]


@pytest.fixture
def dispatches(store):
    """Configures the dispatch secrets; yields a function returning the requests sent."""

    def sent() -> list[dict]:
        with store.tx() as db:
            return db.execute("SELECT url, headers, body FROM net.http_request_queue").fetchall()

    with store.tx() as db:
        db.execute("DELETE FROM net.http_request_queue")
        db.execute(
            "INSERT INTO vault.decrypted_secrets VALUES"
            " ('intake_dispatch_url', %s), ('intake_dispatch_token', 'token-1')",
            (DISPATCH_URL,),
        )
    yield sent
    with store.tx() as db:
        db.execute("DELETE FROM vault.decrypted_secrets WHERE name LIKE 'intake_dispatch_%'")
        db.execute("DELETE FROM net.http_request_queue")


def test_only_becoming_queued_dispatches_the_workflow(store, dispatches):
    queued = store.intakes.create("https://example.org/", None, None)
    [request] = dispatches()
    assert request["url"] == DISPATCH_URL and request["body"] == {"ref": "main"}
    assert request["headers"]["Authorization"] == "Bearer token-1"

    waiting = _insert(store, "review")
    store.intakes.update(queued, state="checking")
    store.intakes.update(queued, name="renamed")
    assert len(dispatches()) == 1

    store.intakes.update(waiting, state="queued", approved=True)
    assert len(dispatches()) == 2


def test_without_the_secrets_nothing_is_sent(store):
    with store.tx() as db:
        db.execute("DELETE FROM net.http_request_queue")
    store.intakes.create("https://example.org/", None, None)
    with store.tx() as db:
        assert db.execute("SELECT count(*) AS n FROM net.http_request_queue").fetchone()["n"] == 0


def test_a_drain_works_the_queue_and_requeues_cut_off_work_first(store, profile):
    waiting = _insert(store, "review")
    cut_off = _insert(store, "harvesting")
    queued = _insert(store, "queued")
    service = IntakeService(store, profile)
    worked: list[int] = []

    def process(intake_id: int) -> None:
        worked.append(intake_id)
        store.intakes.update(intake_id, state="done")

    service.process = process
    assert service.drain() == 2
    assert worked == [cut_off, queued]
    assert store.intakes.get(waiting)["state"] == "review"
    assert service.drain() == 0


def test_a_second_holder_of_a_lock_waits_for_the_first(store):
    order: list[str] = []
    held = threading.Event()

    def first() -> None:
        with store.lock("judge"):
            held.set()
            time.sleep(0.5)
            order.append("first done")

    thread = threading.Thread(target=first)
    thread.start()
    assert held.wait(10)
    with store.lock("judge"):
        order.append("second in")
    thread.join(10)
    assert order == ["first done", "second in"]


def _age_bodies(store) -> None:
    with store.tx() as db:
        db.execute("UPDATE page_body SET stored_at = now() - interval '2 hours'")


def _kept(store) -> set[bytes]:
    with store.tx() as db:
        return {store.pages.body(r["content_hash"]) for r in db.execute("SELECT * FROM page_body")}


def test_pruning_keeps_what_a_fetch_or_the_llm_can_still_read(store):
    store.pages.record("https://example.org/a", b"a, old")
    store.pages.record("https://example.org/a", b"a, latest")
    waiting = store.pages.record("https://example.org/b", b"b, waiting for the llm")
    store.pages.record("https://example.org/b", b"b, latest")
    store.extraction.enqueue(waiting, "https://example.org/b", priority=0)
    _age_bodies(store)
    store.pages.save(b"just stored by another job")

    assert store.pages.prune() == 1
    assert _kept(store) == {
        b"a, latest",
        b"b, waiting for the llm",
        b"b, latest",
        b"just stored by another job",
    }


def test_a_fetch_whose_body_is_gone_asks_for_the_whole_page(store):
    sent: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        sent.append(request.headers)
        return httpx.Response(200, text="<html>page</html>", headers={"etag": '"v1"'})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetcher = Fetcher(store, "test-agent", min_interval_s=0, client=client)
    url = "https://example.org/events"
    assert fetcher.get(url).ok
    assert fetcher.get(url).ok
    assert sent[1]["if-none-match"] == '"v1"'

    with store.tx() as db:
        db.execute("DELETE FROM page_body")
    assert fetcher.get(url).ok
    assert "if-none-match" not in sent[2]
    assert store.pages.body(store.pages.latest(url)["content_hash"]) == b"<html>page</html>"


def test_a_malformed_link_on_a_page_does_not_stop_the_harvest(store, profile):
    pages = {
        "/a": b'<a href="http://[broken/x">x</a> <a href="/events/1">One</a>',
        "/b": b'<a href="/events/2">Two</a>',
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = pages.get(request.url.path)
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, content=body, headers={"content-type": "text/html"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    for path in pages:
        store.sources.add(f"https://example.org{path}", "listing", "OWN", 24, "test")
    harvester = Harvester(
        store, profile, Fetcher(store, "test-agent", min_interval_s=0, client=client)
    )
    harvester.harvest_due()
    assert harvester.counts["fetched"] == 2
    followed = {s["url"] for s in store.sources.all()}
    assert {"https://example.org/events/1", "https://example.org/events/2"} <= followed


TALK = "https://example.org/events/talk"


class _Answers:
    """An LLM stand-in. It sorts events as related unless their line names one of
    `unrelated`, names a page's talk as its event, and keeps what it was asked."""

    def __init__(self, unrelated: tuple[str, ...] = ()):
        self.unrelated = unrelated
        self.asked: list[str] = []
        self.sorted: list[list[str]] = []

    def complete(self, messages, schema, name="events"):
        question = messages[-1]["content"]
        if name == "verdicts":
            lines = question.splitlines()
            self.sorted.append(lines)
            verdicts = [
                {"id": i, "relevant": not any(u in line for u in self.unrelated)}
                for i, line in enumerate(lines)
            ]
            return "test-model", {"verdicts": verdicts}
        self.asked.append(question)
        title = re.search(r"Talk on [\w ]+", question).group(0).strip()
        return "test-model", {"events": [{"title": {"value": title, "quote": title}}]}


def _page(title: str, nonce: str) -> bytes:
    return f'<h1>{title}</h1><p>Room 1</p><script>var nonce = "{nonce}";</script>'.encode()


def _talk_site(store, profile, versions: list[bytes]) -> tuple[Harvester, _Answers]:
    """A harvester over one event page that serves the next of `versions` on each fetch."""
    bodies = iter(versions)
    return _site(store, profile, {"/events/talk": lambda: next(bodies)})


def _site(store, profile, pages: dict, unrelated: tuple[str, ...] = ()):
    """A harvester over event pages: path to a function giving the body of each fetch."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path not in pages:
            return httpx.Response(404)
        body = pages[request.url.path]()
        return httpx.Response(200, content=body, headers={"content-type": "text/html"})

    for path in pages:
        store.sources.add(f"https://example.org{path}", "event_page", "OWN", 72, "test")
    answers = _Answers(unrelated)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetcher = Fetcher(store, "test-agent", min_interval_s=0, client=client)
    return Harvester(store, profile, fetcher, extractor=Extractor(answers, store)), answers


def _event(name: str, start: str) -> bytes:
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
    }
    script = f"<script type='application/ld+json'>{json.dumps(event)}</script>"
    return f"<html><body><h1>{name}</h1>{script}</body></html>".encode()


def _states(store) -> dict[str, str]:
    with store.tx() as db:
        rows = db.execute("SELECT url, state FROM extraction_job").fetchall()
    return {r["url"].removeprefix("https://example.org"): r["state"] for r in rows}


def _due_again(store) -> None:
    with store.tx() as db:
        db.execute("UPDATE source SET last_fetched_at = last_fetched_at - interval '4 days'")


def test_only_the_current_version_of_a_page_goes_to_the_llm(store, profile):
    versions = [_page("Talk on 5 November", "a"), _page("Talk on 12 November", "b")]
    harvester, answers = _talk_site(store, profile, versions)
    harvester.harvest_due()
    _due_again(store)
    harvester.harvest_due()
    harvester.extract()
    assert len(answers.asked) == 1 and "Talk on 12 November" in answers.asked[0]
    assert store.extraction.pending(10) == []


def test_a_page_whose_text_is_unchanged_is_not_asked_about_again(store, profile):
    versions = [_page("Talk on 5 November", "a"), _page("Talk on 5 November", "b")]
    harvester, answers = _talk_site(store, profile, versions)
    harvester.harvest_due()
    harvester.extract()
    _due_again(store)
    harvester.harvest_due()
    harvester.extract()
    assert len(answers.asked) == 1 and len(answers.sorted) == 1
    titles = [o.fields["title"].value for o in store.observations.all()]
    assert titles == ["Talk on 5 November", "Talk on 5 November"]


def test_the_llm_queue_stops_at_its_deadline(store, profile):
    harvester, answers = _talk_site(store, profile, [_page("Talk on 5 November", "a")])
    harvester.harvest_due()
    harvester.extract(until=datetime.now(UTC))
    assert answers.asked == [] and len(store.extraction.pending(10)) == 1


def test_a_source_is_due_a_few_hours_before_its_cadence_passes(store):
    for name, hours in (("early", 20), ("recent", 10)):
        store.sources.add(f"https://example.org/{name}", "listing", "OWN", 24, "test")
        with store.tx() as db:
            db.execute(
                "UPDATE source SET last_fetched_at = now() - make_interval(hours => %s)"
                " WHERE url = %s",
                (hours, f"https://example.org/{name}"),
            )
    assert [s["url"] for s in store.sources.due()] == ["https://example.org/early"]


def test_only_upcoming_events_about_ai_go_to_extraction(store, profile):
    pages = {
        "/events/past": lambda: _event("Talk on AI agents", "2020-03-01T18:00:00+01:00"),
        "/events/museums": lambda: _event("Long Night of the Museums", "2030-10-17T18:00:00+02:00"),
        "/events/agents": lambda: _event("Talk on AI agents", "2030-11-05T18:00:00+01:00"),
    }
    harvester, answers = _site(store, profile, pages, unrelated=("Museums",))
    harvester.harvest_due()
    harvester.extract()
    # The judge finds the museum night off topic by its title alone; the LLM still decides.
    [batch] = answers.sorted
    assert len(batch) == 2 and not any("2020" in line for line in batch)
    assert len(answers.asked) == 1 and "Talk on AI agents" in answers.asked[0]
    assert _states(store) == {
        "/events/past": "skipped",
        "/events/museums": "skipped",
        "/events/agents": "done",
    }
    assert (harvester.counts["llm_skipped"], harvester.counts["llm_unrelated"]) == (1, 1)


def test_identical_events_on_two_pages_are_sorted_once(store, profile):
    same = _event("Long Night of the Museums", "2030-10-17T18:00:00+02:00")
    pages = {"/events/a": lambda: same, "/events/b": lambda: same}
    harvester, answers = _site(store, profile, pages, unrelated=("Museums",))
    harvester.harvest_due()
    harvester.extract()
    assert [len(batch) for batch in answers.sorted] == [1]
    assert answers.asked == [] and set(_states(store).values()) == {"skipped"}


def test_a_startup_organisers_events_stay_and_off_topic_ones_are_not_stored(store, profile):
    pages = {
        "/events/talk": lambda: _page("Talk on 5 November", "a"),
        "/events/museums": lambda: _event("Long Night of the Museums", "2030-10-17T18:00:00+02:00"),
    }
    harvester, answers = _site(store, profile, pages, unrelated=("Museums",))
    org = store.organisers.add("UnternehmerTUM", "example.org", "test", kind="startup")
    with store.tx() as db:
        db.execute("UPDATE source SET organiser_id = %s WHERE url LIKE '%%/talk'", (org,))
    harvester.harvest_due()
    harvester.extract()
    harvester.judge_events()
    [batch] = answers.sorted
    assert len(batch) == 1 and "Museums" in batch[0] and len(answers.asked) == 1
    assert [(e["fields"]["title"]["value"], e["tier"]) for e in store.events.all()] == [
        ("Talk on 5 November", "C")
    ]
    assert harvester.counts["tier_off_topic"] == 1
