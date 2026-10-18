import uuid

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
        audience is not written, and the events it spans are deleted (ADRs 0011, 0012)."""
        stamp = now_iso()
        counts = {"new": 0, "updated": 0, "merged": 0, "deleted": 0}
        with self.database.tx() as db:
            for event in judged:
                found = db.execute(
                    "SELECT DISTINCT e.key, e.first_seen FROM event_alias a JOIN event e"
                    " ON e.key = a.event_key WHERE a.alias = ANY(%s) ORDER BY e.first_seen",
                    (list(event.aliases),),
                ).fetchall()
                if event.verdict.drop_reason in NOT_STORED:
                    counts["deleted"] += _delete(db, [r["key"] for r in found])
                    continue
                key = found[0]["key"] if found else uuid.uuid4().hex[:12]
                for loser in found[1:]:
                    _merge(db, loser["key"], into=key)
                    counts["merged"] += 1
                counts["updated" if found else "new"] += 1
                verdict = event.verdict
                db.execute(
                    "INSERT INTO event (key, fields, merge_basis, tier, tier_reasons, drop_reason,"
                    " sources, first_seen, updated_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (key) DO UPDATE SET fields = excluded.fields,"
                    " merge_basis = excluded.merge_basis, tier = excluded.tier,"
                    " tier_reasons = excluded.tier_reasons, drop_reason = excluded.drop_reason,"
                    " sources = excluded.sources, updated_at = excluded.updated_at",
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
                    ),
                )
                db.cursor().executemany(
                    "INSERT INTO event_alias VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    [(a, key) for a in event.aliases],
                )
        return counts

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
