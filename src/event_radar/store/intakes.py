from datetime import date

from psycopg import sql
from psycopg.types.json import Jsonb

from ..model import WORKING_INTAKE_STATES, IntakeKind, IntakeState
from .database import Repository, now_iso


class Intakes(Repository):
    """Organisations suggested for the registry (ADR 0003) and events added from their
    page (ADR 0014), with their progress. Becoming `queued` dispatches the intake workflow
    (ADR 0006 D3)."""

    def create(self, url: str, events_url: str | None, name: str | None) -> int:
        stamp = now_iso()
        with self.database.tx() as db:
            return db.execute(
                "INSERT INTO intake (url, events_url, name, created_at, updated_at)"
                " VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (url, events_url, name, stamp, stamp),
            ).fetchone()["id"]

    def create_event(self, url: str, starts_on: date, place: str) -> int:
        """An event intake as `api.suggest_event` makes one, without its checks of the
        caller (ADR 0014)."""
        stamp = now_iso()
        with self.database.tx() as db:
            return db.execute(
                "INSERT INTO intake (kind, url, starts_on, place, created_at, updated_at)"
                " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (IntakeKind.EVENT, url, starts_on, place, stamp, stamp),
            ).fetchone()["id"]

    def propose(self, kind: IntakeKind, url: str, events_url: str | None, found_by: str) -> int:
        """An intake waiting for an admin's review, which the web search found (ADR 0016);
        `found_by` is its first log line."""
        stamp = now_iso()
        entry = {"at": stamp, "step": "search", "text": found_by, "level": "info"}
        with self.database.tx() as db:
            return db.execute(
                "INSERT INTO intake (kind, url, events_url, state, log, created_at, updated_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (kind, url, events_url, IntakeState.REVIEW, Jsonb([entry]), stamp, stamp),
            ).fetchone()["id"]

    def proposed_today(self) -> int:
        """How many intakes the web search proposed since the UTC day began."""
        with self.database.tx() as db:
            return db.execute(
                "SELECT count(*) AS n FROM intake WHERE log -> 0 ->> 'step' = 'search'"
                " AND created_at >= date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'"
            ).fetchone()["n"]

    def all(self) -> list[dict]:
        with self.database.tx() as db:
            return db.execute("SELECT id, kind, url, state FROM intake ORDER BY id").fetchall()

    def get(self, intake_id: int) -> dict | None:
        with self.database.tx() as db:
            return db.execute("SELECT * FROM intake WHERE id = %s", (intake_id,)).fetchone()

    def update(self, intake_id: int, **cols) -> None:
        cols = {**cols, "updated_at": now_iso()}
        for name in ("profile", "result"):
            if cols.get(name) is not None:
                cols[name] = Jsonb(cols[name])
        query = sql.SQL("UPDATE intake SET {} WHERE id = %s").format(
            sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(name)) for name in cols)
        )
        with self.database.tx() as db:
            db.execute(query, (*cols.values(), intake_id))

    def log(self, intake_id: int, step: str, text: str, level: str = "info") -> None:
        """Add a line to the intake's log, which the page shows as it runs."""
        entry = {"at": now_iso(), "step": step, "text": text, "level": level}
        with self.database.tx() as db:
            db.execute(
                "UPDATE intake SET log = log || %s, updated_at = %s WHERE id = %s",
                (Jsonb([entry]), entry["at"], intake_id),
            )

    def drop_page(self, intake_id: int) -> int:
        """Take an event intake's page off the radar as `api.remove_event` does (ADR 0014
        D7), waiting for a judge that is running; returns how many events went."""
        with self.database.tx() as db:
            return db.execute(
                "SELECT drop_intake_page(%s, true) AS deleted", (intake_id,)
            ).fetchone()["deleted"]

    def next_queued(self, kind: IntakeKind) -> dict | None:
        with self.database.tx() as db:
            return db.execute(
                "SELECT * FROM intake WHERE state = %s AND kind = %s ORDER BY id LIMIT 1",
                (IntakeState.QUEUED, kind),
            ).fetchone()

    def queued(self, kind: IntakeKind) -> int:
        """The intakes of `kind` a drain would work: queued, or cut off mid-step."""
        with self.database.tx() as db:
            return db.execute(
                "SELECT count(*) AS n FROM intake WHERE state = ANY(%s) AND kind = %s",
                ([IntakeState.QUEUED, *WORKING_INTAKE_STATES], kind),
            ).fetchone()["n"]

    def requeue_interrupted(self, kind: IntakeKind) -> int:
        """Queue again the intakes of `kind` a killed job left mid-step."""
        with self.database.tx() as db:
            return db.execute(
                "UPDATE intake SET state = %s WHERE state = ANY(%s) AND kind = %s",
                (IntakeState.QUEUED, list(WORKING_INTAKE_STATES), kind),
            ).rowcount
