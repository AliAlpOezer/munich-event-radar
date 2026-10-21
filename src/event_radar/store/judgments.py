from collections.abc import Iterable

from psycopg import Connection
from psycopg.types.json import Jsonb

from .database import Repository, now_iso


class Judgments(Repository):
    """What the LLM judged events to be about (ADR 0018): each candidate by its key, with
    the answer once asked."""

    def want(self, candidates: dict[str, dict]) -> None:
        """Keep the candidates no judgment has been asked for yet."""
        with self.database.tx() as db:
            want(db, candidates)

    def unanswered(self, keys: Iterable[str], version: str) -> dict[str, dict]:
        """The candidates of `keys` with no answer to `version`."""
        with self.database.tx() as db:
            rows = db.execute(
                "SELECT key, candidate FROM judgment WHERE key = ANY(%s)"
                " AND (result IS NULL OR version IS DISTINCT FROM %s)",
                (sorted(set(keys)), version),
            ).fetchall()
        return {r["key"]: r["candidate"] for r in rows}

    def answers(self, keys: Iterable[str], version: str | None = None) -> dict[str, dict]:
        """The answers to `version` for `keys`, or to any version without one."""
        with self.database.tx() as db:
            rows = db.execute(
                "SELECT key, result FROM judgment WHERE key = ANY(%s) AND result IS NOT NULL"
                " AND (%s::text IS NULL OR version = %s)",
                (sorted(set(keys)), version, version),
            ).fetchall()
        return {r["key"]: r["result"] for r in rows}

    def answer(self, key: str, version: str, model: str, result: dict) -> None:
        with self.database.tx() as db:
            db.execute(
                "UPDATE judgment SET version = %s, model = %s, result = %s, answered_at = %s"
                " WHERE key = %s",
                (version, model, Jsonb(result), now_iso(), key),
            )


def want(db: Connection, candidates: dict[str, dict]) -> None:
    if not candidates:
        return
    stamp = now_iso()
    db.cursor().executemany(
        "INSERT INTO judgment (key, candidate, created_at) VALUES (%s, %s, %s)"
        " ON CONFLICT (key) DO NOTHING",
        [(k, Jsonb(c), stamp) for k, c in candidates.items()],
    )
