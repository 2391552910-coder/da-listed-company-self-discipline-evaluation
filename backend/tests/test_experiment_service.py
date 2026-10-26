"""Real SQLite/BM25 queue integration, explicit fixture model responses only."""
import json
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from app.documents.common import DomainError
from app.documents.jobs import JobControl
from app.experiments.service import Experiments, CRITERIA
from app.evaluation.service import Evaluations
from test_context_service import ready
from test_evaluation_service import MockProvider, execute


@pytest.fixture
def experiment(ready):
    provider = MockProvider()
    evaluations = Evaluations(ready.docs, provider)
    return Experiments(ready.docs, evaluations), evaluations, provider


def create(ready, service, **changes):
    request = {"name": "明确fixture实验", "cases": [{"key": "c1", "index_id": ready.index_id,
        "stock_code": "000001", "report_year": 2024}], "modes": ["dual_fixed"],
        "questions": [{"key": "q1", "dimension": "D2", "text": "审计委员会履职"}],
        "retrieval": {"tokenizer_kind": "utf8_diagnostic"}}
    request.update(changes)
    return service.create(**request)


def complete(ready, service, evaluations):
    result = create(ready, service)
    item = result["items"][0]
    execute(ready, evaluations, {"id": item["job_id"], "run_id": item["run_id"]})
    return service.get(result["id"])


def rate(service, experiment, **changes):
    item = experiment["items"][0]
    run = service.evaluations.get(item["run_id"])
    request = {"run_id": run["id"], "answer_id": run["answers"][0]["id"], "source_kind": "manual",
        "rubric_version": "evidence-quality-v1", "values": dict.fromkeys(CRITERIA, 4),
        "reviewer": "fixture reviewer", "method_version": "fixture-manual-v1"}
    request.update(changes)
    return service.add_rating(experiment["id"], **request)


def test_batch_create_freezes_protocol_and_only_queues_jobs(ready, experiment):
    service, _, provider = experiment
    result = create(ready, service, modes=["dual_fixed", "dual_agent"], key="batch")
    assert provider.calls == [] and len(result["items"]) == 2
    assert all(i["status"] == "queued" for i in result["items"])
    assert result["protocol"]["model"]["model"] == "explicit-unit-mock"
    assert result["protocol"]["cases"][0]["index_manifest_sha256"]
    assert result["protocol"]["prompt_template"]["system"]
    assert result["protocol"]["agent_protocol"]["version"]
    assert result["protocol"]["declared_max_model_calls"] == 64
    assert result["comparison"]["groups"][0]["comparable"]
    repeated = create(ready, service, modes=["dual_fixed", "dual_agent"], key="batch")
    assert [i["run_id"] for i in result["items"]] == [i["run_id"] for i in repeated["items"]]
    with pytest.raises(DomainError) as error:
        create(ready, service, name="different", modes=["dual_fixed", "dual_agent"], key="batch")
    assert error.value.code == "IDEMPOTENCY_CONFLICT"


def test_batch_cap_rejects_before_any_job_is_created(ready, experiment):
    service, _, _ = experiment
    with pytest.raises(DomainError) as error:
        create(ready, service, max_total_model_calls=1)
    assert error.value.code == "EXPERIMENT_BUDGET_TOO_SMALL"
    with ready.docs.db.session() as db:
        assert db.one("SELECT COUNT(*) AS n FROM t_experiment")["n"] == 0
        assert db.one("SELECT COUNT(*) AS n FROM t_job WHERE kind='evaluation'")["n"] == 0


def test_api_crash_after_child_enqueue_resumes_same_child_without_provider_recheck(ready, experiment, monkeypatch):
    service, evaluations, _ = experiment
    original = evaluations.enqueue
    def crash(**kwargs):
        original(**kwargs)
        raise RuntimeError("simulated API crash after durable queue")
    monkeypatch.setattr(evaluations, "enqueue", crash)
    with pytest.raises(RuntimeError):
        create(ready, service, key="interrupted")
    monkeypatch.setattr(evaluations, "enqueue", original)
    monkeypatch.setattr(evaluations.provider, "identity", lambda *_: (_ for _ in ()).throw(AssertionError("orphan already frozen")))
    recovered = create(ready, service, key="interrupted")
    assert recovered["items"][0]["run_id"]
    with ready.docs.db.session() as db:
        assert db.one("SELECT COUNT(*) AS n FROM t_evaluation_run")["n"] == 1


