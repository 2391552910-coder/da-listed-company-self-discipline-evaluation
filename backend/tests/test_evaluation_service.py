"""Real SQLite/Worker/BM25 integration with an explicitly mocked generation provider.

These tests prove persistence and validation contracts, not model quality or a
successful request to an external generation API.
"""
import json
from copy import deepcopy

import pytest

from app.documents.common import DomainError, dump
from app.documents.jobs import JobControl
from app.documents.worker import work_once
from app.evaluation.service import Evaluations
from test_context_service import ready  # real BM25 index, isolated parser fixture


class MockProvider:
    def __init__(self):
        self.calls = []
        self.hook = None
        self.usage = {"prompt_tokens": 100, "completion_tokens": 80, "total_tokens": 180}
        self.finish_reason = "stop"
        self.bad_output = False
        self.model = "explicit-unit-mock"

    def identity(self, model=None):
        return {"provider": "explicit_unit_mock", "model": model or self.model,
                "base_url": "https://invalid.example/mock", "temperature": None,
                "protocol": "chat-completions-v1", "response_format": "json_object"}

    def complete(self, messages, **kwargs):
        self.calls.append(deepcopy({"messages": messages, **{k: v for k, v in kwargs.items() if k != "heartbeat"}}))
        if self.hook:
            self.hook(messages, kwargs)
        kwargs["heartbeat"]()
        if self.bad_output:
            text = "{invalid JSON"
        else:
            text = dump({"answer": "年报披露审计委员会履行监督职责[A1]；规则规定依法监督[R1]。",
                "evidence_status": "sufficient", "limitations": ["年报为公司自述，法规适用性仍需复核。"],
                "claims": [{"id": "C1", "text": "年报提及审计委员会。", "support_status": "supported",
                    "citations": [{"evidence_id": "A1", "quote": "审计委员会"}]},
                    {"id": "C2", "text": "规则提及审计委员会。", "support_status": "supported",
                    "citations": [{"evidence_id": "R1", "quote": "审计委员会"}]}]})
        return {"text": text, "usage": self.usage, "request_id": "unit-request-" + str(len(self.calls)),
                "model": self.model, "finish_reason": self.finish_reason, "mock": True}


@pytest.fixture
def evaluation(ready):
    provider = MockProvider()
    return Evaluations(ready.docs, provider), provider


def submit(ready, service, full=False, **changes):
    request = {"index_id": ready.index_id, "stock_code": "000001", "report_year": 2024,
        "retrieval": {"tokenizer_kind": "utf8_diagnostic"},
        "questions": None if full else [{"key": "custom-1", "dimension": "D2", "text": "审计委员会履职"}]}
    request.update(changes)
    return service.enqueue(**request)


def claim(ready, task, owner="evaluation-worker"):
    job = ready.docs.claim_job(owner)
    assert job["id"] == task["id"]
    return job, owner


def execute(ready, service, task):
    job, owner = claim(ready, task)
    control = JobControl(ready.docs)
    result = service.build(json.loads(job["payload_json"]), job["id"], owner,
        lambda: control.checkpoint(job["id"], owner))
    control.finish(job["id"], owner, result=result, status=result["status"], error=result.get("error_code"))
    return ready.docs.job(job["id"]), service.get(task["run_id"])


def expire(ready, task):
    with ready.docs.db.session() as db:
        db.execute("UPDATE t_job SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=?", (task["id"],))


def test_single_question_keeps_complete_context_prompt_claims_and_actual_locators(ready, evaluation):
    service, provider = evaluation
    task = submit(ready, service)
    completed, run = execute(ready, service, task)
    assert completed["status"] == run["status"] == "succeeded"
    answer = run["snapshot"]["answers"][0]
    assert answer["validation"]["status"] == "valid"
    assert answer["validation"]["mechanical_citation_valid_count"] == 2
    assert answer["validation"]["semantic_support_verified"] is False
    assert answer["evidence_status"] == "sufficient"
    assert len(provider.calls) == 1
    assert run["request"]["question_set"]["version"] == "custom"
    assert run["snapshot"]["summary"]["coverage_complete"] is False
    assert run["snapshot"]["summary"]["not_run_dimensions"] == ["D1", "D3", "D4"]
    assert run["snapshot"]["model_calls"][0]["usage"]["total_tokens"] == 180
    for record in answer["context"]["source_records"]:
        original = ready.docs.source(record["chunk_id"])
        assert original["frame_text"][record["char_start"]:record["char_end"]] == record["content"]
    assert answer["prompt"]["messages"] == provider.calls[0]["messages"]


def test_worker_integration_uses_evaluation_branch_and_publishes_four_dimensions(ready, evaluation, monkeypatch):
    service, provider = evaluation
    monkeypatch.setattr("app.evaluation.provider.Provider", lambda settings: provider)
    task = submit(ready, service, full=True)
    job = work_once(ready.docs)
    assert job["id"] == task["id"] and job["status"] == "succeeded", job
    run = service.get(task["run_id"])
    assert len(provider.calls) == 4
    assert len(run["snapshot"]["answers"]) == 4
    summary = run["snapshot"]["summary"]
    assert summary["coverage_complete"] and not summary["question_set_is_custom"]
    assert summary["summary_method"] == "deterministic_status_aggregation"
    assert summary["dimensions"][3]["status"] == "insufficient"
    assert all(d["score"] is None for d in summary["dimensions"])
    assert run["snapshot"]["third_chapter_index"] is None


