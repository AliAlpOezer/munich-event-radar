import json
import sqlite3
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from conftest import TEST_DSN
from event_radar.extract.router import BudgetExhausted, DailyCapReached, Model, Router
from event_radar.extract.verify import verify
from event_radar.html import image_url
from event_radar.judge import Judge
from event_radar.model import Evidence, Kind, Observation, SourceClass
from event_radar.parse import parse
from event_radar.platforms import platform_key
from event_radar.profile import Profile
from event_radar.resolve import local_minute, resolve
from event_radar.store import Store

REPO = Path(__file__).resolve().parents[1]
NOW = datetime.fromisoformat("2026-10-07T12:00:00+02:00")


LUMA = "https://luma.com/example-ai-night"
CMS = "https://www.example-academy.org/en/events/view/trustworthy-machine-learning"


def page(name: str) -> bytes:
    return (REPO / "tests" / "fixtures" / name).read_bytes()


def obs(id, url, cls, keys=(), **fields):
    return Observation(
        id,
        f"h{id}",
        url,
        cls,
        "jsonld@1",
        list(keys),
        {k: Evidence(v) for k, v in fields.items()},
        f"2026-10-07T10:00:0{id}",
    )


@pytest.mark.parametrize(
    "url, key",
    [
        ("https://luma.com/8hqv1871", "luma:8hqv1871"),
        ("https://luma.com/pricing", None),
        ("https://pretix.eu/MDSI.TUM/career-fair-2026/", "pretix:MDSI.TUM/career-fair-2026"),
        ("https://pretix.eu/locale/set?locale=de", None),
        (
            "https://www.eventbrite.de/e/aistrategy-conference-tickets-1984493851531",
            "eventbrite:1984493851531",
        ),
    ],
)
def test_platform_key(url, key):
    assert platform_key(url) == key


def test_luma_page_carries_registration_state():
    result = parse(LUMA, "text/html", page("luma-event.html"), Kind.EVENT_PAGE)
    fields = {k: v.value for k, v in result.events[0].fields.items()}
    assert fields["reg_mode"] == "approval" and fields["spots_left"] == 14
    assert fields["reg_closes_at"] == "2026-11-04T22:59:00.000Z"
    assert "luma:evt-example1" in result.events[0].platform_keys
    assert not result.needs_llm


def test_cms_page_without_registration_goes_to_llm():
    assert parse(CMS, "text/html", page("cms-event.html"), Kind.EVENT_PAGE).needs_llm


def test_event_pages_keep_a_cover_image():
    luma = parse(LUMA, "text/html", page("luma-event.html"), Kind.EVENT_PAGE)
    assert luma.events[0].fields["image"].value.startswith("https://images.lumacdn.com/")
    cms = parse(CMS, "text/html", page("cms-event.html"), Kind.EVENT_PAGE)
    assert cms.events[0].fields["image"].value == (
        "https://www.example-academy.org/fileadmin/events/trustworthy-ml.jpg"
    )


def test_listing_pages_do_not_lend_their_share_image_to_events():
    body = b"""<meta property="og:image" content="https://example.org/site.png">
    <script type="application/ld+json">[{"@type": "Event", "name": "A", "startDate": "2026-11-02"},
    {"@type": "Event", "name": "B", "startDate": "2026-11-03", "image": ["/b.jpg"]}]</script>"""
    result = parse("https://example.org/events/", "text/html", body, Kind.LISTING)
    assert [e.fields.get("image") for e in result.events] == [
        None,
        Evidence("https://example.org/b.jpg"),
    ]


@pytest.mark.parametrize(
    "value, image",
    [
        ({"@type": "ImageObject", "url": "/a.png"}, "https://example.org/a.png"),
        ("http://example.org/a.png", None),
        ("javascript:alert(1)", None),
        ([], None),
    ],
)
def test_image_urls_are_https_only(value, image):
    assert image_url(value, "https://example.org/events/") == image


