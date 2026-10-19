"""The Data API as PostgREST will use it (ADR 0005): each block runs as `anon` or as a
logged-in user, with the JWT claims set and only the api schema on the search path."""

import json
import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta

import pytest
from psycopg import errors, sql

from conftest import TEST_DSN
from event_radar.model import Evidence, JudgedEvent, ParsedEvent, SourceClass, Verdict
from event_radar.platforms import canonical
from event_radar.store import Store

PRIVATE = (
    "budget",
    "event_alias",
    "extraction",
    "extraction_job",
    "observation",
    "page_body",
    "push_ledger",
    "snapshot",
    "user_state_unclaimed",
    "schema_migration",
    "api_build",
)


@contextmanager
def api(store: Store, user: uuid.UUID | None = None):
    role = "authenticated" if user else "anon"
    claims = {"role": role, **({"sub": str(user)} if user else {})}
    with store.tx() as db:
        db.execute("SELECT set_config('request.jwt.claims', %s, true)", (json.dumps(claims),))
        db.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
        db.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(store.api)))
        yield db


def _verdict(tier="B"):
    return Verdict(tier, ("ai=core",))


@pytest.fixture
def board(store):
    org = store.organisers.add("Munich NLP", "luma.com/munichnlp", "seed", ai_focus=True)
    store.sources.add("https://luma.com/munichnlp", "listing", "TICKET", 24, "seed", org)
    images = ["https://a.org/own.png"] + ["https://b.org/site.png"] * 3
    # As in a harvest, the run starts before it saves the events.
    run = store.runs.start()
    store.events.save(
        [
            JudgedEvent(
                {f"luma:evt-{i}"},
                {"title": {"value": f"E{i}"}, "image": {"value": image}},
                "platform",
                _verdict(),
                ["https://luma.com/munichnlp", "https://other.example/list"],
            )
            for i, image in enumerate(images)
        ]
    )
    store.runs.finish(run, "ok", {"tier_B": 4})
    return {e["fields"]["title"]["value"]: e["key"] for e in store.events.all()}


def _status(store, user, key):
    with api(store, user) as db:
        row = db.execute("SELECT user_status FROM events WHERE key = %s", (key,)).fetchone()
    return row["user_status"]


def test_anyone_reads_the_board(store, board):
    with api(store) as db:
        events = db.execute("SELECT * FROM events ORDER BY key").fetchall()
        [org] = db.execute("SELECT * FROM organisers").fetchall()
        me = db.execute("SELECT me()").fetchone()["me"]
    assert len(events) == 4 and {e["user_status"] for e in events} == {"none"}
    assert {e["starred"] for e in events} == {False}
    assert events[0]["sources"] == [
        {"url": "https://luma.com/munichnlp", "class": "TICKET", "organiser_id": org["id"]},
        {"url": "https://other.example/list", "class": None, "organiser_id": None},
    ]
    assert org["sources"][0]["url"] == "https://luma.com/munichnlp"
    assert me == {"user_id": None, "admin": False}


def test_the_events_view_derives_covers_organisers_and_staleness(store, board):
    with api(store) as db:
        rows = {r["key"]: r for r in db.execute("SELECT * FROM events").fetchall()}
    covers = {title: rows[key]["cover"] for title, key in board.items()}
    # The image on three events is the site's share image, not a cover.
    assert covers == {"E0": "https://a.org/own.png", "E1": None, "E2": None, "E3": None}
    [org] = store.organisers.all()
    assert all(r["organiser_ids"] == [org["id"]] for r in rows.values())
    assert not any(r["stale"] for r in rows.values())
    with store.tx() as db:
        db.execute(
            "UPDATE event SET updated_at = updated_at - interval '1 day' WHERE key = %s",
            (board["E0"],),
        )
    with api(store) as db:
        stale = db.execute("SELECT key FROM events WHERE stale").fetchall()
    assert stale == [{"key": board["E0"]}]


