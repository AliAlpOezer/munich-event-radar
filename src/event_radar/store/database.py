import hashlib
import uuid
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

from psycopg import Connection, errors, sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

MIGRATIONS = Path(__file__).with_name("migrations")
SQL = Path(__file__).with_name("sql")


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Database:
    """A connection pool on one schema of the radar's Postgres (ADR 0004). All tables live
    in `schema`, which Supabase does not expose; PostgREST sees only `api` (ADR 0005).
    Connect through the session pooler, since each connection sets its search path once."""

    def __init__(self, dsn: str | None, schema: str = "radar", api: str = "api"):
        if not dsn:
            raise ValueError("RADAR_DATABASE_URL is not set")
        self.dsn = dsn
        self.schema = schema
        self.api = api

        def configure(db: Connection) -> None:
            db.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            db.execute("SET TIME ZONE 'UTC'")
            db.commit()

        # Two, opened together: a job holding a lock works on the second.
        self.pool = ConnectionPool(
            dsn,
            min_size=2,
            max_size=4,
            kwargs={"row_factory": dict_row},
            configure=configure,
            open=True,
        )
        weakref.finalize(self, self.pool.close)
        self.pool.wait(timeout=15)
        with self.tx() as db:
            _migrate(db, schema, api)

    @classmethod
    @contextmanager
    def scratch(cls, dsn: str | None) -> Iterator[Self]:
        """A throwaway store in its own schema, dropped afterwards: replays and tests."""
        name = f"scratch_{uuid.uuid4().hex[:12]}"
        database = cls(dsn, name, f"{name}_api")
        try:
            yield database
        finally:
            with database.tx() as db:
                for schema in (database.api, database.schema):
                    db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
            database.close()

    def close(self) -> None:
        self.pool.close()

    @contextmanager
    def tx(self) -> Iterator[Connection]:
        with self.pool.connection() as db:
            yield db

    @contextmanager
    def lock(self, name: str) -> Iterator[None]:
        """Hold the store-wide lock `name` for the block, waiting while another job holds
        it (ADR 0006 D4). A session lock, so it outlives the transaction that took it."""
        key = f"{self.schema}.{name}"
        with self.pool.connection() as db:
            db.execute("SELECT pg_advisory_lock(hashtext(%s))", (key,))
            db.commit()
            try:
                yield
            finally:
                db.execute("SELECT pg_advisory_unlock(hashtext(%s))", (key,))


def _search_path(db: Connection, *schemas: str) -> None:
    path = ", ".join(sql.Identifier(s).as_string(db) for s in schemas)
    db.execute("SELECT set_config('search_path', %s, true)", (f"{path}, pg_temp",))


def _current(db: Connection, schema: str, api: str, versions: set[str], digest: str) -> bool:
    """Whether `schema` holds every migration and `api` the build of api.sql, asked in one
    statement; a schema never migrated has no tables to ask."""
    query = sql.SQL(
        "SELECT (SELECT array_agg(version) FROM {s}.schema_migration) AS done,"
        " (SELECT hash FROM {s}.api_build LIMIT 1) AS built,"
        " to_regnamespace(%s) IS NOT NULL AS present"
    ).format(s=sql.Identifier(schema))
    try:
        row = db.execute(query, (api,)).fetchone()
    except errors.UndefinedTable:
        db.rollback()
        return False
    return row["present"] and row["built"] == digest and versions <= set(row["done"] or ())


def _migrate(db: Connection, schema: str, api: str) -> None:
    """Apply pending migrations to `schema`, then rebuild `api` from api.sql if anything
    changed (ADR 0005). One transaction, serialised by an advisory lock."""
    api_sql = (SQL / "api.sql").read_text(encoding="utf-8")
    digest = hashlib.sha256(api_sql.encode()).hexdigest()
    if _current(db, schema, api, {p.stem for p in MIGRATIONS.glob("*.sql")}, digest):
        return
    db.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
    db.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (schema,))
    _search_path(db, schema)
    db.execute((SQL / "supabase_shim.sql").read_text(encoding="utf-8"))
    db.execute(
        "CREATE TABLE IF NOT EXISTS schema_migration"
        " (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
    )
    db.execute("CREATE TABLE IF NOT EXISTS api_build (hash text NOT NULL)")
    done = {r["version"] for r in db.execute("SELECT version FROM schema_migration")}
    pending = [p for p in sorted(MIGRATIONS.glob("*.sql")) if p.stem not in done]
    built = db.execute("SELECT hash FROM api_build").fetchone()
    present = db.execute("SELECT to_regnamespace(%s) IS NOT NULL AS ok", (api,)).fetchone()
    if not pending and present["ok"] and built and built["hash"] == digest:
        return
    db.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(api)))
    for path in pending:
        db.execute(path.read_text(encoding="utf-8"))
        db.execute("INSERT INTO schema_migration (version) VALUES (%s)", (path.stem,))
    unguarded = db.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = %s AND NOT rowsecurity", (schema,)
    ).fetchall()
    for row in unguarded:
        db.execute(
            sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(
                sql.Identifier(row["tablename"])
            )
        )
    db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(api)))
    _search_path(db, api, schema)
    db.execute(api_sql)
    _search_path(db, schema)
    db.execute("DELETE FROM api_build")
    db.execute("INSERT INTO api_build (hash) VALUES (%s)", (digest,))
    db.execute("NOTIFY pgrst, 'reload schema'")


class Repository:
    """The queries over one aggregate; each method is its own transaction."""

    def __init__(self, database: Database):
        self.database = database