def test_protocol_drift_blocks_new_dispatch_and_retains_failure_reason(ready, experiment, monkeypatch):
    service, _, _ = experiment
    original = service.dispatch
    monkeypatch.setattr(service, "dispatch", service.get)
    pending = create(ready, service)
    monkeypatch.setattr(service, "dispatch", original)
    monkeypatch.setattr("app.evaluation.service.SYSTEM_PROMPT", "changed deployment")
    result = service.dispatch(pending["id"])
    assert result["status"] == "failed"
    assert result["items"][0]["error_code"] == "EXPERIMENT_PROTOCOL_CHANGED"
    assert result["items"][0]["run_id"] is None


def test_cancel_during_enqueue_cancels_late_bound_child(ready, experiment, monkeypatch):
    service, evaluations, provider = experiment
    original = evaluations.enqueue
    def enqueue_then_cancel(**kwargs):
        task = original(**kwargs)
        with ready.docs.db.session() as db:
            experiment_id = db.one("SELECT id FROM t_experiment")["id"]
        service.cancel(experiment_id)
        return task
    monkeypatch.setattr(evaluations, "enqueue", enqueue_then_cancel)
    result = create(ready, service)
    assert result["status"] == "cancelled"
    assert result["items"][0]["status"] == "cancelled"
    assert provider.calls == []
    assert service.dispatch(result["id"])["items"][0]["run_id"] == result["items"][0]["run_id"]


def test_concurrent_dispatch_uses_one_child_identity(ready, experiment, monkeypatch):
    service, evaluations, _ = experiment
    original_dispatch = service.dispatch
    monkeypatch.setattr(service, "dispatch", service.get)
    pending = create(ready, service)
    monkeypatch.setattr(service, "dispatch", original_dispatch)
    barrier = threading.Barrier(2)
    original = evaluations.enqueue
    def synchronized(**kwargs):
        barrier.wait(timeout=20)
        return original(**kwargs)
    monkeypatch.setattr(evaluations, "enqueue", synchronized)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.dispatch(pending["id"]), range(2)))
    assert results[0]["items"][0]["run_id"] == results[1]["items"][0]["run_id"]
    with ready.docs.db.session() as db:
        assert db.one("SELECT COUNT(*) AS n FROM t_evaluation_run")["n"] == 1


def test_append_only_scores_bind_frozen_answer_and_keep_sources_separate(ready, experiment):
    service, evaluations, _ = experiment
    result = complete(ready, service, evaluations)
    first = rate(service, result, key="score")
    assert first["answer_snapshot_sha256"]
    assert rate(service, result, key="score")["id"] == first["id"]
    corrected = rate(service, result, values=dict.fromkeys(CRITERIA, 5), supersedes_score_id=first["id"])
    automatic = rate(service, result, source_kind="automatic", reviewer="explicit fixture judge", method_version="fixture-judge-v1")
    current = service.get(result["id"])
    assert len(current["ratings"]) == 3
    assert current["ratings"][0]["values"]["faithfulness"] == 4
    assert corrected["supersedes_score_id"] == first["id"]
    assert {g["source_kind"] for g in current["comparison"]["rating_groups"]} == {"manual", "automatic"}
    assert all(g["quality_mean"] is None for g in current["comparison"]["rating_groups"])
    assert current["comparison"]["runs"][0]["quality_score"] is None


@pytest.mark.parametrize("values", [dict.fromkeys(CRITERIA, 0), dict.fromkeys(CRITERIA, None),
    dict.fromkeys(CRITERIA, True), {"faithfulness": 4}])
def test_invalid_or_absent_scores_are_not_zero_filled(ready, experiment, values):
    service, evaluations, _ = experiment
    result = complete(ready, service, evaluations)
    with pytest.raises(DomainError) as error:
        rate(service, result, values=values)
    assert error.value.code == "INVALID_RATING"


