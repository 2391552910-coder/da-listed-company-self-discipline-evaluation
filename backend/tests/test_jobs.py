"""Task control contracts: cancellation, leases, retry lineage and attempt history.

Tests use real SQLite transactions. No model, parser, network or API is called.
"""
from datetime import datetime, timedelta, timezone
import json
import sys

import pytest

from app.documents.common import DomainError, dump, now, sha, uid
from app.documents.jobs import JobControl
from app.documents.service import Documents
from app.documents.settings import Settings


@pytest.fixture
def docs(tmp_path):
    service = Documents(Settings(
        "sqlite:///" + str(tmp_path / "jobs.sqlite"),
        tmp_path / "assets",
        sys.executable,
    ))
    service.db.migrate()
    return service


def enqueue(docs, kind="parse", payload=None, key=None):
    payload = payload if payload is not None else {
        "version_id": "frozen-version",
        "config": {"chunk_chars": 1200, "overlap_chars": 160},
        "private_value": "not public",
    }
    with docs.db.session() as db:
        return docs._enqueue(db, kind, payload, key)


def row(docs, job_id):
    with docs.db.session() as db:
        return db.one("SELECT * FROM t_job WHERE id=?", (job_id,))


def mark_running(docs, job_id, owner="worker-a", expired=False):
    lease = (datetime.now(timezone.utc) + timedelta(seconds=-60 if expired else 300)).isoformat()
    with docs.db.session() as db:
        db.execute("""UPDATE t_job SET status='running',lease_owner=?,
                      lease_until=?,attempt_count=1 WHERE id=?""", (owner, lease, job_id))
        db.execute("""INSERT INTO t_job_attempt
                      (id,job_id,worker_id,attempt_number,started_at,status)
                      VALUES(?,?,?,?,?,?)""", (uid(), job_id, owner, 1, now(), "running"))


def index_row(docs, status="failed"):
    index_id = uid()
    config = {"parse_ids": ["annual-parse", "law-parse"], "profile": "qwen_cuda", "rerank": True}
    with docs.db.session() as db:
        db.execute("""INSERT INTO t_index_version
                      (id,status,profile,config_json,created_at) VALUES(?,?,?,?,?)""",
                   (index_id, status, "qwen_cuda", dump(config), now()))
    return index_id, config


def context_job(docs):
    index_id, _ = index_row(docs, "ready")
    run_id = uid()
    payload = {"run_id": run_id, "index_id": index_id, "query": "公司披露依据",
               "stock_code": "000001", "report_year": 2024}
    job = enqueue(docs, "context", payload)
    request = dump({"query": payload["query"], "index_id": index_id})
    with docs.db.session() as db:
        db.execute("""INSERT INTO t_retrieval_run
                      (id,job_id,index_id,request_json,request_sha256,status,created_at)
                      VALUES(?,?,?,?,?,?,?)""",
                   (run_id, job["id"], index_id, request, sha(request.encode()), "queued", now()))
    return job, run_id


def test_cancel_queued_is_immediate_idempotent_and_not_claimable(docs):
    job = enqueue(docs)
    controls = JobControl(docs)
    first = controls.cancel(job["id"])
    second = controls.cancel(job["id"])
    assert first["status"] == second["status"] == "cancelled"
    assert row(docs, job["id"])["cancel_requested"] == 1
    assert docs.claim_job("other-worker") is None


def test_cancel_running_is_cooperative_and_checkpoint_rejects(docs):
    job = enqueue(docs)
    mark_running(docs, job["id"])
    controls = JobControl(docs)
    assert controls.cancel(job["id"])["status"] == "running"
    with pytest.raises(DomainError) as caught:
        controls.checkpoint(job["id"], "worker-a")
    assert caught.value.code == "TASK_CANCELLED"
    assert row(docs, job["id"])["status"] == "running"


def test_cancel_completed_job_preserves_result_and_history(docs):
    job = enqueue(docs)
    mark_running(docs, job["id"])
    controls = JobControl(docs)
    controls.finish(job["id"], "worker-a", result={"parse_id": "already-saved"})
    before = row(docs, job["id"])
    assert controls.cancel(job["id"])["status"] == "succeeded"
    after = row(docs, job["id"])
    assert before["result_json"] == after["result_json"]
    assert before["error_code"] == after["error_code"]


