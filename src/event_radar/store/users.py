from psycopg import errors

from .database import Repository


class Users(Repository):
    """Admins and the statuses people set on events (ADR 0005)."""

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
