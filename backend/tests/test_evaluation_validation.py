"""Mechanical citations and question snapshots; no live model calls are made."""
from copy import deepcopy
import json

import pytest

from app.documents.common import DomainError, dump, sha
from app.evaluation.questions import normalize_questions, question_sets
from app.evaluation.validation import validate_output


def source(eid="A1", content="公司设有审计委员会，每年召开会议。", **changes):
    row = {"evidence_id": eid, "source_role": "annual_report" if eid.startswith("A") else "regulation",
           "corpus_type": "annual_report" if eid.startswith("A") else "regulation",
           "chunk_id": "chunk-" + eid, "version_id": "version-" + eid,
           "content": content, "content_sha256": sha(content.encode()),
           "char_start": 12, "char_end": 12 + len(content), "source_format": "pdf",
           "physical_page": 3, "title": "年报" if eid.startswith("A") else "法规"}
    row.update(changes)
    return row


def output(**changes):
    value = {"answer": "年报披露审计委员会安排；实际履职效果仍需核实。",
             "evidence_status": "sufficient", "limitations": ["不独立证明实际履职效果"],
             "claims": [{"id": "C1", "text": "年报披露设有审计委员会。", "support_status": "supported",
                         "citations": [{"evidence_id": "A1", "quote": "设有审计委员会"}]},
                        {"id": "C2", "text": "法规要求设立审计委员会。", "support_status": "supported",
                         "citations": [{"evidence_id": "R1", "quote": "审计委员会"}]}]}
    value.update(changes)
    return value


def records():
    return [source(), source("R1", "上市公司应设立审计委员会。")]


def codes(result):
    return {error["code"] for error in result["validation"]["errors"]}


def test_mechanical_checks_preserve_exact_quote_and_location_without_certifying_semantics():
    value = output()
    # A real quotation with an invented inference still mechanically matches.
    value["claims"][0]["text"] = "所有董事都绝对诚实，未来永无风险。"
    result = validate_output(dump(value), records())
    assert result["validation"]["status"] == "valid"
    assert result["evidence_status"] == "sufficient"
    assert result["validation"]["mechanical_citation_valid_count"] == 2
    claim = result["claims"][0]
    assert claim["support_status"] == "supported" and claim["support_status_source"] == "model"
    assert claim["semantic_support_verified"] is False
    assert claim["human_review_status"] == "not_reviewed"
    citation = claim["citations"][0]
    assert citation["source"]["content"] == records()[0]["content"]
    assert citation["quote_char_start"] == 14
    assert citation["quote_char_end"] == 14 + len("设有审计委员会")
    assert citation["source_integrity_status"] == "not_checked"
    assert result["validation"]["semantic_support_verified"] is False


@pytest.mark.parametrize("text", ["```json\n{}\n```", "[]", "null", "123", "", "{", '{"answer":"a","answer":"b"}', '{"answer":NaN}'])
def test_malformed_or_non_strict_json_never_becomes_a_valid_answer(text):
    result = validate_output(text, records())
    assert result["validation"]["status"] == "invalid"
    assert result["evidence_status"] == "insufficient"


@pytest.mark.parametrize("change,expected", [
    ({"answer": False}, "INVALID_ANSWER"),
    ({"answer": ""}, "INVALID_ANSWER"),
    ({"evidence_status": []}, "INVALID_EVIDENCE_STATUS"),
    ({"evidence_status": "proven"}, "INVALID_EVIDENCE_STATUS"),
    ({"limitations": "none"}, "INVALID_LIMITATIONS"),
    ({"limitations": [""]}, "INVALID_LIMITATIONS"),
    ({"claims": {}}, "INVALID_CLAIMS"),
    ({"extra": "secret"}, "INVALID_OUTPUT_FIELDS"),
    ({"semantic_support_verified": True}, "INVALID_OUTPUT_FIELDS"),
])
def test_strict_schema_rejects_wrong_type_and_unrequested_fields(change, expected):
    result = validate_output(dump(output(**change)), records())
    assert expected in codes(result)


