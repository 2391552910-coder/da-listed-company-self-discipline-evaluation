"""Citation consistency and incomplete outputs; isolated unit fixtures, no model API."""
from copy import deepcopy

from app.documents.common import dump, sha
from app.evaluation.validation import validate_output


def source(evidence_id):
    text = "公司设有审计委员会。" if evidence_id.startswith("A") else "应当设立审计委员会。"
    role = "annual_report" if evidence_id.startswith("A") else "regulation"
    return {"evidence_id": evidence_id, "source_role": role, "corpus_type": role,
        "chunk_id": "c-" + evidence_id, "version_id": "v-" + evidence_id,
        "content": text, "content_sha256": sha(text.encode()), "char_start": 0,
        "char_end": len(text), "source_format": "txt", "physical_page": None}


def model_output(body="公司披露设有审计委员会[A1]，法规要求设立审计委员会[R1]。"):
    return {"answer": body, "evidence_status": "sufficient", "limitations": ["仅核对给定材料"],
        "claims": [{"id": "C1", "text": "公司披露委员会安排", "support_status": "supported",
                    "citations": [{"evidence_id": "A1", "quote": "设有审计委员会"}]},
                   {"id": "C2", "text": "法规提出委员会要求", "support_status": "supported",
                    "citations": [{"evidence_id": "R1", "quote": "设立审计委员会"}]}]}


def check(value):
    return validate_output(dump(value), [source("A1"), source("R1")])


def errors(value):
    return {error["code"] for error in value["validation"]["errors"]}


def test_valid_inline_citations_are_cross_checked_against_claims_and_frozen_sources():
    result = check(model_output())
    assert result["validation"]["status"] == "valid"
    assert result["validation"]["body_evidence_ids"] == ["A1", "R1"]
    assert result["validation"]["body_unmatched_evidence_ids"] == []
    assert result["semantic_support_verified"] is False


def test_unknown_body_citation_does_not_get_hidden_by_valid_structured_claims():
    result = check(model_output("公司披露安排[A1]；另有未知证据[A99][R99]。"))
    assert result["validation"]["status"] == "invalid"
    assert "UNKNOWN_BODY_EVIDENCE_ID" in errors(result)
    assert result["validation"]["body_unmatched_evidence_ids"] == ["A99", "R99"]
    assert result["evidence_status"] == "insufficient"


def test_known_body_citation_without_a_corresponding_claim_reference_is_invalid():
    value = model_output()
    value["claims"] = value["claims"][:1]
    result = check(value)
    assert "BODY_CITATION_WITHOUT_VALID_CLAIM" in errors(result)
    assert result["validation"]["body_unmatched_evidence_ids"] == ["R1"]


def test_known_body_citation_requires_a_valid_claim_quote_not_only_claim_id_presence():
    value = model_output()
    value["claims"][1]["citations"][0]["quote"] = "模型凭空编造的原文"
    result = check(value)
    assert {"QUOTE_NOT_VERBATIM", "BODY_CITATION_WITHOUT_VALID_CLAIM"} <= errors(result)


def test_no_inline_markers_does_not_force_all_valid_claims_to_appear_in_prose():
    result = check(model_output("年报与法规中均涉及审计委员会；实际执行效果仍需核实。"))
    assert result["validation"]["status"] == "valid"
    assert result["validation"]["body_evidence_ids"] == []
    assert result["validation"]["mechanical_citation_valid_count"] == 2


def test_valid_claim_need_not_have_its_id_repeated_in_body():
    result = check(model_output("年报披露审计委员会[A1]。"))
    assert result["validation"]["status"] == "valid"
    assert result["validation"]["body_evidence_ids"] == ["A1"]


def test_duplicate_inline_markers_keep_first_occurrence_order():
    result = check(model_output("法规[R1]与年报[A1][R1][A1]。"))
    assert result["validation"]["body_evidence_ids"] == ["R1", "A1"]
    assert result["validation"]["status"] == "valid"


def test_noncanonical_inline_zero_number_is_unknown_instead_of_silently_ignored():
    result = check(model_output("年报[A01]，法规[R0]。"))
    assert "UNKNOWN_BODY_EVIDENCE_ID" in errors(result)
    assert result["validation"]["body_unmatched_evidence_ids"] == ["A01", "R0"]


def test_body_citations_cannot_bypass_original_source_integrity_failure():
    value = model_output()
    result = validate_output(dump(value), [source("A1"), source("R1")], lambda row: row["evidence_id"] != "A1")
    assert "SOURCE_INTEGRITY_FAILED" in errors(result)
    assert "BODY_CITATION_WITHOUT_VALID_CLAIM" in errors(result)
    assert result["validation"]["body_unmatched_evidence_ids"] == ["A1"]


def test_partial_json_output_cannot_be_rescued_as_a_valid_evaluation():
    value = dump(model_output())
    result = validate_output(value[:-7], [source("A1"), source("R1")])
    assert result["validation"]["status"] == "invalid"
    assert "INVALID_JSON" in errors(result)
    assert result["answer_text"] is None and result["evidence_status"] == "insufficient"
