"""Service/worker/index integration over real SQLite and BM25 subprocesses.

The annual parser is the isolated unit fixture from test_documents; retrieval,
queue persistence, context assembly, hashing and snapshot replay use real code.
No GPU, generation API, or provider-token counting is claimed here.
"""
import json
import sys
from types import SimpleNamespace

import pytest

from app.documents.common import DomainError
from app.documents.indexes import Indexes
from app.documents.jobs import JobControl
from app.documents.service import Documents
from app.documents.settings import Settings
from app.documents.worker import work_once
from app.retrieval.service import Contexts
from test_documents import annual_fixture, parsed_law


@pytest.fixture
def ready(tmp_path_factory, monkeypatch):
    # Short names keep immutable SHA256 snapshot filenames within Windows limits.
    root = tmp_path_factory.mktemp("c")
    docs = Documents(Settings("sqlite:///" + str(root / "d.sqlite"), root / "a", sys.executable))
    docs.db.migrate()
    law, law_parse = parsed_law(docs)
    annual, annual_parse = annual_fixture(docs, monkeypatch)
    index_task = Indexes(docs).enqueue(
        [law_parse["parse_id"], annual_parse["parse_id"]],
        profile="bm25_diagnostic", rerank=False,
    )
    index_job = work_once(docs)
    assert index_job["id"] == index_task["id"] and index_job["status"] == "succeeded", index_job
    return SimpleNamespace(docs=docs, contexts=Contexts(docs),
                           index_id=index_job["result"]["index_id"], law=law, annual=annual)


def submit(ready, **changes):
    request = {
        "index_id": ready.index_id, "query": "审计委员会",
        "stock_code": "000001", "report_year": 2024,
        "annual_top_k": 3, "regulation_top_k": 3,
        "max_reference_tokens": 6000, "tokenizer_kind": "utf8_diagnostic",
        "max_runtime_seconds": 600,
    }
    request.update(changes)
    return ready.contexts.enqueue(**request)


def run_one(ready, task):
    completed = work_once(ready.docs)
    assert completed["id"] == task["id"], completed
    return completed, ready.contexts.get(task["run_id"])


def test_persisted_context_task_runs_and_replays_numbered_source_locators(ready):
    task = submit(ready)
    assert task["status"] == "queued"
    # Re-open the service as a new process would; tasks and requests are durable.
    reopened = Documents(ready.docs.settings)
    assert reopened.job(task["id"])["status"] == "queued"
    completed = work_once(reopened)
    assert completed["status"] == "succeeded", completed
    run = Contexts(reopened).get(task["run_id"])
    assert run["status"] == "succeeded"
    snapshot = run["snapshot"]
    context = snapshot["context"]
    assert context["budget_unit"] == "utf8_bytes"
    assert context["reference_token_count"] == len(context["context_text"].encode("utf-8"))
    assert context["reference_token_count"] <= snapshot["request"]["max_reference_tokens"]
    assert [record["evidence_id"] for record in context["source_records"]] == ["A1", "R1"]
    assert snapshot["semantic_support_verified"] is False
    assert snapshot["request"]["index_id"] == ready.index_id
    assert snapshot["attempt_number"] == 1
    for record in context["source_records"]:
        original = reopened.source(record["chunk_id"])
        assert original["content"] == record["content"]
        assert original["frame_text"][record["char_start"]:record["char_end"]] == record["content"]
        assert record["version_id"] == original["version_id"]
        assert record["physical_page"] == original["physical_page"]
        assert record["semantic_support_verified"] is False


def test_budget_keeps_complete_annual_chunk_and_records_regulation_omission(ready):
    large_task = submit(ready)
    large_job, large_run = run_one(ready, large_task)
    assert large_job["status"] == "succeeded"
    first_block = large_run["snapshot"]["context"]["context_text"].split("\n\n", 1)[0]
    exact_first_block_bytes = len(first_block.encode("utf-8"))
    smaller = submit(ready, max_reference_tokens=exact_first_block_bytes)
    job, run = run_one(ready, smaller)
    assert job["status"] == "succeeded"
    context = run["snapshot"]["context"]
    assert context["context_text"] == first_block
    assert [row["evidence_id"] for row in context["source_records"]] == ["A1"]
    assert context["diagnostics"]["missing_context_roles"] == ["regulation"]
    assert any(row["source_role"] == "regulation" and row["reason"] == "context_budget"
               for row in context["omitted_records"])
    original = ready.docs.source(context["source_records"][0]["chunk_id"])
    assert context["source_records"][0]["content"] == original["content"]
    assert job["result"]["evidence_status"] == "insufficient"


def test_tiny_budget_has_distinct_terminal_state_and_replayable_empty_context(ready):
    task = submit(ready, max_reference_tokens=1)
    job, run = run_one(ready, task)
    assert job["status"] == run["status"] == "budget_exhausted"
    assert job["error_code"] == "BUDGET_EXHAUSTED"
    context = run["snapshot"]["context"]
    assert context["context_text"] == "" and context["source_records"] == []
    assert context["reference_token_count"] == 0
    assert context["diagnostics"]["budget_excluded_all"]
    assert len(context["omitted_records"]) == 2


