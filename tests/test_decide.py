import json

import httpx
import pytest

from event_radar.decide import calibrate, questions
from event_radar.decide.systemone import SystemOne
from event_radar.extract import key as extraction_key
from event_radar.extract import triage
from event_radar.html import text as page_text
from event_radar.model import ParsedEvent, SourceClass

FILLER = "Our institute hosts talks on many subjects every week. " * 12


def _server(reply: dict, seen: list | None = None, status: int = 200) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(status, json=reply)

    return httpx.MockTransport(handle)


def test_the_client_sends_one_state_with_its_questions_and_options():
    seen = []
    reply = {"answers": {"ai": {"type": "noul", "noul": 0.9}}, "usage": {"input_tokens": 41}}
    client = SystemOne(
        "http://model", "multilingual", options={"max_len": 8192}, transport=_server(reply, seen)
    )
    answer = client.ask({"title": "AI Night"}, {"ai": {"type": "noul", "instructions": "AI?"}})
    path, body = seen[0]
    assert path == "/v1/systemone"
    assert body == {
        "model": "multilingual",
        "state": {"title": "AI Night"},
        "questions": {"ai": {"type": "noul", "instructions": "AI?"}},
        "max_len": 8192,
    }
    assert answer.answers["ai"]["noul"] == 0.9
    assert answer.input_tokens == 41


def test_a_refused_request_raises():
    client = SystemOne("http://model", "kev", transport=_server({"detail": "too long"}, status=422))
    with pytest.raises(httpx.HTTPStatusError):
        client.ask("state", {"q": {"type": "noul", "instructions": "?"}})


def test_in_scope_asks_one_yes_or_no_per_scope_group(profile):
    asked = questions.in_scope(profile)
    assert set(asked) == {"ai", *profile.scope_groups, "kind"}
    assert all(q["type"] == "noul" for q in asked.values())
    assert "gründer" in asked["startup"]["instructions"]
    assert "career fair" in asked["kind"]["instructions"]
    assert "hackathon" in asked["kind"]["instructions"]


def test_each_registration_question_names_the_llm_fields_that_answer_it():
    asked = questions.registration()
    assert set(asked) == set(questions.REGISTRATION_FIELDS)
    named = {f for fields in questions.REGISTRATION_FIELDS.values() for f in fields}
    assert named == {
        "reg_mode",
        "reg_url",
        "reg_opens_at",
        "reg_closes_at",
        "price",
        "reg_availability",
    }


def test_passages_keep_the_text_around_registration_words(profile):
    text = f"{FILLER}Register   now for the AI Night. {FILLER}Tickets are free. {FILLER}"
    kept = questions.passages(text, profile.registration.words, 40)
    assert len(kept) == 2
    assert "Register now for the AI Night." in kept[0]
    assert len(kept[0]) == 40 + len("regist") + 40
    assert "Tickets are free." in kept[1]
    assert questions.passages(FILLER, profile.registration.words, 40) == []


def test_near_passages_merge():
    assert questions.passages("apply here and register there", ("apply", "regist"), 30) == [
        "apply here and register there"
    ]


def test_auroc_counts_ties_as_half():
    assert calibrate.auroc([0.9, 0.8], [0.1, 0.2]) == 1.0
    assert calibrate.auroc([0.5], [0.5]) == 0.5
    assert calibrate.auroc([0.9, 0.1], [0.5]) == 0.5
    assert calibrate.auroc([], [0.5]) is None


def test_a_band_skips_below_its_threshold_and_the_best_keeps_the_target_recall():
    rows = [
        {"label": True, "score": 0.9},
        {"label": True, "score": 0.3},
        {"label": False, "score": 0.25},
        {"label": False, "score": 0.04},
        {"label": False, "score": 0.01},
    ]
    bands = calibrate.bands(rows, (0.02, 0.05, 0.3, 0.5))
    assert bands[1] == {"lo": 0.05, "recall": 1.0, "false_skips": 0, "skipped": 2}
    assert bands[3] == {"lo": 0.5, "recall": 0.5, "false_skips": 1, "skipped": 4}
    assert calibrate.best(bands, 1.0) == bands[2]
    assert calibrate.best(bands[3:], 1.0) is None


def _page(store, url: str, html: str, kind: str = "event_page") -> tuple[str, bytes, str]:
    body = html.encode()
    store.sources.add(url, kind, SourceClass.OWN, 24, "seed")
    digest = store.pages.record(url, body, http_status=200, content_type="text/html")
    return digest, body, page_text(body, url)