def test_verifier_rejects_ungrounded_and_flags_missing_year():
    page = "Bewerbungen bis 27. Oktober. Tickets: https://tally.so/r/BzWNEK"
    event, rejected = verify(
        {
            "title": {"value": "TUM.ai", "quote": "invented heading"},
            "reg_closes_at": {"value": "2026-10-27", "quote": "Bewerbungen bis 27. Oktober"},
            "reg_opens_at": {"value": "2026-10-01", "quote": "Bewerbungen bis 27. Oktober"},
            "reg_url": {"value": "https://tally.so/r/XXXX", "quote": "Tickets:"},
        },
        page,
    )
    assert set(rejected) == {"title", "reg_opens_at", "reg_url"}
    assert event.fields["reg_closes_at"].value == "2026-10-27"
    assert event.fields["reg_closes_at_year_stated"].value is False


def test_ticket_key_merges_and_wins_registration_over_aggregator():
    agg = obs(
        1,
        "https://mcml.ai/e",
        SourceClass.AGG,
        ["eventbrite:1"],
        title="AI@STRATEGY",
        starts_at="2026-10-07",
        reg_availability="open",
    )
    ticket = obs(
        2,
        "https://eventbrite.de/e/1",
        SourceClass.TICKET,
        ["eventbrite:1"],
        title="AI@STRATEGY Conference",
        reg_availability="sold_out",
    )
    [group] = resolve([agg, ticket])
    assert group.fields["reg_availability"]["value"] == "sold_out"
    assert group.fields["title"]["class"] == "TICKET"


def test_aggregator_only_is_unchecked_and_conflicting_keys_never_merge():
    a = obs(
        1, "https://agg/a", SourceClass.AGG, ["luma:aaa"], title="Demo Day", starts_at="2026-10-13"
    )
    b = obs(
        2, "https://agg/b", SourceClass.AGG, ["luma:bbb"], title="Demo Day", starts_at="2026-10-13"
    )
    groups = resolve([a, b])
    assert len(groups) == 2
    assert all(g.fields["reg_availability"]["value"] == "unchecked" for g in groups)


def test_a_platform_search_page_is_an_aggregator(profile):
    search = "https://www.eventbrite.com/d/germany--munich/ai/"
    assert profile.source_class(search) is SourceClass.AGG
    event = "https://www.eventbrite.com/e/ai-night-tickets-123456789"
    assert profile.source_class(event) is SourceClass.TICKET


def test_fuzzy_merge_on_same_date_and_title():
    own = obs(
        1,
        "https://tum.de/fair",
        SourceClass.OWN,
        title="Munich Career Fair AI and Data Science",
        starts_at="2026-10-29T14:00:00+01:00",
    )
    ticket = obs(
        2,
        "https://pretix.eu/x/y",
        SourceClass.TICKET,
        ["pretix:x/y"],
        title="Munich Career Fair AI & Data Science 2026",
        starts_at="2026-10-29T13:00:00+00:00",
    )
    [group] = resolve([own, ticket])
    assert group.merge_basis == "fuzzy"


def test_fuzzy_merge_when_short_title_stems_are_inside_long_title():
    badw = obs(
        1,
        "https://badw.de/en/events/view/a-cryptographic-perspective",
        SourceClass.OWN,
        title="Munich AI Lecture: A Cryptographic Perspective on Machine Learning"
        " Trustworthiness Challenges",
        starts_at="2026-10-20",
        venue="Bayerische Akademie der Wissenschaften",
    )
    tum = obs(
        2,
        "https://www.tum.de/en/news-and-events/events/details/cryptography-for-trustworthy-ai",
        SourceClass.OWN,
        title="Cryptography for Trustworthy AI",
        starts_at="2026-10-20T18:00:00+02:00",
        venue="Bavarian Academy of Sciences and Humanities (BAdW)",
    )
    [group] = resolve([badw, tum])
    assert group.merge_basis == "fuzzy"
    assert group.fields["starts_at"]["value"] == "2026-10-20T18:00:00+02:00"


