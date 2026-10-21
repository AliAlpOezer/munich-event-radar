from collections.abc import Sequence

from psycopg.types.json import Jsonb

from ..model import Evidence, Observation, ParsedEvent, SourceClass, json_key
from .database import Repository, now_iso
from .judgments import want


class Observations(Repository):
    """What each parse of a page said about its events, before resolution."""

    def add(
        self,
        snapshot_hash: str,
        url: str,
        source_class: SourceClass,
        method: str,
        events: Sequence[ParsedEvent],
        candidates: Sequence[dict | None] = (),
    ) -> int:
        """Keep what one parse said, each event with the candidate it is judged by
        (`judgments.candidates`, ADR 0018), which waits for its judgment."""
        observed = now_iso()
        keys = [json_key(c) if c else None for c in candidates] or [None] * len(events)
        with self.database.tx() as db:
            want(db, {k: c for k, c in zip(keys, candidates, strict=False) if k})
            db.cursor().executemany(
                "INSERT INTO observation (snapshot_hash, url, source_class, method,"
                " item, platform_keys, fields, links, observed_at, judgment_key)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                [
                    (
                        snapshot_hash,
                        url,
                        source_class.value,
                        method,
                        item,
                        Jsonb(sorted(event.platform_keys)),
                        Jsonb(
                            {
                                k: {"value": v.value, "quote": v.quote}
                                for k, v in event.fields.items()
                            }
                        ),
                        Jsonb(list(event.links)),
                        observed,
                        keys[item],
                    )
                    for item, event in enumerate(events)
                ],
            )
        return len(events)

    def name_candidates(self, named: dict[int, tuple[str, dict]]) -> None:
        """Give observations, by id, the key and candidate of their event (ADR 0018)."""
        if not named:
            return
        with self.database.tx() as db:
            want(db, dict(named.values()))
            db.cursor().executemany(
                "UPDATE observation SET judgment_key = %s WHERE id = %s",
                [(key, ident) for ident, (key, _) in named.items()],
            )

    def all(self) -> list[Observation]:
        with self.database.tx() as db:
            rows = db.execute("SELECT * FROM observation ORDER BY id").fetchall()
        return [_observation(r) for r in rows]

    def of_url(self, url: str) -> list[Observation]:
        with self.database.tx() as db:
            rows = db.execute(
                "SELECT * FROM observation WHERE url = %s ORDER BY id", (url,)
            ).fetchall()
        return [_observation(r) for r in rows]


def _observation(row: dict) -> Observation:
    return Observation(
        id=row["id"],
        snapshot_hash=row["snapshot_hash"],
        url=row["url"],
        source_class=SourceClass(row["source_class"]),
        method=row["method"],
        platform_keys=row["platform_keys"],
        fields={k: Evidence(v["value"], v.get("quote")) for k, v in row["fields"].items()},
        observed_at=row["observed_at"].isoformat(),
        links=row["links"],
        judgment_key=row["judgment_key"],
    )