def test_anyone_sees_when_the_board_changed(store, board):
    def status():
        with api(store) as db:
            return db.execute("SELECT board_status()").fetchone()["board_status"]

    before = status()
    assert before["harvested_at"]
    run = store.runs.start()
    started = status()
    store.runs.finish(run, "failed", {})
    failed = status()
    assert len({before["version"], started["version"], failed["version"]}) == 3
    assert failed["harvested_at"] == before["harvested_at"]
    store.organisers.add("CDTM", "cdtm.com", "manual")
    assert status()["version"] != failed["version"]


def test_private_tables_and_writes_are_closed(store, board, users):
    user = users()
    target = sql.Identifier
    for who in (None, user):
        for table in PRIVATE:
            with pytest.raises(errors.InsufficientPrivilege), api(store, who) as db:
                db.execute(sql.SQL("SELECT * FROM {}").format(target(store.schema, table)))
        for statement in (
            "UPDATE {} SET tier = 'A'",
            "DELETE FROM {}",
        ):
            with pytest.raises(errors.InsufficientPrivilege), api(store, who) as db:
                db.execute(sql.SQL(statement).format(target(store.schema, "event")))
        with pytest.raises(errors.InsufficientPrivilege), api(store, who) as db:
            db.execute(
                sql.SQL(
                    "INSERT INTO {} (user_id, event_key, status) VALUES (%s, %s, 'applied')"
                ).format(target(store.schema, "user_state")),
                (user, board["E0"]),
            )
        with pytest.raises(errors.InsufficientPrivilege), api(store, who) as db:
            db.execute(
                sql.SQL("INSERT INTO {} (user_id, event_key) VALUES (%s, %s)").format(
                    target(store.schema, "star")
                ),
                (user, board["E0"]),
            )
    for view in ("runs", "intakes"):
        with pytest.raises(errors.InsufficientPrivilege), api(store) as db:
            db.execute(sql.SQL("SELECT * FROM {}").format(target(view)))
    for call in (
        "SELECT set_status('x', 'applied')",
        "SELECT set_star('x', true)",
        "SELECT suggest_organisation('https://a.org')",
        "SELECT suggest_event('https://a.org/e', current_date + 1, 'Munich')",
        "SELECT remove_event(1)",
    ):
        with pytest.raises(errors.InsufficientPrivilege), api(store) as db:
            db.execute(call)
    with api(store, user) as db:
        assert db.execute("SELECT * FROM runs").fetchall() == []


def test_every_table_has_rls_and_every_definer_pins_its_path(store):
    with store.tx() as db:
        open_tables = db.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = %s AND NOT rowsecurity",
            (store.schema,),
        ).fetchall()
        unpinned = db.execute(
            "SELECT proname FROM pg_proc WHERE prosecdef AND pronamespace = ANY(%s::regnamespace[])"
            " AND NOT EXISTS (SELECT FROM unnest(proconfig) c WHERE c LIKE 'search_path=%%')",
            ([store.schema, store.api],),
        ).fetchall()
        public = db.execute(
            "SELECT p.proname FROM pg_proc p,"
            " aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a"
            " WHERE p.pronamespace = ANY(%s::regnamespace[]) AND a.grantee = 0",
            ([store.schema, store.api],),
        ).fetchall()
    assert open_tables == [] and unpinned == [] and public == []


def test_each_user_keeps_their_own_status(store, board, users):
    alice, bob = users(), users()
    key = board["E0"]
    with api(store, alice) as db:
        assert db.execute("SELECT set_status(%s, 'applied')", (key,)).fetchone()["set_status"]
    assert _status(store, alice, key) == "applied"
    assert _status(store, bob, key) == "none" and _status(store, None, key) == "none"
    with api(store, bob) as db:
        assert (
            db.execute(
                sql.SQL("SELECT * FROM {}").format(sql.Identifier(store.schema, "user_state"))
            ).fetchall()
            == []
        )
    with api(store, alice) as db:
        db.execute("SELECT set_status(%s, 'registered')", (key,))
        db.execute("SELECT set_status(%s, 'registered')", (key,))  # a repeat is a no-op
    with pytest.raises(errors.CheckViolation, match="registered cannot become skipped"):
        with api(store, alice) as db:
            db.execute("SELECT set_status(%s, 'skipped')", (key,))
    with pytest.raises(errors.CheckViolation), api(store, alice) as db:
        db.execute("SELECT set_status(%s, 'bogus')", (key,))
    with pytest.raises(errors.ForeignKeyViolation), api(store, alice) as db:
        db.execute("SELECT set_status('nope', 'applied')")
    assert {e["user_status"] for e in store.events.all()} == {"none"}


