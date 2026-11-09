import json
import sqlite3
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest

from conftest import TEST_DSN
from event_radar.extract import judgments
from event_radar.extract.router import (
    BudgetExhausted,
    DailyCapReached,
    Model,
    Provider,
    Router,
    conforms,
)
from event_radar.extract.schema import SCHEMA
from event_radar.extract.verify import names_date, verify
from event_radar.html import image_url
from event_radar.judge import Judge
from event_radar.model import (
    NOT_STORED,
    Evidence,
    Kind,
    Observation,
    ParsedEvent,
    SourceClass,
    Standing,
)
from event_radar.parse import parse
from event_radar.platforms import canonical, platform_key
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


def test_a_luma_page_keeps_its_description_for_the_scope_check():
    result = parse(LUMA, "text/html", page("luma-event.html"), Kind.EVENT_PAGE)
    fields = {k: v.value for k, v in result.events[0].fields.items()}
    assert fields["description"] == (
        "Builders and founders show what they shipped.\nDemos, then drinks.\nDoors open at 18:00"
    )
    bare = {**fields, "title": "The Night Shift", "organiser": "START Munich"}
    assert verdict(ai_focus=False, **bare).tier == "B"
    bare.pop("description")
    assert verdict(ai_focus=False, **bare).drop_reason == "off_topic"


def test_a_luma_event_page_follows_events_but_not_its_category_pages():
    result = parse(LUMA, "text/html", page("luma-event.html"), Kind.EVENT_PAGE)
    assert result.follow == ("https://luma.com/example-ai-night-2",)


@pytest.mark.parametrize("kind", list(Kind))
def test_a_luma_category_page_yields_nothing_and_asks_no_llm(kind):
    data = (
        '{"props": {"pageProps": {"initialData": {"kind": "category", "data": {"category":'
        ' {"slug": "climate"}, "num_upcoming_events": 40}}}}}'
    )
    body = f'<script id="__NEXT_DATA__">{data}</script><a href="/example-ai-night">x</a>'
    result = parse("https://luma.com/climate", "text/html", body.encode(), kind)
    assert (result.events, result.follow, result.needs_llm) == ((), (), False)


def test_cms_page_without_registration_goes_to_llm():
    assert parse(CMS, "text/html", page("cms-event.html"), Kind.EVENT_PAGE).needs_llm


def test_a_structured_event_keeps_its_description_as_plain_lines():
    body = b"""<script type="application/ld+json">{"@type": "Event", "name": "Open House",
    "startDate": "2026-11-05T18:00:00+01:00", "description":
    "<p>Meet the <strong>founders</strong> of R&amp;D teams.</p><p>Doors at 18:00<br>Garching</p>"
    }</script>"""
    result = parse("https://example.org/events/open-house", "text/html", body, Kind.EVENT_PAGE)
    fields = {k: v.value for k, v in result.events[0].fields.items()}
    assert fields["description"] == "Meet the founders of R&D teams.\nDoors at 18:00\nGarching"
    assert verdict(ai_focus=False, **fields).tier == "B"
    fields.pop("description")
    assert verdict(ai_focus=False, **fields).drop_reason == "off_topic"
    long = body.replace(b"Garching", b"Garching " * 400)
    long_event = parse("https://example.org/e", "text/html", long, Kind.EVENT_PAGE).events[0]
    assert len(long_event.fields["description"].value) == 2000


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


# tum-ai.com/apply, read 2026-11-08: "25 Oct (passed)" came back as 2027-10-25, since the
# prompt asks for the next occurrence; the tally form's stated 2025 stays (ADR 0022 D2).
@pytest.mark.parametrize(
    ("value", "quote", "read", "expected"),
    [
        (
            "2027-10-25T23:59:00",
            "Application deadline 25 Oct (passed)",
            "2026-11-08",
            "2026-10-25T23:59:00",
        ),
        ("2027-08-31", "Die Bewerbungsphase endete am 31. August", "2026-11-09", "2026-08-31"),
        ("2026-12-06", "Deadline: 6 December", "2026-11-09", "2026-12-06"),
        ("2026-01-15", "Kick-off 15 January", "2026-11-09", "2027-01-15"),
        (
            "2025-10-25T23:59:00",
            "closed on 25.10.2025 at 23:59",
            "2026-11-08",
            "2025-10-25T23:59:00",
        ),
    ],
)
def test_a_date_without_its_year_takes_the_year_nearest_the_read(value, quote, read, expected):
    page = f"Apply. {quote}."
    raw = {"reg_closes_at": {"value": value, "quote": quote}}
    event, _ = verify(raw, page, date.fromisoformat(read))
    assert event.fields["reg_closes_at"].value == expected


