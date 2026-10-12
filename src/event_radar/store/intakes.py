from psycopg import sql
from psycopg.types.json import Jsonb

from ..model import WORKING_INTAKE_STATES, IntakeState
from .database import Repository, now_iso


class Intakes(Repository):
    """Organisations suggested for the registry and their progress (ADR 0003). Becoming
    `queued` dispatches the intake workflow (ADR 0006 D3)."""

    def create(self, url: str, events_url: str | None, name: str | None) -> int:
        stamp = now_iso()
        with self.database.tx() as db:
            return db.execute(
                "INSERT INTO intake (url, events_url, name, created_at, updated_at)"
                " VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (url, events_url, name, stamp, stamp),
            ).fetchone()["id"]

    def get(self, intake_id: int) -> dict | None:
        with self.database.tx() as db:
            return db.execute("SELECT * FROM intake WHERE id = %s", (intake_id,)).fetchone()

    def recent(self, limit: int) -> list[dict]:
        with self.database.tx() as db:
            return db.execute("SELECT * FROM intake ORDER BY id DESC LIMIT %s", (limit,)).fetchall()

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

    def next_queued(self) -> dict | None:
        with self.database.tx() as db:
            return db.execute(
                "SELECT * FROM intake WHERE state = %s ORDER BY id LIMIT 1", (IntakeState.QUEUED,)
            ).fetchone()

    def requeue_interrupted(self) -> int:
        """Queue again the intakes a killed job left mid-step."""
        with self.database.tx() as db:
            return db.execute(
                "UPDATE intake SET state = %s WHERE state = ANY(%s)",
                (IntakeState.QUEUED, list(WORKING_INTAKE_STATES)),
            ).rowcount