def test_unknown_id_and_invented_quote_are_independently_recorded():
    value = output()
    value["claims"][0]["citations"][0]["quote"] = "从未披露的事实"
    value["claims"][1]["citations"][0]["evidence_id"] = "R99"
    result = validate_output(dump(value), records())
    assert {"QUOTE_NOT_VERBATIM", "UNKNOWN_EVIDENCE_ID", "SUPPORT_WITHOUT_VALID_CITATION"} <= codes(result)
    assert result["validation"]["mechanical_citation_valid_count"] == 0
    assert result["evidence_status"] == "insufficient"


def test_zero_citations_is_valid_for_unknown_insufficient_claim_without_fabricated_score():
    value = output(evidence_status="insufficient", claims=[{
        "id": "C1", "text": "尚缺独立第三方资料，无法作出声誉判断。", "support_status": "unknown", "citations": []}])
    result = validate_output(dump(value), [])
    assert result["validation"]["status"] == "valid"
    assert result["evidence_status"] == "insufficient"
    assert result["claims"][0]["mechanical_citation_status"] == "none"
    assert "score" not in result and "score" not in result["claims"][0]


def test_supported_without_any_citation_is_invalid():
    value = output()
    value["claims"][0]["citations"] = []
    result = validate_output(dump(value), records())
    assert "SUPPORT_WITHOUT_VALID_CITATION" in codes(result)


def test_source_presence_does_not_replace_actual_dual_source_citation():
    value = output(claims=[output()["claims"][0]])
    result = validate_output(dump(value), records())
    assert result["validation"]["status"] == "valid"
    assert result["model_evidence_status"] == "sufficient"
    assert result["evidence_status"] == "insufficient"
    assert result["validation"]["missing_cited_roles"] == ["regulation"]
    assert result["validation"]["warnings"][0]["code"] == "MISSING_CITED_SOURCE_ROLE"
    assert result["validation"]["uncited_context_evidence_ids"] == ["R1"]


@pytest.mark.parametrize("change,expected", [
    ({"content_sha256": "0" * 64}, "SOURCE_CONTENT_HASH_MISMATCH"),
    ({"char_start": True}, "INVALID_SOURCE_LOCATOR"),
    ({"char_end": 999}, "INVALID_SOURCE_LOCATOR"),
    ({"source_role": "regulation"}, "SOURCE_ROLE_MISMATCH"),
    ({"source_format": "txt", "physical_page": 1}, "INVALID_SOURCE_LOCATOR"),
    ({"source_format": []}, "INVALID_SOURCE_LOCATOR"),
])
def test_frozen_source_tampering_is_not_treated_as_a_valid_citation(change, expected):
    rows = records()
    rows[0].update(change)
    result = validate_output(dump(output()), rows)
    assert expected in codes(result)
    assert result["claims"][0]["citations"][0]["mechanical_status"] == "invalid"


def test_duplicate_evidence_ids_are_ambiguous_even_if_text_matches():
    rows = records()
    rows.append(deepcopy(rows[0]))
    result = validate_output(dump(output()), rows)
    assert "DUPLICATE_SOURCE_ID" in codes(result)
    assert result["claims"][0]["citations"][0]["mechanical_status"] == "invalid"


def test_original_source_validator_runs_once_per_record_and_returns_integrity_only():
    calls = []
    def check(row):
        calls.append(row["evidence_id"])
        # Mutation cannot change the frozen result record or affect the next call.
        row["content"] = "mutation"
        return True
    value = output()
    value["claims"][0]["citations"].append({"evidence_id": "A1", "quote": "每年召开会议"})
    result = validate_output(dump(value), records(), check)
    assert calls == ["A1", "R1"]
    assert result["validation"]["status"] == "valid"
    assert result["claims"][0]["citations"][0]["source_integrity_status"] == "verified"
    assert result["claims"][0]["citations"][0]["source"]["content"] == records()[0]["content"]
    assert result["claims"][0]["semantic_support_verified"] is False


