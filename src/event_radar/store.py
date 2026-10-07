import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .model import Evidence, Observation, SourceClass

SCHEMA = """
CREATE TABLE IF NOT EXISTS organiser (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    domain TEXT NOT NULL,
    homepage TEXT,
    kind TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'demoted', 'removed', 'dormant')),
    discovered_via TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    discovery_url TEXT,
    UNIQUE (domain, name)
);
CREATE TABLE IF NOT EXISTS source (
    id INTEGER PRIMARY KEY,
    organiser_id INTEGER REFERENCES organiser(id),
    url TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('feed', 'listing', 'event_page')),
    source_class TEXT NOT NULL CHECK (source_class IN ('TICKET', 'OWN', 'AGG')),
    browser INTEGER NOT NULL DEFAULT 0,
    cadence_hours INTEGER NOT NULL,
    added_at TEXT NOT NULL,
    added_via TEXT NOT NULL,
    last_fetched_at TEXT,
    last_ok_at TEXT,
    last_yield INTEGER,
    max_yield INTEGER NOT NULL DEFAULT 0,
    empty_streak INTEGER NOT NULL DEFAULT 0,
    error_streak INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS snapshot (
    id INTEGER PRIMARY KEY,
    url TEXT NOT NULL,
    content_hash TEXT,
    fetched_at TEXT NOT NULL,
    http_status INTEGER,
    content_type TEXT,
    etag TEXT,
    last_modified TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS snapshot_by_url ON snapshot (url, fetched_at);
CREATE TABLE IF NOT EXISTS observation (
    id INTEGER PRIMARY KEY,
    snapshot_hash TEXT NOT NULL,
    url TEXT NOT NULL,
    source_class TEXT NOT NULL,
    method TEXT NOT NULL,
    item INTEGER NOT NULL,
    platform_keys TEXT NOT NULL,
    fields TEXT NOT NULL,
    links TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    UNIQUE (snapshot_hash, url, method, item)
);
CREATE TABLE IF NOT EXISTS event (
    key TEXT PRIMARY KEY,
    fields TEXT NOT NULL,
    merge_basis TEXT NOT NULL,
    tier TEXT,
    tier_reasons TEXT,
    drop_reason TEXT,
    first_seen TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS event_alias (
    alias TEXT PRIMARY KEY,
    event_key TEXT NOT NULL REFERENCES event(key)
);
CREATE TABLE IF NOT EXISTS user_state (
    event_key TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'none',
    feedback INTEGER,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS push_ledger (
    event_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    fact_hash TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'sent')),
    created_at TEXT NOT NULL,
    sent_at TEXT,
    message_id TEXT,
    UNIQUE (event_key, kind, fact_hash)
);
CREATE TABLE IF NOT EXISTS budget (
    provider TEXT NOT NULL,
    day TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (provider, day)
);
CREATE TABLE IF NOT EXISTS extraction (
    content_hash TEXT NOT NULL,
    version TEXT NOT NULL,
    model TEXT NOT NULL,
    result TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (content_hash, version)
);
CREATE TABLE IF NOT EXISTS extraction_job (
    snapshot_hash TEXT NOT NULL,
    url TEXT NOT NULL,
    priority INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'done', 'needs_look')),
    PRIMARY KEY (snapshot_hash, url)
);
CREATE TABLE IF NOT EXISTS run (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    outcome TEXT,
    summary TEXT
);
"""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _evidence_json(fields: dict[str, Evidence]) -> str:
    return json.dumps(
        {k: {"value": v.value, "quote": v.quote} for k, v in fields.items()},
        ensure_ascii=False,
        sort_keys=True,
    )