def _starred(store, user, key):
    with api(store, user) as db:
        row = db.execute("SELECT starred FROM events WHERE key = %s", (key,)).fetchone()
    return row["starred"]


def _stars(store):
    with store.tx() as db:
        return db.execute("SELECT user_id, event_key, starred_at FROM star").fetchall()


def test_each_user_keeps_their_own_stars(store, board, users):
    alice, bob = users(), users()
    key = board["E0"]
    with api(store, alice) as db:
        assert db.execute("SELECT set_star(%s, true)", (key,)).fetchone()["set_star"] is True
    [first] = _stars(store)
    assert _starred(store, alice, key) is True
    assert _starred(store, bob, key) is False and _starred(store, None, key) is False
    with api(store, bob) as db:
        assert (
            db.execute(
                sql.SQL("SELECT * FROM {}").format(sql.Identifier(store.schema, "star"))
            ).fetchall()
            == []
        )
    with api(store, alice) as db:
        db.execute("SELECT set_star(%s, true)", (key,))  # a repeat keeps the first star
    assert _stars(store) == [first]
    with api(store, alice) as db:
        assert db.execute("SELECT set_star(%s, false)", (key,)).fetchone()["set_star"] is False
        db.execute("SELECT set_star(%s, false)", (key,))  # unstarring twice is a no-op
    assert _starred(store, alice, key) is False and _stars(store) == []
    with pytest.raises(errors.InvalidParameterValue), api(store, alice) as db:
        db.execute("SELECT set_star(%s, NULL)", (key,))
    with pytest.raises(errors.ForeignKeyViolation), api(store, alice) as db:
        db.execute("SELECT set_star('nope', true)")
    with api(store, alice) as db:
        db.execute("SELECT set_star(%s, true)", (key,))
    with store.tx() as db:
        db.execute("DELETE FROM auth.users WHERE id = %s", (alice,))
    assert _stars(store) == []


def test_a_suggestion_waits_for_an_admin(store, users):
    alice, bob = users(), users()
    admin = users("admin@example.org")
    store.users.grant_admin("admin@example.org")
    with api(store, alice) as db:
        intake_id = db.execute(
            "SELECT suggest_organisation('https://example.org', NULL, ' Example ')"
        ).fetchone()["suggest_organisation"]
        [mine] = db.execute("SELECT * FROM intakes").fetchall()
        assert db.execute("SELECT * FROM runs").fetchall() == []
    assert (mine["state"], mine["approved"], mine["mine"], mine["name"]) == (
        "review",
        False,
        True,
        "Example",
    )
    assert store.intakes.next_queued() is None
    with api(store, bob) as db:
        assert db.execute("SELECT * FROM intakes").fetchall() == []
    with pytest.raises(errors.InsufficientPrivilege), api(store, alice) as db:
        db.execute("SELECT decide_intake(%s, true)", (intake_id,))
    with api(store, admin) as db:
        assert db.execute("SELECT me()").fetchone()["me"]["admin"] is True
        [seen] = db.execute("SELECT * FROM intakes").fetchall()
        assert seen["id"] == intake_id and seen["mine"] is False
        decided = db.execute("SELECT decide_intake(%s, true)", (intake_id,)).fetchone()
        assert decided["decide_intake"] == "queued"
        assert len(db.execute("SELECT * FROM runs").fetchall()) == 0
    row = store.intakes.get(intake_id)
    assert row["approved"] and [e["step"] for e in row["log"]] == ["review", "review"]
    assert store.intakes.next_queued()["id"] == intake_id
    with pytest.raises(errors.ObjectNotInPrerequisiteState), api(store, admin) as db:
        db.execute("SELECT decide_intake(%s, false)", (intake_id,))
    with pytest.raises(errors.InvalidParameterValue), api(store, alice) as db:
        db.execute("SELECT suggest_organisation('javascript:alert(1)')")
    with api(store, alice) as db:
        for i in range(5):
            db.execute("SELECT suggest_organisation(%s)", (f"https://org{i}.example",))
    with pytest.raises(errors.ProgramLimitExceeded), api(store, alice) as db:
        db.execute("SELECT suggest_organisation('https://one-too-many.example')")
    with api(store, admin) as db:
        own = db.execute("SELECT suggest_organisation('https://admin.example')").fetchone()
    assert store.intakes.get(own["suggest_organisation"])["state"] == "queued"