def test_fuzzy_merge_needs_two_stems_and_compatible_start_times():
    def talk(id, title, start):
        return obs(id, f"https://tum.de/{id}", SourceClass.OWN, title=title, starts_at=start)

    one_stem = [
        talk(1, "Cryptographic", "2026-10-20"),
        talk(2, "Quantum Cryptography and Post-Quantum Security Day", "2026-10-20"),
    ]
    assert len(resolve(one_stem)) == 2
    sessions = [
        talk(1, "Info session: Searching for accommodation", "2026-10-15T10:00:00+02:00"),
        talk(2, "Info session: Searching for accommodation", "2026-10-15T14:00:00+02:00"),
    ]
    assert len(resolve(sessions)) == 2


def test_seed_reimport_applies_ai_focus_and_migrations_run_once(store):
    org = store.organisers.add("Munich NLP", "luma.com/munichnlp", "seed")
    store.sources.add("https://luma.com/munichnlp", "listing", "TICKET", 24, "seed", org)
    assert store.sources.focused_urls() == set()
    assert store.organisers.add("Munich NLP", "luma.com/munichnlp", "seed", ai_focus=True) == org
    assert store.sources.focused_urls() == {"https://luma.com/munichnlp"}
    again = Store(TEST_DSN, store.schema)
    assert again.sources.focused_urls() == {"https://luma.com/munichnlp"}
    again.close()


JUDGE = Judge(Profile.load(REPO / "profile.yaml"))


def verdict(ai_focus=True, **fields):
    return JUDGE.judge({k: {"value": v} for k, v in fields.items()}, NOW, ai_focus=ai_focus)


def test_judge_drops_only_by_logged_rule_and_never_on_price():
    assert verdict(title="AI meetup", starts_at="2026-10-01").drop_reason == "past"
    assert verdict(title="AI talk", attendance="online").drop_reason == "online_only"
    assert verdict(title="AI day", city="Berlin").drop_reason == "outside_munich"
    paid = verdict(title="AI conference", price="paid", city="München")
    assert paid.tier == "C" and paid.drop_reason is None
    assert verdict(title="Career Fair AI", address="81675 Munich").tier == "A"


def test_unknown_ai_relevance_surfaces_only_for_ai_focused_organisers():
    assert verdict(ai_focus=True, title="Monthly meetup #29").tier == "B"
    general = verdict(ai_focus=False, title="TUM tanzt!", venue="Garching Campus")
    assert general.tier == "C" and "organiser not AI-focused" in general.reasons[-1]
    assert verdict(ai_focus=False, title="TUM Blockchain Conference").tier == "C"
    assert verdict(ai_focus=False, title="Workshop on AI agents").tier == "B"
    assert verdict(ai_focus=False, title="HackaTUM for computer science").tier == "A"
    assert verdict(ai_focus=False, title="Firmenkontaktmesse IKOM").tier == "A"


def test_online_is_inferred_from_venue_or_meetup_description():
    zoom = "Join our virtual meetup.\nOnline. **[Register for the Zoom!](https://voxel51.com/x)**"
    meetup = verdict(title="Oct 14 - Advances in AI at SDSU", description=zoom)
    assert meetup.drop_reason == "online_only" and "register for the zoom" in meetup.reasons[0]
    assert verdict(title="AI info session", venue="Online").drop_reason == "online_only"
    assert verdict(title="AI talks", venue="Live stream on YouTube").drop_reason == "online_only"
    assert verdict(title="AI day", venue="Online & Munich City Campus").tier == "B"
    assert verdict(title="AI meetup", venue="TNG, 81925 München", description=zoom).tier == "B"
    hybrid = verdict(title="AI meetup", description="Join in person or online via the Zoom")
    assert hybrid.tier == "B"