def _evidence_load(raw: str) -> dict[str, Evidence]:
    return {k: Evidence(v["value"], v.get("quote")) for k, v in json.loads(raw).items()}


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.tx() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def record_snapshot(self, url: str, **cols) -> None:
        cols = {"url": url, "fetched_at": now_iso(), **cols}
        names = ", ".join(cols)
        marks = ", ".join("?" for _ in cols)
        with self.tx() as db:
            db.execute(f"INSERT INTO snapshot ({names}) VALUES ({marks})", tuple(cols.values()))

    def latest_snapshot(self, url: str) -> sqlite3.Row | None:
        with self.tx() as db:
            return db.execute(
                "SELECT * FROM snapshot WHERE url = ? AND content_hash IS NOT NULL"
                " ORDER BY id DESC LIMIT 1",
                (url,),
            ).fetchone()

    def add_observations(
        self, snapshot_hash: str, url: str, source_class: SourceClass, method: str, events
    ) -> int:
        observed = now_iso()
        with self.tx() as db:
            for item, event in enumerate(events):
                db.execute(
                    "INSERT OR IGNORE INTO observation (snapshot_hash, url, source_class, method,"
                    " item, platform_keys, fields, links, observed_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        snapshot_hash,
                        url,
                        source_class.value,
                        method,
                        item,
                        json.dumps(sorted(event.platform_keys)),
                        _evidence_json(event.fields),
                        json.dumps(list(event.links)),
                        observed,
                    ),
                )
        return len(events)

    def enqueue_extraction(self, snapshot_hash: str, url: str, priority: int) -> None:
        with self.tx() as db:
            db.execute(
                "INSERT OR IGNORE INTO extraction_job (snapshot_hash, url, priority)"
                " VALUES (?, ?, ?)",
                (snapshot_hash, url, priority),
            )

    def pending_extractions(self, limit: int) -> list[sqlite3.Row]:
        with self.tx() as db:
            return db.execute(
                "SELECT * FROM extraction_job WHERE state = 'pending'"
                " ORDER BY priority, attempts LIMIT ?",
                (limit,),
            ).fetchall()

    def finish_extraction(self, snapshot_hash: str, url: str, state: str) -> None:
        with self.tx() as db:
            db.execute(
                "UPDATE extraction_job SET state = ?, attempts = attempts + 1"
                " WHERE snapshot_hash = ? AND url = ?",
                (state, snapshot_hash, url),
            )

    def cached_extraction(self, content_hash: str, version: str) -> dict | None:
        with self.tx() as db:
            row = db.execute(
                "SELECT result FROM extraction WHERE content_hash = ? AND version = ?",
                (content_hash, version),
            ).fetchone()
        return json.loads(row["result"]) if row else None

    def cache_extraction(self, content_hash: str, version: str, model: str, result: dict) -> None:
        with self.tx() as db:
            db.execute(
                "INSERT OR REPLACE INTO extraction VALUES (?, ?, ?, ?, ?)",
                (content_hash, version, model, json.dumps(result, ensure_ascii=False), now_iso()),
            )

    def spend(self, provider: str, cap: int) -> bool:
        day = datetime.now(UTC).date().isoformat()
        with self.tx() as db:
            db.execute(
                "INSERT OR IGNORE INTO budget (provider, day) VALUES (?, ?)", (provider, day)
            )
            cursor = db.execute(
                "UPDATE budget SET used = used + 1 WHERE provider = ? AND day = ? AND used < ?",
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
                platform_keys=json.loads(r["platform_keys"]),
                fields=_evidence_load(r["fields"]),
                observed_at=r["observed_at"],
                links=json.loads(r["links"]),
            )
            for r in rows
        ]

    def add_organiser(self, name: str, domain: str, via: str, **cols) -> int:
        with self.tx() as db:
            tomb = db.execute(
                "SELECT 1 FROM organiser WHERE domain = ? AND status = 'removed'", (domain,)
            ).fetchone()
            if tomb:
                return 0
            db.execute(
                "INSERT OR IGNORE INTO organiser (name, domain, discovered_via, discovered_at,"
                " homepage, kind, discovery_url) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    name,
                    domain,
                    via,
                    now_iso(),
                    cols.get("homepage"),
                    cols.get("kind"),
                    cols.get("discovery_url"),
                ),
            )
            return db.execute(
                "SELECT id FROM organiser WHERE domain = ? AND name = ?", (domain, name)
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
                "INSERT OR IGNORE INTO source (organiser_id, url, kind, source_class, browser,"
                " cadence_hours, added_at, added_via) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    organiser_id,
                    url,
                    kind,
                    source_class,
                    int(browser),
                    cadence_hours,
                    now_iso(),
                    via,
                ),
            )

    def due_sources(self, now: str, browser: bool = False) -> list[sqlite3.Row]:
        with self.tx() as db:
            return db.execute(
                "SELECT s.* FROM source s LEFT JOIN organiser o ON o.id = s.organiser_id"
                " WHERE s.browser = ? AND COALESCE(o.status, 'active') NOT IN ('removed')"
                " AND (s.last_fetched_at IS NULL OR"
                "      datetime(s.last_fetched_at, '+' || s.cadence_hours || ' hours')"
                "      <= datetime(?))"
                " ORDER BY s.last_fetched_at IS NOT NULL, s.kind = 'event_page', s.id",
                (int(browser), now),
            ).fetchall()

    def mark_source(self, source_id: int, ok: bool, error: str | None, yielded: int | None):
        stamp = now_iso()
        with self.tx() as db:
            if not ok:
                db.execute(
                    "UPDATE source SET last_fetched_at = ?, error_streak = error_streak + 1,"
                    " last_error = ? WHERE id = ?",
                    (stamp, error, source_id),
                )
                return
            db.execute(
                "UPDATE source SET last_fetched_at = ?, last_ok_at = ?, error_streak = 0,"
                " last_error = NULL, last_yield = ?, max_yield = MAX(max_yield, ?),"
                " empty_streak = CASE WHEN ? = 0 AND max_yield > 0 THEN empty_streak + 1"
                " ELSE 0 END WHERE id = ?",
                (stamp, stamp, yielded, yielded, yielded, source_id),
            )

    def set_cadence(self, urls: list[str], hours: int) -> None:
        with self.tx() as db:
            db.executemany(
                "UPDATE source SET cadence_hours = ? WHERE url = ? AND kind = 'event_page'",
                [(hours, u) for u in urls],
            )

    def save_events(self, resolved: list[tuple[set[str], dict, str, object]]) -> dict[str, int]:
        stamp = now_iso()
        counts = {"new": 0, "updated": 0, "merged": 0}
        with self.tx() as db:
            for aliases, fields, basis, verdict in resolved:
                marks = ",".join("?" for _ in aliases)
                found = db.execute(
                    f"SELECT DISTINCT e.key, e.first_seen FROM event_alias a JOIN event e"
                    f" ON e.key = a.event_key WHERE a.alias IN ({marks}) ORDER BY e.first_seen",
                    tuple(aliases),
                ).fetchall()
                key = found[0]["key"] if found else uuid.uuid4().hex[:12]
                for loser in found[1:]:
                    db.execute(
                        "UPDATE event_alias SET event_key = ? WHERE event_key = ?",
                        (key, loser["key"]),
                    )
                    db.execute(
                        "UPDATE OR IGNORE user_state SET event_key = ? WHERE event_key = ?",
                        (key, loser["key"]),
                    )
                    db.execute("DELETE FROM user_state WHERE event_key = ?", (loser["key"],))
                    db.execute("DELETE FROM event WHERE key = ?", (loser["key"],))
                    counts["merged"] += 1
                counts["updated" if found else "new"] += 1
                db.execute(
                    "INSERT INTO event (key, fields, merge_basis, tier, tier_reasons, drop_reason,"
                    " first_seen, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT (key) DO UPDATE SET fields = excluded.fields,"
                    " merge_basis = excluded.merge_basis, tier = excluded.tier,"
                    " tier_reasons = excluded.tier_reasons, drop_reason = excluded.drop_reason,"
                    " updated_at = excluded.updated_at",
                    (
                        key,
                        json.dumps(fields, ensure_ascii=False, sort_keys=True),
                        basis,
                        verdict.tier,
                        json.dumps(verdict.reasons),
                        verdict.drop_reason,
                        stamp,
                        stamp,
                    ),
                )
                db.executemany(
                    "INSERT OR IGNORE INTO event_alias VALUES (?, ?)", [(a, key) for a in aliases]
                )
        return counts

    def events(self) -> list[sqlite3.Row]:
        with self.tx() as db:
            return db.execute(
                "SELECT e.*, COALESCE(u.status, 'none') AS user_status FROM event e"
                " LEFT JOIN user_state u ON u.event_key = e.key"
            ).fetchall()

    def start_run(self) -> int:
        with self.tx() as db:
            return db.execute("INSERT INTO run (started_at) VALUES (?)", (now_iso(),)).lastrowid

    def finish_run(self, run_id: int, outcome: str, summary: dict) -> None:
        with self.tx() as db:
            db.execute(
                "UPDATE run SET finished_at = ?, outcome = ?, summary = ? WHERE id = ?",
                (now_iso(), outcome, json.dumps(summary), run_id),
            )
