from .database import Repository, now_iso

# The daily harvest starts late by a varying amount (GitHub delays scheduled runs), so a
# source is due this much before its cadence passes: one fetched late yesterday is due today.
EARLY_HOURS = 6


class Sources(Repository):
    """The pages the radar fetches, each on its own cadence, with its fetch record."""

    def add(
        self,
        url: str,
        kind: str,
        source_class: str,
        cadence_hours: int,
        via: str,
        organiser_id: int | None = None,
        browser: bool = False,
    ) -> None:
        with self.database.tx() as db:
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

    def get(self, url: str) -> dict | None:
        with self.database.tx() as db:
            return db.execute("SELECT * FROM source WHERE url = %s", (url,)).fetchone()

    def of_organiser(self, organiser_id: int) -> list[dict]:
        with self.database.tx() as db:
            return db.execute(
                "SELECT * FROM source WHERE organiser_id = %s ORDER BY id", (organiser_id,)
            ).fetchall()

    def all(self) -> list[dict]:
        with self.database.tx() as db:
            return db.execute("SELECT * FROM source ORDER BY id").fetchall()

    def focused_urls(self) -> set[str]:
        """The sources of organisers whose main topic is AI."""
        return self._of_organisers("o.ai_focus")

    def startup_urls(self) -> set[str]:
        """The sources of startup organisers, whose every event is in scope (ADR 0011)."""
        return self._of_organisers("o.kind = 'startup'")

    def _of_organisers(self, where: str) -> set[str]:
        with self.database.tx() as db:
            return {
                r["url"]
                for r in db.execute(
                    "SELECT s.url FROM source s JOIN organiser o ON o.id = s.organiser_id"
                    f" WHERE {where}"
                )
            }

    def due(self, browser: bool = False) -> list[dict]:
        """Sources whose cadence has passed, less `EARLY_HOURS`: never fetched first, event
        pages last."""
        with self.database.tx() as db:
            return db.execute(
                "SELECT s.* FROM source s LEFT JOIN organiser o ON o.id = s.organiser_id"
                " WHERE s.browser = %s AND COALESCE(o.status, 'active') <> 'removed'"
                " AND (s.last_fetched_at IS NULL"
                "      OR s.last_fetched_at + make_interval(hours => s.cadence_hours - %s)"
                "         <= %s::timestamptz)"
                " ORDER BY s.last_fetched_at IS NOT NULL, s.kind = 'event_page', s.id",
                (bool(browser), EARLY_HOURS, now_iso()),
            ).fetchall()

    def mark(self, source_id: int, ok: bool, error: str | None, yielded: int | None) -> None:
        stamp = now_iso()
        with self.database.tx() as db:
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
        """Fetch these event pages every `hours`."""
        with self.database.tx() as db:
            db.execute(
                "UPDATE source SET cadence_hours = %s WHERE url = ANY(%s) AND kind = 'event_page'",
                (hours, urls),
            )
