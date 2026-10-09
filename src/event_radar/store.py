import uuid
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from psycopg import Connection, sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .model import USER_TRANSITIONS, Evidence, Observation, SourceClass, UserStatus

MIGRATIONS = Path(__file__).with_name("migrations")
Row = dict


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _evidence_json(fields: dict[str, Evidence]) -> Jsonb:
    return Jsonb({k: {"value": v.value, "quote": v.quote} for k, v in fields.items()})


def _evidence_load(raw: dict) -> dict[str, Evidence]:
    return {k: Evidence(v["value"], v.get("quote")) for k, v in raw.items()}


def _migrate(db: Connection, schema: str) -> None:
    db.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
    db.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (schema,))
    db.execute(
        "CREATE TABLE IF NOT EXISTS schema_migration"
        " (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
    )
    done = {r["version"] for r in db.execute("SELECT version FROM schema_migration")}
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if path.stem not in done:
            db.execute(path.read_text(encoding="utf-8"))
            db.execute("INSERT INTO schema_migration (version) VALUES (%s)", (path.stem,))


class Store:
    """The radar's Postgres store (ADR 0004). All tables live in `schema`, which Supabase
    does not expose; connect through the session pooler, since each connection sets its
    search path once."""

    def __init__(self, dsn: str | None, schema: str = "radar"):
        if not dsn:
            raise ValueError("RADAR_DATABASE_URL is not set")
        self.schema = schema

        def configure(db: Connection) -> None:
            db.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            db.execute("SET TIME ZONE 'UTC'")
            db.commit()

        self.pool = ConnectionPool(
            dsn,
            min_size=1,
            max_size=4,
            kwargs={"row_factory": dict_row},
            configure=configure,
            open=True,
        )
        weakref.finalize(self, self.pool.close)
        self.pool.wait(timeout=15)
        with self.tx() as db:
            _migrate(db, schema)

    @classmethod
    @contextmanager
    def scratch(cls, dsn: str | None) -> Iterator["Store"]:
        """A throwaway store in its own schema, dropped afterwards: replays and tests."""
        store = cls(dsn, f"scratch_{uuid.uuid4().hex[:12]}")
        try:
            yield store
        finally:
            with store.tx() as db:
                db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(store.schema)))
            store.close()

    def close(self) -> None:
        self.pool.close()

    @contextmanager
    def tx(self) -> Iterator[Connection]:
        with self.pool.connection() as db:
            yield db

    def record_snapshot(self, url: str, **cols) -> None:
        cols = {"url": url, "fetched_at": now_iso(), **cols}
        query = sql.SQL("INSERT INTO snapshot ({}) VALUES ({})").format(
            sql.SQL(", ").join(map(sql.Identifier, cols)),
            sql.SQL(", ").join(sql.Placeholder() * len(cols)),
        )
        with self.tx() as db:
            db.execute(query, tuple(cols.values()))

    def latest_snapshot(self, url: str) -> Row | None:
        with self.tx() as db:
            return db.execute(
                "SELECT * FROM snapshot WHERE url = %s AND content_hash IS NOT NULL"
                " ORDER BY id DESC LIMIT 1",
                (url,),
            ).fetchone()

    def add_observations(
        self, snapshot_hash: str, url: str, source_class: SourceClass, method: str, events
    ) -> int:
        observed = now_iso()
        with self.tx() as db:
            db.cursor().executemany(
                "INSERT INTO observation (snapshot_hash, url, source_class, method,"
                " item, platform_keys, fields, links, observed_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                [
                    (
                        snapshot_hash,
                        url,
                        source_class.value,
                        method,
                        item,
                        Jsonb(sorted(event.platform_keys)),
                        _evidence_json(event.fields),
                        Jsonb(list(event.links)),
                        observed,
                    )
                    for item, event in enumerate(events)
                ],
            )
        return len(events)

    def enqueue_extraction(self, snapshot_hash: str, url: str, priority: int) -> None:
        with self.tx() as db:
            db.execute(
                "INSERT INTO extraction_job (snapshot_hash, url, priority)"
                " VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (snapshot_hash, url, priority),
            )

    def pending_extractions(self, limit: int, urls: list[str] | None = None) -> list[Row]:
        only = " AND url = ANY(%s)" if urls is not None else ""
        params = (urls, limit) if urls is not None else (limit,)
        with self.tx() as db:
            return db.execute(
                f"SELECT * FROM extraction_job WHERE state = 'pending'{only}"
                " ORDER BY priority, attempts LIMIT %s",
                params,
            ).fetchall()

    def finish_extraction(self, snapshot_hash: str, url: str, state: str) -> None:
        with self.tx() as db:
            db.execute(
                "UPDATE extraction_job SET state = %s, attempts = attempts + 1"
                " WHERE snapshot_hash = %s AND url = %s",
                (state, snapshot_hash, url),
            )

    def cached_extraction(self, content_hash: str, version: str) -> dict | None:
        with self.tx() as db:
            row = db.execute(
                "SELECT result FROM extraction WHERE content_hash = %s AND version = %s",
                (content_hash, version),
            ).fetchone()
        return row["result"] if row else None

    def cache_extraction(self, content_hash: str, version: str, model: str, result: dict) -> None:
        with self.tx() as db:
            db.execute(
                "INSERT INTO extraction (content_hash, version, model, result, created_at)"
                " VALUES (%s, %s, %s, %s, %s) ON CONFLICT (content_hash, version) DO UPDATE"
                " SET model = excluded.model, result = excluded.result,"
                " created_at = excluded.created_at",
                (content_hash, version, model, Jsonb(result), now_iso()),
            )

    def spend(self, provider: str, cap: int) -> bool:
        day = datetime.now(UTC).date()
        with self.tx() as db:
            db.execute(
                "INSERT INTO budget (provider, day) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (provider, day),
            )
            cursor = db.execute(
                "UPDATE budget SET used = used + 1 WHERE provider = %s AND day = %s AND used < %s",
                (provider, day, cap),
            )
            return cursor.rowcount == 1

    def observations(self) -> list[Observation]:
        with self.tx() as db:
            rows = db.execute("SELECT * FROM observation ORDER BY id").fetchall()
        return [
            Observation(
                id=r["id"],
                snapshot_hash=r["snapshot_hash"],
                url=r["url"],
                source_class=SourceClass(r["source_class"]),
                method=r["method"],
                platform_keys=r["platform_keys"],
                fields=_evidence_load(r["fields"]),
                observed_at=r["observed_at"].isoformat(),
                links=r["links"],
            )
            for r in rows
        ]

    def add_organiser(self, name: str, domain: str, via: str, **cols) -> int:
        ai_focus = bool(cols.get("ai_focus", False))
        with self.tx() as db:
            tomb = db.execute(
                "SELECT 1 FROM organiser WHERE domain = %s AND status = 'removed'", (domain,)
            ).fetchone()
            if tomb:
                return 0
            db.execute(
                "INSERT INTO organiser (name, domain, discovered_via, discovered_at,"
                " homepage, kind, ai_focus, discovery_url)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (
                    name,
                    domain,
                    via,
                    now_iso(),
                    cols.get("homepage"),
                    cols.get("kind"),
                    ai_focus,
                    cols.get("discovery_url"),
                ),
            )
            # The seed file owns ai_focus on seed rows, so a re-import applies its edits.
            if via == "seed":
                db.execute(
                    "UPDATE organiser SET ai_focus = %s WHERE domain = %s AND name = %s"
                    " AND discovered_via = 'seed'",
                    (ai_focus, domain, name),
                )
            return db.execute(
                "SELECT id FROM organiser WHERE domain = %s AND name = %s", (domain, name)
            ).fetchone()["id"]

    def add_source(
        self,
        url: str,
        kind: str,
        source_class: str,
        cadence_hours: int,
        via: str,
        organiser_id: int | None = None,
        browser: bool = False,
    ) -> None:
        with self.tx() as db:
            db.execute(
                "INSERT INTO source (organiser_id, url, kind, source_class, browser,"
                " cadence_hours, added_at, added_via) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT DO NOTHING",
                (
                    organiser_id,
                    url,
                    kind,
                    source_class,
                    bool(browser),
                    cadence_hours,
                    now_iso(),
                    via,
                ),
            )

    def organiser_by_domain(self, domain: str) -> Row | None:
        with self.tx() as db:
            return db.execute(
                "SELECT * FROM organiser WHERE domain = %s ORDER BY status = 'removed' DESC",
                (domain,),
            ).fetchone()

    def set_organiser_status(self, organiser_id: int, status: str) -> None:
        with self.tx() as db:
            db.execute("UPDATE organiser SET status = %s WHERE id = %s", (status, organiser_id))

    def source(self, url: str) -> Row | None:
        with self.tx() as db:
            return db.execute("SELECT * FROM source WHERE url = %s", (url,)).fetchone()

    def organiser_sources(self, organiser_id: int) -> list[Row]:
        with self.tx() as db:
            return db.execute(
                "SELECT * FROM source WHERE organiser_id = %s ORDER BY id", (organiser_id,)
            ).fetchall()

    def focused_urls(self) -> set[str]:
        with self.tx() as db:
            return {
                r["url"]
                for r in db.execute(
                    "SELECT s.url FROM source s JOIN organiser o ON o.id = s.organiser_id"
                    " WHERE o.ai_focus"
                )
            }

    def due_sources(self, now: str, browser: bool = False) -> list[Row]:
        with self.tx() as db:
            return db.execute(
                "SELECT s.* FROM source s LEFT JOIN organiser o ON o.id = s.organiser_id"
                " WHERE s.browser = %s AND COALESCE(o.status, 'active') <> 'removed'"
                " AND (s.last_fetched_at IS NULL"
                "      OR s.last_fetched_at + make_interval(hours => s.cadence_hours)"
                "         <= %s::timestamptz)"
                " ORDER BY s.last_fetched_at IS NOT NULL, s.kind = 'event_page', s.id",
                (bool(browser), now),
            ).fetchall()

    def mark_source(self, source_id: int, ok: bool, error: str | None, yielded: int | None):
        stamp = now_iso()
        with self.tx() as db:
            if not ok:
                db.execute(
                    "UPDATE source SET last_fetched_at = %s, error_streak = error_streak + 1,"
                    " last_error = %s WHERE id = %s",
                    (stamp, error, source_id),
                )
                return
            db.execute(
                "UPDATE source SET last_fetched_at = %(at)s, last_ok_at = %(at)s,"
                " error_streak = 0, last_error = NULL, last_yield = %(n)s,"
                " max_yield = GREATEST(max_yield, %(n)s),"
                " empty_streak = CASE WHEN %(n)s = 0 AND max_yield > 0 THEN empty_streak + 1"
                " ELSE 0 END WHERE id = %(id)s",
                {"at": stamp, "n": yielded, "id": source_id},
            )

    def set_cadence(self, urls: list[str], hours: int) -> None:
        with self.tx() as db:
            db.execute(
                "UPDATE source SET cadence_hours = %s WHERE url = ANY(%s) AND kind = 'event_page'",
                (hours, urls),
            )

    def save_events(
        self, resolved: list[tuple[set[str], dict, str, object, list[str]]]
    ) -> dict[str, int]:
        stamp = now_iso()
        counts = {"new": 0, "updated": 0, "merged": 0}
        with self.tx() as db:
            for aliases, fields, basis, verdict, sources in resolved:
                found = db.execute(
                    "SELECT DISTINCT e.key, e.first_seen FROM event_alias a JOIN event e"
                    " ON e.key = a.event_key WHERE a.alias = ANY(%s) ORDER BY e.first_seen",
                    (list(aliases),),
                ).fetchall()
                key = found[0]["key"] if found else uuid.uuid4().hex[:12]
                for loser in found[1:]:
                    db.execute(
                        "UPDATE event_alias SET event_key = %s WHERE event_key = %s",
                        (key, loser["key"]),
                    )
                    db.execute(
                        "UPDATE user_state SET event_key = %(key)s WHERE event_key = %(loser)s"
                        " AND NOT EXISTS (SELECT 1 FROM user_state WHERE event_key = %(key)s)",
                        {"key": key, "loser": loser["key"]},
                    )
                    db.execute("DELETE FROM user_state WHERE event_key = %s", (loser["key"],))
                    db.execute("DELETE FROM event WHERE key = %s", (loser["key"],))
                    counts["merged"] += 1
                counts["updated" if found else "new"] += 1
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
                        Jsonb(fields),
                        basis,
                        verdict.tier,
                        Jsonb(verdict.reasons),
                        verdict.drop_reason,
                        Jsonb(sorted(sources)),
                        stamp,
                        stamp,
                    ),
                )
                db.cursor().executemany(
                    "INSERT INTO event_alias VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    [(a, key) for a in aliases],
                )
        return counts

    def events(self) -> list[Row]:
        with self.tx() as db:
            return db.execute(
                "SELECT e.*, COALESCE(u.status, 'none') AS user_status FROM event e"
                " LEFT JOIN user_state u ON u.event_key = e.key"
            ).fetchall()

    def organisers(self) -> list[Row]:
        with self.tx() as db:
            return db.execute("SELECT * FROM organiser ORDER BY name").fetchall()

    def sources(self) -> list[Row]:
        with self.tx() as db:
            return db.execute("SELECT * FROM source ORDER BY id").fetchall()

    def runs(self, limit: int) -> list[Row]:
        with self.tx() as db:
            return db.execute("SELECT * FROM run ORDER BY id DESC LIMIT %s", (limit,)).fetchall()

    def set_user_status(self, event_key: str, status: UserStatus) -> UserStatus:
        with self.tx() as db:
            if not db.execute("SELECT 1 FROM event WHERE key = %s", (event_key,)).fetchone():
                raise KeyError(event_key)
            row = db.execute(
                "SELECT status FROM user_state WHERE event_key = %s FOR UPDATE", (event_key,)
            ).fetchone()
            current = UserStatus(row["status"]) if row else UserStatus.NONE
            if status not in USER_TRANSITIONS[current]:
                raise ValueError(f"{current} cannot become {status}")
            db.execute(
                "INSERT INTO user_state (event_key, status, updated_at) VALUES (%s, %s, %s)"
                " ON CONFLICT (event_key) DO UPDATE SET status = excluded.status,"
                " updated_at = excluded.updated_at",
                (event_key, status.value, now_iso()),
            )
        return status

    def create_intake(self, url: str, events_url: str | None, name: str | None) -> int:
        stamp = now_iso()
        with self.tx() as db:
            return db.execute(
                "INSERT INTO intake (url, events_url, name, created_at, updated_at)"
                " VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (url, events_url, name, stamp, stamp),
            ).fetchone()["id"]

    def intake(self, intake_id: int) -> Row | None:
        with self.tx() as db:
            return db.execute("SELECT * FROM intake WHERE id = %s", (intake_id,)).fetchone()

    def intakes(self, limit: int) -> list[Row]:
        with self.tx() as db:
            return db.execute("SELECT * FROM intake ORDER BY id DESC LIMIT %s", (limit,)).fetchall()

    def update_intake(self, intake_id: int, **cols) -> None:
        cols = {**cols, "updated_at": now_iso()}
        for name in ("profile", "result"):
            if cols.get(name) is not None:
                cols[name] = Jsonb(cols[name])
        query = sql.SQL("UPDATE intake SET {} WHERE id = %s").format(
            sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(name)) for name in cols)
        )
        with self.tx() as db:
            db.execute(query, (*cols.values(), intake_id))

    def log_intake(self, intake_id: int, step: str, text: str, level: str = "info") -> None:
        entry = {"at": now_iso(), "step": step, "text": text, "level": level}
        with self.tx() as db:
            db.execute(
                "UPDATE intake SET log = log || %s, updated_at = %s WHERE id = %s",
                (Jsonb([entry]), entry["at"], intake_id),
            )

    def next_intake(self) -> Row | None:
        with self.tx() as db:
            return db.execute(
                "SELECT * FROM intake WHERE state = 'queued' ORDER BY id LIMIT 1"
            ).fetchone()

    def requeue_interrupted_intakes(self) -> int:
        with self.tx() as db:
            return db.execute(
                "UPDATE intake SET state = 'queued' WHERE state IN"
                " ('checking', 'profiling', 'finding', 'harvesting')"
            ).rowcount

    def start_run(self) -> int:
        with self.tx() as db:
            return db.execute(
                "INSERT INTO run (started_at) VALUES (%s) RETURNING id", (now_iso(),)
            ).fetchone()["id"]

    def finish_run(self, run_id: int, outcome: str, summary: dict) -> None:
        with self.tx() as db:
            db.execute(
                "UPDATE run SET finished_at = %s, outcome = %s, summary = %s WHERE id = %s",
                (now_iso(), outcome, Jsonb(summary), run_id),
            )