# The LLM said online for "Apply now" (TUM.ai), "public" (a TUM patient symposium) and the
# audience of the PhilML'26 conference at MCML, 2026-10 (ADR 0022 D3).
@pytest.mark.parametrize(
    ("value", "quote", "kept"),
    [
        ("online", "Apply now", False),
        ("mixed", "public", False),
        ("online", "philosophers and machine learning researchers", False),
        ("online", "Teams of 2-4 apply together", False),
        ("online", "Das kostenfreie Zoom-Webinar", True),
        ("online", "Free online info session", True),
        ("mixed", "Hybrid – Join us in person", True),
        ("online", "Livestream on YouTube", True),
        ("in_person", "Apply now", True),
    ],
)
def test_an_online_attendance_needs_a_quote_that_names_it(value, quote, kept):
    event, rejected = verify({"attendance": {"value": value, "quote": quote}}, f"x {quote} y")
    assert ("attendance" in event.fields) is kept
    assert ("attendance" in rejected) is not kept


# forge.sphere-intelligence.eu, 2026-10-19: "11–13 November 2026" did not name the 11th.
@pytest.mark.parametrize(
    ("text", "named", "within", "not_named"),
    [
        ("11–13 November 2026", ["2026-11-11", "2026-11-13"], ["2026-11-12"], ["2026-11-14"]),
        ("11th - 13th Nov 2026", ["2026-11-11", "2026-11-13"], ["2026-11-12"], ["2026-11-10"]),
        ("vom 11. bis 13. November 2026", ["2026-11-11"], ["2026-11-12"], ["2026-11-14"]),
        ("11.-13.11.2026", ["2026-11-11", "2026-11-13"], ["2026-11-12"], ["2026-11-14"]),
        ("November 11-13, 2026", ["2026-11-11", "2026-11-13"], ["2026-11-12"], ["2026-11-14"]),
        ("Nov 11 to 13, 2026", ["2026-11-11"], ["2026-11-12"], ["2026-11-14"]),
        (
            "30. Oktober bis 2. November 2026",
            ["2026-10-30", "2026-11-02"],
            ["2026-11-01"],
            ["2026-10-29", "2026-11-03"],
        ),
        ("4 and 11 November 2026", ["2026-11-04", "2026-11-11"], [], ["2026-11-05"]),
        ("18-20 Uhr, 5. November 2026", ["2026-11-05"], [], ["2026-11-18", "2026-11-19"]),
    ],
)
def test_a_day_range_names_its_first_and_last_day_and_holds_the_days_between(
    text, named, within, not_named
):
    for day in named:
        assert names_date(day, text) == (True, True), day
        assert names_date(day, text, within=True) == (True, True), day
    for day in within:
        assert names_date(day, text)[0] is False, day
        assert names_date(day, text, within=True) == (True, True), day
    for day in not_named:
        assert names_date(day, text, within=True)[0] is False, day


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


def test_events_listed_on_one_page_stay_apart_though_each_is_given_the_pages_url():
    # Hosted on 2026-10-22, an LLM gave each of eleven events on MDSI's listing the
    # listing's URL, and they resolved into one event (ADR 0020 D1).
    listing = "https://www.example-institute.org/en/events/"
    fair = "https://www.example-university.de/en/events/details/career-fair"

    def item(n, title, start, model="m"):
        fields = {"title": title, "starts_at": start, "event_url": listing}
        return Observation(
            10 + n,
            "h-listing",
            listing,
            SourceClass.OWN,
            f"llm:{model}@events-v2",
            [],
            {k: Evidence(v) for k, v in fields.items()},
            "2026-10-22T09:00:00",
        )

    listed = [
        item(0, "Munich Career Fair AI & Data Science 2026", "2026-10-29"),
        item(1, "Fit for TUMorrow Day 2026", "2026-10-30"),
        item(2, "Data Visualization in R", "2026-11-17"),
        # Another model read one event there: the page is a listing all the same.
        item(3, "Fit for TUMorrow Day 2026", "2026-10-30", model="n"),
    ]
    page = obs(
        1,
        fair,
        SourceClass.OWN,
        title="Munich Career Fair AI and Data Science",
        starts_at="2026-10-29T14:00:00+01:00",
        event_url=fair,
    )
    groups = {g.fields["title"]["value"]: g for g in resolve([*listed, page])}
    assert sorted(len(g.observations) for g in groups.values()) == [1, 2, 2]
    # The one event of a page is still known by the page's URL.
    assert f"url:{canonical(fair)}" in groups["Munich Career Fair AI and Data Science"].aliases
    assert all(f"url:{canonical(listing)}" not in g.aliases for g in groups.values())


