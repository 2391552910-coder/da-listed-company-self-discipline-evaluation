"""Overview scope and frozen evidence counts; fixtures do not prove model quality."""
import json
import os

import pytest

from app.dashboard import Dashboard
from app.documents.common import DomainError, dump
from app.documents.indexes import Indexes
from app.documents.jobs import JobControl
from app.documents.worker import work_once
from test_documents import docs, annual_fixture, text_version
from test_context_service import ready
from test_evaluation_service import evaluation, execute, submit, claim


def test_company_search_pages_use_stable_order_and_literal_wildcards(docs):
    docs.company("000003", "Alpha 100%_! Group")
    docs.company("000001", "Alpha 100XY Group")
    docs.company("000002", "Mixed CASE")
    first = docs.company_page(size=2)
    assert [row["stock_code"] for row in first["items"]] == ["000001", "000002"]
    assert first["total"] == 3 and first["page"] == 1 and first["page_size"] == 2
    assert [row["stock_code"] for row in docs.company_page(page=2, size=2)["items"]] == ["000003"]
    assert docs.company_page(page=3, size=2)["items"] == []
    for query in ["%_!", "100%", "_"]:
        assert [row["stock_code"] for row in docs.company_page(q=query)["items"]] == ["000003"]
    assert docs.company_page(q="case")["items"][0]["stock_code"] == "000002"
    assert docs.company_page(q="' OR 1=1 --")["total"] == 0
    selected = docs.company_page(stock_code="000002.SZ")["items"][0]
    assert docs.company_page(company_id=selected["id"], q="CASE")["total"] == 1
    assert docs.company_page(company_id=selected["id"], stock_code="000001")["total"] == 0
    # Internal callers retain the old list contract, independently of HTTP paging.
    assert len(docs.companies()) == 3


@pytest.mark.parametrize("kwargs", [
    {"page": True}, {"page": 0}, {"page": 1000001}, {"size": 201},
    {"size": False}, {"q": 12}, {"q": "x" * 129}, {"company_id": True}, {"company_id": -1},
])
def test_company_search_rejects_invalid_bounds(docs, kwargs):
    with pytest.raises(DomainError) as caught:
        docs.company_page(**kwargs)
    assert caught.value.status == 422


def test_empty_dashboard_has_explicit_null_quality_and_shared_regulation_scope(docs):
    version = text_version(docs)
    docs.enqueue_parse(version["id"])
    result = Dashboard(docs).get(year=2024)
    assert result["counts"] == {"companies": 0, "annual_documents": 0, "regulation_documents": 1,
                                "ready_indexes": 0, "evaluation_runs": 0}
    assert result["scope"]["regulations"] == "shared_library"
    assert result["job_statuses"][0] == {"status": "queued", "count": 1}
    assert result["coverage"]["quality_score"] is None
    assert result["coverage"]["semantic_support_verified"] is False
    assert len(result["coverage"]["dimensions"]) == 4
    assert all(item["total_answers"] == 0 for item in result["coverage"]["dimensions"])
    with pytest.raises(DomainError) as caught:
        Dashboard(docs).get(company_id=100)
    assert caught.value.status == 404


@pytest.mark.parametrize("kwargs", [{"company_id": 0}, {"company_id": True}, {"year": 1989}, {"year": 2101}, {"year": False}])
def test_dashboard_rejects_invalid_scope(docs, kwargs):
    with pytest.raises(DomainError) as caught:
        Dashboard(docs).get(**kwargs)
    assert caught.value.status == 422


def test_company_year_scope_filters_documents_indexes_jobs_and_evaluations(ready, evaluation, monkeypatch):
    service, provider = evaluation
    docs = ready.docs
    company_id = docs.companies()[0]["id"]
    other, other_parse = annual_fixture(docs, monkeypatch, stock="000002", year=2024)
    prior, prior_parse = annual_fixture(docs, monkeypatch, stock="000001", year=2023)
    # Separate indexes share the same global rule but contain distinct annuals.
    law_parse = docs.versions(ready.law["document_id"])[0]["parse_runs"][0]["id"]
    for parse in (other_parse, prior_parse):
        Indexes(docs).enqueue([law_parse, parse["parse_id"]], "bm25_diagnostic", False)
        assert work_once(docs)["status"] == "succeeded"
    task = submit(ready, service)
    execute(ready, service, task)
    result = Dashboard(docs).get(company_id, 2024)
    assert result["scope"]["stock_code"] == "000001"
    assert result["counts"] == {"companies": 1, "annual_documents": 1, "regulation_documents": 1,
                                "ready_indexes": 1, "evaluation_runs": 1}
    assert result["recent_runs"][0]["id"] == task["run_id"]
    # The shared regulation parse, this index and this evaluation; no other index.
    assert sum(item["count"] for item in result["job_statuses"]) == 3
    empty = Dashboard(docs).get(company_id, 2022)
    assert empty["counts"]["annual_documents"] == empty["counts"]["ready_indexes"] == empty["counts"]["evaluation_runs"] == 0
    assert empty["counts"]["regulation_documents"] == 1
    assert sum(item["count"] for item in empty["job_statuses"]) == 1
    global_result = Dashboard(docs).get()
    assert global_result["counts"]["annual_documents"] == 3 and global_result["counts"]["ready_indexes"] == 3
    assert len(provider.calls) == 1  # Overview itself made no generation request.


