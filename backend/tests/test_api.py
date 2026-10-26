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
