"""Real HTML/PDF rendering and durable export tests; evaluation payloads are fixtures."""
import copy
import json
from types import SimpleNamespace

import pymupdf as fitz
import pytest

from app.documents.common import DomainError, dump, sha
from app.documents.database import Database
from app.documents.storage import Storage
from app.reports.render import render_html, render_pdf, value
from app.reports.schema import upgrade_reports
from app.reports.service import Reports


def evaluation_fixture():
    source = {"evidence_id": "A1", "title": "完整年报 <script>alert('source')</script>",
              "version_id": "11111111-1111-4111-8111-111111111111", "physical_page": 12,
              "source_format": "pdf", "char_start": 0, "char_end": 10,
              "content": "董事会独立董事履职情况", "content_sha256": "a" * 64,
              "metadata": {"effective_to_exclusive": None}}
    answer = {"id": "answer-one", "question_key": "D1-01", "dimension": "D1",
              "question_text": "独立董事如何履职？", "status": "succeeded",
              "answer_text": "据 [A1]，公司披露了履职情况。<script>alert('answer')</script>",
              "limitations": ["尚未核验法律适用性"], "evidence_status": "insufficient",
              "claims": [{"text": "披露了履职情况", "support_status": "partial",
                          "semantic_support_verified": False, "human_review_status": "not_reviewed",
                          "citations": [{"evidence_id": "A1", "quote": source["content"],
                                         "source_record": source}]}],
              "validation": {"status": "valid", "citation_count": 1,
                             "mechanical_citation_valid_count": 1, "semantic_support_verified": False},
              "context": {"source_records": [source], "omitted_records": [{"reason": "budget", "chunk_id": "omitted-one"}],
                          "diagnostics": {"missing_context_roles": ["regulation"]}},
              "retrieval": {"temporal_filter": False}, "prompt": {"version": "v1"}}
    return {"id": "00000000-0000-4000-8000-000000000001", "status": "partial",
            "stock_code": "000001", "report_year": 2024, "snapshot_sha256": "e" * 64,
            "snapshot": {"schema_version": "evaluation-snapshot-v1", "answers": [answer],
                         "request": {"index_id": "frozen-index", "model": {"model": "test-fixture"}},
                         "summary": {"dimensions": [{"code": "D1", "name": "治理结构", "status": "partial",
                                                       "succeeded_count": 0, "score": None}],
                                     "limitations": ["D2/D3/D4 未执行"]},
                         "model_calls": [{"status": "succeeded", "usage": {"input_tokens": 0, "output_tokens": None}}]}}


@pytest.fixture
def reports(tmp_path):
    db = Database("sqlite:///" + str(tmp_path / "r.db"))
    with db.session() as session:
        # Deliberately isolate the report contract from evaluation scheduling.
        session.execute("CREATE TABLE t_evaluation_run(id VARCHAR(36) PRIMARY KEY)")
        session.execute("INSERT INTO t_evaluation_run(id) VALUES(?)", (evaluation_fixture()["id"],))
        upgrade_reports(session)
    payload = evaluation_fixture()
    evaluations = SimpleNamespace(get=lambda run_id: copy.deepcopy(payload))
    service = Reports(SimpleNamespace(db=db, storage=Storage(tmp_path / "a")), evaluations)
    return service, payload


def test_html_escapes_all_model_and_source_text_and_has_no_external_assets():
    evaluation = evaluation_fixture()
    data, manifest = render_html(evaluation, dump(evaluation).encode("utf-8"))
    text = data.decode("utf-8")
    assert "<script>" not in text and "&lt;script&gt;" in text
    assert "Content-Security-Policy" in text and "default-src 'none'" in text
    assert "指数结果未接入" in text and "尚未人工复核" in text
    assert "null" in text and '已完成题数：0' in text
    assert "/api/v1/document-versions/11111111-1111-4111-8111-111111111111/file#page=12" in text
    assert manifest["embedded_snapshot_sha256"] == sha(dump(evaluation).encode("utf-8"))


def test_zero_null_and_source_unsafe_urls_are_not_confused():
    assert value(0) == "0" and value(None) == "未提供 (null)"
    evaluation = evaluation_fixture()
    evaluation["snapshot"]["answers"][0]["context"]["source_records"][0]["version_id"] = 'javascript:alert(1)"'
    data, _ = render_html(evaluation, dump(evaluation).encode("utf-8"))
    assert b'href="javascript:' not in data
    assert "原始版本链接不可用" in data.decode("utf-8")


def test_custom_partial_dimension_coverage_is_explicit_in_report():
    evaluation = evaluation_fixture()
    evaluation["snapshot"]["summary"].update(
        coverage_complete=False, not_run_dimensions=["D2", "D3", "D4"], question_set_is_custom=True)
    data, _ = render_html(evaluation, dump(evaluation).encode("utf-8"))
    text = data.decode("utf-8")
    assert "本运行未覆盖四个维度" in text and "不能据此声称完成全维度评价" in text
    assert "本运行使用自定义问题" in text


def test_html_never_truncates_long_answer_or_evidence():
    evaluation = evaluation_fixture()
    ending = "本段最后一句独特标记 END-OF-LONG-ANSWER"
    evaluation["snapshot"]["answers"][0]["answer_text"] = "长文评价" * 10000 + ending
    evaluation["snapshot"]["answers"][0]["context"]["source_records"][0]["content"] = "长证据" * 10000 + "证据结尾"
    data, _ = render_html(evaluation, dump(evaluation).encode("utf-8"))
    text = data.decode("utf-8")
    assert "长文评价" * 10000 + ending in text
    assert "长证据" * 10000 + "证据结尾" in text