def test_coverage_separates_success_failure_pending_and_snapshot_corruption(ready, evaluation):
    service, provider = evaluation
    good = submit(ready, service)
    execute(ready, service, good)
    provider.bad_output = True
    failed = submit(ready, service)
    execute(ready, service, failed)
    queued = submit(ready, service)
    cancelled = submit(ready, service)
    JobControl(ready.docs).cancel(cancelled["id"])
    result = Dashboard(ready.docs).get(year=2024)
    d2 = result["coverage"]["dimensions"][1]
    assert (d2["total_answers"], d2["succeeded_answers"], d2["failed_answers"], d2["pending_answers"], d2["unknown_answers"]) == (4, 1, 2, 1, 0)
    assert d2["annual_context_answers"] == d2["regulation_context_answers"] == d2["both_context_answers"] == 1
    assert d2["mechanically_valid_citation_answers"] == 1
    assert result["coverage"]["issues"] == []
    # A changed immutable file is unknown, rather than accepted or counted empty.
    with ready.docs.db.session() as db:
        row = db.one("SELECT snapshot_key FROM t_evaluation_run WHERE id=?", (good["run_id"],))
    ready.docs.storage.path(row["snapshot_key"]).write_bytes(b"changed")
    changed = Dashboard(ready.docs).get(year=2024)["coverage"]
    d2 = changed["dimensions"][1]
    assert d2["unknown_answers"] == 1 and d2["succeeded_answers"] == d2["both_context_answers"] == 0
    assert changed["issues"][0]["run_id"] == good["run_id"]
    assert "storage" not in dump(changed) and "snapshot_key" not in dump(changed)
    assert len(provider.calls) == 2


def test_overview_treats_corrupt_request_mode_and_cross_scope_as_unknown(ready, evaluation):
    service, _ = evaluation
    task = submit(ready, service)
    execute(ready, service, task)
    # Changing a row's scope cannot make an unchanged frozen answer appear as
    # evidence for another year, even when the original hashes are intact.
    with ready.docs.db.session() as db:
        db.execute("UPDATE t_evaluation_run SET report_year=2023 WHERE id=?", (task["run_id"],))
    result = Dashboard(ready.docs).get(year=2023)
    d2 = result["coverage"]["dimensions"][1]
    assert d2["unknown_answers"] == 1 and d2["both_context_answers"] == 0
    assert result["coverage"]["issues"][0]["error_code"] == "EVALUATION_SCOPE_MISMATCH"
    with ready.docs.db.session() as db:
        db.execute("UPDATE t_evaluation_run SET request_json='broken' WHERE id=?", (task["run_id"],))
    result = Dashboard(ready.docs).get(year=2023)
    assert result["recent_runs"][0]["mode"] == "unknown"
    assert result["coverage"]["dimensions"][1]["unknown_answers"] == 1


def test_coverage_counts_partial_run_answers_without_inventing_dimension_scores(ready, evaluation):
    service, provider = evaluation
    old_complete = provider.complete
    def fail_one(messages, **kwargs):
        question = json.loads(messages[1]["content"])["question"]
        provider.bad_output = question["dimension"] == "D3"
        return old_complete(messages, **kwargs)
    provider.complete = fail_one
    task = submit(ready, service, full=True)
    _, run = execute(ready, service, task)
    assert run["status"] == "partial"
    result = Dashboard(ready.docs).get(year=2024)
    by_dimension = {item["code"]: item for item in result["coverage"]["dimensions"]}
    assert by_dimension["D3"]["failed_answers"] == 1
    assert by_dimension["D1"]["succeeded_answers"] == by_dimension["D2"]["succeeded_answers"] == by_dimension["D4"]["succeeded_answers"] == 1
    assert by_dimension["D4"]["evidence_insufficient_answers"] == 1
    assert result["coverage"]["quality_score"] is None and result["third_chapter_index"] is None


def test_cancelled_run_does_not_publish_residual_success_as_source_coverage(ready, evaluation):
    service, provider = evaluation
    task = submit(ready, service, full=True)
    job, owner = claim(ready, task)
    control = JobControl(ready.docs)
    def cancel_second(*args):
        if len(provider.calls) == 2:
            control.cancel(job["id"])
    provider.hook = cancel_second
    with pytest.raises(DomainError) as caught:
        service.build(json.loads(job["payload_json"]), job["id"], owner,
                      lambda: control.checkpoint(job["id"], owner))
    assert caught.value.code == "TASK_CANCELLED"
    control.finish(job["id"], owner, error="TASK_CANCELLED")
    run = service.get(task["run_id"])
    assert run["status"] == "cancelled" and run["snapshot"] is None
    assert run["answers"][0]["status"] == "succeeded"
    coverage = Dashboard(ready.docs).get(year=2024)["coverage"]
    d1 = coverage["dimensions"][0]
    assert d1["unknown_answers"] == 1 and d1["succeeded_answers"] == d1["both_context_answers"] == 0
    assert sum(item["failed_answers"] for item in coverage["dimensions"]) == 3
    assert coverage["issues"] == [{"run_id": task["run_id"], "error_code": "EVALUATION_UNPUBLISHED_ANSWERS"}]