def test_an_event_read_on_a_page_without_its_day_is_the_one_there_with_its_title_and_a_day():
    # Hosted on 2026-10-22, one read of a listing gave "Industrial Edge Workshops for
    # Startups and SMEs" its day and another did not (ADR 0020 D1).
    listing = "https://www.example-centre.de/en/events/p2"

    def item(n, model, title, start=None):
        fields = {"title": title, "event_url": listing} | ({"starts_at": start} if start else {})
        return Observation(
            20 + n,
            f"h-{model}",
            listing,
            SourceClass.OWN,
            f"llm:{model}@events-v2",
            [],
            {k: Evidence(v) for k, v in fields.items()},
            "2026-10-22T09:00:00",
        )

    groups = resolve(
        [
            item(0, "m", "Industrial Edge Workshops", "2026-11-06"),
            item(1, "m", "Opportunity Hunters Talks", "2026-11-12"),
            item(2, "m", "Opportunity Hunters Talks", "2026-11-19"),
            item(3, "n", "Industrial Edge Workshops"),
            item(4, "n", "Opportunity Hunters Talks"),
        ]
    )
    # A series leaves the read without a day nothing to be.
    assert sorted((g.fields["title"]["value"], len(g.observations)) for g in groups) == [
        ("Industrial Edge Workshops", 2),
        *[("Opportunity Hunters Talks", 1)] * 3,
    ]


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


PROFILE = Profile.load(REPO / "profile.yaml")
JUDGE = Judge(PROFILE)


def verdict(ai_focus=True, standing=Standing.CALENDAR, stars=0, judgment=None, **fields):
    return JUDGE.judge(
        {k: {"value": v} for k, v in fields.items()},
        NOW,
        standing=standing,
        ai_focus=ai_focus,
        stars=stars,
        judgment=judgment,
    )


def judged(ai="none", kind="talk", audience="adults", event=True, networking=False, **topics):
    """What the LLM judged an event to be about (ADR 0018), whether it is one and whether
    meeting people is its point (ADR 0021)."""
    found = {"startup": False, "career": False, "tech": False, **topics}
    return {
        "ai": ai,
        **found,
        "kind": kind,
        "audience": audience,
        "event": event,
        "networking": networking,
    }


def test_a_window_ends_an_event_without_a_day_and_a_programme():
    """ADR 0022 D1: the stale TUM.ai form (closed 2025-10-25, no day of its own) was A."""
    form = verdict(
        title="TUM.ai Application Period", kind="programme", reg_closes_at="2025-10-25T23:59:00"
    )
    assert form.drop_reason == "past"
    assert verdict(title="AI meetup", reg_closes_at="2026-10-01").drop_reason == "past"
    assert verdict(title="AI meetup", reg_closes_at="2026-10-09").tier is not None
    # A conference whose registration closed still happens; a programme's class is closed.
    talk = verdict(title="AI conference", starts_at="2026-11-20", reg_closes_at="2026-10-01")
    assert talk.tier is not None
    batch = verdict(
        title="AI product batch",
        kind="programme",
        starts_at="2027-02-22",
        ends_at="2027-05-14",
        reg_closes_at="2026-10-02",
    )
    assert batch.drop_reason == "past"
    open_batch = verdict(
        title="AI product batch",
        kind="programme",
        starts_at="2027-02-22",
        reg_closes_at="2026-12-02",
    )
    assert open_batch.tier == "A"


def test_judge_drops_only_by_logged_rule_and_never_on_price():
    assert verdict(title="AI meetup", starts_at="2026-10-01").drop_reason == "past"
    assert verdict(title="AI talk", attendance="online").drop_reason == "online_only"
    assert verdict(title="AI day", city="Berlin").drop_reason == "outside_munich"
    paid = verdict(title="AI conference", price="paid", city="München")
    assert paid.tier == "C" and paid.drop_reason is None
    assert verdict(title="Career Fair AI", address="81675 Munich").tier == "A"


