import uuid
from collections import Counter
from dataclasses import dataclass

from psycopg import Connection, sql
from psycopg.types.json import Jsonb

from ..model import NOT_STORED, JudgedEvent
from ..platforms import canonical
from ..resolve import title_and_day
from .database import Repository, now_iso

# Alp's statuses: the owner's rows, or the unclaimed ones until an owner exists (ADR 0005).
OWNER_STATE = (
    "SELECT event_key, status FROM user_state"
    " WHERE user_id = (SELECT user_id FROM admin WHERE role = 'owner')"
    " UNION ALL SELECT event_key, status FROM user_state_unclaimed"
)

# An event as a harvest sees it: its aliases, the pages it was read from and its fields.
Seen = tuple[set[str], set[str], dict[str, dict]]


@dataclass(frozen=True)
class Continued:
    """What one event carries on of the stored events (`Events.continued`)."""

    keys: tuple[str, ...]  # oldest first: the key it keeps, then the ones merging into it
    stars: int
    judgment_key: str | None


class Events(Repository):
    """The resolved events. An event keeps its key across runs through its aliases."""

    def save(self, judged: list[JudgedEvent]) -> dict[str, int]:
        """Write each event under the key of the oldest stored event it carries on
        (`_claim`); the others it carries on merge into it. An event off topic or for
        another audience is not written, and the stored events it carries on are deleted
        (ADRs 0011, 0012). Every alias an event names ends with it, or with no event when it
        is not written (ADR 0020). The statements do not grow with the board, since each is
        a round trip from a runner far from the database: one lookup, the decisions in
        `_plan`, then batched writes."""
        stamp = now_iso()
        seen = [(e.aliases, set(e.sources), e.fields) for e in judged]
        with self.database.tx() as db:
            named = _named(db, seen)
            plan = _plan(judged, _claim(seen, named), named, stamp)
            # Survivors first, so a merge always moves into a row that exists.
            if plan.rows:
                db.cursor().executemany(
                    "INSERT INTO event (key, fields, merge_basis, tier, tier_reasons, drop_reason,"
                    " sources, first_seen, updated_at, judgment_key)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (key) DO UPDATE SET fields = excluded.fields,"
                    " merge_basis = excluded.merge_basis, tier = excluded.tier,"
                    " tier_reasons = excluded.tier_reasons, drop_reason = excluded.drop_reason,"
                    " sources = excluded.sources, updated_at = excluded.updated_at,"
                    " judgment_key = excluded.judgment_key",
                    plan.rows,
                )
            for loser, into in plan.merges:
                _merge(db, loser, into=into)
            if plan.deleted:
                _delete(db, plan.deleted)
            if plan.dropped:
                db.execute("DELETE FROM event_alias WHERE alias = ANY(%s)", (plan.dropped,))
            if plan.aliases:
                db.cursor().executemany(
                    "INSERT INTO event_alias VALUES (%s, %s)"
                    " ON CONFLICT (alias) DO UPDATE SET event_key = excluded.event_key",
                    plan.aliases,
                )
        return plan.counts

    def continued(self, groups: list[Seen]) -> list[Continued]:
        """For each event, what it carries on of the stored events as `save` would write
        it: their keys, the accounts that starred them (ADR 0015 D4) and the judgment the
        oldest judged one was last judged by (ADR 0018)."""
        with self.database.tx() as db:
            named = _named(db, groups)
        stored = {r["key"]: r for r in named}
        found = []
        for keys in _claim(groups, named):
            stars = {user for k in keys for user in stored[k]["stars"]}
            judged = next((j for k in keys if (j := stored[k]["judgment_key"])), None)
            found.append(Continued(tuple(keys), len(stars), judged))
        return found

    def known(self, aliases: set[str]) -> dict | None:
        """The oldest stored event one of `aliases` names."""
        with self.database.tx() as db:
            return db.execute(
                "SELECT e.* FROM event_alias a JOIN event e ON e.key = a.event_key"
                " WHERE a.alias = ANY(%s) ORDER BY e.first_seen LIMIT 1",
                (list(aliases),),
            ).fetchone()

    def of_source(self, url: str) -> dict | None:
        """The oldest stored event the source `url` describes."""
        with self.database.tx() as db:
            return db.execute(
                "SELECT * FROM event WHERE sources ? %s ORDER BY first_seen LIMIT 1", (url,)
            ).fetchone()

    def all(self) -> list[dict]:
        """Every event, with the owner's status as `user_status`."""
        with self.database.tx() as db:
            return db.execute(
                f"WITH owner_state AS ({OWNER_STATE})"
                " SELECT e.*, COALESCE(u.status, 'none') AS user_status FROM event e"
                " LEFT JOIN owner_state u ON u.event_key = e.key"
            ).fetchall()


def _pages(sources: set[str]) -> set[str]:
    return {"url:" + canonical(url) for url in sources}