@pytest.mark.parametrize("raises", [False, True])
def test_original_source_failure_or_exception_marks_citation_invalid(raises):
    def check(row):
        if row["evidence_id"] == "A1":
            if raises:
                raise DomainError("SOURCE_UNAVAILABLE", "private local path", 409)
            return False
    result = validate_output(dump(output()), records(), check)
    assert "SOURCE_INTEGRITY_FAILED" in codes(result)
    assert "private local path" not in dump(result)
    assert result["claims"][0]["citations"][0]["source_integrity_status"] == "invalid"
    assert result["evidence_status"] == "insufficient"


def test_unhashable_claim_values_fail_cleanly():
    value = output()
    value["claims"][0].update(support_status=[], id={}, citations=[{"evidence_id": {}, "quote": []}])
    result = validate_output(dump(value), records())
    assert {"INVALID_SUPPORT_STATUS", "INVALID_CLAIM_ID", "INVALID_EVIDENCE_ID", "INVALID_QUOTE"} <= codes(result)


def test_duplicate_claim_identity_does_not_get_silently_merged():
    value = output()
    value["claims"][1]["id"] = "C1"
    result = validate_output(dump(value), records())
    assert "DUPLICATE_CLAIM_ID" in codes(result)
    assert len(result["claims"]) == 2


def test_question_set_has_four_named_dimensions_and_independent_snapshots():
    first = question_sets()[0]
    assert first["version"] == "four-dim-v1"
    assert [q["dimension"] for q in first["questions"]] == ["D1", "D2", "D3", "D4"]
    assert [d["name"] for d in first["dimensions"]] == ["信息披露质量", "董事会治理有效性", "内部控制与合规性", "第三方声誉"]
    assert first["questions"][3]["declared_target_date"] is None
    first["questions"][0]["text"] = "changed"
    assert question_sets()[0]["questions"][0]["text"] != "changed"
    assert normalize_questions()["is_custom"] is False


def test_custom_single_question_is_explicitly_labelled_and_date_is_not_inferred():
    snapshot = normalize_questions("four-dim-v1", [{"key": "custom_1", "dimension": "D2", "text": "请评价审计委员会。"}])
    assert snapshot["version"] == "custom" and snapshot["base_version"] == "four-dim-v1"
    assert snapshot["is_custom"] is True
    assert snapshot["questions"][0]["declared_target_date"] is None
    assert snapshot["questions"][0]["target_date_role"] is None


@pytest.mark.parametrize("questions", [
    [], {}, [{"key": "Q", "dimension": "D5", "text": "x"}],
    [{"key": "Q", "dimension": [], "text": "x"}],
    [{"key": "Q", "dimension": "D1", "text": ""}],
    [{"key": "Q", "dimension": "D1", "text": "x", "extra": True}],
    [{"key": "Q", "dimension": "D1", "text": "x"}] * 2,
    [{"key": "Q", "dimension": "D1", "text": "x", "declared_target_date": "20240101", "target_date_role": "event_date"}],
    [{"key": "Q", "dimension": "D1", "text": "x", "declared_target_date": "2024-02-30", "target_date_role": "event_date"}],
    [{"key": "Q", "dimension": "D1", "text": "x", "declared_target_date": "2024-01-01"}],
    [{"key": "Q", "dimension": "D1", "text": "x", "target_date_role": "event_date"}],
])
def test_invalid_question_snapshots_are_rejected_before_work_is_queued(questions):
    with pytest.raises(DomainError) as error:
        normalize_questions("four-dim-v1", questions)
    assert error.value.code == "INVALID_QUESTIONS"


def test_explicit_event_date_is_preserved_with_role_and_unknown_version_rejected():
    snapshot = normalize_questions("four-dim-v1", [{"key": "Q", "dimension": "D1", "text": "x",
        "declared_target_date": "2024-01-01", "target_date_role": "event_date"}])
    assert snapshot["questions"][0]["declared_target_date"] == "2024-01-01"
    with pytest.raises(DomainError) as error:
        normalize_questions("historical-unknown")
    assert error.value.code == "QUESTION_SET_NOT_FOUND"
