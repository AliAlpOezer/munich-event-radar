"""The Data API as PostgREST will use it (ADR 0005): each block runs as `anon` or as a
logged-in user, with the JWT claims set and only the api schema on the search path."""

import json
import uuid
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from psycopg import errors, sql

from conftest import TEST_DSN
from event_radar.model import JudgedEvent, UserStatus, Verdict
from event_radar.store import Store
from event_radar.web import create_app

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


@pytest.fixture
def users(store):
    made = []

    def make(email: str | None = None) -> uuid.UUID:
        user = uuid.uuid4()
        with store.tx() as db:
            db.execute(
                "INSERT INTO auth.users (id, email) VALUES (%s, %s)",
                (user, email or f"{user}@example.org"),
            )
        made.append(user)
        return user

    yield make
    with store.tx() as db:
        db.execute("DELETE FROM auth.users WHERE id = ANY(%s)", (made,))


def _verdict(tier="B"):
    return Verdict(tier, ("ai=core",))


@pytest.fixture
def board(store):
    org = store.organisers.add("Munich NLP", "luma.com/munichnlp", "seed", ai_focus=True)
    store.sources.add("https://luma.com/munichnlp", "listing", "TICKET", 24, "seed", org)
    images = ["https://a.org/own.png"] + ["https://b.org/site.png"] * 3
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
    run = store.runs.start()
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
    assert events[0]["sources"] == [
        {"url": "https://luma.com/munichnlp", "class": "TICKET", "organiser_id": org["id"]},
        {"url": "https://other.example/list", "class": None, "organiser_id": None},
    ]
    assert org["sources"][0]["url"] == "https://luma.com/munichnlp"
    assert me == {"user_id": None, "admin": False}


def test_the_events_view_matches_the_web_app(store, board):
    old = TestClient(create_app(store)).get("/api/events").json()["events"]
    with api(store) as db:
        new = {r["key"]: r for r in db.execute("SELECT * FROM events").fetchall()}
    assert len(old) == len(new)
    for event in old:
        row = new[event["key"]]
        for name in ("tier", "drop_reason", "reasons", "merge_basis", "user_status", "stale"):
            assert row[name] == event[name], name
        for name in ("fields", "cover", "sources", "organiser_ids"):
            assert row[name] == event[name], name


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
    for view in ("runs", "intakes"):
        with pytest.raises(errors.InsufficientPrivilege), api(store) as db:
            db.execute(sql.SQL("SELECT * FROM {}").format(target(view)))
    for call in (
        "SELECT set_status('x', 'applied')",
        "SELECT suggest_organisation('https://a.org')",
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


def test_the_owner_claims_the_statuses_kept_before_logins(store, board, users):
    key = board["E0"]
    store.users.set_owner_status(key, UserStatus.APPLIED)
    owner = users("alp@example.org")
    assert _status(store, owner, key) == "none"
    with pytest.raises(LookupError):
        store.users.grant_admin("nobody@example.org")
    assert store.users.grant_admin("ALP@example.org", owner=True)["claimed"] == 1
    assert _status(store, owner, key) == "applied"
    store.users.set_owner_status(key, UserStatus.REGISTERED)
    assert _status(store, owner, key) == "registered"
    assert {e["key"]: e["user_status"] for e in store.events.all()}[key] == "registered"
    with pytest.raises(ValueError, match="cannot become"):
        store.users.set_owner_status(key, UserStatus.SKIPPED)
    users("second@example.org")
    with pytest.raises(ValueError, match="already an owner"):
        store.users.grant_admin("second@example.org", owner=True)


def test_a_merge_keeps_each_users_status(store, users):
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
    with api(store, bob) as db:
        db.execute("SELECT set_status(%s, 'applied')", (keys["A"],))
        db.execute("SELECT set_status(%s, 'skipped')", (keys["B"],))
    store.users.set_owner_status(keys["B"], UserStatus.REGISTERED)
    counts = store.events.save(
        [JudgedEvent({"url:a", "url:b"}, {"title": {"value": "A"}}, "url", _verdict(), [])]
    )
    assert counts["merged"] == 1
    [event] = store.events.all()
    assert event["key"] == keys["A"] and event["user_status"] == "registered"
    assert _status(store, alice, keys["A"]) == "skipped"
    assert _status(store, bob, keys["A"]) == "applied"


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