def test_labels_come_from_the_cached_llm_answers(store, profile):
    # A structured event whose page the LLM read as it is stored: exact.
    stated = (
        '<html><head><title>AI Night</title><script type="application/ld+json">'
        '{"@context": "https://schema.org", "@type": "Event", "name": "AI Night",'
        ' "startDate": "2026-11-20T18:00:00+01:00", "location": {"@type": "Place",'
        ' "name": "Werk1", "address": "Munich"}}</script></head><body><h1>AI Night</h1>'
        f"<p>{FILLER}</p><p>Register now at https://example.org/signup</p></body></html>"
    )
    url = "https://example.org/ai-night"
    digest, body, text = _page(store, url, stated)
    answer = {
        "events": [
            {
                "title": {"value": "AI Night", "quote": "AI Night"},
                "reg_url": {
                    "value": "https://example.org/signup",
                    "quote": "Register now at https://example.org/signup",
                },
            }
        ]
    }
    store.extraction.cache(
        extraction_key(url, text), "events-v2", "m", {"model": "m", "result": answer}
    )
    candidate, _ = triage.candidate(url, body)
    store.extraction.cache(triage.key(candidate), triage.VERSION, "m", {"relevant": True})
    # A page whose text changed since the LLM read it, with its quote still there: earlier.
    changed = "https://example.org/talk"
    digest2, _, _ = _page(
        store, changed, f"<html><title>Talk</title><p>{FILLER}</p><p>Eintritt frei.</p></html>"
    )
    store.observations.add(digest2, changed, SourceClass.OWN, "llm:m@events-v2", [ParsedEvent({})])
    with store.tx() as db:
        db.execute(
            "UPDATE observation SET fields = %s WHERE url = %s",
            (json.dumps({"price": {"value": "free", "quote": "Eintritt frei."}}), changed),
        )
        db.execute(
            "INSERT INTO extraction_job (snapshot_hash, url, priority, state)"
            " VALUES (%s, %s, 0, 'done'), (%s, %s, 0, 'done')",
            (digest, url, digest2, changed),
        )
    # A page the LLM read without finding a field: an approximate negative.
    plain = "https://example.org/plain"
    digest3, _, _ = _page(store, plain, f"<html><title>Plain</title><p>{FILLER}</p></html>")
    with store.tx() as db:
        db.execute(
            "INSERT INTO extraction_job (snapshot_hash, url, priority, state)"
            " VALUES (%s, %s, 0, 'done')",
            (digest3, plain),
        )
    # Never read by the LLM: no label.
    _page(store, "https://example.org/unread", "<html><title>Unread</title></html>")

    items = calibrate.labelled(store.dsn, profile, store.schema)

    scope = [i for i in items if i.decision == questions.IN_SCOPE]
    assert [(i.url, i.label, i.basis) for i in scope] == [(url, True, "triage")]
    assert scope[0].state == candidate
    found = {i.url: i for i in items if i.variant == "passages"}
    assert set(found) == {url, changed, plain}
    assert (found[url].label, found[url].basis, found[url].d2_page) == (True, "exact", True)
    assert found[url].labels["signup"] and not found[url].labels["price"]
    assert found[url].quotes == (1, 1)
    assert found[url].state["event"]["title"] == "AI Night"
    assert "Register now at https://example.org/signup" in found[url].state["passages"][0]
    assert (found[changed].label, found[changed].basis) == (True, "earlier")
    assert found[changed].labels["price"]
    assert (found[plain].label, found[plain].basis) == (False, "approximate")
    assert found[plain].state == {"event": {"page": "Plain"}, "passages": []}
    whole = {i.url: i for i in items if i.variant == "text"}
    assert FILLER.strip() in whole[plain].state["text"]


def test_a_run_asks_each_item_once_and_keeps_no_page_text(store, profile, tmp_path):
    url = "https://example.org/talk"
    _, body, _ = _page(store, url, f"<html><title>Talk</title><p>{FILLER}</p></html>")
    candidate, _ = triage.candidate(url, body)
    store.extraction.cache(triage.key(candidate), triage.VERSION, "m", {"relevant": False})
    seen = []
    reply = {
        "answers": {q: {"type": "noul", "noul": 0.1} for q in questions.in_scope(profile)},
        "usage": {"input_tokens": 120},
    }
    client = SystemOne("http://model", "multilingual", transport=_server(reply, seen))

    summary = calibrate.run(
        store.dsn, profile, client, tmp_path, [questions.IN_SCOPE], schema=store.schema
    )

    assert len(seen) == 1
    rows = [json.loads(line) for line in (tmp_path / "rows.jsonl").read_text().splitlines()]
    assert rows[0]["url"] == url and rows[0]["score"] == 0.1 and rows[0]["tokens"] == 120
    assert "state" not in rows[0] and FILLER not in json.dumps(rows)
    group = summary["groups"]["in_scope-v1/candidate"]
    assert group["all"]["n"] == 1 and group["all"]["positives"] == 0
    assert json.loads((tmp_path / "summary.json").read_text()) == summary
