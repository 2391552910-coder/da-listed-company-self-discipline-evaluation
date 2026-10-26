"""Controlled evaluation races over real SQLite; generation is explicitly mocked.

Each concurrent submitter has its own service/database/provider instance. The
barriers force both initial key lookups and both final creation lookups to return
empty, exercising transactional rollback of a losing run and its answer rows.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from threading import Barrier

import pytest

from app.documents.common import DomainError, dump
from app.documents.jobs import JobControl
from app.documents.service import Documents
from app.evaluation.service import Evaluations
from test_evaluation_service import ready, MockProvider, submit, claim, expire


@pytest.mark.parametrize("same_payload", [True, False], ids=["same-request", "conflicting-request"])
def test_concurrent_evaluations_commit_one_job_run_and_answer_without_orphans(ready, monkeypatch, same_payload):
    initial_lookups_finished = Barrier(2)
    creation_lookups_finished = Barrier(2)
    key = "evaluation-concurrent-submit"
    services = []
    providers = []
    identity_calls = [0, 0]

    class IsolatedMockProvider(MockProvider):
        def __init__(self, position):
            super().__init__()
            self.position = position

        def identity(self, model=None):
            value = super().identity(model)
            identity_calls[self.position] += 1
            # identity() is reached only after the resource-independent key
            # lookup has returned empty for this submitter.
            initial_lookups_finished.wait(timeout=15)
            return value

    for position in range(2):
        provider = IsolatedMockProvider(position)
        service = Evaluations(Documents(ready.docs.settings), provider)
        original_new_run = service.new_run

        def synchronized_new_run(db, request, parent_run_id=None, original=original_new_run):
            # This is after the second key lookup and immediately before any
            # run/answer mutation. Both callers must exercise the insert race.
            creation_lookups_finished.wait(timeout=15)
            return original(db, request, parent_run_id)

        monkeypatch.setattr(service, "new_run", synchronized_new_run)
        services.append(service)
        providers.append(provider)

    def enqueue(position):
        changes = {} if same_payload or position == 0 else {"budget": {"max_output_tokens": 9000}}
        try:
            return submit(ready, services[position], key=key, **changes)
        except DomainError as error:
            return error

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="evaluation-submit") as executor:
        futures = [executor.submit(enqueue, position) for position in range(2)]
        results = [future.result(timeout=40) for future in futures]

    assert identity_calls == [1, 1]
    assert providers[0] is not providers[1] and providers[0].calls == providers[1].calls == []
    successes = [result for result in results if isinstance(result, dict)]
    failures = [result for result in results if isinstance(result, DomainError)]
    if same_payload:
        assert len(successes) == 2 and failures == []
        assert successes[0]["id"] == successes[1]["id"]
        assert successes[0]["run_id"] == successes[1]["run_id"]
    else:
        assert len(successes) == 1 and len(failures) == 1
        assert failures[0].code == "IDEMPOTENCY_CONFLICT" and failures[0].status == 409
    winner = successes[0]
    with ready.docs.db.session() as db:
        jobs = db.rows("SELECT * FROM t_job WHERE kind='evaluation'")
        runs = db.rows("SELECT * FROM t_evaluation_run")
        answers = db.rows("SELECT * FROM t_evaluation_answer")
        calls = db.rows("SELECT * FROM t_model_call")
    assert len(jobs) == len(runs) == len(answers) == 1
    assert calls == []
    assert jobs[0]["id"] == winner["id"] and jobs[0]["idempotency_key"] == key
    assert runs[0]["id"] == winner["run_id"] and runs[0]["job_id"] == jobs[0]["id"]
    assert answers[0]["run_id"] == runs[0]["id"] and answers[0]["question_key"] == "custom-1"
    assert jobs[0]["status"] == runs[0]["status"] == answers[0]["status"] == "queued"


def pending_call_after_reclaim(ready):
    provider = MockProvider()
    service = Evaluations(ready.docs, provider)
    task = submit(ready, service)
    job, old_owner = claim(ready, task, owner="stale-evaluation-worker")
    provider.hook = lambda *args: (_ for _ in ()).throw(SystemExit("explicit simulated provider boundary crash"))
    with pytest.raises(SystemExit):
        service.build(json.loads(job["payload_json"]), job["id"], old_owner,
            lambda: JobControl(ready.docs).checkpoint(job["id"], old_owner))
    assert len(provider.calls) == 1
    with ready.docs.db.session() as db:
        before_call = db.one("SELECT * FROM t_model_call WHERE run_id=?", (task["run_id"],))
        before_answer = db.one("SELECT * FROM t_evaluation_answer WHERE run_id=?", (task["run_id"],))
        run = db.one("SELECT * FROM t_evaluation_run WHERE id=?", (task["run_id"],))
    assert before_call["status"] == "in_flight" and before_call["attempt_number"] == 1
    assert before_answer["status"] == "running" and before_answer["snapshot_key"]
    expire(ready, task)
    reclaimed = ready.docs.claim_job("replacement-evaluation-worker")
    assert reclaimed["id"] == task["id"] and reclaimed["attempt_count"] == 2
    return service, task, old_owner, before_call, before_answer, run


def test_old_worker_cannot_complete_model_call_after_replacement_lease(ready):
    service, task, old_owner, before_call, before_answer, run = pending_call_after_reclaim(ready)
    late_response = {"text": dump({"answer": "迟到响应，不应发布。", "evidence_status": "insufficient",
        "limitations": ["仅为隔离单元测试"], "claims": []}),
        "usage": {"prompt_tokens": 100, "completion_tokens": 80, "total_tokens": 180},
        "model": "explicit-unit-mock", "request_id": "late-unit-response", "finish_reason": "stop"}
    with pytest.raises(DomainError) as error:
        service._complete_call(before_call, late_response, task["id"], old_owner)
    assert error.value.code == "LEASE_LOST"
    with ready.docs.db.session() as db:
        after_call = db.one("SELECT * FROM t_model_call WHERE id=?", (before_call["id"],))
        after_answer = db.one("SELECT * FROM t_evaluation_answer WHERE id=?", (before_answer["id"],))
        current = db.one("SELECT * FROM t_job WHERE id=?", (task["id"],))
    assert after_call == before_call
    assert after_answer == before_answer
    assert current["lease_owner"] == "replacement-evaluation-worker" and current["attempt_count"] == 2
    assert current["status"] == "running"


def test_old_worker_cannot_publish_answer_after_replacement_lease(ready):
    service, task, old_owner, before_call, before_answer, run = pending_call_after_reclaim(ready)
    frozen = service._read_answer(before_answer, run["request_sha256"])
    late_answer = deepcopy(frozen)
    late_answer.update(status="succeeded", error_code=None, answer_text="旧Worker迟到的正文，不应成为有效快照。")
    with pytest.raises(DomainError) as error:
        service._save_answer(late_answer, run["request_sha256"], task["id"], old_owner)
    assert error.value.code == "LEASE_LOST"
    with ready.docs.db.session() as db:
        after_answer = db.one("SELECT * FROM t_evaluation_answer WHERE id=?", (before_answer["id"],))
        after_call = db.one("SELECT * FROM t_model_call WHERE id=?", (before_call["id"],))
    assert after_answer == before_answer
    assert after_call == before_call
    assert service._read_answer(after_answer, run["request_sha256"])["answer_text"] is None
