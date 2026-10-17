"""Jobs on GitHub Actions (ADR 0006): an intake becoming 'queued' dispatches the intake
workflow, a drain works the queue and exits, store locks serialise jobs, and page bodies
live in the store until nothing can read them again."""

import threading
import time

import httpx
import pytest

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