def test_unknown_ai_relevance_surfaces_only_for_ai_focused_organisers():
    assert verdict(ai_focus=True, title="Monthly meetup #29").tier == "B"
    # A startup word names a topic as strong as AI (ADR 0021).
    startup = verdict(ai_focus=False, title="Industrial Edge Workshops for Startups")
    assert startup.tier == "B" and startup.reasons[-1] == "about AI, startups or careers"
    assert verdict(ai_focus=False, title="Workshop on AI agents").tier == "B"
    assert verdict(ai_focus=False, title="HackaTUM for computer science").tier == "A"
    assert verdict(ai_focus=False, title="Firmenkontaktmesse IKOM").tier == "A"


def test_keywords_match_whole_words_whatever_the_punctuation():
    def relevance(text):
        return JUDGE.ai_relevance({"title": {"value": "Meetup"}, "description": {"value": text}})

    for text in ("Agents and AI, live", "Tools (AI) for teams", "AI-powered search", "KI-gestützt"):
        assert relevance(text) == "core", text
    for text in ("LLMs for teams", "LLM-based search"):
        assert relevance(text) == "core", text
    for text in ("Confidential computing aims to help", "Fresh air in Dubai", "Ski-Ausflug"):
        assert relevance(text) == "unknown", text
    assert relevance("Questions about enrollment and housing") == "unknown"
    assert verdict(ai_focus=False, title="Meet the Start-up Founders").tier == "B"