def _soon() -> date:
    return (datetime.now(UTC) + timedelta(days=30)).date()


def _add_event(store, user, url, place="Munich") -> int:
    with api(store, user) as db:
        return db.execute("SELECT suggest_event(%s, %s, %s)", (url, _soon(), place)).fetchone()[
            "suggest_event"
        ]


@pytest.mark.parametrize(
    "url",
    [
        "https://Example.org",
        "HTTPS://EXAMPLE.org/A/b/?x=1&y=2#frag",
        "https://example.org/e?",
        "https://example.org/a#x?y",
        "http://example.org:8080/path//",
        " https://lu.ma/abc123/ ",
    ],
)
def test_the_store_spells_a_page_as_the_harvester_does(store, url):
    with store.tx() as db:
        spelt = db.execute("SELECT canonical_url(%s) AS url", (url,)).fetchone()["url"]
    assert spelt == canonical(url)


def test_anyone_logged_in_adds_an_event_within_the_caps(store, users):
    alice, bob = users(), users()
    admin = users("admin@example.org")
    store.users.grant_admin("admin@example.org")
    first = _add_event(store, alice, "https://Example.org/ai-night/", place=" Munich ")
    with api(store, alice) as db:
        [mine] = db.execute("SELECT * FROM intakes").fetchall()
    assert (mine["kind"], mine["state"], mine["starts_on"], mine["place"], mine["mine"]) == (
        "event",
        "queued",
        _soon(),
        "Munich",
        True,
    )
    assert store.intakes.next_queued()["id"] == first
    for url, day, place in (
        ("javascript:alert(1)", _soon(), "Munich"),
        ("https://example.org/e", None, "Munich"),
        ("https://example.org/e", date(2020, 1, 1), "Munich"),
        ("https://example.org/e", _soon(), "  "),
        ("https://example.org/e", _soon(), "x" * 201),
    ):
        with pytest.raises(errors.InvalidParameterValue), api(store, alice) as db:
            db.execute("SELECT suggest_event(%s, %s, %s)", (url, day, place))
    # One intake per page, in any spelling: while it is checked, and once it is added.
    for state in ("checking", "done"):
        store.intakes.update(first, state=state)
        with pytest.raises(errors.UniqueViolation), api(store, bob) as db:
            db.execute(
                "SELECT suggest_event('https://example.org/ai-night#tickets', %s, 'Munich')",
                (_soon(),),
            )
    store.intakes.update(first, state="rejected")
    store.intakes.update(_add_event(store, bob, "https://example.org/ai-night"), state="failed")

    opened = [_add_event(store, alice, f"https://example.org/e{i}") for i in range(5)]
    with pytest.raises(errors.ProgramLimitExceeded, match="5 events"), api(store, alice) as db:
        db.execute("SELECT suggest_event('https://example.org/e5', %s, 'Munich')", (_soon(),))
    for intake_id in opened:
        store.intakes.update(intake_id, state="done")
    for i in range(5, 9):
        _add_event(store, alice, f"https://example.org/e{i}")
    with pytest.raises(errors.ProgramLimitExceeded, match="10 events"), api(store, alice) as db:
        db.execute("SELECT suggest_event('https://example.org/e9', %s, 'Munich')", (_soon(),))
    for i in range(11):
        _add_event(store, admin, f"https://example.org/by-admin-{i}")