def _named(db: Connection, seen: list[Seen]) -> list[dict]:
    """The stored events the events' aliases or pages name: one row per alias, with its
    event's key, age, sources, title and day, last judgment and the accounts that starred
    it."""
    return db.execute(
        "SELECT a.alias, e.key, e.first_seen, e.sources, e.judgment_key,"
        " jsonb_build_object('title', e.fields->'title', 'starts_at', e.fields->'starts_at')"
        " AS fields, ARRAY(SELECT s.user_id FROM star s WHERE s.event_key = e.key) AS stars"
        " FROM event_alias a JOIN event e ON e.key = a.event_key WHERE a.alias = ANY(%s)",
        (sorted({a for aliases, sources, _ in seen for a in aliases | _pages(sources)}),),
    ).fetchall()


def _claim(seen: list[Seen], named: list[dict]) -> list[list[str]]:
    """For each event, the stored events it carries on, oldest first. A stored event whose
    pages now resolve into several events goes to one of them: the one naming most of its
    aliases, then on its day, then sharing most of its sources, then the first; the others
    start anew (ADR 0020 D2). A stored event known by the URL of a page that has become a
    listing, and so names none of its events, goes to the event from that page with its
    title on no other day, unless an event names it (D3)."""
    owner = {r["alias"]: r["key"] for r in named}
    stored = {r["key"]: r for r in named}
    best: dict[str, tuple[int, bool, int, int]] = {}
    for n, (aliases, sources, fields) in enumerate(seen):
        title, day = title_and_day(fields)
        counts = Counter(owner[a] for a in aliases if a in owner)
        for key in counts.keys() | {owner[a] for a in _pages(sources) if a in owner}:
            was, then = title_and_day(stored[key]["fields"])
            other_day = None not in (day, then) and day != then
            if not counts[key] and (not title or title != was or other_day):
                continue
            claim = (counts[key], day == then, len(sources & set(stored[key]["sources"])), -n)
            best[key] = max(best.get(key, claim), claim)
    won: list[list[str]] = [[] for _ in seen]
    for key, (*_, n) in best.items():
        won[-n].append(key)
    return [sorted(keys, key=lambda k: (stored[k]["first_seen"], k)) for keys in won]


@dataclass
class _Plan:
    rows: list[tuple]  # the events written, in the columns of `event`
    merges: list[tuple[str, str]]  # stored loser and the survivor it merges into
    deleted: list[str]  # the stored events of events not written
    dropped: list[str]  # the stored aliases of events not written
    aliases: list[tuple[str, str]]  # aliases that move to or join an event written
    counts: dict[str, int]


def _plan(
    judged: list[JudgedEvent], claims: list[list[str]], named: list[dict], stamp: str
) -> _Plan:
    """What writing `judged` leaves, given the stored events they name: each event carries
    on the stored events it claims, under the oldest's key, or starts anew."""
    owner = {r["alias"]: r["key"] for r in named}
    counts = dict.fromkeys(("new", "updated", "split", "merged", "deleted"), 0)
    plan = _Plan([], [], [], [], [], counts)
    for event, keys in zip(judged, claims, strict=True):
        if event.verdict.drop_reason in NOT_STORED:
            plan.deleted += keys
            plan.dropped += sorted(a for a in event.aliases if a in owner)
            counts["deleted"] += len(keys)
            continue
        key = keys[0] if keys else uuid.uuid4().hex[:12]
        plan.merges += [(loser, key) for loser in keys[1:]]
        counts["merged"] += len(keys[1:])
        # An event naming stored events that others carry on splits off.
        counts["updated" if keys else "split" if event.aliases & owner.keys() else "new"] += 1
        plan.aliases += [(a, key) for a in sorted(event.aliases) if owner.get(a) != key]
        verdict = event.verdict
        plan.rows.append(
            (
                key,
                Jsonb(event.fields),
                event.merge_basis,
                verdict.tier,
                Jsonb(verdict.reasons),
                verdict.drop_reason,
                Jsonb(sorted(event.sources)),
                stamp,
                stamp,
                event.judgment_key,
            )
        )
    return plan


def _delete(db: Connection, keys: list[str]) -> int:
    """Delete events with their aliases and push ledger rows; users' statuses and stars go
    by cascade."""
    db.execute("DELETE FROM event_alias WHERE event_key = ANY(%s)", (keys,))
    db.execute("DELETE FROM push_ledger WHERE event_key = ANY(%s)", (keys,))
    return db.execute("DELETE FROM event WHERE key = ANY(%s)", (keys,)).rowcount


def _merge(db: Connection, loser: str, into: str) -> None:
    db.execute("UPDATE event_alias SET event_key = %s WHERE event_key = %s", (into, loser))
    # Each user's status and star move to the survivor unless they set one there; deleting
    # the loser cascades to whatever did not move.
    for table in ("user_state", "star"):
        db.execute(
            sql.SQL(
                "UPDATE {table} l SET event_key = %(key)s WHERE l.event_key = %(loser)s"
                " AND NOT EXISTS (SELECT FROM {table} w"
                "                 WHERE w.user_id = l.user_id AND w.event_key = %(key)s)"
            ).format(table=sql.Identifier(table)),
            {"key": into, "loser": loser},
        )
    db.execute(
        "UPDATE user_state_unclaimed SET event_key = %(key)s"
        " WHERE event_key = %(loser)s AND NOT EXISTS"
        " (SELECT FROM user_state_unclaimed WHERE event_key = %(key)s)",
        {"key": into, "loser": loser},
    )
    db.execute("DELETE FROM event WHERE key = %s", (loser,))