def test_queued_run_uses_enqueue_frozen_prompt_after_code_template_change(ready, evaluation, monkeypatch):
    service, provider = evaluation
    task = submit(ready, service)
    frozen = service.get(task["run_id"])["request"]["prompt_template"]["system"]
    monkeypatch.setattr("app.evaluation.service.SYSTEM_PROMPT", "later deployment template must not alter queued request")
    _, run = execute(ready, service, task)
    assert provider.calls[0]["messages"][0]["content"] == frozen
    assert run["snapshot"]["request"]["prompt_template"]["system"] == frozen


def test_idempotent_submission_resolves_before_model_or_source_availability(ready, evaluation):
    service, provider = evaluation
    first = submit(ready, service, key="same-evaluation")
    provider.identity = lambda model=None: (_ for _ in ()).throw(AssertionError("must not reload provider"))
    path, _ = ready.docs.file(ready.annual["id"])
    path.unlink()
    second = submit(ready, service, key="same-evaluation")
    assert second["id"] == first["id"] and second["run_id"] == first["run_id"]
    with pytest.raises(DomainError) as caught:
        submit(ready, service, key="same-evaluation", budget={"max_output_tokens": 9000})
    assert caught.value.code == "IDEMPOTENCY_CONFLICT"


def test_zero_reference_budget_fit_does_not_call_model(ready, evaluation):
    service, provider = evaluation
    task = submit(ready, service, retrieval={"tokenizer_kind": "utf8_diagnostic", "max_reference_tokens": 1})
    job, run = execute(ready, service, task)
    assert job["status"] == "budget_exhausted"
    assert provider.calls == []
    answer = run["snapshot"]["answers"][0]
    assert answer["answer_text"] is None and answer["context"]["source_records"] == []


def test_malformed_output_is_failed_not_fabricated_success(ready, evaluation):
    service, provider = evaluation
    provider.bad_output = True
    task = submit(ready, service)
    job, run = execute(ready, service, task)
    assert job["status"] == "failed"
    answer = run["snapshot"]["answers"][0]
    assert answer["status"] == "failed" and answer["error_code"] == "MODEL_OUTPUT_INVALID"
    assert answer["raw_model_output"] == "{invalid JSON"
    assert run["snapshot"]["model_calls"][0]["status"] == "succeeded"


@pytest.mark.parametrize("reason", ["length", "content_filter", "max_tokens", None, "unknown"])
def test_truncated_or_unknown_finish_reason_never_publishes_formal_success(ready, evaluation, reason):
    service, provider = evaluation
    provider.finish_reason = reason
    task = submit(ready, service)
    job, run = execute(ready, service, task)
    assert job["status"] == "failed"
    assert run["snapshot"]["answers"][0]["error_code"] == "MODEL_OUTPUT_INCOMPLETE"


def test_unknown_provider_usage_stops_remaining_questions_and_retains_valid_first_answer(ready, evaluation):
    service, provider = evaluation
    provider.usage = None
    task = submit(ready, service, full=True)
    job, run = execute(ready, service, task)
    assert job["status"] == "partial" and len(provider.calls) == 1
    assert run["snapshot"]["answers"][0]["status"] == "succeeded"
    assert all(a["status"] == "budget_exhausted" for a in run["snapshot"]["answers"][1:])
    summary = run["snapshot"]["summary"]
    assert summary["observed_total_tokens"] == 0 and summary["provider_usage_complete"] is False
    assert run["model_calls"][0]["usage"]["total_tokens"] is None


def test_observed_usage_threshold_stops_after_first_crossing_without_hiding_excess(ready, evaluation):
    service, provider = evaluation
    task = submit(ready, service, full=True, budget={"max_observed_tokens": 100})
    job, run = execute(ready, service, task)
    assert job["status"] == "partial" and len(provider.calls) == 1
    assert run["snapshot"]["summary"]["observed_total_tokens"] == 180


def test_inflight_crash_recovery_is_uncertain_and_never_resends(ready, evaluation):
    service, provider = evaluation
    task = submit(ready, service)
    job, owner = claim(ready, task)
    provider.hook = lambda *args: (_ for _ in ()).throw(SystemExit("simulated crash after durable call reservation"))
    with pytest.raises(SystemExit):
        service.build(json.loads(job["payload_json"]), job["id"], owner, lambda: JobControl(ready.docs).checkpoint(job["id"], owner))
    assert len(provider.calls) == 1
    expire(ready, task)
    provider.hook = None
    job, run = execute(ready, service, task)
    assert job["status"] == "failed" and len(provider.calls) == 1
    assert run["model_calls"][0]["status"] == "uncertain"
    assert run["snapshot"]["answers"][0]["error_code"] == "MODEL_CALL_OUTCOME_UNKNOWN"