def test_a_keyword_with_punctuation_is_refused(tmp_path):
    text = (REPO / "profile.yaml").read_text(encoding="utf-8").replace('" ki ",', '"ki-",', 1)
    (tmp_path / "profile.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="ki-"):
        Profile.load(tmp_path / "profile.yaml")


def test_the_kind_comes_from_the_title_not_the_description():
    about = "About the club: a week of workshops, hackathons and networking every year."
    talk = verdict(title="Fundraising in the age of AI", description=about)
    assert talk.tier == "B" and "kind=None" in talk.reasons
    assert verdict(title="Fundraising in the age of AI", kind="conference").tier == "A"


def test_an_off_topic_event_is_off_topic_whatever_its_date_or_place():
    for facts in ({"starts_at": "2026-10-01"}, {"attendance": "online"}, {"city": "Berlin"}):
        assert verdict(ai_focus=False, title="TUM tanzt!", **facts).drop_reason == "off_topic"
    lounge = verdict(ai_focus=False, title="Career Lounge", starts_at="2026-10-01")
    assert lounge.drop_reason == "past"


# The Eventbrite talk refused on 2026-10-19: its summary names no topic, its page names AI
# as a theme and recruiters at the after-event. Hand-written, with invented names.
TALK = {
    "title": "Campus Speakers Series x Example Retail Group",
    "description": "Join us for an evening with two of its executives",
    "price": "free",
}


def test_a_judgment_decides_what_the_words_cannot():
    assert verdict(ai_focus=False, **TALK).drop_reason == "off_topic"
    talk = judged("some", "industry_talk", career=True, tech=True)
    kept = verdict(ai_focus=False, judgment=talk, **TALK)
    assert kept.tier == "A" and "judged: career, tech" in kept.reasons
    salsa = verdict(ai_focus=False, judgment=judged(kind="workshop"), title="Salsa workshop")
    assert salsa.drop_reason == "off_topic"
    cloud = verdict(ai_focus=False, judgment=judged(tech=True, kind="meetup"), title="Cloud Night")
    assert cloud.tier == "C" and "ai=none" in cloud.reasons


def test_a_judgment_overrides_the_words_it_contradicts():
    # "pitch" is a startup word, but a sales pitch training is about none of the topics.
    assert verdict(ai_focus=False, title="Sales pitch training").tier == "B"
    training = verdict(
        ai_focus=False, judgment=judged(kind="workshop"), title="Sales pitch training"
    )
    assert training.drop_reason == "off_topic"
    agent = verdict(ai_focus=False, judgment=judged(), title="Travel agent day")
    assert agent.drop_reason == "off_topic"
    kids = verdict(judgment=judged("core", audience="children"), title="Robots and you")
    assert (kids.drop_reason, kids.reasons) == ("audience", ("audience=children", "judged"))
    # A stated kind is a fact of the page; the judgment's kind fills in when none is stated.
    assert (
        "kind=conference" in verdict(judgment=judged("core"), title="X", kind="conference").reasons
    )
    # Without `tech` in the profile's topics, the scope is ADR 0011's again.
    narrow = Judge(replace(PROFILE, weak_topics=frozenset()))
    cloud = narrow.judge({"title": {"value": "Cloud Night"}}, NOW, judgment=judged(tech=True))
    assert cloud.drop_reason == "off_topic"


def test_the_profiles_topics_are_ones_a_judgment_answers():
    assert PROFILE.topics <= set(judgments.TOPICS)


def test_a_page_with_one_event_is_judged_by_its_text_before_and_after_extraction():
    body = (
        b"<html><head><title>Campus Speakers Series</title></head><body><nav>Menu</nav>"
        b"<p>What AI and the future of work mean; recruiters join the after-event.</p>"
        b'<a href="https://example.org/tickets">Tickets</a>'
        b'<a href="https://example.org/share"><img alt="" src="/share.png"></a></body></html>'
    )
    url = "https://example.org/e/talk"
    parsed = ParsedEvent({"title": Evidence("Campus Speakers Series x Example Retail Group")})
    [page] = judgments.candidates([parsed], body, url, event_page=True)
    assert page["page"] == "Campus Speakers Series" and "recruiters" in page["text"]
    assert "Menu" not in page["text"] and "https" not in page["text"]
    read = ParsedEvent({"title": Evidence("Speakers Series"), "venue": Evidence("Audimax")})
    assert judgments.candidates([read], body, url, event_page=True) == [page]
    # A listing's events, or a page that holds several, are judged by their own fields.
    two = [parsed, ParsedEvent({"title": Evidence("Salsa"), "venue": Evidence("Hall")})]
    assert judgments.candidates(two, body, url, event_page=True) == [
        {"title": "Campus Speakers Series x Example Retail Group"},
        {"title": "Salsa", "venue": "Hall"},
    ]
    assert judgments.candidates([parsed], body, url, event_page=False) == [
        {"title": "Campus Speakers Series x Example Retail Group"}
    ]
    assert judgments.candidates([ParsedEvent({})], b"", url, event_page=True) == [None]


def test_events_outside_ai_startups_and_careers_are_off_topic():
    off = verdict(ai_focus=False, title="TUM tanzt!", venue="Garching Campus")
    assert off.tier is None and off.drop_reason == "off_topic"
    assert verdict(ai_focus=False, title="TUM Blockchain Conference").drop_reason == "off_topic"
    paid = verdict(ai_focus=False, title="Afterwork Fermentieren", price="paid")
    assert paid.drop_reason == "off_topic"
    # An AI-focused organiser's event not about AI is C, stated or judged (ADR 0018 D9).
    party = verdict(ai_focus=True, title="Summer party", ai_relevance="none")
    assert party.tier == "C" and party.reasons[-1] == "about none of the board's topics"
    colloquium = verdict(ai_focus=True, judgment=judged(kind="talk"), title="Colloquium")
    assert colloquium.tier == "C"
    assert verdict(ai_focus=False, title="Engineers for Germany: Career Lounge").tier == "B"
    talks = verdict(ai_focus=False, standing=Standing.COMMUNITY, title="Opportunity Hunters Talks")
    assert talks.tier == "C"
    # A career fair or hackathon ranks by its kind, also when not about AI (ADR 0018).
    fair = verdict(
        ai_focus=False, title="Fit for TUMorrow Day", kind="career_fair", ai_relevance="none"
    )
    assert fair.tier == "A" and fair.reasons[-2:] == (
        "ai=none",
        "a career fair or hackathon, whatever its topic",
    )
    hackathon = verdict(ai_focus=False, judgment=judged(tech=True, kind="hackathon"), title="X")
    assert hackathon.tier == "A"
    paid = verdict(ai_focus=False, title="Career fair", ai_relevance="none", price="paid")
    assert paid.tier == "C"
    assert verdict(ai_focus=False, title="Founders and investors night").tier == "B"


def test_events_rank_by_the_people_worth_meeting_there():
    # ADR 0021, after the CDTM, START and calendar events of 2026-11-06 and 11-08.
    open_day = judged(career=True, tech=True, networking=True)
    cdtm = verdict(ai_focus=False, standing=Standing.ANCHOR, judgment=open_day, title="Open Day")
    assert cdtm.tier == "A"
    assert cdtm.reasons[-1] == "about AI, startups or careers, and built for meeting people"
    assert {"networking", "organiser=anchor"} <= set(cdtm.reasons)
    # An anchor's event is at least B, whatever its topic, and never off topic.
    coffee = verdict(ai_focus=False, standing=Standing.ANCHOR, judgment=judged(), title="Coffee")
    assert coffee.tier == "B" and coffee.reasons[-1] == "an anchor community's event"
    paid = verdict(ai_focus=False, standing=Standing.ANCHOR, title="Summit", price="paid")
    assert paid.tier == "B"
    # A followed organiser's event is never off topic; a general calendar's needs a topic.
    shift = {"ai_focus": False, "judgment": judged(), "title": "The Night Shift"}
    assert verdict(standing=Standing.COMMUNITY, **shift).tier == "C"
    assert verdict(**shift).drop_reason == "off_topic"
    # AI as one theme: C on a general calendar, B at an organiser the radar follows.
    side = {"ai_focus": False, "judgment": judged("some", "conference"), "title": "Vascular"}
    assert verdict(**side).tier == "C"
    assert verdict(standing=Standing.COMMUNITY, **side).tier == "B"
    awareness = verdict(ai_focus=False, judgment=judged("some", "programme"), title="Awareness")
    assert awareness.tier == "C"
    # A strong topic is A with a kind that opens a door or with networking, else B.
    night = {"ai_focus": False, "title": "NLP night"}
    assert verdict(judgment=judged("core", "meetup"), **night).tier == "B"
    assert verdict(judgment=judged("core", "meetup", networking=True), **night).tier == "A"
    pitch = verdict(ai_focus=False, judgment=judged(kind="demo_day", startup=True), title="X")
    assert (
        pitch.tier == "A" and pitch.reasons[-1] == "about AI, startups or careers, and a demo day"
    )
    # An answer to an older question has no networking answer: the title names the format.
    older = {k: v for k, v in judged(career=True).items() if k not in ("event", "networking")}
    assert verdict(ai_focus=False, judgment=older, title="Alumni Stammtisch").tier == "A"
    assert verdict(ai_focus=False, judgment=older, title="Alumni talk").tier == "B"
    assert verdict(ai_focus=False, title="Founders Stammtisch").tier == "A"
    assert verdict(ai_focus=False, title="Afterwork for founders").tier == "B"


def test_a_page_judged_not_an_event_is_a_drop_that_is_stored():
    news = verdict(judgment=judged("core", "demo_day", event=False), title="TUM opens a lab")
    assert news.tier is None and news.drop_reason == "not_event"
    assert news.drop_reason not in NOT_STORED
    # Off topic comes first: a calendar's news page about none of the topics is not stored.
    concert = verdict(ai_focus=False, judgment=judged(event=False), title="Concert review")
    assert concert.drop_reason == "off_topic"
    # An answer to an older question, without `event`, reads as an event.
    older = {k: v for k, v in judged("core").items() if k != "event"}
    assert verdict(judgment=older, title="AI talk").tier == "B"


def test_the_seed_owns_the_standing_of_every_organiser_it_names(store):
    cdtm = store.organisers.add("CDTM", "cdtm.com", "manual", kind="industry")
    store.sources.add("https://luma.com/user/cdtm", "listing", "TICKET", 24, "manual", cdtm)
    calendar = store.organisers.add("TUM", "tum.de", "seed")
    store.sources.add("https://www.tum.de/events", "listing", "OWN", 24, "seed", calendar)
    assert store.sources.standings() == {}
    assert store.organisers.add("CDTM", "cdtm.com", "seed", standing="anchor") == cdtm
    assert store.sources.standings() == {"https://luma.com/user/cdtm": Standing.ANCHOR}
    assert Standing.highest([Standing.COMMUNITY, Standing.ANCHOR]) is Standing.ANCHOR
    assert Standing.highest([]) is Standing.CALENDAR


def test_coding_and_developer_events_are_in_scope():
    # ADR 0014 D9: Alp names coding and developer events relevant; "encoding" is no coding.
    assert verdict(ai_focus=False, title="Coding Dojo: Rust for beginners").tier == "C"
    assert verdict(ai_focus=False, title="Munich Developer Meetup").tier == "C"
    assert verdict(ai_focus=False, title="Software Engineering Night").tier == "C"
    assert verdict(ai_focus=False, title="Video encoding night").drop_reason == "off_topic"


def test_stars_lift_a_tier_from_five_and_ten_but_never_lower_or_lift_a_drop():
    # ADR 0015: 5 stars make an event at least B, 10 make it A; the count stays private.
    paid = {"title": "AI conference", "price": "paid"}
    assert verdict(stars=4, **paid).tier == "C"
    five = verdict(stars=5, **paid)
    assert five.tier == "B" and five.reasons[-2:] == ("paid, no free route", "starred by 5 or more")
    assert verdict(stars=9, title="Monthly meetup #29").tier == "B"
    assert verdict(stars=10, **paid).tier == "A"
    ten = verdict(stars=10, title="Monthly meetup #29")
    assert ten.tier == "A" and ten.reasons[-1] == "starred by 10 or more"
    fair = verdict(stars=10, title="Career Fair AI")
    assert fair.tier == "A" and not any("starred" in r for r in fair.reasons)
    assert verdict(stars=5, title="AI meetup").reasons == verdict(title="AI meetup").reasons
    for facts in ({"starts_at": "2026-10-01"}, {"attendance": "online"}, {"city": "Berlin"}):
        assert verdict(stars=10, title="AI talk", **facts).tier is None
    assert verdict(stars=10, ai_focus=False, title="TUM tanzt!").drop_reason == "off_topic"


def test_events_for_children_pupils_or_families_leave_the_board_whatever_their_topic():
    kids = verdict(title="Children's University: How Is AI Changing the World of Images?")
    assert kids.tier is None and kids.drop_reason == "audience"
    assert kids.reasons == ("audience=children", "title 'children's university'")
    museums = "Children’s Program at the Long Night of Museums"
    assert verdict(title=museums, kind="programme", ai_relevance="core").drop_reason == "audience"
    pupils = verdict(title="KI-Workshop für Schülerinnen", starts_at="2026-10-01")
    assert pupils.drop_reason == "audience" and pupils.reasons[0] == "audience=school_pupils"
    family = verdict(title="appliedAI Developers Meetup - Family Edition", city="Heilbronn")
    assert family.drop_reason == "audience" and family.reasons[0] == "audience=families"
    stated = verdict(
        title="Robotics course", ai_relevance="core", price="paid", audience="children"
    )
    assert stated.drop_reason == "audience" and stated.reasons == ("audience=children",)
    open_house = verdict(
        title="Open House in Garching",
        audience="adults",
        prerequisites="Open to all; students, staff, external guests, families",
    )
    assert open_house.tier == "B"
    assert verdict(title="AI in Children's Medicine Symposium").tier == "A"
    assert verdict(ai_focus=False, title="Familienunternehmen und KI").tier == "B"
    office = verdict(ai_focus=False, standing=Standing.COMMUNITY, title="Family Office Summit")
    assert office.tier == "C"


def test_the_extraction_states_an_audience_from_its_own_values():
    page = "Kinder-Uni am Campus. For children aged 8 to 12."
    stated = {"value": "children", "quote": "For children aged 8 to 12"}
    event, rejected = verify({"audience": stated}, page)
    assert event.fields["audience"].value == "children" and rejected == []
    _, rejected = verify({"audience": {**stated, "value": "kids"}}, page)
    assert rejected == ["audience"]
    assert "audience" in SCHEMA["properties"]["events"]["items"]["required"]


def test_the_verifier_keeps_only_the_fields_it_asked_for():
    page = "Demo night. https://example.org/a.png"
    event, _ = verify(
        {
            "title": {"value": "Demo night", "quote": "Demo night"},
            "image": {"value": "https://example.org/a.png", "quote": "https://example.org/a.png"},
        },
        page,
    )
    assert set(event.fields) == {"title"}


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


OPENROUTER = Provider(
    "openrouter",
    "https://openrouter.example",
    "k",
    5,
    (
        Model("nvidia/nemotron-3-super-120b-a12b:free", "schema"),
        Model("openrouter/free", "json_object"),
    ),
)
GROQ = Provider("groq", "https://groq.example", "g", 5, (Model("openai/gpt-oss-120b", "schema"),))
LIVE = {"data": [{"id": "nvidia/nemotron-3-super-120b-a12b:free"}, {"id": "openai/gpt-oss-120b"}]}


def _router(store, handler, *providers, sent=None):
    """A router over `providers` (OpenRouter alone by default); `sent` collects the hosts
    of the completion requests."""

    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json=LIVE)
        if sent is not None:
            sent.append(request.url.host)
        return handler(request)

    return Router(list(providers) or [OPENROUTER], store, httpx.MockTransport(route))


