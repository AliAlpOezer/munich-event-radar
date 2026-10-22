import uuid
from dataclasses import dataclass

from psycopg import Connection, sql
from psycopg.types.json import Jsonb

from ..model import NOT_STORED, JudgedEvent
from .database import Repository, now_iso

# Alp's statuses: the owner's rows, or the unclaimed ones until an owner exists (ADR 0005).
OWNER_STATE = (
    "SELECT event_key, status FROM user_state"
    " WHERE user_id = (SELECT user_id FROM admin WHERE role = 'owner')"
    " UNION ALL SELECT event_key, status FROM user_state_unclaimed"
)


class Events(Repository):
    """The resolved events. An event keeps its key across runs through its aliases."""

    def save(self, judged: list[JudgedEvent]) -> dict[str, int]:
        """Write each event under the key of the oldest event sharing an alias; the
        other events it now spans merge into it. An event off topic or for another
        audience is not written, and the events it spans are deleted (ADRs 0011, 0012).
        The statements do not grow with the board, since each is a round trip from a runner
        far from the database: one lookup, the decisions in `_plan`, then batched writes."""
        stamp = now_iso()
        with self.database.tx() as db:
            stored = db.execute(
                "SELECT a.alias, e.key, e.first_seen FROM event_alias a JOIN event e"
                " ON e.key = a.event_key WHERE a.alias = ANY(%s)",
                ([a for event in judged for a in event.aliases],),
            ).fetchall()
            plan = _plan(judged, stored, stamp)
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
            if plan.aliases:
                db.cursor().executemany(
                    "INSERT INTO event_alias VALUES (%s, %s) ON CONFLICT DO NOTHING", plan.aliases
                )
        return plan.counts

    def star_counts(self, groups: list[set[str]]) -> list[int]:
        """For each set of aliases, the accounts that starred a stored event one of them
        names: the stars of the event `save` merges those into (ADR 0015 D4)."""
        with self.database.tx() as db:
            rows = db.execute(
                "SELECT g.n, count(DISTINCT s.user_id) AS stars"
                " FROM jsonb_array_elements(%s) WITH ORDINALITY AS g (aliases, n)"
                " CROSS JOIN jsonb_array_elements_text(g.aliases) AS a (alias)"
                " JOIN event_alias ea ON ea.alias = a.alias"
                " JOIN star s ON s.event_key = ea.event_key GROUP BY g.n",
                (Jsonb([sorted(g) for g in groups]),),
            ).fetchall()
        stars = {r["n"]: r["stars"] for r in rows}
        return [stars.get(n, 0) for n in range(1, len(groups) + 1)]

    def judgment_keys(self, groups: list[set[str]]) -> list[str | None]:
        """For each set of aliases, the judgment the oldest stored event one of them names
        was last judged by (ADR 0018)."""
        with self.database.tx() as db:
            rows = db.execute(
                "SELECT DISTINCT ON (g.n) g.n, e.judgment_key"
                " FROM jsonb_array_elements(%s) WITH ORDINALITY AS g (aliases, n)"
                " CROSS JOIN jsonb_array_elements_text(g.aliases) AS a (alias)"
                " JOIN event_alias ea ON ea.alias = a.alias JOIN event e ON e.key = ea.event_key"
                " WHERE e.judgment_key IS NOT NULL ORDER BY g.n, e.first_seen",
                (Jsonb([sorted(g) for g in groups]),),
            ).fetchall()
        keys = {r["n"]: r["judgment_key"] for r in rows}
        return [keys.get(n) for n in range(1, len(groups) + 1)]

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


@dataclass
class _Plan:
    rows: list[tuple]  # the events written, in the columns of `event`
    merges: list[tuple[str, str]]  # stored loser and survivor, in the order they merge
    deleted: list[str]
    aliases: list[tuple[str, str]]  # aliases the store does not have yet, with their event
    counts: dict[str, int]


def _plan(judged: list[JudgedEvent], stored: list[dict], stamp: str) -> _Plan:
    """What writing `judged` one event after the other would leave, given the stored events
    their aliases name: each event looks up the events its aliases name as the writes before
    it left them, and the oldest wins. Among events as old, a stored one wins over one
    written now, so a merge never moves stars into a row that was not there."""
    owner = {r["alias"]: r["key"] for r in stored}
    present = set(owner)  # stored aliases: a merge moves them, a delete takes them
    first_seen = {r["key"]: r["first_seen"] for r in stored}
    counts = {"new": 0, "updated": 0, "merged": 0, "deleted": 0}
    rows: dict[str, tuple] = {}
    merges: list[tuple[str, str]] = []
    deleted: list[str] = []

    def leave(key: str, into: str | None = None) -> None:
        """`key` merges into `into`, its aliases with it, or is deleted with them."""
        rows.pop(key, None)
        for alias, k in list(owner.items()):
            if k != key:
                continue
            if into is None:
                del owner[alias]
                present.discard(alias)
            else:
                owner[alias] = into

    for event in judged:
        found = sorted(
            {owner[a] for a in event.aliases if a in owner},
            key=lambda k: (k not in first_seen, first_seen.get(k), k),
        )
        if event.verdict.drop_reason in NOT_STORED:
            for key in found:
                leave(key)
            deleted += found
            counts["deleted"] += len(found)
            continue
        key = found[0] if found else uuid.uuid4().hex[:12]
        for loser in found[1:]:
            if loser in first_seen:
                merges.append((loser, key))
            leave(loser, into=key)
            counts["merged"] += 1
        counts["updated" if found else "new"] += 1
        owner |= dict.fromkeys(event.aliases, key)
        verdict = event.verdict
        rows[key] = (
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
    aliases = [(a, k) for a, k in owner.items() if a not in present]
    return _Plan(list(rows.values()), merges, deleted, aliases, counts)


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
