"""The intake worker (ADR 0005): NOTIFY wakes it, a poll covers lost notifications, one
worker per store, and a lost session ends it so systemd can restart it."""

import dataclasses
import threading
import time

import psycopg
import pytest
from psycopg import sql

from conftest import TEST_DSN
from event_radar import config
from event_radar.intake import IntakeService
from event_radar.store import WorkerBusy


class Worker:
    def __init__(self, service: IntakeService):
        self.service = service
        self.store = service.store
        self.done: list[int] = []
        self.error: BaseException | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        service.process = self._process

    def _process(self, intake_id: int) -> None:
        self.done.append(intake_id)
        self.store.update_intake(intake_id, state="done")

    def _serve(self, poll_s: float) -> None:
        try:
            self.service.serve(poll_s=poll_s, stop=self._stop.is_set)
        except BaseException as exc:
            self.error = exc

    def start(self, poll_s: float) -> None:
        self._thread = threading.Thread(target=self._serve, args=(poll_s,), daemon=True)
        self._thread.start()
        # The lock is taken after LISTEN, so once it is held the worker is listening.
        assert until(lambda: self.busy() or self.error), self.error
        assert self.error is None, self.error

    def busy(self) -> bool:
        try:
            with self.store.intake_listener():
                return False
        except WorkerBusy:
            return True

    def stop(self) -> None:
        if self._thread and self._thread.is_alive():
            self._stop.set()
            with self.store.tx() as db:
                db.execute("SELECT pg_notify(%s, '')", (f"{self.store.schema}.intake",))
            self._thread.join(10)
            assert not self._thread.is_alive()


# Generous for a loaded 4 GB laptop, yet far below the 60 s poll some tests rely on.
def until(condition, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def worker(tmp_path, store):
    settings = dataclasses.replace(
        config.load(), data_dir=tmp_path, database_url=None, openrouter_key=None
    )
    w = Worker(IntakeService(settings, store))
    yield w
    w.stop()


def _insert(store, state: str) -> int:
    with store.tx() as db:
        return db.execute(
            "INSERT INTO intake (url, state, created_at, updated_at)"
            " VALUES ('https://example.org/', %s, now(), now()) RETURNING id",
            (state,),
        ).fetchone()["id"]


def _state(store, intake_id: int) -> str:
    return store.intake(intake_id)["state"]


def test_only_becoming_queued_is_announced(store):
    with psycopg.connect(TEST_DSN, autocommit=True) as listener:
        listener.execute(sql.SQL("LISTEN {}").format(sql.Identifier(f"{store.schema}.intake")))

        def heard() -> list[str]:
            return [n.payload for n in listener.notifies(timeout=0.5)]

        queued = store.create_intake("https://example.org/", None, None)
        assert heard() == [str(queued)]
        waiting = _insert(store, "review")
        store.update_intake(queued, state="checking")
        assert heard() == []
        store.update_intake(waiting, state="queued", approved=True)
        store.requeue_interrupted_intakes()
        assert sorted(heard()) == sorted([str(waiting), str(queued)])
        store.update_intake(queued, name="renamed")
        assert heard() == []


def test_a_notification_wakes_the_worker_long_before_the_poll(worker):
    worker.start(poll_s=60)
    intake_id = worker.store.create_intake("https://example.org/", None, None)
    assert until(lambda: worker.done == [intake_id]), worker.error


def test_a_lost_notification_only_waits_for_the_poll(worker):
    with worker.store.tx() as db:
        db.execute("ALTER TABLE intake DISABLE TRIGGER intake_queued")
    worker.start(poll_s=0.5)
    intake_id = worker.store.create_intake("https://example.org/", None, None)
    assert until(lambda: worker.done == [intake_id]), worker.error


def test_a_suggestion_in_review_is_not_worked_until_approved(worker):
    waiting = _insert(worker.store, "review")
    queued = _insert(worker.store, "queued")
    worker.start(poll_s=60)
    assert until(lambda: worker.done == [queued]), worker.error
    time.sleep(0.3)
    assert worker.done == [queued] and _state(worker.store, waiting) == "review"

    worker.service.decide(waiting, approve=True)
    assert until(lambda: worker.done == [queued, waiting]), worker.error


def test_one_worker_per_store_and_a_restart_resumes_cut_off_work(worker):
    cut_off = _insert(worker.store, "harvesting")
    worker.start(poll_s=60)
    assert until(lambda: worker.done == [cut_off]), worker.error
    with pytest.raises(WorkerBusy):
        worker.service.serve(poll_s=0.1, stop=lambda: True)


def test_a_lost_session_ends_the_worker(worker):
    worker.start(poll_s=0.5)
    with worker.store.tx() as db:
        ended = db.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
            " WHERE application_name = 'event-radar intake worker' AND pid <> pg_backend_pid()"
        ).fetchall()
    assert ended
    assert until(lambda: worker.error is not None)
    assert isinstance(worker.error, psycopg.OperationalError)
    assert not worker.busy()
