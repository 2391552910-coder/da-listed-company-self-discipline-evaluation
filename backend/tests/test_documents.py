import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from app.documents.common import DomainError, sha
from app.documents.service import Documents
from app.documents.indexes import Indexes
from app.documents.parsing import parse_file, check_annual_identity
from app.documents.settings import Settings
from app.documents.worker import work_once

@pytest.fixture
def docs(tmp_path):
    import sys
    service=Documents(Settings("sqlite:///"+str(tmp_path/"data.sqlite"),tmp_path/"assets",sys.executable))
    service.db.migrate()
    return service

def text_version(docs,text="审计委员会依法履行监督职责。",document_id=None):
    return docs.upload(text.encode(),"law.txt","regulation","监管规则",document_id=document_id)

def parsed_law(docs,text="审计委员会依法履行监督职责。"):
    version=text_version(docs,text)
    queued=docs.enqueue_parse(version["id"])
    job=work_once(docs)
    assert job["id"]==queued["id"] and job["status"]=="succeeded",job
    return version,job["result"]

def annual_fixture(docs,monkeypatch,stock="000001",year=2024):
    # Unit fixture replaces only the parser boundary; real PDFs are tested by smoke_real_documents.py.
    company=docs.company(stock,"Fixture Corp")
    version=docs.upload(b"%PDF-1.4\nfixture","report.pdf","annual_report","年报",company["id"],year)
    text=f"Fixture Corp {stock} {year}年年度报告：审计委员会履行监督职责。"
    def fake_parser(*args):
        return ([{"physical_page":1,"text":text,"text_sha256":sha(text.encode()),
                  "text_origin":"unit_fixture","spans":[(0,len(text),text)]}],
                {"name":"unit_fixture","version":"1","opening":text},[])
    monkeypatch.setattr("app.documents.service.parse_file",fake_parser)
    result=docs.parse(version["id"],{"chunk_chars":1200,"overlap_chars":160,"physical_pages":None})
    return version,result

def test_migration_idempotent_and_company_identity(docs):
    company=docs.company("000001.SZ","示例")
    docs.db.migrate()
    assert docs.company("000001","另一个名称")["id"]==company["id"]
    assert docs.db.ready()
    assert company["stock_code"]=="000001"

def test_duplicate_upload_and_immutable_versions(docs):
    first=text_version(docs)
    repeated=text_version(docs,document_id=first["document_id"])
    second=text_version(docs,"新版本内容",first["document_id"])
    assert repeated["id"]==first["id"] and repeated["duplicate"]
    assert second["id"]!=first["id"]
    assert len(docs.versions(first["document_id"]))==2

@pytest.mark.parametrize("path",["../escape","/absolute","sources/../../escape"])
def test_storage_escape_rejected(docs,path):
    with pytest.raises(DomainError):
        docs.storage.path(path)

@pytest.mark.parametrize("args",[
    dict(data=b"bad",filename="bad.pdf",corpus_type="regulation",title="x"),
    dict(data=b"x",filename="bad.exe",corpus_type="regulation",title="x"),
    dict(data=b"x",filename="law.txt",corpus_type="regulation",title="x",report_year=2024),
    dict(data=b"x",filename="law.txt",corpus_type="regulation",title="x",metadata={"effective_from":"bad"}),
    dict(data=b"x",filename="law.txt",corpus_type="regulation",title="x",metadata={"effective_from":"2024-02-01","effective_to_exclusive":"2024-01-01"}),
])
def test_upload_validation(docs,args):
    with pytest.raises(DomainError):
        docs.upload(**args)

def test_persistent_job_idempotency(docs):
    version=text_version(docs)
    first=docs.enqueue_parse(version["id"],key="same")
    assert first["id"]==docs.enqueue_parse(version["id"],key="same")["id"]
    with pytest.raises(DomainError,match="幂等"):
        docs.enqueue_parse(version["id"],{"chunk_chars":500},key="same")
    reopened=Documents(docs.settings)
    assert work_once(reopened)["status"]=="succeeded"