def test_explicit_retry_creates_new_run_and_preserves_old_snapshot(ready):
    task = submit(ready, max_reference_tokens=1)
    old_job, old_run = run_one(ready, task)
    old_snapshot = old_run["snapshot"]
    old_digest = old_run["snapshot_sha256"]
    with ready.docs.db.session() as db:
        original_payload = db.one("SELECT payload_json FROM t_job WHERE id=?", (task["id"],))["payload_json"]
    retried = JobControl(ready.docs).retry(task["id"], key="retry-context")
    assert retried["id"] != task["id"] and retried["parent_job_id"] == task["id"]
    assert JobControl(ready.docs).retry(task["id"], key="retry-context")["id"] == retried["id"]
    with ready.docs.db.session() as db:
        new_run = db.one("SELECT * FROM t_retrieval_run WHERE job_id=?", (retried["id"],))
    assert new_run["id"] != old_run["id"] and new_run["parent_run_id"] == old_run["id"]
    retried = dict(retried, run_id=new_run["id"])
    second_job, second_run = run_one(ready, retried)
    assert second_job["status"] == second_run["status"] == "budget_exhausted"
    assert second_run["snapshot"]["request"] == old_snapshot["request"]
    assert ready.contexts.get(task["run_id"])["snapshot"] == old_snapshot
    assert ready.contexts.get(task["run_id"])["snapshot_sha256"] == old_digest
    with ready.docs.db.session() as db:
        assert db.one("SELECT payload_json FROM t_job WHERE id=?", (task["id"],))["payload_json"] == original_payload


def test_queued_context_cancellation_does_not_publish_snapshot(ready):
    task = submit(ready)
    assert JobControl(ready.docs).cancel(task["id"])["status"] == "cancelled"
    assert work_once(ready.docs) is None
    run = ready.contexts.get(task["run_id"])
    assert run["status"] == "cancelled" and run["snapshot"] is None
    assert run["snapshot_sha256"] is None


def test_snapshot_tampering_is_rejected_on_replay(ready):
    task = submit(ready)
    job, run = run_one(ready, task)
    assert job["status"] == "succeeded"
    with ready.docs.db.session() as db:
        row = db.one("SELECT snapshot_key FROM t_retrieval_run WHERE id=?", (run["id"],))
    ready.docs.storage.path(row["snapshot_key"]).write_bytes(b"{}")
    with pytest.raises(DomainError) as caught:
        ready.contexts.get(run["id"])
    assert caught.value.code == "HASH_MISMATCH"


def test_idempotent_submission_reuses_job_and_changed_budget_conflicts(ready):
    first = submit(ready, key="same-context-request")
    second = submit(ready, key="same-context-request")
    assert second["id"] == first["id"] and second["run_id"] == first["run_id"]
    with pytest.raises(DomainError) as caught:
        submit(ready, key="same-context-request", max_reference_tokens=5999)
    assert caught.value.code == "IDEMPOTENCY_CONFLICT"
    with ready.docs.db.session() as db:
        assert db.one("SELECT COUNT(*) AS n FROM t_retrieval_run")["n"] == 1


def test_duplicate_key_still_resolves_existing_task_after_source_loss(ready):
    first = submit(ready, key="queued-before-source-loss")
    path, _ = ready.docs.file(ready.annual["id"])
    path.unlink()
    reused = submit(ready, key="queued-before-source-loss")
    assert reused["id"] == first["id"] and reused["run_id"] == first["run_id"]
    assert reused["status"] == "queued"


def test_duplicate_key_does_not_reload_unavailable_tokenizer(ready, monkeypatch):
    first = submit(ready, key="queued-before-tokenizer-loss")
    calls = []
    def unavailable(*args, **kwargs):
        calls.append(True)
        raise DomainError("REFERENCE_TOKENIZER_UNAVAILABLE", "unit boundary unavailable", 503)
    monkeypatch.setattr("app.retrieval.service.load_counter", unavailable)
    reused = submit(ready, key="queued-before-tokenizer-loss")
    assert reused["id"] == first["id"] and reused["run_id"] == first["run_id"]
    assert calls == []


def test_empty_temporally_eligible_regulations_remain_empty_and_success_is_insufficient(ready):
    # Default fixture regulation has no known effective interval. Explicitly exclude
    # unknown intervals instead of inventing an event date from report_year.
    task = submit(ready, temporal_filter=True, target_date="2024-01-01",
                  target_date_role="event_date", unknown_temporal="exclude")
    job, run = run_one(ready, task)
    assert job["status"] == run["status"] == "succeeded"
    assert job["result"]["evidence_status"] == "insufficient"
    snapshot = run["snapshot"]
    assert snapshot["retrieval"]["regulation"] == []
    assert snapshot["retrieval"]["annual_report"]
    assert snapshot["context"]["diagnostics"]["missing_candidate_roles"] == ["regulation"]
    assert snapshot["context"]["diagnostics"]["missing_context_roles"] == ["regulation"]
    assert [row["evidence_id"] for row in snapshot["context"]["source_records"]] == ["A1"]
