from ..model import OrganiserStatus
from .database import Repository, now_iso


class Organisers(Repository):
    """The registry of organisers. A removed domain stays as a tombstone, so neither the
    seed nor an intake adds it again."""

    def add(self, name: str, domain: str, via: str, **cols) -> int:
        """The organiser's id, adding it if new; 0 when its domain was removed."""
        ai_focus = bool(cols.get("ai_focus", False))
        with self.database.tx() as db:
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

    def by_domain(self, domain: str) -> dict | None:
        with self.database.tx() as db:
            return db.execute(
                "SELECT * FROM organiser WHERE domain = %s ORDER BY status = 'removed' DESC",
                (domain,),
            ).fetchone()

    def set_status(self, organiser_id: int, status: OrganiserStatus) -> None:
        with self.database.tx() as db:
            db.execute("UPDATE organiser SET status = %s WHERE id = %s", (status, organiser_id))

    def all(self) -> list[dict]:
        with self.database.tx() as db:
            return db.execute("SELECT * FROM organiser ORDER BY name").fetchall()