def test_coverage_window_is_explicit_and_includes_retries_as_distinct_runs(ready, evaluation, monkeypatch):
    service, _ = evaluation
    first = submit(ready, service)
    JobControl(ready.docs).cancel(first["id"])
    retried = JobControl(ready.docs).retry(first["id"])
    submit(ready, service)
    monkeypatch.setattr("app.dashboard.COVERAGE_RUN_LIMIT", 2)
    result = Dashboard(ready.docs).get(year=2024)
    coverage = result["coverage"]
    assert result["counts"]["evaluation_runs"] == 3
    assert (coverage["total_runs"], coverage["inspected_runs"], coverage["omitted_runs"], coverage["truncated"]) == (3, 2, 1, True)
    assert sum(item["total_answers"] for item in coverage["dimensions"]) == 2
    assert retried["run_id"] != first["run_id"]


def test_dashboard_and_companies_http_contract(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("multipart")
    from fastapi.testclient import TestClient
    from app.documents.api import create_app
    from app.documents.settings import Settings
    import sys
    app = create_app(Settings("sqlite:///" + str(tmp_path / "http.sqlite"), tmp_path / "assets", sys.executable))
    app.state.documents.db.migrate()
    with TestClient(app) as client:
        client.post("/api/v1/companies", json={"stock_code": "000001", "stock_name": "Alpha"})
        response = client.get("/api/v1/companies", params={"q": "alpha", "page_size": 1})
        assert response.status_code == 200
        assert response.json()["data"]["total"] == response.json()["data"]["page_size"] == 1
        company_id = response.json()["data"]["items"][0]["id"]
        response = client.get("/api/v1/dashboard", params={"company_id": company_id, "year": 2024})
        assert response.status_code == 200 and response.json()["data"]["scope"]["stock_code"] == "000001"
        assert client.get("/api/v1/dashboard", params={"year": 2101}).status_code == 422
        assert client.get("/api/v1/companies", params={"page_size": 201}).status_code == 422


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="Disposable PostgreSQL is available in CI only")
def test_postgres_dashboard_scope_frozen_coverage_and_literal_company_search(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from app.documents.service import Documents
    from app.documents.settings import Settings
    from app.evaluation.service import Evaluations
    from test_documents import parsed_law
    from test_evaluation_service import MockProvider
    docs = Documents(Settings(os.environ["TEST_POSTGRES_URL"], tmp_path / "overview-pg", sys.executable))
    docs.db.migrate()
    selected = docs.company("019995", "Overview 100%_! Fixture")
    docs.company("019994", "Overview 100AB Fixture")
    _, law = parsed_law(docs)
    _, annual = annual_fixture(docs, monkeypatch, stock="019995", year=2050)
    # A distinct company/year document cannot widen the selected scope.
    annual_fixture(docs, monkeypatch, stock="019994", year=2049)
    Indexes(docs).enqueue([law["parse_id"], annual["parse_id"]], "bm25_diagnostic", False)
    indexed = work_once(docs)
    assert indexed["status"] == "succeeded"
    provider = MockProvider()
    service = Evaluations(docs, provider)
    task = service.enqueue(indexed["result"]["index_id"], "019995", 2050,
        questions=[{"key": "audit-pg", "dimension": "D2", "text": "审计委员会履职"}],
        retrieval={"tokenizer_kind": "utf8_diagnostic"})
    execute(SimpleNamespace(docs=docs), service, task)
    page = docs.company_page(q="%_!", size=1)
    assert page["total"] == 1 and page["items"][0]["id"] == selected["id"]
    assert docs.company_page(q="OVERVIEW", stock_code="019994.SZ")["total"] == 1
    result = Dashboard(docs).get(selected["id"], 2050)
    assert result["counts"]["annual_documents"] == result["counts"]["ready_indexes"] == result["counts"]["evaluation_runs"] == 1
    assert result["counts"]["regulation_documents"] >= 1
    d2 = result["coverage"]["dimensions"][1]
    assert d2["succeeded_answers"] == d2["both_context_answers"] == 1
    assert not result["coverage"]["issues"]
    other_year = Dashboard(docs).get(selected["id"], 2049)
    assert other_year["counts"]["annual_documents"] == other_year["counts"]["ready_indexes"] == other_year["counts"]["evaluation_runs"] == 0
    assert len(provider.calls) == 1
