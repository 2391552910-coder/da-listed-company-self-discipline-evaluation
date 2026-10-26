"""Stage 4/5 wiring with explicit fixture model outputs, never quality evidence."""
import json
import sys

import pytest

from app.documents.common import dump
from app.documents.indexes import Indexes
from app.documents.service import Documents
from app.documents.settings import Settings
from app.documents.worker import work_once
from app.evaluation.service import Evaluations
from app.reports.service import Reports
from test_documents import annual_fixture, parsed_law


class FixtureProvider:
    """No network: one optional invalid JSON answer tests partial aggregation."""
    def __init__(self, invalid_call=None):
        self.count = 0
        self.invalid_call = invalid_call

    def identity(self, model=None):
        return {"provider": "fixture-only", "model": "fixture-only", "temperature": None}

    def complete(self, messages, **kwargs):
        kwargs["heartbeat"]()
        self.count += 1
        output = {
            "answer": "年报披露了监督职责[A1]；监管片段列出职责[R1]。此为系统集成测试响应。",
            "evidence_status": "sufficient", "limitations": ["fixture-only，不是实际公司评价"],
            "claims": [{"id": "C1", "text": "测试引用年报监督职责", "support_status": "supported",
                        "citations": [{"evidence_id": "A1", "quote": "审计委员会履行监督职责。"}]},
                       {"id": "C2", "text": "测试引用法规监督职责", "support_status": "supported",
                        "citations": [{"evidence_id": "R1", "quote": "审计委员会依法履行监督职责。"}]}],
        }
        return {"text": "invalid fixture JSON" if self.count == self.invalid_call else dump(output),
                "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200},
                "model": "fixture-only", "request_id": f"fixture-{self.count}", "finish_reason": "stop"}


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    docs = Documents(Settings("sqlite:///"+str(tmp_path/"integration.sqlite"), tmp_path/"assets", sys.executable))
    docs.db.migrate()
    _, law = parsed_law(docs)
    _, annual = annual_fixture(docs, monkeypatch)
    Indexes(docs).enqueue([law["parse_id"], annual["parse_id"]], "bm25_diagnostic", False)
    job = work_once(docs)
    assert job["status"] == "succeeded"
    return docs, job["result"]["index_id"]


def test_worker_partial_run_can_be_read_and_exported(prepared, monkeypatch):
    docs, index_id = prepared
    provider = FixtureProvider(invalid_call=2)
    monkeypatch.setattr("app.evaluation.provider.Provider", lambda settings: provider)
    service = Evaluations(docs)
    queued = service.enqueue(index_id, "000001", 2024,
                             retrieval={"tokenizer_kind": "utf8_diagnostic"})
    finished = work_once(docs)
    assert finished["id"] == queued["id"] and finished["status"] == "partial"
    run = service.get(queued["run_id"])
    assert run["status"] == run["snapshot"]["summary"]["status"] == "partial"
    assert run["snapshot"]["summary"]["succeeded_count"] == 3 and provider.count == 4
    assert run["answers"][1]["status"] == "failed"
    assert run["snapshot"]["summary"]["dimensions"][3]["status"] == "insufficient"
    reports = Reports(docs)
    html = reports.create(run["id"], "html", "fixture-html")
    content, meta = reports.download(html["id"])
    assert meta["format"] == "html" and "部分完成".encode() in content
    assert "MODEL_OUTPUT_INVALID".encode() in content
    pdf = reports.create(run["id"], "pdf", "fixture-pdf")
    content, meta = reports.download(pdf["id"])
    assert content.startswith(b"%PDF-") and meta["format"] == "pdf"
    assert reports.create(run["id"], "pdf", "fixture-pdf")["id"] == pdf["id"]


def test_worker_budget_terminal_retains_replay_proof(prepared, monkeypatch):
    docs, index_id = prepared
    provider = FixtureProvider()
    monkeypatch.setattr("app.evaluation.provider.Provider", lambda settings: provider)
    service = Evaluations(docs)
    queued = service.enqueue(index_id, "000001", 2024,
                             retrieval={"tokenizer_kind": "utf8_diagnostic", "max_reference_tokens": 1})
    finished = work_once(docs)
    assert finished["status"] == "budget_exhausted" and provider.count == 0
    run = service.get(queued["run_id"])
    assert run["snapshot"]["summary"]["status"] == "budget_exhausted"
    assert run["snapshot_sha256"] == finished["result"]["snapshot_sha256"]


def test_http_evaluation_evidence_and_report_download(prepared, monkeypatch):
    pytest.importorskip("fastapi", reason="HTTP dependencies unavailable locally; exercised in CI")
    pytest.importorskip("multipart")
    from fastapi.testclient import TestClient
    from app.documents.api import create_app

    docs, index_id = prepared
    provider = FixtureProvider()
    monkeypatch.setattr("app.evaluation.provider.Provider", lambda settings: provider)
    app = create_app(docs.settings)
    with TestClient(app) as client:
        body = {"index_id": index_id, "stock_code": "000001", "report_year": 2024,
                "retrieval": {"tokenizer_kind": "utf8_diagnostic"}}
        assert client.post("/api/v1/evaluations", json=body | {"report_year": True}).status_code == 422
        queued_response = client.post("/api/v1/evaluations", json=body, headers={"Idempotency-Key": "http-eval"})
        assert queued_response.status_code == 202
        queued = queued_response.json()["data"]
        repeated = client.post("/api/v1/evaluations", json=body, headers={"Idempotency-Key": "http-eval"})
        assert repeated.json()["data"]["id"] == queued["id"]
        assert work_once(app.state.documents)["status"] == "succeeded"
        run = client.get(f"/api/v1/evaluations/{queued['run_id']}").json()["data"]
        assert run["status"] == "succeeded" and len(run["answers"]) == 4
        answer = run["answers"][0]
        evidence = client.get(f"/api/v1/evaluations/{run['id']}/answers/{answer['id']}/evidence")
        assert evidence.status_code == 200 and evidence.json()["data"]["claims"]
        assert client.get(f"/api/v1/evaluations/{run['id']}/trace").status_code == 200
        assert client.get("/api/v1/evaluations", params={"stock_code": "000002"}).json()["data"]["total"] == 0
        assert client.get(f"/api/v1/evaluations/{run['id']}/answers/not-an-answer/evidence").status_code == 404
        for kind in ("html", "pdf"):
            exported = client.post(f"/api/v1/evaluations/{run['id']}/exports", json={"format": kind})
            assert exported.status_code == 201
            report_id = exported.json()["data"]["id"]
            downloaded = client.get(f"/api/v1/exports/{report_id}/file")
            assert downloaded.status_code == 200
            assert "attachment" in downloaded.headers["content-disposition"]
            assert downloaded.headers["x-content-type-options"] == "nosniff"
            if kind == "pdf":
                assert downloaded.content.startswith(b"%PDF-")
            else:
                assert "text/html" in downloaded.headers["content-type"]
        assert client.get(f"/api/v1/evaluations/{run['id']}/exports").json()["data"]["total"] == 2
