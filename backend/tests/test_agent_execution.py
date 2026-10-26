"""Real SQLite/BM25 task integration; generation is an explicit test fixture.

These tests demonstrate control flow and persistence, not Agent quality gains.
"""
from copy import deepcopy
import json

import pytest

from app.documents.common import DomainError, dump, sha
from app.documents.jobs import JobControl
from app.documents.worker import work_once
from app.evaluation.agent import normalize_agent_config
from app.evaluation.service import Evaluations
from test_context_service import ready
from test_evaluation_service import claim, execute, expire


class AgentFixtureProvider:
    def __init__(self, plans=None, revise=False):
        self.calls = []
        self.plans = plans or [{"action": "stop", "query": None, "source_role": None, "reason": "fixture evidence sufficient to draft"}]
        self.revise = revise
        self.hook = None
        self.usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    def identity(self, model=None):
        return {"provider": "explicit_agent_fixture", "model": model or "agent-fixture-not-a-model",
                "base_url": "https://invalid.example/fixture", "temperature": None,
                "protocol": "chat-completions-v1", "response_format": "json_object"}

    def complete(self, messages, **kwargs):
        if messages[0]["content"].startswith("[DALUN_AGENT_PLAN_V1]"):
            kind = "plan"
        elif messages[0]["content"].startswith("[DALUN_AGENT_VERIFY_V1]"):
            kind = "verify"
        else:
            kind = "revision" if "revision_request" in json.loads(messages[1]["content"]) else "draft"
        self.calls.append({"kind": kind, "messages": deepcopy(messages)})
        if self.hook:
            self.hook(kind, messages, kwargs)
        kwargs["heartbeat"]()
        if kind == "plan":
            number = sum(c["kind"] == "plan" for c in self.calls)
            text = dump(self.plans[min(number - 1, len(self.plans) - 1)])
        elif kind == "verify":
            text = dump({"decision": "revise" if self.revise else "accept", "reason": "explicit fixture self review only",
                         "issues": ["clarify source limits"] if self.revise else []})
        else:
            text = dump({"answer": ("修订后的合成测试回答" if kind == "revision" else "合成测试草稿") + "[A1][R1]",
                "evidence_status": "insufficient", "limitations": ["explicit synthetic fixture; no research conclusion"],
                "claims": [{"id": "C1", "text": "fixture annual observation", "support_status": "unknown",
                            "citations": [{"evidence_id": "A1", "quote": "审计委员会"}]},
                           {"id": "C2", "text": "fixture rule observation", "support_status": "unknown",
                            "citations": [{"evidence_id": "R1", "quote": "审计委员会"}]}]})
        return {"text": text, "usage": self.usage, "request_id": "fixture-" + str(len(self.calls)),
                "model": "agent-fixture-not-a-model", "finish_reason": "stop", "mock": True}


def submit(ready, service, **changes):
    request = {"index_id": ready.index_id, "stock_code": "000001", "report_year": 2024,
               "questions": [{"key": "agent-D2", "dimension": "D2", "text": "审计委员会履职"}],
               "retrieval": {"tokenizer_kind": "utf8_diagnostic"}, "mode": "dual_agent"}
    request.update(changes)
    return service.enqueue(**request)


def test_agent_plans_drafts_and_reviews_without_promoting_semantic_support(ready):
    provider = AgentFixtureProvider()
    service = Evaluations(ready.docs, provider)
    task = submit(ready, service)
    job, run = execute(ready, service, task)
    assert job["status"] == "succeeded"
    answer = run["answers"][0]
    assert [c["kind"] for c in provider.calls] == ["plan", "draft", "verify"]
    assert answer["validation"]["status"] == "valid"
    assert answer["agent_trace"]["stop_reason"] == "planner_stop"
    assert answer["agent_trace"]["verification"]["semantic_support_verified"] is False
    assert answer["agent_trace"]["verification"]["human_review_status"] == "not_reviewed"
    assert run["request"]["mode"] == "dual_agent"
    assert [c["purpose"] for c in run["model_calls"]] == ["agent_plan", "agent_draft", "agent_verify"]
    assert run["snapshot"]["summary"]["observed_total_tokens"] == 45