def test_pdf_has_searchable_chinese_embedded_font_complete_pagination_and_exact_snapshot():
    evaluation = evaluation_fixture()
    long_text = "中文完整分页核查。" * 500 + "全部正文结尾终止标记"
    evaluation["snapshot"]["answers"][0]["answer_text"] = long_text
    frozen = dump(evaluation).encode("utf-8")
    data, manifest = render_pdf(evaluation, frozen)
    with fitz.open(stream=data, filetype="pdf") as document:
        assert len(document) >= 4 and manifest["page_count"] == len(document)
        all_text = "".join(page.get_text() for page in document)
        assert "上市公司自律性评价报告" in all_text
        compact = "".join(page.get_text(clip=fitz.Rect(40, 43, 555, 795)) for page in document).replace("\n", "")
        assert long_text in compact
        assert document.embfile_get("evaluation-snapshot.json") == frozen
        assert any(document.extract_font(font[0])[3] for font in document[0].get_fonts())
        for page in document:
            for block in page.get_text("dict")["blocks"]:
                if "lines" not in block:
                    continue
                for line in block["lines"]:
                    for span in line["spans"]:
                        assert span["bbox"][0] >= 40 and span["bbox"][2] <= 555
                        assert span["bbox"][1] >= 10 and span["bbox"][3] <= 830
    assert manifest["font_sha256"] and manifest["embedded_snapshot_sha256"] == sha(frozen)


def test_pdf_fails_explicitly_for_unrenderable_characters():
    evaluation = evaluation_fixture()
    evaluation["snapshot"]["answers"][0]["answer_text"] = "无字形\U0010ffff"
    with pytest.raises(DomainError) as error:
        render_pdf(evaluation, dump(evaluation).encode("utf-8"))
    assert error.value.code == "REPORT_GLYPH_UNAVAILABLE"


def test_report_is_frozen_and_reopened_download_does_not_read_latest_evaluation(reports):
    service, payload = reports
    created = service.create(payload["id"], "html")
    data, metadata = service.download(created["id"])
    payload["snapshot"]["answers"][0]["answer_text"] = "后来的变化不得改写历史报告"
    reopened = Reports(service.docs, SimpleNamespace(get=lambda run_id: (_ for _ in ()).throw(AssertionError("must not call"))))
    assert reopened.download(created["id"]) == (data, metadata)
    assert b"storage" not in dump(metadata).encode("utf-8")
    assert sha(data) == created["file_sha256"]
    assert service.list(payload["id"])["total"] == 1


def test_export_idempotency_replays_existing_artifacts_and_conflicts(reports, monkeypatch):
    service, payload = reports
    created = service.create(payload["id"], key="same-export")
    monkeypatch.setattr(service, "_evaluation", lambda *args: (_ for _ in ()).throw(AssertionError("must not call")))
    assert service.create(payload["id"], key="same-export") == created
    with pytest.raises(DomainError) as error:
        service.create(payload["id"], "pdf", key="same-export")
    assert error.value.code == "IDEMPOTENCY_CONFLICT"


@pytest.mark.parametrize("artifact", ["file", "snapshot"])
def test_tampered_artifact_is_not_served_or_silently_regenerated(reports, artifact):
    service, payload = reports
    created = service.create(payload["id"], key="tamper")
    with service.docs.db.session() as db:
        row = db.one("SELECT * FROM t_report_export WHERE id=?", (created["id"],))
    service.docs.storage.path(row[artifact + "_key"]).write_bytes(b"tampered")
    with pytest.raises(DomainError) as error:
        service.download(created["id"])
    assert error.value.code == "HASH_MISMATCH"
    with pytest.raises(DomainError):
        service.create(payload["id"], key="tamper")


@pytest.mark.parametrize("run_status", ["queued", "running", "failed", "cancelled", "budget_exhausted"])
def test_unfinished_or_failed_evaluation_cannot_be_exported_as_formal_report(reports, run_status):
    service, payload = reports
    payload["status"] = run_status
    with pytest.raises(DomainError) as error:
        service.create(payload["id"])
    assert error.value.code == "REPORT_NOT_READY"
    assert service.list(payload["id"])["total"] == 0


def test_no_body_is_not_an_exportable_report(reports):
    service, payload = reports
    payload["snapshot"]["answers"][0]["answer_text"] = ""
    with pytest.raises(DomainError) as error:
        service.create(payload["id"])
    assert error.value.code == "REPORT_NOT_READY"


def test_pdf_export_can_be_downloaded_with_correct_mime_and_manifest(reports):
    service, payload = reports
    created = service.create(payload["id"], "pdf")
    data, meta = service.download(created["id"])
    assert data.startswith(b"%PDF-") and meta["media_type"] == "application/pdf"
    assert created["manifest"]["page_count"] >= 1 and created["size_bytes"] == len(data)
    assert created["filename"].endswith(".pdf")


@pytest.mark.parametrize("format,key", [("docx", None), ("html", ""), ("html", "x" * 201)])
def test_invalid_export_request_rejected(reports, format, key):
    service, payload = reports
    with pytest.raises(DomainError) as error:
        service.create(payload["id"], format, key)
    assert error.value.status == 422


def test_pagination_is_validated_and_missing_run_is_not_an_empty_history(reports):
    service, payload = reports
    with pytest.raises(DomainError) as error:
        service.list(payload["id"], page=True)
    assert error.value.code == "INVALID_PAGINATION"
    with pytest.raises(DomainError) as error:
        service.list("missing")
    assert error.value.status == 404
