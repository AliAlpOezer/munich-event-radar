import pytest
from fastapi.testclient import TestClient

from event_radar.model import IntakeState, JudgedEvent, Verdict
from event_radar.web import create_app


@pytest.fixture
def client(store):
    org = store.organisers.add("Munich NLP", "luma.com/munichnlp", "seed", ai_focus=True)
    store.sources.add("https://luma.com/munichnlp", "listing", "TICKET", 24, "seed", org)
    run = store.runs.start()
    store.events.save(
        [
            JudgedEvent(
                {"luma:evt-1"},
                {"title": {"value": "Munich NLP x JetBrains"}},
                "platform",
                Verdict("B", ("ai=core",)),
                ["https://luma.com/munichnlp"],
            )
        ]
    )
    store.runs.finish(run, "ok", {"tier_B": 1})
    return TestClient(create_app(store))


def test_events_carry_their_organisers_and_sources(client):
    [event] = client.get("/api/events").json()["events"]
    assert event["tier"] == "B" and event["user_status"] == "none"
    assert event["sources"] == [
        {"url": "https://luma.com/munichnlp", "class": "TICKET", "organiser_id": 1}
    ]
    assert event["organiser_ids"] == [1] and not event["stale"]
    [org] = client.get("/api/organisers").json()["organisers"]
    assert org["ai_focus"] and org["sources"][0]["url"] == "https://luma.com/munichnlp"
    assert client.get("/api/status").json()["runs"][0]["summary"] == {"tier_B": 1}


def test_user_status_follows_the_allowed_transitions(client):
    key = client.get("/api/events").json()["events"][0]["key"]
    assert client.post(f"/api/events/{key}/status", json={"status": "applied"}).status_code == 200
    assert client.get("/api/events").json()["events"][0]["user_status"] == "applied"
    client.post(f"/api/events/{key}/status", json={"status": "registered"})
    assert client.post(f"/api/events/{key}/status", json={"status": "skipped"}).status_code == 409
    assert client.post("/api/events/nope/status", json={"status": "applied"}).status_code == 404
    assert client.post(f"/api/events/{key}/status", json={"status": "bogus"}).status_code == 422


def test_page_and_assets_are_served(client):
    assert "Event Radar" in client.get("/").text
    script = client.get("/static/app.js")
    assert script.status_code == 200 and script.headers["cache-control"] == "no-cache"
    again = client.get("/static/app.js", headers={"If-None-Match": script.headers["etag"]})
    assert again.status_code == 304


def test_a_share_image_on_many_events_is_not_a_cover(store):
    tier_c = Verdict("C", ())
    images = ["https://a.org/own.png"] + ["https://b.org/site.png"] * 3
    fields = [{"title": {"value": f"E{i}"}, "image": {"value": u}} for i, u in enumerate(images)]
    store.events.save(
        [JudgedEvent({f"url:{i}"}, f, "url", tier_c, []) for i, f in enumerate(fields)]
    )
    events = TestClient(create_app(store)).get("/api/events").json()["events"]
    covers = {e["fields"]["title"]["value"]: e["cover"] for e in events}
    assert covers == {"E0": "https://a.org/own.png", "E1": None, "E2": None, "E3": None}


@pytest.fixture
def intakes(store):
    return TestClient(create_app(store, intakes_enabled=True))


@pytest.mark.parametrize("raw", ["", "not a url", "ftp://example.org", "javascript:alert(1)"])
def test_bad_urls_are_refused(intakes, raw):
    assert intakes.post("/api/intakes", json={"url": raw}).status_code == 422


def test_a_suggestion_is_decided_only_in_review(store, intakes):
    created = intakes.post("/api/intakes", json={"url": " cdtm.com "})
    assert created.status_code == 202 and created.json()["url"] == "https://cdtm.com"
    decide = f"/api/intakes/{created.json()['id']}/decision"
    assert intakes.post(decide, json={"approve": True}).status_code == 409
    store.intakes.update(created.json()["id"], state=IntakeState.REVIEW)
    assert intakes.post(decide, json={"approve": True}).json()["state"] == "queued"
    assert intakes.post("/api/intakes/999/decision", json={"approve": True}).status_code == 404
    store.organisers.add("CDTM", "cdtm.com", "manual")
    assert intakes.post("/api/intakes", json={"url": "https://www.cdtm.com/"}).status_code == 409


def test_without_the_service_adding_is_disabled(store):
    client = TestClient(create_app(store))
    assert client.get("/api/intakes").json() == {"enabled": False, "intakes": []}
    assert client.post("/api/intakes", json={"url": "example.org"}).status_code == 503
