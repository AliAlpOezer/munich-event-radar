import json
import sqlite3
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
        ensure_ascii=False, sort_keys=True,
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
                        snapshot_hash, url, source_class.value, method, item,
                        json.dumps(sorted(event.platform_keys)), _evidence_json(event.fields),
                        json.dumps(list(event.links)), observed,
                    ),
                )
        return len(events)

    def observations(self) -> list[Observation]:
        with self.tx() as db:
            rows = db.execute("SELECT * FROM observation ORDER BY id").fetchall()
        return [
            Observation(
                id=r["id"], snapshot_hash=r["snapshot_hash"], url=r["url"],
                source_class=SourceClass(r["source_class"]), method=r["method"],
                platform_keys=json.loads(r["platform_keys"]), fields=_evidence_load(r["fields"]),
                observed_at=r["observed_at"], links=json.loads(r["links"]),
            )
            for r in rows
        ]