@pytest.mark.parametrize("operation", ["checkpoint", "finish"])
@pytest.mark.parametrize("expired", [False, True])
def test_foreign_or_expired_lease_cannot_mutate_result(docs, operation, expired):
    job = enqueue(docs)
    mark_running(docs, job["id"], expired=expired)
    owner = "worker-a" if expired else "wrong-worker"
    controls = JobControl(docs)
    with pytest.raises(DomainError) as caught:
        if operation == "finish":
            controls.finish(job["id"], owner, result={"should_not_commit": True})
        else:
            controls.checkpoint(job["id"], owner)
    assert caught.value.code == "LEASE_LOST"
    assert row(docs, job["id"])["status"] == "running"
    assert row(docs, job["id"])["result_json"] is None


def test_checkpoint_renews_lease_without_changing_attempt_number(docs):
    job = enqueue(docs)
    mark_running(docs, job["id"])
    before = row(docs, job["id"])
    JobControl(docs).checkpoint(job["id"], "worker-a")
    after = row(docs, job["id"])
    assert after["lease_until"] > before["lease_until"]
    assert after["attempt_count"] == before["attempt_count"] == 1


def test_cancellation_wins_over_successful_worker_result(docs):
    job, run_id = context_job(docs)
    mark_running(docs, job["id"])
    controls = JobControl(docs)
    controls.cancel(job["id"])
    result = controls.finish(job["id"], "worker-a", result={"snapshot": "not-success"})
    assert result["status"] == "cancelled"
    assert result.get("result") is None
    with docs.db.session() as db:
        attempt = db.one("SELECT * FROM t_job_attempt WHERE job_id=?", (job["id"],))
        run = db.one("SELECT * FROM t_retrieval_run WHERE id=?", (run_id,))
    assert attempt["status"] == run["status"] == "cancelled"
    assert attempt["finished_at"]
    assert attempt["error_code"] == "TASK_CANCELLED"


def test_budget_exhaustion_is_distinct_terminal_state(docs):
    job, run_id = context_job(docs)
    mark_running(docs, job["id"])
    result = JobControl(docs).finish(job["id"], "worker-a", error="BUDGET_EXHAUSTED")
    assert result["status"] == "budget_exhausted"
    with docs.db.session() as db:
        attempt = db.one("SELECT * FROM t_job_attempt WHERE job_id=?", (job["id"],))
        run = db.one("SELECT * FROM t_retrieval_run WHERE id=?", (run_id,))
    assert attempt["status"] == run["status"] == "budget_exhausted"
    assert attempt["error_code"] == "BUDGET_EXHAUSTED"


def test_finish_records_success_in_current_attempt(docs):
    job = enqueue(docs)
    mark_running(docs, job["id"])
    result = JobControl(docs).finish(job["id"], "worker-a", result={})
    assert result["status"] == "succeeded" and result["result"] == {}
    with docs.db.session() as db:
        attempt = db.one("SELECT * FROM t_job_attempt WHERE job_id=?", (job["id"],))
    assert attempt["status"] == "succeeded"
    assert json.loads(attempt["result_json"]) == {}
    assert attempt["finished_at"]


@pytest.mark.parametrize("terminal", ["failed", "cancelled", "budget_exhausted"])
def test_retry_preserves_original_payload_and_links_new_job(docs, terminal):
    original = enqueue(docs)
    with docs.db.session() as db:
        db.execute("UPDATE t_job SET status=?,error_code='original-error' WHERE id=?",
                   (terminal, original["id"]))
    before = row(docs, original["id"])
    retried = JobControl(docs).retry(original["id"], key="retry-this-job")
    new = row(docs, retried["id"])
    assert retried["id"] != original["id"]
    assert new["status"] == "queued" and new["attempt_count"] == 0
    assert new["parent_job_id"] == original["id"]
    assert json.loads(new["payload_json"]) == json.loads(before["payload_json"])
    after = row(docs, original["id"])
    assert before == after


@pytest.mark.parametrize("state", ["queued", "running", "succeeded"])
def test_retry_active_or_successful_job_is_rejected(docs, state):
    job = enqueue(docs)
    with docs.db.session() as db:
        db.execute("UPDATE t_job SET status=? WHERE id=?", (state, job["id"]))
    with pytest.raises(DomainError) as caught:
        JobControl(docs).retry(job["id"])
    assert caught.value.status == 409


