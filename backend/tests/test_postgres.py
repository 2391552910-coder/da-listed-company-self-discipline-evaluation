"""Run against a disposable PostgreSQL service in CI; never against a user database."""
import os
import sys
import pytest
from app.documents.service import Documents
from app.documents.settings import Settings
from app.documents.worker import work_once

@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"),reason="Disposable PostgreSQL test URL not configured")
def test_postgres_migration_task_and_locator(tmp_path):
    docs=Documents(Settings(os.environ["TEST_POSTGRES_URL"],tmp_path/"postgres-assets",sys.executable))
    docs.db.migrate()
    company=docs.company("019999","CI only")
    docs.db.migrate()
    assert docs.company("019999","CI only")["id"]==company["id"]
    assert docs.db.ready()
    version=docs.upload("审计委员会履职依据".encode(),"law.txt","regulation","CI rule")
    queued=docs.enqueue_parse(version["id"])
    result=work_once(docs)
    assert result["id"]==queued["id"] and result["status"]=="succeeded"
    chunk=docs.chunks(version["id"])["items"][0]
    source=docs.source(chunk["id"])
    assert source["content"]=="审计委员会履职依据"
    assert source["physical_page"] is None and source["source_available"]


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"),reason="Disposable PostgreSQL test URL not configured")
def test_postgres_context_snapshot_and_retry_lineage(tmp_path,monkeypatch):
    from app.documents.indexes import Indexes
    from app.documents.jobs import JobControl
    from app.retrieval.service import Contexts
    from test_documents import parsed_law,annual_fixture
    docs=Documents(Settings(os.environ["TEST_POSTGRES_URL"],tmp_path/"context-pg",sys.executable))
    docs.db.migrate()
    if os.getenv("TEST_LEGACY_SEED"):
        with docs.db.session() as db:
            assert db.one("SELECT id FROM t_company WHERE stock_code='019998'")
    law,lp=parsed_law(docs)
    annual,ap=annual_fixture(docs,monkeypatch)
    Indexes(docs).enqueue([lp["parse_id"],ap["parse_id"]],"bm25_diagnostic",False)
    built=work_once(docs)
    assert built["status"]=="succeeded"
    contexts=Contexts(docs)
    task=contexts.enqueue(built["result"]["index_id"],"审计委员会","000001",2024,tokenizer_kind="utf8_diagnostic",
                          max_reference_tokens=1)
    exhausted=work_once(docs)
    assert exhausted["status"]=="budget_exhausted"
    run=contexts.get(task["run_id"])
    assert run["snapshot"]["context"]["reference_token_count"]==0
    retried=JobControl(docs).retry(task["id"])
    assert retried["parent_job_id"]==task["id"] and retried["run_id"]!=task["run_id"]
    assert work_once(docs)["status"]=="budget_exhausted"
    assert contexts.get(task["run_id"])["snapshot_sha256"]==run["snapshot_sha256"]