def test_failed_answer_not_rateable_and_quality_stays_null(ready, experiment):
    service, evaluations, provider = experiment
    provider.bad_output = True
    result = complete(ready, service, evaluations)
    assert result["status"] == "failed"
    with pytest.raises(DomainError) as error:
        rate(service, result)
    assert error.value.code == "ANSWER_NOT_RATEABLE"
    assert result["comparison"]["runs"][0]["quality_score"] is None


def test_attached_run_has_separate_origin_and_is_not_cancelled_by_other_batch(ready, experiment):
    service, evaluations, _ = experiment
    first = create(ready, service, name="owned by first")
    second = create(ready, service, name="second experiment")
    attached = service.attach(second["id"], first["items"][0]["run_id"], label="关联已有fixture运行")
    assert len(attached["items"]) == 2
    assert attached["items"][1]["origin"] == "existing_run"
    service.cancel(second["id"])
    assert ready.docs.job(first["items"][0]["job_id"])["status"] == "queued"


def test_history_stays_outside_current_runs_and_exports_are_frozen_safe_csv(ready, experiment):
    service, evaluations, _ = experiment
    result = complete(ready, service, evaluations)
    result = service.import_history(result["id"], "=history", "fixture-cache-v1",
        [{"record_key": "=formula", "mode": "dual_agent", "answer": "explicit historical fixture"}], key="history")
    assert len(result["comparison"]["runs"]) == 1
    assert result["historical_records"][0]["origin"] == "historical_import"
    first, metadata = service.export(result["id"], "json")
    decoded = json.loads(first)
    assert decoded["historical_records"][0]["records"][0]["answer"] == "explicit historical fixture"
    assert "idempotency_key" not in decoded
    csv_data, csv_meta = service.export(result["id"], "csv")
    assert "'=formula" in csv_data.decode("utf-8-sig")
    assert csv_meta["file_sha256"] and metadata["snapshot_sha256"]
    with ready.docs.db.session() as db:
        stored = db.one("SELECT * FROM t_experiment_export WHERE format='json'")
    assert ready.docs.storage.read(stored["snapshot_key"], stored["snapshot_sha256"]) == first
    rate(service, result)
    assert ready.docs.storage.read(stored["file_key"], stored["file_sha256"]) == first


def test_other_budget_or_scope_gets_a_distinct_comparison_group(ready, experiment):
    service, evaluations, _ = experiment
    first = create(ready, service)
    other = create(ready, service, budget={"max_model_calls": 8})
    attached = service.attach(first["id"], other["items"][0]["run_id"])
    assert len(attached["comparison"]["groups"]) == 2
    assert not any(g["comparable"] for g in attached["comparison"]["groups"])


def test_listing_and_rubric_are_versioned(ready, experiment):
    service, _, _ = experiment
    create(ready, service)
    listing = service.list()
    assert listing["total"] == 1 and listing["items"][0]["status"] == "running"
    assert service.rubrics()["items"][0]["version"] == "evidence-quality-v1"


def test_completed_experiment_cancel_is_a_noop(ready, experiment):
    service, evaluations, _ = experiment
    result = complete(ready, service, evaluations)
    cancelled = service.cancel(result["id"])
    assert cancelled["status"] == "succeeded" and not cancelled["cancel_requested"]


def test_failed_historical_records_cannot_have_quality_scores(ready, experiment):
    service, _, _ = experiment
    result = create(ready, service)
    with pytest.raises(DomainError) as error:
        service.import_history(result["id"], "historical failures", "fixture-cache-v1",
            [{"record_key": "bad", "mode": "dual_agent", "status": "failed", "quality_scores": {"faithfulness": 4}}])
    assert error.value.code == "INVALID_HISTORY"
    imported = service.import_history(result["id"], "historical failures", "fixture-cache-v1",
        [{"record_key": "bad", "mode": "dual_agent", "status": "failed", "quality_scores": {"faithfulness": None}}])
    assert imported["historical_records"][0]["records"][0]["quality_scores"]["faithfulness"] is None