def test_an_admin_removes_an_added_event_and_its_page_stays_off(store, users):
    alice, admin = users(), users("admin@example.org")
    store.users.grant_admin("admin@example.org")
    page = "https://example.org/ai-night"
    intake_id = _add_event(store, alice, page)
    with pytest.raises(errors.ObjectNotInPrerequisiteState), api(store, admin) as db:
        db.execute("SELECT remove_event(%s)", (intake_id,))
    # What the drain leaves behind for an added page (ADR 0014, Flow).
    store.sources.add(page, "event_page", "OWN", 72, f"intake:{intake_id}")
    digest = store.pages.record(page, b"<html>AI Night</html>")
    store.extraction.enqueue(digest, page, 0)
    store.observations.add(
        digest, page, SourceClass.OWN, "jsonld@2", [ParsedEvent({"title": Evidence("AI Night")})]
    )
    store.events.save(
        [
            JudgedEvent({"url:a"}, {"title": {"value": "AI Night"}}, "url", _verdict(), [page]),
            JudgedEvent(
                {"url:b"},
                {"title": {"value": "Shared"}},
                "url",
                _verdict(),
                [page, "https://other.example/list"],
            ),
        ]
    )
    keys = {e["fields"]["title"]["value"]: e["key"] for e in store.events.all()}
    with api(store, alice) as db:
        db.execute("SELECT set_status(%s, 'applied')", (keys["AI Night"],))
        db.execute("SELECT set_star(%s, true)", (keys["AI Night"],))
    with store.tx() as db:
        db.execute(
            "INSERT INTO push_ledger (event_key, kind, fact_hash, state, created_at)"
            " VALUES (%s, 'new', 'h', 'sent', now())",
            (keys["AI Night"],),
        )
    store.intakes.update(intake_id, state="done")
    with pytest.raises(errors.InsufficientPrivilege), api(store, alice) as db:
        db.execute("SELECT remove_event(%s)", (intake_id,))
    organisation = store.intakes.create("https://example.org", None, None)
    store.intakes.update(organisation, state="done")
    with pytest.raises(errors.WrongObjectType), api(store, admin) as db:
        db.execute("SELECT remove_event(%s)", (organisation,))
    # A judge running now would write the event back.
    with store.lock("judge"), pytest.raises(errors.LockNotAvailable), api(store, admin) as db:
        db.execute("SELECT remove_event(%s)", (intake_id,))

    with api(store, admin) as db:
        assert db.execute("SELECT remove_event(%s)", (intake_id,)).fetchone()["remove_event"] == 1
        assert db.execute("SELECT remove_event(%s)", (intake_id,)).fetchone()["remove_event"] == 0
    # The event only that page held is gone with what hangs on it; the shared one stays
    # until the next judge rewrites it from its other source.
    assert [e["key"] for e in store.events.all()] == [keys["Shared"]]
    assert store.sources.get(page) is None and _stars(store) == []
    with store.tx() as db:
        for table in ("observation", "extraction_job", "snapshot", "push_ledger", "user_state"):
            assert db.execute(sql.SQL("SELECT FROM {}").format(sql.Identifier(table))).rowcount == 0
        aliases = db.execute("SELECT alias FROM event_alias").fetchall()
    assert aliases == [{"alias": "url:b"}]
    row = store.intakes.get(intake_id)
    assert row["state"] == "removed" and row["log"][-1]["step"] == "removed"
    with pytest.raises(errors.InsufficientPrivilege, match="removed"), api(store, alice) as db:
        db.execute(
            "SELECT suggest_event('https://example.org/ai-night/', %s, 'Munich')", (_soon(),)
        )
    assert store.intakes.get(_add_event(store, admin, page))["state"] == "queued"


def _unclaimed(store, key, status):
    """A status from before logins existed, as the SQLite import brings them (ADR 0005 D7)."""
    with store.tx() as db:
        db.execute(
            "INSERT INTO user_state_unclaimed (event_key, status, updated_at)"
            " VALUES (%s, %s, now())",
            (key, status),
        )


