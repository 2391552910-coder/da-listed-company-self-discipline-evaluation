"""Controlled recovery races: stale sweepers cannot abandon a new owner's attempt.

The barrier pauses one worker immediately after reading an expired job, while a
second worker completes recovery. No timing sleeps or model calls are used.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event, current_thread
import sys

import pytest

from app.documents.common import DomainError
from app.documents.jobs import JobControl
from app.documents.service import Documents
from app.documents.settings import Settings


@pytest.fixture
def docs(tmp_path):
    service = Documents(Settings("sqlite:///" + str(tmp_path / "recovery.sqlite"),
                                 tmp_path / "assets", sys.executable))
    service.db.migrate()
    return service


def expired_job(docs):
    with docs.db.session() as db:
        queued = docs._enqueue(db, "parse", {"version_id": "unit-fixture", "config": {}})
    claimed = docs.claim_job("worker-old")
    assert claimed["id"] == queued["id"]
    with docs.db.session() as db:
        db.execute("UPDATE t_job SET lease_until=? WHERE id=?",
                   ("2000-01-01T00:00:00+00:00", queued["id"]))
    return queued["id"]


def attempts(docs, job_id):
    with docs.db.session() as db:
        return db.rows("SELECT * FROM t_job_attempt WHERE job_id=? ORDER BY attempt_number",
                       (job_id,))


def test_concurrent_recovery_cannot_abandon_the_new_attempt(docs, monkeypatch):
    job_id = expired_job(docs)
    stale_read, release_stale = Event(), Event()
    original_session = docs.db.session

    class ObservedSession:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def __getattr__(self, name):
            return getattr(self.wrapped, name)

        def rows(self, sql, params=()):
            result = self.wrapped.rows(sql, params)
            # Only the expired-job inventory read is paused. The business
            # assertions do not depend on the text of a particular UPDATE.
            inventory = ("FROM t_job" in sql and "lease_until" in sql and
                         any(row.get("id") == job_id and
                             row.get("lease_owner") == "worker-old" for row in result))
            if (current_thread().name.startswith("stale-recovery") and inventory and
                    not stale_read.is_set()):
                stale_read.set()
                if not release_stale.wait(15):
                    raise AssertionError("Recovery synchronization timed out")
            return result

    @contextmanager
    def observed_session():
        with original_session() as session:
            yield ObservedSession(session)

    monkeypatch.setattr(docs.db, "session", observed_session)

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="stale-recovery") as executor:
        delayed = executor.submit(docs.claim_job, "worker-a")
        try:
            assert stale_read.wait(15), "First worker never observed the expired attempt"
            winner = docs.claim_job("worker-b")
            assert winner["id"] == job_id and winner["attempt_count"] == 2
        finally:
            release_stale.set()
        assert delayed.result(timeout=15) is None

    history = attempts(docs, job_id)
    assert len(history) == 2
    assert history[0]["worker_id"] == "worker-old"
    assert history[0]["status"] in {"abandoned", "lease_expired"}
    assert history[1]["worker_id"] == "worker-b"
    assert history[1]["status"] == "running"
    finished = JobControl(docs).finish(job_id, "worker-b", result={"winner": "worker-b"})
    assert finished["status"] == "succeeded"
    assert finished["result"] == {"winner": "worker-b"}


@pytest.mark.parametrize("action", ["checkpoint", "finish"])
def test_old_owner_cannot_publish_after_a_real_recovery(docs, action):
    job_id = expired_job(docs)
    winner = docs.claim_job("worker-new")
    assert winner["attempt_count"] == 2
    jobs = JobControl(docs)
    with pytest.raises(DomainError) as caught:
        if action == "checkpoint":
            jobs.checkpoint(job_id, "worker-old")
        else:
            jobs.finish(job_id, "worker-old", result={"stale": True})
    assert caught.value.code == "LEASE_LOST"
    current = docs.job(job_id)
    assert current["status"] == "running" and current["result"] is None
    history = attempts(docs, job_id)
    assert history[-1]["worker_id"] == "worker-new"
    assert history[-1]["status"] == "running"
    assert history[-1]["result_json"] is None
    assert jobs.finish(job_id, "worker-new", result={"stale": False})["status"] == "succeeded"