def _ok(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)}}]})


def test_router_skips_upstream_429_and_stops_on_account_cap(store):
    replies = iter([httpx.Response(429, text="rate-limited upstream"), _ok({"events": []})])
    model, result = _router(store, lambda r: next(replies)).complete([], {})
    assert model == "openrouter/free" and result == {"events": []}
    with pytest.raises(DailyCapReached):
        _router(store, lambda r: httpx.Response(429, text="free-models-per-day")).complete([], {})


def test_budget_is_a_ceiling(store):
    spent = replace(OPENROUTER, daily_cap=1)
    router = _router(store, lambda r: httpx.Response(503, text='{"error": "overloaded"}'), spent)
    with pytest.raises(BudgetExhausted):
        router.complete([], {})


def test_groq_answers_when_openrouter_reaches_its_daily_limit(store):
    def handler(request):
        if request.url.host == "openrouter.example":
            return httpx.Response(429, text="free-models-per-day")
        return _ok({"events": []})

    sent: list[str] = []
    router = _router(store, handler, OPENROUTER, GROQ, sent=sent)
    assert router.complete([], {})[0] == "openai/gpt-oss-120b"
    assert router.complete([], {})[0] == "openai/gpt-oss-120b"
    assert sent == ["openrouter.example", "groq.example", "groq.example"]
    with store.tx() as db:
        used = {r["provider"]: r["used"] for r in db.execute("SELECT provider, used FROM budget")}
    assert used == {"openrouter": 1, "groq": 2}