def test_completed_call_recovery_reuses_frozen_response_without_new_provider_request(ready, evaluation, monkeypatch):
    service, provider = evaluation
    task = submit(ready, service)
    job, owner = claim(ready, task)
    actual = service._validate_response
    monkeypatch.setattr(service, "_validate_response", lambda *args: (_ for _ in ()).throw(SystemExit("after response commit")))
    with pytest.raises(SystemExit):
        service.build(json.loads(job["payload_json"]), job["id"], owner, lambda: JobControl(ready.docs).checkpoint(job["id"], owner))
    expire(ready, task)
    monkeypatch.setattr(service, "_validate_response", actual)
    job, run = execute(ready, service, task)
    assert job["status"] == "succeeded" and len(provider.calls) == 1
    assert run["model_calls"][0]["attempt_number"] == 1


def test_recovery_after_full_snapshot_publication_reuses_exact_hash(ready, evaluation):
    service, provider = evaluation
    task = submit(ready, service)
    job, owner = claim(ready, task)
    first = service.build(json.loads(job["payload_json"]), job["id"], owner, lambda: JobControl(ready.docs).checkpoint(job["id"], owner))
    expire(ready, task)
    job, run = execute(ready, service, task)
    assert len(provider.calls) == 1
    assert run["snapshot_sha256"] == first["snapshot_sha256"]
    assert job["result"]["recovered_snapshot"] and job["result"]["snapshot_origin_attempt"] == 1


def test_cancel_during_call_keeps_uncertain_consumption_and_hides_final_run(ready, evaluation):
    service, provider = evaluation
    task = submit(ready, service)
    job, owner = claim(ready, task)
    provider.hook = lambda *args: JobControl(ready.docs).cancel(job["id"])
    with pytest.raises(DomainError) as caught:
        service.build(json.loads(job["payload_json"]), job["id"], owner, lambda: JobControl(ready.docs).checkpoint(job["id"], owner))
    assert caught.value.code == "TASK_CANCELLED"
    JobControl(ready.docs).finish(job["id"], owner, error="TASK_CANCELLED")
    run = service.get(task["run_id"])
    assert run["status"] == "cancelled" and run["snapshot"] is None
    assert run["model_calls"][0]["status"] == "uncertain"
    assert run["answers"][0]["status"] == "cancelled"
    assert run["answers"][0]["stored_status"] == "running"
    assert run["answers"][0]["snapshot_is_interim"] is True


def test_retry_allocates_new_run_and_does_not_mutate_failed_history(ready, evaluation):
    service, provider = evaluation
    provider.bad_output = True
    first = submit(ready, service)
    _, old = execute(ready, service, first)
    retried = JobControl(ready.docs).retry(first["id"], "eval-retry")
    assert retried["run_id"] != first["run_id"]
    assert retried["parent_job_id"] == first["id"]
    provider.bad_output = False
    _, current = execute(ready, service, retried)
    assert current["status"] == "succeeded" and current["parent_run_id"] == old["id"]
    assert service.get(old["id"])["snapshot_sha256"] == old["snapshot_sha256"]


def test_reclaimed_worker_does_not_reset_runtime_budget(ready, evaluation):
    service, provider = evaluation
    task = submit(ready, service)
    with ready.docs.db.session() as db:
        db.execute("UPDATE t_evaluation_run SET execution_started_at='2000-01-01T00:00:00+00:00' WHERE id=?", (task["run_id"],))
    job, run = execute(ready, service, task)
    assert job["status"] == "budget_exhausted" and provider.calls == []


def test_missing_original_after_prompt_freeze_is_not_verified_as_available(ready, evaluation):
    service, provider = evaluation
    path, _ = ready.docs.file(ready.annual["id"])
    provider.hook = lambda *args: path.unlink()
    task = submit(ready, service)
    job, run = execute(ready, service, task)
    assert job["status"] == "failed"
    answer = run["snapshot"]["answers"][0]
    assert answer["validation"]["status"] == "invalid"


def test_snapshot_file_tampering_and_job_proof_mismatch_are_detected(ready, evaluation):
    service, _ = evaluation
    task = submit(ready, service)
    _, run = execute(ready, service, task)
    with ready.docs.db.session() as db:
        db.execute("UPDATE t_job SET result_json='{}' WHERE id=?", (task["id"],))
    with pytest.raises(DomainError) as caught:
        service.get(run["id"])
    assert caught.value.code == "EVALUATION_JOB_MISMATCH"


@pytest.mark.parametrize("changes", [
    {"budget": {"max_model_calls": 1}, "full": True},
    {"budget": {"max_output_tokens": True}},
    {"retrieval": {"unknown": 3}},
    {"stock_code": "000002"},
    {"retrieval": {"temporal_filter": True}},
])
def test_invalid_scope_and_configuration_fail_before_queue_creation(ready, evaluation, changes):
    service, _ = evaluation
    with pytest.raises(DomainError):
        submit(ready, service, **changes)
    assert service.list()["total"] == 0
