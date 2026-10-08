from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from event_radar.store import Store
from event_radar.web import create_app


@pytest.fixture
def client(tmp_path):
    store = Store(tmp_path / "radar.db")
    org = store.add_organiser("Munich NLP", "luma.com/munichnlp", "seed", ai_focus=True)
    store.add_source("https://luma.com/munichnlp", "listing", "TICKET", 24, "seed", org)
    run = store.start_run()
    store.save_events(
        [
            (
                {"luma:evt-1"},
                {"title": {"value": "Munich NLP x JetBrains"}},
                "platform",
                SimpleNamespace(tier="B", reasons=("ai=core",), drop_reason=None),
                ["https://luma.com/munichnlp"],
            )
        ]
    )
    store.finish_run(run, "ok", {"tier_B": 1})
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
    assert client.get("/static/app.js").status_code == 200


def test_a_share_image_on_many_events_is_not_a_cover(tmp_path):
    store = Store(tmp_path / "radar.db")
    tier_c = SimpleNamespace(tier="C", reasons=(), drop_reason=None)
    images = ["https://a.org/own.png"] + ["https://b.org/site.png"] * 3
    fields = [{"title": {"value": f"E{i}"}, "image": {"value": u}} for i, u in enumerate(images)]
    store.save_events([({f"url:{i}"}, f, "url", tier_c, []) for i, f in enumerate(fields)])
    events = TestClient(create_app(store)).get("/api/events").json()["events"]
    covers = {e["fields"]["title"]["value"]: e["cover"] for e in events}
    assert covers == {"E0": "https://a.org/own.png", "E1": None, "E2": None, "E3": None}
