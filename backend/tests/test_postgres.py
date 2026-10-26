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