def test_groq_answers_when_openrouter_times_out(store):
    def handler(request):
        if request.url.host == "openrouter.example":
            raise httpx.ReadTimeout("slow", request=request)
        return _ok({"events": []})

    sent: list[str] = []
    router = _router(store, handler, OPENROUTER, GROQ, sent=sent)
    router.complete([], {})
    router.complete([], {})
    assert sent == ["openrouter.example", "groq.example", "groq.example"]


@pytest.mark.parametrize(
    "answer, fits",
    [
        ({"events": []}, True),
        ({}, True),
        ({"events": [{"title": None, "venue": {"value": "Room 1", "quote": "Room 1"}}]}, True),
        ({"events": [{"kind": {"value": "panel", "quote": "Panel"}, "image": "x"}]}, True),
        ({"events": [{"title": "Talk on AI"}]}, False),
        ({"events": [{"title": {"value": 5, "quote": "5"}}]}, False),
        ({"events": [{"title": {"value": "Talk", "quote": ["Talk"]}}]}, False),
        ({"events": ["Talk on AI"]}, False),
        ({"events": None}, True),
        ({"events": [None]}, False),
        ([], False),
    ],
)
def test_an_answer_conforms_by_its_types_alone(answer, fits):
    assert conforms(answer, SCHEMA) is fits