def test_the_owner_claims_the_statuses_kept_before_logins(store, board, users):
    key = board["E0"]
    _unclaimed(store, key, "applied")
    owner = users("alp@example.org")
    assert _status(store, owner, key) == "none"
    with pytest.raises(LookupError):
        store.users.grant_admin("nobody@example.org")
    assert store.users.grant_admin("ALP@example.org", owner=True)["claimed"] == 1
    assert _status(store, owner, key) == "applied"
    with api(store, owner) as db:
        db.execute("SELECT set_status(%s, 'registered')", (key,))
    assert {e["key"]: e["user_status"] for e in store.events.all()}[key] == "registered"
    users("second@example.org")
    with pytest.raises(ValueError, match="already an owner"):
        store.users.grant_admin("second@example.org", owner=True)


def test_a_merge_keeps_each_users_status_and_stars(store, users):
    store.events.save(
        [JudgedEvent({"url:a"}, {"title": {"value": "A"}}, "url", _verdict(), [])]
        + [JudgedEvent({"url:b"}, {"title": {"value": "B"}}, "url", _verdict(), [])]
    )
    keys = {e["fields"]["title"]["value"]: e["key"] for e in store.events.all()}
    with store.tx() as db:
        db.execute(
            "UPDATE event SET first_seen = first_seen - interval '1 day' WHERE key = %s",
            (keys["A"],),
        )
    alice, bob = users(), users()
    with api(store, alice) as db:
        db.execute("SELECT set_status(%s, 'skipped')", (keys["B"],))
        db.execute("SELECT set_star(%s, true)", (keys["B"],))
    with api(store, bob) as db:
        db.execute("SELECT set_status(%s, 'applied')", (keys["A"],))
        db.execute("SELECT set_status(%s, 'skipped')", (keys["B"],))
        db.execute("SELECT set_star(%s, true)", (keys["A"],))
        db.execute("SELECT set_star(%s, true)", (keys["B"],))
    _unclaimed(store, keys["B"], "registered")
    counts = store.events.save(
        [JudgedEvent({"url:a", "url:b"}, {"title": {"value": "A"}}, "url", _verdict(), [])]
    )
    assert counts["merged"] == 1
    [event] = store.events.all()
    assert event["key"] == keys["A"] and event["user_status"] == "registered"
    assert _status(store, alice, keys["A"]) == "skipped"
    assert _status(store, bob, keys["A"]) == "applied"
    assert _starred(store, alice, keys["A"]) and _starred(store, bob, keys["A"])
    assert len(_stars(store)) == 2


@pytest.mark.parametrize("reason", ["off_topic", "audience"])
def test_an_event_that_leaves_the_board_is_deleted_with_what_hangs_on_it(store, users, reason):
    store.events.save(
        [JudgedEvent({"url:a"}, {"title": {"value": "Dance"}}, "url", _verdict("C"), [])]
    )
    [event] = store.events.all()
    alice = users()
    with api(store, alice) as db:
        db.execute("SELECT set_status(%s, 'applied')", (event["key"],))
        db.execute("SELECT set_star(%s, true)", (event["key"],))
    with store.tx() as db:
        db.execute(
            "INSERT INTO push_ledger (event_key, kind, fact_hash, state, created_at)"
            " VALUES (%s, 'new', 'h', 'sent', now())",
            (event["key"],),
        )
    off = Verdict(None, ("ai=unknown",), reason)
    counts = store.events.save(
        [JudgedEvent({"url:a", "url:b"}, {"title": {"value": "Dance"}}, "url", off, [])]
        + [JudgedEvent({"url:c"}, {"title": {"value": "Concert"}}, "url", off, [])]
    )
    assert counts["deleted"] == 1 and counts["new"] == 0
    assert store.events.all() == [] and _stars(store) == []
    with store.tx() as db:
        for table in ("event_alias", "push_ledger", "user_state"):
            assert db.execute(sql.SQL("SELECT FROM {}").format(sql.Identifier(table))).rowcount == 0


def test_the_api_is_rebuilt_only_when_it_changes(store):
    def api_oid():
        with store.tx() as db:
            return db.execute("SELECT to_regnamespace(%s)::oid AS oid", (store.api,)).fetchone()[
                "oid"
            ]

    def reopen():
        Store(TEST_DSN, store.schema, store.api).close()

    before = api_oid()
    reopen()
    assert api_oid() == before
    with store.tx() as db:
        db.execute("UPDATE api_build SET hash = 'an older file'")
    reopen()
    assert api_oid() != before