def test_source_exact_offsets_and_missing_original(docs):
    version,parsed=parsed_law(docs)
    chunk=docs.chunks(version["id"])["items"][0]
    source=docs.source(chunk["id"])
    assert source["frame_text"][source["char_start"]:source["char_end"]]==source["content"]
    assert source["physical_page"] is None
    path,_=docs.file(version["id"])
    path.unlink()
    assert docs.source(chunk["id"])["source_available"] is False

def test_file_hash_tamper_blocks_parse(docs):
    version=text_version(docs)
    path,_=docs.file(version["id"])
    path.write_bytes(b"changed")
    docs.enqueue_parse(version["id"])
    job=work_once(docs)
    assert job["status"]=="failed" and job["error_code"]=="HASH_MISMATCH"

def test_reparse_preserves_old_locator(docs):
    version,first=parsed_law(docs,"审计委员会职责。" * 80)
    old_id=docs.chunks(version["id"],first["parse_id"])["items"][0]["id"]
    docs.enqueue_parse(version["id"],{"chunk_chars":200,"overlap_chars":20})
    assert work_once(docs)["status"]=="succeeded"
    assert docs.source(old_id)["content"]

def test_empty_text_fails_without_invented_content(docs):
    version=text_version(docs,"   ")
    docs.enqueue_parse(version["id"])
    assert work_once(docs)["error_code"]=="NO_SEARCHABLE_TEXT"

def test_annual_identity_and_summary():
    assert check_annual_identity("示例000001 2024年年度报告","000001","示例",2024)
    with pytest.raises(DomainError):
        check_annual_identity("000001 2023年年度报告","000001","示例",2024)
    with pytest.raises(DomainError):
        check_annual_identity("000001 2024年年度报告摘要","000001","示例",2024)

def test_scope_and_index_integrity(docs,monkeypatch):
    law,law_parse=parsed_law(docs)
    annual,annual_parse=annual_fixture(docs,monkeypatch)
    indexes=Indexes(docs)
    queue=indexes.enqueue([law_parse["parse_id"],annual_parse["parse_id"]],"bm25_diagnostic",False,"index-once")
    assert indexes.enqueue([law_parse["parse_id"],annual_parse["parse_id"]],"bm25_diagnostic",False,"index-once")["id"]==queue["id"]
    job=work_once(docs)
    assert job["status"]=="succeeded",job
    iid=job["result"]["index_id"]
    result=indexes.search(iid,"审计委员会","000001",2024,2)
    assert result["annual_report"] and result["regulation"] and result["warning"]
    for hit in result["annual_report"]+result["regulation"]:
        assert docs.source(hit["chunk_id"])["content"]==hit["content"]
    with pytest.raises(DomainError) as caught:
        indexes.search(iid,"审计委员会","000002",2024)
    assert caught.value.code=="ANNUAL_SCOPE_EMPTY"
    with pytest.raises(DomainError):
        indexes.search(iid,"审计委员会","000001",2023)
    with docs.db.session() as db:
        index=db.one("SELECT manifest_key FROM t_index_version WHERE id=?",(iid,))
    docs.storage.path(index["manifest_key"]).write_text("{}",encoding="utf-8")
    with pytest.raises(DomainError):
        indexes.validate(iid)

def test_single_source_index_fails(docs):
    law,parsed=parsed_law(docs)
    Indexes(docs).enqueue([parsed["parse_id"]],"bm25_diagnostic")
    result=work_once(docs)
    assert result["status"]=="failed" and result["error_code"]=="BOTH_CORPORA_REQUIRED"

def test_frame_tamper_blocked(docs):
    law,parsed=parsed_law(docs)
    chunk=docs.chunks(law["id"])["items"][0]
    with docs.db.session() as db:
        db.execute("UPDATE t_text_frame SET text='tampered' WHERE id=?",(chunk["frame_id"],))
    with pytest.raises(DomainError):
        docs.source(chunk["id"])
