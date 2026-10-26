"""Controlled concurrent context submission over real SQLite transactions.

Both requests pass their initial lookup, then a barrier releases them only after
both have observed the second lookup empty. The loser must roll back any newly
created run and return the matching winner, or reject a different public request.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier, Lock, current_thread

import pytest

from app.documents.common import DomainError
from test_context_service import ready, submit


@pytest.mark.parametrize("same_request", [True, False], ids=["same-request", "conflicting-request"])
def test_concurrent_idempotency_returns_one_committed_job_and_run(ready, monkeypatch, same_request):
    docs = ready.docs
    key = "concurrent-context-key"
    both_empty = Barrier(2)
    count_lock = Lock()
    lookups = {}
    synchronized = set()
    original_session = docs.db.session

    @contextmanager
    def observed_session():
        with original_session() as db:
            original_one = db.one

            def one(sql, params=()):
                result = original_one(sql, params)
                # The initial resource-independent lookup is deliberately left
                # alone. Pause immediately after the creation transaction's key
                # lookup has returned empty, before either request writes a run.
                if ("FROM t_job" in sql and "idempotency_key" in sql and
                        tuple(params) == (key,)):
                    name = current_thread().name
                    with count_lock:
                        lookups[name] = lookups.get(name, 0) + 1
                        lookup_number = lookups[name]
                    if lookup_number == 2 and result is None:
                        with count_lock:
                            synchronized.add(name)
                        both_empty.wait(timeout=15)
                return result

            db.one = one
            yield db

    monkeypatch.setattr(docs.db, "session", observed_session)

    def enqueue(position):
        query = "审计委员会" if same_request or position == 0 else "审计委员会补充"
        try:
            return submit(ready, query=query, key=key)
        except DomainError as error:
            return error

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="context-submit") as executor:
        futures = [executor.submit(enqueue, position) for position in range(2)]
        results = [future.result(timeout=30) for future in futures]

    assert len(synchronized) == 2, "Both requests must reach the controlled empty-key race"
    successes = [result for result in results if isinstance(result, dict)]
    failures = [result for result in results if isinstance(result, DomainError)]
    if same_request:
        assert not failures
        assert len(successes) == 2
        assert successes[0]["id"] == successes[1]["id"]
        assert successes[0]["run_id"] == successes[1]["run_id"]
    else:
        assert len(successes) == 1 and len(failures) == 1
        assert failures[0].code == "IDEMPOTENCY_CONFLICT"
        assert failures[0].status == 409

    winner = successes[0]
    with docs.db.session() as db:
        jobs = db.rows("SELECT * FROM t_job WHERE kind='context'")
        runs = db.rows("SELECT * FROM t_retrieval_run")
    assert len(jobs) == 1
    assert len(runs) == 1
    assert jobs[0]["id"] == winner["id"]
    assert runs[0]["id"] == winner["run_id"]
    assert runs[0]["job_id"] == jobs[0]["id"]
    assert jobs[0]["idempotency_key"] == key
    assert runs[0]["status"] == jobs[0]["status"] == "queued"
