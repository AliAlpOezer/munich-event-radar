import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from event_radar.extract.router import BudgetExhausted, DailyCapReached, Model, Router
from event_radar.extract.verify import verify
from event_radar.judge import Judge
from event_radar.model import Evidence, Kind, Observation, SourceClass
from event_radar.parse import parse
from event_radar.platforms import platform_key
from event_radar.resolve import resolve
from event_radar.store import Store

REPO = Path(__file__).resolve().parents[1]
NOW = datetime.fromisoformat("2026-10-07T12:00:00+02:00")


def fixture(url: str) -> tuple[str | None, bytes]:
    manifest = json.loads((REPO / "eval/fixtures/manifest.json").read_text())
    entry = manifest[url]
    return entry.get("content_type"), (REPO / "eval/fixtures" / entry["file"]).read_bytes()


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
    content_type, body = fixture("https://luma.com/4k19ckbb")
    result = parse("https://luma.com/4k19ckbb", content_type, body, Kind.EVENT_PAGE)
    fields = {k: v.value for k, v in result.events[0].fields.items()}
    assert fields["reg_mode"] == "approval" and fields["spots_left"] == 14
    assert "luma:evt-9NpVAZn7j9dZQWA" in result.events[0].platform_keys
    assert not result.needs_llm


def test_cms_page_without_registration_goes_to_llm():
    url = "https://badw.de/en/events/view/a-cryptographic-perspective-on-machine-learning-trustworthiness-challenges"
    content_type, body = fixture(url)
    assert parse(url, content_type, body, Kind.EVENT_PAGE).needs_llm


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


def test_judge_drops_only_by_logged_rule_and_never_on_price():
    judge = Judge.from_file(REPO / "profile.yaml")
    field = lambda **kw: {k: {"value": v} for k, v in kw.items()}  # noqa: E731
    assert judge.judge(field(title="AI meetup", starts_at="2026-10-01"), NOW).drop_reason == "past"
    assert (
        judge.judge(field(title="AI talk", attendance="online"), NOW).drop_reason == "online_only"
    )
    assert judge.judge(field(title="AI day", city="Berlin"), NOW).drop_reason == "outside_munich"
    paid = judge.judge(field(title="AI conference", price="paid", city="München"), NOW)
    assert paid.tier == "C" and paid.drop_reason is None
    assert judge.judge(field(title="Career Fair AI", address="81675 Munich"), NOW).tier == "A"


def _router(tmp_path, handler, cap=5):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    router = Router("k", Store(tmp_path / "r.db"), cap, client)
    router._live = {m.id for m in (Model("nvidia/nemotron-3-super-120b-a12b:free", "schema"),)}
    return router


def _ok(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)}}]})


def test_router_skips_upstream_429_and_stops_on_account_cap(tmp_path):
    replies = iter([httpx.Response(429, text="rate-limited upstream"), _ok({"events": []})])
    model, result = _router(tmp_path, lambda r: next(replies)).complete([], {})
    assert model == "openrouter/free" and result == {"events": []}
    with pytest.raises(DailyCapReached):
        _router(tmp_path, lambda r: httpx.Response(429, text="free-models-per-day")).complete(
            [], {}
        )


def test_budget_is_a_ceiling(tmp_path):
    router = _router(tmp_path, lambda r: httpx.Response(503, text='{"error": "overloaded"}'), cap=1)
    with pytest.raises(BudgetExhausted):
        router.complete([], {})
