import gzip
import hashlib

from psycopg import Connection, sql

from ..model import JobState
from .database import Repository, now_iso


class Pages(Repository):
    """Fetched pages: a snapshot row per fetch, and the gzipped body by content hash
    (ADR 0006 D2)."""

    def record(self, url: str, body: bytes, **cols) -> str:
        """Keep a fetched page and its snapshot row in one transaction; returns its hash."""
        digest = hashlib.sha256(body).hexdigest()
        with self.database.tx() as db:
            _save_body(db, digest, body)
            _insert_snapshot(db, url, content_hash=digest, **cols)
        return digest

    def record_failure(self, url: str, http_status: int | None, error: str) -> None:
        with self.database.tx() as db:
            _insert_snapshot(db, url, http_status=http_status, error=error)

    def save(self, body: bytes) -> str:
        """Keep a page body that has no fetch behind it (the golden replay)."""
        digest = hashlib.sha256(body).hexdigest()
        with self.database.tx() as db:
            _save_body(db, digest, body)
        return digest

    def body(self, content_hash: str) -> bytes:
        with self.database.tx() as db:
            row = db.execute(
                "SELECT body FROM page_body WHERE content_hash = %s", (content_hash,)
            ).fetchone()
        if row is None:
            raise KeyError(content_hash)
        return gzip.decompress(row["body"])

    def latest(self, url: str) -> dict | None:
        """The newest successful fetch of `url` whose body is still kept."""
        with self.database.tx() as db:
            return db.execute(
                "SELECT s.* FROM snapshot s JOIN page_body USING (content_hash)"
                " WHERE s.url = %s ORDER BY s.id DESC LIMIT 1",
                (url,),
            ).fetchone()

    def prune(self, keep_hours: int = 1) -> int:
        """Drop the bodies nothing can read again: kept are each URL's latest fetch, pages
        waiting for the LLM, and anything stored in the last `keep_hours`, which a job
        running alongside may not have recorded yet (ADR 0006 D2)."""
        with self.database.tx() as db:
            return db.execute(
                "WITH latest AS (SELECT DISTINCT ON (url) content_hash FROM snapshot"
                "                WHERE content_hash IS NOT NULL ORDER BY url, id DESC)"
                " DELETE FROM page_body b"
                " WHERE b.stored_at < now() - make_interval(hours => %s)"
                " AND NOT EXISTS (SELECT FROM latest l WHERE l.content_hash = b.content_hash)"
                " AND NOT EXISTS (SELECT FROM extraction_job j"
                "                 WHERE j.snapshot_hash = b.content_hash AND j.state = %s)",
                (keep_hours, JobState.PENDING),
            ).rowcount


def _save_body(db: Connection, digest: str, body: bytes) -> None:
    db.execute(
        "INSERT INTO page_body (content_hash, body) VALUES (%s, %s)"
        " ON CONFLICT (content_hash) DO UPDATE SET stored_at = now()",
        (digest, gzip.compress(body)),
    )


def _insert_snapshot(db: Connection, url: str, **cols) -> None:
    cols = {"url": url, "fetched_at": now_iso(), **cols}
    db.execute(
        sql.SQL("INSERT INTO snapshot ({}) VALUES ({})").format(
            sql.SQL(", ").join(map(sql.Identifier, cols)),
            sql.SQL(", ").join(sql.Placeholder() * len(cols)),
        ),
        tuple(cols.values()),
    )
