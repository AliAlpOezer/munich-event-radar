"""Jobs on GitHub Actions (ADR 0006): an intake becoming 'queued' dispatches the intake
workflow, a drain works the queue and exits, store locks serialise jobs, and page bodies
live in the store until nothing can read them again."""

import json
import re
import threading
import time
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from event_radar.extract import Extractor, judgments
from event_radar.extract.router import Model, Provider, Router
from event_radar.fetch import Fetcher
from event_radar.harvester import Harvester
from event_radar.intake.service import IntakeService
from event_radar.model import IntakeKind, JudgedEvent, Verdict
from event_radar.store import database

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


def test_a_drain_of_one_kind_leaves_the_other_kinds_work_alone(store, profile):
    # Event intakes are drained in a job of their own, beside a long organisation intake.
    profiling = _insert(store, "profiling")
    organisation = _insert(store, "queued")
    event = store.intakes.create_event(
        "https://example.org/events/ai-night", date.today(), "Munich"
    )
    service = IntakeService(store, profile)
    worked: list[int] = []

    def process(intake_id: int) -> None:
        worked.append(intake_id)
        store.intakes.update(intake_id, state="done")

    service.process = process
    assert store.intakes.queued(IntakeKind.ORGANISATION) == 2
    assert service.drain(IntakeKind.EVENT) == 1
    assert worked == [event]
    assert store.intakes.get(profiling)["state"] == "profiling"
    assert service.drain(IntakeKind.ORGANISATION) == 2
    assert worked == [event, profiling, organisation]
    assert store.intakes.queued(IntakeKind.ORGANISATION) == 0


class _Counting:
    """A connection that notes every statement it sends: from a runner in the US each one
    is a round trip, and an executemany goes as one pipeline."""

    def __init__(self, db, asked: list):
        self.db, self.asked = db, asked

    def execute(self, query, *args):
        self.asked.append(query)
        return self.db.execute(query, *args)

    def executemany(self, query, *args):
        self.asked.append(query)
        return self.db.executemany(query, *args)

    def cursor(self):
        return _Counting(self.db.cursor(), self.asked)

    def __getattr__(self, name):
        return getattr(self.db, name)


def test_opening_a_store_that_is_up_to_date_asks_one_question(store):
    # Every job opens the store.
    with store.tx() as db:
        asked = []
        database._migrate(_Counting(db, asked), store.schema, store.api)
        assert len(asked) == 1
        db.execute("DELETE FROM api_build")
        database._migrate(_Counting(db, []), store.schema, store.api)
        assert db.execute("SELECT count(*) AS n FROM api_build").fetchone()["n"] == 1


def test_judging_a_board_of_twelve_events_asks_no_more_than_one_of_two(store, profile, monkeypatch):
    # An added event has the whole board judged again while its person waits.
    pages = {
        f"/events/{n}": lambda n=n: _event(
            f"Talk on AI agents, part {n}" if n % 2 else f"Salsa night {n}",
            f"2030-11-{n + 1:02d}T18:00:00+01:00",
        )
        for n in range(12)
    }
    shown = dict(list(pages.items())[:2])
    harvester, _ = _site(store, profile, shown)
    real = store.tx

    @contextmanager
    def counting():
        with real() as db:
            yield _Counting(db, asked)

    def judged() -> int:
        harvester.harvest_due()
        asked.clear()
        with monkeypatch.context() as patch:
            patch.setattr(store, "tx", counting)
            harvester.judge_events()
        return len(asked)

    asked: list = []
    two = judged()
    for path in list(pages)[2:]:
        shown[path] = pages[path]
        store.sources.add(f"https://example.org{path}", "event_page", "OWN", 72, "test")
    assert judged() == two
    events = store.events.all()
    assert len(events) == 6 and {e["tier"] for e in events} & {"A", "B"}


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
    for path in ("a", "b"):
        store.sources.add(f"https://example.org/{path}", "event_page", "OWN", 72, "test")
    store.pages.record("https://example.org/a", b"a, old")
    store.pages.record("https://example.org/a", b"a, latest")
    waiting = store.pages.record("https://example.org/b", b"b, waiting for the llm")
    store.pages.record("https://example.org/b", b"b, latest")
    store.extraction.enqueue(waiting, "https://example.org/b", priority=0)
    # A page that is no source, such as one an event intake refused, is fetched no more.
    store.pages.record("https://example.org/refused", b"refused")
    _age_bodies(store)
    store.pages.save(b"just stored by another job")

    assert store.pages.prune() == 2
    assert _kept(store) == {
        b"a, latest",
        b"b, waiting for the llm",
        b"b, latest",
        b"just stored by another job",
    }