def test_retry_idempotency_is_scoped_to_original_job(docs):
    controls = JobControl(docs)
    first, second = enqueue(docs), enqueue(docs)
    with docs.db.session() as db:
        db.execute("UPDATE t_job SET status='failed' WHERE id IN (?,?)", (first["id"], second["id"]))
    retried = controls.retry(first["id"], key="shared-retry-key")
    assert controls.retry(first["id"], key="shared-retry-key")["id"] == retried["id"]
    with pytest.raises(DomainError) as caught:
        controls.retry(second["id"], key="shared-retry-key")
    assert caught.value.code == "IDEMPOTENCY_CONFLICT"
    with docs.db.session() as db:
        count = db.one("SELECT COUNT(*) AS n FROM t_job")["n"]
    assert count == 3


def test_index_retry_allocates_new_index_without_touching_ready_original(docs):
    old_index, config = index_row(docs, "ready")
    original = enqueue(docs, "index", config | {"index_id": old_index})
    with docs.db.session() as db:
        db.execute("UPDATE t_job SET status='failed' WHERE id=?", (original["id"],))
    controls = JobControl(docs)
    retried = controls.retry(original["id"], key="index-retry")
    payload = json.loads(row(docs, retried["id"])["payload_json"])
    assert payload["index_id"] != old_index
    assert {k: v for k, v in payload.items() if k != "index_id"} == config
    assert controls.retry(original["id"], key="index-retry")["id"] == retried["id"]
    with docs.db.session() as db:
        old = db.one("SELECT * FROM t_index_version WHERE id=?", (old_index,))
        new = db.one("SELECT * FROM t_index_version WHERE id=?", (payload["index_id"],))
        count = db.one("SELECT COUNT(*) AS n FROM t_index_version")["n"]
    assert old["status"] == "ready" and new["status"] == "staging"
    assert json.loads(new["config_json"]) == config
    assert count == 2


def test_context_retry_callback_creates_and_binds_new_run(docs, monkeypatch):
    original, old_run_id = context_job(docs)
    with docs.db.session() as db:
        db.execute("UPDATE t_job SET status='failed' WHERE id=?", (original["id"],))
        db.execute("UPDATE t_retrieval_run SET status='failed' WHERE id=?", (old_run_id,))
    calls = []
    def prepare(kind, payload, db):
        assert kind == "context"
        old = db.one("SELECT * FROM t_retrieval_run WHERE id=?", (payload["run_id"],))
        new_id = uid()
        db.execute("""INSERT INTO t_retrieval_run
                      (id,index_id,request_json,request_sha256,status,parent_run_id,created_at)
                      VALUES(?,?,?,?,?,?,?)""",
                   (new_id, old["index_id"], old["request_json"], old["request_sha256"],
                    "queued", old["id"], now()))
        calls.append(new_id)
        return payload | {"run_id": new_id}
    monkeypatch.setattr(docs, "retry_payload", prepare, raising=False)
    retried = JobControl(docs).retry(original["id"], key="context-retry")
    assert JobControl(docs).retry(original["id"], key="context-retry")["id"] == retried["id"]
    payload = json.loads(row(docs, retried["id"])["payload_json"])
    assert payload["run_id"] != old_run_id and len(calls) == 1
    with docs.db.session() as db:
        new_run = db.one("SELECT * FROM t_retrieval_run WHERE id=?", (payload["run_id"],))
        old_run = db.one("SELECT * FROM t_retrieval_run WHERE id=?", (old_run_id,))
    assert new_run["job_id"] == retried["id"]
    assert new_run["parent_run_id"] == old_run_id
    assert old_run["status"] == "failed"


def test_cancel_queued_context_also_cancels_run(docs):
    job, run_id = context_job(docs)
    JobControl(docs).cancel(job["id"])
    with docs.db.session() as db:
        run = db.one("SELECT * FROM t_retrieval_run WHERE id=?", (run_id,))
    assert run["status"] == "cancelled"


def test_list_pages_stably_and_does_not_expose_internal_payload(docs):
    first = enqueue(docs)
    second = enqueue(docs)
    third = enqueue(docs)
    with docs.db.session() as db:
        for stamp, job in zip(["2024-01-01", "2024-01-02", "2024-01-03"], [first, second, third]):
            db.execute("UPDATE t_job SET created_at=? WHERE id=?", (stamp, job["id"]))
        db.execute("UPDATE t_job SET status='failed' WHERE id=?", (second["id"],))
    controls = JobControl(docs)
    listing = controls.list(page=1, size=2)
    assert listing["total"] == 3
    assert [r["id"] for r in listing["items"]] == [third["id"], second["id"]]
    assert [r["id"] for r in controls.list(page=2, size=2)["items"]] == [first["id"]]
    filtered = controls.list(status="failed")
    assert filtered["total"] == 1 and filtered["items"][0]["id"] == second["id"]
    assert "private_value" not in dump(listing)
    assert all("payload_json" not in r and "request_sha256" not in r for r in listing["items"])


