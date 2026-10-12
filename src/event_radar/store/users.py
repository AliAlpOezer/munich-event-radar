from psycopg import errors

from ..model import UserStatus
from .database import Repository, now_iso


class Users(Repository):
    """Admins and the statuses people set on events (ADR 0005)."""

    def set_owner_status(self, event_key: str, status: UserStatus) -> UserStatus:
        """The owner's status (the pre-login web app). The database checks the transition."""
        with self.database.tx() as db:
            owner = db.execute("SELECT user_id FROM admin WHERE role = 'owner'").fetchone()
            try:
                if owner:
                    db.execute(
                        "INSERT INTO user_state (user_id, event_key, status, updated_at)"
                        " VALUES (%s, %s, %s, %s) ON CONFLICT (user_id, event_key) DO UPDATE"
                        " SET status = excluded.status, updated_at = excluded.updated_at",
                        (owner["user_id"], event_key, status, now_iso()),
                    )
                else:
                    db.execute(
                        "INSERT INTO user_state_unclaimed (event_key, status, updated_at)"
                        " VALUES (%s, %s, %s) ON CONFLICT (event_key) DO UPDATE"
                        " SET status = excluded.status, updated_at = excluded.updated_at",
                        (event_key, status, now_iso()),
                    )
            except errors.ForeignKeyViolation as exc:
                raise KeyError(event_key) from exc
            except errors.CheckViolation as exc:
                raise ValueError(exc.diag.message_primary) from exc
        return status

    def grant_admin(self, email: str, owner: bool = False) -> dict:
        """Make a user who has logged in once an admin. The owner also receives the
        unclaimed statuses; the user's own rows win where both exist."""
        role = "owner" if owner else "admin"
        with self.database.tx() as db:
            user = db.execute(
                "SELECT id FROM auth.users WHERE lower(email) = lower(%s)", (email,)
            ).fetchone()
            if user is None:
                raise LookupError(f"No user with email {email}; they must log in once first")
            try:
                db.execute(
                    "INSERT INTO admin (user_id, role) VALUES (%s, %s)"
                    " ON CONFLICT (user_id) DO UPDATE SET role = excluded.role",
                    (user["id"], role),
                )
            except errors.UniqueViolation as exc:
                raise ValueError("There is already an owner") from exc
            claimed = 0
            if owner:
                claimed = db.execute(
                    "INSERT INTO user_state (user_id, event_key, status, feedback, updated_at)"
                    " SELECT %s, event_key, status, feedback, updated_at"
                    " FROM user_state_unclaimed ON CONFLICT DO NOTHING",
                    (user["id"],),
                ).rowcount
                db.execute("DELETE FROM user_state_unclaimed")
        return {"user_id": str(user["id"]), "role": role, "claimed": claimed}
