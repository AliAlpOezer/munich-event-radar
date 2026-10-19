import os
import uuid
from pathlib import Path

import pytest

from event_radar.profile import Profile
from event_radar.store import Store

# Tests never read RADAR_DATABASE_URL, so they cannot reach the hosted database.
TEST_DSN = os.environ.get("RADAR_TEST_DATABASE_URL", "postgresql://postgres@localhost/radar_test")


@pytest.fixture
def store():
    with Store.scratch(TEST_DSN) as scratch:
        yield scratch


@pytest.fixture
def users(store):
    made = []

    def make(email: str | None = None) -> uuid.UUID:
        user = uuid.uuid4()
        with store.tx() as db:
            db.execute(
                "INSERT INTO auth.users (id, email) VALUES (%s, %s)",
                (user, email or f"{user}@example.org"),
            )
        made.append(user)
        return user

    yield make
    with store.tx() as db:
        db.execute("DELETE FROM auth.users WHERE id = ANY(%s)", (made,))


@pytest.fixture(scope="session")
def profile() -> Profile:
    return Profile.load(Path(__file__).resolve().parents[1] / "profile.yaml")