def test_application_is_a_registration_mode_not_a_programme():
    assert (
        JUDGE.kind({"title": {"value": "From Prompts to Agents: Practical Applications"}}) is None
    )
    assert JUDGE.kind({"title": {"value": "Online Q&A sessions TUMexchange application"}}) is None
    assert JUDGE.kind({"title": {"value": "AI Fellowship 2027"}}) == "programme"


def _router(store, handler, cap=5):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    router = Router("k", store, cap, client)
    router._live = {m.id for m in (Model("nvidia/nemotron-3-super-120b-a12b:free", "schema"),)}
    return router


def _ok(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)}}]})


def test_router_skips_upstream_429_and_stops_on_account_cap(store):
    replies = iter([httpx.Response(429, text="rate-limited upstream"), _ok({"events": []})])
    model, result = _router(store, lambda r: next(replies)).complete([], {})
    assert model == "openrouter/free" and result == {"events": []}
    with pytest.raises(DailyCapReached):
        _router(store, lambda r: httpx.Response(429, text="free-models-per-day")).complete([], {})


def test_budget_is_a_ceiling(store):
    router = _router(store, lambda r: httpx.Response(503, text='{"error": "overloaded"}'), cap=1)
    with pytest.raises(BudgetExhausted):
        router.complete([], {})


def test_board_times_are_munich_local():
    assert local_minute("2026-10-13T16:30:00+00:00") == "2026-10-13T18:30"
    assert local_minute("2026-10-29T13:00:00Z") == "2026-10-29T14:00"
    assert local_minute("2026-10-20T18:00:00+02:00") == "2026-10-20T18:00"
    assert local_minute("2026-11-30") == "2026-11-30"
    assert local_minute(None) is None


def test_a_sqlite_database_moves_into_postgres_with_its_ids(store, tmp_path):
    from event_radar import sqlite_import

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        db.executescript(
            "CREATE TABLE organiser (id INTEGER PRIMARY KEY, name TEXT, domain TEXT, homepage TEXT,"
            " kind TEXT, ai_focus INTEGER, status TEXT, discovered_via TEXT, discovered_at TEXT,"
            " discovery_url TEXT);"
            "INSERT INTO organiser VALUES (7, 'TUM.ai', 'tum-ai.com', NULL, 'club', 1, 'active',"
            " 'seed', '2026-10-07T10:00:00+00:00', NULL);"
            "CREATE TABLE event (key TEXT PRIMARY KEY, fields TEXT, merge_basis TEXT, tier TEXT,"
            " tier_reasons TEXT, drop_reason TEXT, sources TEXT, first_seen TEXT,"
            " updated_at TEXT);"
            "INSERT INTO event VALUES ('k1', '{\"title\": {\"value\": \"Demo Day\"}}', 'url', 'A',"
            " '[\"ai=core\"]', NULL, '[]', '2026-10-07T10:00:00+00:00',"
            " '2026-10-08T10:00:00+00:00');"
            "CREATE TABLE user_state (event_key TEXT, status TEXT, feedback INTEGER,"
            " updated_at TEXT);"
            "INSERT INTO user_state VALUES ('k1', 'registered', NULL, '2026-10-08T11:00:00+00:00');"
        )
        for table in sqlite_import.TABLES:
            db.execute(f"CREATE TABLE IF NOT EXISTS {table} (unused INTEGER)")
    db.close()

    counts = sqlite_import.run(store, path)
    assert counts["organiser"] == counts["event"] == counts["user_state"] == 1
    [org] = store.organisers.all()
    assert org["id"] == 7 and org["ai_focus"] is True
    [event] = store.events.all()
    assert event["fields"]["title"]["value"] == "Demo Day" and event["user_status"] == "registered"
    assert store.organisers.add("CDTM", "cdtm.com", "manual") == 8
    with pytest.raises(RuntimeError, match="already holds data"):
        sqlite_import.run(store, path)