def test_a_judgment_id_is_an_integer_not_a_boolean():
    judged = {"ai": "some", "startup": False, "career": True, "tech": True}
    judged |= {"kind": "industry_talk", "audience": "adults", "event": True, "networking": False}
    assert conforms({"judgments": [{"id": 0, **judged}]}, judgments.SCHEMA)
    assert not conforms({"judgments": [{"id": True, **judged}]}, judgments.SCHEMA)
    assert judgments.answers({"judgments": [{"id": True, **judged}]}, 2) == {}
    assert judgments.answers({"judgments": [{"id": 1, **judged}]}, 2) == {1: judged}
    wrongs = ({"ai": "maybe"}, {"tech": "yes"}, {"kind": "party"}, {"audience": None})
    for wrong in (*wrongs, {"event": "yes"}, {"networking": None}):
        assert judgments.answers({"judgments": [{"id": 0, **judged, **wrong}]}, 1) == {}


def test_a_judgment_that_cannot_tell_is_no_judgment():
    judged = {"ai": "unknown", "startup": True, "career": False, "tech": False}
    judged |= {"kind": "meetup", "audience": "adults", "event": True, "networking": True}
    assert judgments.answers({"judgments": [{"id": 0, **judged}]}, 1) == {0: judged}
    assert judgments.telling(judged) is None and judgments.telling(None) is None
    assert judgments.telling(judged | {"ai": "none"})["ai"] == "none"


def test_a_json_object_model_reads_the_schema_it_answers_in(store):
    asked = []

    def handler(request):
        asked.append(json.loads(request.content)["messages"])
        return _ok({"events": []})

    plain = replace(OPENROUTER, models=(Model("openrouter/free", "json_object"),))
    _router(store, handler, plain).complete([{"role": "user", "content": "page"}], SCHEMA)
    [[shape, question]] = asked
    assert shape["role"] == "system" and json.dumps(SCHEMA) in shape["content"]
    assert question == {"role": "user", "content": "page"}


def test_every_provider_spent_defers_the_work(store):
    capped = (replace(OPENROUTER, daily_cap=0), replace(GROQ, daily_cap=0))
    with pytest.raises(BudgetExhausted):
        _router(store, lambda r: _ok({}), *capped).complete([], {})


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