def test_supplement_retrieves_only_frozen_scope_and_reassembles_before_draft(ready, monkeypatch):
    provider = AgentFixtureProvider([{"action": "retrieve", "query": "审计委员会监督", "source_role": "regulation", "reason": "fixture gap"}])
    service = Evaluations(ready.docs, provider)
    observed = []
    original = service.indexes.search
    def search(*args, **kwargs):
        observed.append((args, kwargs))
        return original(*args, **kwargs)
    monkeypatch.setattr(service.indexes, "search", search)
    task = submit(ready, service)
    _, run = execute(ready, service, task)
    answer = run["answers"][0]
    steps = answer["agent_trace"]["steps"]
    supplement = next(s for s in steps if s["kind"] == "retrieval")
    assert supplement["selected_roles"] == ["regulation"]
    assert len(observed) == 2
    assert all(args[0] == ready.index_id and args[2:4] == ("000001", 2024) for args, _ in observed)
    assert all(kwargs["target_date"] is None and kwargs["temporal_filter"] is False for _, kwargs in observed)
    assert supplement["context"]["reference_token_count"] <= 6000
    assert {s["context_sha256"] for s in steps if s["kind"] in {"draft", "verify"}} == {answer["context"]["context_sha256"]}
    assert answer["agent_trace"]["final_budget"]["supplemental_calls_used"] == 1


def test_revision_uses_same_final_ids_and_does_not_claim_second_review(ready):
    provider = AgentFixtureProvider(revise=True)
    service = Evaluations(ready.docs, provider)
    _, run = execute(ready, service, submit(ready, service))
    answer = run["answers"][0]
    assert answer["status"] == "succeeded" and "修订" in answer["answer_text"]
    assert [c["kind"] for c in provider.calls] == ["plan", "draft", "verify", "revision"]
    assert answer["agent_trace"]["revised"]
    assert answer["agent_trace"]["final_verification_status"] == "not_reverified_after_revision"
    contexts = {s["context_sha256"] for s in answer["agent_trace"]["steps"] if s["kind"] in {"draft", "verify", "revision"}}
    assert contexts == {answer["context"]["context_sha256"]}
    assert "revision_request" in json.loads(answer["prompt"]["messages"][1]["content"])


def test_revision_request_with_disabled_revision_is_explicit_failure(ready):
    provider = AgentFixtureProvider(revise=True)
    service = Evaluations(ready.docs, provider)
    job, run = execute(ready, service, submit(ready, service, agent={"allow_revision": False}))
    assert job["status"] == "failed"
    assert run["answers"][0]["error_code"] == "AGENT_REVISION_DISABLED"
    assert len(provider.calls) == 3


def test_supplement_budget_zero_does_not_silently_use_fixed_fallback(ready):
    provider = AgentFixtureProvider([{"action": "retrieve", "query": "审计委员会", "source_role": "both", "reason": "fixture"}])
    service = Evaluations(ready.docs, provider)
    job, run = execute(ready, service, submit(ready, service, agent={"max_supplemental_calls": 0}))
    assert job["status"] == "budget_exhausted"
    assert [c["kind"] for c in provider.calls] == ["plan"]
    assert run["answers"][0]["agent_trace"]["stop_reason"] == "supplemental_budget_exhausted"


def test_supplement_budget_is_shared_across_all_questions(ready):
    provider = AgentFixtureProvider([{"action": "retrieve", "query": "审计委员会", "source_role": "both", "reason": "fixture"}])
    service = Evaluations(ready.docs, provider)
    questions = [{"key": "D2-one", "dimension": "D2", "text": "审计委员会"},
                 {"key": "D2-two", "dimension": "D2", "text": "审计委员会监督"}]
    job, run = execute(ready, service, submit(ready, service, questions=questions, agent={"max_supplemental_calls": 1}))
    assert job["status"] == "partial"
    assert [a["status"] for a in run["answers"]] == ["succeeded", "budget_exhausted"]
    assert sum(s["kind"] == "retrieval" for a in run["answers"] for s in a["agent_trace"]["steps"]) == 1


def test_max_rounds_terminates_and_records_each_query(ready):
    provider = AgentFixtureProvider([{"action": "retrieve", "query": "审计委员会", "source_role": "both", "reason": "fixture"}])
    service = Evaluations(ready.docs, provider)
    _, run = execute(ready, service, submit(ready, service, agent={"max_rounds": 2}))
    trace = run["answers"][0]["agent_trace"]
    assert trace["stop_reason"] == "max_rounds_reached"
    assert [s["key"] for s in trace["steps"]] == ["plan:1", "retrieve:1", "plan:2", "retrieve:2", "draft", "verify"]
    assert len(provider.calls) == 4


