from psycopg.types.json import Jsonb

from ..model import RunOutcome
from .database import Repository, now_iso


class Runs(Repository):
    """One row per harvest run, with its counts."""

    def start(self) -> int:
        with self.database.tx() as db:
            return db.execute(
                "INSERT INTO run (started_at) VALUES (%s) RETURNING id", (now_iso(),)
            ).fetchone()["id"]

    def finish(self, run_id: int, outcome: RunOutcome, summary: dict) -> None:
        with self.database.tx() as db:
            db.execute(
                "UPDATE run SET finished_at = %s, outcome = %s, summary = %s WHERE id = %s",
                (now_iso(), outcome, Jsonb(summary), run_id),
            )