def test_robots_txt_does_not_make_the_first_page_wait(store):
    # Every event intake waited the whole interval between robots.txt and its page.
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="")))
    fetcher = Fetcher(store, "test-agent", min_interval_s=0.5, client=client)
    started = time.monotonic()
    assert fetcher.get("https://example.org/a").ok
    assert time.monotonic() - started < 0.4
    assert fetcher.get("https://example.org/b").ok
    assert time.monotonic() - started >= 0.5


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
    org = store.organisers.add("Example", "example.org", "test")
    for path in pages:
        store.sources.add(f"https://example.org{path}", "listing", "OWN", 24, "test", org)
    harvester = Harvester(
        store, profile, Fetcher(store, "test-agent", min_interval_s=0, client=client)
    )
    harvester.harvest_due()
    assert harvester.counts["fetched"] == 2
    followed = {s["url"] for s in store.sources.all()}
    assert {"https://example.org/events/1", "https://example.org/events/2"} <= followed


def test_a_page_without_an_organiser_follows_no_links(store, profile):
    # ADR 0014 D4: an added event's page puts none of its links on the radar.
    luma = (Path(__file__).parent / "fixtures" / "luma-event.html").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=luma, headers={"content-type": "text/html"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    org = store.organisers.add("Example AI", "luma.com/example", "test")
    added, tracked = "https://luma.com/example-ai-night", "https://luma.com/example-ai-night-3"
    store.sources.add(added, "event_page", "TICKET", 72, "intake:1")
    store.sources.add(tracked, "event_page", "TICKET", 72, "test", org)
    harvester = Harvester(
        store, profile, Fetcher(store, "test-agent", min_interval_s=0, client=client)
    )
    harvester.harvest_due()
    assert harvester.counts["fetched"] == 2
    followed = {
        s["url"]: s["organiser_id"]
        for s in store.sources.all()
        if s["added_via"].startswith("link:")
    }
    assert followed == {"https://luma.com/example-ai-night-2": org}


TALK = "https://example.org/events/talk"


def _judged(i: int, about_ai: bool, ai: str | None = None) -> dict:
    topics = {"startup": False, "career": False, "tech": False}
    kind = "talk" if about_ai else "other"
    return {
        "id": i,
        "ai": ai or ("core" if about_ai else "none"),
        **topics,
        "kind": kind,
        "audience": "adults",
    }


class _Answers:
    """An LLM stand-in. It judges events about AI unless their line names one of
    `unrelated`, or cannot tell when it names one of `unclear`, names a page's talks as its
    events, and keeps what it was asked."""

    def __init__(self, unrelated: tuple[str, ...] = (), unclear: tuple[str, ...] = ()):
        self.unrelated = unrelated
        self.unclear = unclear
        self.asked: list[str] = []
        self.sorted: list[list[str]] = []

    def complete(self, messages, schema, name="events", first=None):
        question = messages[-1]["content"]
        if name == "judgments":
            lines = question.splitlines()
            self.sorted.append(lines)
            judged = [
                _judged(
                    i,
                    not any(u in line for u in self.unrelated),
                    "unknown" if any(u in line for u in self.unclear) else None,
                )
                for i, line in enumerate(lines)
            ]
            return "test-model", {"judgments": judged}
        self.asked.append(question)
        titles = [t.strip() for t in re.findall(r"Talk on [\w ]+", question)]
        return "test-model", {"events": [{"title": {"value": t, "quote": t}} for t in titles]}


def _page(title: str, nonce: str) -> bytes:
    return f'<h1>{title}</h1><p>Room 1</p><script>var nonce = "{nonce}";</script>'.encode()


def _talk_site(store, profile, versions: list[bytes]) -> tuple[Harvester, _Answers]:
    """A harvester over one event page that serves the next of `versions` on each fetch."""
    bodies = iter(versions)
    return _site(store, profile, {"/events/talk": lambda: next(bodies)})


def _site(
    store, profile, pages: dict, unrelated: tuple[str, ...] = (), unclear: tuple[str, ...] = ()
):
    """A harvester over event pages: path to a function giving the body of each fetch."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path not in pages:
            return httpx.Response(404)
        body = pages[request.url.path]()
        return httpx.Response(200, content=body, headers={"content-type": "text/html"})

    for path in pages:
        store.sources.add(f"https://example.org{path}", "event_page", "OWN", 72, "test")
    answers = _Answers(unrelated, unclear)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetcher = Fetcher(store, "test-agent", min_interval_s=0, client=client)
    return Harvester(store, profile, fetcher, extractor=Extractor(answers, store)), answers


def _ticketed(name: str, start: str, about: str) -> bytes:
    """An event page whose structured data has its tickets, so it never goes to the LLM
    queue, and whose text says what it is about."""
    event = {
        "@context": "https://schema.org",
        "@type": "Event",
        "name": name,
        "startDate": start,
        "location": {"@type": "Place", "name": "Audimax", "address": "Arcisstr. 21, 80333 München"},
        "offers": {"@type": "Offer", "price": "0", "url": "https://example.org/register"},
    }
    script = f"<script type='application/ld+json'>{json.dumps(event)}</script>"
    return f"<html><body><h1>{name}</h1><p>{about}</p>{script}</body></html>".encode()


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


SHAPELESS = {"events": [{"title": "Talk on 5 November", "venue": "Room 1"}]}


def test_a_model_answering_out_of_shape_hands_over_to_the_next(store, profile):
    """The harvest of 2026-10-19 crashed on an answer giving an event's fields as bare
    strings instead of each value with its quote."""
    talk = {"title": {"value": "Talk on 5 November", "quote": "Talk on 5 November"}}
    replies = iter([SHAPELESS, {"events": [talk]}])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "first"}]})
        body = json.loads(request.content)
        if body.get("response_format", {}).get("json_schema", {}).get("name") == "judgments":
            answer = {"judgments": [_judged(0, True)]}
        else:
            answer = next(replies)
        message = {"content": json.dumps(answer)}
        return httpx.Response(200, json={"choices": [{"message": message}]})

    models = (Model("first", "schema"), Model("openrouter/free", "json_object"))
    router = Router(
        [Provider("openrouter", "https://llm.example", "k", 10, models)],
        store,
        httpx.MockTransport(handler),
    )
    harvester, _ = _talk_site(store, profile, [_page("Talk on 5 November", "a")])
    harvester.extractor = Extractor(router, store)
    harvester.harvest_due()
    harvester.extract()
    [seen] = store.observations.all()
    assert seen.method == "llm:openrouter/free@events-v2"
    assert seen.fields["title"].value == "Talk on 5 November"


def test_an_answer_cached_out_of_shape_is_asked_again(store, profile):
    versions = [_page("Talk on 5 November", "a"), _page("Talk on 5 November", "b")]
    harvester, answers = _talk_site(store, profile, versions)
    harvester.harvest_due()
    harvester.extract()
    with store.tx() as db:
        db.execute(
            "UPDATE extraction SET result = %s WHERE version = 'events-v2'",
            (json.dumps({"model": "test-model", "result": SHAPELESS}),),
        )
    _due_again(store)
    harvester.harvest_due()
    harvester.extract()
    assert len(answers.asked) == 2
    titles = [o.fields["title"].value for o in store.observations.all()]
    assert titles == ["Talk on 5 November", "Talk on 5 November"]


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


def test_a_past_off_topic_event_is_not_asked_about_and_not_stored(store, profile):
    pages = {
        "/events/dance": lambda: _event("TUM tanzt!", "2020-05-18T14:00:00+02:00"),
        "/events/past": lambda: _event("Talk on AI agents", "2020-03-01T18:00:00+01:00"),
    }
    harvester, answers = _site(store, profile, pages)
    harvester.harvest_due()
    harvester.extract()
    harvester.judge_events()
    assert answers.sorted == [] and answers.asked == []
    assert set(_states(store).values()) == {"skipped"}
    assert [(e["fields"]["title"]["value"], e["drop_reason"]) for e in store.events.all()] == [
        ("Talk on AI agents", "past")
    ]
    assert harvester.counts["tier_off_topic"] == 1


TALK = "Campus Speakers Series x Example Retail Group"


def test_every_upcoming_event_is_judged_once_and_its_judgment_keeps_it(store, profile):
    pages = {
        "/events/talk": lambda: _ticketed(TALK, "2030-10-26T17:30:00+01:00", "AI and work."),
        "/events/past": lambda: _ticketed(TALK, "2020-10-26T17:30:00+01:00", "AI and play."),
        "/events/salsa": lambda: _ticketed("Salsa", "2030-10-27T19:00:00+01:00", "Dance."),
    }
    harvester, answers = _site(store, profile, pages, unrelated=("Salsa",))
    harvester.harvest_due()
    harvester.judge_events()
    # Without a judgment the words decide, as before ADR 0018.
    assert store.events.all() == [] and harvester.counts["judgment_pending"] == 2
    harvester.extract()
    harvester.ask_judgments()
    [batch] = answers.sorted
    assert len(batch) == 2 and not any("2020" in line for line in batch)
    assert answers.asked == []
    harvester.judge_events()
    [event] = store.events.all()
    assert (event["fields"]["title"]["value"], event["tier"]) == (TALK, "B")
    assert "judged: no topic" in event["tier_reasons"]
    harvester.ask_judgments()
    assert len(answers.sorted) == 1


def test_an_event_whose_page_says_too_little_is_judged_by_its_words(store, profile):
    pages = {
        "/events/night": lambda: _ticketed("AI Night #29", "2030-10-26T18:00:00+01:00", "Join."),
        "/events/stats": lambda: _ticketed(
            "Colloquium", "2030-10-27T16:00:00+01:00", "A statistical test for latent distance."
        ),
    }
    harvester, _ = _site(store, profile, pages, unrelated=("Colloquium",), unclear=("Night",))
    org = store.organisers.add("Example AI Institute", "example.org", "test", ai_focus=True)
    with store.tx() as db:
        db.execute("UPDATE source SET organiser_id = %s WHERE url LIKE '%%/stats'", (org,))
    harvester.harvest_due()
    harvester.ask_judgments()
    harvester.judge_events()
    # Its words keep the night; an AI-focused organiser's event its page says is not about
    # AI is C (ADR 0018 D9).
    tiers = {e["fields"]["title"]["value"]: e["tier"] for e in store.events.all()}
    assert tiers == {"AI Night #29": "B", "Colloquium": "C"}
    assert all(e["judgment_key"] for e in store.events.all())


def test_a_page_that_says_too_little_to_judge_is_extracted(store, profile):
    pages = {"/events/night": lambda: _event("Community night", "2030-11-05T18:00:00+01:00")}
    harvester, answers = _site(store, profile, pages, unclear=("Community",))
    harvester.harvest_due()
    harvester.extract()
    assert len(answers.sorted) == 1 and len(answers.asked) == 1
    assert _states(store) == {"/events/night": "done"}


def test_an_event_keeps_its_judgment_while_its_changed_page_waits_for_one(store, profile):
    abouts = iter(["AI and work.", "AI, work and a new speaker."])
    pages = {"/events/talk": lambda: _ticketed(TALK, "2030-10-26T17:30:00+01:00", next(abouts))}
    harvester, answers = _site(store, profile, pages)
    harvester.harvest_due()
    harvester.ask_judgments()
    harvester.judge_events()
    [before] = store.events.all()
    _due_again(store)
    with store.tx() as db:
        db.execute("UPDATE observation SET observed_at = observed_at - interval '1 day'")
    harvester.harvest_due()
    harvester.extractor = None
    harvester.judge_events()
    [after] = store.events.all()
    assert (after["key"], after["tier"]) == (before["key"], before["tier"]) == (after["key"], "B")
    assert after["judgment_key"] == before["judgment_key"]
    assert harvester.counts["judgment_pending"] == 1


def test_an_answer_to_an_older_question_holds_until_the_new_one_is_answered(
    store, profile, monkeypatch
):
    pages = {"/events/talk": lambda: _ticketed(TALK, "2030-10-26T17:30:00+01:00", "AI and work.")}
    harvester, answers = _site(store, profile, pages)
    harvester.harvest_due()
    harvester.ask_judgments()
    harvester.judge_events()
    [before] = store.events.all()
    monkeypatch.setattr(judgments, "VERSION", "judgments-next")
    harvester.extractor = None
    harvester.judge_events()
    [after] = store.events.all()
    assert (after["key"], after["tier"]) == (before["key"], before["tier"]) == (after["key"], "B")
    assert harvester.counts["judgment_pending"] == 1


def test_observations_made_before_judgments_get_their_candidates(store, profile):
    pages = {"/events/talk": lambda: _ticketed(TALK, "2030-10-26T17:30:00+01:00", "AI and work.")}
    harvester, answers = _site(store, profile, pages)
    harvester.harvest_due()
    with store.tx() as db:
        db.execute("UPDATE observation SET judgment_key = NULL")
        db.execute("DELETE FROM judgment")
    harvester.ask_judgments()
    [batch] = answers.sorted
    assert "AI and work." in batch[0]
    [seen] = store.observations.all()
    assert seen.judgment_key and store.judgments.answers([seen.judgment_key], judgments.VERSION)


def test_a_childrens_event_is_not_asked_about_and_not_stored(store, profile):
    pages = {
        "/events/agents": lambda: _event("Talk on AI agents", "2030-11-05T18:00:00+01:00"),
        "/events/kids": lambda: _event(
            "Children's University: How Do AI Agents Learn?", "2030-11-27T17:00:00+01:00"
        ),
    }
    harvester, answers = _site(store, profile, pages)
    harvester.harvest_due()
    harvester.extract()
    harvester.judge_events()
    [batch] = answers.sorted
    assert len(batch) == 1 and "Talk on AI agents" in batch[0] and len(answers.asked) == 1
    assert _states(store)["/events/kids"] == "skipped"
    assert [e["fields"]["title"]["value"] for e in store.events.all()] == ["Talk on AI agents"]
    assert harvester.counts["tier_audience"] == 1


def test_a_page_without_an_organiser_read_as_several_events_keeps_none(store, profile):
    # ADR 0014 D4: an added event's page holds one event, also when the daily run reads it.
    two = b"<h1>Talk on 5 November</h1><h1>Talk on 12 November</h1>"
    pages = {"/events/added": lambda: two, "/events/tracked": lambda: two}
    harvester, _ = _site(store, profile, pages)
    org = store.organisers.add("Example AI", "example.org", "test", ai_focus=True)
    with store.tx() as db:
        db.execute("UPDATE source SET organiser_id = %s WHERE url LIKE '%%/tracked'", (org,))
    harvester.harvest_due()
    harvester.extract()
    seen = sorted(
        (o.url.rsplit("/", 1)[1], o.fields["title"].value) for o in store.observations.all()
    )
    assert seen == [("tracked", "Talk on 12 November"), ("tracked", "Talk on 5 November")]
    assert set(_states(store).values()) == {"done"} and harvester.counts["llm_several"] == 1


def _star(store, key: str, accounts: list) -> None:
    with store.tx() as db:
        db.cursor().executemany(
            "INSERT INTO star (user_id, event_key) VALUES (%s, %s)", [(a, key) for a in accounts]
        )


def test_stars_on_events_that_merge_count_each_account_once(store, users):
    # ADR 0015 D4: every stored event the aliases name, counted as `Events.save` merges them.
    store.events.save(
        [JudgedEvent({alias}, {}, "url", Verdict("C", ()), []) for alias in ("obs:a", "obs:b")]
    )
    with store.tx() as db:
        rows = db.execute("SELECT alias, event_key FROM event_alias").fetchall()
    keys = {r["alias"]: r["event_key"] for r in rows}
    accounts = [users() for _ in range(4)]
    _star(store, keys["obs:a"], accounts[:3])
    _star(store, keys["obs:b"], accounts)
    assert store.events.star_counts([{"obs:a", "obs:b"}, {"obs:a"}, {"obs:c"}]) == [4, 3, 0]


def test_an_event_with_five_stars_rises_to_b_at_the_next_judging(store, profile, users):
    pages = {"/events/pitch": lambda: _event("Startup pitch night", "2030-11-05T18:00:00+01:00")}
    harvester, _ = _site(store, profile, pages)
    harvester.harvest_due()
    harvester.judge_events()
    [event] = store.events.all()
    assert event["tier"] == "C"
    _star(store, event["key"], [users() for _ in range(5)])
    harvester.judge_events()
    [event] = store.events.all()
    assert event["tier"] == "B" and event["tier_reasons"][-1] == "starred by 5 or more"
    with store.tx() as db:
        assert db.execute("SELECT cadence_hours FROM source").fetchone()["cadence_hours"] == 24
