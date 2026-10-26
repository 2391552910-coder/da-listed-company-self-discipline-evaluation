"""Requires requirements-test.txt; skipped explicitly in dependency-restricted environments."""
import pytest
pytest.importorskip("fastapi",reason="Web dependencies are unavailable in this environment")
pytest.importorskip("multipart",reason="Multipart upload dependency is unavailable")
from fastapi.testclient import TestClient
from app.documents.api import create_app
from app.documents.settings import Settings
from app.documents.worker import work_once
import sys

def test_http_upload_task_and_source(tmp_path):
    app=create_app(Settings("sqlite:///"+str(tmp_path/"api.sqlite"),tmp_path/"files",sys.executable))
    docs=app.state.documents
    docs.db.migrate()
    with TestClient(app) as client:
        assert client.get("/health/ready").status_code==200
        response=client.post("/api/v1/documents/upload",data={"corpus_type":"regulation","title":"规则"},
                             files={"file":("law.txt","审计委员会职责".encode(),"text/plain")})
        assert response.status_code==200
        version=response.json()["data"]
        task=client.post(f"/api/v1/document-versions/{version['id']}/parse",json={})
        assert task.status_code==202
        job=work_once(docs)
        assert job["status"]=="succeeded"
        response=client.get(f"/api/v1/document-versions/{version['id']}/chunks")
        chunk=response.json()["data"]["items"][0]
        assert client.get(f"/api/v1/chunks/{chunk['id']}/source").json()["data"]["source_available"]
        assert client.get(f"/api/v1/document-versions/{version['id']}/file").content=="审计委员会职责".encode()
        assert client.post("/api/v1/retrieval/search",json={"index_id":"x"}).status_code==422


def test_http_context_queue_cancel_retry_and_replay(tmp_path,monkeypatch):
    from app.documents.indexes import Indexes
    from test_documents import parsed_law,annual_fixture
    app=create_app(Settings("sqlite:///"+str(tmp_path/"context-api.sqlite"),tmp_path/"files",sys.executable))
    docs=app.state.documents
    docs.db.migrate()
    law,lp=parsed_law(docs)
    annual,ap=annual_fixture(docs,monkeypatch)
    Indexes(docs).enqueue([lp["parse_id"],ap["parse_id"]],"bm25_diagnostic",False)
    indexed=work_once(docs)
    assert indexed["status"]=="succeeded"
    payload={"index_id":indexed["result"]["index_id"],"query":"审计委员会","stock_code":"000001","report_year":2024,
             "tokenizer_kind":"utf8_diagnostic","max_reference_tokens":6000}
    with TestClient(app) as client:
        invalid=client.post("/api/v1/retrieval/contexts",json=payload|{"max_reference_tokens":True})
        assert invalid.status_code==422
        response=client.post("/api/v1/retrieval/contexts",json=payload,headers={"Idempotency-Key":"http-context"})
        assert response.status_code==202
        first=response.json()["data"]
        repeated=client.post("/api/v1/retrieval/contexts",json=payload,headers={"Idempotency-Key":"http-context"})
        assert repeated.json()["data"]["id"]==first["id"]
        assert client.get("/api/v1/jobs",params={"status":"queued","page_size":5}).json()["data"]["total"]==1
        cancelled=client.post(f"/api/v1/jobs/{first['id']}/cancel")
        assert cancelled.json()["data"]["status"]=="cancelled"
        run=client.get(f"/api/v1/retrieval/contexts/{first['run_id']}").json()["data"]
        assert run["status"]=="cancelled" and run["snapshot"] is None
        retried=client.post(f"/api/v1/jobs/{first['id']}/retry")
        assert retried.status_code==202
        new=retried.json()["data"]
        assert new["run_id"]!=first["run_id"]
        assert work_once(docs)["status"]=="succeeded"
        replay=client.get(f"/api/v1/retrieval/contexts/{new['run_id']}").json()["data"]
        assert replay["snapshot"]["context"]["source_records"]
        assert replay["snapshot"]["context"]["budget_unit"]=="utf8_bytes"
        assert client.get("/api/v1/retrieval/contexts").json()["data"]["total"]==2
