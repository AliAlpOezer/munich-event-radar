"""One-off move of a SQLite radar database into the Postgres store (ADR 0004, step 1)."""

import json
import sqlite3
from pathlib import Path

from psycopg import sql
from psycopg.types.json import Jsonb

from .store import Store

# Parents before children, for the foreign keys.
TABLES = (
    "organiser",
    "source",
    "snapshot",
    "observation",
    "event",
    "event_alias",
    "user_state",
    "push_ledger",
    "budget",
    "extraction",
    "extraction_job",
    "intake",
    "run",
)


def _convert(value, kind: str):
    if value is None:
        return None
    if kind == "jsonb":
        return Jsonb(json.loads(value))
    if kind == "boolean":
        return bool(value)
    return value


def run(store: Store, path: Path) -> dict[str, int]:
    """Copy every row, keeping ids, into an empty store. All or nothing."""
    if not path.is_file():
        raise FileNotFoundError(path)
    source = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    counts = {}
    try:
        with store.tx() as db:
            if db.execute("SELECT 1 FROM organiser LIMIT 1").fetchone():
                raise RuntimeError(f"schema {store.schema} already holds data; import refused")
            for table in TABLES:
                kinds = {
                    r["column_name"]: r["data_type"]
                    for r in db.execute(
                        "SELECT column_name, data_type FROM information_schema.columns"
                        " WHERE table_schema = %s AND table_name = %s",
                        (store.schema, table),
                    )
                }
                rows = source.execute(f"SELECT * FROM {table}").fetchall()
                counts[table] = len(rows)
                if not rows:
                    continue
                columns = [c for c in rows[0].keys() if c in kinds]
                query = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                    sql.Identifier(table),
                    sql.SQL(", ").join(map(sql.Identifier, columns)),
                    sql.SQL(", ").join(sql.Placeholder() * len(columns)),
                )
                db.cursor().executemany(
                    query, [[_convert(r[c], kinds[c]) for c in columns] for r in rows]
                )
                if "id" in kinds:
                    db.execute(
                        sql.SQL(
                            "SELECT setval(pg_get_serial_sequence({}, 'id'), max(id)) FROM {}"
                        ).format(sql.Literal(f"{store.schema}.{table}"), sql.Identifier(table))
                    )
    finally:
        source.close()
    return counts
