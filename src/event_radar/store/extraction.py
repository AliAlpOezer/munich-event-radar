from datetime import UTC, datetime

from psycopg.types.json import Jsonb

from ..model import JobState
from .database import Repository, now_iso


class Extraction(Repository):
    """Pages queued for the LLM, its cached answers by content hash, and its daily budget."""

    def enqueue(self, snapshot_hash: str, url: str, priority: int) -> None:
        with self.database.tx() as db:
            db.execute(
                "INSERT INTO extraction_job (snapshot_hash, url, priority)"
                " VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (snapshot_hash, url, priority),
            )

    def pending(self, limit: int, urls: list[str] | None = None) -> list[dict]:
        only = " AND url = ANY(%s)" if urls is not None else ""
        params = (JobState.PENDING, urls, limit) if urls is not None else (JobState.PENDING, limit)
        with self.database.tx() as db:
            # A job whose body was never imported waits until a fetch stores it again.
            return db.execute(
                "SELECT * FROM extraction_job j WHERE state = %s AND EXISTS"
                f" (SELECT FROM page_body b WHERE b.content_hash = j.snapshot_hash){only}"
                " ORDER BY priority, attempts LIMIT %s",
                params,
            ).fetchall()

    def finish(self, snapshot_hash: str, url: str, state: JobState) -> None:
        with self.database.tx() as db:
            db.execute(
                "UPDATE extraction_job SET state = %s, attempts = attempts + 1"
                " WHERE snapshot_hash = %s AND url = %s",
                (state, snapshot_hash, url),
            )

    def cached(self, content_hash: str, version: str) -> dict | None:
        with self.database.tx() as db:
            row = db.execute(
                "SELECT result FROM extraction WHERE content_hash = %s AND version = %s",
                (content_hash, version),
            ).fetchone()
        return row["result"] if row else None

    def cache(self, content_hash: str, version: str, model: str, result: dict) -> None:
        with self.database.tx() as db:
            db.execute(
                "INSERT INTO extraction (content_hash, version, model, result, created_at)"
                " VALUES (%s, %s, %s, %s, %s) ON CONFLICT (content_hash, version) DO UPDATE"
                " SET model = excluded.model, result = excluded.result,"
                " created_at = excluded.created_at",
                (content_hash, version, model, Jsonb(result), now_iso()),
            )

    def spend(self, provider: str, cap: int) -> bool:
        """Take one call from today's budget for `provider`; False once `cap` is used."""
        day = datetime.now(UTC).date()
        with self.database.tx() as db:
            db.execute(
                "INSERT INTO budget (provider, day) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (provider, day),
            )
            cursor = db.execute(
                "UPDATE budget SET used = used + 1 WHERE provider = %s AND day = %s AND used < %s",
                (provider, day, cap),
            )
            return cursor.rowcount == 1