def test_insufficient_planned_model_budget_rejected_before_queue(ready):
    service = Evaluations(ready.docs, AgentFixtureProvider())
    with pytest.raises(DomainError) as error:
        submit(ready, service, budget={"max_model_calls": 3})
    assert error.value.code == "BUDGET_TOO_SMALL"
    with ready.docs.db.session() as db:
        assert db.one("SELECT COUNT(*) AS n FROM t_evaluation_run")["n"] == 0


def test_unknown_usage_after_plan_stops_draft_and_keeps_ledger(ready):
    provider = AgentFixtureProvider()
    provider.usage = None
    service = Evaluations(ready.docs, provider)
    job, run = execute(ready, service, submit(ready, service))
    assert job["status"] == "budget_exhausted"
    assert [c["kind"] for c in provider.calls] == ["plan"]
    assert run["model_calls"][0]["usage"]["status"] == "unknown"
    assert run["answers"][0]["error_code"] == "MODEL_USAGE_UNKNOWN"


def test_agent_inflight_crash_recovery_never_resends_step(ready):
    provider = AgentFixtureProvider()
    service = Evaluations(ready.docs, provider)
    task = submit(ready, service)
    job, owner = claim(ready, task)
    provider.hook = lambda *args: (_ for _ in ()).throw(SystemExit("fixture crash"))
    control = JobControl(ready.docs)
    with pytest.raises(SystemExit):
        service.build(json.loads(job["payload_json"]), job["id"], owner, lambda: control.checkpoint(job["id"], owner))
    expire(ready, task)
    provider.hook = None
    final, run = execute(ready, service, task)
    assert final["status"] == "failed" and len(provider.calls) == 1
    assert run["model_calls"][0]["status"] == "uncertain"
    assert run["answers"][0]["error_code"] == "MODEL_CALL_OUTCOME_UNKNOWN"


def test_completed_response_recovery_reuses_provider_result(ready, monkeypatch):
    provider = AgentFixtureProvider()
    service = Evaluations(ready.docs, provider)
    task = submit(ready, service)
    job, owner = claim(ready, task)
    original = service._save_answer
    crashed = []
    def save(answer, *args):
        steps = (answer.get("agent_trace") or {}).get("steps", [])
        if not crashed and any(s["key"] == "plan:1" and s["status"] == "succeeded" for s in steps):
            crashed.append(True)
            raise SystemExit("fixture after response committed")
        return original(answer, *args)
    monkeypatch.setattr(service, "_save_answer", save)
    control = JobControl(ready.docs)
    with pytest.raises(SystemExit):
        service.build(json.loads(job["payload_json"]), job["id"], owner, lambda: control.checkpoint(job["id"], owner))
    expire(ready, task)
    _, run = execute(ready, service, task)
    assert run["status"] == "succeeded"
    assert [c["kind"] for c in provider.calls] == ["plan", "draft", "verify"]
    assert run["model_calls"][0]["attempt_number"] == 1
    assert run["model_calls"][1]["attempt_number"] == 2


def test_worker_cancellation_during_agent_call_does_not_publish_late_answer(ready, monkeypatch):
    provider = AgentFixtureProvider()
    service = Evaluations(ready.docs, provider)
    task = submit(ready, service)
    provider.hook = lambda *args: JobControl(ready.docs).cancel(task["id"])
    monkeypatch.setattr("app.evaluation.provider.Provider", lambda settings: provider)
    result = work_once(ready.docs)
    assert result["status"] == "cancelled"
    run = service.get(task["run_id"])
    assert run["snapshot"] is None and run["answers"][0]["status"] == "cancelled"
    assert run["model_calls"][0]["status"] == "uncertain"


def test_interrupted_supplement_reservation_is_not_silently_resent(ready, monkeypatch):
    provider = AgentFixtureProvider([{"action": "retrieve", "query": "审计委员会", "source_role": "both", "reason": "fixture"}])
    service = Evaluations(ready.docs, provider)
    task = submit(ready, service, agent={"max_supplemental_calls": 1})
    job, owner = claim(ready, task)
    original = service.indexes.search
    searches = []
    def search(*args, **kwargs):
        searches.append(args)
        if len(searches) == 2:
            raise SystemExit("fixture interrupted local supplement")
        return original(*args, **kwargs)
    monkeypatch.setattr(service.indexes, "search", search)
    control = JobControl(ready.docs)
    with pytest.raises(SystemExit):
        service.build(json.loads(job["payload_json"]), job["id"], owner, lambda: control.checkpoint(job["id"], owner))
    expire(ready, task)
    final, run = execute(ready, service, task)
    assert final["status"] == "failed" and len(searches) == 2
    assert len(provider.calls) == 1
    assert run["answers"][0]["error_code"] == "AGENT_RETRIEVAL_OUTCOME_UNKNOWN"
    assert run["answers"][0]["agent_trace"]["steps"][1]["status"] == "uncertain"