@pytest.mark.parametrize("kwargs", [{"page": 0}, {"size": 0}, {"size": 201}, {"status": "made-up"}])
def test_invalid_list_filters_are_rejected(docs, kwargs):
    with pytest.raises(DomainError) as caught:
        JobControl(docs).list(**kwargs)
    assert caught.value.status == 422


def test_finish_write_boundary_honors_racing_cancellation(docs, monkeypatch):
    from contextlib import contextmanager

    job = enqueue(docs)
    mark_running(docs, job["id"])
    original_session = docs.db.session
    injected = []

    @contextmanager
    def interleaved_session():
        with original_session() as db:
            original_execute = db.execute
            def execute(sql, params=()):
                if "status=CASE WHEN cancel_requested=1" in sql and not injected:
                    original_execute("UPDATE t_job SET cancel_requested=1 WHERE id=?", (job["id"],))
                    injected.append(True)
                return original_execute(sql, params)
            db.execute = execute
            yield db

    monkeypatch.setattr(docs.db, "session", interleaved_session)
    result = JobControl(docs).finish(job["id"], "worker-a", result={"late_success": True})
    assert injected
    assert result["status"] == "cancelled"
    assert result["error_code"] == "TASK_CANCELLED"
    assert result["result"] is None


def test_old_worker_cannot_finish_a_reclaimed_attempt(docs):
    job = enqueue(docs)
    mark_running(docs, job["id"], owner="old-worker")
    future = (datetime.now(timezone.utc) + timedelta(seconds=300)).isoformat()
    with docs.db.session() as db:
        db.execute("UPDATE t_job SET lease_owner='new-worker',lease_until=?,attempt_count=2 WHERE id=?",
                   (future, job["id"]))
        db.execute("UPDATE t_job_attempt SET status='lease_expired',finished_at=? WHERE job_id=?",
                   (now(), job["id"]))
        db.execute("""INSERT INTO t_job_attempt
                      (id,job_id,worker_id,attempt_number,started_at,status)
                      VALUES(?,?,?,?,?,?)""", (uid(), job["id"], "new-worker", 2, now(), "running"))
    controls = JobControl(docs)
    with pytest.raises(DomainError) as caught:
        controls.finish(job["id"], "old-worker", result={"stale": True})
    assert caught.value.code == "LEASE_LOST"
    controls.finish(job["id"], "new-worker", result={"current": True})
    with docs.db.session() as db:
        attempts = db.rows("SELECT * FROM t_job_attempt WHERE job_id=? ORDER BY attempt_number", (job["id"],))
    assert attempts[0]["status"] == "lease_expired"
    assert attempts[0]["result_json"] is None
    assert attempts[1]["status"] == "succeeded"
    assert json.loads(attempts[1]["result_json"]) == {"current": True}


def test_context_retry_rejects_reused_run_identity_atomically(docs, monkeypatch):
    job, old_run_id = context_job(docs)
    with docs.db.session() as db:
        db.execute("UPDATE t_job SET status='failed' WHERE id=?", (job["id"],))
    monkeypatch.setattr(docs, "retry_payload", lambda kind, payload, db: payload, raising=False)
    with pytest.raises(DomainError) as caught:
        JobControl(docs).retry(job["id"], key="invalid-run-retry")
    assert caught.value.code == "RETRY_IDENTITY_REUSED"
    with docs.db.session() as db:
        assert db.one("SELECT COUNT(*) AS n FROM t_job")["n"] == 1
        assert db.one("SELECT COUNT(*) AS n FROM t_retrieval_run")["n"] == 1


def test_finish_without_current_attempt_rolls_back_job_result(docs):
    job = enqueue(docs)
    mark_running(docs, job["id"])
    with docs.db.session() as db:
        db.execute("DELETE FROM t_job_attempt WHERE job_id=?", (job["id"],))
    with pytest.raises(DomainError) as caught:
        JobControl(docs).finish(job["id"], "worker-a", result={"must_rollback": True})
    assert caught.value.code == "ATTEMPT_MISMATCH"
    saved = row(docs, job["id"])
    assert saved["status"] == "running"
    assert saved["result_json"] is None
    assert saved["lease_until"]