def test_declared_temporal_filter_is_kept_on_every_supplement(ready, monkeypatch):
    provider = AgentFixtureProvider([{"action": "retrieve", "query": "审计委员会", "source_role": "both", "reason": "fixture"}])
    service = Evaluations(ready.docs, provider)
    original = service.indexes.search
    kwargs_seen = []
    def search(*args, **kwargs):
        kwargs_seen.append(kwargs)
        return original(*args, **kwargs)
    monkeypatch.setattr(service.indexes, "search", search)
    questions = [{"key": "event", "dimension": "D2", "text": "审计委员会", "declared_target_date": "2024-05-01", "target_date_role": "event_date"}]
    _, run = execute(ready, service, submit(ready, service, questions=questions,
        retrieval={"tokenizer_kind": "utf8_diagnostic", "temporal_filter": True, "unknown_temporal": "retain"}))
    assert run["status"] == "succeeded" and len(kwargs_seen) == 2
    assert all(k["temporal_filter"] is True and k["target_date"] == "2024-05-01" and
               k["target_date_role"] == "event_date" and k["unknown_temporal"] == "retain" for k in kwargs_seen)


def test_agent_queued_protocol_is_frozen_across_later_template_changes(ready, monkeypatch):
    provider = AgentFixtureProvider()
    service = Evaluations(ready.docs, provider)
    task = submit(ready, service)
    frozen = service.get(task["run_id"])["request"]["agent_protocol"]
    monkeypatch.setattr("app.evaluation.agent_protocol.PLANNING_SYSTEM", "later code must not alter queued plan")
    _, run = execute(ready, service, task)
    assert provider.calls[0]["messages"][0]["content"] == frozen["planning_system"]
    assert run["request"]["agent_protocol"] == frozen


def test_invalid_plan_has_known_call_usage_and_no_fixed_fallback(ready, monkeypatch):
    provider = AgentFixtureProvider()
    original = provider.complete
    def complete(*args, **kwargs):
        response = original(*args, **kwargs)
        response["text"] = '{"action":"open_url","url":"https://example.com"}'
        return response
    monkeypatch.setattr(provider, "complete", complete)
    service = Evaluations(ready.docs, provider)
    final, run = execute(ready, service, submit(ready, service))
    assert final["status"] == "failed" and len(provider.calls) == 1
    assert run["answers"][0]["error_code"] == "AGENT_PLAN_INVALID"
    assert run["model_calls"][0]["status"] == "succeeded"
    assert run["model_calls"][0]["usage"]["total_tokens"] == 15


def test_agent_provider_failure_preserves_failed_step_budget_and_call_link(ready):
    provider = AgentFixtureProvider()
    provider.hook = lambda *args: (_ for _ in ()).throw(DomainError("MODEL_TIMEOUT", "fixture timeout", 504))
    service = Evaluations(ready.docs, provider)
    final, run = execute(ready, service, submit(ready, service))
    assert final["status"] == "failed"
    step = run["answers"][0]["agent_trace"]["steps"][0]
    assert step["status"] == "uncertain" and step["model_call"]["id"] == run["model_calls"][0]["id"]
    assert step["budget"]["model_calls_used"] == 1 and step["budget"]["provider_usage_complete"] is False


def test_list_exposes_frozen_agent_mode_and_invalid_mode_is_domain_error(ready):
    service = Evaluations(ready.docs, AgentFixtureProvider())
    task = submit(ready, service)
    listing = service.list()
    assert listing["items"][0]["id"] == task["run_id"]
    assert listing["items"][0]["mode"] == "dual_agent"
    assert "request_json" not in listing["items"][0]
    with pytest.raises(DomainError) as error:
        submit(ready, service, mode={})
    assert error.value.code == "INVALID_EVALUATION_MODE"


@pytest.mark.parametrize("config", [{"max_rounds": True}, {"max_rounds": 0}, {"max_rounds": 4},
    {"max_supplemental_calls": -1}, {"max_supplemental_calls": 13}, {"allow_revision": 1}, {"unexpected": 1}, []])
def test_agent_config_rejects_coercion_and_unknown_fields(config):
    with pytest.raises(DomainError) as error:
        normalize_agent_config(config)
    assert error.value.code == "INVALID_AGENT_CONFIG"
